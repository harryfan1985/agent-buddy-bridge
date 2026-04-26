# Agent Buddy Bridge

BLE Bridge connecting M5StickC Plus hardware buddy to Hermes Agent for physical button approvals.

## Overview

```
M5StickC Plus (BLE Peripheral)
        ↓ BLE NUS (Nordic UART Service)
   Mac (Python BLE Central + Hermes Bridge)
        ↓ HTTP POST/GET
   Hermes Gateway (BuddyAdapter)
        ↓
   Hermes AIAgent
```

## Features

- Physical button (A/B) approval for Hermes Agent dangerous commands
- Real-time session state display on M5StickC Plus
- BLE NUS protocol compatible with Claude Desktop Buddy firmware
- Seamless integration with Hermes Agent Gateway

## Architecture

- **M5StickC Plus**: BLE Peripheral with screen + buttons (firmware from Claude Desktop Buddy project)
- **Mac BLE Central**: Python bridge using `bleak` library
- **Hermes BuddyAdapter**: Custom platform adapter for Hermes Gateway

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bridge
python -m hermes_buddy_bridge
```

## Project Structure

```
agent-buddy-bridge/
├── README.md
├── LICENSE
├── requirements.txt
├── hermes_buddy_bridge/
│   ├── __init__.py
│   ├── ble_central.py     # BLE Central (bleak)
│   ├── http_server.py     # Receives Hermes state
│   ├── http_client.py     # Posts button decisions
│   ├── json_codec.py      # NUS JSON encoding/decoding
│   └── main.py            # Entry point
└── buddy_adapter/          # Hermes Gateway adapter
    └── buddy.py
```

## Documentation

See [Wiki](https://github.com/harryfan1985/agent-buddy-bridge/wiki) for detailed documentation.

## License

MIT
