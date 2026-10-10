from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from dghub_plugin import panel
from dghub_plugin.sdk_config import defaults
from test_dghub_sdk import client, ready


@pytest.mark.parametrize("rect,kind", [(panel.CHECK_BUTTON, "check"), (panel.GUIDE_BUTTON, "guide")])
def test_popup_clicks_do_not_toggle_arm_or_layout(rect, kind):
    controller = panel.DGHubPanelController()
    x, y = rect[0] + 5, rect[1] + 5
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, x, y)
    controller.on_mouse(cv2.EVENT_LBUTTONUP, x, y)
    assert controller.consume_popup() == kind
    assert not controller.consume_toggle() and not controller.consume_layout()
    assert controller.consume_popup() is None


@pytest.mark.parametrize("cancel", ["drag", "pause", "island"])
def test_popup_gesture_does_not_survive_drag_pause_or_layout(cancel):
    controller = panel.DGHubPanelController()
    x, y = panel.CHECK_BUTTON[0] + 5, panel.CHECK_BUTTON[1] + 5
    controller.on_mouse(cv2.EVENT_LBUTTONDOWN, x, y)
    if cancel == "drag":
        controller.on_mouse(cv2.EVENT_MOUSEMOVE, 0, 0)
        controller.on_mouse(cv2.EVENT_MOUSEMOVE, x, y)
    else:
        controller.cancel_pending()
        controller.compact = cancel == "island"
    controller.on_mouse(cv2.EVENT_LBUTTONUP, x, y)
    assert controller.consume_popup() is None


def test_hidden_panel_f8_works_without_allocating_window(client, tmp_path, monkeypatch):
    ready(client)
    monkeypatch.setattr(cv2, "imshow", lambda *args: pytest.fail("Hidden panel created a window"))
    key = [False]
    ui = panel.DGHubPanel(tmp_path, key_down=lambda: key[0])
    ui.configure(defaults() | {"panel_mode": "hidden"})
    assert ui.poll(client) == []
    key[0] = True
    assert ui.poll(client) == ["toggle"]
    assert ui.poll(client) == []
    key[0] = False
    assert ui.poll(client) == []
    key[0] = True
    assert ui.poll(client) == ["toggle"]


def test_popup_child_has_no_host_credentials_and_is_not_launched_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("DGHUB_TOKEN", "secret-do-not-copy")
    monkeypatch.setenv("DGHUB_PORT", "12345")
    launched = []
    child = SimpleNamespace(poll=lambda: None, terminate=lambda: launched.append("terminated"))

    def spawn(command, **kwargs):
        launched.append((command, kwargs))
        return child

    ui = panel.DGHubPanel(tmp_path, key_down=lambda: False, spawn=spawn)
    ui.open_popup("check", defaults() | {"hud_scale_percent": 125})
    ui.open_popup("check", defaults())
    assert len(launched) == 1
    command, options = launched[0]
    assert command[-3:] == ["--popup-check", "--hud-scale", "125"]
    assert not any(key.startswith("DGHUB_") for key in options["env"])
    assert options["cwd"] == tmp_path
    assert Path(command[0]).is_absolute() and Path(command[1]).is_absolute()
    ui.close()
    assert launched[-1] == "terminated"
