# Agent Buddy Bridge - 项目状态与 TODO

## 当前状态：v0.12.0 就绪

**核心审批链路已打通。** Hermes v0.12.0 合并的 hooks 和 platform_registry 填补了之前所有的关键空白。

### Hermes 接口能力 (v0.12.0)

| # | 能力 | 状态 | PR |
|---|------|------|----|
| 1 | `pre_approval_request` hook | ✅ 已合并 | [#16776](https://github.com/NousResearch/hermes-agent/pull/16776) (2026-04-28) |
| 2 | `post_approval_response` hook | ✅ 已合并 | [#16776](https://github.com/NousResearch/hermes-agent/pull/16776) |
| 3 | Plugin 注册平台适配器 (`platform_registry`) | ✅ 内置 | IRC/Teams 插件即范例 |
| 4 | `resolve_gateway_approval()` | ✅ 一直可用 | — |
| 5 | Session 清理时唤醒阻塞审批 | ✅ 已合并 | [#18171](https://github.com/NousResearch/hermes-agent/pull/18171) (2026-05-01) |

### 已验证可工作的组件

| 组件 | 端口 | 状态 |
|------|------|------|
| BuddyBridge HTTP Server | 8765 | ✅ 可运行 |
| Approval Relay | 8766 | ✅ 可运行，`hermes_loaded: true` |
| BLE Central (Claude-0C1E) | — | ✅ 已连接 |
| Fallback 路径测试 | — | ✅ `via: approval-relay` |
| pre_approval_request hook 触发 | — | ✅ session_key 可用 |
| plugin.yaml 注册 | — | ✅ Hermes 可发现 |

### 已解决的旧问题

| 旧问题 | 解决方案 |
|--------|---------|
| "M5StickC 不知道何时有审批" | `pre_approval_request` hook 主动推送 `{command, session_key}` |
| "session_key 无法从外部获取" | hook 回调携带 session_key |
| ""手动模式"不可行" | **已可行** — hook 通知 + Approval Relay 审批 = 完整闭环 |
| "需要 /internal/approve HTTP 端点" | 不再需要 — Approval Relay 直接调用 `resolve_gateway_approval()` |

---

## 剩余工作

### P1: BuddyPlatformAdapter 适配 `pre_approval_request` hook

**当前状态：** `platform.py` 中的 `BuddyPlatformAdapter` 基于旧架构设计（`send_exec_approval()` + `approval_callback`），尚未适配新的 hook 驱动架构。

**需要的变更：**

1. **`hermes_plugin/__init__.py`** — 注册 `pre_approval_request` hook（替换/补充现有的 `pre_tool_call` hook）：
   ```python
   def register(ctx):
       ctx.register_hook("pre_approval_request", _on_pre_approval_request)
       ctx.register_hook("post_approval_response", _on_post_approval_response)
   ```

2. **`hermes_plugin/plugin.yaml`** — 更新 hooks 列表：
   ```yaml
   hooks:
     - pre_approval_request
     - post_approval_response
   ```

3. **Hook 实现**：
   ```python
   async def _on_pre_approval_request(command, session_key, pattern_key, surface, **kwargs):
       """审批请求时推送状态到 BuddyBridge → M5StickC"""
       # POST /buddy/state to http://localhost:8765
       # Body: {command, session_key, pattern_key, surface}

   async def _on_post_approval_response(choice, command, session_key, **kwargs):
       """审批完成后清理 M5StickC 屏幕"""
       # POST /buddy/clear or similar to http://localhost:8765
   ```

4. **`http_server.py`** — 确保 `/buddy/state` 端点能接收 hook 推送的数据格式。

### P2: 端到端集成测试

- [ ] 触发真实危险命令 → M5StickC 屏幕显示审批提示
- [ ] M5StickC 按钮 → Approval Relay → agent 解除阻塞
- [ ] Telegram `/approve` 和 M5StickC 按钮并发 → 先到先得
- [ ] 超时 → 自动 deny → M5StickC 屏幕清理
- [ ] `/new` 重置 session → 阻塞审批自动 deny（PR #18171）

### P3: Optional — 等待 PR #11816 合并

| PR | 功能 | 影响 |
|----|------|------|
| [#11816](https://github.com/NousResearch/hermes-agent/pull/11816) | `pre_tool_call {"action": "approve"}` 指令 + `"plugin"` 审批模式 | 白名单命令自动放行，不触发 Telegram/M5StickC 审批 |

**不影响核心链路。** 仅用于 UX 优化（静默放行已知安全命令）。

---

## 审批流程（当前 v0.12.0 的实际路径）

```
Dangerous command detected
    ↓
approval.py: prompt_dangerous_approval()
    ├─→ pre_approval_request hook → Plugin → Bridge → BLE → M5StickC
    ├─→ Telegram notification (simultaneous)
    └─→ Agent thread blocks

M5StickC button OR Telegram /approve
    ↓
resolve_gateway_approval(session_key, "once")
    ↓
event.set() → Agent unblocked
    ↓
post_approval_response hook → Clean M5StickC screen
```

---

## 文件结构

```
agent-buddy-bridge/
├── README.md                   # 安装/使用/验证/排错完整指南
├── TODO.md                     # 本文件
├── LICENSE                     # MIT
├── requirements.txt            # bleak, aiohttp
├── hermes_plugin/              # Hermes Plugin
│   ├── __init__.py             # register() + hooks
│   └── plugin.yaml             # 插件元数据
└── hermes_buddy_bridge/        # BuddyBridge 主程序
    ├── __init__.py
    ├── platform.py             # BuddyPlatformAdapter
    ├── ble_central.py          # BLE Central (bleak)
    ├── json_codec.py           # NUS JSON 编解码
    ├── http_server.py          # HTTP 服务器 (:8765)
    ├── approval_relay.py       # Approval Relay (:8766)
    ├── http_client.py          # HTTP 客户端
    └── main.py                 # 入口程序
```
