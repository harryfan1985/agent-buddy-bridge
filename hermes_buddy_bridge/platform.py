"""
BuddyPlatformAdapter — Hermes Platform Adapter via platform_class.

Injects into Hermes as a custom platform adapter (platform_class config).
Handles:
- send_exec_approval() → push state to BuddyBridge via HTTP
- Registers approval_callback so AIAgent can resolve approvals
  when BuddyBridge POSTs button decisions back to Hermes /internal/approve

Requires PR #11812 ( NousResearch/hermes-agent ) to be merged for:
- platform_class support in gateway/config.py
- approval_callback parameter in AIAgent
- pre_tool_call {"action": "approve"} directive
"""

from __future__ import annotations

import aiohttp
import asyncio
import logging
from typing import Any, Dict, Optional

from gateway.platforms.base import BasePlatformAdapter, PlatformConfig, Platform
from gateway.platforms.base_types import SendResult

logger = logging.getLogger(__name__)


class BuddyApprovalCallback:
    """
    Approval callback registered with AIAgent (via approval_callback param).

    When BuddyBridge receives a button decision from M5StickC, it POSTs
    to Hermes's /internal/approve endpoint which calls this callback to
    resolve the gateway approval.
    """

    def __init__(self, hermes_approve_url: str):
        """
        Args:
            hermes_approve_url: Hermes internal approve endpoint
                (e.g. "http://localhost:8642/internal/approve")
        """
        self.hermes_approve_url = hermes_approve_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def __call__(self, session_key: str, choice: str) -> int:
        """
        Resolve an approval from BuddyBridge button.

        Called by Hermes's /internal/approve handler when BuddyBridge
        POSTs a button decision.

        Args:
            session_key: The approval session key
            choice: "once", "deny", "always", "session"

        Returns:
            Number of approvals resolved
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        try:
            async with self._session.post(
                f"{self.hermes_approve_url}/approve",
                json={"session_key": session_key, "choice": choice},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                count = result.get("resolved", 0)
                logger.info(
                    f"[BuddyApprovalCallback] resolved={count} "
                    f"session={session_key[:20]} choice={choice}"
                )
                return count
        except Exception as e:
            logger.error(f"[BuddyApprovalCallback] failed: {e}")
            return 0

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class BuddyPlatformAdapter(BasePlatformAdapter):
    """
    Hermes Platform Adapter that bridges to M5StickC via BuddyBridge HTTP.

    Registered via platform_class config:
        platforms:
          buddy:
            enabled: true
            platform_class: "agent_buddy_bridge.platform.BuddyPlatformAdapter"
            bridge_url: "http://localhost:8765"   # BuddyBridge HTTP server
            hermes_approve_url: "http://localhost:8642"  # Hermes internal API

    Sends approval requests to M5StickC via BuddyBridge BLE bridge.
    Receives button decisions via Hermes /internal/approve webhook.
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WEBHOOK)
        self.bridge_url: str = config.extra.get("bridge_url", "http://localhost:8765")
        self.hermes_approve_url: str = config.extra.get(
            "hermes_approve_url", "http://localhost:8642"
        )
        # session_key → prompt_id for correlation (BuddyBridge uses prompt_id)
        self._session_to_prompt: Dict[str, str] = {}
        # Approval callback for AIAgent (set by register_approval_callback)
        self._approval_callback: Optional[BuddyApprovalCallback] = None

    # ------------------------------------------------------------------
    # AIAgent integration
    # ------------------------------------------------------------------

    def register_approval_callback(self) -> BuddyApprovalCallback:
        """
        Create and return the approval callback for AIAgent.

        The AIAgent should call:
            agent = AIAgent(..., approval_callback=adapter.register_approval_callback())

        The returned callback POSTs to Hermes's /internal/approve when called.
        """
        self._approval_callback = BuddyApprovalCallback(self.hermes_approve_url)
        return self._approval_callback

    # ------------------------------------------------------------------
    # BasePlatformAdapter interface
    # ------------------------------------------------------------------

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        Send approval request to M5StickC via BuddyBridge HTTP.

        Pushes state to BuddyBridge (:8765/buddy/state) which forwards
        it to M5StickC over BLE for user button confirmation.

        Args:
            chat_id: Ignored for BuddyBridge (no chat)
            command: The dangerous command string
            session_key: Hermes approval session key
            description: Human-readable description
            metadata: Additional metadata

        Returns:
            SendResult indicating success
        """
        metadata = metadata or {}

        # Generate a prompt_id from session_key for BuddyBridge correlation
        prompt_id = session_key.split("/")[-1] if session_key else "unknown"
        self._session_to_prompt[session_key] = prompt_id

        # Build Buddy Desktop heartbeat format state
        state = {
            "total": 1,
            "running": 1,
            "waiting": 0,
            "msg": f"approve: {description}",
            "entries": [],
            "tokens": 0,
            "tokens_today": 0,
            "prompt": {
                "id": prompt_id,
                "tool": metadata.get("tool_name", "Bash"),
                "hint": command[:200] if command else "",
            },
        }

        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    f"{self.bridge_url}/buddy/state",
                    json=state,
                    headers={"X-Session-Key": session_key},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        logger.info(
                            f"[BuddyPlatformAdapter] approval pushed: "
                            f"session={session_key[:30]} cmd={command[:50]}"
                        )
                        return SendResult(success=True)
                    body = await resp.text()
                    logger.warning(
                        f"[BuddyPlatformAdapter] push failed: {resp.status} - {body}"
                    )
                    return SendResult(success=False, error=f"HTTP {resp.status}")
        except Exception as e:
            logger.error(f"[BuddyPlatformAdapter] send_exec_approval error: {e}")
            return SendResult(success=False, error=str(e))

    async def send(
        self,
        chat_id: str,
        content: str,
        **kwargs,
    ) -> SendResult:
        """
        Send a message. BuddyBridge is primarily for approvals, not messaging.

        This is a no-op — responses come back through the approval callback.
        """
        return SendResult(success=True)

    async def send_update_prompt(
        self,
        chat_id: str,
        content: str,
        **kwargs,
    ) -> SendResult:
        """Send a status update. No-op for BuddyBridge."""
        return SendResult(success=True)

    async def send_model_picker(
        self,
        chat_id: str,
        models: list,
        **kwargs,
    ) -> SendResult:
        """Model picker. No-op for BuddyBridge."""
        return SendResult(success=True)

    async def handle_message(self, msg_event: Any) -> None:
        """
        Handle incoming messages. BuddyBridge receives decisions via HTTP,
        not through this path.
        """
        pass

    # ------------------------------------------------------------------
    # Session key helpers (used by Hermes gateway)
    # ------------------------------------------------------------------

    def get_prompt_id(self, session_key: str) -> Optional[str]:
        """Look up prompt_id for a session_key (for BuddyBridge correlation)."""
        return self._session_to_prompt.get(session_key)
