"""Exercise actual runner control flow with capture/UI/sender replaced by fakes."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.config_loader import load_config
from src.state_machine import DetectionResult


def test_desktop_pairing_failure_does_not_silently_become_monitor(monkeypatch):
    from src import runner_real as runner
    events=[]
    capture=SimpleNamespace(is_ready=True,monitor_size=(1920,1080),
                            close=lambda:events.append('capture.close'))
    def failed(config):
        raise ConnectionError('software APP timeout')
    monkeypatch.setattr(runner,'ScreenCapture',lambda **kw:capture)
    monkeypatch.setattr(runner,'TriggerDispatch',failed)
    monkeypatch.setattr(runner.cv2,'destroyAllWindows',lambda:events.append('gui.close'))
    with pytest.raises(RuntimeError,match='APP 配对失败'):
        runner.main(['--enable-trigger','--sender-mode','mock','--require-output'])
    assert events==['capture.close','gui.close']


def test_capture_failure_stops_output_and_escape_still_exits(monkeypatch):
    from src import runner_real as runner
    events = []
    capture = SimpleNamespace(is_ready=True, monitor_size=(1920, 1080),
                              grab=lambda: None, close=lambda: events.append("capture.close"))
    trigger = SimpleNamespace(reset=lambda reason: events.append(reason),
                              safe_stop=lambda: events.append("safe_stop"))
    monkeypatch.setattr(runner, "ScreenCapture", lambda **kw: capture)
    monkeypatch.setattr(runner, "TriggerDispatch", lambda config: trigger)
    escaped = iter([False, True])
    monkeypatch.setattr(runner, "_key_down", lambda vk: next(escaped) if vk == 0x1B else True)
    monkeypatch.setattr(runner, "install_panel_controls", lambda *a: False)
    monkeypatch.setattr(runner, "set_panel_topmost", lambda *a, **kw: True)
    monkeypatch.setattr(runner.cv2, "imshow", lambda *a: None)
    monkeypatch.setattr(runner.cv2, "waitKey", lambda *a: 0)
    monkeypatch.setattr(runner.cv2, "destroyAllWindows", lambda: events.append("gui.close"))
    runner.main(["--enable-trigger", "--sender-mode", "mock", "--fps", "0"])
    assert events == ["resumed", "capture_failed", "safe_stop", "capture.close", "gui.close"]


def test_cleanup_error_cannot_skip_output_shutdown(monkeypatch):
    from src import runner_real as runner
    events = []
    def failed_close():
        events.append("capture.close")
        raise RuntimeError("fake capture cleanup failure")
    capture = SimpleNamespace(is_ready=True, monitor_size=(1920, 1080), close=failed_close)
    trigger = SimpleNamespace(safe_stop=lambda: events.append("safe_stop"))
    monkeypatch.setattr(runner, "ScreenCapture", lambda **kw: capture)
    monkeypatch.setattr(runner, "TriggerDispatch", lambda config: trigger)
    monkeypatch.setattr(runner, "_key_down", lambda vk: True)
    monkeypatch.setattr(runner.cv2, "destroyAllWindows", lambda: events.append("gui.close"))
    with pytest.raises(RuntimeError, match="cleanup"):
        runner.main(["--enable-trigger", "--sender-mode", "mock"])
    assert events == ["safe_stop", "capture.close", "gui.close"]


def test_mock_forces_no_hardware_and_dispatches_even_without_event(monkeypatch):
    from src import runner_mock as runner, trigger, vision_pipeline
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.ini")
    config["trigger"]["sender_mode"] = "dglab"
    modes, observations, stops = [], [], []
    fake_trigger = SimpleNamespace(reset=lambda *a: None, dispatch=observations.append,
                                   safe_stop=lambda: stops.append(True))
    def make_trigger(cfg):
        modes.append(cfg["trigger"]["sender_mode"])
        return fake_trigger
    result = DetectionResult(health_raw=1, health_stable=1, valid=True, state="full_health", event="none")
    fake_pipeline = SimpleNamespace(reset=lambda: None, process=lambda frame: (result, None, None))
    monkeypatch.setattr(trigger, "TriggerDispatch", make_trigger)
    monkeypatch.setattr(vision_pipeline, "VisionPipeline", lambda cfg: fake_pipeline)
    monkeypatch.setattr("src.config_loader.load_config", lambda *a: config)
    monkeypatch.setattr(runner, "_collect_images", lambda path: [("full", [Path("fake.jpg")])])
    monkeypatch.setattr(runner.cv2, "imread", lambda *a: np.zeros((2, 2, 3), np.uint8))
    runner.main(["--interval", "0"])
    assert modes == ["mock"]
    assert observations == [result]
    assert stops == [True]


def test_pairing_script_import_has_no_side_effects(monkeypatch):
    import importlib.util
    import src.dglab_sender as sender
    def forbidden(*a, **kw):
        raise AssertionError("A hardware operation was attempted during import")
    monkeypatch.setattr(sender, "init_sender", forbidden)
    path = Path(__file__).resolve().parents[1] / "tools" / "test_pairing.py"
    spec = importlib.util.spec_from_file_location("isolated_pairing_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit):
        module.main([])


@pytest.mark.parametrize("calibration,size", [
    ({"calibrated": 0, "resolution": "1920x1080"}, (1920, 1080)),
    ({"calibrated": 1, "resolution": "2560x1440"}, (1920, 1080)),
    ({"calibrated": 1}, (1920, 1080)),
])
def test_direct_hardware_entry_rejects_missing_or_wrong_calibration(monkeypatch, calibration, size):
    from src import runner_real as runner
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.ini")
    config["calibration"] = calibration
    events = []
    capture = SimpleNamespace(is_ready=True, monitor_size=size,
                              close=lambda: events.append("capture.close"))
    monkeypatch.setattr(runner, "load_config", lambda *a: config)
    monkeypatch.setattr(runner, "ScreenCapture", lambda **kw: capture)
    def forbidden(*a):
        raise AssertionError("Pairing must not start before calibration validation")
    monkeypatch.setattr(runner, "TriggerDispatch", forbidden)
    with pytest.raises(SystemExit) as exc:
        runner.main(["--enable-trigger", "--sender-mode", "dglab"])
    assert exc.value.code == 2
    assert events == ["capture.close"]


def test_resolution_change_stops_hardware_before_processing_frame(monkeypatch):
    from src import runner_real as runner
    from src import dglab_sender
    monkeypatch.setattr(dglab_sender, "is_paired", lambda: True)
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.ini")
    config["calibration"]["calibrated"] = 1
    events = []
    capture = SimpleNamespace(is_ready=True, monitor_size=(1920, 1080),
                              grab=lambda: np.zeros((720, 1280, 3), np.uint8),
                              close=lambda: events.append("capture.close"))
    trigger = SimpleNamespace(reset=lambda reason: events.append(reason),
                              safe_stop=lambda: events.append("safe_stop"))
    monkeypatch.setattr(runner, "load_config", lambda *a: config)
    monkeypatch.setattr(runner, "ScreenCapture", lambda **kw: capture)
    monkeypatch.setattr(runner, "TriggerDispatch", lambda config: trigger)
    monkeypatch.setattr(runner, "_key_down", lambda vk: vk == runner.ARM_HOTKEY_VK)
    monkeypatch.setattr(runner, "install_panel_controls", lambda *a: False)
    monkeypatch.setattr(runner, "set_panel_topmost", lambda *a, **kw: True)
    monkeypatch.setattr(runner.cv2, "destroyAllWindows", lambda: events.append("gui.close"))
    with pytest.raises(RuntimeError, match="分辨率"):
        runner.main(["--enable-trigger", "--sender-mode", "dglab"])
    assert events == ["resumed", "safe_stop", "capture.close", "gui.close"]


def test_calibration_validation_accepts_current_monitor_dimensions():
    from src.runner_real import _hardware_calibration_error
    config = {"calibration": {"calibrated": 1, "resolution": "1920x1080"}}
    assert _hardware_calibration_error(config, (1920, 1080)) == ""


def _run_control_sequence(monkeypatch, hotkeys, mouse_events, *, failure=None,
                          compact=False, close_at=None, death_at=None, rejected_at=None):
    """Run the real main loop with deterministic input and no capture/device IO."""
    import cv2
    from src import runner_real as runner
    from src.control_panel import PanelController

    controller = PanelController()
    controller.compact = compact
    events = []
    iteration = 0
    frame = np.zeros((2, 2, 3), np.uint8)
    result = DetectionResult(health_raw=0.5, health_stable=0.5,
                             valid=True, state="injured")

    def key_down(vk):
        nonlocal iteration
        if vk == 0x1B:
            iteration += 1
            return iteration > len(hotkeys)
        return hotkeys[iteration - 1]

    def pump_mouse(*args):
        for event in mouse_events.get(iteration, []):
            if isinstance(event, tuple):
                controller.on_mouse(*event)
            else:
                controller.on_mouse(event, *((247, 28) if controller.compact else (100, 110)))

    def process(image):
        if failure == "pipeline" and iteration == 3:
            raise RuntimeError("simulated vision failure")
        return result, None, None

    capture = SimpleNamespace(
        is_ready=True, monitor_size=(1920, 1080),
        grab=lambda: None if failure == "capture" and iteration == 3 else frame,
        close=lambda: events.append(("capture.close", iteration)),
    )
    pipeline = SimpleNamespace(max_frame_gap=1.0, reset=lambda: None, process=process)
    def dispatch(observation):
        events.append(("dispatch", iteration))
        if iteration == death_at:
            trigger.challenge_failed = True
        return {"reason": "continuous_rejected"} if iteration == rejected_at else None
    def reset_challenge():
        trigger.challenge_failed = False
        events.append(("challenge_reset", iteration))
    trigger = SimpleNamespace(
        reset=lambda reason: events.append((reason, iteration)),
        dispatch=dispatch, challenge_failed=False, reset_challenge=reset_challenge,
        safe_stop=lambda: events.append(("safe_stop", iteration)),
    )
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.ini")
    monkeypatch.setattr(runner, "load_config", lambda *args: config)
    monkeypatch.setattr(runner, "VisionPipeline", lambda config: pipeline)
    monkeypatch.setattr(runner, "ScreenCapture", lambda **kw: capture)
    monkeypatch.setattr(runner, "TriggerDispatch", lambda config: trigger)
    monkeypatch.setattr(runner, "PanelController", lambda: controller)
    monkeypatch.setattr(runner, "_key_down", key_down)
    monkeypatch.setattr(runner, "install_panel_controls", lambda *args: True)
    monkeypatch.setattr(runner, "panel_window_closed", lambda *args: iteration == close_at)
    monkeypatch.setattr(runner, "set_panel_topmost", lambda *args, **kw: True)
    monkeypatch.setattr(cv2, "imshow", lambda *args: None)
    monkeypatch.setattr(cv2, "waitKey", pump_mouse)
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    runner.main(["--enable-trigger", "--sender-mode", "mock", "--fps", "0"])
    return events


def test_real_main_loop_f8_reset_beats_mouse_and_stays_paused(monkeypatch):
    import cv2
    events = _run_control_sequence(monkeypatch, [True, False, False, True, False, True],
        {3: [cv2.EVENT_LBUTTONDOWN, cv2.EVENT_LBUTTONUP]}, death_at=2)
    assert ("challenge_reset", 4) in events
    assert not any(("dispatch", tick) in events for tick in (4, 5))
    assert ("resumed", 6) in events and ("dispatch", 6) in events


def test_real_main_loop_sender_rejection_requires_manual_resume(monkeypatch):
    events = _run_control_sequence(monkeypatch, [True, False, False, False, True], {}, rejected_at=2)
    assert ("continuous_rejected", 2) in events
    assert ("dispatch", 3) not in events and ("dispatch", 4) not in events
    assert ("resumed", 5) in events


def test_simultaneous_hotkey_and_stop_click_never_rearm_next_frame(monkeypatch):
    import cv2
    events = _run_control_sequence(
        monkeypatch, [True, False, True, False, False],
        {2: [cv2.EVENT_LBUTTONDOWN, cv2.EVENT_LBUTTONUP]},
    )
    assert [event for event in events if event[0] in ("resumed", "paused")] == [
        ("resumed", 1), ("paused", 3),
    ]
    assert [event for event in events if event[0] == "dispatch"] == [
        ("dispatch", 1), ("dispatch", 2),
    ]
    assert events[-2:] == [("safe_stop", 6), ("capture.close", 6)]


@pytest.mark.parametrize("transition", ["hotkey", "capture", "pipeline"])
def test_release_after_lock_cannot_rearm_a_gesture_from_running_state(monkeypatch, transition):
    import cv2
    events = _run_control_sequence(
        monkeypatch, [True, False, transition == "hotkey", False, False],
        {2: [cv2.EVENT_LBUTTONDOWN], 4: [cv2.EVENT_LBUTTONUP]},
        failure=transition,
    )
    assert [event for event in events if event[0] == "resumed"] == [("resumed", 1)]
    assert [event for event in events if event[0] == "dispatch"] == [
        ("dispatch", 1), ("dispatch", 2),
    ]
    reason = "paused" if transition == "hotkey" else transition + "_failed"
    assert (reason, 3) in events


def test_fresh_completed_click_can_arm_after_stale_gesture_is_cancelled(monkeypatch):
    import cv2
    events = _run_control_sequence(
        monkeypatch, [True, False, True, False, False, False],
        {2: [cv2.EVENT_LBUTTONDOWN], 4: [cv2.EVENT_LBUTTONUP],
         5: [cv2.EVENT_LBUTTONDOWN, cv2.EVENT_LBUTTONUP]},
    )
    assert [event for event in events if event[0] in ("resumed", "paused")] == [
        ("resumed", 1), ("paused", 3), ("resumed", 6),
    ]


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("failure", ["capture", "pipeline", None])
def test_both_layouts_cancel_stale_release_on_stop(monkeypatch, compact, failure):
    import cv2
    events = _run_control_sequence(
        monkeypatch, [True, False, failure is None, False, False],
        {2: [cv2.EVENT_LBUTTONDOWN], 4: [cv2.EVENT_LBUTTONUP]},
        compact=compact, failure=failure)
    assert [event for event in events if event[0] == "resumed"] == [("resumed", 1)]
    assert events[-2:] == [("safe_stop", 6), ("capture.close", 6)]


@pytest.mark.parametrize("compact", [False, True])
def test_window_close_stops_both_layouts_without_recreating(monkeypatch, compact):
    events = _run_control_sequence(monkeypatch, [True, False, False], {},
                                   compact=compact, close_at=2)
    assert events[-2:] == [("safe_stop", 2), ("capture.close", 2)]


def test_fold_and_f8_same_frame_only_apply_hotkey_once(monkeypatch):
    import cv2
    events = _run_control_sequence(
        monkeypatch, [True, False, True, False, False],
        {2: [(cv2.EVENT_LBUTTONDOWN, 70, 230), (cv2.EVENT_LBUTTONUP, 70, 230)]})
    assert [event for event in events if event[0] in ("resumed", "paused")] == [
        ("resumed", 1), ("paused", 3)]
