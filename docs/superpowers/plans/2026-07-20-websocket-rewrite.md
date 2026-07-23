# dglab_sender WebSocketApp 架构重写 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `dglab_sender.py` 的同步 `create_connection` + `recv()` 架构替换为经过 SQUAD_DGLAB 真机验证的 `WebSocketApp` + 后台线程 + `on_message` 回调架构，解决波形发送到 SOCKET v2 后端失败的问题。

**Architecture:** 改 4 个文件。内部用 `_DglabWSClient` 类（WebSocketApp + daemon thread + on_message 分发）替代 `DglabSender` 类（同步 create_connection）。模块级 API 保持 `init_sender(config)` / `send_action(action, payload)` / `safe_stop()` / `is_paired()`。trigger.py 的 dglab 初始化路径从同步 `connect_and_pair()` 改为异步 `init_sender()` + `wait_for_pair()`。保留 HELLDIVER 的 ACTION_PROFILES、WAVEFORM_V3 模板（波形来自 SQUAD 真机验证）、_get_lan_ip() 自动检测、QR 码 os.startfile()。safety timer 用 threading.Timer + cancel + self-cleanup。

**Tech Stack:** websocket-client, numpy, opencv-python, mss, qrcode（可选）

---

## 审查历史

> 2026-07-20 — PRO + FLASH 初审：11 个问题 → 修正 Plan v1
> 2026-07-20 — PRO + LongCat 终审：3 致命 + 3 严重 + 3 中等 + 1 内存 bug → Plan v2（本文件）

| 轮次 | 发现 | 严重度 | 修复 |
|------|------|--------|------|
| v1 | F1-F4: config_loader/WSL/SAN IP/safe_stop 不对接 | **致命** ×4 | Plan v1 修正 |
| v1 | S1-S3: 波形名映射/测试脚本/异步无反馈 | 高 ×3 | Plan v1 修正 |
| v2 PRO | F1/F2: trigger.py 调 `connect_and_pair()` 不存在 → `AttributeError` | **致命** | trigger.py 改为异步初始化 + `wait_for_pair()` |
| v2 PRO | F3: death_pulse 频率 0A→BE（来自 SQUAD 模板），静默替换需标注 | **高** | Plan 明确标注，有意识采用 SQUAD 验证模板 |
| v2 PRO | M1: is_paired() 私有暴露 | 中 | 新增模块级 `is_paired()` 公开函数 |
| v2 LongCat | Unicode 框线在 Windows GBK cmd 下乱码 | 低 | 改纯 ASCII + 中文 |
| v2 LongCat | `_handle_app_msg` 净信息量=0 | 砍 | 内联为一 debug 行 |
| v2 LongCat | `_handle_break` 只有一处调用 | 砍 | 内联到 on_message |
| v2 LongCat | `_safety_timers` dict 不清理 → 内存泄漏 | Bug | timer 回调 self-cleanup |
| v2 LongCat | test_pairing.py 无 shock 警告 | UX | 每次发送前显示强度+倒计时 |
| v2 LongCat | Task 1 Step 2 一口气 500 行 | 粒度 | 拆为 2a-2e |

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/dglab_sender.py` | **重写** | `_DglabWSClient`（~220行）+ 模块级 API（~180行）+ helper functions（~60行）= ~460行 |
| `src/trigger.py` | **修改**（~10行） | dglab 初始化：砍 `connect_and_pair()`，改 `init_sender()` + `wait_for_pair()` |
| `tools/test_pairing.py` | **修改** | 适配新 API：`init_sender()` + `is_paired()` + shock 警告 |
| `src/runner_mock.py` | **修改**（+4行）| `TriggerDispatch(config)` 加 try/except |

### 不改的文件

`src/runner_real.py`、`src/state_machine.py`、`src/health_detector.py`、`src/roi_locator.py`、`src/effect_detector.py`、`src/screen_capture.py`、`src/config_loader.py`、`src/debug_overlay.py`、`src/control_panel.py`、`config.ini`、`requirements.txt` — **全部零改动。**

---

### Task 1: 重写 dglab_sender.py — _DglabWSClient 类骨架 + 生命周期

**Files:**
- Read reference: `E:\SQUAD_DGLAB\SQUAD_DGLAB\python_trigger\adapters\dglab_ws_client.py`（WebSocketApp 架构参考）
- Modify: `src/dglab_sender.py`

#### 架构概览

```
改后:
  _DglabWSClient 类 (内部, ~210 行)
    ├─ __init__(ws_url)
    ├─ start() / stop()
    ├─ _run_forever(): WebSocketApp(ping_interval=30) in daemon thread
    ├─ _on_open / _on_message / _on_error / _on_close
    ├─ is_paired(): bool
    └─ send_json(payload) -> bool

  模块级 API (~180 行)
    ├─ init_sender(config) → _DglabWSClient
    ├─ is_paired() → bool              ← 公开函数（PRO M1 修复）
    ├─ wait_for_pair(timeout) → bool    ← trigger.py 配对等待
    ├─ send_action(action, payload) → {"dispatched": bool}
    └─ safe_stop()

  模块级 helper (~60 行)
    ├─ _send_strength / _clear_channel / _stop_channel
    ├─ _schedule_safety_timer (含 self-cleanup)
    └─ _get_lan_ip (不变)
