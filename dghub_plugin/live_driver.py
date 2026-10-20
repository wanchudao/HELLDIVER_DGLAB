"""Nonblocking coordination of the SDK owner, capture worker and panel.

The panel/capture factories are injectable. This file never launches a host,
connects a device, changes the firewall or loads the obsolete plugin core.
"""
import time

from dghub_plugin.capture_worker import CaptureWorker


class LiveDriver:
    def __init__(self, *, capture_factory=None, ui=None, clock=None):
        self.capture_factory = capture_factory
        self.ui = ui
        self.clock = clock or time.monotonic
        self.worker = None
        self.closed = False
        self._created_at = None
        self._last_request_at = -float("inf")
        self._last_capture_status = None
        self._last_record_status = None
        self._last_hotkey_status = None

    def poll(self, session):
        batches = []
        if self.closed or session.closed:
            return batches
        now = self.clock()
        record_status = getattr(session.log_writer, "status", None)
        if record_status is not None:
            record = record_status()
            signature = (record["state"], record["snapshot_written"],
                         bool(record["rows_dropped"]), bool(record["rows_invalid"]))
            if signature != self._last_record_status:
                self._last_record_status = signature
                batches.append(session._batch([{"op": "status", "fields": {"session_record": record}}]))
        if session.settings is not None and self.worker is None:
            self.worker = CaptureWorker(session.settings["monitor_index"],
                                        factory=self.capture_factory, clock=self.clock)
            self._created_at = now
            if self.ui is not None:
                self.ui.configure(session.settings)
        if self.worker is not None:
            if self.worker.error:
                session.last_error = "Capture initialization failed or unsupported resolution"
                return [session.close(self.worker.error)]
            if not self.worker.ready.is_set() and now - self._created_at >= 2:
                session.last_error = "Capture initialization timed out"
                return [session.close("capture_initialization_timeout")]
            capture_status = "ok" if self.worker.ready.is_set() else "pending"
            if capture_status == "ok" and session.capture_resolution is None:
                batches.append(session.bind_capture_geometry(self.worker.monitor_size))
                if session.closed:
                    return batches
            if capture_status != self._last_capture_status:
                self._last_capture_status = capture_status
                batches.append(session._batch([{"op": "status", "fields": {
                    "capture_state": capture_status, "monitor_index": session.settings["monitor_index"],
                    "capture_resolution": f"{self.worker.monitor_size[0]}x{self.worker.monitor_size[1]}"}}]))
        if self.ui is not None:
            for action in self.ui.poll(session):
                if action == "close":
                    return [session.close("panel_closed")]
                if action in ("toggle", "hotkey_toggle"):
                    if self.worker is None or not self.worker.ready.is_set():
                        batches.append(session.pause("waiting_capture"))
                    else:
                        batches.append(session.local_toggle("hotkey" if action == "hotkey_toggle" else "button"))
                elif action == "hotkey_unavailable":
                    # Local shortcut failure must not leave an armed session
                    # running. The visible button remains an explicit fallback.
                    batches.append(session.pause("hotkey_unavailable"))
                elif action == "guide":
                    self.ui.open_popup("guide", session.settings)
                elif action == "check":
                    if not session.armed:
                        self.ui.open_popup("check", session.settings)
                    else:
                        batches.append(session._batch([{"op": "status", "fields": {
                            "settings_note": "截图检查仅在暂停状态下打开；按 F8 暂停后重试"}}]))
            hotkey_status = getattr(self.ui, "hotkey_status", None)
            if hotkey_status is not None and session.settings is not None:
                controls = hotkey_status()
                if controls != self._last_hotkey_status:
                    self._last_hotkey_status = dict(controls)
                    batches.append(session._batch([{"op": "status", "fields": controls}]))
        if self.worker is not None and self.worker.ready.is_set():
            reply = self.worker.take()
            if reply is not None and session.frame_ticket() == reply.generation:
                if reply.error:
                    if reply.error == "capture_size_mismatch":
                        session.last_error = "Capture resolution changed; restart required"
                    stop = session.close if reply.error == "capture_size_mismatch" else session.pause
                    batches.append(stop(reply.error))
                else:
                    batches.append(session.accept_processed_frame(reply.processed, reply.generation,
                                                                  reply.captured_at))
            if not session.closed and session.armed and now - self._last_request_at >= 1 / 30:
                if self.worker.request(session):
                    self._last_request_at = now
        return batches

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.worker is not None:
            self.worker.close()
        if self.ui is not None:
            self.ui.close()
