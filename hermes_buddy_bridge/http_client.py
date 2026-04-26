"""
HTTP Client module - posts button decisions to Hermes Gateway.

Used to send permission decisions from M5StickC buttons
back to Hermes Gateway for approval resolution.
"""

import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


class HermesHTTPClient:
    """HTTP client for communicating with Hermes Gateway."""

    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def post_decision(self, session_key: str, decision: str) -> bool:
        """
        Post a button decision to the Approval Relay.

        Args:
            session_key: The approval session key
            decision: "once", "deny", "always", or "session"
        """
        try:
            session = await self._get_session()
            payload = {
                "session_key": session_key,
                "choice": decision
            }
            async with session.post(
                f"{self.gateway_url}/approve",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    logger.info(f"Decision posted: {decision} → resolved={result.get('resolved', '?')}")
                    return True
                else:
                    body = await resp.text()
                    logger.warning(f"Decision failed: {resp.status} - {body}")
                    return False
        except Exception as e:
            logger.error(f"Failed to post decision: {e}")
            return False

    async def get_status(self) -> Optional[dict]:
        """
        Get Hermes Gateway status.

        Returns:
            Status dict or None if failed
        """
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.gateway_url}/buddy/status",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return None
