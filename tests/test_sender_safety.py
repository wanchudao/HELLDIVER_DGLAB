"""Sender/mixer 回归：假时钟、假 Timer、假 WebSocket，绝不连接硬件。"""

import json
import threading
from types import SimpleNamespace

import pytest

from src import dglab_sender as sender
from src.feedback_mixer import FeedbackMixer, MixedOutput


@pytest.fixture
def rig(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    timers = []

    class FakeTimer:
        def __init__(self, interval, callback, args=()):
            self.interval, self.callback, self.args = interval, callback, args
            self.cancelled = False
            self.daemon = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def fire(self):
            # 故意允许已取消的 timer 触发，模拟 cancel 时回调已在等待发送锁。
            self.callback(*self.args)

    class FakeSocket:
        def __init__(self):
            self.messages = []
            self.closed = False
            self.fail_on = lambda message: False

        def send(self, raw):
            message = json.loads(raw)
            if self.fail_on(message):
                raise OSError("simulated send failure")
            self.messages.append(message)

        def close(self, **kwargs):
            self.closed = True

    socket = FakeSocket()
    client = sender._DglabWSClient("ws://offline.invalid:9999")
    client._ws = socket
    client.client_id, client.target_id = "web", "app"
    client._paired_ready = True
    monkeypatch.setattr(sender, "_client", client)
    monkeypatch.setattr(sender, "_mixer", FeedbackMixer({}))
    monkeypatch.setattr(sender, "_rendered_output", MixedOutput())
    monkeypatch.setattr(sender, "_next_waveform_at", 0.0)
    monkeypatch.setattr(sender, "_rendered_output_b", MixedOutput())
    monkeypatch.setattr(sender, "_next_waveform_at_b", 0.0)
    monkeypatch.setattr(sender, "_safety_timers", {})
    monkeypatch.setattr(sender, "_channel_generation", {"A": 0, "B": 0})
    monkeypatch.setattr(sender, "_operation_generation", 0)
    monkeypatch.setattr(sender, "ACTION_PROFILES", {
        name: profile.copy() for name, profile in sender._DEFAULT_ACTION_PROFILES.items()
    })
    monkeypatch.setattr(sender.threading, "Timer", FakeTimer)
    monkeypatch.setattr(sender.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(sender.time, "sleep", lambda seconds: None)
    return SimpleNamespace(clock=clock, client=client, socket=socket,
                           timers=timers, timer_type=FakeTimer)


def continuous(rig, *, low=True, healing=False, hp=0.5, at=None):
    if at is not None:
        rig.clock.now = at
    return sender.update_continuous(low_health=low, healing=healing, hp=hp)


def tick(rig, at):
    rig.clock.now = at
    sender._safety_timers["A"].fire()


def nonzero_strengths(rig):
    return [m["strength"] for m in rig.socket.messages
            if m["type"] == 3 and m["strength"] > 0]


def test_continuous_and_damage_pulse_add_then_return_to_baseline(rig):
    assert continuous(rig)["dispatched"]
    assert sender.send_action("injury_pulse", {"damage_pct": 0.2})["dispatched"]
    assert nonzero_strengths(rig) == [10, 14]
    # 受伤需重启脉冲模板，但逻辑低血层仍保留；不是无限追加队列。
    assert sum(m["message"].startswith("pulse-A:") for m in rig.socket.messages) == 2
    for at in (1000.5, 1001.0, 1001.5):
        continuous(rig, at=at)
    tick(rig, 1002.01)
    assert sender._rendered_output.strength == 10
    assert sender._mixer.low_health


def test_heal_and_injury_have_independent_lifetimes(rig):
    continuous(rig, healing=True)
    sender.send_action("heal_pulse")
    rig.clock.now = 1000.5
    sender.send_action("injury_pulse", {"damage_pct": 0.2})
    assert sender._rendered_output == MixedOutput(34, "heal_pulse")
    continuous(rig, healing=True, at=1001.0)
    continuous(rig, healing=True, at=1001.5)
    continuous(rig, healing=True, at=1002.1)
    assert sender._rendered_output == MixedOutput(19, "weak_pulse")
    continuous(rig, healing=True, at=1002.6)
    assert sender._rendered_output == MixedOutput(15, "heal_pulse")


def test_death_discards_every_other_layer(rig):
    continuous(rig, healing=True)
    sender.send_action("heal_pulse")
    sender.send_action("injury_pulse", {"damage_pct": 1})
    assert sender.send_action("death_pulse")["dispatched"]
    assert sender._rendered_output == MixedOutput(40, "death_pulse")
    assert not sender._mixer.low_health and not sender._mixer.healing
    assert list(sender._mixer.pulses) == ["death_pulse"]


def test_small_damage_is_one_unit_but_zero_damage_is_zero(rig):
    sender.send_action("injury_pulse", {"damage_pct": 0.0001})
    assert sender._rendered_output.strength == 1
    sender.stop_output()
    rig.socket.messages.clear()
    sender.send_action("injury_pulse", {"damage_pct": 0})
    assert rig.socket.messages == []


def test_equal_later_damage_restarts_pulse_instead_of_only_extending_it(rig):
    continuous(rig)
    sender.send_action("injury_pulse", {"damage_pct": .2})
    before = len(rig.socket.messages)
    rig.clock.now += .3
    sender.send_action("injury_pulse", {"damage_pct": .2})
    added = rig.socket.messages[before:]
    assert any(m.get("message") == "clear-1" for m in added)
    assert any(str(m.get("message")).startswith("pulse-A:") for m in added)
    assert sender._mixer.low_health
    assert sender._rendered_output.strength == 14


@pytest.mark.parametrize("kind, code", [("msg", "400"), ("msg", "402"),
                                        ("msg", "404"), ("msg", "500"), ("error", "500")])
def test_late_backend_rejection_stops_and_invalidates_pair(rig, kind, code):
    continuous(rig)
    rig.client._on_message(rig.socket, json.dumps({"type": kind, "message": code}))
    assert not rig.client.is_paired()
    assert rig.socket.closed
    assert sender._mixer.output(rig.clock.now).strength == 0
    assert sender._safety_timers == {}
    assert not sender.send_action("death_pulse")["dispatched"]


def test_total_strength_is_bounded_and_zero_cap_never_outputs(rig, monkeypatch):
    monkeypatch.setattr(sender, "_mixer", FeedbackMixer({"sender": {"max_strength": 25}}))
    continuous(rig, healing=True)
    sender.send_action("heal_pulse")
    sender.send_action("injury_pulse", {"damage_pct": 1})
    assert sender._rendered_output.strength == 25
    assert max(nonzero_strengths(rig)) <= 25
    sender.stop_output()
    monkeypatch.setattr(sender, "_mixer", FeedbackMixer({"sender": {"max_strength": 0}}))
    rig.socket.messages.clear()
    continuous(rig, healing=True)
    sender.send_action("death_pulse")
    assert rig.socket.messages == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 101, True])
