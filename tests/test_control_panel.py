"""Pure rendering and click-intent tests for the interactive safety panel."""
import cv2
import numpy as np
import pytest

from src.control_panel import (
    PANEL_H,
    PANEL_W,
    PanelController,
    draw_control_panel,
    point_in_toggle_button,
)


def test_redesigned_panel_renders_both_safety_states():
    safe = draw_control_panel(paused=True, output_mode="mock",
                              stop_reason="Startup safety lock")
    armed = draw_control_panel(paused=False, output_mode="hardware",
                               health_stable=0.42, state="injured", cur_fps=30)
    assert safe.shape == armed.shape == (PANEL_H, PANEL_W, 3)
    assert safe.dtype == armed.dtype == np.uint8
    assert not np.array_equal(safe, armed)


def test_only_a_completed_click_inside_large_button_requests_toggle():
    controller = PanelController()
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, 100, 110)
    assert not controller.consume_toggle()
    controller.on_mouse(cv2.EVENT_LBUTTONUP, 5, 5)
    assert not controller.consume_toggle()
    controller.on_mouse(cv2.EVENT_LBUTTONUP, 100, 110)
    assert not controller.consume_toggle()
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, 100, 110)
    controller.on_mouse(cv2.EVENT_LBUTTONUP, 100, 110)
    assert controller.consume_toggle()
    assert not controller.consume_toggle()


def test_drag_from_background_into_button_does_not_request_toggle():
    controller = PanelController()
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, 5, 5)
    controller.on_mouse(cv2.EVENT_LBUTTONUP, 100, 110)
    assert not controller.consume_toggle()


@pytest.mark.parametrize("completed", [False, True])
def test_state_change_cancels_held_gesture_and_queued_click(completed):
    controller = PanelController()
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, 100, 110)
    if completed:
        controller.on_mouse(cv2.EVENT_LBUTTONUP, 100, 110)
    controller.cancel_pending()
    controller.on_mouse(cv2.EVENT_LBUTTONUP, 100, 110)
    assert not controller.consume_toggle()
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, 100, 110)
    controller.on_mouse(cv2.EVENT_LBUTTONUP, 100, 110)
    assert controller.consume_toggle()


def test_button_hitbox_has_inclusive_edges_and_rejects_outside():
    assert point_in_toggle_button(20, 88)
    assert point_in_toggle_button(PANEL_W - 20, 145)
    assert not point_in_toggle_button(19, 88)
    assert not point_in_toggle_button(PANEL_W - 19, 145)


def _click(controller, x, y):
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, x, y)
    controller.on_mouse(cv2.EVENT_LBUTTONUP, x, y)


def test_island_switch_never_requests_output_and_expand_restores_layout():
    controller = PanelController()
    _click(controller, 70, 230)
    assert not controller.compact  # applied by runner, not inside callback
    assert controller.consume_layout() and controller.compact
    assert not controller.consume_toggle()
    assert not controller.consume_layout()
    _click(controller, 302, 28)
    assert controller.consume_layout() and not controller.compact
    assert not controller.consume_toggle()


def test_compact_toggle_uses_only_its_visible_button():
    controller = PanelController()
    controller.compact = True
    _click(controller, 100, 28)  # status text, not an arm button
    _click(controller, 100, 110)  # old full-panel coordinates
    assert not controller.consume_toggle()
    _click(controller, 247, 28)
    assert controller.consume_toggle()
    assert not controller.consume_toggle()


@pytest.mark.parametrize("event", [cv2.EVENT_MOUSEMOVE, cv2.EVENT_LBUTTONUP])
def test_hover_and_unpaired_release_do_not_expand_or_arm(event):
    controller = PanelController()
    controller.compact = True
    for point in ((247, 28), (302, 28)):
        controller.on_mouse(event, *point)
    assert not controller.consume_layout()
    assert not controller.consume_toggle()


