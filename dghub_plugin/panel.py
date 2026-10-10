"""Reuse the non-activating main-edition panel; SDK intents stay in its owner."""
from pathlib import Path
import subprocess
import sys
import time

import cv2

from dghub_plugin.hotkey import RegisteredF8Hotkey
from src.control_panel import (BACKGROUND, BLUE_DARK, MUTED, PANEL_NAME, PanelController,
                               draw_control_panel, install_panel_controls, panel_window_closed,
                               release_panel_controls, set_panel_topmost, update_panel_layout)


CHECK_BUTTON = (146, 218, 240, 242)
GUIDE_BUTTON = (246, 218, 360, 242)
PAUSE_REASONS = {
    "startup_paused": "Use this button or F8 to start",
    "output_safety_locked": "DGHub output insurance is OFF",
    "output_safety_unlocked": "Insurance ON; press F8 to start",
    "waiting_configuration": "Waiting for DGHub configuration",
    "waiting_capture": "Waiting for capture initialization",
    "frame_timeout": "No fresh frames; press F8 to retry",
    "device_changed": "Device changed; press F8 to restart",
    "paused": "Paused by F8 / local button",
}


def inside(rect, x, y):
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


class DGHubPanelController(PanelController):
    def __init__(self):
        super().__init__()
        self._popup_pressed = None
        self._popup_requested = None

    def on_mouse(self, event, x, y, flags=None, param=None):
        super().on_mouse(event, x, y, flags, param)
        rects = {} if self.compact else {"check": CHECK_BUTTON, "guide": GUIDE_BUTTON}
        if event == cv2.EVENT_LBUTTONDOWN:
            self._popup_pressed = next((key for key, rect in rects.items() if inside(rect, x, y)), None)
        elif event == cv2.EVENT_MOUSEMOVE:
            if self._popup_pressed and not inside(rects.get(self._popup_pressed, (0, 0, -1, -1)), x, y):
                self._popup_pressed = None
        elif event == cv2.EVENT_LBUTTONUP:
            if self._popup_pressed and inside(rects[self._popup_pressed], x, y):
                self._popup_requested = self._popup_pressed
            self._popup_pressed = None

    def cancel_pending(self):
        super().cancel_pending()
        self._popup_pressed = self._popup_requested = None

    def consume_popup(self):
        value, self._popup_requested = self._popup_requested, None
        return value


