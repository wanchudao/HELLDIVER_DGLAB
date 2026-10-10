import sys
from pathlib import Path

import pytest

from dghub_plugin.diagnostics import inspect_images
from dghub_plugin.sdk_config import defaults
from src.config_loader import load_config
from src.hud_profiles import MEASURED_SCALES, SCALES


ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "screenshots/hud_1080p_20261002"


@pytest.mark.parametrize("actual", MEASURED_SCALES)
@pytest.mark.parametrize("selected", SCALES)
def test_offline_dghub_diagnostics_crosscheck_originals_without_event_dispatch(monkeypatch, actual, selected):
    from src.trigger import TriggerDispatch
    monkeypatch.setattr(TriggerDispatch, "__init__", lambda *args, **kw: pytest.fail("Screenshot created sender"))
    report = inspect_images([MATERIALS / f"hud_{actual:03d}_full.jpg"],
                            defaults() | {"hud_scale_percent": selected}, load_config(ROOT / "config.example.ini"))
    assert not report["app_connected"] and not report["screen_capture_started"]
    record = report["images"][0]
    assert record["hud_valid"] is (actual == selected)
    assert not record["first_frame_valid"]
    assert record["hud_scale"] == selected
    if actual == selected:
        assert record["health_raw"] == 1 and record["first_frame_reason"] == "observation_warmup"


def test_offline_dghub_diagnostics_refuses_wrong_image_size():
    report = inspect_images([MATERIALS / "hud_100_aux_1919x1079.png"], defaults(),
                            load_config(ROOT / "config.example.ini"))
    record = report["images"][0]
    assert record["resolution"] == "1919x1079" and record["health_raw"] is None
    assert record["reject_reason"] == "resolution_mismatch" and not record["hud_valid"]


def test_candidate_plain_entry_does_not_register_host_and_inspection_never_imports_transport(monkeypatch, tmp_path):
    import socket
    from tools.dghub_live_entry import main
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("Offline entry allocated socket"))
    assert main([]) == 1  # normal entry requires a valid DGHub launch environment
    report = tmp_path / "inspection.json"
    assert main(["--inspect-images", str(MATERIALS / "hud_125_full.jpg"),
                 "--hud-scale", "125", "--report", str(report)]) == 0
    assert report.is_file()
