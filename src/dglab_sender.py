r"""
dglab_sender.py — DG-LAB SOCKET v2 真机发送器。

WebSocketApp + daemon thread + on_message 回调分发架构（SQUAD_DGLAB 真机验证）。
协议: DG-LAB SOCKET v2 (V3 波形格式, Coyote 3.0)
参考: F:\SQUAD_DGLAB\SQUAD_DGLAB\official_v2\socket\v2\

使用步骤:
    1. 启动 DG-LAB SOCKET v2 后端
    2. config.ini [trigger] sender_mode = dglab
    3. config.ini [sender] ws_host = <你的电脑 LAN IP>
    4. python src/runner_real.py --enable-trigger
       （runner_mock 仅离线预演，不连接硬件）
    5. 终端显示的 QR 码用 DG-LAB APP 扫描配对
    6. 战斗中触发事件 → 设备反馈
"""

import atexit
import json
import logging
import os
import socket
import tempfile
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

from src.feedback_mixer import FeedbackMixer, MixedOutput, bounded_number, action_profiles
from src.network_addresses import validate_advertised_ipv4

logger = logging.getLogger(__name__)

# ── V3 波形模板 (SQUAD_DGLAB 真机验证) ──
# 格式: 每段 100ms, 16 字符 hex (前 8 字符频率, 后 8 字符强度 00=0% 64=100%)
# 注: death_pulse 频率 BEBE(190) 来自 SQUAD 模板，替换了原先的 0A(10) 低频
#     因为 SQUAD 真机已确认 BEBE 能安全送达设备，而 0A 从未真机验证过。
from src.waveforms import WAVEFORM_V3

# ── 动作配置 ──
# waveform 字段是间接映射：injury_pulse 和 low_health 共用 weak_pulse
ACTION_PROFILES: dict[str, dict] = action_profiles({})
_DEFAULT_ACTION_PROFILES = {name: profile.copy() for name, profile in ACTION_PROFILES.items()}

STOP_CLEAR_TO_ZERO_DELAY = 0.08
FEEDBACK_TICK_SEC = 0.05
CONNECT_TIMEOUT_SEC = 3.0
SOCKET_TIMEOUT_SEC = 1.0


