from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.config_loader import load_config
from tools import video_acceptance as video
from tools import video_acceptance_isolated as isolated


def config():
    return load_config(video.ROOT / "config.example.ini")


def test_video_config_scales_coordinates_without_mutating_or_arming():
    original = config()
    original["trigger"]["sender_mode"] = "dglab"
    before = deepcopy(original)
    result = video.video_config(original, 1280, 720)
    assert original == before
    assert result["ROI"]["inner_x1"] == 76
    assert result["ROI"]["inner_x2"] == 196
    assert result["ROI"]["inner_y1"] == 664
    assert result["trigger"]["sender_mode"] == "mock"
    assert result["calibration"]["calibrated"] == 0
    assert result["filters"]["edge_region_1"] == "133, 0, 1147, 27, 1.0"


@pytest.mark.parametrize("resolution", ["", "garbage", "0x0", "1920xno"])
def test_missing_or_bad_calibration_resolution_fails(resolution):
    cfg = config()
    cfg["calibration"]["resolution"] = resolution
    with pytest.raises(ValueError):
        video.video_config(cfg, 1280, 720)


def test_nonuniform_resize_is_not_silently_treated_as_calibration():
    with pytest.raises(ValueError, match="aspect ratio"):
        video.video_config(config(), 1024, 768)


class FakeCapture:
    def __init__(self, timestamps, count=None):
        self.timestamps = timestamps
        self.index = -1
        self.count = len(timestamps) if count is None else count
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FPS: 30,
                cv2.CAP_PROP_FRAME_COUNT: self.count,
                cv2.CAP_PROP_POS_MSEC: self.timestamps[max(0, self.index)]}.get(prop, 0)

    def read(self):
        self.index += 1
        return ((True, np.zeros((720, 1280, 3), np.uint8))
                if self.index < len(self.timestamps) else (False, None))

    def release(self):
        self.released = True


class FakePipeline:
    def __init__(self, cfg):
        assert cfg["trigger"]["sender_mode"] == "mock"
        self.index = 0

    def process(self, frame, timestamp):
        values = [.9, .9, .5, .5, .5, .6, .6, .6]
        hp = values[min(self.index, len(values) - 1)]
        self.index += 1
        result = SimpleNamespace(state="injured", valid=True, health_raw=hp,
                                 health_stable=hp, confidence=1., death_score=0., debug={})
        return result, SimpleNamespace(anchor_score=1.), SimpleNamespace(health_raw=hp)


def setup_video(tmp_path, monkeypatch, timestamps, count=None):
    path = tmp_path / "本地测试.mp4"
    path.write_bytes(b"fake test video")
    cap = FakeCapture(timestamps, count)
    monkeypatch.setattr(video.cv2, "VideoCapture", lambda _: cap)
    monkeypatch.setattr(video, "VisionPipeline", FakePipeline)
    from src import trigger
    monkeypatch.setattr(trigger, "_AsyncJsonlWriter", lambda _: pytest.fail("Shared log touched"))
    return path, cap


def test_replay_uses_pts_real_trigger_and_lossless_isolated_logs(tmp_path, monkeypatch):
    timestamps = [0, 100, 200, 300, 450, 500, 600, 700]
    path, cap = setup_video(tmp_path, monkeypatch, timestamps)
    cfg = config()
    cfg["trigger"]["sender_mode"] = "dglab"
    rows, summary, logs = video.evaluate_video(path, cfg)
    assert cap.released
    assert len(rows) == 8
    assert len(logs) == len(rows) + 2  # all frames, session start/end
    assert [r["time_sec"] for r in rows] == [t / 1000 for t in timestamps]
    assert summary["timestamp_fallback_frames"] == 0
    assert summary["action_counts"] == {"injury_pulse": 1, "heal_pulse": 1}
    assert rows[4]["damage_pct"] == .4
    assert rows[4]["strength"] == 18  # sustained 10 + 40% * injury20
    assert rows[5]["healing_sustain"]
    assert summary["final_strength"] == 0
    assert cfg["trigger"]["sender_mode"] == "dglab"


def test_timestamp_fallback_is_monotonic_and_reported(tmp_path, monkeypatch):
    path, cap = setup_video(tmp_path, monkeypatch, [0, 0, float("nan"), 100, 90])
    rows, summary, _ = video.evaluate_video(path, config())
    times = [row["time_sec"] for row in rows]
    assert all(right > left for left, right in zip(times, times[1:]))
    assert summary["timestamp_fallback_frames"] == 3


