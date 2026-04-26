"""
BuddyBridge Hermes Plugin.

Provides pre_tool_call hook for BuddyBridge approval integration.

This plugin enables the M5StickC Plus to approve/deny Hermes dangerous
commands through physical button presses.

Installation (two options):

  Option A — pip editable install (recommended):
    cd agent-buddy-bridge && pip install -e .
    → Hermes auto-discovers via pip entry_points or plugin scan

  Option B — symlink to plugins dir:
    ln -s ~/code/agent-buddy-bridge/hermes_plugin ~/.hermes/plugins/buddy-bridge

Then in ~/.hermes/config.yaml:
    plugins:
      enabled:
        - buddy-bridge

    platforms:
      buddy:
        enabled: true
        platform_class: "agent_buddy_bridge.platform.BuddyPlatformAdapter"
        bridge_url: "http://localhost:8765"
        hermes_approve_url: "http://localhost:8642"

Requires PR #11812 ( NousResearch/hermes-agent ) merged for:
  - platform_class config support
  - pre_tool_call {"action": "approve"} directive
  - approval_callback parameter to AIAgent
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# pre_tool_call hook
# ------------------------------------------------------------------

# Approved tool→command patterns (plugin-side allowlist).
# Tools matching this list return {"action": "approve"} from pre_tool_call,
# bypassing Hermes's built-in dangerous-command guard entirely.
# This is for tools that BuddyBridge explicitly handles via send_exec_approval().
#
# Format: tool_name → list of command patterns (substring match)
_APPROVED_PATTERNS: Dict[str, list[str]] = {
    "terminal": ["echo", "ls", "pwd", "git status", "git log", "git diff --stat"],
}


def _match_approved(tool_name: str, args: Dict[str, Any]) -> bool:
    """Check if tool+args match the plugin-side allowlist."""
    patterns = _APPROVED_PATTERNS.get(tool_name, [])
    if not patterns:
        return False
    value = ""
    if tool_name == "terminal":
        value = args.get("command", "")
    for pat in patterns:
        if pat in value:
            return True
    return False


async def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> list[dict]:
    """
    pre_tool_call hook — intercepts tools BEFORE execution.

    Returns:
      []                  — let Hermes handle it normally (no override)
      [{"action": "block", "message": "..."}]  — block the tool call
      [{"action": "approve", "message": "..."}] — approve immediately (PR #11812)

    For BuddyBridge, this hook is used for plugin-side allowlisted tools.
    The main approval flow goes through BuddyPlatformAdapter.send_exec_approval()
    which pushes state to M5StickC and waits for button press.
    """
    if not tool_name or not args:
        return []

    if _match_approved(tool_name, args):
        logger.info(
            f"[buddy-bridge] pre_tool_call approve: "
            f"tool={tool_name} task={task_id[:8]}"
        )
        return [{"action": "approve", "message": "BuddyBridge plugin approved"}]

    return []


# ------------------------------------------------------------------
# Plugin registration
# ------------------------------------------------------------------

def register(ctx) -> None:
    """
    Register BuddyBridge hooks with Hermes plugin system.

    Args:
        ctx: Hermes PluginContext — provides ctx.register_hook(),
                                      ctx.register_tool(), etc.
    """
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    logger.info("[buddy-bridge] plugin registered: pre_tool_call hook active")
