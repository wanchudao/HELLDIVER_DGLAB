"""Daemon-owned capture and shared vision; the control loop never joins a grab.

One request in flight, one latest result. Both generation tickets are acquired
before capture. The caller can pause/exit while a native grab is still blocked.
"""
from dataclasses import dataclass
import queue
import threading
import time


@dataclass(frozen=True)
class FrameRequest:
    generation: int
    vision_ticket: int
    vision: object


@dataclass(frozen=True)
class FrameReply:
    generation: int
    captured_at: float
    processed: object = None
    error: str = ""


class CaptureWorker:
    def __init__(self, monitor_index, *, factory=None, clock=None):
        if factory is None:
            from src.screen_capture import ScreenCapture
            factory = ScreenCapture
        self.monitor_index = monitor_index
        self.factory = factory
        self.clock = clock or time.monotonic
        self.monitor_size = (0, 0)
        self.ready = threading.Event()
        self.busy = threading.Event()
        self.error = ""
        self._requests = queue.Queue(maxsize=1)
        self._results = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="DGHubCaptureVision", daemon=True)
        self._thread.start()

    def request(self, session):
        ticket = session.frame_ticket()
        if (ticket is None or not self.ready.is_set() or self.error or self.busy.is_set()
                or self._stop.is_set()):
            return False
        vision_ticket = session.vision.frame_ticket()
        if vision_ticket is None:
            return False
        self.busy.set()
        try:
            self._requests.put_nowait(FrameRequest(ticket, vision_ticket, session.vision))
            return True
        except queue.Full:
            self.busy.clear()
            return False

    def take(self):
        try:
            reply = self._results.get_nowait()
        except queue.Empty:
            return None
        self.busy.clear()
        return reply

    def _publish(self, reply):
        if self._stop.is_set():
            self.busy.clear()
            return
        try:
            self._results.get_nowait()
        except queue.Empty:
            pass
        self._results.put_nowait(reply)

    def _run(self):
        capture = None
        try:
            capture = self.factory(monitor_index=self.monitor_index)
            if not capture.is_ready:
                raise RuntimeError()
            self.monitor_size = capture.monitor_size
            from src.hud_profiles import SUPPORTED_RESOLUTIONS
            if self.monitor_size not in SUPPORTED_RESOLUTIONS:
                self.error = "capture_size_mismatch"
                return
            self.ready.set()
            while not self._stop.is_set():
                try:
                    request = self._requests.get(timeout=.05)
                except queue.Empty:
                    continue
                captured_at = self.clock()
                try:
                    frame = capture.grab()
                    if self._stop.is_set():
                        break
                    gap = float(request.vision.config.get("trigger", {}).get("max_frame_gap_sec", 1.0))
                    if not 0 <= self.clock() - captured_at < gap:
                        reply = FrameReply(request.generation, captured_at, error="stale_capture")
                    elif frame is None:
                        reply = FrameReply(request.generation, captured_at, error="capture_failed")
                    elif frame.shape[:2] != (self.monitor_size[1], self.monitor_size[0]):
                        reply = FrameReply(request.generation, captured_at, error="capture_size_mismatch")
                    else:
                        processed = request.vision.process_frame(frame, request.vision_ticket,
                                                                 timestamp=captured_at)
                        reply = FrameReply(request.generation, captured_at, processed=processed)
                except Exception:
                    reply = FrameReply(request.generation, captured_at, error="vision_or_capture_failed")
                self._publish(reply)
        except Exception:
            self.error = "capture_initialization_failed"
        finally:
            self.ready.set()
            self.busy.clear()
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    pass

    def close(self):
        self._stop.set()
        # No executor shutdown or unbounded join can delay transport zeros.
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=.1)
