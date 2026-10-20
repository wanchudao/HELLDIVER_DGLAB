"""Interactive always-on-top safety panel for the real-time runner.

The panel deliberately keeps all text in ASCII because OpenCV's built-in font
does not render Chinese reliably. Rendering and click intent are kept here;
the runner remains the only owner of the armed/paused state and device stop.
"""
from typing import Optional
import logging
import sys

import cv2
import numpy as np


PANEL_W, PANEL_H = 380, 250
PANEL_NAME = "HELLDIVER DGLAB - Safety Control"
TOGGLE_BUTTON = (20, 88, PANEL_W - 20, 145)
ISLAND_W, ISLAND_H = 336, 56
ISLAND_BUTTON = (20, 218, 135, 242)
ISLAND_TOGGLE = (222, 9, 272, 47)
ISLAND_EXPAND = (282, 9, 324, 47)

WHITE = (245, 245, 245)
MUTED = (155, 165, 175)
BACKGROUND = (22, 25, 30)
PANEL_EDGE = (70, 78, 88)
SAFE = (62, 170, 92)
SAFE_DARK = (30, 82, 48)
ARMED = (45, 55, 225)
ARMED_DARK = (28, 32, 115)
BLUE = (210, 125, 45)
BLUE_DARK = (95, 58, 25)


def _centered_text(image, text, y, scale, color, thickness=1):
    size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0]
    x = max(6, (image.shape[1] - size[0]) // 2)
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, thickness, cv2.LINE_AA)


def point_in_toggle_button(x: int, y: int) -> bool:
    """Return whether a panel-space point is inside the large safety button."""
    x1, y1, x2, y2 = TOGGLE_BUTTON
    return x1 <= x <= x2 and y1 <= y <= y2


def _inside(rect, x, y):
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


class PanelController:
    """Turns OpenCV mouse callbacks into a one-shot toggle request."""

    def __init__(self):
        self._toggle_requested = False
        self._button_pressed = False
        self.compact = False
        self._layout_pressed = False
        self._layout_requested = False

    def on_mouse(self, event, x, y, flags=None, param=None):
        toggle = ISLAND_TOGGLE if self.compact else TOGGLE_BUTTON
        layout = ISLAND_EXPAND if self.compact else ISLAND_BUTTON
        if event == cv2.EVENT_LBUTTONDOWN:
            self._button_pressed = _inside(toggle, x, y)
            self._layout_pressed = _inside(layout, x, y)
        elif event == cv2.EVENT_MOUSEMOVE:
            # Once dragged out, returning to the button must not revive a click.
            self._button_pressed &= _inside(toggle, x, y)
            self._layout_pressed &= _inside(layout, x, y)
        elif event == cv2.EVENT_LBUTTONUP:
            if self._button_pressed and _inside(toggle, x, y):
                self._toggle_requested = True
            if self._layout_pressed and _inside(layout, x, y):
                self._layout_requested = True
            self._button_pressed = False
            self._layout_pressed = False

    def cancel_pending(self):
        """Discard clicks and held gestures when their displayed action changes."""
        self._toggle_requested = False
        self._button_pressed = False
        self._layout_pressed = False
        self._layout_requested = False

    def consume_layout(self) -> bool:
        if not self._layout_requested:
            return False
        self.compact = not self.compact
        # A button release must never be replayed in the other layout.
        self.cancel_pending()
        return True

    def consume_toggle(self) -> bool:
        requested = self._toggle_requested
        self._toggle_requested = False
        return requested


