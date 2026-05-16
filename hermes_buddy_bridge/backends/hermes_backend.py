"""
Hermes Platform Backend.

Resolves approval decisions via Hermes Agent's approval system.
Two-path resolution:
  1. Primary: POST to Hermes /internal/approve (PR #11812)
  2. Fallback: POST to Approval Relay (:8766) → resolve_gateway_approval()
"""

from __future__ import annotations

import aiohttp
import logging
from typing import Any, Dict, Optional

from . import PlatformBackend

logger = logging.getLogger(__name__)


class HermesBackend(PlatformBackend):
    """
    Approval resolution for Hermes Agent platforms.

    Args:
        hermes_approve_url: Hermes internal approve URL
            (e.g. "http://localhost:8642")
        relay_url: Approval Relay fallback URL
            (e.g. "http://localhost:8766")
    """

    def __init__(
        self,
        hermes_approve_url: str = "http://localhost:8642",
        relay_url: str = "http://localhost:8766",
    ):
        self.hermes_approve_url = hermes_approve_url.rstrip("/")
        self.relay_url = relay_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "hermes"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def resolve(
        self,
        *,
        session_key: str = "",
        tool_id: str = "",
        choice: str = "once",
    ) -> bool:
        """
        Resolve approval on Hermes.

        Uses session_key (ignores tool_id). Two-path:
        1. POST to Hermes internal /internal/approve
        2. Fallback: POST to Approval Relay /approve
        """
        if not session_key:
            logger.warning("[HermesBackend] resolve called without session_key, skipping")
            return False

        # Path 1: Hermes internal approve endpoint
        if await self._try_hermes_internal(session_key, choice):
            return True

        # Path 2: Approval Relay fallback
        return await self._try_relay(session_key, choice)

    async def _try_hermes_internal(self, session_key: str, choice: str) -> bool:
        """Try resolving via Hermes's own /internal/approve endpoint."""
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.hermes_approve_url}/internal/approve",
                json={"session_key": session_key, "choice": choice},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info(
                        "[HermesBackend] resolved via Hermes internal: "
                        f"session={session_key[:20]} choice={choice}"
                    )
                    return True
                logger.debug(
                    f"[HermesBackend] Hermes internal returned {resp.status}"
                )
                return False
        except Exception as e:
            logger.debug(f"[HermesBackend] Hermes internal unreachable: {e}")
            return False

    async def _try_relay(self, session_key: str, choice: str) -> bool:
        """Fallback: resolve via standalone Approval Relay process."""
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.relay_url}/approve",
                json={"session_key": session_key, "choice": choice},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    logger.info(
                        "[HermesBackend] resolved via Approval Relay: "
                        f"session={session_key[:20]} choice={choice} "
                        f"resolved={result.get('resolved', '?')}"
                    )
                    return True
                logger.warning(
                    f"[HermesBackend] Relay returned {resp.status}"
                )
                return False
        except Exception as e:
            logger.error(f"[HermesBackend] Approval Relay also unavailable: {e}")
            return False

    async def health(self) -> Dict[str, Any]:
        """Check Hermes platform health."""
        status = {
            "status": "ok",
            "platform": "hermes",
            "hermes_approve_url": self.hermes_approve_url,
            "relay_url": self.relay_url,
        }

        # Check Hermes internal
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.hermes_approve_url}/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                status["hermes_internal"] = "ok" if resp.status == 200 else f"status_{resp.status}"
        except Exception:
            status["hermes_internal"] = "unreachable"

        # Check Approval Relay
        try:
            async with session.get(
                f"{self.relay_url}/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    status["relay"] = body.get("status", f"status_{resp.status}")
                else:
                    status["relay"] = f"status_{resp.status}"
        except Exception:
            status["relay"] = "unreachable"

        # Aggregate status
        if status.get("hermes_internal") != "ok" and status.get("relay") != "ok":
            status["status"] = "error"

        return status

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
