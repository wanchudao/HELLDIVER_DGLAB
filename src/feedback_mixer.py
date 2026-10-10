"""feedback_mixer.py — A/B 共享节奏、独立强度；纯时间逻辑，不访问设备。

持续层与短脉冲相加强度，波形按优先级选择，绝不永久累加强度。
所有层共用有效帧租约：采集卡住后，即使没有下一帧也能被 sender 清零。
"""

import math
import logging
import time
from dataclasses import dataclass
from src.waveforms import WAVEFORM_V3


def selected_channels(sender: dict) -> tuple[str, ...]:
    mode = sender.get("channel_mode", "a")
    if mode not in ("a", "b", "both"):
        raise ValueError("channel_mode 必须为 a、b 或 both")
    return {"a": ("A",), "b": ("B",), "both": ("A", "B")}[mode]


def configured_waveform(sender, name, default):
    wave = sender.get(f"{name}_waveform", default)
    if not isinstance(wave, str) or wave not in WAVEFORM_V3:
        raise ValueError(f"{name}: 未知波形 {wave!r}")
    return wave


def action_profiles(config: dict) -> dict:
    """Mock/设备共享同一组强度、时长和波形映射。"""
    sender = config.get("sender", {})
    channels = selected_channels(sender)
    defaults = {
        "injury_pulse": (20, 2, "weak_pulse"),
        "death_pulse": (40, 5, "death_pulse"),
        "heal_pulse": (15, 2, "heal_pulse"),
        "low_health": (10, 3, "weak_pulse"),
    }
    return {
        name: {"channel": channels[0], "waveform": configured_waveform(sender, name, waveform),
               "strength": bounded_number(sender.get(f"{name}_strength", strength),
                                           f"{name}_strength", 0, 100),
               "strength_b": bounded_number(sender.get(f"{name}_strength_b",
                                                       sender.get(f"{name}_strength", strength)),
                                             f"{name}_strength_b", 0, 100),
               "duration": bounded_number(sender.get(f"{name}_duration", duration),
                                           f"{name}_duration", .01, 60)}
        for name, (strength, duration, waveform) in defaults.items()
    }


def bounded_number(value, name: str, minimum: float, maximum: float) -> float:
    """拒绝 NaN/Infinity 和越界配置，避免安全边界被非有限值绕过。"""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


@dataclass(frozen=True)
class MixedOutput:
    strength: int = 0
    waveform: str = ""


