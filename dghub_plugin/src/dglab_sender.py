"""
dglab_sender.py — DG-LAB SOCKET v2 真机发送器。

WebSocketApp + daemon thread + on_message 回调分发架构（SQUAD_DGLAB 真机验证）。
协议: DG-LAB SOCKET v2 (V3 波形格式, Coyote 3.0)
参考: F:\SQUAD_DGLAB\SQUAD_DGLAB\official_v2\socket\v2\

使用步骤:
    1. 启动 DG-LAB SOCKET v2 后端
    2. config.ini [trigger] sender_mode = dglab
    3. config.ini [sender] ws_host = <你的电脑 LAN IP>
    4. python src/runner_mock.py (或实时 runner)
    5. 终端显示的 QR 码用 DG-LAB APP 扫描配对
    6. 战斗中触发事件 → 设备反馈
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
            print("  [DGLAB] QR 码已生成，请用 DG-LAB APP 扫描。")
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