```

- [ ] **Step 1a: 阅读 SQUAD 参考代码**

```bash
# 阅读（不执行）
# E:\SQUAD_DGLAB\SQUAD_DGLAB\python_trigger\adapters\dglab_ws_client.py
# E:\SQUAD_DGLAB\SQUAD_DGLAB\python_trigger\adapters\dglab_sender.py
```

关键要点：`WebSocketApp(url, on_open/on_message/on_error/on_close)` → `run_forever()` in daemon thread、`on_message` 按 `msg["type"]` 分发、`send_json()` lock + auto-fill clientId/targetId。

- [ ] **Step 1b: 编写 _DglabWSClient 类骨架（生命周期 + 回调分发）**

```python
# 在 src/dglab_sender.py 中写入以下代码（替换整个文件）

"""
dglab_sender.py — DG-LAB SOCKET v2 真机发送器。

WebSocketApp + daemon thread + on_message 回调分发架构（SQUAD_DGLAB 真机验证）。
协议: DG-LAB SOCKET v2 (V3 波形格式, Coyote 3.0)
"""

import json
import logging
import os
import socket
import tempfile
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── V3 波形模板 (SQUAD_DGLAB 真机验证) ──
# 格式: 每段 100ms, 16 字符 hex (前 8 字符频率, 后 8 字符强度 00=0% 64=100%)
# 注: death_pulse 频率 BEBE(190) 来自 SQUAD 模板，替换了原先的 0A(10) 低频
#     因为 SQUAD 真机已确认 BEBE 能安全送达设备，而 0A 从未真机验证过。
WAVEFORM_V3 = {
    "weak_pulse": [
        "0A0A0A0A00000000", "0A0A0A0A14141414", "0A0A0A0A28282828",
        "0A0A0A0A3C3C3C3C", "0A0A0A0A50505050", "0A0A0A0A64646464",
        "0A0A0A0A64646464", "0A0A0A0A64646464", "0A0A0A0A00000000",
        "0A0A0A0A00000000", "0A0A0A0A00000000", "0A0A0A0A00000000",
    ],
    "death_pulse": [
        "BEBEBEBE64646464", "BEBEBEBE64646464", "BEBEBEBE64646464",
        "BEBEBEBE64646464", "BEBEBEBE64646464", "BEBEBEBE64646464",
        "BEBEBEBE64646464", "BEBEBEBE64646464", "BEBEBEBE64646464",
        "BEBEBEBE64646464", "BEBEBEBE64646464", "BEBEBEBE64646464",
        "BEBEBEBE64646464", "BEBEBEBE64646464", "BEBEBEBE64646464",
        "BEBEBEBE64646464", "BEBEBEBE64646464", "BEBEBEBE64646464",
        "BEBEBEBE64646464", "BEBEBEBE64646464",
    ],
    "heal_pulse": [
        "0A0A0A0A0A0A0A0A", "0A0A0A0A14141414", "0A0A0A0A1E1E1E1E",
        "0A0A0A0A28282828", "0A0A0A0A32323232", "0A0A0A0A32323232",
        "0A0A0A0A28282828", "0A0A0A0A1E1E1E1E", "0A0A0A0A14141414",
        "0A0A0A0A0A0A0A0A", "0A0A0A0A00000000", "0A0A0A0A00000000",
    ],
}

