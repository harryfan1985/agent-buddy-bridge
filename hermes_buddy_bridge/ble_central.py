"""
BLE Central module for Mac - connects to M5StickC Plus via BLE NUS.

Nordic UART Service (NUS) protocol:
- Service UUID: 6e400001-b5a3-f393-e0a9-e50e24dcca9e
- RX Char (write): 6e400002-b5a3-f393-e0a9-e50e24dcca9e
- TX Char (notify): 6e400003-b5a3-f393-e0a9-e50e24dcca9e
"""

import asyncio
import logging
from typing import Optional, Callable
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice

from .json_codec import NUSJSONCodec

logger = logging.getLogger(__name__)

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


class BLECentral:
    """BLE Central that connects to M5StickC Plus over NUS."""

    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.device: Optional[BLEDevice] = None
        self._notification_callback: Optional[Callable[[str], None]] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self.client is not None

    async def scan(self, timeout: float = 5.0) -> list[BLEDevice]:
        """Scan for Claude-XXXX devices."""
        logger.info("Scanning for Claude-XXXX BLE devices...")
        devices = await BleakScanner.discover(timeout=timeout)
        claude_devices = [
            d for d in devices
            if d.name and d.name.startswith("Claude-")
        ]
        logger.info(f"Found {len(claude_devices)} Claude device(s)")
        for d in claude_devices:
            logger.info(f"  - {d.name} ({d.address})")
        return claude_devices

    async def connect(self, device: BLEDevice) -> bool:
        """Connect to a BLE device."""
        try:
            self.device = device
            self.client = BleakClient(device)
            await self.client.connect()
            self._connected = True
            logger.info(f"Connected to {device.name} ({device.address})")

            # Enable notifications on TX characteristic
            await self.client.start_notify(NUS_TX_UUID, self._handle_notification)
            logger.info("NUS notifications enabled")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            self._connected = False
            return False

    async def connect_first(self, timeout: float = 10.0) -> bool:
        """Scan and connect to the first available Claude device."""
        devices = await self.scan(timeout=timeout)
        if not devices:
            logger.warning("No Claude devices found")
            return False
        return await self.connect(devices[0])

    def set_notification_callback(self, callback: Callable[[dict], None]) -> None:
        """
        Set callback for incoming BLE notifications.

        Callback receives decoded JSON dict (not raw string).
        """
        self._notification_callback = callback

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle incoming BLE notification data, decode JSON and dispatch."""
        try:
            text = data.decode("utf-8").strip()
            if not text:
                return

            # Try to decode as JSON; if invalid, still dispatch raw text
            msg = NUSJSONCodec.decode_message(text)
            logger.debug(f"[BLE RX] {text[:80]}{'...' if len(text) > 80 else ''}")
            if self._notification_callback:
                self._notification_callback(msg or {"raw": text})
        except Exception as e:
            logger.error(f"Failed to handle BLE notification: {e}")

    async def write(self, data: str) -> bool:
        """Write data to the NUS RX characteristic."""
        if not self.is_connected:
            logger.warning("Not connected, cannot write")
            return False
        try:
            await self.client.write_gatt_char(
                NUS_RX_UUID,
                data.encode("utf-8")
            )
            logger.debug(f"BLE TX: {data[:100]}")
            return True
        except Exception as e:
            logger.error(f"Failed to write: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the BLE device."""
        if self.client:
            try:
                await self.client.stop_notify(NUS_TX_UUID)
                await self.client.disconnect()
            except Exception as e:
                logger.error(f"Error during disconnect: {e}")
        self._connected = False
        self.client = None
        logger.info("Disconnected")

    async def run(self) -> None:
        """Run the BLE connection indefinitely, reconnecting on disconnect."""
        while True:
            if not self.is_connected:
                if await self.connect_first():
                    logger.info("Connected, monitoring...")
                else:
                    logger.info("Retrying in 5 seconds...")
                    await asyncio.sleep(5)
            else:
                # Wait for disconnect
                try:
                    await asyncio.sleep(1)
                except Exception:
                    break
