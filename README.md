# Agent Buddy Bridge

BLE Bridge connecting M5StickC Plus hardware buddy to AI agent platforms (Hermes Agent, BitFun ADE) for physical button approvals via a pluggable backend system.

> **Hermes v0.12.0 ready.** Core approval hooks and platform plugin support are merged.
> **BitFun support added.** EventSubscriber-based integration via `--platforms bitfun`.

## Approval Flow

The same M5StickC button can approve commands from multiple platforms simultaneously. Each platform registers a backend that handles resolution in its own protocol.

```
Hermes Agent                          BitFun ADE
    │                                     │
    ▼                                     ▼
pre_approval_request hook            ConfirmationNeeded event
    │                                     │
    └──────────┬──────────────────────────┘
               ▼
         BuddyBridge (:8765)
         PlatformBackend dispatch
               │
               ▼
         BLE → M5StickC screen
               │
               ▼
         Button press (BLE notify)
               │
     ┌─────────┴─────────┐
     ▼                   ▼
HermesBackend        BitFunBackend
POST /internal       POST /buddy/approve
→ resolve_gateway    → confirm_tool() / reject_tool()
_approval()
```

**Two channels (M5StickC + Telegram), one goal.** First to arrive wins; the other is idempotent (returns 0).

### Hermes-specific flow (legacy diagram)

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

---

## Prerequisites

| Component | Requirement | Check |
|-----------|------------|-------|
| **OS** | macOS (CoreBluetooth for BLE) | `uname -s` |
| **System Python** | 3.9+ with pyobjc | `/usr/bin/python3 --version` |
| **Hermes venv** | `~/.hermes/hermes-agent/venv/bin/python` | Hermes installed at `~/.hermes/` |
| **Hermes version** | v0.12.0+ | `hermes --version` |
| **M5StickC Plus** | Flashed with Claude Desktop Buddy firmware | Device broadcasts as `Claude-XXXX` |
| **Python packages** | bleak, aiohttp | `pip list \| grep -E "bleak\|aiohttp"` |

**BLE permissions:** macOS will prompt for Bluetooth access on first run. Grant it in System Settings → Privacy → Bluetooth.

---

## Installation & Setup

### Step 1: Clone and install

```bash
cd ~/code
git clone https://github.com/harryfan1985/agent-buddy-bridge.git
cd agent-buddy-bridge
```

**Two install methods** — pick one:

#### Option A: pip editable install (recommended)

```bash
# 1. Install the package itself (editable, so code changes take effect immediately)
/usr/bin/pip3 install -e .

# 2. Install BLE dependencies into system Python
/usr/bin/pip3 install bleak aiohttp
```

Hermes auto-discovers the plugin via `pip` entry points or plugin scan.

#### Option B: Symlink plugin (no pip needed)

```bash
# 1. Install BLE dependencies
/usr/bin/pip3 install bleak aiohttp

# 2. Symlink plugin into Hermes plugins directory
ln -s ~/code/agent-buddy-bridge/hermes_plugin ~/.hermes/plugins/buddy-bridge
```

### Step 2: Configure Hermes

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

> **⚠️ Dangerous operation.** Editing `~/.hermes/config.yaml` requires care. Always backup first:
> ```bash
> cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.$(date +%Y%m%d)
> ```

**Verify config:**

```bash
# Check YAML syntax
python3 -c "import yaml; yaml.safe_load(open('$HOME/.hermes/config.yaml')); print('OK')"

# Check plugin loaded
grep -n "buddy-bridge" ~/.hermes/config.yaml
```

### Step 3: Restart Hermes gateway

```bash
hermes gateway restart
```

---

## Running

The bridge runs as **two processes**. Start them in separate terminals (or use `screen`/`tmux`):

### Terminal 1: BuddyBridge (BLE + HTTP)

```bash
cd ~/code/agent-buddy-bridge

# Must use SYSTEM Python (bleak requires pyobjc / CoreBluetooth)

# Default: Hermes only (backward compatible)
/usr/bin/python3 -m hermes_buddy_bridge.main \
    --http-port 8765 \
    --relay-url http://localhost:8766

# Or: Hermes + BitFun dual-platform
/usr/bin/python3 -m hermes_buddy_bridge.main \
    --platforms hermes,bitfun \
    --bitfun-url http://localhost:48765

# Or: BitFun only
/usr/bin/python3 -m hermes_buddy_bridge.main \
    --platforms bitfun \
    --bitfun-url http://localhost:48765
```

