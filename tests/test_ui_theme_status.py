import json
import os
import time
from pathlib import Path
import pytest
from src.ui_theme import UiSettingsStore, responsive_size, work_area_geometry
from src.ui_status import StatusReader, StatusPublisher, valid_record


def record(**kwargs):
    return dict(session='test', sequence=1, written_at=time.time(), paused=False,
                valid=True, hp=.5, connected=False, strengths={'A': 10, 'B': 0}, mode='mock', **kwargs)


def test_preferences_default_saved_and_corrupt(tmp_path):
    path = tmp_path/'ui_settings.ini'
    store = UiSettingsStore(path)
    assert store.animation and not path.exists()
    store.save(False)
    assert not UiSettingsStore(path).animation
    store.save(True)
    assert UiSettingsStore(path).animation
    path.write_text('[ui]\nanimation=yes', encoding='utf-8')
    bad = UiSettingsStore(path)
    assert not bad.animation and bad.error


def test_preferences_failed_replace_preserves_file_and_memory(tmp_path, monkeypatch):
    store = UiSettingsStore(tmp_path/'ui_settings.ini')
    store.save(False)
    original = store.path.read_bytes()
    def fail(*a):
        raise OSError('denied')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        store.save(True)
    assert not store.animation and store.path.read_bytes() == original
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('key,value', [('sequence', True), ('sequence', 0), ('hp', float('nan')),
                                      ('hp', True), ('hp', 2), ('valid', 1), ('paused', 'yes'),
                                      ('connected', None), ('written_at', float('inf')),
                                      ('mode', 'dghub'), ('strengths', {'A': True, 'B': 0}),
                                      ('strengths', {'A': 0}), ('session', 'old')])
def test_invalid_status(key, value):
    data = record()
    data[key] = value
    assert not valid_record(data, 'test')


def test_reader_stale_old_session_reverse_and_bad_json(tmp_path):
    tick = [0.]
    path = tmp_path/'status.json'
    read = StatusReader(path, 'test', clock=lambda: tick[0])
    path.write_text(json.dumps(record()), encoding='utf-8')
    assert read.read()['hp'] == .5
    data = record(); data.update(sequence=2, hp=.3)
    path.write_text(json.dumps(data), encoding='utf-8')
    assert read.read()['hp'] == .3
    data.update(session='old', sequence=99)
    path.write_text(json.dumps(data), encoding='utf-8')
    assert read.read()['sequence'] == 2
    path.write_text('{bad', encoding='utf-8')
    tick[0] = 1.1
    assert read.read() is None
    path.write_text(json.dumps(record()), encoding='utf-8')
    assert read.read() is None  # reverse sequence does not renew freshness
    data = record(); data.update(sequence=3, written_at=time.time()-2)
    path.write_text(json.dumps(data), encoding='utf-8')
    assert read.read() is None


def test_publisher_bounded_nonblocking_and_fault_isolated(tmp_path):
    tick = [0.]
    path = tmp_path/'status.json'
    pub = StatusPublisher(path, 'test', clock=lambda: tick[0])
    args = dict(paused=True, valid=False, hp=0, connected=False,
                strengths={'A': 0, 'B': 0}, mode='mock')
    try:
        assert pub.publish(**args)
        assert not pub.publish(**args)
        tick[0] = .2
        assert pub.publish(**args)
    finally:
        pub.close()
    data = json.loads(path.read_text())
    assert data['sequence'] == 2 and data['hp'] is None and valid_record(data, 'test')
    assert not pub.publish(**args)
    bad = StatusPublisher(tmp_path/'missing'/'status.json', 'test')
    assert bad.publish(**args)
    bad.close()  # unwritable telemetry cannot stop output or raise


@pytest.mark.parametrize('scale', [1, 1.25, 1.5, 2])
def test_logical_work_area_clamped(scale):
    w, h = int(1920/scale), int(1040/scale)
    width, height = responsive_size(1280, 820, w, h)
    assert width <= w and height <= h


def test_control_theme_is_opt_in():
    import numpy as np
    from src.control_panel import draw_control_panel, draw_island
    for draw, args in ((draw_control_panel, {}), (draw_island, {'paused': True})):
        legacy = draw(**args)
        assert np.array_equal(legacy, draw(**args, theme='legacy'))
        new = draw(**args, theme='cutting_edge')
        assert legacy.shape == new.shape and not np.array_equal(legacy, new)


@pytest.mark.parametrize('work', [(0, 0, 1920, 1040), (-1920, -1080, 0, -40), (80, 60, 1000, 680), (-960, 0, 0, 500)])
@pytest.mark.parametrize('outer', [(0, 0, 1296, 859), (-2400, -1200, -1104, -341)])
def test_native_work_area_geometry_includes_titlebar_and_negative_monitor(work, outer):
    width, height, x, y, border_w, border_h = work_area_geometry(1280, 820, outer, work)
    assert x >= work[0] and y >= work[1]
    assert x+width+border_w <= work[2] and y+height+border_h <= work[3]
    assert width > 0 and height > 0


def test_sender_ui_snapshot_does_not_send_or_wait(monkeypatch):
    from src import dglab_sender as sender
    from types import SimpleNamespace
    client = SimpleNamespace(_paired_ready=True, _should_stop=False)
    monkeypatch.setattr(sender, '_client', client)
    monkeypatch.setattr(sender, '_ui_strengths', {'A': (client, 12), 'B': (client, None)})
    assert sender.ui_output_snapshot() == {'connected': True, 'strengths': {'A': 12, 'B': None}}
    client._should_stop = True
    assert sender.ui_output_snapshot()['strengths'] == {'A': None, 'B': None}
