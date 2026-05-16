"""
BitFun Platform Backend.

Resolves approval decisions via BitFun's confirm/reject HTTP endpoint.

The BitFun side must expose an HTTP endpoint that accepts:
    POST /buddy/approve
    Body: {"tool_id": "...", "choice": "once|deny"}

This endpoint should call BitFun's ToolPipeline.confirm_tool() or
reject_tool() internally. The endpoint can be registered as a Tauri
command or via an embedded HTTP server.
"""

from __future__ import annotations

import aiohttp
import logging
from typing import Any, Dict, Optional

from . import PlatformBackend

logger = logging.getLogger(__name__)


class BitFunBackend(PlatformBackend):
    """
    Approval resolution for BitFun ADE platform.

    Args:
        bitfun_url: BitFun's approval endpoint URL
            (e.g. "http://localhost:48765" — Tauri dev server,
             or "http://localhost:9100" — embedded axum/actix server)
    """

    def __init__(self, bitfun_url: str = "http://localhost:48765"):
        self.bitfun_url = bitfun_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "bitfun"

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
        Resolve approval on BitFun.

        Uses tool_id (ignores session_key). POSTs to BitFun's
        /approve endpoint to call confirm_tool() / reject_tool().

        Args:
            session_key: Ignored (BitFun uses tool_id)
            tool_id: BitFun tool execution identifier
            choice: "once" (confirm) or "deny" (reject)
        """
        if not tool_id:
            logger.warning("[BitFunBackend] resolve called without tool_id, skipping")
            return False

        try:
            session = await self._get_session()
            async with session.post(
                f"{self.bitfun_url}/buddy/approve",
                json={
                    "tool_id": tool_id,
                    "choice": choice,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info(
                        "[BitFunBackend] resolved: "
                        f"tool={tool_id[:20]} choice={choice}"
                    )
                    return True

                body = await resp.text()
                logger.warning(
                    f"[BitFunBackend] returned {resp.status}: {body[:200]}"
                )
                return False

        except aiohttp.ClientConnectorError:
            logger.debug(
                f"[BitFunBackend] BitFun endpoint unreachable at {self.bitfun_url}"
            )
            return False
        except Exception as e:
            logger.error(f"[BitFunBackend] resolve error: {e}")
            return False

    async def health(self) -> Dict[str, Any]:
        """Check BitFun platform health."""
        status = {
            "status": "ok",
            "platform": "bitfun",
            "bitfun_url": self.bitfun_url,
        }

        try:
            session = await self._get_session()
            async with session.get(
                f"{self.bitfun_url}/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                status["endpoint"] = "ok" if resp.status == 200 else f"status_{resp.status}"
        except aiohttp.ClientConnectorError:
            status["endpoint"] = "unreachable"
            status["status"] = "error"
        except Exception:
            status["endpoint"] = "error"
            status["status"] = "error"

        return status

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