class FeedbackMixer:
    """事件期限和波形优先级共用；强度与限幅在启用通道内独立计算。"""

    def __init__(self, config: dict):
        sender = config.get("sender", {})
        trigger = config.get("trigger", {})
        self.channels = selected_channels(sender)
        self.profiles = action_profiles(config)
        self.max_strength = bounded_number(sender.get("max_strength", 100),
                                           "max_strength", 0, 100)
        self.low_strength = bounded_number(sender.get("low_health_strength", 10),
                                           "low_health_strength", 0, 100)
        self.healing_strength = bounded_number(sender.get("healing_sustain_strength", 5),
                                               "healing_sustain_strength", 0, 100)
        self.max_strength_b = bounded_number(sender.get("max_strength_b", self.max_strength),
                                             "max_strength_b", 0, 100)
        self.low_strength_b = bounded_number(sender.get("low_health_strength_b", self.low_strength),
                                             "low_health_strength_b", 0, 100)
        self.healing_strength_b = bounded_number(sender.get("healing_sustain_strength_b", self.healing_strength),
                                                 "healing_sustain_strength_b", 0, 100)
        self.low_waveform = configured_waveform(sender, "low_health", "weak_pulse")
        self.healing_waveform = configured_waveform(sender, "healing_sustain", "heal_pulse")
        self.lease_sec = bounded_number(trigger.get("max_frame_gap_sec", 1.0),
                                        "max_frame_gap_sec", 0.05, 5.0)
        self.reset()

    def reset(self):
        self.low_health = False
        self.healing = False
        self.lease_deadline = 0.0
        self.pulses: dict[str, tuple[float, float, str, float]] = {}

    def _expire(self, now: float):
        if now >= self.lease_deadline:
            self.reset()
            return
        self.pulses = {name: pulse for name, pulse in self.pulses.items()
                       if pulse[1] > now}

    def update_continuous(self, *, low_health: bool, healing: bool, hp: float, now: float):
        hp = bounded_number(hp, "hp", 0, 1)
        self._expire(now)
        self.lease_deadline = now + self.lease_sec
        self.low_health = bool(low_health) and hp > 0
        self.healing = bool(healing) and hp > 0
        if hp > 0:
            self.pulses.pop("death_pulse", None)

    def add_pulse(self, action: str, *, strength: float, duration: float,
                  waveform: str, now: float, damage_pct: float = 1.0, strength_b=None):
        strength = bounded_number(strength, "pulse strength", 0, 100)
        strength_b = bounded_number(strength if strength_b is None else strength_b,
                                    "pulse strength B", 0, 100)
        if waveform not in WAVEFORM_V3:
            raise ValueError(f"未知波形: {waveform}")
        duration = bounded_number(duration, "pulse duration", 0.01, 60)
        if action == "injury_pulse":
            damage_pct = bounded_number(damage_pct, "damage_pct", 0, 1)
            strength *= damage_pct
            strength_b *= damage_pct
            if strength > 0:
                strength = max(1.0, strength)  # 最小正伤害保留一个设备强度单位
            if strength_b > 0:
                strength_b = max(1.0, strength_b)
        self._expire(now)
        if action == "death_pulse":
            self.reset()  # 死亡时不再叠加低血/治疗，也不遗留旧脉冲
        self.lease_deadline = now + self.lease_sec
        self.pulses[action] = (strength, now + duration, waveform, strength_b)

    def output(self, now: float) -> MixedOutput:
        """兼容旧单路调用方；新发送器与日志使用 outputs() 的两路快照。"""
        return self.outputs(now)[self.channels[0]]

    def _contributes(self, strength_a, strength_b):
        return (("A" in self.channels and self.max_strength > 0 and strength_a > 0)
                or ("B" in self.channels and self.max_strength_b > 0 and strength_b > 0))

    def outputs(self, now: float) -> dict[str, MixedOutput]:
        self._expire(now)
        dominant = self.dominant_action(now)
        waveform = self.pulses[dominant][2] if dominant else ""
        if not waveform and self.healing and self._contributes(self.healing_strength, self.healing_strength_b):
            waveform = self.healing_waveform
        if not waveform and self.low_health and self._contributes(self.low_strength, self.low_strength_b):
            waveform = self.low_waveform
        result = {}
        for ch, low, heal, limit, pulse_index in (
                ("A", self.low_strength, self.healing_strength, self.max_strength, 0),
                ("B", self.low_strength_b, self.healing_strength_b, self.max_strength_b, 3)):
            strength = (low if self.low_health else 0) + (heal if self.healing else 0)
            strength += sum(p[pulse_index] for p in self.pulses.values())
            # 先量化再限幅；不会把 25.7 的旧配置上限四舍五入到 26。
            strength = min(int(limit), int(round(strength))) if ch in self.channels else 0
            result[ch] = MixedOutput(strength, waveform) if strength > 0 else MixedOutput()
        return result

    def dominant_action(self, now: float) -> str:
        self._expire(now)
        return next((name for name in ("death_pulse", "heal_pulse", "injury_pulse")
                     if name in self.pulses and self._contributes(self.pulses[name][0], self.pulses[name][3])), "")


class MockFeedbackSender:
    """使用真实合成逻辑，但没有线程、socket或设备协议输出。"""

    def __init__(self, config: dict, clock=None):
        self.clock = clock or time.monotonic
        self.mixer = FeedbackMixer(config)
        self.profiles = action_profiles(config)
        self._last_output = MixedOutput()

    def _result(self, action="none"):
        outputs = self.mixer.outputs(self.clock())
        output = outputs[self.mixer.channels[0]]
        if action != "none" or outputs != self._last_output:
            logging.getLogger(__name__).info(
                "[MOCK SEND] action=%s channels=%s", action, outputs)
        self._last_output = outputs
        return {"dispatched": True, "mode": "mock", "action": action,
                "strength": output.strength, "waveform": output.waveform,
                "channels": {ch: vars(value) for ch, value in outputs.items()}}

    def update_continuous(self, **kwargs):
        self.mixer.update_continuous(**kwargs, now=self.clock())
        return self._result()

    def send_action(self, action, payload=None):
        profile = self.profiles[action]
        self.mixer.add_pulse(action, strength=profile["strength"], duration=profile["duration"],
                             strength_b=profile["strength_b"],
                             waveform=profile["waveform"], now=self.clock(),
                             damage_pct=(payload or {}).get("damage_pct", 1.0))
        return self._result(action)

    def stop_output(self):
        self.mixer.reset()
        self._result()
