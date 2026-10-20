"""HUD selection reaches the production config and offline diagnostic interface."""
from pathlib import Path
import cv2
import pytest

from src.config_loader import load_config
from src.desktop_runtime import ROOT, make_config
from src.hud_profiles import SCALES, get_profile
from tools.desktop_app import inspect_images


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("mode", ["mock","dglab"])
def test_hud_selection_reaches_session_snapshot(tmp_path,scale,mode):
    settings={"mode":"manual","scale_percent":scale}
    target=make_config(tmp_path/"session.ini",1,(1920,1080),mode,hud_settings=settings)
    cfg=load_config(target)
    assert cfg["ROI"] == get_profile(scale)["roi"]
    assert cfg["hud"]["scale_percent"] == scale
    assert cfg["hud"]["source"] == get_profile(scale)["hud"]["source"]
    assert cfg["trigger"]["sender_mode"] == mode
    assert cfg["filters"]["edge_region_1"] == load_config(ROOT/"config.example.ini")["filters"]["edge_region_1"]
    settings["scale_percent"] = 75 if scale != 75 else 125
    assert load_config(target)["hud"]["scale_percent"] == scale


@pytest.mark.parametrize("size",[(1919,1079),(1280,720),(2560,1440),(3840,2160)])
def test_manual_hud_rejects_other_dimensions_without_creating_session(tmp_path,size):
    target=tmp_path/"session.ini"
    with pytest.raises(ValueError):
        make_config(target,1,size,"mock",hud_settings={"mode":"manual","scale_percent":100})
    assert not target.exists()


def test_explicit_legacy_preserves_original_config(tmp_path):
    left=load_config(make_config(tmp_path/"old.ini",1,(1920,1080),"mock"))
    right=load_config(make_config(tmp_path/"new.ini",1,(1920,1080),"mock",
                                hud_settings={"mode":"legacy","scale_percent":100}))
    assert left == right and "hud" not in right


def test_offline_report_rejects_auxiliary_size_and_keeps_single_frame_warmup():
    folder=ROOT/"screenshots/hud_1080p_20261002"
    paths=[folder/"hud_100_full.jpg",folder/"hud_100_aux_1919x1079.png"]
    report=inspect_images(paths,hud_settings={"mode":"manual","scale_percent":100})
    correct,wrong=report["images"]
    assert correct["hud_valid"] and not correct["first_frame_valid"]
    assert correct["first_frame_reason"] == "observation_warmup"
    assert wrong["reject_reason"] == "resolution_mismatch" and wrong["health_raw"] is None
    assert not report["app_connected"] and not report["screen_capture_started"]


def test_mock_runner_size_change_stops_before_processing(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from src import runner_real as runner
    target=make_config(tmp_path/"session.ini",1,(1920,1080),"mock",
                       hud_settings={"mode":"manual","scale_percent":100})
    import numpy as np
    events=[]
    capture=SimpleNamespace(is_ready=True,monitor_size=(1920,1080),
                            grab=lambda:np.zeros((720,1280,3),np.uint8),
                            close=lambda:events.append("capture.close"))
    monkeypatch.setattr(runner,"ScreenCapture",lambda **kw:capture)
    trigger=SimpleNamespace(reset=lambda reason:events.append(reason),safe_stop=lambda:events.append("stop"))
    monkeypatch.setattr(runner,"TriggerDispatch",lambda cfg:trigger)
    monkeypatch.setattr(runner,"_key_down",lambda vk: vk==0x77)
    monkeypatch.setattr(runner.cv2,"destroyAllWindows",lambda:events.append("windows.close"))
    with pytest.raises(RuntimeError,match="分辨率"):
        runner.main(["--config",str(target),"--enable-trigger","--no-global-escape"])
    assert "stop" in events and events[-2:]==["capture.close","windows.close"]
