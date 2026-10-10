"""Replace legacy DXGI/watchdog tests with shared capture/session regressions."""
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def load_core():
    previous = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "plugin_freeze_core", ROOT / "dghub_plugin/offline_core.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = previous


def test_blocked_capture_close_is_bounded_and_discards_late_frame():
    load_core()
    from src.screen_capture import BufferedScreenCapture
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    owners = []

    class Backend:
        is_ready = True
        monitor_size = (1920, 1080)

        def __init__(self, monitor_index):
            owners.append(threading.get_ident())

        def grab(self):
            owners.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release fake capture")
            return object()

        def close(self):
            owners.append(threading.get_ident())
            closed.set()

    capture = BufferedScreenCapture(capture_factory=Backend)
    try:
        assert entered.wait(1)
        started = time.monotonic()
        assert capture.grab(timeout=60) is None
        capture.close()
        assert time.monotonic() - started < .75
        assert not capture.is_ready
        assert capture.grab(timeout=0) is None
        release.set()
        assert closed.wait(1)
        assert capture._frames.empty()
        assert len(set(owners)) == 1 and owners[0] != threading.get_ident()
    finally:
        release.set()
        capture.close()


def test_pause_and_resume_rejects_capture_ticket_from_previous_generation():
    core = load_core()
    log = SimpleNamespace(append=lambda entry: None, close=lambda: None)
    session = core.MockVisionSession(core.load_config(ROOT / "config.example.ini"), log_writer=log)
    try:
        assert session.frame_ticket() is None
        session.set_running(True)
        old_ticket = session.frame_ticket()
        session.set_running(False)
        session.set_running(True)
        assert session.frame_ticket() != old_ticket
        # This object would raise if the obsolete frame entered the real pipeline.
        assert session.process_frame(object(), old_ticket) is None
        assert session.mock_output()["strength"] == 0
    finally:
        session.close()


def test_shared_trigger_log_flushes_without_dispatch_thread_file_io(tmp_path):
    load_core()
    from src.trigger import _AsyncJsonlWriter
    log_path = tmp_path / "trigger_log.jsonl"
    writer = _AsyncJsonlWriter(log_path)
    writer.append({"frame": 1, "state": "full_health"})
    writer.close()
    assert '"frame": 1' in log_path.read_text(encoding="utf-8")