def test_invalid_strength_config_is_rejected(value):
    with pytest.raises(ValueError):
        FeedbackMixer({"sender": {"max_strength": value}})


@pytest.mark.parametrize("cap, expected", [(25.7, 25), (99.9, 99), (0.9, 0)])
def test_fractional_strength_cap_is_never_rounded_up(cap, expected):
    mixer = FeedbackMixer({"sender": {"max_strength": cap, "low_health_strength": 100}})
    mixer.update_continuous(low_health=True, healing=False, hp=0.5, now=1000)
    output = mixer.output(1000)
    assert output.strength == expected
    assert output.strength <= cap


def test_independent_tick_expires_all_layers_when_frames_stop(rig):
    continuous(rig, healing=True)
    sender.send_action("injury_pulse", {"damage_pct": 0.5})
    tick(rig, 1001.01)
    assert sender._rendered_output == MixedOutput()
    assert sender._safety_timers == {}
    assert rig.socket.messages[-2]["message"] == "clear-1"
    assert rig.socket.messages[-1]["strength"] == 0
    assert sender._mixer.pulses == {}


def test_old_timer_cannot_stop_new_action_or_remove_its_timer(rig):
    sender.send_action("injury_pulse")
    old = sender._safety_timers["A"]
    sender.send_action("heal_pulse")
    new = sender._safety_timers["A"]
    count = len(rig.socket.messages)
    old.fire()
    assert len(rig.socket.messages) == count
    assert sender._safety_timers["A"] is new


