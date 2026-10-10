"""Original SPEC across the actual Trigger, Mixer and sender protocol adapters."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config_loader import load_config
from src.feedback_mixer import FeedbackMixer
from src.state_machine import DetectionResult
from src import dglab_sender as sender
from src import trigger as trigger_module


class Clock:
    now = 0.0
    def __call__(self):
        return self.now


class FakeTimer:
    def __init__(self, interval, function, args=()):
        self.function, self.args = function, args
        self.daemon = False
    def start(self):
        pass
    def cancel(self):
        pass


@pytest.fixture
def chain(monkeypatch, tmp_path):
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.ini")
    clock, messages = Clock(), []
    client = SimpleNamespace(is_paired=lambda: True, stop=lambda: None)
    client.send_json = lambda payload, **kw: messages.append(payload.copy()) or True
    monkeypatch.setattr(sender, "_client", client)
    monkeypatch.setattr(sender, "_mixer", FeedbackMixer(config))
    monkeypatch.setattr(sender, "_safety_timers", {})
    monkeypatch.setattr(sender, "_channel_generation", {"A": 0, "B": 0})
    monkeypatch.setattr(sender, "_rendered_output", sender.MixedOutput())
    monkeypatch.setattr(sender, "_next_waveform_at", 0.0)
    monkeypatch.setattr(sender, "time", SimpleNamespace(monotonic=clock, sleep=lambda _: None))
    monkeypatch.setattr(sender.threading, "Timer", FakeTimer)
    monkeypatch.setattr(trigger_module, "LOG_PATH", tmp_path / "events.jsonl")
    dispatch = trigger_module.TriggerDispatch(config, clock=clock)
    dispatch._send = sender.send_action
    dispatch._update_continuous = sender.update_continuous
    dispatch._stop_output = sender.stop_output
    dispatch._close_sender = sender.safe_stop

    def frame(t, raw, stable=None, state="injured", valid=True):
        clock.now = t
        return dispatch.dispatch(DetectionResult(
            health_raw=raw, health_stable=raw if stable is None else stable,
            valid=valid, state=state, confidence=1, anchor_score=1))

    yield SimpleNamespace(frame=frame, clock=clock, dispatch=dispatch,
                          messages=messages, client=client)
    dispatch.safe_stop()


def test_damage_proportional_low_background_healing_death_and_revival(chain):
    f = chain.frame
    assert f(0, 1)["reason"] == "baseline"
    f(.03, 1)
    f(.1, .7)
    f(.15, .6)
    damage = f(.31, .6)
    assert "injury_pulse" in damage["actions"]
    assert sender._mixer.output(.31).strength == 18  # low10 + 40% of injury20

    healed = f(.4, .8, stable=.4)
    assert "heal_pulse" in healed["actions"]
    assert not healed["low_health"]  # raw recovery, not delayed EMA
    assert sender._mixer.output(.4).strength == 28  # injury8 + confirm15 + sustain5

    died = f(.51, 0, stable=.5)
    assert died["actions"] == ["death_pulse"]
    assert sender._mixer.output(.51).strength == 40
    f(1.0, 0, stable=.2, state="death")
    assert sender._mixer.output(1.0).strength == 40  # EMA cannot cancel a death pulse

    revived = f(1.1, .3, stable=.1, state="death")
    assert "heal_pulse" in revived["actions"]  # display-state debounce must not swallow revival
    assert sender._mixer.output(1.1).strength == 30
    assert "death_pulse" not in sender._mixer.pulses


def test_sustained_feedback_expires_without_a_next_frame(chain):
    chain.frame(0, .5)
    chain.frame(.1, .5)
    assert sender._mixer.output(.1).strength == 10
    chain.clock.now = 1.2
    # Run the real independent timer callback; no new trigger/frame is delivered.
    sender._stop_channel_and_cleanup("A", sender._channel_generation["A"], chain.client)
    assert sender._mixer.output(1.2).strength == 0
    assert any(msg.get("type") == 3 and msg.get("strength") == 0 for msg in chain.messages)


def test_invalid_frame_and_pause_clear_every_layer(chain):
    chain.frame(0, .4)
    chain.frame(.1, .4)
    chain.frame(.2, .5)
    assert sender._mixer.output(.2).strength > 10
    chain.frame(.3, 0, valid=False, state="unknown")
    assert sender._mixer.output(.3).strength == 0
    baseline = chain.frame(.4, .2)
    assert baseline["reason"] == "baseline" and not baseline["dispatched"]
    chain.frame(.5, .2)
    chain.dispatch.reset("paused")
    assert sender._mixer.output(.5).strength == 0
    assert chain.frame(.6, .9)["reason"] == "baseline"


def test_protocol_only_drives_a_and_never_modifies_persistent_baseline(chain):
    chain.frame(0, .4)
    chain.frame(.1, .4)
    chain.frame(.2, .6)
    pulses = [msg for msg in chain.messages if str(msg.get("message", "")).startswith("pulse-")]
    assert pulses and all(msg["message"].startswith("pulse-A:") for msg in pulses)
    assert all(msg.get("channel", 1) == 1 for msg in chain.messages)
    assert all("permanent" not in str(msg) for msg in chain.messages)