Expected output:
```
Starting Hermes Buddy Bridge...
HTTP server listening on http://localhost:8765
  POST /buddy/state (Hermes → M5StickC)
  GET  /buddy/status
BLE connected to Claude-0C1E
Bridge started. Press Ctrl+C to stop.
```

If the device isn't found immediately, the bridge enters background scan mode and will connect when it appears.

### Terminal 2: Approval Relay

```bash
cd ~/code/agent-buddy-bridge

# Must use HERMES VENV Python (imports tools.approval)
~/.hermes/hermes-agent/venv/bin/python \
    -m hermes_buddy_bridge.approval_relay \
    --hermes-home ~/.hermes \
    --port 8766
```

Expected output:
```
Imported Hermes resolve_gateway_approval OK
Approval relay listening on http://localhost:8766
  POST /approve {session_key, choice}
  GET  /health
```

### Running as background services

```bash
# BuddyBridge (system Python) — Hermes only
nohup /usr/bin/python3 -m hermes_buddy_bridge.main \
    --http-port 8765 --relay-url http://localhost:8766 \
    > ~/.hermes/logs/buddy-bridge.log 2>&1 &

# BuddyBridge (system Python) — Hermes + BitFun
nohup /usr/bin/python3 -m hermes_buddy_bridge.main \
    --platforms hermes,bitfun --bitfun-url http://localhost:48765 \
    > ~/.hermes/logs/buddy-bridge.log 2>&1 &

# Approval Relay (Hermes venv Python)
nohup ~/.hermes/hermes-agent/venv/bin/python \
    -m hermes_buddy_bridge.approval_relay \
    --hermes-home ~/.hermes --port 8766 \
    > ~/.hermes/logs/approval-relay.log 2>&1 &
```

---

## Verification

Run these checks in order to confirm everything works:

### 1. Both processes alive

```bash
pgrep -af "hermes_buddy_bridge"

# Should show TWO lines:
#   ... python -m hermes_buddy_bridge.main ...
#   ... python -m hermes_buddy_bridge.approval_relay ...
```

### 2. Health endpoints

```bash
# BuddyBridge
curl -s http://localhost:8765/health | python3 -m json.tool
# → {"status": "ok", "service": "buddy-bridge"}

# Approval Relay
curl -s http://localhost:8766/health | python3 -m json.tool
# → {"status": "ok", "service": "approval-relay", "hermes_loaded": true}
```

### 3. BLE device connection

```bash
curl -s http://localhost:8765/buddy/status | python3 -m json.tool
# → {"connected": true, "device_name": "Claude-0C1E", "pending_prompts": 0}
```

**If `connected: false`:** Power on the M5StickC and wait 5-10 seconds for auto-reconnect.

### 4. Approval path (idempotent test)

```bash
# This calls resolve_gateway_approval() with a fake session_key.
# Returns resolved=0 when no real approval is pending — this is normal.
curl -s -X POST http://localhost:8765/internal/approve \
  -H "Content-Type: application/json" \
  -d '{"session_key":"test","choice":"once"}' | python3 -m json.tool

# → {"status": "ok", "via": "approval-relay", "resolved": 0}
```

`"via": "approval-relay"` confirms the full path: Bridge → Relay → `resolve_gateway_approval()`.

### 5. Plugin loaded in Hermes

```bash
grep -i "buddy-bridge" ~/.hermes/logs/gateway.log | tail -3
# → [buddy-bridge] plugin registered: pre_tool_call hook active
```

### 6. End-to-end test (trigger a real approval)

Send a dangerous command to Hermes on Telegram:

```
rm test_file
```

Expected:
- M5StickC screen shows approval prompt
- Telegram shows `/approve` / `/deny` prompt
- Pressing M5StickC button resolves the approval
- `post_approval_response` hook clears the M5StickC screen

---

## HTTP Endpoints

| Port | Method | Path | Direction | Body |
|------|--------|------|-----------|------|
| 8765 | POST | `/buddy/state` | Platforms → Bridge | State JSON + `X-Session-Key` (from approval hook / event subscriber) |
| 8765 | GET | `/buddy/status` | Health check | — |
| 8765 | GET | `/health` | Bridge health | — |
| 8765 | POST | `/internal/approve` | Bridge → Relay (legacy) | `{"session_key", "choice"}` → relay (:8766) |
| 8766 | POST | `/approve` | Relay receive (Hermes) | `{"session_key", "choice"}` → `resolve_gateway_approval()` |
| 8766 | GET | `/health` | Relay health | — |
| 48765 | POST | `/buddy/approve` | Bridge → BitFun | `{"tool_id", "choice"}` → `confirm_tool()` / `reject_tool()` |
| 48765 | GET | `/health` | BitFun bridge health | — |

