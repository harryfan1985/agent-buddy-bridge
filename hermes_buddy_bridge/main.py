"""
Hermes Buddy Bridge - Main entry point.

Bridges M5StickC Plus BLE Peripheral to Hermes Agent Gateway.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from .ble_central import BLECentral, NUSJSONCodec
from .http_server import HTTPServer
from .http_client import HermesHTTPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class HermesBuddyBridge:
    """Main bridge application."""

    def __init__(
        self,
        http_port: int = 8765,
        gateway_url: str = "http://localhost:8080"
    ):
        self.ble = BLECentral()
        self.http_server = HTTPServer(port=http_port)
        self.gateway_client = HermesHTTPClient(gateway_url)
        self._running = False
        self._pending_prompt_id: Optional[str] = None

    def _setup_ble_callback(self) -> None:
        """Set up BLE notification callback."""

        def on_ble_message(data: str):
            msg = NUSJSONCodec.decode_message(data)
            if msg:
                self._handle_device_message(msg)

        self.ble.set_notification_callback(on_ble_message)

    def _setup_http_callbacks(self) -> None:
        """Set up HTTP server callbacks."""

        def on_state(state: dict):
            self._forward_state_to_device(state)

        self.http_server.set_state_callback(on_state)
        self.http_server.set_status_callback(self._get_device_status)

    def _handle_device_message(self, msg: dict) -> None:
        """Handle incoming message from M5StickC."""
        permission_data = NUSJSONCodec.get_permission_data(msg)
        if permission_data:
            prompt_id, decision = permission_data
            logger.info(f"Button pressed: {decision} for prompt {prompt_id[:20]}")
            asyncio.create_task(
                self.gateway_client.post_decision(prompt_id, decision)
            )

    def _forward_state_to_device(self, state: dict) -> None:
        """Forward Hermes state to M5StickC via BLE."""
        if not self.ble.is_connected:
            logger.debug("BLE not connected, skipping state forward")
            return

        # Track pending prompt for correlation
        prompt = state.get("prompt", {})
        if prompt and prompt.get("id"):
            self._pending_prompt_id = prompt["id"]

        # Encode and send
        json_data = NUSJSONCodec.encode_state(state)
        # Add newline as message delimiter (NUS is line-oriented)
        asyncio.create_task(self.ble.write(json_data))
        asyncio.create_task(self.ble.write("\n"))

    def _get_device_status(self) -> dict:
        """Get current device status."""
        return {
            "connected": self.ble.is_connected,
            "device_name": self.ble.device.name if self.ble.device else None,
        }

    async def start(self) -> None:
        """Start the bridge."""
        logger.info("Starting Hermes Buddy Bridge...")
        self._running = True

        # Set up callbacks
        self._setup_ble_callback()
        self._setup_http_callbacks()

        # Start HTTP server
        await self.http_server.start()

        # Connect BLE
        if await self.ble.connect_first():
            logger.info("BLE connected")
        else:
            logger.warning("No BLE device found, will retry...")

        # Start BLE connection monitor
        asyncio.create_task(self._ble_monitor())

        logger.info("Bridge started. Press Ctrl+C to stop.")

    async def _ble_monitor(self) -> None:
        """Monitor and reconnect BLE as needed."""
        while self._running:
            if not self.ble.is_connected:
                logger.info("Attempting BLE reconnect...")
                if await self.ble.connect_first():
                    logger.info("BLE reconnected")
                else:
                    logger.debug("BLE scan failed, retrying in 5s...")
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


async def main():
    """Entry point."""
    bridge = HermesBuddyBridge()
    
    loop = asyncio.get_running_loop()
    
    def signal_handler():
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
