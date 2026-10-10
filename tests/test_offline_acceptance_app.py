import json
from pathlib import Path
import queue
from types import MethodType, SimpleNamespace

import cv2
import numpy as np
import pytest

from tools import offline_acceptance_app as app


def test_collect_images_is_recursive_and_excludes_generated_crops(tmp_path):
    (tmp_path / "受伤").mkdir()
    (tmp_path / "受伤" / "01.jpg").write_bytes(b"image")
    (tmp_path / "受伤" / "01_crop.jpg").write_bytes(b"derived")
    (tmp_path / "note.txt").write_text("not an image", encoding="utf-8")
    assert [path.name for path in app.collect_images(tmp_path)] == ["01.jpg"]


def test_labels_and_healing_sequences_are_inferred_from_directories(tmp_path):
    root = tmp_path / "screenshots"
    path = root / "healing" / "group_two" / "02.jpg"
    path.parent.mkdir(parents=True)
    path.touch()
    assert app.infer_expected_and_group(path, root) == ("healing", "healing/group_two")


def test_unicode_safe_image_read(tmp_path):
    path = tmp_path / "治疗截图.png"
    expected = np.full((3, 4, 3), 127, np.uint8)
    ok, encoded = cv2.imencode(".png", expected)
    assert ok
    encoded.tofile(path)
    actual = app._read_image(path)
    assert actual is not None
    assert np.array_equal(actual, expected)


def test_opencv_decode_error_is_an_unreadable_image(tmp_path, monkeypatch):
    path = tmp_path / "damaged.png"
    path.write_bytes(b"invalid image")

    def fail(*args, **kwargs):
        raise cv2.error("image pixel limit exceeded")

    monkeypatch.setattr(app.cv2, "imdecode", fail)
    assert app._read_image(path) is None


def test_unreadable_image_breaks_health_history_and_batch_continues(tmp_path, monkeypatch):
    root = tmp_path / "shots"
    for name in ("01.png", "02.png", "03.png"):
        path = root / "injured" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(app, "_read_image", lambda path: (
        None if path.name == "02.png" else np.zeros((2, 2, 3), np.uint8)
    ))
    history = []

    class FakePipeline:
        def __init__(self, config):
            self.previous = None
        def reset(self):
            self.previous = None
            history.append("reset")
        def process(self, frame, timestamp):
            history.append(self.previous)
            self.previous = timestamp
            result = SimpleNamespace(
                state="unknown", valid=False, health_raw=0.5,
                health_stable=0.5, confidence=0.9, red_score=0.0, death_score=0.0,
            )
            return result, SimpleNamespace(anchor_score=0.8), SimpleNamespace()

    monkeypatch.setattr(app, "VisionPipeline", FakePipeline)
    monkeypatch.setattr(app, "load_config", lambda path: {})
    records, summary = app.evaluate_screenshots(root, tmp_path / "safe.ini")
    assert [record["state"] for record in records] == ["unknown", "error", "unknown"]
    assert history == [None, "reset", None]
    assert summary["read_errors"] == 1
    assert summary["total_images"] == 3


def test_evaluation_groups_sequences_and_reports_truth_without_devices(tmp_path, monkeypatch):
    root = tmp_path / "shots"
    paths = [root / "injured" / "01.jpg", root / "injured" / "02.jpg",
             root / "misc" / "single.jpg"]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(app, "_read_image", lambda path: np.zeros((2, 2, 3), np.uint8))
    constructed = []

    class FakePipeline:
        def __init__(self, config):
            self.calls = 0
            constructed.append(self)
        def process(self, frame, timestamp):
            self.calls += 1
            state = "unknown" if self.calls == 1 else "injured"
            result = SimpleNamespace(
                state=state, valid=state != "unknown", health_raw=0.5,
                health_stable=0.5, confidence=0.9, red_score=0.1, death_score=0.0,
            )
            roi = SimpleNamespace(anchor_score=0.8)
            return result, roi, SimpleNamespace()

    monkeypatch.setattr(app, "VisionPipeline", FakePipeline)
    monkeypatch.setattr(app, "load_config", lambda path: {})
    records, summary = app.evaluate_screenshots(root, tmp_path / "safe.ini")
    assert len(constructed) == 2
    assert [record["state"] for record in records] == ["unknown", "injured", "unknown"]
    assert summary["total_images"] == 3
    assert summary["labelled_images"] == 2
    assert summary["matched_images"] == 1
    assert summary["offline_only"] is True


def test_reports_are_utf8_and_contain_summary(tmp_path):
    records = [{"file": "受伤/01.jpg", "state": "injured"}]
    summary = {"offline_only": True, "total_images": 1}
    csv_path, json_path = app.write_reports(tmp_path, records, summary)
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "受伤/01.jpg" in csv_path.read_text(encoding="utf-8-sig")
    assert '"offline_only": true' in json_path.read_text(encoding="utf-8")


