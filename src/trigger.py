"""Trigger dispatch: 接收 DetectionResult，HP% 直判路由到 sender。

设计原则:
- 不用 event 中间层——HP% 直接判定
- 无效/unknown 帧先停止输出并切断历史，恢复首帧只建立基线
- 死亡只在连续有效 HP 从阈值上方跨到阈值内时触发，每次生命一次
- 受伤按固定 0.2s 窗口合并，事件使用 raw HP，避免 EMA 拆分一次伤害
- 治疗看有效帧间的 HP 回升，确认脉冲与限时舒缓并行
- 有效 raw HP < 75% 续租残血持续层，与受伤脉冲叠加；EMA 仅供显示
- 窗口、差值和冷却均可配置，计时使用 monotonic
- sender 通过 config.ini [trigger] sender_mode 切换
"""
import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Callable
from src.feedback_mixer import MockFeedbackSender, validate_death_mode

logger = logging.getLogger(__name__)

# JSONL 日志路径
LOG_PATH = Path(__file__).resolve().parent.parent / "debug" / "trigger_log.jsonl"

# 已确认的原始 SPEC 默认值；可通过 config.ini [trigger] 覆盖。
INJURY_WINDOW_SEC = 0.2     # 受伤合并窗口
INJURY_DELTA_THRESH = 0.0   # 任何有效下降；需要时可配置噪声阈值
LOW_HEALTH_HP_THRESH = 0.75  # hp < 75% 持续残血反馈