# ── 动作配置 ──
# waveform 字段是间接映射：injury_pulse 和 low_health 共用 weak_pulse
ACTION_PROFILES: dict[str, dict] = {
    "injury_pulse": {"channel": "A", "strength": 20, "duration": 2,
                     "waveform": "weak_pulse"},
    "death_pulse":  {"channel": "A", "strength": 40, "duration": 5,
                     "waveform": "death_pulse"},
    "heal_pulse":   {"channel": "A", "strength": 15, "duration": 2,
                     "waveform": "heal_pulse"},
    "low_health":   {"channel": "A", "strength": 10, "duration": 3,
                     "waveform": "weak_pulse"},
}

STOP_CLEAR_TO_ZERO_DELAY = 0.08


def _get_lan_ip() -> str:
    """获取本机 LAN IP（socket 连 8.8.8.8 自动检测）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ════════════════════════════════════════════════════════════════
# _DglabWSClient — WebSocketApp + 后台线程
# ════════════════════════════════════════════════════════════════

class _DglabWSClient:
    """DG-LAB SOCKET v2 WebSocket 长连接客户端（内部类）。"""

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self.client_id: Optional[str] = None
        self.target_id: Optional[str] = None
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._should_stop = False

    # ── 生命周期 ──

    def start(self):
        """启动后台连接线程（非阻塞）。"""
        if self._thread is not None and self._thread.is_alive():
            logger.info("dglab ws: already running")
            return
        self._should_stop = False
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="DGLabWSClient")
        self._thread.start()
        logger.info("dglab ws: started  target=%s", self._ws_url)

    def stop(self):
        """停止连接。"""
        self._should_stop = True
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        logger.info("dglab ws: stopped")

    def _run_forever(self):
        while not self._should_stop:
            try:
                from websocket import WebSocketApp  # noqa: F811
                self._ws = WebSocketApp(
                    self._ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except ImportError:
                logger.error("dglab ws: websocket-client 未安装。pip install websocket-client")
                return
            except Exception as e:
                logger.error("dglab ws: connection error: %s", e)
            if self._should_stop:
                break
            logger.info("dglab ws: reconnecting in 5s...")
            with self._lock:
                self.client_id = None
                self.target_id = None
            time.sleep(5)

    # ── WebSocket 回调 ──

    def _on_open(self, ws):
        logger.info("dglab ws: connected, waiting for clientId...")

    def _on_message(self, ws, raw_msg):
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.warning("dglab ws: non-JSON message: %s", raw_msg[:100])
            return
        mt = msg.get("type")
        if mt == "bind":
            self._handle_bind(msg)
        elif mt == "heartbeat":
            pass
        elif mt == "msg":
            logger.debug("dglab ws: APP msg: %s", msg.get("message", "")[:80])
        elif mt == "break":
            logger.info("dglab ws: partner disconnected  code=%s", msg.get("message"))
            with self._lock:
                self.target_id = None
        elif mt == "error":
            logger.warning("dglab ws: error from backend: code=%s", msg.get("message"))
        else:
            logger.debug("dglab ws: unknown type=%s", mt)

    def _on_error(self, ws, error):
        logger.error("dglab ws: WebSocket error: %s", error)

    def _on_close(self, ws, status_code, close_msg):
        logger.info("dglab ws: closed  code=%s, msg=%s", status_code, close_msg)
        # 注意: _run_forever 重连分支也会清 state（安全重复操作）
        with self._lock:
            self.client_id = None
            self.target_id = None

    # ── 业务消息 ──

    def _handle_bind(self, msg: dict):
        client_id = msg.get("clientId", "")
        target_id = msg.get("targetId", "")
        message = msg.get("message", "")

        # 1. 后端分配 clientId（targetId 为空）
        if not target_id and message == "targetId":
            with self._lock:
                self.client_id = client_id
            lan_ip = _get_lan_ip()
            port = self._ws_url.split(":")[-1].rstrip("/")
            qr_url = (
                "https://www.dungeon-lab.com/app-download.php"
                f"#DGLAB-SOCKET#ws://{lan_ip}:{port}/{client_id}"
            )
            print()
            print(f"  [DGLAB] QR 码已生成，请用 DG-LAB APP 扫描。")
            print(f"  {qr_url}")
            print()
            self._show_qrcode(qr_url)
            return

        # 2. APP 配对成功
        if message == "200":
            with self._lock:
                self.client_id = client_id
                self.target_id = target_id
            print()
            print("  [DGLAB] OK 配对成功! APP 已连接设备。")
            print()
            logger.info("dglab ws: paired  clientId=%s  targetId=%s",
                        client_id, target_id)
            return

        # 3. 配对失败
        if message in ("400", "401"):
            logger.warning("dglab ws: pairing failed  code=%s", message)
            print(f"  [DGLAB] 配对失败 (code={message})")
            return

        logger.debug("dglab ws: unknown bind message=%s", msg)

    # ── QR 码 ──

    def _show_qrcode(self, url: str):
        try:
            import qrcode
            img = qrcode.make(url)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img.save(tmp.name)
            tmp.close()
            os.startfile(tmp.name)
            print("  [DGLAB] QR 码图片已打开，请扫描。")
        except ImportError:
            print("  [DGLAB] 提示: pip install qrcode 可自动弹二维码图片。")
        except Exception as e:
            logger.warning("dglab ws: QR code failed: %s", e)

    # ── 查询 ──

    def is_paired(self) -> bool:
        with self._lock:
            return self.client_id is not None and self.target_id is not None

    # ── 发送 ──

    def send_json(self, payload: dict) -> bool:
        if not self.is_paired():
            logger.warning("dglab ws: not paired, dropping msg type=%s",
                           payload.get("type"))
            return False
        with self._lock:
            payload["clientId"] = self.client_id
            payload["targetId"] = self.target_id
        try:
            self._ws.send(json.dumps(payload))
            return True
        except Exception as e:
            logger.error("dglab ws: send failed: %s", e)
            return False


# ════════════════════════════════════════════════════════════════
# 模块级状态
# ════════════════════════════════════════════════════════════════

_client: Optional[_DglabWSClient] = None
_safety_timers: dict[str, threading.Timer] = {}
_timer_lock = threading.Lock()
```

- [ ] **Step 1c: 验证骨架可导入**

```bash
cd e:\HELLDIVER_DGLAB
python -c "
import sys; sys.path.insert(0, '.')
from src.dglab_sender import _DglabWSClient, _get_lan_ip
c = _DglabWSClient('ws://127.0.0.1:9999')
print('client init OK, paired:', c.is_paired())
print('lan_ip:', _get_lan_ip())
"
```

预期: `client init OK, paired: False` + 打印 LAN IP。

- [ ] **Step 1d: Stage & backup**

```bash
git add src/dglab_sender.py
# 暂不 commit，等 Task 1e 完成模块级 API 后一起 commit
```

---

### Task 1e: 编写模块级 API + helper functions

**Files:**
- Modify: `src/dglab_sender.py`（在骨架之后追加）

```python
# ════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════

def init_sender(config: dict) -> _DglabWSClient:
    """初始化 sender（由 trigger.py 调用）。非阻塞，后台连接。"""
    global _client

    try:
        import websocket  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "sender_mode=dglab 但 websocket-client 未安装。\n"
            "pip install websocket-client 或改 config.ini [trigger] sender_mode = mock"
        )

    sender_cfg = config.get("sender", {})
    ws_host = sender_cfg.get("ws_host", "127.0.0.1")
    ws_port = int(sender_cfg.get("ws_port", 9999))
    ws_url = f"ws://{ws_host}:{ws_port}"

    # 用 config.ini 的强度值覆盖默认值
    for action, cfg_key in [
        ("injury_pulse", "injury_pulse_strength"),
        ("death_pulse", "death_pulse_strength"),
        ("heal_pulse", "heal_pulse_strength"),
        ("low_health", "low_health_strength"),
    ]:
        if cfg_key in sender_cfg:
            ACTION_PROFILES[action]["strength"] = int(sender_cfg[cfg_key])

    _client = _DglabWSClient(ws_url)
    _client.start()
    logger.info("dglab sender: initialized  ws_url=%s", ws_url)
    return _client


def is_paired() -> bool:
    """是否已与 APP 配对成功。（供 test_pairing.py 等外部使用）"""
    return _client is not None and _client.is_paired()


def wait_for_pair(timeout: float = 120) -> bool:
    """阻塞等待 APP 扫码配对。返回 True 成功 / False 超时。"""
    if _client is None:
        return False
    start = time.time()
    while not _client.is_paired():
        if time.time() - start > timeout:
            return False
        time.sleep(0.5)
    return True


def send_action(action: str, payload: Optional[dict] = None) -> dict:
    """发送设备动作。返回 {"dispatched": True/False, "action": str, "mode": "dglab"}。"""
    if _client is None:
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": "sender not initialized"}

    profile = ACTION_PROFILES.get(action)
    if profile is None:
        logger.warning("dglab sender: unknown action=%s", action)
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": f"unknown action: {action}"}

    if not _client.is_paired():
        logger.debug("dglab sender: not paired, dropping action=%s", action)
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": "not paired"}

    channel = profile["channel"]
    channel_num = 1 if channel == "A" else 2

    # 1. 发送强度
    if not _client.send_json({
        "type": 3, "channel": channel_num, "strength": profile["strength"],
        "message": "set channel",
    }):
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": "strength send failed"}

    # 2. 发送波形
    waveform_name = profile["waveform"]
    waveform = WAVEFORM_V3.get(waveform_name, [])
    if not waveform:
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": f"no waveform: {waveform_name}"}

    if not _client.send_json({
        "type": "clientMsg", "channel": channel, "time": profile["duration"],
        "message": f"{channel}:{json.dumps(waveform, separators=(',', ':'))}",
    }):
        logger.error("dglab sender: pulse send failed, zeroing channel %s", channel)
        _stop_channel(channel)
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": "pulse send failed (strength zeroed)"}

    # 3. 启动 safety timer
    _schedule_safety_timer(channel, profile["duration"])

    logger.info("dglab sender: sent %s  ch=%s  str=%d  dur=%ds  hp=%.1f%%",
                action, channel, profile["strength"], profile["duration"],
                (payload or {}).get("hp", 0) * 100)
    return {"dispatched": True, "action": action, "mode": "dglab"}


def safe_stop():
    """安全停止：取消所有 safety timer → A/B 通道归零 → 关连接。"""
    global _client

    with _timer_lock:
        for ch, timer in list(_safety_timers.items()):
            if timer is not None:
                timer.cancel()
                logger.info("dglab sender: cancelled safety timer ch=%s", ch)
        _safety_timers.clear()

    for ch in ("A", "B"):
        _stop_channel(ch)

    if _client is not None:
        _client.stop()
        _client = None

    logger.info("dglab sender: safe_stop done")


# ════════════════════════════════════════════════════════════════
# 内部 helper
# ════════════════════════════════════════════════════════════════

def _send_strength(channel_letter: str, strength: int) -> bool:
    if _client is None:
        return False
    cn = 1 if channel_letter.upper() == "A" else 2
    return _client.send_json({"type": 3, "channel": cn, "strength": strength,
                              "message": "set channel"})


def _clear_channel(channel_letter: str) -> bool:
    if _client is None:
        return False
    cn = 1 if channel_letter.upper() == "A" else 2
    return _client.send_json({"type": 4, "message": f"clear-{cn}"})


def _stop_channel(channel_letter: str):
    if _client is None or not _client.is_paired():
        return
    _clear_channel(channel_letter)
    time.sleep(STOP_CLEAR_TO_ZERO_DELAY)
    _send_strength(channel_letter, 0)
    logger.info("dglab sender: stopped channel %s", channel_letter)


def _stop_channel_and_cleanup(channel_letter: str):
    """Timer 回调: 归零后从 _safety_timers 清理自身（防内存泄漏）。"""
    _stop_channel(channel_letter)
    with _timer_lock:
        _safety_timers.pop(channel_letter, None)


def _schedule_safety_timer(channel_letter: str, duration: float):
    with _timer_lock:
        old = _safety_timers.get(channel_letter)
        if old is not None:
            old.cancel()
        timer = threading.Timer(duration, _stop_channel_and_cleanup,
                                args=(channel_letter,))
        timer.daemon = True
        timer.start()
        _safety_timers[channel_letter] = timer
```

- [ ] **Step 1f: 验证模块级 API 可导入**

```bash
cd e:\HELLDIVER_DGLAB
python -c "
import sys; sys.path.insert(0, '.')
from src.dglab_sender import init_sender, send_action, safe_stop, is_paired, wait_for_pair
print('All public APIs OK')
print('is_paired():', is_paired())
# mock 模式下 send_action 应该安全返回未初始化
result = send_action('heal_pulse')
print('send_action (no init):', result)
"
```

预期: `All public APIs OK` / `is_paired(): False` / `send_action (no init): {"dispatched": False, ...}`

- [ ] **Step 1g: Commit**

```bash
git add src/dglab_sender.py
git commit -m "refactor: replace sync WebSocket with WebSocketApp + daemon thread (SQUAD-validated)

- _DglabWSClient: WebSocketApp + on_message callback dispatch
- Module-level API: init_sender / send_action / safe_stop / is_paired / wait_for_pair
- Waveform templates from SQUAD (death_pulse BEBE frequency)
- Safety timer with self-cleanup to prevent memory leak
- Plain ASCII output (no Unicode box chars for GBK compat)
- json.dumps with compact separators for protocol consistency"
```

---

### Task 2: 修改 trigger.py — 异步初始化路径

**Files:**
- Modify: `src/trigger.py` (dglab 初始化段，~L74-93)

**原因（PRO F1/F2）**：新 `_DglabWSClient` 没有 `connect_and_pair()` 方法也永远不会阻塞配对。旧代码 `sender.connect_and_pair()` 直接 `AttributeError`。

旧代码:
```python
if self._mode == "dglab":
    try:
        import websocket  # noqa: F401
    except ImportError:
        raise RuntimeError(...)
    try:
        from src.dglab_sender import init_sender, send_action as dglab_send
        sender = init_sender(config)
        if not sender.connect_and_pair():
            raise ConnectionError("配对超时或失败")
        self._send = dglab_send
        logger.info("trigger: dglab connected and paired")
    except ConnectionError as e:
        raise ConnectionError(...) from e
```

- [ ] **Step 1: 替换为异步初始化**

```python
        if self._mode == "dglab":
            try:
                import websocket  # noqa: F401
            except ImportError:
                raise RuntimeError(
                    "sender_mode=dglab 但 websocket-client 未安装。\n"
                    "请运行: pip install websocket-client\n"
                    "或改 config.ini [trigger] sender_mode = mock"
                )
            try:
                from src.dglab_sender import init_sender, send_action as dglab_send
                from src.dglab_sender import wait_for_pair
                init_sender(config)
                # 非阻塞: WebSocket 后台连接 + QR 码弹出
                # 等待配对（最多 120s）
                if not wait_for_pair(timeout=120):
                    raise ConnectionError("配对超时（120s）或失败")
                self._send = dglab_send
                logger.info("trigger: dglab connected and paired")
            except ConnectionError as e:
                raise ConnectionError(
                    f"DG-LAB 后端连接失败: {e}\n"
                    "请先启动 SOCKET v2 后端, 或改 config.ini [trigger] sender_mode = mock"
                ) from e
```

- [ ] **Step 2: 验证 trigger.py 语法**

```bash
cd e:\HELLDIVER_DGLAB
python -c "
import sys; sys.path.insert(0, '.')
with open('src/trigger.py', 'r', encoding='utf-8') as f:
    code = f.read()
compile(code, 'src/trigger.py', 'exec')
print('trigger.py syntax OK')
"
```

预期: `trigger.py syntax OK`

- [ ] **Step 3: 验证 mock 模式不受影响**

```bash
cd e:\HELLDIVER_DGLAB
python -c "
import sys; sys.path.insert(0, '.')
from src.config_loader import load_config
from src.trigger import TriggerDispatch
config = load_config()
trigger = TriggerDispatch(config)
print('mock mode OK, _mode:', trigger._mode)
trigger.safe_stop()
"
```

预期: `mock mode OK, _mode: mock` — 无 WS 连接尝试。

- [ ] **Step 4: Commit**

```bash
git add src/trigger.py
git commit -m "fix: replace sync connect_and_pair with async wait_for_pair in trigger.py"
```

---

### Task 3: 单元测试

**Files:**
- Create: `tests/test_dglab_sender.py`

- [ ] **Step 1: 创建测试文件**

```python
"""test_dglab_sender.py — 模块级 API + 内部函数测试。无需真实 DG-LAB 后端。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.dglab_sender import (
    ACTION_PROFILES, WAVEFORM_V3, _get_lan_ip, _DglabWSClient,
    is_paired, send_action,
)