def draw_control_panel(
    health_stable: float = 0.0,
    state: str = "",
    event: str = "none",
    cur_fps: float = 0.0,
    paused: bool = True,
    paused_fps: Optional[float] = None,
    output_mode: str = "MONITOR",
    stop_reason: str = "Startup safety lock",
    compact: bool = False,
    valid: bool = True,
    theme: str = "legacy",
    challenge_failed: bool = False,
) -> np.ndarray:
    """Render the complete safety control panel as a BGR image."""
    if compact:
        return draw_island(paused=paused, health=health_stable, valid=valid, theme=theme,
                           challenge_failed=challenge_failed)
    panel = np.full((PANEL_H, PANEL_W, 3), BACKGROUND, dtype=np.uint8)
    cv2.rectangle(panel, (0, 0), (PANEL_W - 1, PANEL_H - 1), PANEL_EDGE, 1)

    cv2.putText(panel, "HELLDIVER DGLAB", (16, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 1, cv2.LINE_AA)
    mode = str(output_mode).upper()[:12]
    mode_size = cv2.getTextSize(mode, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0]
    cv2.putText(panel, mode, (PANEL_W - mode_size[0] - 16, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                ARMED if mode == "HARDWARE" else MUTED, 1, cv2.LINE_AA)

    status_color = SAFE_DARK if paused else ARMED_DARK
    status_edge = SAFE if paused else ARMED
    cv2.rectangle(panel, (12, 36), (PANEL_W - 12, 76), status_color, -1)
    cv2.rectangle(panel, (12, 36), (PANEL_W - 12, 76), status_edge, 2)
    _centered_text(
        panel,
        "CHALLENGE FAILED" if challenge_failed else "SAFE - OUTPUT LOCKED" if paused else "ARMED - BATTLE MODE",
        63, 0.62, WHITE, 2,
    )

    x1, y1, x2, y2 = TOGGLE_BUTTON
    button_color = BLUE_DARK if paused else ARMED_DARK
    button_edge = BLUE if paused else ARMED
    cv2.rectangle(panel, (x1, y1), (x2, y2), button_color, -1)
    cv2.rectangle(panel, (x1, y1), (x2, y2), button_edge, 2)
    _centered_text(
        panel,
        ("RESUME PENALTY" if paused else "PAUSE PENALTY") if challenge_failed
        else "ARM FOR BATTLE  [F8]" if paused else "STOP OUTPUT NOW  [F8]",
        124, 0.56, WHITE, 2,
    )

    if paused:
        reason = str(stop_reason or "Manual safety lock")[:42]
        cv2.putText(panel, f"Reason: {reason}", (20, 171),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.37, MUTED, 1, cv2.LINE_AA)
        fps = paused_fps if paused_fps is not None else cur_fps
        cv2.putText(panel, f"Last FPS: {fps:.1f}", (20, 194),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.37, MUTED, 1, cv2.LINE_AA)
    else:
        hp = max(0.0, min(1.0, float(health_stable)))
        cv2.putText(panel, f"HP {hp:5.1%}" if valid else "HP --", (20, 171),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, WHITE, 1, cv2.LINE_AA)
        bar_x1, bar_y1, bar_x2, bar_y2 = 105, 158, 245, 173
        cv2.rectangle(panel, (bar_x1, bar_y1), (bar_x2, bar_y2), MUTED, 1)
        fill = int((bar_x2 - bar_x1 - 2) * hp)
        if valid and fill > 0:
            fill_color = SAFE if hp >= 0.75 else (45, 185, 225) if hp >= 0.25 else ARMED
            cv2.rectangle(panel, (bar_x1 + 1, bar_y1 + 1),
                          (bar_x1 + 1 + fill, bar_y2 - 1), fill_color, -1)
        visible_state = str(state or 'unknown').upper()[:14] if valid else "NO HUD"
        cv2.putText(panel, f"State: {visible_state}", (20, 198),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, WHITE, 1, cv2.LINE_AA)
        cv2.putText(panel, f"FPS: {cur_fps:.1f}", (265, 171),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, MUTED, 1, cv2.LINE_AA)

    cv2.line(panel, (12, 214), (PANEL_W - 12, 214), PANEL_EDGE, 1)
    cv2.rectangle(panel, ISLAND_BUTTON[:2], ISLAND_BUTTON[2:], BLUE_DARK, -1)
    cv2.putText(panel, "ISLAND  [-]", (28, 234),
                cv2.FONT_HERSHEY_SIMPLEX, .37, WHITE, 1, cv2.LINE_AA)
    cv2.putText(panel, "F8 RESET + PAUSE" if challenge_failed else "F8  START / STOP", (167, 234),
                cv2.FONT_HERSHEY_SIMPLEX, .35, MUTED, 1, cv2.LINE_AA)
    return _theme_panel(panel) if theme == "cutting_edge" else panel


def draw_island(*, paused, health=0.0, valid=True, theme="legacy", challenge_failed=False):
    """Compact pill; background is clipped to a rounded region on Windows."""
    panel = np.full((ISLAND_H, ISLAND_W, 3), BACKGROUND, np.uint8)
    color = MUTED if paused else SAFE if valid else (45, 185, 225)
    cv2.circle(panel, (22, 28), 5, color, -1, cv2.LINE_AA)
    title = "FAILED" if challenge_failed else "PAUSED" if paused else "LIVE" if valid else "NO HUD"
    cv2.putText(panel, title, (38, 26), cv2.FONT_HERSHEY_SIMPLEX,
                .44, WHITE, 1, cv2.LINE_AA)
    cv2.putText(panel, "F8 RESET + PAUSE" if challenge_failed else "F8 START / STOP", (38, 43), cv2.FONT_HERSHEY_SIMPLEX,
                .26, MUTED, 1, cv2.LINE_AA)
    hp = f"{max(0.0, min(1.0, float(health))):.0%}" if valid and not paused else "--"
    cv2.putText(panel, "HP " + hp, (137, 33), cv2.FONT_HERSHEY_SIMPLEX,
                .49, WHITE, 1, cv2.LINE_AA)
    cv2.rectangle(panel, ISLAND_TOGGLE[:2], ISLAND_TOGGLE[2:], BLUE_DARK if paused else ARMED_DARK, -1)
    if paused:
        cv2.fillConvexPoly(panel, np.array([[242, 19], [242, 37], [256, 28]], np.int32), WHITE)
    else:
        cv2.rectangle(panel, (240, 20), (245, 36), WHITE, -1)
        cv2.rectangle(panel, (250, 20), (255, 36), WHITE, -1)
    cv2.rectangle(panel, ISLAND_EXPAND[:2], ISLAND_EXPAND[2:], (43, 47, 55), -1)
    cv2.rectangle(panel, (293, 20), (312, 36), MUTED, 1, cv2.LINE_AA)
    cv2.line(panel, (293, 25), (312, 25), MUTED, 1, cv2.LINE_AA)
    return _theme_panel(panel) if theme == "cutting_edge" else panel


def _theme_panel(panel):
    """Static skin only; preserve hit boxes, dimensions and window policy."""
    palette = {BACKGROUND: (27, 21, 8), PANEL_EDGE: (81, 69, 40),
               BLUE: (232, 201, 65), BLUE_DARK: (53, 44, 22),
               SAFE_DARK: (53, 44, 22), SAFE: (232, 201, 65),
               WHITE: (244, 242, 234), MUTED: (162, 152, 130)}
    original = panel.copy()
    for old, new in palette.items():
        panel[np.all(original == old, axis=2)] = new
    h, w = panel.shape[:2]
    color = (71, 59, 30)
    cv2.line(panel, (3, 3), (w-4, 3), color, 1)
    cv2.line(panel, (3, h-4), (w-4, h-4), color, 1)
    for x in range(12, min(w-10, 100), 10):
        cv2.circle(panel, (x, h-4), 0, color, 1)
    return panel


def set_panel_topmost(window_title: str = PANEL_NAME, *, interactive: bool = True) -> bool:
    """Reassert topmost through OpenCV and Win32, using pointer-safe signatures."""
    if sys.platform == "win32":
        # OpenCV's Win32 topmost property omits SWP_NOACTIVATE. Never invoke
        # that path on Windows: its periodic refresh can steal the game focus.
        from src.panel_window import set_topmost
        return set_topmost(window_title)
    log = logging.getLogger(__name__)
    applied = False

    # HighGUI backends that support this property can retain it across redraws.
    try:
        topmost_property = getattr(cv2, "WND_PROP_TOPMOST", None)
        if topmost_property is not None:
            cv2.setWindowProperty(window_title, topmost_property, 1)
            applied = True
    except cv2.error:
        log.debug("OpenCV topmost property is unavailable")

    return applied


def install_panel_controls(controller: PanelController,
                           window_title: str = PANEL_NAME) -> bool:
    """Install click handling after the first frame created the OpenCV window."""
    try:
        cv2.setMouseCallback(window_title, controller.on_mouse)
        if sys.platform == "win32":
            from src.panel_window import install_guard
            if not install_guard(window_title, controller):
                raise RuntimeError("Cannot install non-activating panel")
        set_panel_topmost(window_title, interactive=True)
        return True
    except cv2.error:
        logging.getLogger(__name__).warning(
            "Clickable control panel unavailable; global F8 remains active")
        return False


def panel_window_closed(window_title: str = PANEL_NAME) -> bool:
    """Treat a user-closed safety panel as a request to safely exit."""
    if sys.platform == "win32":
        from src.panel_window import guard_closed
        if guard_closed(window_title):
            return True
    try:
        return cv2.getWindowProperty(window_title, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return False


def update_panel_layout(controller, window_title=PANEL_NAME):
    if sys.platform == "win32":
        from src.panel_window import update_layout
        live = getattr(controller, "live_small", False)
        size = ((controller.live_size, controller.live_size) if live
                else (ISLAND_W, ISLAND_H) if controller.compact else (PANEL_W, PANEL_H))
        if getattr(controller, "live_mode", False):
            update_layout(window_title, live or controller.compact, size,
                          kind="live" if live else "island" if controller.compact else "full",
                          monitor_bounds=controller.monitor_bounds)
        else:
            update_layout(window_title, controller.compact, size)


def release_panel_controls(window_title=PANEL_NAME):
    if sys.platform == "win32":
        from src.panel_window import release_guard
        release_guard(window_title)
