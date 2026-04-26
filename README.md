# Agent Buddy Bridge

BLE Bridge connecting M5StickC Plus hardware buddy to Hermes Agent for physical button approvals.

## Architecture

```
M5StickC Plus (BLE Peripheral, Claude Desktop Buddy firmware)
       ↕ BLE NUS (Nordic UART Service)
BLECentral (ble_central.py, bleak/CoreBluetooth)
       ↓ JSON over BLE
HTTPServer (:8765) ← POST /buddy/state + X-Session-Key header
       ↑
       Hermes BuddyAdapter (webhook caller)
       ↑ (Hermes Gateway calls adapter.send_exec_approval())
       Hermes AIAgent

M5StickC Button Press
       ↕ BLE notification
BLECentral._handle_notification()
       ↓
prompt_id → session_key lookup
       ↓ POST /approve {session_key, choice}
ApprovalRelay (:8766)
       ↓
Hermes tools/approval.py :: resolve_gateway_approval()
```

## Two-Process Design

This project runs as **two separate processes**:

1. **Approval Relay** (`approval_relay.py`, port 8766)
   - Receives button decisions from the bridge
   - Calls Hermes's internal `resolve_gateway_approval()` directly
   - Must be able to import from `~/.hermes/hermes-agent/`

2. **Main Bridge** (`main.py`)
   - BLE Central (connects to M5StickC)
   - HTTP Server (receives state from Hermes)
   - HTTP Client (posts decisions to Approval Relay)

## Installation

```bash
# System dependencies (macOS)
# bleak requires pyobjc — install via pip (pre-built wheels available):
pip install bleak aiohttp

# Hermes Agent must be installed at ~/.hermes/hermes-agent/
```

## Running

```bash
# Terminal 1: Start Approval Relay
python -m hermes_buddy_bridge.approval_relay \
    --hermes-home ~/.hermes \
    --port 8766

# Terminal 2: Start Main Bridge
python -m hermes_buddy_bridge.main \
    --http-port 8765 \
    --relay-url http://localhost:8766
```

## Hermes Integration

Hermes calls the bridge via its platform adapter system. The BuddyAdapter must be registered in `~/.hermes/config.yaml` under `platforms:`, pointing to `http://localhost:8765/buddy/state`.

For detailed integration steps, see the [Wiki](https://github.com/harryfan1985/agent-buddy-bridge/wiki).

## Protocol

### BLE NUS (Nordic UART Service)
- Service UUID: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- RX (write): `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- TX (notify): `6e400003-b5a3-f393-e0a9-e50e24dcca9e`

### HTTP Endpoints

| Port | Method | Path | Direction | Body |
|------|--------|------|-----------|------|
| 8765 | POST | `/buddy/state` | Hermes → Bridge | Session state JSON + `X-Session-Key` header |
| 8765 | GET | `/buddy/status` | Hermes → Bridge | — |
| 8766 | POST | `/approve` | Bridge → Hermes | `{"session_key","choice"}` |

### M5StickC → Bridge Message Format
```json
{"cmd": "permission", "id": "req_xxx", "decision": "once|deny"}
```

## Project Structure

```
hermes_buddy_bridge/
├── __init__.py
├── ble_central.py      # BLE Central (bleak, macOS CoreBluetooth)
├── json_codec.py       # NUS JSON encode/decode
├── http_server.py      # HTTP server (aiohttp, :8765)
├── http_client.py      # HTTP client → Approval Relay (:8766)
├── approval_relay.py   # Approval resolution relay server (:8766)
└── main.py             # Bridge entry point
```

## License

MIT
