"""Queued F8 tests use message fixtures; no keys are sent to Windows."""
import ctypes
from ctypes import wintypes
from queue import Empty, Queue
import threading
import time

import cv2
import numpy as np
import pytest

from dghub_plugin import hotkey, panel
from dghub_plugin.sdk_config import defaults
from test_dghub_sdk import client, ready


class MessageAPI:
    def __init__(self, *, accepted=True):
        self.accepted = accepted
        self.messages = Queue()
        self.read_count = 0
        self.threads = []
        self.removed = threading.Event()

    def register(self):
        self.threads.append(("register", threading.get_ident()))
        return self.accepted

    def next_message(self):
        try:
            message = self.messages.get_nowait()
        except Empty:
            return None
        self.read_count += 1
        if isinstance(message, Exception):
            raise message
        return message

    def unregister(self):
        self.threads.append(("unregister", threading.get_ident()))
        self.removed.set()

    def press(self, *, age=0, kind=hotkey.WM_HOTKEY, identifier=hotkey.HOTKEY_ID, key=hotkey.VK_F8):
        self.messages.put((kind, identifier, key, age))


def eventually(condition):
    deadline = time.monotonic() + .5
    while not condition():
        if time.monotonic() >= deadline:
            pytest.fail("Hotkey worker did not finish bounded fixture work")
        time.sleep(.002)


@pytest.fixture
def listener():
    api = MessageAPI()
    key = hotkey.RegisteredF8Hotkey(api_factory=lambda: api)
    key.start()
    assert key.ready
    yield api, key
    key.close()
    assert not key._thread.is_alive()


def test_short_press_is_queued_until_consumed(listener):
    api, key = listener
    # The OS message remains after the physical key has already been released.
    api.press()
    eventually(lambda: api.read_count == 1)
    assert key.consume()
    assert not key.consume()


def test_pending_press_queue_is_bounded_and_does_not_repeat(listener):
    api, key = listener
    for _ in range(50):
        api.press()
    eventually(lambda: api.read_count == 50)
    assert len(key._events) == 1
    assert key.consume()
    assert not key.consume()


@pytest.mark.parametrize("fields", [
    {"kind": 0x100}, {"identifier": hotkey.HOTKEY_ID + 1}, {"key": 0x76},
    {"age": .251}, {"age": -1},
])
def test_unrelated_or_stale_messages_do_not_toggle(listener, fields):
    api, key = listener
    api.press(**fields)
    eventually(lambda: api.read_count == 1)
    assert not key.consume()


def test_waiting_event_expires_without_late_start():
    api = MessageAPI()
    now = [10.]
    key = hotkey.RegisteredF8Hotkey(api_factory=lambda: api, clock=lambda: now[0])
    key.start()
    try:
        api.press()
        eventually(lambda: api.read_count == 1)
        now[0] += .251
        assert not key.consume()
    finally:
        key.close()


def test_discard_clears_pending_and_already_posted_os_events():
    api = MessageAPI()
    now = [10.]
    key = hotkey.RegisteredF8Hotkey(api_factory=lambda: api, clock=lambda: now[0])
    key.start()
    try:
        api.press()
        eventually(lambda: api.read_count == 1)
        key.discard()
        assert not key.consume()
        now[0] += .1
        api.press(age=.2)
        eventually(lambda: api.read_count == 2)
        assert not key.consume()
        api.press()
        eventually(lambda: api.read_count == 3)
        assert key.consume()
    finally:
        key.close()


def test_close_unregisters_on_owner_thread_and_cannot_restart(listener):
    api, key = listener
    api.press()
    eventually(lambda: api.read_count == 1)
    key.close()
    assert api.removed.is_set()
    assert api.threads[0][1] == api.threads[1][1] != threading.get_ident()
    assert not key.ready and not key.consume()
    key.start()
    api.press()
    assert not key.consume()
    assert len(api.threads) == 2


