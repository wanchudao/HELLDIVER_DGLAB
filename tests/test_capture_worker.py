"""Buffered capture tests use event-controlled fakes, never mss or real screens."""
import queue
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src.screen_capture import BufferedScreenCapture, ScreenCapture


class ControlledBackend:
    """Each grab blocks until the test supplies a frame, None, or an exception."""

    def __init__(self, ready=True):
        self.is_ready = ready
        self.monitor_size = (1920, 1080)
        self.commands = queue.Queue()
        self.entered = queue.Queue()
        self.closed = threading.Event()
        self.calls = []
        self.monitor_index = None

    def factory(self, monitor_index):
        self.monitor_index = monitor_index
        self.calls.append(("create", threading.get_ident()))
        return self

    def grab(self):
        self.calls.append(("grab", threading.get_ident()))
        self.entered.put(True)
        value = self.commands.get(timeout=5)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.calls.append(("close", threading.get_ident()))
        self.closed.set()


@pytest.fixture
def capture_factory():
    resources = []

    def create(**kwargs):
        backend = ControlledBackend()
        capture = BufferedScreenCapture(
            monitor_index=2, capture_factory=backend.factory, target_fps=0, **kwargs,
        )
        resources.append((capture, backend))
        assert capture.is_ready
        assert backend.entered.get(timeout=1)
        return capture, backend

    yield create
    for capture, backend in resources:
        capture.close()
        backend.commands.put(None)
        capture._thread.join(timeout=1)
        assert not capture._thread.is_alive()


def test_mss_lifecycle_stays_on_one_worker_thread(capture_factory):
    capture, backend = capture_factory()
    frame = np.full((2, 3, 3), 37, dtype=np.uint8)
    backend.commands.put(frame)
    # Entering the next grab proves the previous frame has been published.
    assert backend.entered.get(timeout=1)
    assert capture.grab(timeout=0) is frame
    assert capture.monitor_size == (1920, 1080)
    assert backend.monitor_index == 2
    backend.commands.put(None)
    capture.close()
    assert backend.closed.wait(1)
    owners = {owner for _, owner in backend.calls}
    assert len(owners) == 1
    assert threading.get_ident() not in owners
    assert backend.calls[0][0] == "create"
    assert backend.calls[-1][0] == "close"


def test_blocked_native_grab_cannot_block_main_grab_or_close(capture_factory):
    capture, backend = capture_factory()
    started = time.monotonic()
    assert capture.grab(timeout=60) is None
    assert time.monotonic() - started < 0.25
    started = time.monotonic()
    capture.close()
    assert time.monotonic() - started < 0.5
    assert not capture.is_ready
    assert capture._thread.daemon and capture._thread.is_alive()
    assert not backend.closed.is_set()  # main thread must not close worker-owned mss
    assert capture.grab(timeout=0) is None
    backend.commands.put(np.zeros((1, 1, 3), dtype=np.uint8))
    assert backend.closed.wait(1)
    assert capture._frames.empty()  # a late result cannot escape shutdown


def test_queue_keeps_only_latest_and_each_frame_is_consumed_once(capture_factory):
    capture, backend = capture_factory()
    older = np.zeros((1, 1, 3), dtype=np.uint8)
    newest = np.ones((1, 1, 3), dtype=np.uint8)
    backend.commands.put(older)
    assert backend.entered.get(timeout=1)
    backend.commands.put(newest)
    assert backend.entered.get(timeout=1)
    assert capture._frames.maxsize == 1
    assert capture._frames.qsize() == 1
    assert capture.grab(timeout=0) is newest
    assert capture.grab(timeout=0) is None


def test_queued_frame_older_than_one_second_is_rejected(capture_factory):
    clock = [100.0]
    capture, backend = capture_factory(clock=lambda: clock[0], max_frame_age=60)
    backend.commands.put(np.zeros((1, 1, 3), dtype=np.uint8))
    assert backend.entered.get(timeout=1)
    clock[0] += 1.01
    assert capture.grab(timeout=0) is None
    assert capture._frames.empty()


def test_stricter_configured_frame_age_is_respected(capture_factory):
    clock = [100.0]
    capture, backend = capture_factory(clock=lambda: clock[0], max_frame_age=0.1)
    backend.commands.put(np.zeros((1, 1, 3), dtype=np.uint8))
    assert backend.entered.get(timeout=1)
    clock[0] += 0.11
    assert capture.grab(timeout=0) is None


