"""Trigger dispatch: 接收 DetectionResult，HP% 直判路由到 sender。

设计原则:
- 不用 event 中间层——HP% 直接判定
- 死亡看 hp ≤ 1%
- 受伤看 0.2s 窗口内 hp 下降 > 2%
- 治疗看 hp 比上一帧高
- 残血看 hp < 50% 每 3s
- 冷却统一在 trigger 层用挂钟时间管理
- sender 通过 config.ini [trigger] sender_mode 切换
"""
import json
import logging
import os
import sys as _sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread

logger = logging.getLogger(__name__)

# JSONL 日志路径（PyInstaller 下对齐 main.py 的 EXE_DIR → TEMP fallback）
if getattr(_sys, 'frozen', False):
    _TRIGGER_BASE = Path(_sys.executable).resolve().parent
else:
    _TRIGGER_BASE = Path(__file__).resolve().parent.parent

# DGHub Job Object 可能禁止 .exe 目录写入，fallback 到 %TEMP%
LOG_PATH = None
for _base in [_TRIGGER_BASE, Path(os.environ.get("TEMP", os.environ.get("TMP", "")))]:
    try:
        if not _base or str(_base) == ".":
            continue
        _log_dir = _base / "debug"
        _log_dir.mkdir(exist_ok=True)
        LOG_PATH = _log_dir / "trigger_log.jsonl"
        break
    except Exception:
        continue

if LOG_PATH is None:
    LOG_PATH = _TRIGGER_BASE / "debug" / "trigger_log.jsonl"  # 最后的 fallback

# 判定阈值（不受 config.ini 覆盖 — 调参经验值）
INJURY_WINDOW_SEC = 0.2     # 受伤合并窗口
INJURY_DELTA_THRESH = 0.02   # 窗口内 hp 下降超过 2% 触发
LOW_HEALTH_HP_THRESH = 0.50  # hp < 50% 触发残血


