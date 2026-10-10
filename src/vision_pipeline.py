"""Shared frame gate for real, mock and offline evaluation; never sends output."""
import time
import math
from collections import deque

from src.roi_locator import RoiLocator
from src.health_detector import HealthDetector
from src.effect_detector import EffectDetector
from src.state_machine import StateMachine


class VisionPipeline:
    def __init__(self, config):
        self.roi_locator = RoiLocator(config)
        self.health_detector = HealthDetector(config)
        self.effect_detector = EffectDetector(config)
        self.state_machine = StateMachine(config)
        self.max_frame_gap = float(config.get("trigger", {}).get("max_frame_gap_sec", 1.0))
        if not math.isfinite(self.max_frame_gap) or not .05 <= self.max_frame_gap <= 5.0:
            raise ValueError("max_frame_gap_sec must be finite and within [0.05, 5.0]")
        self._last_frame = None
        self._health_observations = deque(maxlen=3)

    def reset(self):
        self.health_detector.reset_ema()
        self.state_machine.reset()
        self._last_frame = None
        self._health_observations.clear()

    def process(self, frame, timestamp=None):
        try:
            return self._process(frame, timestamp)
        except Exception:
            # 处理失败同样切断历史；调用方负责同步 trigger.reset/停止输出。
            self.reset()
            raise

    def _process(self, frame, timestamp=None):
        now = time.monotonic() if timestamp is None else timestamp
        if not math.isfinite(now):
            raise ValueError("Frame timestamp must be finite")
        if self._last_frame is not None and (
                now < self._last_frame or now - self._last_frame > self.max_frame_gap):
            self.reset()
        self._last_frame = now
        roi = self.roi_locator.locate(frame)
        # Probe first: even a high filled/empty confidence is not HUD evidence.
        health = self.health_detector.detect(roi.inner_img, update_ema=False)
        effects = self.effect_detector.detect(frame, health_raw=health.health_raw)
        finite = all(math.isfinite(value) and 0 <= value <= 1 for value in (
            health.health_raw, health.confidence, roi.anchor_score,
            effects.red_score, effects.cyan_score, effects.death_score))
        accepted = (finite and roi.valid and health.confidence > 0
                    and health.confidence >= self.state_machine.min_confidence
                    and roi.anchor_score > 0
                    and roi.anchor_score >= self.state_machine.min_anchor_score)
        warming_up = False
        if accepted:
            # 事件层收到的是经观测确认的非 EMA 血量。三观测中值只去掉孤立
            # 跳点，持续的单像素涨跌仍会在下一观测确认，不能改回 2% 门槛。
            self._health_observations.append(health.health_raw)
            confirmed_hp = (sorted(self._health_observations)[1]
                            if len(self._health_observations) == 3
                            else self._health_observations[0])
            warming_up = len(self._health_observations) == 1
            if (len(self._health_observations) == 2
                    and self._health_observations[0] != self._health_observations[1]):
                # 两次启动观测不同，不能把第一值重复两次冒充确认。这也适用
                # 于拒识/暂停/长帧间隔后恢复，避免先建立假低基线再虚假治疗。
                accepted = False
                self.health_detector.reset_ema()
            else:
                health.health_stable = self.health_detector.commit_health(confirmed_hp)
        else:
            self.health_detector.reset_ema()
            self._health_observations.clear()
            confirmed_hp = health.health_raw
        # 零血的单帧跳点也不能经 death_score 绕开血量观测确认。
        death_score = (effects.death_score
                       if confirmed_hp <= self.state_machine.health_near_zero else 0.0)
        result = self.state_machine.update(
            health_raw=confirmed_hp, health_stable=health.health_stable,
            red_score=effects.red_score, cyan_score=effects.cyan_score,
            death_score=death_score, anchor_score=roi.anchor_score,
            confidence=health.confidence, valid=accepted, timestamp=now,
        )
        result.debug["health_observed"] = health.health_raw
        result.debug["health_confirmed"] = confirmed_hp if accepted else None
        if not accepted:
            result.debug["reject_reason"] = (
                roi.reject_reason or ("nonfinite_observation" if not finite
                                      else "low_health_confidence" if health.confidence < self.state_machine.min_confidence
                                      else "observation_confirmation"))
        if warming_up:
            result.state = "unknown"
            result.valid = False
            result.debug["health_confirmed"] = None
            result.debug["reject_reason"] = "observation_warmup"
        return result, roi, health
