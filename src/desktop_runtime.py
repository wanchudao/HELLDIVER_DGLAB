"""Owned official-backend process and screenshot-derived desktop presets."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from src.network_addresses import validate_advertised_ipv4


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def backend_paths():
    if getattr(sys, "frozen", False):
        return ROOT / "runtime/node.exe", ROOT / "backend/src/managed.js"
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node runtime missing")
    return Path(node), ROOT / "official_v2/socket/v2/backend/src/managed.js"


def validate_display(size, hud_settings=None):
    """Validate before starting a backend or creating a session."""
    from src.hud_settings import validate_settings
    settings = validate_settings(hud_settings) if hud_settings is not None else None
    width, height = size
    if settings is not None and settings["mode"] == "manual":
        if (width, height) != (1920, 1080):
            raise ValueError("HUD 档位模式仅支持完整的 1920×1080 画面；不会自动缩放。")
    elif width < 640 or height < 360 or abs(width / height - 16 / 9) > .01:
        raise ValueError("当前内置预设支持 16:9；请先将游戏/显示器设为 1920×1080。")
    return settings


def make_config(destination, monitor, size, mode, port=9999, *, qr_host=None, feedback_settings=None,
                hud_settings=None, trigger_settings=None):
    """Use the verified 1080p sample preset, not a falsely enlarged manual box."""
    width, height = size
    if feedback_settings is not None:
        from src.feedback_settings import validate_settings
        feedback_settings = validate_settings(feedback_settings)
    if trigger_settings is not None:
        from src.feedback_settings import validate_trigger_settings
        trigger_settings = validate_trigger_settings(trigger_settings)
    if mode not in {"mock", "dglab"} or monitor < 1:
        raise ValueError("Invalid display or connection mode")
    hud_settings = validate_display(size, hud_settings)
    cp = configparser.ConfigParser()
    with (ROOT / "config.example.ini").open(encoding="utf-8-sig") as stream:
        cp.read_file(stream)
    if hud_settings is not None and hud_settings["mode"] == "manual":
        from src.hud_profiles import get_profile
        profile = get_profile(hud_settings["scale_percent"])
        cp["ROI"] = {key: str(value) for key, value in profile["roi"].items()}
        cp["hud"] = {key: str(value) for key, value in profile["hud"].items()}
    else:
        for key, value in list(cp["ROI"].items()):
            scale = width / 1920 if ("x" in key or "left" in key or "right" in key) else height / 1080
            cp["ROI"][key] = str(round(int(value) * scale))
    for key, value in list(cp["filters"].items()):
        if key.startswith("edge_region_"):
            x1, y1, x2, y2, weight = value.split(",")
            cp["filters"][key] = ", ".join(map(str, (
                round(int(x1)*width/1920), round(int(y1)*height/1080),
                round(int(x2)*width/1920), round(int(y2)*height/1080), weight.strip())))
    cp["capture"]["monitor_index"] = str(monitor)
    cp["calibration"]["calibrated"] = "1"
    cp["calibration"]["resolution"] = f"{width}x{height}"
    cp["calibration"]["source"] = "provided_1080p_screenshot_preset"
    if cp.has_section("hud"):
        cp["calibration"]["source"] = "hud_1080p_" + cp["hud"]["source"]
    cp["trigger"]["sender_mode"] = mode
    if trigger_settings is not None:
        cp["trigger"]["low_health_hp_threshold"] = str(trigger_settings["low_health_threshold_percent"] / 100)
    cp["sender"]["ws_host"] = "127.0.0.1"
    cp["sender"]["ws_port"] = str(port)
    if feedback_settings is not None:
        cp["sender"].update({key: str(value) for key, value in feedback_settings.items()})
    if qr_host is not None:
        cp["sender"]["qr_host"] = validate_advertised_ipv4(qr_host)
    destination = Path(destination)
    with destination.open("x", encoding="utf-8") as stream:
        cp.write(stream)
    return destination


def guide_text():
    node, _ = backend_paths()
    return ("当前软件使用的后端完整路径（防火墙放行此文件）：\n" + str(node) + "\n\n"
            + (ROOT / "docs/main_official_guide.md").read_text(encoding="utf-8"))


class OfficialBackend:
    """Never reuse or kill an unrelated Node process or an occupied port."""

    def __init__(self, directory, log=lambda line: None):
        self.directory = Path(directory)
        self.log = log
        self.process = None
        self.port = None
        self.ready = threading.Event()

    def start(self, cancelled=None):
        node, entry = backend_paths()
        if not node.is_file() or not entry.is_file():
            raise RuntimeError("内置后端文件缺失，请完整解压整个软件包。")
        env = dict(os.environ, PORT="0", HELLDIVER_MANAGED="1", LOG_LEVEL="warn")
        self.process = subprocess.Popen(
            [str(node), str(entry)], cwd=self.directory, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
        threading.Thread(target=self._read, daemon=True).start()
        deadline = time.monotonic() + 12
        while not self.ready.wait(.05):
            if cancelled is not None and cancelled.is_set():
                raise RuntimeError("启动已取消")
            if self.process.poll() is not None:
                raise RuntimeError("官方后端启动失败，请查看日志。")
            if time.monotonic() >= deadline:
                raise RuntimeError("官方后端启动超时")
        return self.port

    def _read(self):
        for line in self.process.stdout:
            if line.startswith("HD_READY="):
                value = line.strip().partition("=")[2]
                if value.isdecimal() and 0 < int(value) < 65536:
                    self.port = int(value)
                    self.ready.set()
            else:
                self.log(line.rstrip())

    def close(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.stdin.write("STOP\n")
                process.stdin.flush()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass  # The owned backend may already have closed its pipe.


def worker_command(config, session):
    args = ["--worker", str(config), "--session", str(session)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(ROOT / "tools/desktop_app.py"), *args]


def stop_worker(process, session):
    if process is None:
        return
    (Path(session) / "STOP").touch()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