class _AsyncJsonlWriter:
    """异步写入帧诊断日志，不让文件 I/O 阻塞 vision 线程。

    冻结调查中发现旧实现每帧都在 vision 线程中 open/write/close
    ``trigger_log.jsonl``。在 DGHub 的安装目录被 Defender 或其他
    minifilter 扫描时，CreateFileW 可能长时间挂起；异常处理无法捕获
    这种阻塞。这里让单独的 daemon 线程持有一个带缓冲的文件句柄，
    vision 线程只做有界队列的 ``put_nowait``。日志异常时宁可丢失诊断
    帧，也不能停止检测或设备的 safe-stop 路径。
    """

    _QUEUE_SIZE = 600  # 约 20 秒 @ 30 FPS，满时丢弃而不是反压 vision
    _FLUSH_EVERY = 120  # 约 4 秒 @ 30 FPS，避免高频 FlushFileBuffers

    def __init__(self, path: Path):
        self._path = path
        self._queue: Queue[str] = Queue(maxsize=self._QUEUE_SIZE)
        self._stopped = Event()
        self._drop_warning_logged = False
        self._thread = Thread(
            target=self._run,
            daemon=True,
            name="TriggerJsonlWriter",
        )
        self._thread.start()

    def append(self, entry: dict) -> None:
        """排入一条日志；队列满时丢弃，绝不阻塞调用方。"""
        if self._stopped.is_set():
            return
        try:
            line = json.dumps(entry, ensure_ascii=False)
            self._queue.put_nowait(line)
        except Full:
            if not self._drop_warning_logged:
                self._drop_warning_logged = True
                logger.warning(
                    "trigger JSONL queue is full; dropping diagnostic frames "
                    "to keep the vision loop responsive"
                )
        except (TypeError, ValueError):
            logger.exception("trigger JSONL serialization failed")

    def close(self) -> None:
        """请求收尾但不等待可能被文件系统卡住的写线程。"""
        self._stopped.set()
        self._thread.join(timeout=0.25)
        if self._thread.is_alive():
            logger.warning("trigger JSONL writer did not stop within 250ms")

    def _run(self) -> None:
        """在后台批量落盘。任何阻塞只会影响诊断日志线程。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8", buffering=64 * 1024) as f:
                pending_since_flush = 0
                while not self._stopped.is_set() or not self._queue.empty():
                    try:
                        line = self._queue.get(timeout=0.1)
                    except Empty:
                        if pending_since_flush:
                            f.flush()
                            pending_since_flush = 0
                        continue

                    f.write(line + "\n")
                    pending_since_flush += 1
                    if pending_since_flush >= self._FLUSH_EVERY:
                        f.flush()
                        pending_since_flush = 0

                if pending_since_flush:
                    f.flush()
        except Exception:
            # 不能让诊断日志失败影响 trigger 或 vision 生命周期。
            logger.exception("trigger JSONL writer failed")


def _mock_send(action: str, payload: dict | None = None) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [MOCK SEND] action={action}  payload={payload}")
    return {"dispatched": True, "action": action, "mode": "mock"}


class TriggerDispatch:
    """接收 vision 结果，HP% 直判路由到 sender adapter。"""

    def __init__(self, config: dict):
        trigger_cfg = config.get("trigger", {})
        self._mode = trigger_cfg.get("sender_mode", "mock")
        self._frame_idx = 0

        # ── 冷却时间（从 config.ini [trigger] 读取，有默认值兜底）──
        self._cooldowns: dict[str, float] = {
            "injury_pulse": float(trigger_cfg.get("injury_cooldown_sec", 0.5)),
            "death_pulse": float(trigger_cfg.get("death_cooldown_sec", 5.0)),
            "heal_pulse": float(trigger_cfg.get("healing_cooldown_sec", 1.0)),
            "low_health": float(trigger_cfg.get("low_health_cooldown_sec", 3.0)),
        }
        # 死亡阈值（优先 [trigger] 自定义，fallback 到 [death] health_near_zero）
        death_cfg = config.get("death", {})
        self._death_hp_thresh = float(
            trigger_cfg.get("death_hp_threshold", death_cfg.get("health_near_zero", 0.05))
        )

        # ── HP% 直判状态 ──
        self._hp_history: deque[tuple[float, float]] = deque()
        self._last_hp = -1.0
        self._last_fired: dict[str, float] = {}
        self._log_writer = _AsyncJsonlWriter(LOG_PATH)

        self._write_log({
            "type": "session_start",
            "ts": datetime.now().isoformat(),
            "mode": self._mode,
        })

        if self._mode == "mock":
            self._send = _mock_send
            logger.info("trigger ready  mode=mock (HP%-direct)")
            return

        if self._mode == "dghub":
            # DGHub sender — _send 由 main.py 在运行时注入（需要 event loop 上下文）
            # 这里设一个占位函数，避免 dispatch() 中 _send 未定义
            self._send = lambda action, payload=None: logger.debug(
                "dghub sender stub (should be replaced by main.py)")
            logger.info("trigger ready  mode=dghub (waiting for main.py to wire _send)")
            return

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
            return

        logger.warning("trigger: unknown sender_mode=%s, falling back to mock", self._mode)
        self._send = _mock_send
        self._mode = "mock"
        logger.info("trigger ready  mode=%s", self._mode)

    def dispatch(self, result) -> dict:
        """接收 DetectionResult，HP% 直判路由。"""
        self._frame_idx += 1
        hp = result.health_stable
        now = time.time()

        # ── 无效帧跳过：HUD 不可见时不触发任何事件 ──
        if not result.valid:
            self._hp_history.clear()
            self._last_hp = -1.0  # 与 _hp_history 同步清空，防 stale 值触发误判
            return {"action": None, "reason": "invalid_frame"}

        # ── HP 历史（0.2s 滑动窗口，用于受伤检测）──
        self._hp_history.append((now, hp))
        cutoff = now - INJURY_WINDOW_SEC
        while self._hp_history and self._hp_history[0][0] < cutoff:
            self._hp_history.popleft()

        action = None

        # 死亡: state_machine 判定 OR hp ≤ 阈值
        # state 路径是主路径（多信号融合），hp 路径是 fallback（HUD 消失后兜底）
        if (result.state == "death"
                or (hp <= self._death_hp_thresh and self._last_hp >= 0)):
            action = "death_pulse"

        # 治疗: hp 比上一帧高 > 2%（防止测量噪点误触发）
        elif self._last_hp >= 0 and hp - self._last_hp > 0.02:
            action = "heal_pulse"

        # 受伤: 0.2s 窗口内 hp 下降 > 2%
        elif len(self._hp_history) >= 2:
            hp_oldest = self._hp_history[0][1]
            hp_drop = hp_oldest - hp
            if hp_drop > INJURY_DELTA_THRESH:
                action = "injury_pulse"

        # 残血: hp < 50%, 每 3s（仅当没有更高优先级事件时）
        if action is None and hp < LOW_HEALTH_HP_THRESH:
            last_lh = self._last_fired.get("low_health", 0)
            if now - last_lh >= self._cooldowns["low_health"]:
                action = "low_health"

        # ── 冷却检查 + 发送 ──
        dispatched = False
        if action is not None:
            last = self._last_fired.get(action, 0)
            if now - last >= self._cooldowns.get(action, 0.5):
                payload = {"hp": round(hp, 3), "state": result.state}
                self._send(action, payload)
                self._last_fired[action] = now
                dispatched = True
                logger.info(
                    "trigger: %s  hp=%.1f%%  模式=%s",
                    action, hp * 100, self._mode,
                )

        # ── 日志 ──
        log_entry = {
            "frame": self._frame_idx,
            "ts": datetime.now().isoformat(),
            "hp": round(hp, 3),
            "raw_hp": round(result.health_raw, 3),
            "state": result.state,
            "action": action if dispatched else "none",
            "dispatched": dispatched,
        }
        self._write_log(log_entry)

        self._last_hp = hp
        return {"dispatched": dispatched, "action": action or "none"}

    def safe_stop(self):
        self._write_log({
            "type": "session_end",
            "ts": datetime.now().isoformat(),
            "frames": self._frame_idx,
        })
        self._log_writer.close()
        if self._mode == "dglab":
            try:
                from src.dglab_sender import safe_stop as dglab_safe_stop
                dglab_safe_stop()
            except Exception as e:
                logger.error("trigger: safe_stop failed: %s", e)
        elif self._mode == "dghub":
            logger.info("trigger: safe_stop (dghub: no local cleanup needed)")
        else:
            logger.info("trigger: safe_stop (no-op in mock mode)")

    def _write_log(self, entry: dict):
        self._log_writer.append(entry)
