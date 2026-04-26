"""
Hermes Buddy Bridge - Main entry point.

Bridges M5StickC Plus BLE Peripheral to Hermes Agent Gateway.

Architecture:
  M5StickC (BLE Peripheral)
       ↕ BLE NUS (Nordic UART Service)
    BLECentral (ble_central.py)
       ↕ JSON over BLE
    HTTPServer (http_server.py, :8765)
       ↕ POST /buddy/state
    Hermes Gateway (via BuddyAdapter webhook)
       ↕
    ApprovalRelay (approval_relay.py, :8766)
       → Hermes tools/approval.py::resolve_gateway_approval()
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from .ble_central import BLECentral
from .json_codec import NUSJSONCodec
from .http_server import HTTPServer
from .http_client import HermesHTTPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class HermesBuddyBridge:
    """
    Main bridge application.

    Data flows:
      Hermes (send_exec_approval webhook)
        → POST /buddy/state + X-Session-Key
        → HTTPServer (:8765)
        → BLE → M5StickC display

      M5StickC button press
        → BLE notification
        → BLECentral._handle_notification
        → prompt_id → session_key lookup
        → HermesHTTPClient.post_decision(session_key, choice)
        → ApprovalRelay (:8766)
        → Hermes resolve_gateway_approval()
    """

    def __init__(
        self,
        http_port: int = 8765,
        relay_url: str = "http://localhost:8766",
    ):
        self.ble = BLECentral()
        self.http_server = HTTPServer(port=http_port)
        self.gateway_client = HermesHTTPClient(gateway_url=relay_url)
        self._running = False

        # prompt_id → session_key mapping (populated from Hermes state)
        self._prompt_to_session: dict[str, str] = {}

    def _setup_ble_callback(self) -> None:
        """Set up BLE notification callback for incoming device messages."""

        def on_ble_message(data: str) -> None:
            msg = NUSJSONCodec.decode_message(data)
            if msg:
                self._handle_device_message(msg)

        self.ble.set_notification_callback(on_ble_message)

    def _setup_http_callbacks(self) -> None:
        """Set up HTTP server callbacks for Hermes state updates."""

        def on_state(state: dict, session_key: str) -> None:
            self._handle_hermes_state(state, session_key)

        self.http_server.set_state_callback(on_state)
        self.http_server.set_status_callback(self._get_device_status)

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

        # Look up session_key from prompt_id
        session_key = self._prompt_to_session.get(prompt_id, "")
        if not session_key:
            logger.warning(f"Unknown prompt_id: {prompt_id}, cannot resolve approval")
            return

        # Send decision to ApprovalRelay → Hermes
        asyncio.create_task(
            self.gateway_client.post_decision(session_key, decision)
        )

    def _handle_hermes_state(self, state: dict, session_key: str) -> None:
        """
        Handle incoming session state from Hermes Gateway.

        Stores prompt_id → session_key mapping for button correlation,
        then forwards state to M5StickC via BLE.
        """
        # Track prompt.id → session_key for button correlation
        prompt = state.get("prompt") or {}
        prompt_id = prompt.get("id", "")

        if prompt_id and session_key:
            self._prompt_to_session[prompt_id] = session_key
            logger.debug(f"Tracking: {prompt_id[:20]} → session={session_key[:20] if session_key else '?'}")
        elif prompt_id:
            logger.debug(f"Prompt {prompt_id[:20]} received (no session_key)")

        # Forward state to M5StickC
        self._forward_state_to_device(state)

    def _forward_state_to_device(self, state: dict) -> None:
        """Forward Hermes state to M5StickC via BLE."""
        if not self.ble.is_connected:
            logger.debug("BLE not connected, skipping state forward")
            return

        json_data = NUSJSONCodec.encode_state(state)
        # NUS is line-oriented: each JSON message is newline-delimited
        asyncio.create_task(self.ble.write(json_data))
        asyncio.create_task(self.ble.write("\n"))

    def _get_device_status(self) -> dict:
        """Get current device status."""
        return {
            "connected": self.ble.is_connected,
            "device_name": self.ble.device.name if self.ble.device else None,
            "pending_prompts": len(self._prompt_to_session),
        }

    async def start(self) -> None:
        """Start the bridge."""
        logger.info("Starting Hermes Buddy Bridge...")
        self._running = True

        # Set up callbacks
        self._setup_ble_callback()
        self._setup_http_callbacks()

        # Start HTTP server (receives state from Hermes)
        await self.http_server.start()
        logger.info(f"HTTP server listening on http://localhost:{self.http_server.port}")
        logger.info(f"  POST /buddy/state (Hermes → M5StickC)")
        logger.info(f"  GET  /buddy/status")

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
        """Stop the bridge."""
        logger.info("Stopping Hermes Buddy Bridge...")
        self._running = False
        await self.ble.disconnect()
        await self.http_server.stop()
        await self.gateway_client.close()
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
        help="HTTP server port for Hermes webhook (default: 8765)"
    )
    parser.add_argument(
        "--relay-url", default="http://localhost:8766",
        help="Approval relay URL (default: http://localhost:8766)"
    )
    args = parser.parse_args()

    bridge = HermesBuddyBridge(
        http_port=args.http_port,
        relay_url=args.relay_url,
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