def test_bad_frame_is_recorded_and_next_image_continues(tmp_path, monkeypatch):
    root = tmp_path / "shots"
    for name in ("01.jpg", "02.jpg"):
        path = root / "injured" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(app, "_read_image", lambda path: np.zeros((2, 2, 3), np.uint8))

    class FakePipeline:
        def __init__(self, config):
            self.calls = 0
            self.resets = 0
        def reset(self):
            self.resets += 1
        def process(self, frame, timestamp):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("bad synthetic frame")
            result = SimpleNamespace(
                state="injured", valid=True, health_raw=0.5,
                health_stable=0.5, confidence=0.9, red_score=0.1, death_score=0.0,
            )
            return result, SimpleNamespace(anchor_score=0.8), SimpleNamespace()

    monkeypatch.setattr(app, "VisionPipeline", FakePipeline)
    monkeypatch.setattr(app, "load_config", lambda path: {})
    records, summary = app.evaluate_screenshots(root, tmp_path / "safe.ini")
    assert [record["state"] for record in records] == ["error", "injured"]
    assert summary["read_errors"] == 1
    assert summary["matched_images"] == 1


def test_video_evaluation_forces_mock_and_writes_complete_reports(tmp_path, monkeypatch):
    from tools import video_acceptance

    video = tmp_path / "治疗.mp4"
    output = tmp_path / "chosen_reports"
    rows = [{"frame": 0, "actions": "healing_pulse", "strength": 15}]
    logs = [{"action": "healing_pulse", "note": "治疗"}]
    summary = {"offline_only": True, "sender_mode": "mock", "frames": 1}
    callbacks = []

    def evaluate(path, config, progress=None):
        assert path == video
        assert config["trigger"]["sender_mode"] == "mock"
        progress(1, 1)
        return rows, summary, logs

    monkeypatch.setattr(app, "load_config", lambda path: {"trigger": {"sender_mode": "dglab"}})
    monkeypatch.setattr(video_acceptance, "evaluate_video", evaluate)
    result, paths = app.evaluate_video_file(
        video, tmp_path / "hardware.ini", lambda *args: callbacks.append(args),
        output_dir=output,
    )
    assert result is summary
    assert callbacks == [(1, 1)]
    assert all(path.parent == output for path in paths)
    csv_path, json_path, log_path = paths
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "healing_pulse" in csv_path.read_text(encoding="utf-8-sig")
    assert json.loads(json_path.read_text(encoding="utf-8")) == summary
    assert json.loads(log_path.read_text(encoding="utf-8")) == logs[0]


class FakeWidget(dict):
    def configure(self, **values):
        self.update(values)


def headless_window(monkeypatch):
    """Use real window methods, but never construct a Tk window or start a thread."""
    from tkinter import messagebox

    window = SimpleNamespace(
        messages=queue.Queue(), running=False, config_path=Path("safe.ini"),
        run_button=FakeWidget(), video_button=FakeWidget(), config_button=FakeWidget(),
        progress=FakeWidget(), status=FakeWidget(), lines=[], scheduled=[], dialogs=[],
    )
    window.append = window.lines.append
    window.after = lambda delay, callback: window.scheduled.append((delay, callback))
    monkeypatch.setattr(messagebox, "showinfo", lambda *args: window.dialogs.append(("info", args)))
    monkeypatch.setattr(messagebox, "showerror", lambda *args: window.dialogs.append(("error", args)))
    window_type = app._window_type()
    for name in ("worker", "poll_messages", "set_running", "start_worker",
                 "choose_and_run", "choose_video"):
        setattr(window, name, MethodType(getattr(window_type, name), window))
    return window


def video_summary():
    return {"frames": 7, "valid_frames": 4, "fps": 30.0, "resolution": [1280, 720],
            "action_counts": {"healing_pulse": 1}, "nonzero_frames": 3,
            "final_strength": 0, "timestamp_fallback_frames": 0}


def test_video_button_selects_video_worker_and_screenshot_button_stays_separate(monkeypatch):
    from tkinter import filedialog

    window = headless_window(monkeypatch)
    calls = []
    window.start_worker = lambda path, video=False: calls.append((path, video))
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: "local.mp4")
    monkeypatch.setattr(filedialog, "askdirectory", lambda **kwargs: "shots")
    window.choose_video()
    window.choose_and_run()
    assert calls == [(Path("local.mp4"), True), (Path("shots"), False)]
    window.running = True
    window.choose_video()
    window.choose_and_run()
    assert len(calls) == 2


def test_video_worker_dispatches_progress_report_and_restores_all_buttons(monkeypatch):
    window = headless_window(monkeypatch)
    paths = tuple(Path(name) for name in ("frames.csv", "summary.json", "mock.jsonl"))
    def evaluate(path, config, progress):
        assert (path, config) == (Path("local.mp4"), window.config_path)
        progress(3, 7)
        return video_summary(), paths
    monkeypatch.setattr(app, "evaluate_video_file", evaluate)
    monkeypatch.setattr(app, "evaluate_screenshots", lambda *args: pytest.fail("Wrong evaluator"))
    window.set_running(True)
    window.worker(Path("local.mp4"), window.config_path, video=True)
    assert window.running is True  # Workers do not touch Tk widgets.
    window.poll_messages()
    assert window.running is False
    assert all(button["state"] == "normal" for button in (
        window.run_button, window.video_button, window.config_button,
    ))
    assert window.progress["value"] == window.progress["maximum"] == 7
    assert window.status["text"] == "完成"
    assert all(any(str(path) in line for line in window.lines) for path in paths)
    assert window.dialogs[0][0] == "info"
    assert len(window.scheduled) == 1


