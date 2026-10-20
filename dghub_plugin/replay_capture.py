"""Explicit software acceptance input. Never inferred from SDK configuration.

This factory exists to test the final frozen EXE without capturing the desktop
or involving the installed host. Ordinary plugin startup never selects it.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


class ReplayCapture:
    def __init__(self, directory, *, monitor_index=1):
        root = Path(directory).resolve()
        manifest = root / "replay.json"
        if manifest.stat().st_size > 65536:
            raise ValueError("Oversized software replay manifest")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != {"synthetic", "frames"}
                or data["synthetic"] is not True or not isinstance(data["frames"], list)
                or not 1 <= len(data["frames"]) <= 600):
            raise ValueError("Expected explicitly synthetic software replay")
        self.paths = []
        for name in data["frames"]:
            if not isinstance(name, str) or not name or Path(name).is_absolute():
                raise ValueError("Invalid software replay path")
            path = (root / name).resolve()
            if not path.is_relative_to(root) or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                raise ValueError("Software replay file must remain inside fixture directory")
            if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Invalid software replay image")
            self.paths.append(path)
        self.monitor_index = monitor_index
        from src.hud_profiles import validate_resolution
        with Image.open(self.paths[0]) as header:
            self.monitor_size = validate_resolution(header.size)
        self.is_ready = True
        self.index = 0
        self.closed = False

    def grab(self):
        if self.closed:
            return None
        path = self.paths[min(self.index, len(self.paths) - 1)]
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Software replay image changed size")
        with Image.open(path) as header:
            if header.size != self.monitor_size:
                raise ValueError("Software replay frame size changed within the session")
        frame = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.shape != (self.monitor_size[1], self.monitor_size[0], 3):
            raise ValueError("Invalid software replay frame")
        self.index += 1
        return frame

    def close(self):
        self.closed = True
        self.is_ready = False


class ReplayControls:
    """Explicit acceptance-only local F8/click intentions, never SDK commands.

    The normal launcher cannot select this UI. Each new sequence is consumed
    exactly once, so pause/device changes cannot replay an old start request.
    """
    def __init__(self, directory):
        self.path = Path(directory).resolve() / "controls.json"
        self.sequence = 0
        self.settings = None
        self.closed = False

    def configure(self, settings):
        self.settings = dict(settings)

    def poll(self, session):
        if self.closed or self.settings is None:
            return []
        try:
            if self.path.stat().st_size > 4096:
                raise ValueError("Oversized replay-only local control")
            raw = self.path.read_bytes()
            if len(raw) > 4096:
                raise ValueError("Oversized replay-only local control")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return []  # A fixture writer may be between its two small writes.
        if (not isinstance(data, dict) or not {"sequence", "running"}.issubset(data)
                or set(data) - {"sequence", "running", "source"}
                or type(data["sequence"]) is not int or data["sequence"] < 1
                or type(data["running"]) is not bool or data.get("source", "button") not in ("button", "hotkey")):
            raise ValueError("Invalid replay-only local control")
        if data["sequence"] <= self.sequence:
            return []
        self.sequence = data["sequence"]
        if data.get("source") == "hotkey":
            return ["hotkey_toggle"]
        return ["toggle"] if data["running"] != session.armed else []

    def close(self):
        self.closed = True
