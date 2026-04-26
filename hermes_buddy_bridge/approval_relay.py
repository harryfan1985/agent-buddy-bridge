"""
Approval Relay - receives button decisions from BuddyBridge and resolves them in Hermes.

This runs as a separate process. It:
1. Exposes POST /approve endpoint (for BuddyBridge to call)
2. Imports and calls Hermes's resolve_gateway_approval()

Usage:
    python -m hermes_buddy_bridge.approval_relay --hermes-home ~/.hermes
"""

import argparse
import asyncio
import logging
import sys
import os

# Add Hermes to path so we can import its modules
HERMES_HOME = os.path.expanduser("~/.hermes")
sys.path.insert(0, os.path.join(HERMES_HOME, "hermes-agent"))

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class ApprovalRelay:
    """Relays button decisions from BuddyBridge to Hermes approval system."""

    def __init__(self, hermes_home: str, port: int = 8766):
        self.hermes_home = hermes_home
        self.port = port
        self.app: web.Application | None = None
        self._resolve_fn: callable | None = None

    def _import_hermes_approval(self) -> callable:
        """Import resolve_gateway_approval from Hermes tools/approval."""
        try:
            # Set up Hermes environment
            os.environ["HERMES_HOME"] = self.hermes_home

            from tools.approval import resolve_gateway_approval
            self._resolve_fn = resolve_gateway_approval
            logger.info("Imported Hermes resolve_gateway_approval OK")
            return resolve_gateway_approval
        except ImportError as e:
            logger.error(f"Failed to import Hermes approval module: {e}")
            raise

    async def handle_approve(self, request: web.Request) -> web.Response:
        """
        POST /approve

        Body: {"session_key": "...", "choice": "once|deny|always|session"}

        Calls Hermes's resolve_gateway_approval(session_key, choice).
        """
        if self._resolve_fn is None:
            return web.json_response(
                {"error": "Hermes approval not initialized"},
                status=500
            )

        try:
            data = await request.json()
            session_key = data.get("session_key", "")
            choice = data.get("choice", "")

            if not session_key or not choice:
                return web.json_response(
                    {"error": "session_key and choice required"},
                    status=400
                )

            if choice not in ("once", "deny", "always", "session"):
                return web.json_response(
                    {"error": f"Invalid choice: {choice}"},
                    status=400
                )

            # Resolve in Hermes's thread-safe approval system
            # resolve_gateway_approval(session_key, choice, resolve_all=False)
            resolve_all = (choice == "session")
            if choice == "session":
                choice = "once"  # /approve session = approve all pending

            count = self._resolve_fn(session_key, choice, resolve_all=resolve_all)
            logger.info(f"Resolved approval: session={session_key[:20]}, choice={choice}, count={count}")

            return web.json_response({
                "status": "ok",
                "resolved": count
            })

        except Exception as e:
            logger.error(f"Approval resolution error: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /health - health check."""
        return web.json_response({
            "status": "ok",
            "service": "approval-relay",
            "hermes_loaded": self._resolve_fn is not None
        })

    async def start(self) -> None:
        """Start the relay server."""
        self._import_hermes_approval()

        self.app = web.Application()
        self.app.router.add_post("/approve", self.handle_approve)
        self.app.router.add_get("/health", self.handle_health)

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.port)
        await site.start()

        logger.info(f"Approval relay listening on http://localhost:{self.port}")
        logger.info(f"  POST /approve {{session_key, choice}}")
        logger.info(f"  GET  /health")


async def main():
    parser = argparse.ArgumentParser(description="Hermes BuddyBridge Approval Relay")
    parser.add_argument(
        "--hermes-home",
        default="~/.hermes",
        help="Hermes config directory (default: ~/.hermes)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8766,
        help="Relay HTTP port (default: 8766)"
    )
    args = parser.parse_args()

    relay = ApprovalRelay(
        hermes_home=os.path.expanduser(args.hermes_home),
        port=args.port
    )
    await relay.start()

    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Approval relay stopped")
