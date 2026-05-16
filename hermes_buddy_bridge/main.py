"""
Hermes Buddy Bridge - Main entry point.

Bridges M5StickC Plus BLE Peripheral to one or more agent platforms
(Hermes Agent, BitFun ADE, etc.) for physical button-press approvals.

Architecture:
  M5StickC (BLE Peripheral)
       ↕ BLE NUS (Nordic UART Service)
    BLECentral (ble_central.py)
       ↕ JSON over BLE
    HTTPServer (http_server.py, :8765)
       ↕ POST /buddy/state
    HermesBuddyBridge (this file)
       ↕ PlatformBackend dispatch
    ├── HermesBackend → Hermes /internal/approve or Approval Relay
    └── BitFunBackend → BitFun /buddy/approve
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from .ble_central import BLECentral
from .json_codec import NUSJSONCodec
from .http_server import HTTPServer
from .backends import PlatformBackend
from .backends.hermes_backend import HermesBackend
from .backends.bitfun_backend import BitFunBackend

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class HermesBuddyBridge:
    """
    Main bridge application with multi-platform backend support.

    Data flows:
      Agent platform detects dangerous command
        → POST /buddy/state + platform headers
        → HTTPServer (:8765)
        → BLE → M5StickC display

      M5StickC button press
        → BLE notification
        → BLECentral._handle_notification
        → prompt_id → info lookup
        → dispatch to ALL enabled PlatformBackend instances
        → each backend resolves in its own protocol
    """

    def __init__(
        self,
        http_port: int = 8765,
        hermes_approve_url: str = "http://localhost:8642",
        relay_url: str = "http://localhost:8766",
        bitfun_url: str = "http://localhost:48765",
        platforms: Optional[list[str]] = None,
    ):
        """
        Args:
            http_port: HTTP server port for receiving state from agent platforms
            hermes_approve_url: Hermes internal approve URL (primary path)
            relay_url: Approval Relay fallback URL (Hermes secondary path)
            bitfun_url: BitFun approval endpoint URL
            platforms: List of enabled platform names (default: ["hermes"])
                       Supported: "hermes", "bitfun"
        """
        self.ble = BLECentral()
        self.http_server = HTTPServer(
            port=http_port,
            hermes_approve_url=hermes_approve_url,
        )
        self._running = False

        # prompt_id → platform-specific resolution info
        #   {"session_key": ..., "tool_id": ..., "platform": "hermes"|"bitfun"}
        self._prompt_map: dict[str, dict] = {}

        # Build enabled backends
        self._backends: dict[str, PlatformBackend] = {}
        enabled = platforms or ["hermes"]

        if "hermes" in enabled:
            self._backends["hermes"] = HermesBackend(
                hermes_approve_url=hermes_approve_url,
                relay_url=relay_url,
            )
            logger.info(f"Platform backend enabled: hermes")

        if "bitfun" in enabled:
            self._backends["bitfun"] = BitFunBackend(
                bitfun_url=bitfun_url,
            )
            logger.info(f"Platform backend enabled: bitfun → {bitfun_url}")

        if not self._backends:
            logger.warning("No platform backends enabled!")

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _setup_ble_callback(self) -> None:
        """Set up BLE notification callback for incoming device messages."""

        def on_ble_message(data: str) -> None:
            msg = NUSJSONCodec.decode_message(data)
            if msg:
                self._handle_device_message(msg)

        self.ble.set_notification_callback(on_ble_message)

    def _setup_http_callbacks(self) -> None:
        """Set up HTTP server callbacks for agent platform state updates."""

        def on_state(state: dict, session_key: str) -> None:
            self._handle_state_received(state, session_key)

        self.http_server.set_state_callback(on_state)
        self.http_server.set_status_callback(self._get_device_status)

    # ------------------------------------------------------------------
    # State handling (from agent platforms via HTTP)
    # ------------------------------------------------------------------

    def _handle_state_received(self, state: dict, session_key: str) -> None:
        """
        Handle incoming state from any agent platform.

        Extracts prompt_id for button correlation and stores platform-
        specific resolution info for all enabled backends.
        """
        prompt = state.get("prompt") or {}
        prompt_id = prompt.get("id", "")

        if not prompt_id:
            logger.debug("State received without prompt.id, skipping mapping")
            return

        # Store resolution info for all enabled backends
        info: dict = {}
        if session_key:
            info["session_key"] = session_key
        info["tool_id"] = prompt_id
        info["tool_name"] = prompt.get("tool", "unknown")
        info["hint"] = prompt.get("hint", "")

        self._prompt_map[prompt_id] = info

        if session_key:
            logger.debug(
                f"Tracking: prompt={prompt_id[:20]} "
                f"session={session_key[:20]} "
                f"backends={list(self._backends.keys())}"
            )
        else:
            logger.debug(
                f"Tracking: prompt={prompt_id[:20]} (no session_key) "
                f"backends={list(self._backends.keys())}"
            )

        # Forward state to M5StickC display via BLE
        self._forward_state_to_device(state)

    def _forward_state_to_device(self, state: dict) -> None:
        """Forward agent platform state to M5StickC via BLE."""
        if not self.ble.is_connected:
            logger.debug("BLE not connected, skipping state forward")
            return

        json_data = NUSJSONCodec.encode_state(state)
        # NUS is line-oriented: each JSON message is newline-delimited
        asyncio.create_task(self.ble.write(json_data))
        asyncio.create_task(self.ble.write("\n"))

    # ------------------------------------------------------------------
    # Device message handling (from M5StickC via BLE)
    # ------------------------------------------------------------------

    def _handle_device_message(self, msg: dict) -> None:
        """
        Handle incoming message from M5StickC (via BLE).

        Expects permission decision format:
          {"cmd": "permission", "id": "req_xxx", "decision": "once|deny"}
        """
        permission_data = NUSJSONCodec.get_permission_data(msg)
        if not permission_data:
            logger.debug(f"Ignoring non-permission message: {msg.get('cmd', '?')}")
            return

        prompt_id, decision = permission_data
        logger.info(f"Button: {decision} for prompt {prompt_id[:20]}")

        # Look up resolution info for this prompt
        info = self._prompt_map.get(prompt_id)
        if not info:
            logger.warning(f"Unknown prompt_id: {prompt_id}, cannot resolve approval")
            return

        # Dispatch to ALL enabled backends concurrently
        for backend_name, backend in self._backends.items():
            asyncio.create_task(
                self._dispatch_to_backend(backend_name, backend, info, decision)
            )

    async def _dispatch_to_backend(
        self,
        backend_name: str,
        backend: PlatformBackend,
        info: dict,
        choice: str,
    ) -> None:
        """
        Dispatch a button decision to a specific backend.

        Each backend receives the full info dict and uses the fields it
        needs (Hermes uses session_key, BitFun uses tool_id).
        """
        session_key = info.get("session_key", "")
        tool_id = info.get("tool_id", "")

        ok = await backend.resolve(
            session_key=session_key,
            tool_id=tool_id,
            choice=choice,
        )

        if ok:
            logger.info(f"Backend [{backend_name}]: {choice} resolved OK")
        else:
            logger.warning(f"Backend [{backend_name}]: {choice} failed")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _get_device_status(self) -> dict:
        """Get current device status."""
        return {
            "connected": self.ble.is_connected,
            "device_name": self.ble.device.name if self.ble.device else None,
            "pending_prompts": len(self._prompt_map),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the bridge."""
        logger.info("Starting Hermes Buddy Bridge...")
        self._running = True

        # Set up callbacks
        self._setup_ble_callback()
        self._setup_http_callbacks()

        # Start HTTP server (receives state from agent platforms)
        await self.http_server.start()
        logger.info(f"HTTP server listening on http://localhost:{self.http_server.port}")
        logger.info(f"  POST /buddy/state (agent platform → M5StickC)")
        logger.info(f"  GET  /buddy/status")
        logger.info(f"Enabled backends: {list(self._backends.keys())}")

        # Connect BLE
        if await self.ble.connect_first():
            logger.info(f"BLE connected to {self.ble.device.name}")
        else:
            logger.warning("No M5StickC found, will retry in background...")

        # Start BLE connection monitor
        asyncio.create_task(self._ble_monitor())

        logger.info("Bridge started. Press Ctrl+C to stop.")

    async def _ble_monitor(self) -> None:
        """Monitor and reconnect BLE as needed."""
        while self._running:
            if not self.ble.is_connected:
                logger.info("BLE: scanning for M5StickC (Claude-*)...")
                if await self.ble.connect_first():
                    logger.info(f"BLE: reconnected to {self.ble.device.name}")
                else:
                    await asyncio.sleep(5)
            else:
                await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the bridge and clean up all backends."""
        logger.info("Stopping Hermes Buddy Bridge...")
        self._running = False
        await self.ble.disconnect()
        await self.http_server.stop()

        # Clean up backends
        for name, backend in self._backends.items():
            try:
                await backend.close()
                logger.debug(f"Backend [{name}] closed")
            except Exception as e:
                logger.warning(f"Error closing backend [{name}]: {e}")

        logger.info("Bridge stopped")

    async def run(self) -> None:
        """Run the bridge until stopped."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()


