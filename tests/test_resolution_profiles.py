"""Extrapolated geometry is not evidence of native 1440p/4K gameplay."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.config_loader import load_config
from src.desktop_runtime import make_config
from src.hud_profiles import (RESOLUTION, SCALES, SUPPORTED_RESOLUTIONS,
                              get_profile, source_label, validate_hud_section)
from src.hud_settings import EXPERIMENTAL_KEY, HudSettingsStore
from src.vision_pipeline import VisionPipeline
from tools.resolution_fixtures import synthetic_frame

ROOT = Path(__file__).resolve().parents[1]
COMBINATIONS = [(size, scale) for size in SUPPORTED_RESOLUTIONS for scale in SCALES]


def config(scale, size):
    base = load_config(ROOT / "config.example.ini")
    profile = get_profile(scale, size)
    base["ROI"], base["hud"] = profile["roi"], profile["hud"]
    return base


@pytest.mark.parametrize("size,scale", COMBINATIONS)
def test_thirty_three_profiles_validate_without_changing_default_api(size, scale):
    profile = get_profile(scale, size)
    assert validate_hud_section(profile["hud"], profile["roi"]) == profile
    roi, hud = profile["roi"], profile["hud"]
    assert 0 <= roi["outer_x1"] < roi["inner_x1"] < roi["inner_x2"] < roi["outer_x2"] <= size[0]
    assert 0 <= roi["outer_y1"] < roi["inner_y1"] < roi["inner_y2"] < roi["outer_y2"] <= size[1]
    if size == RESOLUTION:
        assert profile == get_profile(scale)
    else:
        assert hud["source"] == "resolution_extrapolated" and hud["base_frame_width"] == 1920
        assert "未实机验收" in source_label(scale, size) and "已有满血实拍" not in source_label(scale, size)


@pytest.mark.parametrize("size,left,right,top,bottom,thickness,tolerance,spacing", [
    ((2560, 1440), 157, 444, 1303, 1340, 5, 3, 4),
    ((3840, 2160), 236, 666, 1954, 2010, 8, 4, 6)])
def test_independent_golden_pixels(size, left, right, top, bottom, thickness, tolerance, spacing):
    hud = get_profile(100, size)["hud"]
    assert (hud["cap_left_x"], hud["cap_right_x"], hud["cap_top"], hud["cap_bottom"], hud["cap_stem_width"]) == (left,right,top,bottom,thickness)
    assert hud["position_tolerance_px"] == hud["center_tolerance_px"] == tolerance
    assert hud["spacing_tolerance_px"] == spacing


@pytest.mark.parametrize("size,scale", COMBINATIONS)
@pytest.mark.parametrize("hp", [1., .8, .75, .3, .01, 0.])
def test_independent_synthetic_original_pixels_measure_and_warm_up(size, scale, hp):
    pipeline = VisionPipeline(config(scale, size))
    frame = synthetic_frame(scale, size, hp)
    result, roi, health = pipeline.process(frame, 0)
    assert roi.valid and not result.valid
    width = pipeline.health_detector.bar_max_x - pipeline.health_detector.bar_min_x
    assert health.health_raw == pytest.approx(hp, abs=2 / width)
    pipeline.process(frame, .1)
    result, _, _ = pipeline.process(frame, .2)
    assert result.valid


@pytest.mark.parametrize("size,scale", COMBINATIONS)
def test_wrong_hud_profiles_shift_blank_and_cap_occlusion_reject(size, scale):
    frame = synthetic_frame(scale, size)
    for selected in SCALES:
        result, roi, _ = VisionPipeline(config(selected, size)).process(frame, 0)
        assert roi.valid is (selected == scale)
    pipeline = VisionPipeline(config(scale, size))
    assert not pipeline.process(np.zeros_like(frame), .1)[1].valid
    shifted = synthetic_frame(scale, size, shift=(12, 0))
    assert not pipeline.process(shifted, .2)[1].valid
    missing = frame.copy()
    hud = get_profile(scale, size)["hud"]
    missing[hud["cap_top"]:hud["cap_bottom"], hud["cap_left_x"]-12:hud["cap_left_x"]+16] = 20
    assert not pipeline.process(missing, .3)[1].valid


@pytest.mark.parametrize("bad", [(1919,1079), (1280,720), (3440,1440), (2048,1080), (True,1080), "1920x1080", None])
def test_other_sizes_do_not_silently_scale(bad):
    with pytest.raises(ValueError):
        get_profile(100, bad)


def test_schema_one_loads_false_without_rewriting_and_failure_preserves_ack(tmp_path, monkeypatch):
    path = tmp_path / "hud_settings.ini"
    original = "[hud]\nschema_version=1\nmode=manual\nscale_percent=95\n"
    path.write_text(original)
    store = HudSettingsStore(path)
    assert store.snapshot()[EXPERIMENTAL_KEY] is False and path.read_text() == original
    store.save(store.values | {EXPERIMENTAL_KEY: True})
    before = path.read_bytes()
    monkeypatch.setattr("src.hud_settings.os.replace", lambda *a: (_ for _ in ()).throw(OSError("owned failure")))
    with pytest.raises(OSError):
        store.save(store.values | {EXPERIMENTAL_KEY: False})
    assert path.read_bytes() == before and store.snapshot()[EXPERIMENTAL_KEY]


@pytest.mark.parametrize("size", SUPPORTED_RESOLUTIONS[1:])
def test_live_requires_ack_but_mock_screenshot_preview_does_not(tmp_path, size):
    values = {"mode": "manual", "scale_percent": 100}
    with pytest.raises(ValueError, match="实验性"):
        make_config(tmp_path / "blocked.ini", 1, size, "mock", hud_settings=values)
    preview = load_config(make_config(tmp_path / "preview.ini", 1, size, "mock", hud_settings=values, preview=True))
    assert preview["hud"]["frame_width"] == size[0]
    active = load_config(make_config(tmp_path / "active.ini", 1, size, "dglab", hud_settings=values | {EXPERIMENTAL_KEY: True}))
    assert active["calibration"][EXPERIMENTAL_KEY] == 1 and active["hud"] == preview["hud"]
    with pytest.raises(ValueError, match="Mock"):
        make_config(tmp_path / "invalid.ini", 1, size, "dglab", hud_settings=values, preview=True)


@pytest.mark.parametrize("size", SUPPORTED_RESOLUTIONS[1:])
def test_sdk_defers_one_actual_snapshot_and_pending_ack_does_not_apply(size):
    from dghub_plugin.sdk_session import SDKSession
    from test_dghub_sdk import receive, settings, catalog_fixture
    snapshots = []
    values = settings() | {EXPERIMENTAL_KEY: True}
    client = SDKSession(load_config(ROOT / "config.example.ini"), require_capture_binding=True,
        preset_catalog=catalog_fixture(), available_presets=set(catalog_fixture()),
        log_writer=SimpleNamespace(append=lambda row: None, configured=lambda v,c: snapshots.append(deepcopy(c))))
    try:
        client.hello("test-only")
        receive(client, op="hello_ack", accepted=True, sdk_version="1.0")
        receive(client, op="config", data=values)
        assert not snapshots and client.vision is None
        client.set_running(True)
        assert not client.armed
        client.bind_capture_geometry(size)
        assert len(snapshots) == 1 and snapshots[0]["hud"]["frame_width"] == size[0]
        client.bind_capture_geometry(size)
        receive(client, op="config_changed", key=EXPERIMENTAL_KEY, value=False)
        assert client.settings[EXPERIMENTAL_KEY] and client.pending[EXPERIMENTAL_KEY] is False
        assert len(snapshots) == 1 and client.capture_resolution == size
        client.bind_capture_geometry(RESOLUTION)
        assert client.closed and not client.armed
    finally:
        client.close()


@pytest.mark.parametrize("bad", [None, 0, 1, "1", "true", "false", [], {}])
def test_experimental_ack_is_strict_boolean_in_both_stores(tmp_path,bad):
    from dghub_plugin.sdk_config import validate
    with pytest.raises(ValueError):
        validate({EXPERIMENTAL_KEY:bad})
    with pytest.raises(ValueError):
        HudSettingsStore(tmp_path/'hud_settings.ini').save(dict(mode='manual',scale_percent=100,**{EXPERIMENTAL_KEY:bad}))


@pytest.mark.parametrize("size", SUPPORTED_RESOLUTIONS[1:])
def test_missing_sdk_ack_never_creates_core_snapshot_or_nonzero(size):
    from dghub_plugin.sdk_session import SDKSession
    from test_dghub_sdk import receive,settings,messages
    saved=[]
    client=SDKSession(load_config(ROOT/'config.example.ini'),require_capture_binding=True,
        log_writer=SimpleNamespace(configured=lambda *args:saved.append(args),append=lambda row:None))
    client.hello('test-only')
    receive(client,op='hello_ack',accepted=True,sdk_version='1.0')
    receive(client,op='config',data=settings())
    batch=client.bind_capture_geometry(size)
    assert client.closed and not client.armed and client.vision is None and not saved
    assert all(p.get('op') not in {'trigger','pulse'} and not p.get('pct',0) for p in messages(batch))