class TestActionProfiles:
    def test_all_actions_have_waveforms(self):
        for action, profile in ACTION_PROFILES.items():
            assert profile["waveform"] in WAVEFORM_V3, (
                f"{action} -> {profile['waveform']} not in WAVEFORM_V3")

    def test_all_channels_are_A(self):
        for action, profile in ACTION_PROFILES.items():
            assert profile["channel"] == "A"

    def test_injury_and_low_health_share_weak_pulse(self):
        assert ACTION_PROFILES["injury_pulse"]["waveform"] == "weak_pulse"
        assert ACTION_PROFILES["low_health"]["waveform"] == "weak_pulse"


class TestGetLanIp:
    def test_returns_string(self):
        ip = _get_lan_ip()
        assert isinstance(ip, str) and len(ip) > 0

    def test_has_dots_or_localhost(self):
        ip = _get_lan_ip()
        parts = ip.split(".")
        assert len(parts) == 4 or ip == "127.0.0.1"


class TestWsClientInit:
    def test_unpaired_after_init(self):
        c = _DglabWSClient("ws://127.0.0.1:9999")
        assert not c.is_paired()
        assert c.client_id is None and c.target_id is None

    def test_send_json_blocked_when_unpaired(self):
        c = _DglabWSClient("ws://127.0.0.1:9999")
        assert c.send_json({"type": "test"}) is False


