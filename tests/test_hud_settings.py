import pytest

from src import hud_settings as settings


def test_missing_settings_preserve_legacy_without_writing(tmp_path):
    path = tmp_path / "hud_settings.ini"
    store = settings.HudSettingsStore(path)
    assert store.snapshot() == {"mode": "legacy", "scale_percent": 100}
    assert not path.exists()


@pytest.mark.parametrize("scale", range(75, 126, 5))
def test_all_eleven_exact_steps_roundtrip_without_float_arithmetic(tmp_path, scale):
    path = tmp_path / "hud_settings.ini"
    values = {"mode": "manual", "scale_percent": scale}
    store = settings.HudSettingsStore(path)
    assert store.save(values) == values
    restored = settings.HudSettingsStore(path)
    assert restored.snapshot() == values
    copy = restored.snapshot()
    copy["scale_percent"] = 100
    assert restored.snapshot() == values
    assert "schema_version = 1" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", [74, 76, 126, True, False, 100.0, 1.0, "100", "1.00", None])
def test_illegal_programmatic_scale_never_modifies_saved_file(tmp_path, bad):
    path = tmp_path / "hud_settings.ini"
    settings.save_settings(path, {"mode": "manual", "scale_percent": 100})
    before = path.read_bytes()
    with pytest.raises(ValueError):
        settings.save_settings(path, {"mode": "manual", "scale_percent": bad})
    assert path.read_bytes() == before


@pytest.mark.parametrize("values", [{}, {"mode": "manual"}, {"mode": "auto", "scale_percent": 100},
                                    {"mode": None, "scale_percent": 100},
                                    {"mode": "manual", "scale_percent": 100, "resolution": "1920x1080"}])
def test_incomplete_unknown_and_unsupported_modes_are_rejected(values):
    with pytest.raises(ValueError):
        settings.validate_settings(values)


@pytest.mark.parametrize("text", ["broken data", "[other]\nschema_version=1\nmode=manual\nscale_percent=100",
    "[DEFAULT]\nx=1\n[hud]\nschema_version=1\nmode=manual\nscale_percent=100",
    "[hud]\nschema_version=2\nmode=manual\nscale_percent=100",
    "[hud]\nschema_version=1\nmode=manual\nscale_percent=100.0",
    "[hud]\nschema_version=1\nmode=manual\nscale_percent=１００",
    "[hud]\nschema_version=1\nmode=manual\nscale_percent=76",
    "[hud]\nschema_version=1\nmode=manual",
    "[hud]\nschema_version=1\nmode=manual\nscale_percent=100\nunknown=1",
    "[hud]\nschema_version=1\nmode=manual\nscale_percent=100\nscale_percent=105"])
def test_damaged_saved_config_blocks_until_explicit_repair(tmp_path, text):
    path = tmp_path / "hud_settings.ini"
    path.write_text(text, encoding="utf-8")
    store = settings.HudSettingsStore(path)
    assert store.error
    with pytest.raises(ValueError, match="HUD 设置"):
        store.snapshot()
    assert path.read_text(encoding="utf-8") == text
    store.save({"mode": "manual", "scale_percent": 100})
    assert not store.error
    assert store.snapshot()["mode"] == "manual"


def test_atomic_replace_failure_preserves_disk_and_in_memory_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "hud_settings.ini"
    store = settings.HudSettingsStore(path)
    store.save({"mode": "manual", "scale_percent": 95})
    before = path.read_bytes()
    def fail(*args):
        raise OSError("read-only disk")
    monkeypatch.setattr(settings.os, "replace", fail)
    with pytest.raises(OSError):
        store.save({"mode": "manual", "scale_percent": 125})
    assert path.read_bytes() == before
    assert store.snapshot()["scale_percent"] == 95
    assert not list(tmp_path.glob(".hud-*.tmp"))


def test_active_session_including_f8_pause_cannot_save(tmp_path):
    path = tmp_path / "hud_settings.ini"
    store = settings.HudSettingsStore(path)
    with pytest.raises(ValueError, match="F8"):
        store.save({"mode": "manual", "scale_percent": 125}, running=True)
    assert not path.exists()
    assert store.snapshot() == settings.default_settings()


def test_oversized_or_non_utf8_config_stays_blocking(tmp_path):
    path = tmp_path / "hud_settings.ini"
    path.write_bytes(b"x" * (8 * 1024 + 1))
    assert settings.HudSettingsStore(path).error
    path.write_bytes(b"\xff\xfe\x00")
    assert settings.HudSettingsStore(path).error
