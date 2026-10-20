"""SDK v1 configuration schema; no host access or official-edition mutations."""
from copy import deepcopy

from src.feedback_settings import (EVENTS, default_settings, threshold_percent, THRESHOLD_KEY,
                                   DEATH_MODE_KEY, DEATH_MODE_LABELS)
from src.feedback_mixer import validate_death_mode
from src.hud_profiles import SCALES, RESOLUTION, get_profile, validate_resolution
from src.hud_settings import EXPERIMENTAL_KEY


PLUGIN_ID = "helldiver_dglab"
WAVE_TO_EVENT = {wave: event for event, _label, _strength, wave in EVENTS}
DEFAULT_PRESETS = dict(injury_pulse="连击", low_health="呼吸", heal_pulse="潮汐",
                       healing_sustain="心跳节奏", death_pulse="CS2-死亡")
HOST_CONFIG_KEYS = frozenset({"idle_strength", "enabled", "target_id"})
LEGACY_CONFIG_KEYS = frozenset({"running"})


def manifest():
    sections = [{"section": "运行", "fields": [
        {"key": "output_allowed", "type": "bool", "label": "允许输出（保险；启停请用浮窗或 F8）",
         "default": True, "description": "关闭立即暂停并归零；重新打开不会启动，仍需浮窗或 F8。"},
        {"key": "monitor_index", "type": "number", "label": "显示器编号", "default": 1,
         "min": 1, "max": 32, "step": 1},
        {"key": "hud_scale_percent", "type": "select", "label": "游戏 HUD 大小",
         "default": 100, "description": "与游戏相同；0.75–1.25 每档0.05。不是分辨率或Windows缩放。",
         "options": [{"value": scale, "label": f"{scale / 100:.2f}"} for scale in SCALES]},
        {"key": EXPERIMENTAL_KEY, "type": "bool", "label": "允许实验性 2K／4K 适配",
         "default": False, "description": "2560×1440／3840×2160 为1080p等比推算，未实机验收；分辨率按实际抓屏读取。停用再启用生效。"},
        {"key": "panel_mode", "type": "select", "label": "控制面板", "default": "full",
         "options": [{"value": "full", "label": "完整面板"},
                     {"value": "island", "label": "灵动岛"},
                     {"value": "live", "label": "直播模式（极简按钮）"},
                     {"value": "hidden", "label": "隐藏（F8仍可用）"}]},
        {"key": "channel_mode", "type": "channel", "label": "输出通道", "default": "a"},
        {"key": "max_strength", "type": "percent", "label": "本插件 A 上限 %", "default": 100},
        {"key": "max_strength_b", "type": "percent", "label": "本插件 B 上限 %", "default": 100},
    ]}]
    for event, label, strength, _wave in EVENTS:
        sections.append({"section": label, "fields": [
            {"key": event + "_preset", "type": "preset", "label": "DGHub 波形预设",
             "default": DEFAULT_PRESETS[event],
             "description": "从宿主选择；舒缓与死亡默认值不同于独立版专用模板。"},
            {"key": event + "_strength", "type": "percent", "label": "A 基准 %", "default": strength},
            {"key": event + "_strength_b", "type": "percent", "label": "B 基准 %", "default": strength},
        ]})
        if event == "low_health":
            sections[-1]["fields"].insert(0, {
                "key": THRESHOLD_KEY, "type": "number", "label": "残血／濒死触发血量 %",
                "default": 75, "min": 0, "max": 100, "step": 1,
                "description": "低于此血量持续反馈；0% 关闭此反馈，不是 A/B 强度。停用再启用生效。"})
        if event == "death_pulse":
            sections[-1]["fields"].insert(0, {
                "key": DEATH_MODE_KEY, "type": "select", "label": "死亡反馈模式", "default": "pulse",
                "options": [{"value": mode, "label": label} for label, mode in DEATH_MODE_LABELS.items()],
                "description": "挑战复活后仍持续，F8 重置并暂停；异常仍停输出。停用再启用生效。"})
    return {"id": PLUGIN_ID, "name": "HELLDIVER — 多分辨率可调 HUD",
            "version": "1.0.7", "sdk": "1", "entry": "helldiver_dglab.exe",
            "description": "11档HUD、五事件波形与独立A/B强度；每次启用后按F8开始。",
            "capabilities": {"startup_check": True},
            "config_schema": sections}