def test_stop_output_keeps_pair_but_invalidates_every_layer_and_timer(rig):
    continuous(rig, healing=True)
    sender.send_action("injury_pulse")
    old = sender._safety_timers["A"]
    sender.stop_output()
    assert sender._client is rig.client and rig.client.is_paired()
    assert not rig.socket.closed
    assert sender._mixer.pulses == {} and not sender._mixer.low_health
    count = len(rig.socket.messages)
    old.fire()
    assert len(rig.socket.messages) == count
    assert sender._safety_timers == {}
    assert sender.send_action("heal_pulse")["dispatched"]


def test_stop_invalidates_a_call_waiting_for_the_send_lock(rig, monkeypatch):
    main_thread = threading.current_thread()
    waiting = threading.Event()
    lock = threading.RLock()

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread() is not main_thread:
                waiting.set()
            lock.acquire()

        def __exit__(self, *args):
            lock.release()

    monkeypatch.setattr(sender, "_timer_lock", ObservedLock())
    results = []
    with sender._timer_lock:
        worker = threading.Thread(target=lambda: results.append(sender.send_action("injury_pulse")),
                                  daemon=True)
        worker.start()
        assert waiting.wait(1)
        sender.stop_output()
        count = len(rig.socket.messages)
    worker.join(1)
    assert not worker.is_alive()
    assert results[0]["dispatched"] is False
    assert len(rig.socket.messages) == count


def test_safe_stop_zeros_both_channels_before_closing(rig):
    continuous(rig)
    sender.safe_stop()
    assert sender._client is None and rig.socket.closed
    assert [m["channel"] for m in rig.socket.messages
            if m["type"] == 3 and m["strength"] == 0] == [1, 2]
    assert not sender.send_action("death_pulse")["dispatched"]


def test_reconnect_zeroes_before_becoming_ready_and_old_timer_is_inert(rig):
    continuous(rig)
    old = sender._safety_timers["A"]
    rig.client._on_close(rig.socket, 1006, "lost")
    assert not rig.client.is_paired()
    rig.socket.messages.clear()
    rig.client._handle_bind({"clientId": "web2", "targetId": "app2", "message": "200"})
    assert rig.client.is_paired()
    assert len(rig.socket.messages) == 4
    assert all(m["targetId"] == "app2" for m in rig.socket.messages)
    old.fire()
    assert len(rig.socket.messages) == 4
    assert sender._rendered_output == MixedOutput()


def test_stale_socket_callback_cannot_unpair_current_socket(rig):
    rig.client._on_close(object(), 1006, "old socket")
    assert rig.client.is_paired()


def test_pair_zero_failure_stays_unready(rig):
    rig.socket.fail_on = lambda message: message["type"] == 3
    rig.client._handle_bind({"clientId": "web2", "targetId": "app2", "message": "200"})
    assert not rig.client.is_paired()
    assert not sender.send_action("injury_pulse")["dispatched"]


def test_missing_waveform_is_rejected_before_strength_send(rig, monkeypatch):
    monkeypatch.setitem(sender.ACTION_PROFILES["injury_pulse"], "waveform", "missing")
    assert not sender.send_action("injury_pulse")["dispatched"]
    assert rig.socket.messages == []


def test_pulse_send_failure_invalidates_connection_and_does_not_report_success(rig):
    rig.socket.fail_on = lambda message: message["message"].startswith("pulse-")
    assert not sender.send_action("injury_pulse")["dispatched"]
    assert not rig.client.is_paired() and rig.socket.closed
    assert sender._safety_timers == {}
    assert sender._rendered_output == MixedOutput()


def test_timer_start_failure_requests_zero(rig, monkeypatch):
    def fail_start(self):
        raise RuntimeError("timer unavailable")
    monkeypatch.setattr(rig.timer_type, "start", fail_start)
    assert not sender.send_action("injury_pulse")["dispatched"]
    assert rig.socket.messages[-1]["strength"] == 0
    assert sender._safety_timers == {}


def test_expired_lease_cannot_resurrect_old_pulse_on_new_frame(rig):
    sender.send_action("injury_pulse")
    continuous(rig, at=1002.0)
    assert sender._mixer.pulses == {}
    assert sender._rendered_output.strength == 10