class DGHubPanel:
    def __init__(self, root, *, key_down=None, hotkey=None, spawn=None, clock=None):
        self.root = Path(root)
        self.controller = DGHubPanelController()
        self.clock = clock or time.monotonic
        self.spawn = spawn or subprocess.Popen
        # key_down remains an explicitly injected deterministic test path only.
        self.key_down = key_down
        self.hotkey = (hotkey or RegisteredF8Hotkey()) if key_down is None else None
        self.settings = None
        self.ready = False
        self.hidden = False
        self.hotkey_was_down = False
        self._last_topmost = -float("inf")
        self._last_armed = None
        self._popups = {}
        self._hotkey_failure_reported = False
        self._closed = False

    def configure(self, settings):
        self.settings = dict(settings)
        self.controller.compact = settings["panel_mode"] == "island"
        self.hidden = settings["panel_mode"] == "hidden"
        if self.hotkey is not None:
            self.hotkey.start()
            self.hotkey.discard()

    def hotkey_status(self):
        return {"hotkey_ready": not self._closed and (self.hotkey is None or self.hotkey.ready),
                "hotkey_diagnostic": self.hotkey.diagnostic if self.hotkey is not None else ""}

    def poll(self, session):
        actions = []
        if self.settings is None or self._closed:
            return actions
        if self.ready and panel_window_closed(PANEL_NAME):
            return ["close"]
        if self._last_armed != session.armed:
            self.controller.cancel_pending()
            if self._last_armed is not None and self.hotkey is not None:
                self.hotkey.discard()
            self._last_armed = session.armed
        hotkey_ready = self.hotkey_status()["hotkey_ready"]
        hotkey_failed = not hotkey_ready and not self._hotkey_failure_reported
        if hotkey_failed:
            self._hotkey_failure_reported = True
            self.controller.cancel_pending()
            self.hidden = self.controller.compact = False
            actions.append("hotkey_unavailable")
        down = bool(self.key_down()) if self.key_down is not None else False
        key_toggle = (down and not self.hotkey_was_down) if self.hotkey is None else self.hotkey.consume()
        mouse_toggle = self.controller.consume_toggle()
        if not hotkey_failed and (key_toggle or mouse_toggle):
            actions.append("toggle")
        self.hotkey_was_down = down
        popup = self.controller.consume_popup()
        if popup:
            actions.append(popup)
        if self.controller.consume_layout():
            update_panel_layout(self.controller, PANEL_NAME)
        if not self.hidden:
            output = session.output_snapshot()
            image = draw_control_panel(health_stable=session._last_hp or 0, valid=session._last_hp is not None,
                                       paused=not session.armed,
                                       output_mode="VISION ONLY" if session.armed and not session.device_output_enabled else "DGHUB",
                                       compact=self.controller.compact,
                                       stop_reason=PAUSE_REASONS.get(session.last_pause_reason,
                                                                     session.last_pause_reason))
            if not self.controller.compact:
                for rect, label in ((CHECK_BUTTON, "CHECK"), (GUIDE_BUTTON, "GUIDE")):
                    cv2.rectangle(image, rect[:2], rect[2:], BLUE_DARK, -1)
                    cv2.putText(image, label, (rect[0] + 8, 234), cv2.FONT_HERSHEY_SIMPLEX,
                                .37, MUTED, 1, cv2.LINE_AA)
                cv2.rectangle(image, (12, 178), (368, 212), BACKGROUND, -1)
                text = (f"HUD {self.settings['hud_scale_percent'] / 100:.2f}   "
                        f"A {output.get('A', {}).get('strength', 0)}%  "
                        f"B {output.get('B', {}).get('strength', 0)}%")
                cv2.putText(image, text, (20, 199), cv2.FONT_HERSHEY_SIMPLEX, .39, MUTED, 1, cv2.LINE_AA)
            if not hotkey_ready:
                if self.controller.compact:
                    cv2.rectangle(image, (35, 32), (130, 48), BACKGROUND, -1)
                    cv2.putText(image, "F8 UNAVAILABLE", (38, 43), cv2.FONT_HERSHEY_SIMPLEX,
                                .26, MUTED, 1, cv2.LINE_AA)
                else:
                    # Do not advertise F8 on the main button when registration failed.
                    cv2.rectangle(image, (23, 91), (357, 142), BLUE_DARK, -1)
                    label = "CLICK TO START" if not session.armed else "CLICK TO STOP"
                    cv2.putText(image, label, (75, 124), cv2.FONT_HERSHEY_SIMPLEX,
                                .56, MUTED, 2, cv2.LINE_AA)
                    cv2.rectangle(image, (15, 152), (365, 177), BACKGROUND, -1)
                    cv2.putText(image, "F8 unavailable; use local button", (20, 171),
                                cv2.FONT_HERSHEY_SIMPLEX, .37, MUTED, 1, cv2.LINE_AA)
            cv2.imshow(PANEL_NAME, image)
            if not self.ready:
                if not install_panel_controls(self.controller, PANEL_NAME):
                    raise RuntimeError("Non-activating panel installation failed")
                self.ready = True
                update_panel_layout(self.controller, PANEL_NAME)
            if self.clock() - self._last_topmost >= .5:
                set_panel_topmost(PANEL_NAME)
                self._last_topmost = self.clock()
            cv2.waitKey(1)
        return actions

    def open_popup(self, kind, settings):
        if kind not in {"check", "guide"} or settings is None:
            return
        old = self._popups.get(kind)
        if old is not None and old.poll() is None:
            return
        if getattr(sys, "frozen", False):
            command = [sys.executable]
        else:
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools/dghub_live_entry.py")]
        command += ["--popup-" + kind, "--hud-scale", str(settings["hud_scale_percent"])]
        # These helpers don't inherit SDK credentials or connect to any host.
        import os
        environment = {key: value for key, value in os.environ.items() if not key.startswith("DGHUB_")}
        self._popups[kind] = self.spawn(command, cwd=self.root, env=environment,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.controller.cancel_pending()
        if self.hotkey is not None:
            self.hotkey.close()
        if self.ready:
            release_panel_controls(PANEL_NAME)
            cv2.destroyWindow(PANEL_NAME)
            self.ready = False
        for process in self._popups.values():
            if process.poll() is None:
                process.terminate()
