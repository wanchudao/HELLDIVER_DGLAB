"""Configuration/calibration regressions: temporary files only, no GUI or devices."""
import configparser
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from src import config_loader
from tools import calibrate


TEMPLATE = """# keep the checked-in template unchanged
[ROI]
outer_x1 = 100
[trigger]
sender_mode = mock
[sender]
injury_pulse_strength = 12
[capture]
monitor_index = 2
"""


@pytest.fixture
def example(tmp_path, monkeypatch):
    path = tmp_path / "config.example.ini"
    path.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(calibrate, "EXAMPLE_CONFIG_PATH", path)
    return path


def _read(path):
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    return cp


def _fake_selection(monkeypatch, roi=(100, 993, 205, 14)):
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    monkeypatch.setattr(calibrate.cv2, "imread", lambda path: image)
    monkeypatch.setattr(calibrate, "_fit_to_screen", lambda img: (img, 1.0))
    monkeypatch.setattr(calibrate, "select_roi_loop", lambda img: roi)
    return image


def test_default_loader_uses_example_without_creating_local_config(tmp_path, example, monkeypatch):
    monkeypatch.setattr(config_loader, "__file__", str(tmp_path / "src" / "config_loader.py"))
    config = config_loader.load_config()
    assert config["trigger"]["sender_mode"] == "mock"
    assert not (tmp_path / "config.ini").exists()


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        config_loader.load_config(tmp_path / "missing.ini")


def test_calibration_creates_missing_local_config_from_example(tmp_path, example):
    target = tmp_path / "config.ini"
    params = calibrate.compute_roi_params(100, 993, 205, 14)
    calibrate.write_config(params, (1920, 1080), target)
    saved = _read(target)
    assert saved.getint("ROI", "outer_x1") == 100
    assert saved.get("calibration", "resolution") == "1920x1080"
    assert saved.getint("calibration", "calibrated") == 1
    assert saved.get("trigger", "sender_mode") == "mock"
    assert saved.getint("capture", "monitor_index") == 2
    assert example.read_text(encoding="utf-8") == TEMPLATE
    assert not list(tmp_path.glob("config.ini.bak.*"))