def test_registration_conflict_is_explicit_and_not_retried():
    api = MessageAPI(accepted=False)
    key = hotkey.RegisteredF8Hotkey(api_factory=lambda: api)
    key.start()
    key.start()
    key.close()
    assert not key.ready and not key.consume()
    assert key.diagnostic == "F8 registration failed; use local button"
    assert len(api.threads) == 1


def test_message_thread_failure_clears_queued_intent(listener):
    api, key = listener
    api.press()
    api.messages.put(RuntimeError("not exposed"))
    eventually(lambda: bool(key.diagnostic))
    assert not key.ready and not key.consume()
    assert key.diagnostic == "F8 listener failed; use local button"
    assert api.removed.wait(.5)


def test_start_timeout_latches_failure_and_late_registration_is_removed():
    gate = threading.Event()
    api = MessageAPI()
    key = hotkey.RegisteredF8Hotkey(api_factory=lambda: (gate.wait(.5), api)[1],
                                   startup_timeout=.01)
    key.start()
    assert key.diagnostic == "F8 startup timed out; use local button"
    gate.set()
    key.close()
    assert not key.ready and not key.consume()
    assert api.removed.is_set() and not key._thread.is_alive()


def test_thread_start_failure_is_visible_and_close_does_not_join_unstarted_thread(monkeypatch):
    def fail_start(thread):
        raise RuntimeError("not exposed")

    monkeypatch.setattr(hotkey.threading.Thread, "start", fail_start)
    key = hotkey.RegisteredF8Hotkey(api_factory=MessageAPI)
    key.start()
    assert not key.ready and not key.consume()
    assert key.diagnostic == "F8 listener failed; use local button"
    key.close()
    key.close()
    assert key._thread.ident is None


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def test_win32_wrapper_registers_only_norepeat_f8_and_preserves_posted_age(monkeypatch):
    calls = []
    pending = [False]

    def peek(pointer, hwnd, minimum, maximum, remove):
        calls.append(("peek", hwnd, minimum, maximum, remove))
        if not pending[0]:
            return 0
        message = ctypes.cast(pointer, ctypes.POINTER(wintypes.MSG)).contents
        message.message, message.wParam = hotkey.WM_HOTKEY, hotkey.HOTKEY_ID
        message.lParam = hotkey.VK_F8 << 16
        message.time = 0xfffffff0
        pending[0] = False
        return 1

    class Library:
        RegisterHotKey = Function(lambda *args: calls.append(("register", *args)) or 1)
        UnregisterHotKey = Function(lambda *args: calls.append(("unregister", *args)) or 1)
        PeekMessageW = Function(peek)
        GetTickCount = Function(lambda: 0x10)

    monkeypatch.setattr(hotkey.sys, "platform", "win32")
    monkeypatch.setattr(hotkey.ctypes, "WinDLL", lambda *args, **kwargs: Library(), raising=False)
    api = hotkey._Win32F8API()
    assert api.register()
    assert ("register", None, hotkey.HOTKEY_ID, hotkey.MOD_NOREPEAT, hotkey.VK_F8) in calls
    pending[0] = True
    assert api.next_message() == (hotkey.WM_HOTKEY, hotkey.HOTKEY_ID, hotkey.VK_F8, .032)
    assert api.next_message() is None
    api.unregister()
    assert calls[-1] == ("unregister", None, hotkey.HOTKEY_ID)


class PanelKey:
    def __init__(self):
        self.ready, self.diagnostic = True, ""
        self.pending, self.discards, self.starts, self.closed = False, 0, 0, False

    def start(self):
        self.starts += 1

    def discard(self):
        self.pending = False
        self.discards += 1

    def consume(self):
        result, self.pending = self.pending, False
        return result

    def close(self):
        self.closed, self.pending = True, False


def setup_panel(client, tmp_path, *, mode="hidden"):
    ready(client)
    client.pause("startup_paused")
    key = PanelKey()
    ui = panel.DGHubPanel(tmp_path, hotkey=key)
    ui.configure(defaults() | {"panel_mode": mode})
    return ui, key


