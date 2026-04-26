"""
HTTP Server module - receives session state from Hermes Gateway AND
proxies button decisions to Hermes's internal approve endpoint.

Endpoints:
- POST /buddy/state - receives state updates from Hermes (forward to M5StickC)
- GET  /buddy/status - device status query
- POST /internal/approve - button decisions → Hermes resolve_gateway_approval()
                         (requires PR #11812 merged; falls back to Approval Relay)
"""

import asyncio
import logging
from aiohttp import web, ClientSession, ClientTimeout
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


class HTTPServer:
    """HTTP server receiving Hermes Gateway state updates and proxying approvals."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        hermes_approve_url: str = "http://localhost:8642",
    ):
        self.port = port
        self.hermes_approve_url = hermes_approve_url.rstrip("/")
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self._state_callback: Optional[Callable[[dict], None]] = None
        self._status_callback: Optional[Callable[[], dict]] = None

    def set_state_callback(self, callback: Callable[[dict, str], None]) -> None:
        """Set callback for incoming state updates.

        Callback signature: (state: dict, session_key: str) -> None
        """
        self._state_callback = callback

    def set_status_callback(self, callback: Callable[[], dict]) -> None:
        """Set callback for status queries."""
        self._status_callback = callback

    async def handle_state(self, request: web.Request) -> web.Response:
        """
        POST /buddy/state

        Receives session state from Hermes Gateway (via BuddyAdapter webhook).
        Forwards to M5StickC via BLE.

        Headers:
            X-Session-Key: Hermes approval session key (for correlation)

        Body: Session State JSON (Claude Desktop Buddy heartbeat format)
        """
        try:
            # Extract session key from header for button correlation
            session_key = request.headers.get("X-Session-Key", "")

            data = await request.json()
            logger.debug(f"Received state: {str(data)[:200]}, session_key={session_key[:20] if session_key else '?'}")

            if self._state_callback:
                # Pass both state and session_key to callback
                self._state_callback(data, session_key)

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"State handler error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def handle_status(self, request: web.Request) -> web.Response:
        """
        GET /buddy/status

        Returns device status (battery, connection state, etc.)
        """
        try:
            if self._status_callback:
                status = self._status_callback()
            else:
                status = {"connected": False}
            return web.json_response(status)
        except Exception as e:
            logger.error(f"Status handler error: {e}")
            return web.json_response({"status": "error"}, status=500)

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /health - health check."""
        return web.json_response({"status": "ok", "service": "hermes-buddy-bridge"})

    async def handle_internal_approve(self, request: web.Request) -> web.Response:
        """
        POST /internal/approve

        Button decision from M5StickC → proxy to Hermes's internal approve endpoint.
        This is the callback path when PR #11812 is merged and Hermes exposes
        /internal/approve for BuddyBridge button decisions.

        Falls back to Approval Relay (:8766) if Hermes endpoint is unreachable.

        Body: {"session_key": "...", "choice": "once|deny|always|session"}
        """
        try:
            data = await request.json()
            session_key = data.get("session_key", "")
            choice = data.get("choice", "")

            if not session_key or not choice:
                return web.json_response(
                    {"error": "session_key and choice required"}, status=400
                )

            # Try Hermes internal endpoint first
            hermes_ok = await self._proxy_to_hermes(session_key, choice)
            if hermes_ok:
                return web.json_response({"status": "ok", "via": "hermes-internal"})

            # Fallback: proxy to Approval Relay (standalone process)
            relay_ok = await self._proxy_to_relay(session_key, choice)
            if relay_ok:
                return web.json_response({"status": "ok", "via": "approval-relay"})

            return web.json_response(
                {"error": "Both Hermes internal and Approval Relay unavailable"},
                status=503,
            )

        except Exception as e:
            logger.error(f"Internal approve error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _proxy_to_hermes(self, session_key: str, choice: str) -> bool:
        """Proxy approval to Hermes /internal/approve endpoint."""
        try:
            async with ClientSession() as sess:
                async with sess.post(
                    f"{self.hermes_approve_url}/internal/approve",
                    json={"session_key": session_key, "choice": choice},
                    timeout=ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        logger.info(
                            f"[HTTPServer] Hermes approve: "
                            f"session={session_key[:20]} choice={choice}"
                        )
                        return True
                    logger.warning(
                        f"[HTTPServer] Hermes approve failed: {resp.status}"
                    )
                    return False
        except Exception as e:
            logger.debug(f"[HTTPServer] Hermes approve unreachable: {e}")
            return False

    async def _proxy_to_relay(self, session_key: str, choice: str) -> bool:
        """Fallback: proxy approval to standalone Approval Relay (:8766)."""
        try:
            async with ClientSession() as sess:
                async with sess.post(
                    "http://localhost:8766/approve",
                    json={"session_key": session_key, "choice": choice},
                    timeout=ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(
                            f"[HTTPServer] Relay approve: "
                            f"session={session_key[:20]} resolved={result.get('resolved', '?')}"
                        )
                        return True
                    return False
        except Exception as e:
            logger.debug(f"[HTTPServer] Approval Relay unreachable: {e}")
            return False

    async def start(self) -> None:
        """Start the HTTP server."""
        self.app = web.Application()
        self.app.router.add_post("/buddy/state", self.handle_state)
        self.app.router.add_get("/buddy/status", self.handle_status)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_post("/internal/approve", self.handle_internal_approve)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "localhost", self.port)
        await self.site.start()
        logger.info(f"HTTP server listening on http://localhost:{self.port}")

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            logger.info("HTTP server stopped")