def test_cancel_discards_layout_gesture_and_queued_output():
    controller = PanelController()
    _click(controller, 100, 110)
    _click(controller, 70, 230)
    controller.cancel_pending()
    assert not controller.consume_layout()
    assert not controller.consume_toggle()


@pytest.mark.parametrize("compact,point", [(False, (100, 110)), (True, (247, 28))])
def test_drag_out_and_back_cancels_click(compact, point):
    controller = PanelController()
    controller.compact = compact
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, *point)
    controller.on_mouse(cv2.EVENT_MOUSEMOVE, 5, 5)
    controller.on_mouse(cv2.EVENT_MOUSEMOVE, *point)
    controller.on_mouse(cv2.EVENT_LBUTTONUP, *point)
    assert not controller.consume_toggle()


def test_full_panel_invalid_health_does_not_render_stale_value():
    first = draw_control_panel(paused=False, valid=False, health_stable=.8)
    second = draw_control_panel(paused=False, valid=False, health_stable=0)
    assert np.array_equal(first, second)


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 2])
@pytest.mark.parametrize("origin", [(0, 0), (-1920, 0), (0, -1080)])
def test_layout_keeps_bounds_inside_simulated_scaled_work_area(scale, origin):
    from src.panel_window import clamp_position
    left, top = origin
    work = (left, top, left + round(1920 / scale), top + round(1040 / scale))
    for width, height in ((336, 56), (396, 289)):
        x, y = clamp_position(-9999, 9999, width, height, work)
        assert left <= x and x + width <= work[2]
        assert top <= y and y + height <= work[3]


@pytest.mark.parametrize("paused,valid", [(True, True), (False, True), (False, False)])
def test_compact_rendering_is_small_and_invalid_health_is_not_shown(paused, valid):
    from src.control_panel import ISLAND_W, ISLAND_H
    panel = draw_control_panel(compact=True, paused=paused, valid=valid, health_stable=.42)
    assert panel.shape == (ISLAND_H, ISLAND_W, 3)
    assert panel.dtype == np.uint8
    if paused or not valid:
        assert np.array_equal(panel, draw_control_panel(compact=True, paused=paused,
                                                       valid=valid, health_stable=.9))


def test_windows_topmost_never_calls_opencv_activating_property(monkeypatch):
    from src import control_panel, panel_window
    monkeypatch.setattr(control_panel.sys, "platform", "win32")
    monkeypatch.setattr(panel_window, "set_topmost", lambda title: title == "ours")
    monkeypatch.setattr(cv2, "setWindowProperty", lambda *a: pytest.fail("activating HighGUI path"))
    assert control_panel.set_panel_topmost("ours")


def test_native_message_policy_blocks_hover_and_protects_all_resizes():
    import ctypes
    from types import SimpleNamespace
    from src.panel_window import PanelWindowGuard, WindowPos, WM_MOUSEACTIVATE, WM_WINDOWPOSCHANGING
    guard = PanelWindowGuard.__new__(PanelWindowGuard)
    guard.compact = False
    guard.hwnd = 10
    guard.common = SimpleNamespace(DefSubclassProc=lambda *args: 27)
    assert guard._message(10, WM_MOUSEACTIVATE, 0, 0, 1, 0) == 3
    assert guard._message(11, WM_MOUSEACTIVATE, 0, 0, 1, 0) == 3
    position = WindowPos()
    position.flags = 0x3
    assert guard._message(10, WM_WINDOWPOSCHANGING, 0, ctypes.addressof(position), 1, 0) == 27
    assert position.flags == 0x13


@pytest.mark.parametrize("work,xy,expected", [
    ((0, 0, 1920, 1080), (9000, -50), (1584, 0)),
    ((-1920, -200, 0, 880), (-3000, 9000), (-1920, 824)),
])
def test_island_position_clamps_with_negative_monitor_coordinates(work, xy, expected):
    from src.panel_window import clamp_position
    assert clamp_position(*xy, 336, 56, work) == expected
