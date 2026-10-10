"""Trigger 原始 SPEC 与安全边界回归；仅 fake sender，不访问设备或网络。"""
import json
import io
import builtins
import sys
import time
from threading import Event
from types import SimpleNamespace

import pytest

from src import trigger as trigger_module


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Sender:
    def __init__(self):
        self.actions = []
        self.continuous = []
        self.stops = 0
        self.closes = 0
        self.accept = True
        self.accept_continuous = True

    def send(self, action, payload):
        self.actions.append((action, payload))
        return {"dispatched": self.accept}

    def update_continuous(self, **values):
        self.continuous.append(values)
        return {"dispatched": self.accept_continuous}

    def stop(self):
        self.stops += 1

    def close(self):
        self.closes += 1


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setattr(trigger_module, "LOG_PATH", tmp_path / "trigger.jsonl")
    live = []

    def make(**settings):
        clock = Clock()
        sender = Sender()
        dispatch = trigger_module.TriggerDispatch(
            {"trigger": {"sender_mode": "mock", **settings}}, clock=clock)
        live.append(dispatch)
        dispatch._send = sender.send
        dispatch._update_continuous = sender.update_continuous
        dispatch._stop_output = sender.stop
        dispatch._close_sender = sender.close

        def frame(hp, at, *, stable=None, valid=True, state="injured"):
            clock.now = at
            return dispatch.dispatch(SimpleNamespace(
                health_raw=hp, health_stable=hp if stable is None else stable,
                valid=valid, state=state))

        return dispatch, sender, frame

    yield make
    for dispatch in live:
        dispatch.safe_stop()


@pytest.mark.parametrize("valid,state", [(False, "injured"), (True, "unknown"),
                                         (False, "death"), (True, "bad_state")])
def test_rejected_frames_never_start_output(rig, valid, state):
    _, sender, frame = rig()
    for at in (0.0, 0.1, 0.2):
        assert not frame(0, at, valid=valid, state=state)["dispatched"]
    assert sender.actions == sender.continuous == []
    assert sender.stops == 0


@pytest.mark.parametrize("bad_hp", [float("nan"), float("inf"), -0.1, 1.1, None])
def test_bad_hp_is_rejected_and_stops_previous_output(rig, bad_hp):
    _, sender, frame = rig()
    frame(0.5, 0)
    frame(0.5, 0.05)
    assert sender.continuous[-1]["low_health"]
    assert frame(bad_hp, 0.1)["reason"] == "invalid_frame"
    assert sender.stops == 1
    assert sender.actions == []


@pytest.mark.parametrize("next_hp", [0.0, 0.2, 1.0])
def test_invalid_transition_stops_once_and_recovery_is_a_silent_baseline(rig, next_hp):
    dispatch, sender, frame = rig()
    frame(0.8, 0)
    frame(0.7, 0.05)  # 伤害窗口尚未到期。
    frame(0.7, 0.1, valid=False)
    frame(0.7, 0.15, state="unknown")
    assert sender.stops == 1
    before = len(sender.continuous)
    assert frame(next_hp, 0.2)["reason"] == "baseline"
    assert len(sender.continuous) == before
    frame(next_hp, 0.5)
    assert sender.actions == []
    assert dispatch._injury_started is None


def test_pause_reset_cancels_candidates_without_disconnect_or_cooldown_reset(rig):
    dispatch, sender, frame = rig(healing_cooldown_sec=5.0)
    frame(0.5, 0)
    assert frame(0.6, 0.05)["actions"] == ["heal_pulse"]
    dispatch.reset("paused")
    assert sender.stops == 1 and sender.closes == 0
    frame(0.2, 0.1)
    assert frame(0.3, 0.15)["reason"] == "cooldown"
    assert [name for name, _ in sender.actions] == ["heal_pulse"]


def test_missing_frames_break_history_before_recovery(rig):
    _, sender, frame = rig(max_frame_gap_sec=0.5)
    frame(1.0, 0)
    frame(0.8, 0.05)
    assert frame(0.2, 0.7)["reason"] == "baseline"
    frame(0.2, 0.8)
    assert sender.stops == 1
    assert sender.actions == []