## CLI Reference

```
/usr/bin/python3 -m hermes_buddy_bridge.main [options]

Options:
  --http-port PORT           HTTP server port (default: 8765)
  --platforms P1,P2,...      Comma-separated platform list: hermes, bitfun
                             (default: hermes)
  --hermes-approve-url URL   Hermes internal approve URL (default: http://localhost:8642)
  --relay-url URL            Approval Relay fallback URL (default: http://localhost:8766)
  --bitfun-url URL           BitFun callback endpoint URL (default: http://localhost:48765)
```

---

## Troubleshooting

### BLE: device not found / not connecting

```bash
# Scan for BLE devices (system Python required)
/usr/bin/python3 << 'EOF'
import asyncio
from bleak import BleakScanner

async def scan():
    devices = await BleakScanner.discover(timeout=5.0)
    for d in devices:
        print(f"{d.name or '(unnamed)'} | {d.address} | RSSI: {d.rssi}")
    return devices

devices = asyncio.run(scan())
print(f"\nFound {len(devices)} device(s)")
EOF
```

Look for a device named `Claude-XXXX`. If not found:
- Ensure M5StickC is powered on and not connected to another host
- Check Bluetooth is enabled in macOS System Settings
- Try restarting the M5StickC

### "Platform 'buddy' is registered but adapter creation failed"

```bash
# Check the plugin is actually found by Hermes
ls -la ~/.hermes/plugins/buddy-bridge/
# Should show __init__.py and plugin.yaml

# Check plugin.yaml format
python3 -c "import yaml; yaml.safe_load(open('$HOME/.hermes/plugins/buddy-bridge/plugin.yaml')); print('OK')"

# If symlink is broken, recreate it
rm ~/.hermes/plugins/buddy-bridge 2>/dev/null
ln -s ~/code/agent-buddy-bridge/hermes_plugin ~/.hermes/plugins/buddy-bridge
```

### Approval Relay: "Failed to import Hermes approval module"

```bash
# Check venv Python exists and has access to Hermes
~/.hermes/hermes-agent/venv/bin/python -c "import sys; sys.path.insert(0, '$HOME/.hermes/hermes-agent'); from tools.approval import resolve_gateway_approval; print('OK')"
```

**Must use Hermes venv Python**, not system Python. The relay imports `tools.approval` which is only available inside the Hermes checkout.

### "via": "approval-relay" but resolved=0

This is expected for the test. `resolve_gateway_approval()` returns the number of approvals resolved — 0 means no pending approval matched the fake session_key. This confirms the function is callable and working correctly.

### Permissions: Bluetooth access denied

macOS System Settings → Privacy & Security → Bluetooth → ensure Terminal (or your terminal app) is enabled.

---

## Hermes Interface Matrix (v0.12.0)