def test_templates_are_repeated_only_when_the_previous_template_finishes(rig):
    continuous(rig)
    continuous(rig, at=1000.5)
    continuous(rig, at=1001.0)
    assert sum(m["message"].startswith("pulse-") for m in rig.socket.messages) == 1
    tick(rig, 1001.21)
    pulses = [m for m in rig.socket.messages if m["message"].startswith("pulse-")]
    assert len(pulses) == 2
    assert pulses[0]["message"] == pulses[1]["message"]


def test_socket_timeout_invalidates_actions_and_releases_the_send_lock(rig, monkeypatch):
    def timeout(raw):
        raise TimeoutError("write timed out")
    monkeypatch.setattr(rig.socket, "send", timeout)
    result = sender.send_action("injury_pulse")
    assert result["dispatched"] is False
    assert not rig.client.is_paired() and rig.socket.closed
    assert sender._safety_timers == {}
    # 失败返回后 stop 不再等待未释放的连接/发送锁。
    sender.stop_output()


def test_old_socket_send_failure_cannot_close_the_replacement_socket(rig, monkeypatch):
    replacement = SimpleNamespace(closed=False)
    def close(**kwargs):
        replacement.closed = True
    replacement.close = close

    def late_failure(raw):
        # 模拟旧 send 返回异常前，后台线程已换上新 socket。
        rig.client._ws = replacement
        raise TimeoutError("old socket timed out")
    monkeypatch.setattr(rig.socket, "send", late_failure)
    assert rig.client.send_json({"type": 4, "message": "clear-1"}) is False
    assert not replacement.closed
    assert rig.client.is_paired()


@pytest.mark.parametrize("callback", ["send_failure", "error", "close", "backend_rejection"])
def test_socket_replaced_between_error_detection_and_forget_keeps_new_pair(
        rig, monkeypatch, callback):
    old_socket = rig.socket
    replacement = type(old_socket)()
    entered_forget = threading.Event()
    release_forget = threading.Event()
    original_forget = rig.client._forget_pair
    failures = []

    def delayed_forget(*args, **kwargs):
        # 严格制造原 TOCTOU 窗口：错误已发现，但真正清配对前换上新 socket。
        entered_forget.set()
        assert release_forget.wait(2)
        return original_forget(*args, **kwargs)

    monkeypatch.setattr(rig.client, "_forget_pair", delayed_forget)
    if callback == "send_failure":
        def fail_send(raw):
            raise TimeoutError("old send timed out")
        monkeypatch.setattr(old_socket, "send", fail_send)

    def invoke_old_callback():
        try:
            if callback == "send_failure":
                assert not rig.client.send_json({"type": 4, "message": "clear-1"})
            elif callback == "error":
                rig.client._on_error(old_socket, TimeoutError("old socket"))
            elif callback == "close":
                rig.client._on_close(old_socket, 1006, "old socket")
            else:
                rig.client._reject_server_message(old_socket)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=invoke_old_callback, daemon=True)
    worker.start()
    try:
        assert entered_forget.wait(2)
        assert rig.client._install_socket(replacement)
        assert not rig.client.is_paired()  # 新 socket 不能继承旧配对
        rig.client._handle_bind({"clientId": "web2", "targetId": "app2", "message": "200"})
        assert sender.update_continuous(low_health=True, healing=False, hp=0.5)["dispatched"]
        current_timer = sender._safety_timers["A"]
        current_generation = sender._operation_generation
        count = len(replacement.messages)
    finally:
        release_forget.set()
        worker.join(2)
    assert not worker.is_alive(), "callback deadlocked against socket installation"
    assert not failures
    assert rig.client.is_paired()
    assert (rig.client.client_id, rig.client.target_id) == ("web2", "app2")
    assert not replacement.closed and len(replacement.messages) == count
    assert sender._safety_timers["A"] is current_timer
    assert sender._operation_generation == current_generation
    assert sender._rendered_output == MixedOutput(10, "weak_pulse")


def test_stopped_client_refuses_socket_installation(rig):
    rig.client._should_stop = True
    assert not rig.client._install_socket(type(rig.socket)())
    assert rig.client._ws is rig.socket


