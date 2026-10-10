"""Desktop launch policy and the user's full-endcap calibration regression."""
from pathlib import Path
import json
import subprocess
from types import SimpleNamespace

import cv2
import pytest

from src import desktop_runtime as desktop
from src.config_loader import load_config
from src.vision_pipeline import VisionPipeline
from tools.calibrate import compute_roi_params
from tools.desktop_app import self_check, inspect_images


@pytest.mark.parametrize('size', [(1920,1080),(1280,720),(2560,1440)])
@pytest.mark.parametrize('mode', ['mock','dglab'])
def test_preset_config_is_local_and_resolution_specific(tmp_path,size,mode):
    target=tmp_path/'session.ini'
    desktop.make_config(target,2,size,mode,43210)
    cfg=load_config(target)
    assert cfg['capture']['monitor_index']==2
    assert cfg['calibration']['resolution']==f'{size[0]}x{size[1]}'
    assert cfg['calibration']['source']=='provided_1080p_screenshot_preset'
    assert cfg['trigger']['sender_mode']==mode
    assert cfg['sender']['ws_host']=='127.0.0.1'
    assert cfg['sender']['ws_port']==43210
    assert 0<cfg['ROI']['inner_y2']-cfg['ROI']['inner_y1']<20
    with pytest.raises(FileExistsError):
        desktop.make_config(target,1,size,mode)


@pytest.mark.parametrize('size', [(3440,1440),(100,100),(1920,1200)])
def test_incompatible_preset_fails_without_creating_config(tmp_path,size):
    target=tmp_path/'session.ini'
    with pytest.raises(ValueError):
        desktop.make_config(target,1,size,'mock')
    assert not target.exists()


@pytest.mark.parametrize('filename', ['20260913154935_1.jpg','20260913160405_1.jpg','20260913160406_1.jpg'])
def test_complete_endcap_box_does_not_turn_full_hp_into_zero(filename):
    path=desktop.ROOT/'screenshots/20260913'/filename
    if not path.is_file():
        pytest.skip('Local user screenshot not present')
    config=load_config(desktop.ROOT/'config.example.ini')
    config['ROI'].update(compute_roi_params(105,988,196,24))
    result,roi,health=VisionPipeline(config).process(cv2.imread(str(path)),timestamp=0)
    assert roi.valid
    assert health.health_raw==1 and health.confidence>.9
    assert not result.valid  # A still image must not bypass temporal warmup.


def test_desktop_selfcheck_does_not_require_app_or_capture():
    result=self_check()
    assert result['status']=='ok'
    assert not result['screen_capture_started'] and not result['app_connected']


def test_worker_uses_absolute_own_entry_and_session_paths(tmp_path):
    cmd=desktop.worker_command(tmp_path/'config.ini',tmp_path)
    assert '--worker' in cmd and '--session' in cmd
    assert str(tmp_path/'config.ini') in cmd
    assert 'dghub' not in ' '.join(cmd).lower()


def test_backend_close_only_stops_owned_process():
    calls=[]
    class Process:
        def poll(self): return None
        stdin=SimpleNamespace(write=lambda s:calls.append(s),flush=lambda:None,close=lambda:None)
        def wait(self,timeout): calls.append(('wait',timeout))
    backend=desktop.OfficialBackend('.')
    backend.process=Process()
    backend.close()
    assert calls==['STOP\n',('wait',5)]


def test_worker_stop_requests_graceful_exit_before_forcing(tmp_path):
    calls=[]
    process=SimpleNamespace(wait=lambda timeout:calls.append(timeout))
    desktop.stop_worker(process,tmp_path)
    assert (tmp_path/'STOP').is_file()
    assert calls==[5]


def test_backend_close_tolerates_already_closed_pipe():
    def broken():
        raise BrokenPipeError('already exited')
    backend=desktop.OfficialBackend('.')
    backend.process=SimpleNamespace(poll=lambda:0,stdin=SimpleNamespace(close=broken))
    backend.close()


def test_image_diagnostic_keeps_first_frame_gate():
    path=desktop.ROOT/'screenshots/20260913/20260913154935_1.jpg'
    if not path.is_file(): pytest.skip('Local screenshot not present')
    report=inspect_images([path])
    reading=report['images'][0]
    assert reading['health_raw']==1 and reading['hud_valid']
    assert not reading['first_frame_valid']
    assert reading['first_frame_reason']=='observation_warmup'
    assert not report['app_connected'] and not report['screen_capture_started']


def test_image_diagnostic_rejects_non_image(tmp_path):
    path=tmp_path/'bad.jpg'
    path.write_bytes(b'not an image')
    with pytest.raises(ValueError,match='无法读取截图'): inspect_images([path])