class TestModuleLevelAPI:
    def test_is_paired_no_init(self):
        """未调用 init_sender 时 is_paired() 返回 False。"""
        assert is_paired() is False

    def test_send_action_no_init(self):
        """未初始化时 send_action 返回 dispatched=False。"""
        result = send_action("heal_pulse")
        assert result["dispatched"] is False
        assert "not initialized" in result.get("error", "")


class TestWaveformIndirection:
    def test_heal_pulse_has_dedicated_waveform(self):
        profile = ACTION_PROFILES["heal_pulse"]
        waveform = WAVEFORM_V3[profile["waveform"]]
        assert len(waveform) > 0
        assert waveform[0].startswith("0A0A0A0A")

    def test_death_pulse_has_dedicated_waveform(self):
        profile = ACTION_PROFILES["death_pulse"]
        waveform = WAVEFORM_V3[profile["waveform"]]
        assert waveform[0].startswith("BEBEBEBE")
```

- [ ] **Step 2: 运行测试**

```bash
cd e:\HELLDIVER_DGLAB
python -m pytest tests/test_dglab_sender.py -v
```

预期: 10 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dglab_sender.py
git commit -m "test: add dglab_sender API + waveform indirection unit tests (10 tests)"
```

---

### Task 4: 更新 tools/test_pairing.py + runner_mock.py 保护

