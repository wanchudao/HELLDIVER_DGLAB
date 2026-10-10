"""Shared, offline-only plugin core. No DGHub registration or device transport.

This core never accesses a host or device. The SDK adapter uses its snapshots
to schedule short strength leases and independent waveform cycles. Frame
tickets make pause/restart invalidate work already in flight.
"""
from copy import deepcopy
from pathlib import Path
from threading import RLock
import logging
import sys
import time


CORE_ROOT = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
             else Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(CORE_ROOT))
logger = logging.getLogger(__name__)


def assert_shared_core():
    """Fail closed if an already-loaded legacy ``src`` would shadow the root.

    Merely prepending sys.path cannot replace an entry in sys.modules. Never
    delete/reload those entries in a live process: mixed class identities could
    leave callbacks attached to the obsolete implementation.
    """
    expected = (CORE_ROOT / "src").resolve()
    for name, module in tuple(sys.modules.items()):
        if name != "src" and not name.startswith("src."):
            continue
        origin = getattr(module, "__file__", None)
        if origin and not Path(origin).resolve().is_relative_to(expected):
            raise RuntimeError(f"Refusing non-shared core module: {name}")
        if name == "src":
            locations = getattr(module, "__path__", ())
            if not any(Path(path).resolve() == expected for path in locations):
                raise RuntimeError("Refusing legacy src package; start a fresh process")


assert_shared_core()
from src.config_loader import load_config
from src.trigger import TriggerDispatch
from src.vision_pipeline import VisionPipeline
assert_shared_core()


HOST_BLOCK_REASON = (
    "DGHub hardware output is disabled: the installed-host protocol has no "
    "verified lease cancellation/zero-baseline lifecycle. Registration is "
    "blocked as well. Use --offline for local Mock testing."
)


def mock_config(config):
    """Never trust an old config's dghub/dglab mode to enable output."""
    result = deepcopy(config)
    result.setdefault("trigger", {})["sender_mode"] = "mock"
    return result


class MockVisionSession:
    """One root VisionPipeline + TriggerDispatch + FeedbackMixer session.

    Detection runs outside the lock so a slow frame cannot delay pause. The
    caller serializes process_frame calls; pause is safe from another thread.
    A generation change resets the pipeline on its owner thread before reuse.
    """

    def __init__(self, config, *, clock=None, log_writer=None):
        assert_shared_core()
        self.config = mock_config(config)
        self.clock = clock or time.monotonic
        self.pipeline = VisionPipeline(self.config)
        self.trigger = TriggerDispatch(self.config, clock=self.clock, log_writer=log_writer)
        self._lock = RLock()
        self._generation = 0
        self._pipeline_generation = -1
        self._armed = False
        self._closed = False
        self.reason = "startup_paused"

    def set_running(self, running, reason="user_pause"):
        with self._lock:
            if self._closed:
                return False
            if bool(running) != self._armed:
                self._generation += 1
                self.trigger.reset("resume" if running else reason)
            self._armed = bool(running)
            self.reason = "running_mock" if running else reason
            return self._armed

    def frame_ticket(self):
        """Take immediately BEFORE capture; pause invalidates even late grabs."""
        with self._lock:
            return self._generation if self._armed and not self._closed else None

    def _current(self, ticket):
        return (ticket is not None and ticket == self._generation
                and self._armed and not self._closed)

    def invalidate(self, reason, ticket=None):
        with self._lock:
            if self._closed or (ticket is not None and not self._current(ticket)):
                return
            self._generation += 1
            self._armed = False
            self.reason = reason
            self.trigger.reset(reason)

    def process_frame(self, frame, ticket, timestamp=None):
        with self._lock:
            if not self._current(ticket):
                return None
        if frame is None:
            self.invalidate("capture_failed", ticket)
            return None
        try:
            if self._pipeline_generation != ticket:
                self.pipeline.reset()
                self._pipeline_generation = ticket
            result, roi, health = self.pipeline.process(frame, timestamp=timestamp)
            with self._lock:
                if not self._current(ticket):
                    return None
                # Initial debounce is unknown but must be allowed to acquire a
                # baseline. Invalid frames clear feedback and HP history.
                action = self.trigger.dispatch(result)
                return result, roi, health, action
        except Exception:
            # Reset and dispatch can fail too. Clear feedback before reporting
            # the fault, but never let an old frame pause a newer generation.
            self.invalidate("vision_error", ticket)
            logger.exception("Mock frame failed; invalidated its session generation")
            raise

    def mock_output(self):
        with self._lock:
            return self.trigger.mock_output()

    def waveform_remaining(self, event):
        """Winning feedback's lifetime, not the renewable capture lease.

        Kept behind the same lock as output so protocol scheduling never edits
        the shared event engine or confuses its one-second frame watchdog with
        the duration of a complete waveform.
        """
        with self._lock:
            now = self.trigger._clock()
            if event == "low_health":
                return float("inf")
            if event == "healing_sustain":
                return max(0.0, self.trigger._healing_until - now)
            pulse = self.trigger._mock_sender.mixer.pulses.get(event)
            return max(0.0, pulse[1] - now) if pulse else 0.0

    def close(self, reason="closed"):
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            self._armed = False
            self._closed = True
            self.reason = reason
            self.trigger.safe_stop()