def stub_render(monkeypatch):
    labels = []
    monkeypatch.setattr(panel, "draw_control_panel", lambda **kwargs: np.zeros((250, 380, 3), np.uint8))
    monkeypatch.setattr(cv2, "imshow", lambda *args: None)
    monkeypatch.setattr(cv2, "waitKey", lambda *args: -1)
    monkeypatch.setattr(cv2, "destroyWindow", lambda *args: None)
    monkeypatch.setattr(cv2, "putText", lambda image, text, *args: labels.append(text) or image)
    monkeypatch.setattr(panel, "install_panel_controls", lambda *args: True)
    monkeypatch.setattr(panel, "release_panel_controls", lambda *args: None)
    monkeypatch.setattr(panel, "update_panel_layout", lambda *args: None)
    monkeypatch.setattr(panel, "set_panel_topmost", lambda *args: None)
    monkeypatch.setattr(panel, "panel_window_closed", lambda *args: False)
    return labels


def test_hidden_panel_uses_queued_short_press_and_initial_poll_keeps_it(client, tmp_path, monkeypatch):
    ui, key = setup_panel(client, tmp_path)
    monkeypatch.setattr(cv2, "imshow", lambda *args: pytest.fail("Hidden panel rendered"))
    assert key.starts == 1 and key.discards == 1
    key.pending = True
    assert ui.poll(client) == ["toggle"]
    assert ui.poll(client) == []
    assert ui.hotkey_status() == {"hotkey_ready": True, "hotkey_diagnostic": ""}


def test_state_change_discards_old_key_and_mouse_intents(client, tmp_path):
    ui, key = setup_panel(client, tmp_path)
    assert ui.poll(client) == []
    key.pending = True
    ui.controller._toggle_requested = True
    client.armed = True
    assert ui.poll(client) == []
    assert key.discards == 2


def test_queued_f8_and_mouse_click_produce_one_intent(client, tmp_path):
    ui, key = setup_panel(client, tmp_path)
    ui.poll(client)
    key.pending = ui.controller._toggle_requested = True
    assert ui.poll(client) == ["toggle"]
    assert ui.poll(client) == []


@pytest.mark.parametrize("mode", ["full", "island", "hidden"])
def test_conflict_is_visible_keeps_mouse_and_reports_failure_once(client, tmp_path, monkeypatch, mode):
    labels = stub_render(monkeypatch)
    ui, key = setup_panel(client, tmp_path, mode=mode)
    key.ready, key.diagnostic = False, "F8 registration failed; use local button"
    assert ui.poll(client) == ["hotkey_unavailable"]
    assert not ui.hidden and not ui.controller.compact
    assert "F8 unavailable; use local button" in labels
    assert "CLICK TO START" in labels
    assert not ui.hotkey_status()["hotkey_ready"]
    ui.controller._toggle_requested = True
    assert ui.poll(client) == ["toggle"]
    assert key.starts == 1
    ui.close()


def test_runtime_listener_failure_discards_start_and_returns_pause_intent(client, tmp_path, monkeypatch):
    stub_render(monkeypatch)
    ui, key = setup_panel(client, tmp_path)
    ui.poll(client)
    client.armed = True
    ui.poll(client)
    key.ready, key.diagnostic = False, "F8 listener failed; use local button"
    key.pending = ui.controller._toggle_requested = True
    assert ui.poll(client) == ["hotkey_unavailable"]
    assert ui.poll(client) == []
    ui.close()


def test_panel_close_discards_f8_and_cannot_create_late_toggle(client, tmp_path):
    ui, key = setup_panel(client, tmp_path)
    key.pending = ui.controller._toggle_requested = True
    ui.close()
    assert key.closed and not key.pending
    assert ui.poll(client) == []
    assert not ui.hotkey_status()["hotkey_ready"]