def test_connection_and_io_timeouts_are_instance_scoped(rig, monkeypatch):
    import websocket
    before = websocket.getdefaulttimeout()
    connection_args = []

    class Transport:
        timeout = None
        closed = False
        def settimeout(self, timeout):
            self.timeout = timeout
        def close(self):
            self.closed = True

    transport = Transport()

    def connect(address, timeout):
        connection_args.append((address, timeout))
        return transport

    class FakeApp:
        def __init__(self, url, *, socket, **callbacks):
            assert socket is transport
            self.sock = socket
            self.callbacks = callbacks
        def run_forever(self, **kwargs):
            assert transport.timeout == sender.SOCKET_TIMEOUT_SEC
            self.callbacks["on_open"](self)
            rig.client._should_stop = True

    monkeypatch.setattr(sender.socket, "create_connection", connect)
    monkeypatch.setattr(websocket, "WebSocketApp", FakeApp)
    rig.client._run_forever()
    assert connection_args == [(("offline.invalid", 9999), sender.CONNECT_TIMEOUT_SEC)]
    assert transport.timeout == sender.SOCKET_TIMEOUT_SEC and transport.closed
    assert websocket.getdefaulttimeout() == before


def integrated_trigger(rig, monkeypatch):
    from src import trigger
    # 使用真实 Trigger/状态和真实 mixer，仅日志存内存，不启动日志线程或打开文件。
    monkeypatch.setattr(trigger, "_AsyncJsonlWriter", lambda path: SimpleNamespace(
        append=lambda entry: None, close=lambda: None))
    dispatch = trigger.TriggerDispatch({"trigger": {
        "sender_mode": "mock", "death_hp_threshold": 0.0, "max_frame_gap_sec": 1.0,
    }}, clock=lambda: rig.clock.now)
    dispatch._send = sender.send_action
    dispatch._stop_output = sender.stop_output
    dispatch._close_sender = sender.safe_stop
    dispatch._update_continuous = sender.update_continuous
    return dispatch


def frame(raw, stable, state):
    return SimpleNamespace(health_raw=raw, health_stable=stable, state=state, valid=True)


def test_trigger_zero_raw_hp_renews_death_even_while_ema_is_positive(rig, monkeypatch):
    dispatch = integrated_trigger(rig, monkeypatch)
    dispatch.dispatch(frame(0.5, 0.5, "injured"))
    rig.clock.now = 1000.1
    dispatch.dispatch(frame(0.0, 0.35, "death"))
    assert sender._rendered_output == MixedOutput(40, "death_pulse")
    for at in (1000.6, 1001.1, 1001.6, 1002.1, 1002.6, 1003.1,
               1003.6, 1004.1, 1004.6, 1005.09):
        rig.clock.now = at
        dispatch.dispatch(frame(0.0, 0.1, "death"))
        assert sender._rendered_output == MixedOutput(40, "death_pulse")
    tick(rig, 1005.11)
    assert sender._rendered_output == MixedOutput()
    dispatch.safe_stop()


def test_trigger_positive_raw_hp_cancels_death_before_its_duration(rig, monkeypatch):
    dispatch = integrated_trigger(rig, monkeypatch)
    dispatch.dispatch(frame(0.5, 0.5, "injured"))
    rig.clock.now = 1000.1
    dispatch.dispatch(frame(0.0, 0.35, "death"))
    rig.clock.now = 1000.2
    dispatch.dispatch(frame(0.25, 0.02, "healing"))
    assert "death_pulse" not in sender._mixer.pulses
    assert sender._rendered_output.waveform == "heal_pulse"
    assert sender._rendered_output.strength == 30  # low 10 + sustain 5 + confirmation 15
    dispatch.safe_stop()


def configure_feedback(rig, monkeypatch, mode="both", **overrides):
    from src.feedback_settings import default_settings
    from src.feedback_mixer import action_profiles
    values = default_settings() | {"channel_mode": mode} | overrides
    monkeypatch.setattr(sender, "_mixer", FeedbackMixer({"sender": values}))
    monkeypatch.setattr(sender, "ACTION_PROFILES", action_profiles({"sender": values}))
    return values


