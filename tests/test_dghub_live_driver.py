import threading
from types import SimpleNamespace

import pytest

from dghub_plugin.capture_worker import FrameReply
from dghub_plugin.live_driver import LiveDriver
from test_dghub_sdk import client, messages, observation, ready


class WorkerFixture:
    def __init__(self, monitor_index, **kwargs):
        self.monitor_index = monitor_index
        self.monitor_size = (1920, 1080)
        self.ready = threading.Event()
        self.ready.set()
        self.error = ""
        self.reply = None
        self.requests = []
        self.closed = False

    def request(self, session):
        self.requests.append(session.frame_ticket())
        return True

    def take(self):
        reply, self.reply = self.reply, None
        return reply

    def close(self):
        self.closed = True


class UiFixture:
    def __init__(self):
        self.actions = []
        self.popups = []
        self.settings = None
        self.closed = False

    def configure(self, settings):
        self.settings = settings

    def poll(self, session):
        actions, self.actions = self.actions, []
        return actions

    def open_popup(self, kind, settings):
        self.popups.append((kind, settings["hud_scale_percent"]))

    def close(self):
        self.closed = True


@pytest.fixture
def driver(monkeypatch, client):
    monkeypatch.setattr("dghub_plugin.live_driver.CaptureWorker", WorkerFixture)
    result = LiveDriver(ui=UiFixture(), clock=client.clock)
    yield result
    result.close()


def test_live_driver_fixes_monitor_and_hud_and_does_not_start_before_config(driver, client):
    assert driver.poll(client) == [] and driver.worker is None
    ready(client)
    driver.poll(client)
    assert driver.worker.monitor_index == 1
    assert driver.ui.settings["hud_scale_percent"] == 100
    assert driver.worker.requests == [client.frame_ticket()]


def test_f8_intent_starts_capture_without_device_and_does_not_write_host_start(driver, client):
    from test_dghub_local_controls import configured_without_device
    configured_without_device(client)
    driver.poll(client)
    assert driver.worker.requests == []
    driver.ui.actions = ["toggle"]
    packets = [packet for batch in driver.poll(client) for packet in messages(batch)]
    assert client.armed and driver.worker.requests == [client.frame_ticket()]
    assert not client.device_output_enabled and not any(p["op"] == "set_config" for p in packets)
    driver.ui.actions = ["toggle"]
    driver.poll(client)
    assert not client.armed


def test_mouse_or_f8_pause_invalidates_reply_before_consumption(driver, client):
    ready(client)
    driver.poll(client)
    ticket = client.frame_ticket()
    driver.worker.reply = FrameReply(ticket, 0, processed=(observation(.5), None, None, {}))
    driver.ui.actions = ["toggle"]
    packets = [packet for batch in driver.poll(client) for packet in messages(batch)]
    assert not client.armed and client._last_hp is None
    assert {p["channel"] for p in packets if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}
    assert not any(p["op"] == "trigger" for p in packets)


@pytest.mark.parametrize("armed,expected", [(True, ["guide"]), (False, ["check", "guide"])])
def test_screenshot_is_pause_only_guide_always_opens(driver, client, armed, expected):
    ready(client)
    driver.poll(client)
    if not armed:
        client.pause()
    driver.ui.actions = ["check", "guide"]
    driver.poll(client)
    assert [kind for kind, scale in driver.ui.popups] == expected
    assert all(scale == 100 for _kind, scale in driver.ui.popups)


@pytest.mark.parametrize("error,closed", [("capture_failed", False), ("stale_capture", False),
                                       ("vision_or_capture_failed", False), ("capture_size_mismatch", True)])
def test_capture_failure_pauses_or_requires_restart(driver, client, error, closed):
    ready(client)
    driver.poll(client)
    driver.worker.reply = FrameReply(client.frame_ticket(), 0, error=error)
    driver.poll(client)
    assert not client.armed and client.closed is closed


def test_initialization_timeout_and_old_reply_cannot_resume(driver, client):
    ready(client)
    driver.poll(client)
    driver.worker.ready.clear()
    client.test_clock[0] = 2
    driver.poll(client)
    assert client.closed and not client.armed
    driver.worker.ready.set()
    assert driver.poll(client) == []


def test_panel_close_ends_session_and_driver_cleanup_is_idempotent(driver, client):
    ready(client)
    driver.poll(client)
    driver.ui.actions = ["close"]
    driver.poll(client)
    assert client.closed
    driver.close()
    driver.close()
    assert driver.worker.closed and driver.ui.closed


def test_record_status_transition_is_reported_without_repeating_every_tick(driver, client):
    ready(client)
    current = dict(state="recording", snapshot_written=False, rows_dropped=0, rows_invalid=0)
    client.log_writer = SimpleNamespace(status=lambda: dict(current))
    first = [packet for batch in driver.poll(client) for packet in messages(batch)]
    assert any(p.get("fields", {}).get("session_record") == current for p in first)
    assert not any("session_record" in p.get("fields", {})
                   for batch in driver.poll(client) for p in messages(batch))
    current.update(state="failed")
    next_packets = [packet for batch in driver.poll(client) for packet in messages(batch)]
    assert any(p.get("fields", {}).get("session_record", {}).get("state") == "failed" for p in next_packets)
    # Logging problems are visible but don't invent a capture failure/event.
    assert client.armed and not client.closed


def test_hotkey_status_is_configured_only_and_deduplicated(driver, client):
    controls = dict(hotkey_ready=True, hotkey_diagnostic="")
    driver.ui.hotkey_status = lambda: dict(controls)
    assert driver.poll(client) == []
    ready(client)
    packets = [p for batch in driver.poll(client) for p in messages(batch)]
    assert any(p.get("fields") == controls for p in packets)
    assert not any("hotkey_ready" in p.get("fields", {})
                   for batch in driver.poll(client) for p in messages(batch))
    controls.update(hotkey_ready=False, hotkey_diagnostic="F8 unavailable; use panel button")
    packets = [p for batch in driver.poll(client) for p in messages(batch)]
    assert any(p.get("fields") == controls for p in packets)


def test_hotkey_failure_pauses_and_discards_old_frame_but_allows_explicit_mouse_retry(driver, client):
    ready(client)
    driver.poll(client)
    driver.worker.reply = FrameReply(client.frame_ticket(), 0, processed=(observation(.5), None, None, {}))
    driver.ui.actions = ["hotkey_unavailable"]
    packets = [p for batch in driver.poll(client) for p in messages(batch)]
    assert not client.armed and client._last_hp is None and not client.closed
    assert client.last_pause_reason == "hotkey_unavailable"
    assert {p["channel"] for p in packets if p["op"] == "set_strength" and p["pct"] == 0} == {"a", "b"}
    assert not any(p["op"] in {"pulse", "trigger"} for p in packets)
    driver.ui.actions = ["toggle"]
    driver.poll(client)
    assert client.armed