def test_fixed_injury_window_sums_damage_once_and_keeps_continuous_layer(rig):
    _, sender, frame = rig()
    frame(1.0, 0)
    frame(0.9, 0.01)
    frame(0.7, 0.1)
    assert sender.actions == []
    result = frame(0.7, 0.21)
    assert result["actions"] == ["injury_pulse"]
    assert sender.actions[0][1]["damage_pct"] == pytest.approx(0.3)
    assert sender.continuous[-1]["low_health"]
    for at in (0.3, 0.5, 0.7):
        frame(0.7, at)
    assert len(sender.actions) == 1


def test_new_damage_after_window_is_not_lost_to_default_cooldown(rig):
    _, sender, frame = rig()
    frame(1, 0)
    frame(0.9, 0.01)
    frame(0.9, 0.21)
    frame(0.8, 0.22)
    frame(0.8, 0.42)
    assert [name for name, _ in sender.actions] == ["injury_pulse", "injury_pulse"]


def test_raw_hp_prevents_ema_from_splitting_a_single_injury(rig):
    _, sender, frame = rig()
    frame(1, 0)
    frame(0.5, 0.01, stable=0.9)
    frame(0.5, 0.11, stable=0.8)
    frame(0.5, 0.21, stable=0.7)
    frame(0.5, 0.41, stable=0.6)
    frame(0.5, 0.61, stable=0.5)
    assert [name for name, _ in sender.actions] == ["injury_pulse"]
    assert sender.actions[0][1]["damage_pct"] == pytest.approx(0.5)


def test_damage_and_healing_in_same_window_are_both_preserved(rig):
    _, sender, frame = rig()
    frame(0.9, 0)
    frame(0.8, 0.01)
    frame(0.9, 0.05)
    frame(0.7, 0.1)
    result = frame(0.7, 0.21)
    assert [name for name, _ in sender.actions] == ["heal_pulse", "injury_pulse"]
    assert sender.actions[-1][1]["damage_pct"] == pytest.approx(0.3)
    assert result["low_health"] and result["healing_sustain"]


def test_injury_window_does_not_extend_when_a_later_drop_arrives(rig):
    _, sender, frame = rig()
    frame(1, 0)
    frame(0.9, 0.01)
    frame(0.7, 0.25)
    assert sender.actions[0][1]["damage_pct"] == pytest.approx(0.1)
    frame(0.7, 0.45)
    assert sender.actions[1][1]["damage_pct"] == pytest.approx(0.2)


def test_injury_window_and_noise_threshold_are_configurable(rig):
    _, sender, frame = rig(injury_window_sec=0.1, injury_delta_threshold=0.02)
    frame(1, 0)
    frame(0.99, 0.01)
    frame(0.99, 0.11)
    assert sender.actions == []
    frame(0.9, 0.12)
    frame(0.9, 0.22)
    assert [name for name, _ in sender.actions] == ["injury_pulse"]


def test_first_near_zero_frame_is_not_death_and_one_percent_is_alive(rig):
    _, sender, frame = rig()
    frame(0.01, 0, state="death")
    frame(0.01, 0.1, state="injured")
    assert sender.actions == []
    assert sender.continuous[-1]["low_health"]


def test_death_once_per_life_and_low_health_never_overwrites_death(rig):
    _, sender, frame = rig()
    frame(0.5, 0)
    result = frame(0, 0.01, state="death")
    assert result["actions"] == ["death_pulse"]
    for at in (0.1, 0.2, 0.3, 0.4):
        frame(0, at, state="death")
    assert [name for name, _ in sender.actions] == ["death_pulse"]
    assert all(not update["low_health"] and not update["healing"]
               for update in sender.continuous)
    assert frame(1, 0.5, state="healing")["actions"] == ["heal_pulse"]
    assert frame(0, 0.6, state="death")["actions"] == ["death_pulse"]


def test_initial_death_page_and_death_label_alone_do_not_trigger_death(rig):
    _, sender, frame = rig()
    frame(0, 0, state="death")
    frame(0, 0.1, state="death")
    assert sender.actions == []
    # 状态显示仍滞后为 death，但通过门控的 raw HP 已复活，应确认治疗。
    assert frame(0.8, 0.2, state="death")["actions"] == ["heal_pulse"]


def test_death_preempts_pending_injury_and_healing_sustain(rig):
    dispatch, sender, frame = rig()
    frame(0.5, 0)
    frame(0.8, 0.01)
    frame(0.4, 0.05)
    assert frame(0, 0.1, state="death")["actions"] == ["death_pulse"]
    assert dispatch._injury_started is None
    assert sender.continuous[-1] == {"low_health": False, "healing": False, "hp": 0.0}
    frame(0, 0.4, state="death")
    assert [name for name, _ in sender.actions] == ["heal_pulse", "death_pulse"]