def _get_lan_ip() -> str:
    """获取本机 LAN IP（socket 连 8.8.8.8 自动检测）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# ════════════════════════════════════════════════════════════════
# _DglabWSClient — WebSocketApp + 后台线程
# ════════════════════════════════════════════════════════════════

class _DglabWSClient:
    """DG-LAB SOCKET v2 WebSocket 长连接客户端（内部类）。"""

    def __init__(self, ws_url: str, *, qr_host=None):
        self._ws_url = ws_url
        self._qr_host = validate_advertised_ipv4(qr_host) if qr_host is not None else None
        self.client_id: Optional[str] = None
        self.target_id: Optional[str] = None
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._should_stop = False
        self._paired_ready = False  # 新配对先清零，之后才能发送动作
        self._stop_event = threading.Event()

    # ── 生命周期 ──

    def start(self):
        """启动后台连接线程（非阻塞）。"""
        if self._thread is not None and self._thread.is_alive():
            logger.info("dglab ws: already running")
            return
        self._should_stop = False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="DGLabWSClient")
        self._thread.start()
        logger.info("dglab ws: started  target=%s", self._ws_url)

    def stop(self):
        """停止连接；直接调用时也先取消动作并尽力清零。"""
        with _timer_lock:
            if self is _client:
                stop_output()
            self._should_stop = True
            self._stop_event.set()
        self._forget_pair()
        if self._ws:
            try:
                self._ws.close(timeout=SOCKET_TIMEOUT_SEC)
            except Exception:
                pass
        logger.info("dglab ws: stopped")

    def _run_forever(self):
        while not self._should_stop:
            transport = None
            ws = None
            try:
                from websocket import WebSocketApp  # noqa: F811
                # run_forever 不提供实例级连接 timeout；传入已连接的 socket，
                # 避免修改 websocket 的全局默认值影响其他组件。
                target = urlsplit(self._ws_url)
                transport = socket.create_connection(
                    (target.hostname, target.port or 80), timeout=CONNECT_TIMEOUT_SEC)
                transport.settimeout(SOCKET_TIMEOUT_SEC)
                if self._should_stop:
                    break
                ws = WebSocketApp(
                    self._ws_url,
                    socket=transport,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                if not self._install_socket(ws):
                    break
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except ImportError:
                logger.error("dglab ws: websocket-client 未安装。pip install websocket-client")
                return
            except Exception as e:
                logger.error("dglab ws: connection error: %s", e)
            finally:
                if transport is not None:
                    transport.close()
            if ws is not None:
                self._forget_pair(ws)
            if self._should_stop:
                break
            logger.info("dglab ws: reconnecting in 5s...")
            self._stop_event.wait(5)

    def _install_socket(self, ws) -> bool:
        """替换连接与遗忘旧配对使用同一锁顺序，不能跨 socket 继承配对。"""
        with _timer_lock:
            with self._lock:
                if self._should_stop:
                    return False
                self._ws = ws
                self.client_id = None
                self.target_id = None
                self._paired_ready = False
            _invalidate_client_actions(self)
        return True

    # ── WebSocket 回调 ──

    def _on_open(self, ws):
        # Dispatcher 先 select 到可读事件才 recv，空闲连接不会因 1s 超时退出；
        # send 背压和收到一半的帧则必须在有限时间内失败，给 stop/tick 让出锁。
        if ws is self._ws and ws.sock is not None:
            ws.sock.settimeout(SOCKET_TIMEOUT_SEC)
        logger.info("dglab ws: connected, waiting for clientId...")

    def _on_message(self, ws, raw_msg):
        if ws is not self._ws:
            return  # 旧连接的迟到回调不能修改新配对
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.warning("dglab ws: non-JSON message: %s", raw_msg[:100])
            return
        if not isinstance(msg, dict):
            logger.warning("dglab ws: invalid non-object message")
            self._reject_server_message(ws)
            return
        mt = msg.get("type")
        code = str(msg.get("message", ""))
        # 本地后端把验证/路由错误放在 type=msg，而不是只有 type=error。
        # socket.send 成功不等于后端接受；迟到的拒绝必须停止整段输出。
        if mt == "error" or (mt == "msg" and len(code) == 3
                             and code.isdigit() and 400 <= int(code) < 600):
            logger.error("dglab ws: backend rejected output, code=%s", code[:80])
            self._reject_server_message(ws)
            return
        if mt == "bind":
            self._handle_bind(msg)
        elif mt == "heartbeat":
            pass
        elif mt == "msg":
            logger.debug("dglab ws: APP msg: %s", code[:80])
        elif mt == "break":
            logger.info("dglab ws: partner disconnected  code=%s", msg.get("message"))
            self._forget_pair(ws)
        else:
            logger.debug("dglab ws: unknown type=%s", mt)

    def _on_error(self, ws, error):
        logger.error("dglab ws: WebSocket error: %s", error)
        if self._forget_pair(ws):
            self._abort_connection(ws)

    def _reject_server_message(self, ws):
        if self._forget_pair(ws, stop_output_first=True):
            self._abort_connection(ws)

    def _on_close(self, ws, status_code, close_msg):
        logger.info("dglab ws: closed  code=%s, msg=%s", status_code, close_msg)
        # 注意: _run_forever 重连分支也会清 state（安全重复操作）
        self._forget_pair(ws)

    def _forget_pair(self, expected_ws=None, *, stop_output_first: bool = False) -> bool:
        """在锁内按 socket 身份清理，迟到错误/close 不得清掉新连接。

        锁顺序始终是模块发送锁 → 连接锁，与 _install_socket/send_action 一致。
        expected_ws=None 仅用于显式停止整个客户端。
        """
        with _timer_lock:
            with self._lock:
                if expected_ws is not None and expected_ws is not self._ws:
                    return False
            # 发送清零需要再次取得连接锁，故不得持有非重入的 self._lock。
            # 模块锁仍保护 socket 不会在身份核验与清理之间被替换。
            if stop_output_first and self is _client:
                stop_output()
            with self._lock:
                self.client_id = None
                self.target_id = None
                self._paired_ready = False
            _invalidate_client_actions(self)
        return True

    def _abort_connection(self, ws):
        """网络失败后不继续声称 paired；唤醒 recv 并有界关闭，促使后端清零。"""
        try:
            core_socket = getattr(ws, "sock", None)
            if core_socket is not None:
                core_socket.abort()
            ws.close(timeout=0.2)
        except Exception as exc:
            logger.warning("dglab ws: failed to close broken connection: %s", exc)

    # ── 业务消息 ──

    def _handle_bind(self, msg: dict):
        client_id = msg.get("clientId", "")
        target_id = msg.get("targetId", "")
        message = msg.get("message", "")

        # 1. 后端分配 clientId（targetId 为空）
        if not target_id and message == "targetId":
            with self._lock:
                self.client_id = client_id
            lan_ip = self._qr_host if self._qr_host is not None else _get_lan_ip()
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
        if message == "200" and client_id and target_id:
            with self._lock:
                self.client_id = client_id
                self.target_id = target_id
                self._paired_ready = False
            if not _prepare_pair(self):
                logger.error("dglab ws: initial zero failed; pairing remains inactive")
                return
            print()
            print("  [DGLAB] OK 配对成功! 已连接 APP（不代表已连接实体设备）。")
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
            return (not self._should_stop and self._paired_ready
                    and self.client_id is not None and self.target_id is not None)

    # ── 发送 ──

    def send_json(self, payload: dict, *, allow_unready: bool = False) -> bool:
        ws = None
        try:
            # 配对快照和 socket.send 是同一临界区；不能把旧动作发给新设备。
            with self._lock:
                if (self._should_stop or self._ws is None
                        or self.client_id is None or self.target_id is None
                        or (not allow_unready and not self._paired_ready)):
                    logger.warning("dglab ws: not paired, dropping msg type=%s",
                                   payload.get("type"))
                    return False
                message = dict(payload, clientId=self.client_id, targetId=self.target_id)
                ws = self._ws
                ws.send(json.dumps(message))
            return True
        except Exception as e:
            logger.error("dglab ws: send failed: %s", e)
            if ws is not None and self._forget_pair(ws):
                self._abort_connection(ws)
            return False


# ════════════════════════════════════════════════════════════════
# 模块级状态
# ════════════════════════════════════════════════════════════════

_client: Optional[_DglabWSClient] = None
_ui_strengths = {"A": (None, None), "B": (None, None)}
_safety_timers: dict[str, threading.Timer] = {}
# 发送、停止及 timer 回调必须作为完整事务串行；cancel() 不能撤销已开始的回调。
_timer_lock = threading.RLock()
_operation_generation = 0
_channel_generation = {"A": 0, "B": 0}
_mixer = FeedbackMixer({})
_rendered_output = MixedOutput()
_next_waveform_at = 0.0
_rendered_output_b = MixedOutput()
_next_waveform_at_b = 0.0


# ════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════

def init_sender(config: dict) -> _DglabWSClient:
    """初始化 sender（由 trigger.py 调用）。非阻塞，后台连接。"""
    global _client, _mixer

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
    qr_host = sender_cfg.get("qr_host")
    if qr_host is not None:
        qr_host = validate_advertised_ipv4(qr_host)
    mixer = FeedbackMixer(config)
    profiles = action_profiles(config)

    # 所有安全配置校验通过后才替换会话，不能留下旧连接或旧 timer。
    safe_stop()

    with _timer_lock:
        ACTION_PROFILES.clear()
        ACTION_PROFILES.update(profiles)
        _mixer = mixer
        _client = _DglabWSClient(ws_url, qr_host=qr_host)
        _client.start()
        client = _client
    logger.info("dglab sender: initialized  ws_url=%s", ws_url)
    return client


def is_paired() -> bool:
    """是否已与 APP 配对成功。（供 test_pairing.py 等外部使用）"""
    client = _client
    return client is not None and client.is_paired()


def wait_for_pair(timeout: float = 120) -> bool:
    """阻塞等待 APP 扫码配对。返回 True 成功 / False 超时。"""
    client = _client
    if client is None:
        return False
    start = time.monotonic()
    while client is _client and not client._should_stop:
        if client.is_paired():
            return True
        if time.monotonic() - start > timeout:
            return False
        time.sleep(0.5)
    return False


def send_action(action: str, payload: Optional[dict] = None) -> dict:
    """加入短脉冲层；injury 的 damage_pct 按累计伤害比例缩放配置上限。"""
    # 在等待发送锁前取快照：stop_output 之前排队的调用之后也不能复活。
    operation = _operation_generation
    client = _client
    if client is None:
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": "sender not initialized"}

    profile = ACTION_PROFILES.get(action)
    if profile is None:
        logger.warning("dglab sender: unknown action=%s", action)
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": f"unknown action: {action}"}

    waveform_name = profile["waveform"]
    waveform = WAVEFORM_V3.get(waveform_name, [])
    # 必须在升强度前确认存在波形，避免配置错误后只升强度却没有 safety timer。
    if not waveform:
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": f"no waveform: {waveform_name}"}
    if profile["channel"] not in _mixer.channels:
        return {"dispatched": False, "action": action, "mode": "dglab",
                "error": "action channel does not match this session"}

    with _timer_lock:
        if client is not _client or operation != _operation_generation:
            return {"dispatched": False, "action": action, "mode": "dglab",
                    "error": "action cancelled by stop or reconnect"}
        if not client.is_paired():
            logger.debug("dglab sender: not paired, dropping action=%s", action)
            return {"dispatched": False, "action": action, "mode": "dglab",
                    "error": "not paired"}

        coordinator = _mixer.channels[0]
        _channel_generation[coordinator] += 1
        old = _safety_timers.pop(coordinator, None)
        if old is not None:
            old.cancel()
        try:
            now = time.monotonic()
            if action == "low_health":
                # 兼容旧调用方；新 Trigger 每有效帧用 update_continuous 续租。
                _mixer.update_continuous(low_health=True, healing=_mixer.healing,
                                         hp=(payload or {}).get("hp", 0.5), now=now)
            else:
                _mixer.add_pulse(action, strength=profile["strength"],
                                 strength_b=profile["strength_b"],
                                 duration=profile["duration"], waveform=waveform_name,
                                 damage_pct=(payload or {}).get("damage_pct", 1.0), now=now)
            if not _render_feedback_locked(client, now,
                    restart_waveform=action == _mixer.dominant_action(now)):
                raise RuntimeError("feedback send failed")
            _ensure_feedback_timer_locked(client)
        except Exception as exc:
            logger.error("dglab sender: action %s failed: %s", action, exc)
            _invalidate_actions_locked()
            for ch in ("A", "B"):
                _stop_channel(ch, client)
            return {"dispatched": False, "action": action, "mode": "dglab",
                    "error": str(exc)}

    logger.info("dglab sender: sent %s  channels=%s  max_str=A:%s/B:%s  dur=%ss  hp=%.1f%%",
                action, "+".join(_mixer.channels), profile["strength"], profile["strength_b"], profile["duration"],
                (payload or {}).get("hp", 0) * 100)
    return {"dispatched": True, "action": action, "mode": "dglab"}


def update_continuous(*, low_health: bool, healing: bool, hp: float) -> dict:
    """有效帧续租；到期后的独立 tick 发起清零，网络操作另受有限 I/O 超时约束。"""
    operation = _operation_generation
    client = _client
    with _timer_lock:
        if (client is None or client is not _client or operation != _operation_generation
                or not client.is_paired()):
            return {"dispatched": False, "mode": "dglab", "error": "not paired or cancelled"}
        try:
            now = time.monotonic()
            _mixer.update_continuous(low_health=low_health, healing=healing, hp=hp, now=now)
            if not _render_feedback_locked(client, now):
                raise RuntimeError("feedback send failed")
            _ensure_feedback_timer_locked(client)
        except Exception as exc:
            logger.error("dglab sender: continuous feedback failed: %s", exc)
            _invalidate_actions_locked()
            for ch in ("A", "B"):
                _stop_channel(ch, client)
            return {"dispatched": False, "mode": "dglab", "error": str(exc)}
    return {"dispatched": True, "mode": "dglab"}


def stop_output():
    """暂停输出：作废排队动作和旧 timer，清 A/B 队列并归零，保留配对。"""
    with _timer_lock:
        _invalidate_actions_locked()
        client = _client
        for ch in ("A", "B"):
            _stop_channel(ch, client)
    logger.info("dglab sender: output stopped (connection retained)")


def safe_stop():
    """安全停止：取消所有 safety timer → A/B 通道归零 → 关连接。"""
    global _client

    with _timer_lock:
        _invalidate_actions_locked()
        client = _client
        for ch in ("A", "B"):
            _stop_channel(ch, client)
        _client = None
    # 不持有发送锁等待 close，避免 close 回调清理时相互等待。
    if client is not None:
        client.stop()

    logger.info("dglab sender: safe_stop done")


# ════════════════════════════════════════════════════════════════
# 内部 helper
# ════════════════════════════════════════════════════════════════

def _invalidate_actions_locked():
    """调用方持有 _timer_lock；generation 防止 cancel 后迟到的回调误伤新动作。"""
    global _operation_generation, _rendered_output, _next_waveform_at
    global _rendered_output_b, _next_waveform_at_b
    _operation_generation += 1
    _mixer.reset()
    _rendered_output = MixedOutput()
    _next_waveform_at = 0.0
    _rendered_output_b = MixedOutput()
    _next_waveform_at_b = 0.0
    for ch in _channel_generation:
        _channel_generation[ch] += 1
    for ch, timer in list(_safety_timers.items()):
        timer.cancel()
        logger.info("dglab sender: cancelled safety timer ch=%s", ch)
    _safety_timers.clear()


def _invalidate_client_actions(client):
    with _timer_lock:
        if client is _client:
            _invalidate_actions_locked()


def _prepare_pair(client) -> bool:
    """新配对/重配对不继承旧输出：先清两路，再开放 is_paired。"""
    with _timer_lock:
        if client is not _client:
            return False
        _invalidate_actions_locked()
        with client._lock:
            pair = (client.client_id, client.target_id)
        ok_a = _stop_channel("A", client, allow_unready=True)
        ok_b = _stop_channel("B", client, allow_unready=True)
        with client._lock:
            if (client.client_id, client.target_id) != pair or client._should_stop:
                return False
            client._paired_ready = ok_a and ok_b
            return client._paired_ready


def _send_strength(channel_letter: str, strength: int, client=None,
                   *, allow_unready: bool = False) -> bool:
    if client is None:
        return False
    cn = 1 if channel_letter.upper() == "A" else 2
    ok = client.send_json({"type": 3, "channel": cn, "strength": strength,
                          "message": "set channel"}, allow_unready=allow_unready)
    _ui_strengths[channel_letter.upper()] = (client, strength if ok else None)
    return ok


def ui_output_snapshot():
    """Last successful software strength send, not APP/device readback. Never wait for I/O."""
    if not _timer_lock.acquire(blocking=False):
        client = _client
        return {"connected": bool(client and client._paired_ready and not client._should_stop),
                "strengths": {"A": None, "B": None}}
    try:
        client = _client
        # Avoid the socket lock: a status label must never wait behind a send.
        connected = bool(client and client._paired_ready and not client._should_stop)
        return {"connected": connected,
                "strengths": {ch: value if owner is client and connected else None
                              for ch, (owner, value) in _ui_strengths.items()}}
    finally:
        _timer_lock.release()


def _clear_channel(channel_letter: str, client=None,
                   *, allow_unready: bool = False) -> bool:
    if client is None:
        return False
    cn = 1 if channel_letter.upper() == "A" else 2
    return client.send_json({"type": 4, "message": f"clear-{cn}"},
                            allow_unready=allow_unready)


def _stop_channel(channel_letter: str, client=None, *, allow_unready: bool = False) -> bool:
    if client is None or (not allow_unready and not client.is_paired()):
        return False
    cleared = _clear_channel(channel_letter, client, allow_unready=allow_unready)
    time.sleep(STOP_CLEAR_TO_ZERO_DELAY)
    zeroed = _send_strength(channel_letter, 0, client, allow_unready=allow_unready)
    if cleared and zeroed:
        logger.info("dglab sender: stopped channel %s", channel_letter)
    else:
        logger.error("dglab sender: channel %s stop could not be confirmed sent", channel_letter)
    return cleared and zeroed


def _stop_channel_and_cleanup(channel_letter: str, generation: int, client):
    """独立租约/反馈 tick；仅当前通道 token 能操作输出或登记下一个 tick。"""
    with _timer_lock:
        if client is not _client or generation != _channel_generation[channel_letter]:
            return
        _channel_generation[channel_letter] += 1
        _safety_timers.pop(channel_letter, None)
        try:
            if not client.is_paired() or not _render_feedback_locked(client, time.monotonic()):
                raise RuntimeError("feedback connection unavailable")
            _ensure_feedback_timer_locked(client)
        except Exception:
            logger.exception("dglab sender: feedback tick failed; requesting zero")
            _invalidate_actions_locked()
            for ch in ("A", "B"):
                _stop_channel(ch, client)


def _render_feedback_locked(client, now: float, *, restart_waveform: bool = False) -> bool:
    """两路独立投影同一事件节奏；一条归零不会抹去另一条的合成层。"""
    global _rendered_output, _next_waveform_at
    global _rendered_output_b, _next_waveform_at_b
    outputs = _mixer.outputs(now)
    previous = {"A": _rendered_output, "B": _rendered_output_b}
    deadlines = {"A": _next_waveform_at, "B": _next_waveform_at_b}
    for ch, output in outputs.items():
        old = previous[ch]
        if output.strength == 0:
            if old.strength > 0 and not _stop_channel(ch, client):
                return False
            deadlines[ch] = 0.0
            continue
        waveform = WAVEFORM_V3.get(output.waveform)
        if not waveform:
            return False
        # 相同强度/波形的两次受伤仍应重启模板。
        changed = output.waveform != old.waveform or restart_waveform
        if changed and old.strength > 0:
            if not _clear_channel(ch, client):
                return False
            time.sleep(STOP_CLEAR_TO_ZERO_DELAY)
        if output.strength != old.strength and not _send_strength(ch, output.strength, client):
            return False
        if changed or now >= deadlines[ch]:
            # 原生 pulse 单份发送，不启用后端循环，完整模板按其实际长度续播。
            message = f"pulse-{ch}:{json.dumps(waveform, separators=(',', ':'))}"
            if not client.send_json({"type": 4, "message": message}):
                return False
            deadlines[ch] = time.monotonic() + len(waveform) * 0.1
    _rendered_output, _rendered_output_b = outputs["A"], outputs["B"]
    _next_waveform_at, _next_waveform_at_b = deadlines["A"], deadlines["B"]
    if not any(value.strength for value in outputs.values()):
        _invalidate_actions_locked()
    return True


def _ensure_feedback_timer_locked(client):
    # 一个协调 tick 续租/停止两路，不让两个 timer 相互清理或重复续播。
    coordinator = _mixer.channels[0]
    if (_rendered_output.strength > 0 or _rendered_output_b.strength > 0) and coordinator not in _safety_timers:
        _schedule_safety_timer(coordinator, FEEDBACK_TICK_SEC, _channel_generation[coordinator], client)


def _schedule_safety_timer(channel_letter: str, duration: float, generation: int, client):
    with _timer_lock:
        timer = threading.Timer(duration, _stop_channel_and_cleanup,
                                args=(channel_letter, generation, client))
        timer.daemon = True
        _safety_timers[channel_letter] = timer
        try:
            timer.start()
        except Exception:
            _safety_timers.pop(channel_letter, None)
            raise


atexit.register(safe_stop)