**Files:**
- Modify: `tools/test_pairing.py`
- Modify: `src/runner_mock.py`

- [ ] **Step 1: 重写 test_pairing.py（适配新 API + shock 警告）**

```python
"""test_pairing.py — 测试 DG-LAB SOCKET v2 连接与配对。

使用方法:
    1. 启动后端
    2. python tools/test_pairing.py
    3. 终端弹出 QR 码 → APP 扫码
    4. 看到 "配对成功" 后自动发送测试波形
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.dglab_sender import init_sender, is_paired, wait_for_pair
from src.dglab_sender import send_action, safe_stop

config = load_config(str(Path(__file__).resolve().parent.parent / "config.ini"))

print("初始化 DG-LAB 连接...")
init_sender(config)

print("等待 APP 扫码配对...")
if not wait_for_pair(timeout=120):
    print("配对超时（120s），退出。")
    safe_stop()
    sys.exit(1)

print("\n[OK] 配对成功!\n")

TESTS = [
    ("heal_pulse",   15, 2.5, "最温和"),
    ("injury_pulse", 20, 2.5, "中等"),
    ("death_pulse",  40, 5.5, "最强，注意!"),
]

for action, strength, wait, desc in TESTS:
    print(f"[{desc}] 强度={strength}, 3秒后开始...")
    time.sleep(3)
    result = send_action(action, {"hp": 0.5, "state": "test"})
    print(f"  {action}: {result}")
    time.sleep(wait)

safe_stop()
print("\n测试完成。")
```

