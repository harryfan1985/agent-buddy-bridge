"""
HTTP Server module - receives session state from Hermes Gateway.

Exposes endpoints for:
- POST /buddy/state - receives state updates to forward to M5StickC
- GET /buddy/status - device status query
"""

import asyncio
import logging
from aiohttp import web
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


class HTTPServer:
    """HTTP server receiving Hermes Gateway state updates."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
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

    async def start(self) -> None:
        """Start the HTTP server."""
        self.app = web.Application()
        self.app.router.add_post("/buddy/state", self.handle_state)
        self.app.router.add_get("/buddy/status", self.handle_status)
        self.app.router.add_get("/health", self.handle_health)

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
