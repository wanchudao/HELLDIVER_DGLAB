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
import time
from collections import deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# JSONL 日志路径
LOG_PATH = Path(__file__).resolve().parent.parent / "debug" / "trigger_log.jsonl"

# 冷却时间（秒）
COOLDOWNS: dict[str, float] = {
    "injury_pulse": 0.5,
    "death_pulse": 5.0,
    "heal_pulse": 1.0,
    "low_health": 3.0,
}

# 判定阈值
INJURY_WINDOW_SEC = 0.2     # 受伤合并窗口
INJURY_DELTA_THRESH = 0.02   # 窗口内 hp 下降超过 2% 触发
DEATH_HP_THRESH = 0.01       # hp ≤ 1% 触发死亡
LOW_HEALTH_HP_THRESH = 0.50  # hp < 50% 触发残血


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

        # ── HP% 直判状态 ──
        self._hp_history: deque[tuple[float, float]] = deque()
        self._last_hp = -1.0
        self._last_fired: dict[str, float] = {}

        # 日志
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session_start",
                "ts": datetime.now().isoformat(),
                "mode": self._mode,
            }) + "\n")

        if self._mode == "mock":
            self._send = _mock_send
            logger.info("trigger ready  mode=mock (HP%-direct)")
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

        # ── HP 历史（0.2s 滑动窗口，用于受伤检测）──
        self._hp_history.append((now, hp))
        cutoff = now - INJURY_WINDOW_SEC
        while self._hp_history and self._hp_history[0][0] < cutoff:
            self._hp_history.popleft()

        action = None

        # 死亡: hp ≤ 1%
        if hp <= DEATH_HP_THRESH and self._last_hp >= 0:
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
            if now - last_lh >= COOLDOWNS["low_health"]:
                action = "low_health"

        # ── 冷却检查 + 发送 ──
        dispatched = False
        if action is not None:
            last = self._last_fired.get(action, 0)
            if now - last >= COOLDOWNS.get(action, 0.5):
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
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session_end",
                "ts": datetime.now().isoformat(),
                "frames": self._frame_idx,
            }) + "\n")
        if self._mode == "dglab":
            try:
                from src.dglab_sender import safe_stop as dglab_safe_stop
                dglab_safe_stop()
            except Exception as e:
                logger.error("trigger: safe_stop failed: %s", e)
        else:
            logger.info("trigger: safe_stop (no-op in mock mode)")

    def _write_log(self, entry: dict):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