async def main() -> None:
    """Entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Hermes Buddy Bridge")
    parser.add_argument(
        "--http-port", type=int, default=8765,
        help="HTTP server port for agent platform webhook (default: 8765)"
    )
    parser.add_argument(
        "--hermes-approve-url", default="http://localhost:8642",
        help="Hermes internal approve URL (default: http://localhost:8642)"
    )
    parser.add_argument(
        "--relay-url", default="http://localhost:8766",
        help="Approval relay fallback URL (default: http://localhost:8766)"
    )
    parser.add_argument(
        "--bitfun-url", default="http://localhost:48765",
        help="BitFun approval endpoint URL (default: http://localhost:48765)"
    )
    parser.add_argument(
        "--platforms", default="hermes",
        help="Comma-separated list of enabled platforms: hermes,bitfun "
             "(default: hermes)"
    )
    args = parser.parse_args()

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    for p in platforms:
        if p not in ("hermes", "bitfun"):
            logger.warning(f"Unknown platform '{p}', ignoring. Supported: hermes, bitfun")

    bridge = HermesBuddyBridge(
        http_port=args.http_port,
        hermes_approve_url=args.hermes_approve_url,
        relay_url=args.relay_url,
        bitfun_url=args.bitfun_url,
        platforms=platforms,
    )

    loop = asyncio.get_running_loop()

    def signal_handler() -> None:
        logger.info("Received shutdown signal")
        asyncio.create_task(bridge.stop())
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
        sys.exit(0)