class _AsyncJsonlWriter:
    """复用插件版的有界异步日志模式，文件 I/O 不占用检测/安全停止线程。"""

    _QUEUE_SIZE = 600
    _FLUSH_EVERY = 120

    def __init__(self, path: Path):
        self._path = path
        self._queue: Queue[str] = Queue(maxsize=self._QUEUE_SIZE)
        self._stopped = Event()
        self._drop_warning_logged = False
        self._thread = Thread(target=self._run, daemon=True, name="TriggerJsonlWriter")
        self._thread.start()

    def append(self, entry: dict):
        if self._stopped.is_set():
            return
        try:
            self._queue.put_nowait(json.dumps(entry, ensure_ascii=False, allow_nan=False))
        except Full:
            if not self._drop_warning_logged:
                self._drop_warning_logged = True
                logger.warning("trigger JSONL queue full; dropping diagnostic frames")
        except (TypeError, ValueError):
            logger.exception("trigger JSONL serialization failed")

    def close(self):
        """最多等待 250ms；阻塞的文件系统不能阻塞设备归零。"""
        self._stopped.set()
        self._thread.join(timeout=0.25)
        if self._thread.is_alive():
            logger.warning("trigger JSONL writer did not stop within 250ms")

    def _run(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8", buffering=64 * 1024) as stream:
                pending = 0
                while not self._stopped.is_set() or not self._queue.empty():
                    try:
                        line = self._queue.get(timeout=0.1)
                    except Empty:
                        if pending:
                            stream.flush()
                            pending = 0
                        continue
                    stream.write(line + "\n")
                    pending += 1
                    if pending >= self._FLUSH_EVERY:
                        stream.flush()
                        pending = 0
                if pending:
                    stream.flush()
        except Exception:
            logger.exception("trigger JSONL writer failed")
            self._stopped.set()


def _mock_send(action: str, payload: dict | None = None) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [MOCK SEND] action={action}  payload={payload}")
    return {"dispatched": True, "action": action, "mode": "mock"}


class TriggerDispatch:
    """接收 vision 结果，HP% 直判路由到 sender adapter。"""

    def __init__(self, config: dict, *, clock: Callable[[], float] | None = None,
                 log_writer=None):
        trigger_cfg = config.get("trigger", {})
        self._mode = trigger_cfg.get("sender_mode", "mock")
        self._frame_idx = 0
        self._clock = clock if clock is not None else time.monotonic
        self._closed = False
        self._stop_output = lambda: None
        self._close_sender = lambda: None
        self._update_continuous = lambda **kwargs: {"dispatched": True, "mode": "mock"}

        # ── 冷却时间（从 config.ini [trigger] 读取，有默认值兜底）──
        self._cooldowns: dict[str, float] = {
            "injury_pulse": float(trigger_cfg.get("injury_cooldown_sec", 0.0)),
            "death_pulse": float(trigger_cfg.get("death_cooldown_sec", 0.0)),
            "heal_pulse": float(trigger_cfg.get("healing_cooldown_sec", 0.0)),
            "low_health": float(trigger_cfg.get("low_health_cooldown_sec", 3.0)),
        }
        # 死亡阈值（优先 [trigger] 自定义，fallback 到 [death] health_near_zero）
        death_cfg = config.get("death", {})
        self._death_hp_thresh = float(
            trigger_cfg.get("death_hp_threshold", death_cfg.get("health_near_zero", 0.0))
        )
        self._injury_window = float(trigger_cfg.get(
            "injury_window_sec",
            config.get("state_machine", {}).get("merge_window_sec", INJURY_WINDOW_SEC),
        ))
        self._injury_delta_thresh = float(trigger_cfg.get(
            "injury_delta_threshold", INJURY_DELTA_THRESH))
        self._healing_delta_thresh = float(trigger_cfg.get("healing_delta_threshold", 0.0))
        self._healing_sustain = float(trigger_cfg.get("healing_sustain_sec", 2.0))
        self._low_health_thresh = float(trigger_cfg.get(
            "low_health_hp_threshold", LOW_HEALTH_HP_THRESH))
        self._max_frame_gap = float(trigger_cfg.get("max_frame_gap_sec", 1.0))
        self.death_feedback_mode = validate_death_mode(trigger_cfg.get("death_feedback_mode", "pulse"))
        # Logical session memory survives output resets; only F8/new session
        # clears a failed challenge. It never bypasses the fresh-frame lease.
        self.death_marked = False
        self._death_layer_active = False
        timing = [*self._cooldowns.values(), self._injury_window, self._max_frame_gap,
                  self._healing_sustain]
        thresholds = [self._death_hp_thresh, self._injury_delta_thresh,
                      self._healing_delta_thresh, self._low_health_thresh]
        if any(not math.isfinite(value) or value < 0 for value in timing):
            raise ValueError("trigger 的窗口、冷却和帧间隔必须是有限的非负数")
        if self._max_frame_gap == 0:
            raise ValueError("trigger max_frame_gap_sec 必须大于 0")
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in thresholds):
            raise ValueError("trigger 的 HP 阈值必须在 [0, 1] 范围内")

        # ── HP% 直判状态 ──
        self._last_hp = -1.0
        self._last_frame_time: float | None = None
        self._death_latched = False
        self._injury_started: float | None = None
        self._injury_damage = 0.0
        self._healing_until = -math.inf
        self._healing_confirmation_armed = True
        self._continuous_state = (False, False)
        self._last_fired: dict[str, float] = {}
        if self._mode not in {"mock", "dglab", "dghub"}:
            logger.warning("trigger: unknown sender_mode=%s, falling back to mock", self._mode)
            self._mode = "mock"
        # 在启动日志线程前校验所有 Mock 强度/时长，错误配置不能泄漏线程。
        self._mock_sender = MockFeedbackSender(config, self._clock) if self._mode == "mock" else None
        # 离线回放可注入同步/内存记录器，不能因快于实时处理而丢掉验收证据。
        # 默认实时路径仍用有界后台日志，避免文件 I/O 阻塞安全停止。
        self._log_writer = log_writer if log_writer is not None else _AsyncJsonlWriter(LOG_PATH)

        # 日志不可阻止安全路径；设备停止永远先于 session_end 写入。
        self._write_log({"type": "session_start", "ts": datetime.now().isoformat(),
                         "mode": self._mode})

        if self._mode == "mock":
            self._send = self._mock_sender.send_action
            self._update_continuous = self._mock_sender.update_continuous
            self._stop_output = self._mock_sender.stop_output
            self._close_sender = self._mock_sender.stop_output
            logger.info("trigger ready  mode=mock (HP%-direct)")
            return

        if self._mode == "dghub":
            # DGHub sender — _send 由 main.py 在运行时注入（需要 event loop 上下文）
            # 这里设一个占位函数，避免 dispatch() 中 _send 未定义
            self._send = lambda action, payload=None: {
                "dispatched": False, "error": "dghub sender not wired"}
            self._update_continuous = lambda **kwargs: {
                "dispatched": False, "error": "dghub continuous sender not wired"}
            logger.info("trigger ready  mode=dghub (waiting for main.py to wire _send)")
            return

        if self._mode == "dglab":
            try:
                try:
                    import websocket  # noqa: F401
                except ImportError as error:
                    raise RuntimeError(
                        "sender_mode=dglab 但 websocket-client 未安装。\n"
                        "请运行: pip install websocket-client\n"
                        "或改 config.ini [trigger] sender_mode = mock"
                    ) from error
                from src.dglab_sender import init_sender, send_action as dglab_send
                from src.dglab_sender import (
                    wait_for_pair, stop_output, safe_stop, update_continuous,
                )
                self._stop_output = stop_output
                self._close_sender = safe_stop
                self._update_continuous = update_continuous
                init_sender(config)
                # 后台连接 + QR 码弹出；失败或 Ctrl+C 都回收后台连接。
                if not wait_for_pair(timeout=120):
                    raise ConnectionError("配对超时（120s）或失败")
                self._send = dglab_send
                logger.info("trigger: dglab connected and paired")
            except BaseException as error:
                self._closed = True
                try:
                    self._close_sender()
                except Exception:
                    logger.exception("trigger: sender initialization cleanup failed")
                self._log_writer.close()
                if isinstance(error, ConnectionError):
                    raise ConnectionError(
                        f"DG-LAB 后端连接失败: {error}\n"
                        "请先启动 SOCKET v2 后端, 或改 config.ini [trigger] sender_mode = mock"
                    ) from error
                raise
            return


    def dispatch(self, result) -> dict:
        """接收经过视觉门控的 DetectionResult；拒识永远不发送新动作。"""
        if self._closed:
            return {"dispatched": False, "action": "none", "reason": "stopped"}
        self._frame_idx += 1
        state = getattr(result, "state", "unknown")
        try:
            hp = float(result.health_raw)
            stable_hp = float(result.health_stable)
            valid_hp = all(math.isfinite(value) and 0 <= value <= 1
                           for value in (hp, stable_hp))
        except (AttributeError, TypeError, ValueError):
            hp = stable_hp = 0.0
            valid_hp = False
        if (not getattr(result, "valid", False) or not valid_hp
                or state not in {"full_health", "injured", "healing", "death"}):
            # 只在有效→无效转换时归零，避免 unknown 每帧刷设备命令。
            if self._last_frame_time is not None:
                self.reset("invalid_frame")
            if not valid_hp:
                hp = stable_hp = 0.0
            return self._record(hp, stable_hp, str(state), reason="invalid_frame")

        now = self._clock()
        if self._last_frame_time is not None:
            gap = now - self._last_frame_time
            if gap < 0 or gap > self._max_frame_gap:
                self.reset("frame_gap")

        if self._last_hp < 0:
            self._last_hp = hp
            self._last_frame_time = now
            self._death_latched = hp <= self._death_hp_thresh
            return self._record(hp, stable_hp, state, reason="baseline")

        previous_hp = self._last_hp
        self._last_hp = hp
        self._last_frame_time = now
        candidates: list[tuple[str, float]] = []
        low_health = False
        healing = False
        reason = "no_change"

        # state='death' 本身不能证明发生了新的死亡；必须观察连续有效的跨阈值。
        if hp <= self._death_hp_thresh:
            if previous_hp > self._death_hp_thresh and not self._death_latched:
                candidates.append(("death_pulse", 0.0))
                if self.death_feedback_mode != "pulse":
                    self.death_marked = True
                    self._stop_output()  # new death clears old injury/healing layers once
            self._death_latched = True
            self._clear_injury()
            self._healing_until = -math.inf
            self._healing_confirmation_armed = True
            reason = "death_latched"
        else:
            if self.death_feedback_mode == "until_revive":
                self.death_marked = False
            self._death_latched = False  # 已重新观察到有效的正血量生命。
            if previous_hp - hp > self._injury_delta_thresh:
                # 受伤后再次回血是新的治疗确认，但不打断当前舒缓持续层。
                self._healing_confirmation_armed = True
            if hp - previous_hp > self._healing_delta_thresh:
                if self._healing_confirmation_armed or now >= self._healing_until:
                    candidates.append(("heal_pulse", 0.0))
                self._healing_confirmation_armed = False
                self._healing_until = now + self._healing_sustain
            healing = now < self._healing_until
            low_health = hp < self._low_health_thresh

            # 固定窗口从首个下降帧开始；先结算到期窗口，再收集新的下降。
            if (self._injury_started is not None
                    and now - self._injury_started + 1e-9 >= self._injury_window):
                damage = self._injury_damage
                self._clear_injury()
                if damage > self._injury_delta_thresh:
                    candidates.append(("injury_pulse", damage))
            if hp < previous_hp:
                if self._injury_started is None:
                    self._injury_started = now
                # 原 SPEC 按窗口内累计下降量映射强度；治疗不抹去已确认伤害。
                self._injury_damage = min(1.0, self._injury_damage + previous_hp - hp)
            if self._injury_started is not None:
                reason = "injury_pending"
                if self._injury_window == 0:
                    damage = self._injury_damage
                    self._clear_injury()
                    if damage > self._injury_delta_thresh:
                        candidates.append(("injury_pulse", damage))

        # 连续层每有效帧续租；sender 超时自动停层，避免抓屏阻塞时无限输出。
        # 更新为空层并不取消正在播放的死亡脉冲，stop_output 才清全部层。
        try:
            values = dict(low_health=low_health, healing=healing, hp=hp)
            if self.death_feedback_mode != "pulse":
                values["death_active"] = self.death_marked
            continuous = self._update_continuous(**values)
            continuous_ok = (isinstance(continuous, dict)
                             and continuous.get("dispatched") is True)
        except Exception:
            logger.exception("trigger: continuous update failed")
            continuous_ok = False
        if not continuous_ok:
            self.reset("continuous_rejected")
            return self._record(hp, stable_hp, state, reason="continuous_rejected")
        self._death_layer_active = bool(values.get("death_active", False))
        if self._continuous_state != (low_health, healing):
            logger.info("trigger: continuous  low_health=%s  healing=%s",
                        low_health, healing)
        self._continuous_state = (low_health, healing)

        # ── 冷却检查 + 发送 ──
        actions = []
        for action, damage in candidates:
            last = self._last_fired.get(action, -math.inf)
            if now - last >= self._cooldowns.get(action, 0.5):
                payload = {"hp": round(stable_hp, 3), "raw_hp": round(hp, 3),
                           "state": state}
                if action == "injury_pulse":
                    payload["damage_pct"] = round(damage, 6)
                dispatched = False
                try:
                    sent = (continuous if action == "death_pulse" and self.death_feedback_mode != "pulse"
                            else self._send(action, payload))
                    dispatched = isinstance(sent, dict) and sent.get("dispatched") is True
                    reason = "sent" if dispatched else "sender_rejected"
                except Exception:
                    logger.exception("trigger: sender failed for %s", action)
                    self.reset("sender_error")
                    reason = "sender_error"
                    break
                if dispatched:
                    self._last_fired[action] = now
                    actions.append(action)
                    logger.info("trigger: %s  hp=%.1f%%  模式=%s",
                                action, hp * 100, self._mode)
            else:
                reason = "cooldown"
        action = actions[0] if actions else candidates[0][0] if candidates else None
        damage = sum(amount for name, amount in candidates if name == "injury_pulse")
        return self._record(hp, stable_hp, state, action, bool(actions), reason, damage, actions)

    def _record(self, hp, stable_hp, state, action=None, dispatched=False,
                reason="no_change", damage=0.0, actions=None) -> dict:
        actions = [] if actions is None else actions
        self._write_log({
            "frame": self._frame_idx,
            "ts": datetime.now().isoformat(),
            "hp": round(stable_hp, 3),
            "raw_hp": round(hp, 3),
            "state": state,
            "action": action if dispatched else "none",
            "dispatched": dispatched,
            "actions": actions,
            "reason": reason,
            "damage_pct": round(damage, 6),
            "low_health": self._continuous_state[0],
            "healing_sustain": self._continuous_state[1],
            **self.death_status(),
        })
        return {"dispatched": dispatched, "action": action or "none", "actions": actions,
                "reason": reason, "low_health": self._continuous_state[0],
                "healing_sustain": self._continuous_state[1]}

    @property
    def challenge_failed(self):
        return self.death_feedback_mode == "until_reset" and self.death_marked

    def death_status(self):
        return {"death_feedback_mode": self.death_feedback_mode, "death_marked": self.death_marked,
                "death_layer_active": bool(self._death_layer_active and self._last_frame_time is not None
                    and 0 <= self._clock() - self._last_frame_time < self._max_frame_gap)}

    def reset_challenge(self):
        """Explicit F8 reset; caller also pauses and invalidates frame tickets."""
        self.death_marked = False
        self.reset("challenge_reset")

    def mock_output(self) -> dict:
        """只读 Mock 合成快照；不导入设备发送器、不生成任何协议消息。"""
        if self._mock_sender is None:
            raise RuntimeError("Output snapshots are available only in mock mode")
        outputs = self._mock_sender.mixer.outputs(self._clock())
        channel = self._mock_sender.mixer.channels[0]
        output = outputs[channel]
        return {"strength": output.strength, "waveform": output.waveform,
                "mode": "mock", "channel": channel,
                "channels": {ch: vars(value) for ch, value in outputs.items()}}

    def _clear_injury(self):
        self._injury_started = None
        self._injury_damage = 0.0

    def reset(self, reason: str = "reset"):
        """暂停/恢复/回放换组时停止输出并清基线；保留冷却，连接仍可复用。"""
        self._last_hp = -1.0
        self._last_frame_time = None
        self._death_latched = False
        self._clear_injury()
        self._healing_until = -math.inf
        self._healing_confirmation_armed = True
        self._continuous_state = (False, False)
        self._death_layer_active = False
        if not self._closed:
            try:
                self._stop_output()
            except Exception:
                logger.exception("trigger: stop_output failed during %s", reason)
        logger.info("trigger: reset  reason=%s", reason)

    def safe_stop(self):
        """结束会话（幂等）；先归零关闭 sender，再写日志，不能再次 dispatch。"""
        if self._closed:
            return
        self._closed = True
        self.death_marked = False
        self.reset("session_end")
        try:
            self._close_sender()
        except Exception:
            logger.exception("trigger: safe_stop failed")
        self._write_log({"type": "session_end", "ts": datetime.now().isoformat(),
                         "frames": self._frame_idx})
        self._log_writer.close()
        logger.info("trigger: safe_stop  mode=%s", self._mode)

    def _write_log(self, entry: dict):
        self._log_writer.append(entry)
