"""Portable HUD selection; absent settings preserve the original preset."""
import configparser
import os
from pathlib import Path
import tempfile

from src.hud_profiles import SCALES

SETTING_KEYS = {"mode", "scale_percent"}
SCHEMA_VERSION = 1


def default_settings():
    return {"mode": "legacy", "scale_percent": 100}


def validate_settings(values):
    if not isinstance(values, dict) or set(values) != SETTING_KEYS:
        raise ValueError("HUD 设置需要且只能包含 mode 和 scale_percent")
    mode = values["mode"]
    if not isinstance(mode, str) or mode not in ("legacy", "manual"):
        raise ValueError("HUD 模式必须为 legacy 或 manual")
    scale = values["scale_percent"]
    if type(scale) is not int or scale not in SCALES:
        raise ValueError("HUD 大小必须为 0.75–1.25，每档 +0.05（整数 75–125）")
    return {"mode": mode, "scale_percent": scale}


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
        if set(section) != SETTING_KEYS | {"schema_version"}:
            raise ValueError("HUD 设置字段不完整或包含不支持的字段")
        if section.pop("schema_version") != str(SCHEMA_VERSION):
            raise ValueError("不支持的 HUD 设置版本")
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
