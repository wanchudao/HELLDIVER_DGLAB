"""Portable desktop preferences; strict UI values, legacy INI compatibility."""
import configparser
import os
from pathlib import Path
import tempfile

from src.feedback_mixer import FeedbackMixer, configured_waveform, selected_channels, validate_death_mode


EVENTS = (
    ("injury_pulse", "受伤反馈（基准）", 20, "official_combo"),
    ("low_health", "残血／濒死", 10, "weak_pulse"),
    ("heal_pulse", "治疗确认", 15, "official_tide"),
    ("healing_sustain", "治疗持续舒缓", 5, "heal_pulse"),
    ("death_pulse", "死亡", 40, "death_pulse"),
)
LEGACY_WAVES = dict(injury_pulse="weak_pulse", low_health="weak_pulse",
                    heal_pulse="heal_pulse", healing_sustain="heal_pulse", death_pulse="death_pulse")
SETTING_KEYS = {"channel_mode", "max_strength", "max_strength_b"}
SETTING_KEYS.update(f"{event}_{suffix}" for event, *_ in EVENTS
                    for suffix in ("waveform", "strength", "strength_b"))
THRESHOLD_KEY = "low_health_threshold_percent"
DEATH_MODE_KEY = "death_feedback_mode"
DEATH_MODE_LABELS = {"定时反馈（默认 5 秒）": "pulse", "持续至复活": "until_revive",
                     "一次也不能死（F8 重置）": "until_reset"}


def default_trigger_settings():
    return {THRESHOLD_KEY: 75, DEATH_MODE_KEY: "pulse"}


def threshold_percent(value):
    """Integer UI units, with narrow compatibility for persisted wire strings."""
    if type(value) is str and value in {str(n) for n in range(101)}:
        value = int(value)
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError("残血／濒死触发血量必须是 0–100 的整数；0% 关闭此反馈")
    return value


def validate_trigger_settings(values):
    if not isinstance(values, dict) or set(values) - {THRESHOLD_KEY, DEATH_MODE_KEY}:
        raise ValueError("触发设置包含不支持的字段")
    return {THRESHOLD_KEY: threshold_percent(values.get(THRESHOLD_KEY, 75)),
            DEATH_MODE_KEY: validate_death_mode(values.get(DEATH_MODE_KEY, "pulse"))}


def default_settings():
    values = {"channel_mode": "a", "max_strength": 100, "max_strength_b": 100}
    for event, _, strength, waveform in EVENTS:
        values.update({f"{event}_waveform": waveform, f"{event}_strength": strength,
                       f"{event}_strength_b": strength})
    return values


def _integer(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是 0–100 的整数")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or not 0 <= int(text) <= 100:
        raise ValueError(f"{name} 必须是 0–100 的整数")
    return int(text)


def validate_settings(values):
    """Only feedback fields may override a generated capture/network config."""
    if not isinstance(values, dict) or set(values) - SETTING_KEYS:
        raise ValueError("反馈设置包含不支持的字段")
    sender = dict(values)
    selected_channels(sender)
    result = {"channel_mode": sender.get("channel_mode", "a")}
    result["max_strength"] = _integer(sender.get("max_strength", 100), "A 总上限")
    result["max_strength_b"] = _integer(sender.get("max_strength_b", result["max_strength"]), "B 总上限")
    for event, label, strength, _ in EVENTS:
        result[f"{event}_waveform"] = configured_waveform(sender, event, LEGACY_WAVES[event])
        result[f"{event}_strength"] = _integer(sender.get(f"{event}_strength", strength), label + " A")
        result[f"{event}_strength_b"] = _integer(sender.get(f"{event}_strength_b", result[f"{event}_strength"]), label + " B")
    FeedbackMixer({"sender": result})
    return result


def _load_preferences(path):
    path = Path(path)
    if not path.exists():
        return default_settings(), default_trigger_settings()
    cp = configparser.ConfigParser(interpolation=None)
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError("反馈设置文件过大")
        with path.open(encoding="utf-8-sig") as stream:
            cp.read_file(stream)
        if "sender" not in cp or set(cp.sections()) - {"sender", "trigger"} or cp.defaults():
            raise ValueError("反馈设置需要 [sender]，只能另含 [trigger]")
        triggers = validate_trigger_settings(dict(cp["trigger"]) if "trigger" in cp else {})
        return validate_settings(dict(cp["sender"])), triggers
    except (configparser.Error, UnicodeError) as error:
        raise ValueError(f"反馈设置文件损坏：{error}") from error


def load_settings(path):
    return _load_preferences(path)[0]


def load_trigger_settings(path):
    return _load_preferences(path)[1]


def save_settings(path, values, *, trigger_settings=None):
    validated = validate_settings(values)
    path = Path(path)
    # Sender-only callers must retain the threshold, not reset it to 75%.
    if trigger_settings is not None and not isinstance(trigger_settings, dict):
        raise ValueError("触发设置必须为对象")
    previous = (default_trigger_settings() if trigger_settings is not None
                and set(trigger_settings) == {THRESHOLD_KEY, DEATH_MODE_KEY}
                else load_trigger_settings(path))
    triggers = validate_trigger_settings(previous if trigger_settings is None else previous | trigger_settings)
    cp = configparser.ConfigParser()
    cp["sender"] = {key: str(value) for key, value in validated.items()}
    cp["trigger"] = {key: str(value) for key, value in triggers.items()}
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".feedback-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            cp.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return validated


class SettingsStore:
    """Invalid saved values block startup; recover explicitly through settings."""
    def __init__(self, path):
        self.path = Path(path)
        self.values = default_settings()
        self.trigger_values = default_trigger_settings()
        self.error = ""
        try:
            self.values, self.trigger_values = _load_preferences(self.path)
        except (OSError, ValueError) as error:
            self.error = str(error)

    def snapshot(self):
        if self.error:
            raise ValueError("请先在反馈设置中修正并保存：" + self.error)
        return validate_settings(self.values)

    def trigger_snapshot(self):
        if self.error:
            raise ValueError("请先在反馈设置中修正并保存：" + self.error)
        return validate_trigger_settings(self.trigger_values)

    def save(self, values, *, running=False, trigger_settings=None):
        if running:
            raise ValueError("请先停止全部，运行中不可修改反馈设置")
        if trigger_settings is not None and not isinstance(trigger_settings, dict):
            raise ValueError("触发设置必须为对象")
        triggers = validate_trigger_settings(self.trigger_values if trigger_settings is None
                                             else self.trigger_values | trigger_settings)
        self.values = save_settings(self.path, values, trigger_settings=triggers)
        self.trigger_values = triggers
        self.error = ""
        return dict(self.values)

