"""
Platform Backend abstraction for BuddyBridge.

Each backend handles approval resolution for a specific platform
(Hermes, BitFun, etc.). The bridge dispatches button decisions to
all enabled backends — each backend knows its own protocol.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

logger = logging.getLogger(__name__)


class PlatformBackend(ABC):
    """
    Abstract backend that resolves approval decisions for a platform.

    When M5StickC button is pressed, the bridge dispatches the decision
    to all enabled PlatformBackend instances. Each backend translates
    the decision into its platform-specific protocol.
    """

    @abstractmethod
    async def resolve(
        self,
        *,
        session_key: str = "",
        tool_id: str = "",
        choice: str = "once",
    ) -> bool:
        """
        Resolve an approval decision on the target platform.

        Args:
            session_key: Platform session key (Hermes uses this)
            tool_id: Tool identifier (BitFun uses this)
            choice: "once", "deny", "always", or "session"

        Returns:
            True if the resolution was delivered successfully
        """
        ...

    @abstractmethod
    async def health(self) -> Dict[str, Any]:
        """
        Platform health check.

        Returns:
            Dict with at least {"status": "ok"/"error", "platform": "<name>"}
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g. "hermes", "bitfun")."""
        ...

    async def close(self) -> None:
        """Clean up resources. Override in subclasses if needed."""
        pass
