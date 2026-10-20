"""Only synthetic frames and fake capture; blocked grabs must not block stop."""
import threading
import time

import pytest

from dghub_plugin.capture_worker import CaptureWorker
from test_dghub_sdk import client, messages, ready
from test_hud_profiles import synthetic_hud_frame


class CaptureFixture:
    is_ready = True
    monitor_size = (1920, 1080)

    def __init__(self, *, frame=None, block=None):
        self.frame = frame if frame is not None else synthetic_hud_frame(100)
        self.block = block
        self.started = threading.Event()
        self.closed = threading.Event()
        self.owner = None

    def grab(self):
        self.owner = threading.get_ident()
        self.started.set()
        if self.block is not None:
            self.block.wait(2)
        return self.frame

    def close(self):
        assert self.owner is None or threading.get_ident() == self.owner
        self.closed.set()


def take_reply(worker):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        reply = worker.take()
        if reply is not None:
            return reply
        time.sleep(.001)
    pytest.fail("Fake capture worker did not deliver within deadline")


def test_worker_uses_selected_hud_and_original_capture_timestamp(client):
    ready(client)
    fixture = CaptureFixture()
    worker = CaptureWorker(1, factory=lambda **kw: fixture, clock=client.clock)
    try:
        assert worker.ready.wait(1) and not worker.error
        for i in range(4):
            client.test_clock[0] = i / 30
            assert worker.request(client)
            reply = take_reply(worker)
            assert not reply.error
            assert reply.captured_at == i / 30
            client.accept_processed_frame(reply.processed, reply.generation, reply.captured_at)
        assert client._last_hp == 1 and fixture.owner != threading.get_ident()
    finally:
        worker.close()
    assert fixture.closed.wait(.2)


def test_pause_while_grab_blocked_invalidates_reply_without_waiting(client):
    ready(client)
    release = threading.Event()
    fixture = CaptureFixture(frame=synthetic_hud_frame(100, .5), block=release)
    worker = CaptureWorker(1, factory=lambda **kw: fixture, clock=client.clock)
    try:
        assert worker.ready.wait(1) and worker.request(client)
        assert fixture.started.wait(1)
        assert not worker.request(client)  # bounded, no second job
        before = time.monotonic()
        zeros = messages(client.pause())
        assert time.monotonic() - before < .1
        assert {p["channel"] for p in zeros if p["op"] == "set_strength"} == {"a", "b"}
        client.set_running(True)
        new_ticket = client.frame_ticket()
        release.set()
        reply = take_reply(worker)
        assert not client.accept_processed_frame(reply.processed, reply.generation, reply.captured_at).messages
        assert client.frame_ticket() == new_ticket and client._last_hp is None
    finally:
        release.set()
        worker.close()


def test_close_does_not_wait_for_blocked_native_grab(client):
    ready(client)
    release = threading.Event()
    fixture = CaptureFixture(block=release)
    worker = CaptureWorker(1, factory=lambda **kw: fixture, clock=client.clock)
    assert worker.ready.wait(1) and worker.request(client) and fixture.started.wait(1)
    before = time.monotonic()
    worker.close()
    assert time.monotonic() - before < .3
    assert not worker.request(client)
    release.set()
    assert fixture.closed.wait(1)
    assert worker.take() is None


@pytest.mark.parametrize("size", [(1280, 720), (1919, 1079), (3440, 1440)])
def test_unsupported_capture_size_is_rejected_before_any_job(client, size):
    ready(client)
    fixture = CaptureFixture()
    fixture.monitor_size = size
    worker = CaptureWorker(1, factory=lambda **kw: fixture)
    try:
        assert worker.ready.wait(1) and worker.error == "capture_size_mismatch"
        assert not worker.request(client) and not fixture.started.is_set()
    finally:
        worker.close()


def test_slow_grab_is_rejected_before_vision_dispatch(client):
    ready(client)
    fixture = CaptureFixture()

    def slow_grab():
        client.test_clock[0] = 2
        return fixture.frame

    fixture.grab = slow_grab
    worker = CaptureWorker(1, factory=lambda **kw: fixture, clock=client.clock)
    try:
        assert worker.ready.wait(1) and worker.request(client)
        reply = take_reply(worker)
        assert reply.error == "stale_capture" and reply.processed is None
        client.accept_processed_frame(reply.processed, reply.generation, reply.captured_at)
        assert not client.armed and client._last_hp is None
    finally:
        worker.close()


def test_processed_reply_ages_out_without_refreshing_frame_watchdog(client):
    ready(client)
    processed = client.vision.process_frame(synthetic_hud_frame(100), client.vision.frame_ticket())
    ticket = client.frame_ticket()
    client.test_clock[0] = 1
    batch = client.accept_processed_frame(processed, ticket, 0)
    assert not client.armed and client._last_frame_at is None
    assert {p["channel"] for p in messages(batch) if p["op"] == "set_strength"} == {"a", "b"}