def test_continuous_low_health_uses_75_percent_raw_hp_and_renews_each_frame(rig):
    _, sender, frame = rig()
    frame(0.8, 0)
    assert sender.continuous == []  # 第一帧保持静默。
    frame(0.75, 0.1, stable=0.8)
    frame(0.749, 0.2, stable=0.8)
    frame(0.749, 0.25, stable=0.8)
    assert [item["low_health"] for item in sender.continuous] == [False, True, True]
    assert sender.actions == []  # 不再定时重复 low_health 离散动作。


@pytest.mark.parametrize("recovered_hp", [0.75, 0.8])
def test_recovery_above_threshold_stops_low_immediately_despite_ema(rig, recovered_hp):
    _, sender, frame = rig()
    frame(0.749, 0, stable=0.4)
    frame(0.749, 0.1, stable=0.4)
    assert sender.continuous[-1]["low_health"]
    frame(recovered_hp, 0.2, stable=0.4)
    assert not sender.continuous[-1]["low_health"]


def test_death_lease_uses_raw_zero_even_while_ema_is_positive(rig):
    _, sender, frame = rig()
    frame(0.5, 0)
    frame(0, 0.1, stable=0.4, state="death")
    frame(0, 0.2, stable=0.3, state="death")
    assert sender.continuous[-1]["hp"] == 0
    assert [name for name, _ in sender.actions] == ["death_pulse"]


def test_healing_confirms_once_then_sustains_for_configured_hold(rig):
    _, sender, frame = rig(healing_sustain_sec=0.4)
    frame(0.5, 0)
    assert frame(0.6, 0.1)["actions"] == ["heal_pulse"]
    assert frame(0.7, 0.2)["actions"] == []
    assert frame(0.7, 0.5)["healing_sustain"]
    assert not frame(0.7, 0.7)["healing_sustain"]
    assert frame(0.8, 0.8)["actions"] == ["heal_pulse"]


def test_damage_rearms_healing_confirmation_without_interrupting_sustain(rig):
    _, sender, frame = rig()
    frame(0.5, 0)
    assert frame(0.6, 0.1)["actions"] == ["heal_pulse"]
    assert frame(0.7, 0.2)["actions"] == []
    assert frame(0.6, 0.3)["healing_sustain"]
    assert frame(0.7, 0.4)["actions"] == ["heal_pulse"]
    assert frame(0.8, 0.45)["actions"] == []
    assert [name for name, _ in sender.actions] == ["heal_pulse", "heal_pulse"]


def test_sender_failure_is_not_reported_as_success_or_cooled_down(rig):
    dispatch, sender, frame = rig(injury_window_sec=0)
    sender.accept = False
    frame(1, 0)
    result = frame(0.9, 0.1)
    assert not result["dispatched"] and result["reason"] == "sender_rejected"
    assert "injury_pulse" not in dispatch._last_fired
    sender.accept = True
    assert frame(0.8, 0.2)["dispatched"]


def test_sender_exception_stops_output_without_crashing(rig):
    dispatch, sender, frame = rig(injury_window_sec=0)
    frame(1, 0)

    def broken(*args):
        raise RuntimeError("offline sender failure")

    dispatch._send = broken
    result = frame(0.5, 0.1)
    assert not result["dispatched"] and result["reason"] == "sender_error"
    assert sender.stops == 1
    assert dispatch._last_hp == -1


def test_continuous_rejection_stops_and_does_not_send_discrete_action(rig):
    _, sender, frame = rig(injury_window_sec=0)
    frame(1, 0)
    sender.accept_continuous = False
    assert frame(0.5, 0.1)["reason"] == "continuous_rejected"
    assert sender.stops == 1 and sender.actions == []


def test_monotonic_clock_is_injected_and_wall_time_is_not_consulted(rig, monkeypatch):
    _, sender, frame = rig()
    monkeypatch.setattr(trigger_module, "time", SimpleNamespace(
        time=lambda: pytest.fail("wall clock used"), monotonic=time.monotonic))
    frame(1, 0)
    frame(0.9, 0.01)
    frame(0.9, 0.21)
    assert len(sender.actions) == 1