def test_truncated_decode_is_failure_not_a_pass(tmp_path, monkeypatch):
    path, cap = setup_video(tmp_path, monkeypatch, [0, 100], count=10)
    with pytest.raises(ValueError, match="Incomplete decode"):
        video.evaluate_video(path, config())
    assert cap.released


def test_processing_error_stops_trigger_and_releases_video(tmp_path, monkeypatch):
    path, cap = setup_video(tmp_path, monkeypatch, [0, 100])
    stopped = []
    actual_stop = video.TriggerDispatch.safe_stop
    def stop(self):
        actual_stop(self)
        stopped.append(self.mock_output()["strength"])
    def broken(self, *args, **kwargs):
        raise ValueError("synthetic decode processing failure")
    monkeypatch.setattr(video.TriggerDispatch, "safe_stop", stop)
    monkeypatch.setattr(FakePipeline, "process", broken)
    with pytest.raises(ValueError, match="synthetic"):
        video.evaluate_video(path, config())
    assert cap.released and stopped == [0]


def test_stop_failure_still_releases_video(tmp_path, monkeypatch):
    path, cap = setup_video(tmp_path, monkeypatch, [0, 100])

    def fail(self):
        raise RuntimeError("synthetic Mock cleanup failure")

    monkeypatch.setattr(video.TriggerDispatch, "safe_stop", fail)
    with pytest.raises(RuntimeError, match="cleanup"):
        video.evaluate_video(path, config())
    assert cap.released


def test_expectation_empty_range_and_wrong_hash_cannot_pass():
    rows = [{"time_sec": 1, "valid": False, "strength": 0, "actions": ""}]
    checks = video.check_expectations(rows, {"sha256": "actual"}, {
        "sha256": "expected", "checks": [
            {"start": 10, "field": "strength", "value": 0, "all": True},
            {"field": "valid", "value": True, "max": 0}]})
    assert [item["passed"] for item in checks] == [False, False, True]


def test_reports_are_unicode_lossless_and_never_overwrite(tmp_path):
    rows = [{"frame": 0, "state": "unknown"}]
    first = video.write_reports(tmp_path, "无血条", rows, {"frames": 1}, [{"frame": 1}])
    second = video.write_reports(tmp_path, "无血条", rows, {"frames": 1}, [{"frame": 1}])
    assert first != second and all(path.is_file() for path in (*first, *second))
    assert first[0].read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(first[1].read_text(encoding="utf-8"))["frames"] == 1


@pytest.mark.parametrize("sample", json.loads(
    (video.ROOT / "tests" / "fixtures" / "video_acceptance.json").read_text(encoding="utf-8")
)["samples"], ids=lambda sample: sample["file"])
def test_supplied_mp4_regression(sample):
    path = video.ROOT / "screenshots" / "video" / sample["file"]
    if not path.is_file():
        pytest.skip("User-owned MP4 fixture not present; use --regression on the sample machine")
    result = isolated.evaluate_isolated(path, config(), sample, timeout_seconds=120)
    assert result["status"] == "ok", json.dumps(result, ensure_ascii=False, indent=2)
    assert result["regression_passed"] is True
    assert result["log_entries"] == result["rows"] + 2
    assert result["summary"]["final_strength"] == 0


def test_isolated_timeout_is_failure_and_reaps_only_owned_child(monkeypatch):
    import subprocess

    class StalledChild:
        returncode = None
        terminated = False
        killed = False

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if self.killed:
                self.returncode = -9
                return self.returncode
            raise subprocess.TimeoutExpired("owned Mock child", timeout)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    child = StalledChild()
    requests = []

    def launch(command, **kwargs):
        request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
        requests.append(request)
        assert kwargs["stdin"] == subprocess.DEVNULL
        return child

    monkeypatch.setattr(isolated.subprocess, "Popen", launch)
    original = config()
    original["trigger"]["sender_mode"] = "dglab"
    before = deepcopy(original)
    result = isolated.evaluate_isolated("unused.mp4", original, timeout_seconds=.01)
    assert original == before
    assert requests[0]["config"]["trigger"]["sender_mode"] == "mock"
    assert result["status"] == "timeout" and result["regression_passed"] is False
    assert result["worker_reaped"] and child.terminated and child.killed


def test_isolated_decode_failure_is_reported_without_hanging(tmp_path):
    path = tmp_path / "not-a-video.mp4"
    path.write_bytes(b"not an MP4")
    result = isolated.evaluate_isolated(path, config(), timeout_seconds=30)
    assert result["status"] == "failed" and result["regression_passed"] is False
    assert result["sender_mode"] == "mock" and result["worker_exit_code"] != 0
    assert "Cannot decode" in result["error"]
