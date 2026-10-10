"""test_dglab_sender.py — 模块级 API + 内部函数测试。无需真实 DG-LAB 后端。"""
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.dglab_sender import (
    ACTION_PROFILES, WAVEFORM_V3, _get_lan_ip, _DglabWSClient,
    is_paired, send_action,
)


class TestActionProfiles:
    def test_all_actions_have_waveforms(self):
        for action, profile in ACTION_PROFILES.items():
            assert profile["waveform"] in WAVEFORM_V3, (
                f"{action} -> {profile['waveform']} not in WAVEFORM_V3")

    def test_all_channels_are_A(self):
        for action, profile in ACTION_PROFILES.items():
            assert profile["channel"] == "A"

    def test_injury_and_low_health_share_weak_pulse(self):
        assert ACTION_PROFILES["injury_pulse"]["waveform"] == "weak_pulse"
        assert ACTION_PROFILES["low_health"]["waveform"] == "weak_pulse"


class TestGetLanIp:
    def test_returns_string(self):
        with patch("src.dglab_sender.socket.socket") as socket_type:
            sock = socket_type.return_value.__enter__.return_value
            sock.getsockname.return_value = ("192.0.2.10", 12345)
            assert _get_lan_ip() == "192.0.2.10"

    def test_has_dots_or_localhost(self):
        with patch("src.dglab_sender.socket.socket", side_effect=OSError("offline")):
            assert _get_lan_ip() == "127.0.0.1"


class TestWsClientInit:
    def test_unpaired_after_init(self):
        c = _DglabWSClient("ws://127.0.0.1:9999")
        assert not c.is_paired()
        assert c.client_id is None and c.target_id is None

    def test_send_json_blocked_when_unpaired(self):
        c = _DglabWSClient("ws://127.0.0.1:9999")
        assert c.send_json({"type": "test"}) is False


class TestModuleLevelAPI:
    def test_is_paired_no_init(self):
        """未调用 init_sender 时 is_paired() 返回 False。"""
        assert is_paired() is False

    def test_send_action_no_init(self):
        """未初始化时 send_action 返回 dispatched=False。"""
        result = send_action("heal_pulse")
        assert result["dispatched"] is False
        assert "not initialized" in result.get("error", "")


class TestWaveformIndirection:
    def test_heal_pulse_has_dedicated_waveform(self):
        profile = ACTION_PROFILES["heal_pulse"]
        waveform = WAVEFORM_V3[profile["waveform"]]
        assert len(waveform) > 0
        assert waveform[0].startswith("0A0A0A0A")

    def test_death_pulse_has_dedicated_waveform(self):
        profile = ACTION_PROFILES["death_pulse"]
        waveform = WAVEFORM_V3[profile["waveform"]]
        assert waveform[0].startswith("BEBEBEBE")