- [ ] **Step 2: 修改 runner_mock.py — TriggerDispatch 加 try/except**

旧:
```python
trigger = TriggerDispatch(config)
```

新:
```python
try:
    trigger = TriggerDispatch(config)
except (ConnectionError, RuntimeError) as e:
    print(f"[runner_mock] 设备连接失败: {e}")
    print("[runner_mock] 将继续运行（仅终端输出）。")
    trigger = None
```

- [ ] **Step 3: 验证语法**

```bash
cd e:\HELLDIVER_DGLAB
python -c "
with open('tools/test_pairing.py', 'r', encoding='utf-8') as f:
    compile(f.read(), 'tools/test_pairing.py', 'exec')
with open('src/runner_mock.py', 'r', encoding='utf-8') as f:
    compile(f.read(), 'src/runner_mock.py', 'exec')
print('Both files syntax OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add tools/test_pairing.py src/runner_mock.py
git commit -m "fix: update test_pairing for new API + add runner_mock try/except

- test_pairing.py: use is_paired()/wait_for_pair(), add shock warning delays
- runner_mock.py: add try/except for TriggerDispatch (matches runner_real)"
```

---

### Task 5: 全量回归

- [ ] **Step 1: 跑全量单元测试**

```bash
cd e:\HELLDIVER_DGLAB
python -m pytest tests/ -v -q
```

