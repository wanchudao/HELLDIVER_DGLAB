"""Portable HUD selection; absent settings preserve the original preset."""
import configparser
import os
from pathlib import Path
import tempfile

from src.hud_profiles import SCALES

SETTING_KEYS = {"mode", "scale_percent"}
EXPERIMENTAL_KEY = "allow_experimental_high_resolution"
SCHEMA_VERSION = 2


def default_settings():
    return {"mode": "legacy", "scale_percent": 100, EXPERIMENTAL_KEY: False}


def validate_settings(values):
    if (not isinstance(values, dict) or not SETTING_KEYS <= set(values)
            or not set(values) <= SETTING_KEYS | {EXPERIMENTAL_KEY}):
        raise ValueError("HUD 设置字段缺失或未知")
    mode = values["mode"]
    if not isinstance(mode, str) or mode not in ("legacy", "manual"):
        raise ValueError("HUD 模式必须为 legacy 或 manual")
    scale = values["scale_percent"]
    if type(scale) is not int or scale not in SCALES:
        raise ValueError("HUD 大小必须为 0.75–1.25，每档 +0.05（整数 75–125）")
    experimental = values.get(EXPERIMENTAL_KEY, False)
    if type(experimental) is not bool:
        raise ValueError("实验性高分辨率设置必须为布尔值")
    return {"mode": mode, "scale_percent": scale, EXPERIMENTAL_KEY: experimental}


def load_settings(path):
    path = Path(path)
    if not path.exists():
        return default_settings()
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    try:
        if path.stat().st_size > 8 * 1024:
            raise ValueError("HUD 设置文件过大")
        with path.open(encoding="utf-8-sig") as stream:
            cp.read_file(stream)
        if cp.sections() != ["hud"] or cp.defaults():
            raise ValueError("HUD 设置需要且只能包含 [hud]")
        section = dict(cp["hud"])
        version = section.pop("schema_version", None)
        expected_keys = SETTING_KEYS if version == "1" else SETTING_KEYS | {EXPERIMENTAL_KEY}
        if version not in {"1", str(SCHEMA_VERSION)}:
            raise ValueError("不支持的 HUD 设置版本")
        if set(section) != expected_keys:
            raise ValueError("HUD 设置字段不完整或包含不支持的字段")
        if version != "1":
            if section[EXPERIMENTAL_KEY] not in {"0", "1"}:
                raise ValueError("实验性高分辨率设置必须为 0 或 1")
            section[EXPERIMENTAL_KEY] = section[EXPERIMENTAL_KEY] == "1"
        scale_text = section["scale_percent"]
        if not scale_text.isascii() or not scale_text.isdecimal():
            raise ValueError("HUD scale_percent 必须为整数 75–125，每档 +5")
        section["scale_percent"] = int(scale_text)
        return validate_settings(section)
    except (configparser.Error, UnicodeError) as error:
        raise ValueError(f"HUD 设置文件损坏：{error}") from error


def save_settings(path, values):
    validated = validate_settings(values)
    cp = configparser.ConfigParser(interpolation=None)
    cp["hud"] = {"schema_version": str(SCHEMA_VERSION),
                 **{key: str(value) for key, value in validated.items()}}
    cp["hud"][EXPERIMENTAL_KEY] = "1" if validated[EXPERIMENTAL_KEY] else "0"
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".hud-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            cp.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return validated


class HudSettingsStore:
    """Keep malformed preferences blocking until the user explicitly repairs them."""
    def __init__(self, path):
        self.path = Path(path)
        self.values = default_settings()
        self.error = ""
        try:
            self.values = load_settings(self.path)
        except (OSError, ValueError) as error:
            self.error = str(error)

    def snapshot(self):
        if self.error:
            raise ValueError("请先在 HUD 设置中修正并保存：" + self.error)
        return validate_settings(self.values)

    def save(self, values, *, running=False):
        if running:
            raise ValueError("请先停止全部，运行中不可修改 HUD 设置；F8 暂停不会解锁")
        self.values = save_settings(self.path, values)
        self.error = ""
        return dict(self.values)
