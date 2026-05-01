# Agent Buddy Bridge

BLE Bridge connecting M5StickC Plus hardware buddy to Hermes Agent for physical button approvals.

> **Hermes v0.12.0 ready.** Core approval hooks and platform plugin support are merged.
> Only [PR #11816](https://github.com/NousResearch/hermes-agent/pull/11816) (`pre_tool_call approve`) remains open — and it's optional for the button approval flow.

## Approval Flow

```
Dangerous command detected (e.g. rm -rf /data)
    │
    ▼
tools/approval.py: prompt_dangerous_approval()
    │
    ├─→ 🔔 pre_approval_request hook
    │       → Plugin receives {command, session_key, pattern_key, surface="gateway"}
    │       → BuddyPlatformAdapter pushes to Bridge (:8765) → BLE → M5StickC screen
    │
    ├─→ 📱 Telegram notification (simultaneous)
    │       "⚠️ Dangerous command: rm -rf /data
    │        Reply /approve, /always, or /cancel"
    │
    └─→ ⏸️ Agent thread blocks (threading.Event.wait)
            │
    ┌───────┴───────┐
    ▼               ▼
 M5StickC button   Telegram /approve
 (BLE notify)      (slash command)
    │               │
    ▼               ▼
 Approval Relay    gateway._handle_approve_command()
    │               │
    └───────┬───────┘
            ▼
 resolve_gateway_approval(session_key, "once")
            │
            ▼
    event.set() → Agent unblocked
            │
            ▼
    🔔 post_approval_response hook
        → Clean up M5StickC screen
        → Command executes
```

**Two channels, one goal.** The user can approve from Telegram (at desk) or M5StickC button (away from desk). First to arrive wins; the other returns 0 (idempotent).

## Architecture

```
M5StickC Plus (BLE Peripheral, Claude Desktop Buddy firmware)
    ↕ BLE NUS (Nordic UART Service)
BLECentral (ble_central.py, bleak/CoreBluetooth)
    ↓ JSON over BLE
HTTPServer (:8765)
    ↑ POST /buddy/state (Hermes → Bridge, from pre_approval_request hook)
    ↓ POST /internal/approve (Bridge → Hermes, on button press)
    ↓
Approval Relay (:8766) → resolve_gateway_approval(session_key, choice)
    ↑
    Hermes tools/approval.py
```

## Hermes Interface Matrix (v0.12.0)

| # | Capability | Status | PR |
|---|-----------|--------|----|
| 1 | `pre_approval_request` hook | ✅ Merged | [#16776](https://github.com/NousResearch/hermes-agent/pull/16776) |
| 2 | `post_approval_response` hook | ✅ Merged | [#16776](https://github.com/NousResearch/hermes-agent/pull/16776) |
| 3 | Plugin platform adapter (`platform_registry`) | ✅ Built-in | — (IRC/Teams plugins are examples) |
| 4 | `resolve_gateway_approval()` | ✅ Always available | — |
| 5 | Approval wake on session cleanup | ✅ Merged | [#18171](https://github.com/NousResearch/hermes-agent/pull/18171) |
| 6 | `pre_tool_call {"action": "approve"}` | ❌ Open | [#11816](https://github.com/NousResearch/hermes-agent/pull/11816) |

**PR #11816** would add whitelist auto-approve (plugin skips dangerous-command guard for known-safe commands). It is **not required** for the physical button approval flow — the hook + relay path handles that independently.

## Two-Process Design

| Process | Python | Port | Role |
|---------|--------|------|------|
| **BuddyBridge** | `/usr/bin/python3` (system) | 8765 | BLE Central + HTTP server (Hermes ↔ M5StickC) |
| **Approval Relay** | `~/.hermes/hermes-agent/venv/bin/python` | 8766 | Calls `resolve_gateway_approval()` directly |

**Why two Pythons:** `bleak` requires system Python with pyobjc. `resolve_gateway_approval()` requires Hermes venv.

**Why the relay is needed (even in v0.12.0):** The button press arrives via BLE → `/internal/approve` on port 8765. There is no HTTP `/internal/approve` endpoint in Hermes gateway — `resolve_gateway_approval()` is a Python function. The relay bridges this gap by accepting HTTP and calling the function directly.

## Installation

```bash
cd ~/code/agent-buddy-bridge

# Editable install
pip install -e .

# BLE dependencies
pip install bleak aiohttp
```

## Hermes Configuration

Add to `~/.hermes/config.yaml`:

```yaml
# Register Buddy as a plugin platform adapter
gateway:
  platforms:
    buddy:
      enabled: true
      extra:
        bridge_url: "http://localhost:8765"

# Enable the buddy-bridge plugin (registers platform + hooks)
plugins:
  enabled:
    - buddy-bridge
```

## Running

```bash
# 1. BuddyBridge (system Python — always needed)
cd ~/code/agent-buddy-bridge && /usr/bin/python3 -m hermes_buddy_bridge.main \
    --http-port 8765 \
    --relay-url http://localhost:8766

# 2. Approval Relay (Hermes venv Python — always needed)
cd ~/code/agent-buddy-bridge && ~/.hermes/hermes-agent/venv/bin/python \
    -m hermes_buddy_bridge.approval_relay \
    --hermes-home ~/.hermes \
    --port 8766
```

## HTTP Endpoints

| Port | Method | Path | Direction | Body |
|------|--------|------|-----------|------|
| 8765 | POST | `/buddy/state` | Hermes → Bridge | Session state + `X-Session-Key` (from pre_approval_request hook) |
| 8765 | GET | `/buddy/status` | Health check | — |
| 8765 | POST | `/internal/approve` | Bridge → Hermes | `{"session_key", "choice"}` → relay (:8766) |
| 8766 | POST | `/approve` | Relay receive | `{"session_key", "choice"}` → `resolve_gateway_approval()` |

## Verification

```bash
# BuddyBridge health
curl http://localhost:8765/health

# BuddyBridge status
curl http://localhost:8765/buddy/status

# Approval Relay health
curl http://localhost:8766/health

# Test approval path (idempotent — returns {"resolved": 0} when nothing pending)
curl -X POST http://localhost:8765/internal/approve \
  -H "Content-Type: application/json" \
  -d '{"session_key":"test","choice":"once"}'
# → {"status":"ok","via":"approval-relay","resolved":0}
```

## Project Structure

```
agent-buddy-bridge/
├── README.md
├── LICENSE
├── requirements.txt              # bleak, aiohttp
├── hermes_plugin/                # Hermes Plugin (pre_approval_request hook)
│   ├── __init__.py               # register() — hooks into approval lifecycle
│   └── plugin.yaml
└── hermes_buddy_bridge/          # BuddyBridge main program
    ├── __init__.py
    ├── platform.py               # BuddyPlatformAdapter (extends BasePlatformAdapter)
    ├── ble_central.py            # BLE Central (bleak, macOS CoreBluetooth)
    ├── json_codec.py             # NUS JSON encode/decode
    ├── http_server.py            # HTTP server (:8765)
    ├── approval_relay.py         # Approval Relay (:8766)
    └── main.py                   # Bridge entry point
```

## References

- Hermes PR #16776: `pre_approval_request` / `post_approval_response` hooks
- Hermes PR #11816: `pre_tool_call {"action": "approve"}` directive (optional, OPEN)
- Hermes `platform_registry`: `gateway/platform_registry.py` (IRC plugin is the reference implementation)
- Claude Desktop Buddy firmware: `anthropics/claude-desktop-buddy`

## License

MIT
