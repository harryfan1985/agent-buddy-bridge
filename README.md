# Agent Buddy Bridge

BLE Bridge connecting M5StickC Plus hardware buddy to Hermes Agent for physical button approvals.

> **Requires PR #11812** ([NousResearch/hermes-agent#11812](https://github.com/NousResearch/hermes-agent/pull/11812))
> to be merged for full integration. Fallback mode works without it.

## Architecture

```
M5StickC Plus (BLE Peripheral, Claude Desktop Buddy firmware)
       ↕ BLE NUS (Nordic UART Service)
BLECentral (ble_central.py, bleak/CoreBluetooth)
       ↓ JSON over BLE
HTTPServer (:8765) ← POST /buddy/state + X-Session-Key header
       ↑
       Hermes BuddyPlatformAdapter.send_exec_approval()
       (via platform_class: "agent_buddy_bridge.platform.BuddyPlatformAdapter")
       ↑ Hermes Gateway
       Hermes AIAgent + approval_callback

M5StickC Button Press
       ↕ BLE notification
BLECentral._handle_notification()
       ↓
prompt_id → session_key lookup
       ↓ POST /internal/approve {session_key, choice}
HTTPServer.handle_internal_approve()
       ↓ (PR #11812 merged)
Hermes /internal/approve → resolve_gateway_approval()
       ↓ (fallback without PR #11812)
Approval Relay (:8766) → resolve_gateway_approval()
```

## Two-Process Design

This project runs as **two processes**:

1. **BuddyBridge** (`main.py`, port 8765)
   - BLE Central (connects to M5StickC)
   - HTTP Server (receives state from Hermes, proxies approvals)

2. **Approval Relay** (`approval_relay.py`, port 8766) — **fallback only**
   - Used when PR #11812 is NOT yet merged
   - Calls Hermes's `resolve_gateway_approval()` directly

## Installation

```bash
# 1. Install agent-buddy-bridge (editable so Hermes can load it)
cd ~/code/agent-buddy-bridge
pip install -e .

# 2. Hermes must be installed at ~/.hermes/hermes-agent/

# 3. Install BLE dependencies (macOS)
pip install bleak aiohttp
# bleak requires pyobjc — pre-built wheels available on macOS
```

## Hermes Configuration

Add to `~/.hermes/config.yaml`:

```yaml
# PR #11812 合并后（推荐）
platforms:
  buddy:
    enabled: true
    platform_class: "agent_buddy_bridge.platform.BuddyPlatformAdapter"
    bridge_url: "http://localhost:8765"
    hermes_approve_url: "http://localhost:8642"

plugins:
  enabled:
    - buddy-bridge   # → hermes_plugin/
```

## Running

```bash
# BuddyBridge (always needed)
python -m hermes_buddy_bridge.main \
    --http-port 8765 \
    --hermes-approve-url http://localhost:8642 \
    --relay-url http://localhost:8766

# Approval Relay (fallback only — not needed after PR #11812 merges)
python -m hermes_buddy_bridge.approval_relay \
    --hermes-home ~/.hermes \
    --port 8766
```

## HTTP Endpoints

| Port | Method | Path | Direction | Body |
|------|--------|------|-----------|------|
| 8765 | POST | `/buddy/state` | Hermes → Bridge | Session state JSON + `X-Session-Key` |
| 8765 | GET | `/buddy/status` | Hermes → Bridge | — |
| 8765 | POST | `/internal/approve` | Bridge → Hermes | `{"session_key","choice"}` |
| 8766 | POST | `/approve` | Bridge → Hermes | `{"session_key","choice"}` (fallback) |

## Project Structure

```
agent-buddy-bridge/
├── README.md
├── LICENSE
├── requirements.txt          # bleak, aiohttp
├── hermes_plugin/            # Hermes Plugin (pre_tool_call hook)
│   ├── __init__.py           # register() + _on_pre_tool_call()
│   └── plugin.yaml
└── hermes_buddy_bridge/      # BuddyBridge main program
    ├── __init__.py
    ├── platform.py           # BuddyPlatformAdapter + BuddyApprovalCallback
    ├── ble_central.py        # BLE Central (bleak, macOS CoreBluetooth)
    ├── json_codec.py          # NUS JSON encode/decode
    ├── http_server.py         # HTTP server (:8765, state + /internal/approve)
    ├── approval_relay.py      # Approval Relay (:8766, fallback)
    └── main.py                # Bridge entry point
```

## PR #11812 Key Features

| Feature | Impact |
|---------|--------|
| `platform_class` config | Register BuddyPlatformAdapter without modifying Hermes core |
| `pre_tool_call {"action": "approve"}` | Plugin can approve tools without built-in approval guard |
| `approval_callback` param in AIAgent | Button decisions reach Hermes without Approval Relay process |

## References

- Claude Desktop Buddy firmware: `anthropics/claude-desktop-buddy`
- PR #11812: `NousResearch/hermes-agent/pull/11812`
- PR #11816 (implementation): `NousResearch/hermes-agent/pull/11816`

## License

MIT
