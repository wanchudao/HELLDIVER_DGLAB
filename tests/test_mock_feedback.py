from types import SimpleNamespace

import pytest

from src.feedback_mixer import MockFeedbackSender, action_profiles
from src import dglab_sender


def test_mock_uses_actual_profiles_scaling_mixing_and_configured_duration():
    clock = SimpleNamespace(now=0)
    config = {"sender": {"injury_pulse_strength": 30, "injury_pulse_duration": .4}}
    mock = MockFeedbackSender(config, lambda: clock.now)
    assert mock.update_continuous(low_health=True, healing=False, hp=.5)["strength"] == 10
    assert mock.send_action("injury_pulse", {"damage_pct": .5})["strength"] == 25
    clock.now = .41
    assert mock.update_continuous(low_health=True, healing=False, hp=.5)["strength"] == 10
    mock.stop_output()
    assert mock.mixer.output(clock.now).strength == 0


def test_hardware_and_mock_share_profile_defaults():
    assert action_profiles({}) == dglab_sender._DEFAULT_ACTION_PROFILES


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), 61])
def test_invalid_duration_rejected_in_mock_before_hardware_is_involved(duration):
    with pytest.raises(ValueError):
        MockFeedbackSender({"sender": {"death_pulse_duration": duration}})
