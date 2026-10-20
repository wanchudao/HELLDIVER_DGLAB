"""Best-effort one-way UI telemetry. Never used for output or safety decisions."""
import json
import math
import os
from pathlib import Path
import queue
import threading
import time


def valid_record(record, session):
    if not isinstance(record, dict) or record.get('session') != session:
        return False
    if type(record.get('sequence')) is not int or record['sequence'] < 1:
        return False
    if any(type(record.get(key)) is not bool for key in ('paused', 'valid', 'connected')):
        return False
    if record.get('death_feedback_mode', 'pulse') not in ('pulse', 'until_revive', 'until_reset'):
        return False
    if any(type(record.get(key, False)) is not bool for key in ('death_marked', 'death_layer_active')):
        return False
    for key in ('resolution', 'hud_source', 'hud_profile_version'):
        if record.get(key) is not None and (type(record[key]) is not str or len(record[key]) > 240):
            return False
    for key in ('written_at', 'hp'):
        value = record.get(key)
        if key == 'hp' and value is None and not record['valid']:
            continue
        if type(value) not in (float, int) or not math.isfinite(value):
            return False
        if key == 'hp' and not 0 <= value <= 1:
            return False
    strengths = record.get('strengths')
    return (isinstance(strengths, dict) and set(strengths) == {'A', 'B'}
            and all(value is None or type(value) is int and 0 <= value <= 100 for value in strengths.values())
            and record.get('mode') in ('mock', 'dglab', 'monitor'))


class StatusPublisher:
    def __init__(self, path, session, *, clock=time.monotonic):
        self.path, self.session, self.clock = Path(path), session, clock
        self.queue = queue.Queue(maxsize=1)
        self.last_publish = -float('inf')
        self.sequence = 0
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._write, daemon=True, name='ui-status')
        self.thread.start()

    def publish(self, *, paused, valid, hp, connected, strengths, mode,
                death_feedback_mode="pulse", death_marked=False, death_layer_active=False,
                resolution=None, hud_source=None, hud_profile_version=None):
        now = self.clock()
        if self.stopped.is_set() or now-self.last_publish < .2:
            return False
        self.last_publish = now
        self.sequence += 1
        record = dict(session=self.session, sequence=self.sequence, written_at=time.time(),
                      paused=paused, valid=valid, hp=hp if valid else None, connected=connected,
                      strengths=strengths, mode=mode, death_feedback_mode=death_feedback_mode,
                      death_marked=death_marked, death_layer_active=death_layer_active,
                      resolution=resolution, hud_source=hud_source, hud_profile_version=hud_profile_version)
        try:
            self.queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            pass
        return True

    def _write(self):
        while not self.stopped.is_set() or not self.queue.empty():
            try:
                record = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            temporary = self.path.with_suffix('.ui.tmp')
            try:
                temporary.write_text(json.dumps(record, separators=(',', ':')), encoding='utf-8')
                os.replace(temporary, self.path)
            except OSError:
                pass  # Diagnostics cannot reset, block, or reconnect output.

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=.3)


class StatusReader:
    def __init__(self, path, session, *, clock=time.monotonic, wall=time.time):
        self.path, self.session = Path(path), session
        self.clock, self.wall = clock, wall
        self.sequence = 0
        self.received_at = -float('inf')
        self.record = None

    def read(self):
        try:
            if self.path.stat().st_size > 8192:
                return None
            record = json.loads(self.path.read_text(encoding='utf-8'))
            if (valid_record(record, self.session) and record['sequence'] > self.sequence
                    and -.5 <= self.wall()-record['written_at'] <= 1):
                self.sequence = record['sequence']
                self.received_at = self.clock()
                self.record = record
        except (OSError, ValueError, TypeError):
            pass
        if self.clock()-self.received_at > 1:
            return None
        return self.record