def test_safe_stop_is_terminal_idempotent_and_closes_before_log(rig):
    dispatch, sender, frame = rig()
    frame(0.5, 0)
    frame(0.6, 0.1)
    order = []
    dispatch._close_sender = lambda: order.append("close")
    dispatch._write_log = lambda entry: order.append(entry["type"])
    dispatch.safe_stop()
    dispatch.safe_stop()
    before = (len(sender.actions), len(sender.continuous))
    assert frame(0, 0.2)["reason"] == "stopped"
    assert (len(sender.actions), len(sender.continuous)) == before
    assert order == ["close", "session_end"]


def test_unwritable_log_does_not_prevent_shutdown(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(trigger_module, "LOG_PATH", tmp_path)  # 目录不可作为日志文件。
    dispatch, sender, _ = rig()
    dispatch.safe_stop()
    assert sender.closes == 1


def test_jsonl_records_rejections_and_accumulated_damage(rig):
    dispatch, _, frame = rig()
    frame(1, 0)
    frame(0.8, 0.01)
    frame(0.8, 0.21)
    frame(0.8, 0.3, valid=False)
    dispatch._log_writer.close()
    entries = [json.loads(line) for line in trigger_module.LOG_PATH.read_text().splitlines()]
    assert entries[-1]["reason"] == "invalid_frame"
    assert entries[-2]["damage_pct"] == pytest.approx(0.2)


def test_slow_log_io_uses_bounded_queue_and_does_not_block_append_or_close(monkeypatch,
                                                                        tmp_path):
    entered = Event()
    release = Event()

    def slow_open(*args, **kwargs):
        entered.set()
        release.wait(timeout=2.0)
        return io.StringIO()

    monkeypatch.setattr(trigger_module, "open", slow_open, raising=False)
    writer = trigger_module._AsyncJsonlWriter(tmp_path / "slow.jsonl")
    try:
        assert entered.wait(timeout=1.0)
        for index in range(1000):
            writer.append({"frame": index})
        assert writer._queue.qsize() <= writer._QUEUE_SIZE
        started = time.monotonic()
        writer.close()
        assert time.monotonic() - started < 0.75
    finally:
        release.set()
        writer._thread.join(timeout=1.0)


@pytest.mark.parametrize("settings", [
    {"injury_window_sec": -1}, {"max_frame_gap_sec": 0},
    {"healing_sustain_sec": float("inf")}, {"death_hp_threshold": -0.01},
    {"low_health_hp_threshold": 1.1}, {"injury_delta_threshold": float("nan")},
])
def test_unsafe_configuration_is_rejected(rig, settings):
    with pytest.raises(ValueError):
        rig(**settings)


@pytest.mark.parametrize("failure", [False, ConnectionError("offline"), KeyboardInterrupt()])
def test_sender_initialization_failure_closes_sender_and_log(monkeypatch, failure):
    closed = []
    monkeypatch.setattr(trigger_module, "_AsyncJsonlWriter", lambda path: SimpleNamespace(
        append=lambda entry: None, close=lambda: closed.append("log")))

    def wait_for_pair(**kwargs):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setitem(sys.modules, "websocket", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "src.dglab_sender", SimpleNamespace(
        init_sender=lambda config: None, send_action=lambda *args: {"dispatched": True},
        wait_for_pair=wait_for_pair, stop_output=lambda: None,
        safe_stop=lambda: closed.append("sender"),
        update_continuous=lambda **kwargs: {"dispatched": True}))
    expected = type(failure) if isinstance(failure, BaseException) else ConnectionError
    with pytest.raises(expected):
        trigger_module.TriggerDispatch({"trigger": {"sender_mode": "dglab"}})
    assert closed == ["sender", "log"]


def test_missing_sender_dependency_does_not_leak_log_writer(monkeypatch):
    closed = []
    monkeypatch.setattr(trigger_module, "_AsyncJsonlWriter", lambda path: SimpleNamespace(
        append=lambda entry: None, close=lambda: closed.append("log")))
    original_import = builtins.__import__

    def offline_import(name, *args, **kwargs):
        if name == "websocket":
            raise ImportError("offline missing dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", offline_import)
    with pytest.raises(RuntimeError, match="websocket-client"):
        trigger_module.TriggerDispatch({"trigger": {"sender_mode": "dglab"}})
    assert closed == ["log"]
