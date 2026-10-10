"""
state_machine.py
跨帧状态机。

负责：
1. 状态确认 (debounce)
2. 受伤脉冲事件 (0.2s 窗口合并)
3. 死亡事件
4. 治疗事件
5. unknown 拒识 — unknown 绝不触发设备
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class GameState(Enum):
    UNKNOWN = "unknown"
    FULL_HEALTH = "full_health"
    INJURED = "injured"
    DEATH = "death"
    HEALING = "healing"


@dataclass
class DetectionResult:
    """最终检测结果输出"""
    health_raw: float = 0.0
    health_stable: float = 0.0
    red_score: float = 0.0
    cyan_score: float = 0.0
    death_score: float = 0.0
    state: str = "unknown"
    confidence: float = 0.0
    event: str = "none"
    anchor_score: float = 0.0
    valid: bool = False
    timestamp: float = 0.0
    debug: dict = field(default_factory=dict)


class StateMachine:
    """跨帧状态机。"""

    def __init__(self, config: dict):
        sm_cfg = config["state_machine"]
        self.merge_window = sm_cfg["merge_window_sec"]
        self.debounce_frames = sm_cfg["debounce_frames"]
        self.unknown_alarm = sm_cfg["unknown_alarm_frames"]
        # 状态判定阈值（从 config.ini 读取，不再硬编码）
        self.injured_health_threshold = sm_cfg["injured_health_threshold"]
        self.healing_full_threshold = sm_cfg["healing_full_threshold"]
        self.healing_cyan_threshold = sm_cfg["healing_cyan_threshold"]

        # 从其他 section 读取
        self.min_confidence = config["unknown"]["min_confidence"]
        self.min_anchor_score = config["unknown"]["min_anchor_score"]
        self.death_score_threshold = config["death"]["death_score_threshold"]
        self.health_near_zero = config["death"]["health_near_zero"]

        # 内部状态
        self._state = GameState.UNKNOWN
        self._prev_state = GameState.UNKNOWN
        self._state_frame_count = 0  # 当前状态连续帧数
        self._unknown_counter = 0  # 连续 unknown 帧数

        # 历史
        self._prev_health_stable = -1.0
        self._health_history: list[float] = []

        logger.info(
            "state_machine ready  merge_window=%.2fs  debounce=%d  "
            "injured_hp<%.2f  healing=rising_hp<%.2f  "
            "death_score>%.2f  hp_near_zero=%.2f  "
            "min_conf=%.2f  min_anchor=%.2f",
            self.merge_window, self.debounce_frames,
            self.injured_health_threshold, self.healing_full_threshold,
            self.death_score_threshold, self.health_near_zero,
            self.min_confidence, self.min_anchor_score,
        )

    def reset(self):
        """重置所有内部状态（用于 mock 循环重启或目录切换）。"""
        self._state = GameState.UNKNOWN
        self._prev_state = GameState.UNKNOWN
        self._state_frame_count = 0
        self._unknown_counter = 0
        self._prev_health_stable = -1.0
        self._health_history.clear()

    @property
    def state(self) -> GameState:
        return self._state

    @property
    def is_unknown(self) -> bool:
        return self._state == GameState.UNKNOWN

    def update(
        self,
        health_raw: float,
        health_stable: float,
        red_score: float,
        cyan_score: float,
        death_score: float,
        anchor_score: float,
        confidence: float,
        valid: bool,
        timestamp: float = -1.0,
    ) -> DetectionResult:
        """输入新一帧的检测结果，返回最终结果。

        Args:
            health_raw: 原始血量
            health_stable: EMA 平滑血量
            red_score: 红屏强度
            cyan_score: 青蓝滤镜强度
            death_score: 死亡辅助置信度
            anchor_score: 锚点匹配度
            confidence: 综合置信度
            valid: ROI 是否有效
            timestamp: 时间戳（-1 表示用当前时间）

        Returns:
            DetectionResult
        """
        if timestamp < 0:
            timestamp = time.time()

        result = DetectionResult(
            health_raw=health_raw,
            health_stable=health_stable,
            red_score=red_score,
            cyan_score=cyan_score,
            death_score=death_score,
            anchor_score=anchor_score,
            confidence=confidence,
            timestamp=timestamp,
        )

        # ——— 1. unknown 拒识 ———
        if not valid or confidence < self.min_confidence or anchor_score < self.min_anchor_score:
            self._unknown_counter += 1
            self._state = GameState.UNKNOWN
            result.state = GameState.UNKNOWN.value
            result.event = "none"
            result.debug = {
                "raw_state": "unknown",
                "reject_reason": "valid" if not valid else "confidence" if confidence < self.min_confidence else "anchor",
                "state_frame_count": 0,
                "unknown_counter": self._unknown_counter,
            }
            if self._unknown_counter >= self.unknown_alarm:
                logger.warning(
                    "state_machine: unknown 连续 %d 帧，建议检查 HUD",
                    self._unknown_counter,
                )
            return result
        else:
            self._unknown_counter = 0

        # ——— 2. 单帧状态判定 ———
        raw_state = self._judge_state(
            health_raw, health_stable, red_score, cyan_score, death_score
        )

        # ——— 3. debounce ———
        if raw_state == self._prev_state:
            self._state_frame_count += 1
        else:
            self._state_frame_count = 1
            self._prev_state = raw_state

        if self._state_frame_count >= self.debounce_frames:
            self._state = raw_state

        result.state = self._state.value
        result.event = "none"

        result.valid = True
        result.debug = {
            "raw_state": raw_state.value,
            "state_frame_count": self._state_frame_count,
            "unknown_counter": self._unknown_counter,
        }

        # 更新历史
        self._prev_health_stable = health_stable
        self._health_history.append(health_stable)
        if len(self._health_history) > 300:  # 保留最近 5 秒 @60fps
            self._health_history = self._health_history[-300:]

        return result

    def _judge_state(
        self,
        health_raw: float,
        health_stable: float,
        red_score: float,
        cyan_score: float,
        death_score: float,
    ) -> GameState:
        """单帧状态判定。"""
        # 优先级：死亡 > 治疗 > 受伤 > 满血
        # 信号1: effect_detector 多信号融合（红屏+血量双重确认）
        if death_score >= self.death_score_threshold:
            return GameState.DEATH
        # 信号2: HP% 归零 fallback（红屏消退后 HUD 不可见时兜底）
        # 需要 prev > 0 避免 unknown/EMA 未初始化时的假阳性
        if (health_stable <= self.health_near_zero
                and self._prev_health_stable > self.health_near_zero):
            return GameState.DEATH

        # 治疗 = 血量回升（纯亮度方向判断，不依赖滤镜颜色）
        # HD2 中只有打针会让血量上升，复活也是一种血量回升
        if (self._prev_health_stable >= 0
                and health_stable > self._prev_health_stable
                and health_stable < self.healing_full_threshold):
            return GameState.HEALING

        if health_stable < self.injured_health_threshold:
            return GameState.INJURED

        return GameState.FULL_HEALTH
