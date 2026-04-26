"""
JSON Codec for BLE NUS protocol.

Handles encoding/decoding of JSON messages between Hermes Gateway and M5StickC.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class NUSJSONCodec:
    """Encode/decode JSON messages for NUS transport."""

    @staticmethod
    def encode_state(state: dict) -> str:
        """
        Encode session state for BLE transmission.

        Format: JSON object per line, newline-delimited.
        """
        return json.dumps(state, ensure_ascii=False)

    @staticmethod
    def decode_message(data: str) -> Optional[dict]:
        """
        Decode incoming NUS message.

        Handles:
        - Heartbeat snapshots (Desktop → Device)
        - Permission decisions (Device → Desktop)
        - Command acknowledgements
        - Time sync
        """
        data = data.strip()
        if not data:
            return None

        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to decode JSON: {e}, data: {data[:100]}")
            return None

    @staticmethod
    def encode_permission(prompt_id: str, decision: str) -> str:
        """
        Encode permission decision for BLE transmission.

        Args:
            prompt_id: The approval request ID
            decision: "once" (approve) or "deny"
        """
        return json.dumps({
            "cmd": "permission",
            "id": prompt_id,
            "decision": decision
        }, ensure_ascii=False)

    @staticmethod
    def encode_status_request() -> str:
        """Encode status request command."""
        return json.dumps({"cmd": "status"}, ensure_ascii=False)

    @staticmethod
    def is_permission_response(msg: dict) -> bool:
        """Check if message is a permission decision from device."""
        return msg.get("cmd") == "permission"

    @staticmethod
    def get_permission_data(msg: dict) -> Optional[tuple[str, str]]:
        """
        Extract permission data from message.

        Returns:
            (prompt_id, decision) if valid, None otherwise
        """
        if not NUSJSONCodec.is_permission_response(msg):
            return None
        pid = msg.get("id", "")
        decision = msg.get("decision", "")
        if not pid or decision not in ("once", "deny"):
            return None
        return (pid, decision)
