"""config_loader.py — 从 config.ini 加载配置为嵌套 dict。"""
import configparser
import logging
from pathlib import Path
from typing import Union


def load_config(config_path: Union[str, Path, None] = None) -> dict:
    """读取 config.ini 并解析为嵌套 dict，数值自动转为 int/float。

    Args:
        config_path: config.ini 路径，默认为项目根目录下的 config.ini

    Returns:
        dict: {section: {key: value}}
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "config.ini"
        if not config_path.is_file():
            config_path = config_path.with_name("config.example.ini")
            logging.getLogger(__name__).warning(
                "config.ini missing; using safe mock defaults from %s. Calibrate before real output.",
                config_path,
            )

    cp = configparser.ConfigParser()
    # Explicit paths must not silently turn into {}, causing a later KeyError.
    with Path(config_path).open(encoding="utf-8-sig") as stream:
        cp.read_file(stream)

    config = {}
    for section in cp.sections():
        config[section] = {}
        for key, val in cp.items(section):
            try:
                config[section][key] = float(val) if "." in val else int(val)
            except ValueError:
                config[section][key] = val
    return config