@pytest.mark.parametrize("video", [False, True])
def test_worker_failure_restores_ui_and_does_not_claim_a_report(monkeypatch, video):
    window = headless_window(monkeypatch)
    def fail(*args, **kwargs):
        raise OSError("report directory is read-only")
    monkeypatch.setattr(app, "evaluate_video_file", fail)
    monkeypatch.setattr(app, "evaluate_screenshots", lambda *args: ([{}], {}))
    monkeypatch.setattr(app, "write_reports", fail)
    window.set_running(True)
    window.worker(Path("input"), window.config_path, video=video)
    window.poll_messages()
    assert window.running is False
    assert window.status["text"] == "失败"
    assert window.run_button["state"] == window.video_button["state"] == "normal"
    assert window.config_button["state"] == "normal"
    assert window.dialogs[0] == ("error", ("离线验收失败", "report directory is read-only"))
    assert not any("CSV：" in line for line in window.lines)
    assert len(window.scheduled) == 1


def test_thread_start_failure_releases_controls_and_duplicate_run_is_ignored(monkeypatch):
    window = headless_window(monkeypatch)
    attempts = []
    class FailedThread:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
        def start(self):
            raise RuntimeError("cannot start worker")
    monkeypatch.setattr(app.threading, "Thread", FailedThread)
    window.start_worker(Path("one.mp4"), video=True)
    window.start_worker(Path("two.mp4"), video=True)
    assert len(attempts) == 1
    assert attempts[0]["args"] == (Path("one.mp4"), window.config_path, True)
    window.poll_messages()
    assert not window.running
    assert window.status["text"] == "失败"
    assert window.video_button["state"] == "normal"


def test_headless_video_cli_selects_explicit_config_and_output_without_gui(tmp_path, monkeypatch, capsys):
    calls = []
    def evaluate(path, config, *, output_dir):
        calls.append((path, config, output_dir))
        return video_summary(), (output_dir / "report.json",)
    monkeypatch.setattr(app, "evaluate_video_file", evaluate)
    monkeypatch.setattr(app, "run_gui", lambda: pytest.fail("CLI must not open GUI"))
    assert app.main(["--video", "local.mp4", "--config", "custom.ini",
                     "--output-dir", str(tmp_path)]) == 0
    assert calls == [(Path("local.mp4"), Path("custom.ini"), tmp_path)]
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["summary"]["frames"] == 7
    assert output["reports"] == [str(tmp_path / "report.json")]


def test_headless_video_failure_returns_nonzero_without_gui(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise ValueError("cannot decode video")
    monkeypatch.setattr(app, "evaluate_video_file", fail)
    monkeypatch.setattr(app, "run_gui", lambda: pytest.fail("CLI must not open GUI"))
    assert app.main(["--video", "broken.mp4"]) == 1
    assert "cannot decode video" in capsys.readouterr().err


def test_self_check_exercises_mock_core_and_never_opens_gui(monkeypatch, capsys):
    config = app.load_config(app.default_config_path())
    config["trigger"]["sender_mode"] = "dglab"
    monkeypatch.setattr(app, "load_config", lambda path: config)
    monkeypatch.setattr(app, "run_gui", lambda: pytest.fail("Self-check must not open GUI"))
    assert app.main(["--self-check"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["offline_only"]
    assert result["sender_mode"] == "mock"
    assert result["final_strength"] == 0


@pytest.mark.parametrize("args, expected", [
    (["--self-check"], 0), (["--video", "local.mp4"], 0),
    (["--video", "broken.mp4"], 1),
])
def test_windowed_exe_cli_works_without_console_streams(monkeypatch, args, expected):
    def evaluate(path, config, *, output_dir):
        if path.name == "broken.mp4":
            raise ValueError("cannot decode video")
        return video_summary(), (Path("report.json"),)

    monkeypatch.setattr(app, "evaluate_video_file", evaluate)
    monkeypatch.setattr(app, "run_gui", lambda: pytest.fail("CLI must not open GUI"))
    # PyInstaller --windowed starts with both streams absent. Success and
    # error reporting must preserve the exit status instead of crashing.
    monkeypatch.setattr(app.sys, "stdout", None)
    monkeypatch.setattr(app.sys, "stderr", None)
    assert app.main(args) == expected


@pytest.mark.parametrize("args", [
    ["--output-dir", "reports"], ["--config", "custom.ini"],
    ["--self-check", "--video", "local.mp4"],
])
def test_cli_rejects_ambiguous_options_without_opening_gui(monkeypatch, args):
    monkeypatch.setattr(app, "run_gui", lambda: pytest.fail("Invalid CLI must not open GUI"))
    with pytest.raises(SystemExit) as exc:
        app.main(args)
    assert exc.value.code == 2