def test_frame_at_exact_age_limit_is_still_accepted(capture_factory):
    clock = [100.0]
    capture, backend = capture_factory(clock=lambda: clock[0])
    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    backend.commands.put(frame)
    assert backend.entered.get(timeout=1)
    clock[0] = 101.0
    assert capture.grab(timeout=0) is frame


def test_slow_grab_does_not_retimestamp_old_frame_as_fresh(capture_factory):
    clock = [100.0]
    capture, backend = capture_factory(clock=lambda: clock[0])
    # The grab started at 100.0 and only returns after its freshness limit.
    clock[0] = 102.0
    backend.commands.put(np.zeros((1, 1, 3), dtype=np.uint8))
    assert backend.entered.get(timeout=1)
    assert capture.grab(timeout=0) is None
    assert capture._frames.empty()


def test_worker_exception_closes_backend_on_worker_thread(capture_factory):
    capture, backend = capture_factory()
    backend.commands.put(RuntimeError("simulated native grab error"))
    assert backend.closed.wait(1)
    assert not capture.is_ready
    assert capture.grab(timeout=0) is None
    assert len({owner for _, owner in backend.calls}) == 1


def test_unready_backend_is_released_after_initialization_failure():
    backend = ControlledBackend(ready=False)
    capture = BufferedScreenCapture(capture_factory=backend.factory)
    assert not capture.is_ready
    assert backend.closed.wait(1)
    capture.close()
    assert [name for name, _ in backend.calls] == ["create", "close"]
    assert len({owner for _, owner in backend.calls}) == 1


def test_monitor_metadata_exception_releases_initialized_backend():
    closed = threading.Event()
    owners = []

    class InvalidMonitorBackend:
        is_ready = True

        def __init__(self, monitor_index):
            owners.append(threading.get_ident())

        @property
        def monitor_size(self):
            raise RuntimeError("invalid monitor metadata")

        def close(self):
            owners.append(threading.get_ident())
            closed.set()

    capture = BufferedScreenCapture(capture_factory=InvalidMonitorBackend)
    assert not capture.is_ready
    assert closed.wait(1)
    capture.close()
    assert len(owners) == 2 and len(set(owners)) == 1
    assert owners[0] != threading.get_ident()


def test_startup_wait_is_capped_at_two_seconds_and_late_init_is_closed():
    backend = ControlledBackend()
    release_init = threading.Event()

    def delayed_factory(monitor_index):
        release_init.wait(5)
        return backend.factory(monitor_index)

    capture = None
    try:
        started = time.monotonic()
        capture = BufferedScreenCapture(capture_factory=delayed_factory, startup_timeout=60)
        elapsed = time.monotonic() - started
        assert elapsed < 2.5
        assert not capture.is_ready
        assert capture.grab(timeout=0) is None
    finally:
        release_init.set()
        assert backend.closed.wait(1)
        if capture is not None:
            capture.close()
    assert [name for name, _ in backend.calls] == ["create", "close"]
    assert len({owner for _, owner in backend.calls}) == 1


def test_sync_capture_releases_mss_when_monitor_initialization_fails(monkeypatch):
    owners = []
    fake_mss = SimpleNamespace(
        monitors=[{"width": 1920, "height": 1080}],
        close=lambda: owners.append(threading.get_ident()),
    )
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=lambda: fake_mss))
    capture = ScreenCapture(monitor_index=99)
    assert not capture.is_ready
    assert owners == [threading.get_ident()]
    capture.close()
    assert len(owners) == 1


@pytest.mark.parametrize("kwargs", [
    {"target_fps": -1}, {"target_fps": float("inf")},
    {"max_frame_age": 0}, {"max_frame_age": float("nan")},
    {"startup_timeout": -1}, {"startup_timeout": float("inf")},
])
def test_invalid_capture_parameters_fail_before_starting_thread(kwargs):
    def forbidden_factory(**kwargs):
        pytest.fail("invalid arguments must not create a capture backend")

    with pytest.raises(ValueError):
        BufferedScreenCapture(capture_factory=forbidden_factory, **kwargs)
