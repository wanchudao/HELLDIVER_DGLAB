import json

import cv2
import numpy as np
import pytest

from dghub_plugin.replay_capture import ReplayCapture, ReplayControls


def fixture(tmp_path, size=(1080, 1920)):
    image = np.full((*size, 3), 30, np.uint8)
    assert cv2.imwrite(str(tmp_path / "frame.png"), image)
    (tmp_path / "replay.json").write_text(json.dumps(dict(synthetic=True, frames=["frame.png"])))
    return tmp_path


def test_explicit_replay_never_resizes_and_stops_without_screen_capture(tmp_path):
    replay = ReplayCapture(fixture(tmp_path), monitor_index=2)
    assert replay.monitor_index == 2 and replay.is_ready
    assert replay.grab().shape == (1080, 1920, 3)
    assert replay.grab().shape == (1080, 1920, 3) and replay.index == 2
    replay.close()
    assert replay.grab() is None and not replay.is_ready


@pytest.mark.parametrize("data", [dict(synthetic=False, frames=["frame.png"]),
    dict(synthetic=True, frames=[]), dict(synthetic=True, frames=["../escape.png"]),
    dict(synthetic=True, frames=["frame.png"], extra=True),
    dict(synthetic=True, frames=["frame.png"] * 601), dict(synthetic=True, frames=[42])])
def test_malformed_or_escaping_replay_rejected(tmp_path, data):
    fixture(tmp_path)
    (tmp_path / "replay.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ReplayCapture(tmp_path)


def test_wrong_original_size_is_not_secretly_rescaled(tmp_path):
    with pytest.raises(ValueError):
        ReplayCapture(fixture(tmp_path, size=(1079, 1919)))


def test_missing_or_oversized_manifest_fails_before_input(tmp_path):
    with pytest.raises(OSError):
        ReplayCapture(tmp_path)


def test_local_replay_controls_are_one_shot_and_never_rearm_after_pause(tmp_path):
    from types import SimpleNamespace
    ui = ReplayControls(tmp_path)
    session = SimpleNamespace(armed=False)
    path = tmp_path / "controls.json"
    path.write_text(json.dumps(dict(sequence=1, running=True)))
    assert ui.poll(session) == []
    ui.configure({})
    assert ui.poll(session) == ["toggle"]
    assert ui.poll(session) == []  # a pause has not changed this one-shot intent
    session.armed = True
    path.write_text(json.dumps(dict(sequence=2, running=False)))
    assert ui.poll(session) == ["toggle"]
    ui.close()
    path.write_text(json.dumps(dict(sequence=3, running=True)))
    assert ui.poll(session) == []


@pytest.mark.parametrize("data", [[], {"sequence": True, "running": True},
    {"sequence": 0, "running": True}, {"sequence": 1, "running": "true"},
    {"sequence": 1, "running": True, "extra": 1}])
def test_local_replay_invalid_controls_fail_closed(tmp_path, data):
    from types import SimpleNamespace
    ui = ReplayControls(tmp_path)
    ui.configure({})
    (tmp_path / "controls.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ui.poll(SimpleNamespace(armed=False))
    (tmp_path / "replay.json").write_text("x" * 65537)
    with pytest.raises(ValueError, match="manifest"):
        ReplayCapture(tmp_path)
