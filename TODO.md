# Agent Buddy Bridge - 项目状态与 TODO

## 当前状态

### 已验证可工作的组件

| 组件 | 端口 | 状态 |
|------|------|------|
| BuddyBridge HTTP Server | 8765 | ✅ 运行中 |
| Approval Relay | 8766 | ✅ 运行中，`hermes_loaded: true` |
| BLE Central (Claude-0C1E) | - | ✅ 已连接 |
| Fallback 路径测试 | - | ✅ `via: approval-relay` |

### 已验证的功能

1. **BLE 扫描与连接** ✅
   - `Claude-0C1E` 设备发现成功
   - NUS Service (6e400001-b5a3-f393-e0a9-e50e24dcca9e) 确认
   - TX/RX Characteristics 可用

2. **Approval Relay** ✅
   - 成功导入 `resolve_gateway_approval`
   - `POST /approve {session_key, choice}` 端点可用
   - Fallback 路径 (`/internal/approve` → `:8766`) 已验证

3. **BuddyBridge HTTP Server** ✅
   - `POST /buddy/state` 接收 Hermes 状态
   - `GET /buddy/status` 返回设备状态
   - `pending_prompts` 追踪正常

## 无法打通的原因

### 核心瓶颈：PR #11812 未合并

完整双向链路需要：

```
Hermes → M5StickC: Hermes 发送审批请求（显示在设备屏幕上）
M5StickC → Hermes: 按钮审批（批准/拒绝）
```

| 路径 | 方向 | 依赖 | 状态 |
|------|------|------|------|
| Hermes → M5StickC (显示审批) | → | `BuddyPlatformAdapter` + PR #11812 | ❌ |
| M5StickC → Hermes (按钮审批) | ← | Approval Relay | ✅ (但无法自动触发) |

### "手动模式"方案分析（不可行）

尝试方案：M5StickC 看到审批后，通过 Approval Relay HTTP 端点注入审批结果。

**不可行的原因：**

1. **无通知通道** — Hermes 没有 BuddyPlatformAdapter 时，不会主动发送审批状态到任何外部系统。M5StickC 不知道何时有审批需要处理。

2. **session_key 无法获取** — `resolve_gateway_approval(session_key, choice)` 需要正确的 session_key。Hermes 的 `_pending_approvals` 是内部 dict，不暴露 API。无法自动发现当前等待审批的 session。

3. **双重等待冲突** — Hermes 会同时等待自己的 `/approve` 命令。即使 Approval Relay 注入了审批，Hermes 原生审批 UI 也会超时。

**结论：** 在不修改 Hermes 代码的情况下，无法打通从 M5StickC 按钮到 Hermes 审批的完整链路。

## 依赖的 PR

| PR | 内容 | 状态 |
|----|------|------|
| [#11812](https://github.com/NousResearch/hermes-agent/issues/11812) | `pre_tool_call approve` action + `platform_class` 支持 | ❌ 未合并 |
| [#11816](https://github.com/NousResearch/hermes-agent/pull/11816) | BuddyAdapter 实现 | ❌ 未合并 |

## 下一步

1. **等待 PR #11812 合并** — 合并后配置 `platforms.buddy` 即可完整打通
2. **PR 合并后的升级步骤：**
   - 更新 Hermes 到最新版本
   - 配置 `platforms.buddy` 和 `plugins.enabled`
   - 重启 Hermes gateway
   - 验证完整双向链路

## 架构图（目标状态）

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

## 配置摘要

### BuddyBridge (后台进程)

```bash
# BuddyBridge HTTP Server
cd ~/code/agent-buddy-bridge && ~/.hermes/hermes-agent/venv/bin/python -m hermes_buddy_bridge.main \
    --http-port 8765 \
    --hermes-approve-url http://localhost:8642 \
    --relay-url http://localhost:8766

# Approval Relay (fallback)
cd ~/code/agent-buddy-bridge && ~/.hermes/hermes-agent/venv/bin/python -m hermes_buddy_bridge.approval_relay \
    --hermes-home ~/.hermes \
    --port 8766
```

### Hermes 配置（PR #11812 合并后添加）

```yaml
platforms:
  buddy:
    enabled: true
    platform_class: "agent_buddy_bridge.platform.BuddyPlatformAdapter"
    bridge_url: "http://localhost:8765"
    hermes_approve_url: "http://localhost:8642"

plugins:
  enabled:
    - buddy-bridge
```

## 文件结构

```
agent-buddy-bridge/
├── README.md
├── LICENSE
├── requirements.txt          # bleak, aiohttp
├── hermes_plugin/           # Hermes Plugin (pre_tool_call hook)
│   ├── __init__.py
│   └── plugin.yaml
└── hermes_buddy_bridge/    # BuddyBridge main program
     ├── __init__.py
     ├── platform.py         # BuddyPlatformAdapter + BuddyApprovalCallback
     ├── ble_central.py      # BLE Central (bleak, macOS CoreBluetooth)
     ├── json_codec.py       # NUS JSON encode/decode
     ├── http_server.py      # HTTP server (:8765, state + /internal/approve)
     ├── approval_relay.py   # Approval Relay (:8766, fallback)
     └── main.py             # Bridge entry point
```
