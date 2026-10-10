"""Exercise freezer policy with a fake Analysis, without freezing or hardware."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "dghub_plugin"
REQUIRED = ("src.vision_pipeline", "src.trigger", "src.feedback_mixer")


def source_record(name):
    return name, str(ROOT / (name.replace(".", "/") + ".py")), "PYMODULE"


def run_spec(records=None):
    captured = {}

    def analysis(scripts, **kwargs):
        captured.update(scripts=scripts, **kwargs)
        return SimpleNamespace(
            pure=[source_record(name) for name in REQUIRED] if records is None else records,
            scripts=[], binaries=[], datas=[],
        )

    runpy.run_path(str(PLUGIN / "helldiver_dglab.spec"), init_globals={
        "SPECPATH": str(PLUGIN), "Analysis": analysis,
        "PYZ": lambda *args, **kwargs: None,
        "EXE": lambda *args, **kwargs: None,
    })
    return captured


def test_entry_cannot_prepend_legacy_plugin_directory():
    analysis = run_spec()
    entry = Path(analysis["scripts"][0])
    assert entry == ROOT / "tools/dghub_offline_entry.py"
    assert entry.is_file() and not entry.is_relative_to(PLUGIN)
    assert analysis["pathex"] == [str(ROOT), str(PLUGIN)]
    assert {"mss", *REQUIRED} <= set(analysis["hiddenimports"])
    assert all(not Path(source).is_relative_to(PLUGIN / "src")
               for source, _destination in analysis["datas"])


def test_freezer_rejects_a_legacy_core_source():
    records = [source_record(name) for name in REQUIRED]
    records.append(("src.control_panel", str(PLUGIN / "src/control_panel.py"), "PYMODULE"))
    with pytest.raises(RuntimeError, match="Legacy core selected"):
        run_spec(records)


@pytest.mark.parametrize("missing", REQUIRED)
def test_freezer_requires_all_shared_core_modules(missing):
    with pytest.raises(RuntimeError, match="Shared core is missing"):
        run_spec([source_record(name) for name in REQUIRED if name != missing])


@pytest.mark.parametrize("forbidden", [
    "src.dglab_sender", "websocket", "websocket._core", "websockets",
    "websockets.client", "dxcam", "qrcode",
])
def test_freezer_rejects_device_or_legacy_backends(forbidden):
    with pytest.raises(RuntimeError, match="Forbidden device/legacy backend"):
        run_spec([source_record(name) for name in (*REQUIRED, forbidden)])


def test_freezer_explicitly_excludes_device_modules():
    assert {"src.dglab_sender", "websocket", "websockets", "dxcam", "qrcode"} <= set(
        run_spec()["excludes"])


def test_shipping_live_entry_uses_normal_sdk_launch_without_developer_flag(monkeypatch):
    from dghub_plugin import panel, sdk_transport
    from tools import dghub_live_entry
    calls = []
    environment = SimpleNamespace(plugin_root=ROOT)
    monkeypatch.setattr(sdk_transport.HostEnvironment, "from_environ", classmethod(lambda cls: environment))
    monkeypatch.setattr(panel, "DGHubPanel", lambda root: SimpleNamespace(owned_root=root))

    def run_host(actual, **kwargs):
        calls.append((actual, kwargs))
        return 0

    monkeypatch.setattr(dghub_live_entry, "run_host", run_host)
    # This is branch wiring, not a socket/event-loop integration test. The
    # offline suite forbids sockets, including Proactor's internal self-pipe.
    monkeypatch.setattr(dghub_live_entry.asyncio, "run", lambda value: value)
    assert dghub_live_entry.main([]) == 0
    assert calls == [(environment, {"ui": SimpleNamespace(owned_root=ROOT)})]


def test_shipping_live_entry_missing_environment_is_not_candidate_gate(monkeypatch):
    from dghub_plugin.sdk_transport import HostEnvironment
    from tools import dghub_live_entry

    def reject(cls):
        raise ValueError("No software fixture launcher")

    monkeypatch.setattr(HostEnvironment, "from_environ", classmethod(reject))
    assert dghub_live_entry.main([]) == 1
