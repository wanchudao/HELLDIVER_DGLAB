"""Per-run metadata and bounded diagnostic logging, never SDK credentials.

Only the runtime owner finishes this writer, AFTER transport zero requests.
TriggerDispatch.close() is deliberately nonblocking. A stuck disk is not
allowed to hold the protocol owner or its vision lock while output is active.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import tempfile
from threading import Event, Lock, Thread
import sys

from dghub_plugin.sdk_config import manifest, validate


RECOGNITION_SECTIONS = frozenset({"ROI", "hud", "calibration", "capture", "features",
    "filters", "health", "death", "unknown", "state_machine", "color_space", "trigger"})


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class SessionRecord:
    def __init__(self, plugin_root, *, storage_root=None, queue_size=600, capture_source="selected_monitor"):
        if type(queue_size) is not int or not 1 <= queue_size <= 10000:
            raise ValueError("Invalid session log queue size")
        if capture_source not in {"selected_monitor", "synthetic_replay", "injected_test"}:
            raise ValueError("Invalid capture source")
        root = Path(plugin_root).resolve()
        if not root.is_dir():
            raise ValueError("Plugin root does not exist")
        storage = root if storage_root is None else Path(storage_root).resolve()
        if not storage.is_dir():
            raise ValueError("Session storage does not exist")
        parent = storage / "sessions"
        parent.mkdir(exist_ok=True)
        if not parent.resolve().is_relative_to(storage):
            raise ValueError("Session directory must remain inside its owned storage")
        stamp = datetime.now(timezone.utc)
        self.directory = Path(tempfile.mkdtemp(prefix=stamp.strftime("%Y%m%dT%H%M%S_"), dir=parent))
        info = manifest()
        self._metadata = dict(record_version=1, plugin_id=info["id"], plugin_version=info["version"],
            sdk=info["sdk"], created_utc=stamp.isoformat(), capture_source=capture_source,
            storage_kind="isolated_plugin_root" if storage == root else "persistent_user_data",
            frozen=bool(getattr(sys, "frozen", False)), native_host_verified=False,
            physical_delivery_verified=False)
        self._queue = Queue(maxsize=queue_size)
        self._lock = Lock()
        self._stop = Event()
        self.done = Event()
        self.snapshot_written = Event()
        self._snapshot = None
        self._terminal = None
        self.failed = False
        self.enqueued = self.written = self.dropped = self.invalid = 0
        self._thread = Thread(target=self._run, daemon=True, name="DGHubSessionRecord")
        self._thread.start()

    def configured(self, settings, core):
        """Freeze the actual applied ROI once; pending settings never overwrite it."""
        values = validate(settings)
        recognition = {key: value for key, value in core.items() if key in RECOGNITION_SECTIONS}
        # Exclude official backend addresses, old DGHub fields and debug paths.
        sender = {key: value for key, value in core.get("sender", {}).items()
                  if key in values or key.endswith("_duration") or key.endswith("_waveform")}
        snapshot = encode(dict(record_version=1, settings=values, recognition_config=recognition,
                               sender_config=sender))
        with self._lock:
            if self._stop.is_set() or self._snapshot is not None:
                raise RuntimeError("Session configuration is already fixed or closed")
            self._snapshot = snapshot

    def append(self, entry):
        try:
            line = encode(entry)
            if len(line) > 16384:
                raise ValueError("Oversized diagnostic entry")
        except (TypeError, ValueError):
            with self._lock:
                self.invalid += 1
            return
        with self._lock:
            if self._stop.is_set() or self.failed:
                return
            try:
                self._queue.put_nowait(line)
                self.enqueued += 1
            except Full:
                self.dropped += 1

    def close(self):
        # Trigger owns its event lifecycle, not the runtime writer lifecycle.
        # In particular, no join/file I/O can precede SDK final zero requests.
        return None

    def finish(self, reason, *, error=False, diagnostic="", timeout=.25):
        if not 0 <= timeout <= 1:
            raise ValueError("Invalid log finish timeout")
        if not isinstance(diagnostic, str) or len(diagnostic) > 256 or any(ord(c) < 32 for c in diagnostic):
            raise ValueError("Invalid sanitized runtime diagnostic")
        with self._lock:
            if not self._stop.is_set():
                self._terminal = dict(reason=str(reason)[:128], runtime_error=bool(error),
                                      runtime_diagnostic=diagnostic,
                                      ended_utc=datetime.now(timezone.utc).isoformat())
                self._stop.set()
        self._thread.join(timeout=timeout)
        return self.done.is_set() and not self.failed

    def status(self):
        with self._lock:
            return dict(state="failed" if self.failed else "complete" if self.done.is_set()
                        else "finishing" if self._stop.is_set() else "recording",
                        snapshot_written=self.snapshot_written.is_set(),
                        directory=str(self.directory),
                        rows_enqueued=self.enqueued, rows_written=self.written,
                        rows_dropped=self.dropped, rows_invalid=self.invalid)

    def _atomic(self, name, text):
        temporary = self.directory / (name + ".tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
            stream.flush()
        os.replace(temporary, self.directory / name)

    def _write_snapshot(self):
        with self._lock:
            snapshot = self._snapshot
        if snapshot is not None and not self.snapshot_written.is_set():
            self._atomic("config_snapshot.json", snapshot)
            self.snapshot_written.set()

    def _run(self):
        try:
            self._atomic("session.json", encode(self._metadata | {"state": "running"}))
            with (self.directory / "trigger_log.jsonl").open("x", encoding="utf-8", buffering=65536) as stream:
                while not self._stop.is_set() or not self._queue.empty():
                    self._write_snapshot()
                    try:
                        line = self._queue.get(timeout=.02)
                    except Empty:
                        stream.flush()
                        continue
                    stream.write(line + "\n")
                    with self._lock:
                        self.written += 1
                    if self.written % 120 == 0:
                        stream.flush()
                stream.flush()
            self._write_snapshot()
            with self._lock:
                terminal = dict(self._terminal or {})
                counts = dict(rows_enqueued=self.enqueued, rows_written=self.written,
                              rows_dropped=self.dropped, rows_invalid=self.invalid)
            self._atomic("session.json", encode(self._metadata | terminal | counts | {"state": "complete"}))
        except Exception:
            # Never expose raw filesystem or SDK data via synchronous logging.
            with self._lock:
                self.failed = True
                self._stop.set()
        finally:
            self.done.set()