def test_existing_config_backed_up_and_unrelated_settings_preserved(tmp_path, example):
    target = tmp_path / "plugin.ini"
    original = TEMPLATE.replace("sender_mode = mock", "sender_mode = dghub")
    target.write_text(original, encoding="utf-8")
    calibrate.write_config(calibrate.compute_roi_params(200, 1986, 410, 28), (3840, 2160), target)
    backups = list(tmp_path.glob("plugin.ini.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    saved = _read(target)
    assert saved.get("trigger", "sender_mode") == "dghub"
    assert saved.getint("sender", "injury_pulse_strength") == 12
    assert saved.get("calibration", "resolution") == "3840x2160"


def test_same_timestamp_backups_never_overwrite(tmp_path, example, monkeypatch):
    target = tmp_path / "config.ini"
    target.write_text(TEMPLATE, encoding="utf-8")
    fixed = datetime(2026, 8, 30, 12, 0, 0, 123456)
    monkeypatch.setattr(calibrate, "datetime", SimpleNamespace(now=lambda: fixed))
    params = calibrate.compute_roi_params(100, 993, 205, 14)
    calibrate.write_config(params, (1920, 1080), target)
    first_saved = target.read_text(encoding="utf-8")
    calibrate.write_config(params, (1920, 1080), target)
    backups = sorted(tmp_path.glob("config.ini.bak.*"))
    assert len(backups) == 2
    assert "123456" in backups[0].name
    assert backups[0].read_text(encoding="utf-8") == TEMPLATE
    assert backups[1].read_text(encoding="utf-8") == first_saved


def test_failed_atomic_replace_preserves_config_and_cleans_temp(tmp_path, example, monkeypatch):
    target = tmp_path / "config.ini"
    target.write_text(TEMPLATE, encoding="utf-8")

    def fail_replace(source, dest):
        raise OSError("simulated write protection")

    monkeypatch.setattr(calibrate.os, "replace", fail_replace)
    with pytest.raises(OSError, match="write protection"):
        calibrate.write_config(calibrate.compute_roi_params(100, 993, 205, 14), (1920, 1080), target)
    assert target.read_text(encoding="utf-8") == TEMPLATE
    assert not list(tmp_path.glob(".config.ini.*.tmp"))


@pytest.mark.parametrize("existing", [False, True])
def test_cancel_does_not_create_modify_or_backup_config(tmp_path, example, monkeypatch, existing):
    target = tmp_path / "config.ini"
    if existing:
        target.write_text(TEMPLATE, encoding="utf-8")
    image_path = tmp_path / "screenshot.png"
    image_path.touch()
    _fake_selection(monkeypatch, roi=None)
    assert calibrate.main(["--image", str(image_path), "--config", str(target)]) == 0
    assert target.exists() is existing
    if existing:
        assert target.read_text(encoding="utf-8") == TEMPLATE
    assert not list(tmp_path.glob("config.ini.bak.*"))
    assert example.read_text(encoding="utf-8") == TEMPLATE


def test_explicit_plugin_config_is_the_only_write_target(tmp_path, example, monkeypatch):
    target = tmp_path / "plugin.ini"
    image_path = tmp_path / "screenshot.png"
    image_path.touch()
    _fake_selection(monkeypatch)
    assert calibrate.main(["--image", str(image_path), "--config", str(target)]) == 0
    assert target.is_file()
    assert not (tmp_path / "config.ini").exists()
    assert _read(target).getint("ROI", "inner_x1") == 106


def test_capture_uses_configured_monitor_and_closes_on_cancel(tmp_path, example, monkeypatch):
    import src.screen_capture

    target = tmp_path / "config.ini"
    image = _fake_selection(monkeypatch, roi=None)
    calls = []

    class FakeCapture:
        is_ready = True

        def __init__(self, monitor_index):
            calls.append(("monitor", monitor_index))

        def grab(self):
            return image

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(src.screen_capture, "ScreenCapture", FakeCapture)
    monkeypatch.setattr(calibrate, "_COUNTDOWN_SEC", 0)
    assert calibrate.main(["--config", str(target)]) == 0
    assert calls == [("monitor", 2), ("close",)]
    assert not target.exists()


def test_invalid_monitor_fails_before_capture(tmp_path, example, monkeypatch):
    target = tmp_path / "config.ini"
    original = TEMPLATE.replace("monitor_index = 2", "monitor_index = 0")
    target.write_text(original, encoding="utf-8")
    assert calibrate.main(["--config", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("config.ini.bak.*"))


def test_directory_target_is_rejected_without_gui_or_writes(tmp_path, example):
    target = tmp_path / "not_a_file"
    target.mkdir()
    assert calibrate.main(["--config", str(target)]) == 1
    assert not list(target.iterdir())


def test_malformed_existing_config_is_not_replaced(tmp_path, example):
    target = tmp_path / "config.ini"
    target.write_text("this is not an ini file", encoding="utf-8")
    assert calibrate.main(["--config", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "this is not an ini file"
    assert not list(tmp_path.glob("config.ini.bak.*"))


def test_start_batch_encoding_and_safe_default_branch():
    script = Path(__file__).resolve().parents[1] / "start.bat"
    data = script.read_bytes()
    data.decode("ascii")
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in data.replace(b"\r\n", b"")
    source = data.decode("ascii")
    default_branch = source.split(":hardware\r\n", 1)[0]
    assert "--enable-trigger --sender-mode mock" in default_branch
    assert "cmd /k npm start" not in default_branch
    assert "--hardware" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows batch help smoke test")
def test_start_help_has_no_runner_or_backend_side_effect(tmp_path):
    source = Path(__file__).resolve().parents[1] / "start.bat"
    script = tmp_path / "launcher with spaces.bat"
    script.write_bytes(source.read_bytes())
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Default: safe MOCK" in result.stdout
    # The copied directory has no Python/project dependencies or backend files.
    assert sorted(path.name for path in tmp_path.iterdir()) == [script.name]


@pytest.mark.skipif(os.name != "nt", reason="Windows batch isolated launch test")
def test_default_launcher_forces_mock_even_with_hardware_config(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    source = Path(__file__).resolve().parents[1] / "start.bat"
    script = project / "start.bat"
    script.write_bytes(source.read_bytes())
    (project / "src").mkdir()
    (project / "src" / "runner_real.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "Path('observed.json').write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    (project / "config.ini").write_text(TEMPLATE.replace("mock", "dglab"), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script)],
        # CMD's localized PAUSE output can use the OEM code page even when
        # the parent Python runs in UTF-8 mode. These checks need only ASCII
        # markers, so retain bytes instead of decoding in a reader thread.
        input=b"\n", capture_output=True, env=env, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((project / "observed.json").read_text()) == [
        "--enable-trigger", "--sender-mode", "mock",
    ]
    assert b"Starting the local SOCKET" not in result.stdout