预期: 所有已有测试 + 新增 10 tests = ~27 tests 全部通过。

- [ ] **Step 2: 跑 mock runner 确认管线正常**

```bash
python src/runner_mock.py
```

预期: mock 模式正常启动，视觉管线正常，状态识别正常。

- [ ] **Step 3: Commit（如无问题则无需额外 commit）**

---

### Task 6: 真机验证

- [ ] **Step 1: 启动 SOCKET v2 后端**

```bash
cd official_v2\socket\v2\backend
npm start
```

- [ ] **Step 2: 启动 runner + trigger**

```bash
cd e:\HELLDIVER_DGLAB
python src/runner_real.py --enable-trigger
```

- [ ] **Step 3: 配对验证**

APP 扫码后，确认：
- 终端显示: `[DGLAB] OK 配对成功! APP 已连接设备。`
- 后端终端显示: `配对成功：xxx <-> yyy`

- [ ] **Step 4: 波形送达验证**

进任务按 P 开始检测。受伤/死亡后确认：
- 后端终端显示: `[clientId] 波形消息已发送：通道A, 次数X, 时长Xs`
- 设备有对应电击反馈

- [ ] **Step 5: 安全停止验证**

Ctrl+C 退出，确认：
- 终端显示 `dglab sender: safe_stop done`
- 设备立即停止电击

---

## 自审查

### Spec 覆盖

| 需求 | 任务 |
|------|------|
| WebSocketApp + daemon thread | Task 1b (`_DglabWSClient`) |
| 模块级 API 不变 | Task 1e (`init_sender`/`send_action`/`safe_stop`/`is_paired`/`wait_for_pair`) |
| 波形名间接映射 | Task 1e (`profile["waveform"]`) + Task 3 (`TestWaveformIndirection`) |
| 保留 HELLDIVER 特有配置 | Task 1b (ACTION_PROFILES / _get_lan_ip / QR os.startfile) |
| SQUAD 验证的波形模板 | Task 1b (WAVEFORM_V3, 标注 death_pulse BEBE 来源) |
| safety timer self-cleanup | Task 1e (`_stop_channel_and_cleanup` + `_safety_timers.pop`) |
| trigger.py 异步初始化 | Task 2 (`init_sender` + `wait_for_pair`) |
| 纯 ASCII 输出 | Task 1b (砍 Unicode 框线) |
| 内联 _handle_app_msg / _handle_break | Task 1b (on_message 内联) |
| 单元测试 | Task 3 (10 tests) |
| test_pairing 适配 | Task 4 |
| runner_mock try/except | Task 4 |

### 占位符扫描

0 TBD/TODO/占位符。所有代码完整可运行。

### 类型一致性

- `init_sender(config)` -> `_DglabWSClient` — trigger.py + test_pairing 使用
- `is_paired()` -> `bool` — 公开函数，test_pairing + 外部使用
- `wait_for_pair(timeout)` -> `bool` — trigger.py 使用
- `send_action(action, payload)` -> `{"dispatched": bool, ...}` — trigger.dispatch 读取 `dispatched`
- `safe_stop()` 无参数 — trigger.py + runner_real.py 调用不变
- `ACTION_PROFILES[action]["waveform"]` -> `WAVEFORM_V3[name]` — Task 3 测试覆盖
- `json.dumps(waveform, separators=(',', ':'))` — 紧凑格式，与 SQUAD 一致