@pytest.mark.parametrize("mode,active", [("a", {"A"}), ("b", {"B"}), ("both", {"A", "B"})])
def test_selected_channels_reach_real_protocol_with_independent_strengths(rig, monkeypatch, mode, active):
    configure_feedback(rig, monkeypatch, mode, low_health_strength_b=6, injury_pulse_strength_b=40)
    continuous(rig)
    sender.send_action("injury_pulse", {"damage_pct": .5})
    messages = rig.socket.messages
    waves = [m["message"] for m in messages if m["message"].startswith("pulse-")]
    assert {m[6] for m in waves} == active
    strengths = {m["channel"]: m["strength"] for m in messages if m["type"] == 3}
    assert strengths == {index: value for ch, index, value in (("A", 1, 20), ("B", 2, 26)) if ch in active}
    assert all(json.loads(m.split(":", 1)[1]) == sender.WAVEFORM_V3["official_combo"] for m in waves[-len(active):])
    assert len(sender._safety_timers) == 1
    timer = next(iter(sender._safety_timers.values()))
    rig.clock.now += 1.1
    timer.fire()
    assert sender._rendered_output.strength == sender._rendered_output_b.strength == 0
    for ch in active:
        assert f"clear-{1 if ch == 'A' else 2}" in [m["message"] for m in rig.socket.messages]


@pytest.mark.parametrize("wave", list(sender.WAVEFORM_V3))
def test_selected_official_wave_bytes_reach_both_channels(rig, monkeypatch, wave):
    configure_feedback(rig, monkeypatch, heal_pulse_waveform=wave)
    assert sender.send_action("heal_pulse")["dispatched"]
    waves = [m["message"] for m in rig.socket.messages if m["message"].startswith("pulse-")]
    assert {m[6] for m in waves} == {"A", "B"}
    assert all(json.loads(m.split(":", 1)[1]) == sender.WAVEFORM_V3[wave] for m in waves)


def test_channel_zero_does_not_cancel_other_channel_layers_or_tick(rig, monkeypatch):
    configure_feedback(rig, monkeypatch, low_health_strength_b=0, injury_pulse_strength=0)
    continuous(rig)
    sender.send_action("injury_pulse")
    assert sender._rendered_output.strength == 10 and sender._rendered_output_b.strength == 20
    sender.update_continuous(low_health=False, healing=False, hp=.8)
    assert sender._rendered_output.strength == 0 and sender._rendered_output_b.strength == 20
    assert sender._mixer.pulses and sender._safety_timers
    sender.stop_output()
    assert sender._rendered_output_b.strength == 0


def test_dual_channel_failure_invalidates_both_and_old_tick_cannot_restart(rig, monkeypatch):
    configure_feedback(rig, monkeypatch)
    continuous(rig)
    old = next(iter(sender._safety_timers.values()))
    rig.socket.fail_on = lambda message: message.get("message", "").startswith("pulse-B:")
    assert not sender.send_action("heal_pulse")["dispatched"]
    assert not rig.client.is_paired() and rig.socket.closed
    assert not sender._mixer.pulses and not sender._safety_timers
    assert sender._rendered_output.strength == sender._rendered_output_b.strength == 0
    count = len(rig.socket.messages)
    old.fire()
    assert len(rig.socket.messages) == count


def test_dual_stop_clears_both_and_late_timer_does_nothing(rig, monkeypatch):
    configure_feedback(rig, monkeypatch)
    continuous(rig)
    old = next(iter(sender._safety_timers.values()))
    sender.stop_output()
    assert rig.client.is_paired()
    assert all(f"clear-{ch}" in [m["message"] for m in rig.socket.messages] for ch in (1, 2))
    assert {m["channel"] for m in rig.socket.messages if m["type"] == 3 and m["strength"] == 0} == {1, 2}
    count = len(rig.socket.messages)
    old.fire()
    assert len(rig.socket.messages) == count


def test_long_wave_does_not_repeat_each_frame_and_renews_when_due(rig, monkeypatch):
    configure_feedback(rig, monkeypatch, low_health_waveform="official_taps")
    continuous(rig)
    def pulses():
        return [m for m in rig.socket.messages if m["message"].startswith("pulse-")]
    assert len(pulses()) == 2
    for i in range(1, 17):
        continuous(rig, at=1000 + i * .5)
    assert len(pulses()) == 2
    continuous(rig, at=1008.5)
    assert len(pulses()) == 4