def defaults():
    return {field["key"]: field["default"] for section in manifest()["config_schema"]
            for field in section["fields"]}


def validate(data, *, available_presets=None):
    if not isinstance(data, dict):
        raise ValueError("SDK configuration must be an object")
    result = defaults()
    if set(data) - (set(result) | HOST_CONFIG_KEYS | LEGACY_CONFIG_KEYS):
        raise ValueError("Unsupported SDK configuration field")
    if "running" in data and type(data["running"]) is not bool:
        raise ValueError("Legacy running must be a boolean")
    # This checks supplied data; it does not clean an installed host baseline
    # or prove that a host has not already restored a cached value at hello.
    if type(data.get("idle_strength", 0)) is not int or data.get("idle_strength", 0) != 0:
        raise ValueError("Nonzero or invalid host baseline; migration requires separate approval")
    if "enabled" in data and type(data["enabled"]) is not bool:
        raise ValueError("Invalid host enabled flag")
    target = data.get("target_id")
    if target is not None and (not isinstance(target, str) or len(target) > 256
                               or any(ord(char) < 32 for char in target)):
        raise ValueError("Invalid host target identifier")
    result.update({key: value for key, value in data.items() if key in result})
    if type(result["output_allowed"]) is not bool:
        raise ValueError("output_allowed must be a boolean")
    if type(result[EXPERIMENTAL_KEY]) is not bool:
        raise ValueError("实验性高分辨率开关必须为布尔值")
    if result["channel_mode"] not in ("a", "b", "both"):
        raise ValueError("channel_mode must be a, b or both")
    if result["panel_mode"] not in ("full", "island", "hidden", "live"):
        raise ValueError("Invalid control panel mode")
    scale = result["hud_scale_percent"]
    # DGHub's browser select may persist a string even with integer schema
    # options. Normalize only exact listed wire values, not arbitrary numbers;
    # the shared HUD profiles and saved session snapshot remain integer-based.
    if type(scale) is str and scale in {str(value) for value in SCALES}:
        scale = result["hud_scale_percent"] = int(scale)
    if type(scale) is not int or scale not in SCALES:
        raise ValueError("HUD must be 75..125, step 5 (0.75–1.25)")
    result[THRESHOLD_KEY] = threshold_percent(result[THRESHOLD_KEY])
    result[DEATH_MODE_KEY] = validate_death_mode(result[DEATH_MODE_KEY])
    for key, value in result.items():
        if key in {"output_allowed", EXPERIMENTAL_KEY, "channel_mode", "panel_mode", "hud_scale_percent", DEATH_MODE_KEY}:
            continue
        if key.endswith("_preset"):
            if (not isinstance(value, str) or not value.strip() or value != value.strip()
                    or len(value) > 128 or any(ord(char) < 32 for char in value)):
                raise ValueError(f"Select a valid DGHub preset for {key}")
            if available_presets is not None and value not in available_presets:
                raise ValueError(f"Unknown DGHub preset for {key}")
        else:
            lower, upper = (1, 32) if key == "monitor_index" else (0, 100)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{key} must be an integer within {lower}..{upper}")
    return result


def core_config(base, settings, resolution=RESOLUTION):
    """Unique existing logical IDs preserve event identity without changing Mixer.

    Their waveform bytes are never sent to DGHub. A second, validated mapping
    resolves the winning ID to the user's host preset name at the boundary.
    """
    resolution = validate_resolution(resolution)
    config = deepcopy(base)
    config.setdefault("trigger", {})["sender_mode"] = "mock"
    config["trigger"]["low_health_hp_threshold"] = settings[THRESHOLD_KEY] / 100
    config["trigger"][DEATH_MODE_KEY] = settings[DEATH_MODE_KEY]
    sender = default_settings()
    for key in sender:
        if key in settings:
            sender[key] = settings[key]
    config.setdefault("sender", {}).update(sender)
    config.setdefault("capture", {})["monitor_index"] = settings["monitor_index"]
    profile = get_profile(settings["hud_scale_percent"], resolution)
    config["ROI"] = profile["roi"]
    config["hud"] = profile["hud"]
    config.setdefault("calibration", {}).update(
        calibrated=1, resolution=f"{resolution[0]}x{resolution[1]}",
        source=("dghub_hud1080_" if resolution == RESOLUTION else "dghub_hud_") + profile["hud"]["source"],
        allow_experimental_high_resolution=int(settings.get(EXPERIMENTAL_KEY, False)))
    return config