| # | Capability | Status | PR |
|---|-----------|--------|----|
| 1 | `pre_approval_request` hook | ✅ Merged | [#16776](https://github.com/NousResearch/hermes-agent/pull/16776) |
| 2 | `post_approval_response` hook | ✅ Merged | [#16776](https://github.com/NousResearch/hermes-agent/pull/16776) |
| 3 | Plugin platform adapter (`platform_registry`) | ✅ Built-in | — (IRC/Teams plugins are examples) |
| 4 | `resolve_gateway_approval()` | ✅ Always available | — |
| 5 | Approval wake on session cleanup | ✅ Merged | [#18171](https://github.com/NousResearch/hermes-agent/pull/18171) |
| 6 | `pre_tool_call {"action": "approve"}` | ❌ Open | [#11816](https://github.com/NousResearch/hermes-agent/pull/11816) |

**Prerequisite check script** (paste into terminal to verify everything at once):

```bash
#!/bin/bash
echo "=== Agent Buddy Bridge — Prerequisites Check ==="
echo ""

# OS
echo -n "OS: "; uname -s

# System Python
echo -n "System Python: "; /usr/bin/python3 --version 2>&1

# Hermes Python
echo -n "Hermes Python: "; ~/.hermes/hermes-agent/venv/bin/python --version 2>&1 || echo "NOT FOUND"

# Hermes version
echo -n "Hermes version: "; ~/.hermes/hermes-agent/venv/bin/python -c "import hermes_cli; print(hermes_cli.__version__)" 2>&1 || echo "NOT FOUND"

# bleak
echo -n "bleak: "; /usr/bin/python3 -c "import bleak; print(bleak.__version__)" 2>&1 || echo "NOT INSTALLED"

# aiohttp
echo -n "aiohttp: "; /usr/bin/python3 -c "import aiohttp; print(aiohttp.__version__)" 2>&1 || echo "NOT INSTALLED"

# Plugin symlink
echo -n "Plugin: "; ls ~/.hermes/plugins/buddy-bridge/plugin.yaml 2>/dev/null && echo "OK" || echo "NOT FOUND"

# BLE scan (quick check)
echo -n "BLE scan: "; /usr/bin/python3 -c "
import asyncio, bleak
async def s():
    d = await bleak.BleakScanner.discover(timeout=2.0)
    names = [x.name for x in d if x.name and 'Claude' in x.name]
    print(names[0] if names else 'No Claude device found')
asyncio.run(s())
" 2>&1

echo ""
echo "=== Done ==="
```

---

## Architecture

```
M5StickC Plus (BLE Peripheral, Claude Desktop Buddy firmware)
    ↕ BLE NUS (Nordic UART Service)
BLECentral (ble_central.py, bleak/CoreBluetooth)
    ↕ JSON over BLE
HTTPServer (:8765)
    ↕ POST /buddy/state
HermesBuddyBridge (main.py)
    ↕ PlatformBackend dispatch
├── HermesBackend → Hermes /internal/approve or Approval Relay (:8766)
└── BitFunBackend → BitFun /buddy/approve (:48765)
```

### Platform Backends

The bridge supports multiple agent platforms via a pluggable backend system. Each backend implements the `PlatformBackend` interface:

| Backend | File | Resolution |
|---------|------|------------|
| **HermesBackend** | `backends/hermes_backend.py` | Two-path: Hermes `/internal/approve` → fallback Approval Relay `:8766` |
| **BitFunBackend** | `backends/bitfun_backend.py` | POST `/buddy/approve` to BitFun's embedded HTTP callback server |

Enable backends via `--platforms`:

```bash
# Hermes only (default — backward compatible)
--platforms hermes

# Both platforms
--platforms hermes,bitfun --bitfun-url http://localhost:48765

# BitFun only
--platforms bitfun --bitfun-url http://localhost:48765
```

## Two-Process Design

| Process | Python | Port | Role |
|---------|--------|------|------|
| **BuddyBridge** | `/usr/bin/python3` (system) | 8765 | BLE Central + HTTP server + PlatformBackend dispatch |
| **Approval Relay** | `~/.hermes/hermes-agent/venv/bin/python` | 8766 | Calls `resolve_gateway_approval()` directly (Hermes only) |

**Why two Pythons:** `bleak` requires system Python with `pyobjc` (CoreBluetooth). `resolve_gateway_approval()` requires Hermes venv. BitFun does not need the Approval Relay — its backend calls BitFun's HTTP callback directly.

## Project Structure

```
agent-buddy-bridge/
├── README.md
├── TODO.md
├── LICENSE
├── requirements.txt              # bleak, aiohttp
├── hermes_plugin/                # Hermes Plugin (pre_approval_request hook)
│   ├── __init__.py               # register() — hooks into approval lifecycle
│   └── plugin.yaml
└── hermes_buddy_bridge/          # BuddyBridge main program
    ├── __init__.py
    ├── main.py                   # Bridge entry point, PlatformBackend dispatch
    ├── platform.py               # BuddyPlatformAdapter (extends BasePlatformAdapter)
    ├── ble_central.py            # BLE Central (bleak, macOS CoreBluetooth)
    ├── json_codec.py             # NUS JSON encode/decode
    ├── http_server.py            # HTTP server (:8765)
    ├── http_client.py            # HTTP client
    ├── approval_relay.py         # Approval Relay (:8766, Hermes only)
    └── backends/                 # Platform backends
        ├── __init__.py           # PlatformBackend abstract base class
        ├── hermes_backend.py     # Hermes resolution (internal + relay)
        └── bitfun_backend.py     # BitFun resolution (HTTP callback)
```

## References

- Hermes PR #16776: `pre_approval_request` / `post_approval_response` hooks
- Hermes PR #11816: `pre_tool_call {"action": "approve"}` directive (optional, OPEN)
- Hermes `platform_registry`: `gateway/platform_registry.go`
- BitFun `ToolPipeline`: `confirmation_channels` / `confirm_tool()` / `reject_tool()` in `src/crates/core/src/agentic/tools/pipeline/tool_pipeline.rs`
- BitFun `EventSubscriber`: `src/crates/core/src/agentic/events/router.rs`
- Claude Desktop Buddy firmware: `anthropics/claude-desktop-buddy`

## License

MIT
