"""Owned DGHub 1.9.6 import, real loopback route, and final plugin process.

Executed only by a copy of the native host in a fixed build directory. Native
startup/updater/device connection and installed configuration are not used.
Only a disconnected controller is published. The plugin is started normally,
with its real panel/capture; output must remain zero throughout this probe.
"""
import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import time
import traceback
import zipfile

ROOT = Path("E:/HELLDIVER_DGLAB/build/dghub_threshold_native_host_20261010").resolve()
KEY = "low_health_threshold_percent"
ID = "helldiver_dglab"


def write(name, data):
    (ROOT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def run():
    # Prevent accidental native service discovery/updater/device connections.
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def loopback_connect(sock, address):
        if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
            raise RuntimeError("Probe allows owned numeric loopback only")
        return original_connect(sock, address)

    def loopback_connect_ex(sock, address):
        if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
            raise RuntimeError("Probe allows owned numeric loopback only")
        return original_connect_ex(sock, address)

    socket.socket.connect = loopback_connect
    socket.socket.connect_ex = loopback_connect_ex
    from src.adapters.plugin_store import PluginStore
    from src.config.config_manager import ConfigManager
    from src.adapters.manager import AdapterManager
    from src.adapters.remote import PluginRuntime
    from src.device.controller import DGLabV3Controller
    from src.device.preset_manager import PresetManager
    from src.api.state import state
    import uvicorn

    archive_name = os.environ.get("HELLDIVER_IMPORT_FIXTURE", "candidate_import.zip")
    if Path(archive_name).name != archive_name or not archive_name.endswith(".zip"):
        raise ValueError("Archive must be an owned-root filename")
    archive = ROOT / archive_name
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        exe_hash = hashlib.sha256(zf.read(manifest["entry"])).hexdigest()
    assert manifest["id"] == ID and manifest["version"] == "1.0.5"
    fields = {f["key"]: f for s in manifest["config_schema"] for f in s["fields"]}
    assert fields[KEY]["type"] == "number" and fields[KEY]["min"] == 0 and fields[KEY]["max"] == 100
    fixture = Path(tempfile.mkdtemp(prefix="native_runtime_", dir=ROOT))
    plugin_root = fixture / "plugins"
    plugin_root.mkdir()
    appdata = fixture / "appdata"
    appdata.mkdir()
    os.environ["LOCALAPPDATA"] = str(appdata)
    store = PluginStore(plugins_dir=plugin_root)
    assert store.install_zip(archive) == ID
    installed = store.get(ID)
    assert hashlib.sha256(installed.entry_path.read_bytes()).hexdigest() == exe_hash
    config_path = fixture / "config.json"
    config = ConfigManager(config_path=str(config_path))
    defaults = {name: field["default"] for name, field in fields.items()}
    config.update_plugin_batch(ID, defaults | {"enabled": False, "idle_strength": 0})
    roundtrips = []
    for value in range(101):
        for setting in (value, str(value)):
            config.update_plugin_batch(ID, {KEY: setting})
            reloaded = ConfigManager(config_path=str(config_path))
            assert reloaded.get_plugin_config(ID)[KEY] == setting
        roundtrips.append(value)
    config.update_plugin_batch(ID, {KEY: "75", "hud_scale_percent": "100", "panel_mode": "full"})
    before, before_bytes = config.get_plugin_config(ID), config_path.read_bytes()
    original_save = config._save
    config._save = lambda: (_ for _ in ()).throw(OSError("controlled save failure"))
    try:
        config.update_plugin_batch(ID, {KEY: 30})
    except OSError:
        pass
    else:
        raise AssertionError("Save failure not propagated")
    finally:
        config._save = original_save
    assert config.get_plugin_config(ID) == before and config_path.read_bytes() == before_bytes

    # Import the actual HTTP/WS app, but do not run normal startup hooks.
    # Those would start devices, global integrations, and updater checks.
    from src.api.app import app, preset_manager
    device = DGLabV3Controller("127.0.0.1")
    device.is_connected = False
    manager = AdapterManager(config_manager=config, dglab_queue=device.queue,
        dglab_info_fn=device.get_device_info, preset_manager=preset_manager,
        strength_layers=device.strength_layers)
    manager.plugin_store = store
    manager.discover_installed_plugins()
    state.dglab = device
    state.adapter_manager = manager
    state.device_type = "v3"
    state.plugin_session_token = secrets.token_urlsafe(32)
    received = []
    original_handle = PluginRuntime._handle_raw

    async def observe(self, raw):
        packet = json.loads(raw)
        # Never write session tokens or credentials to the report.
        if packet.get("op") != "hello":
            received.append(packet)
        return await original_handle(self, raw)

    PluginRuntime._handle_raw = observe
    listen = socket.socket()
    listen.bind(("127.0.0.1", 0))
    port = listen.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        lifespan="off", access_log=False, log_level="warning"))
    server_task = asyncio.create_task(server.serve(sockets=[listen]))
    manager.configure_remote(host="127.0.0.1", port=port, token=state.plugin_session_token)
    checks = []

    def applied_latest():
        return next((p["fields"] for p in reversed(received) if KEY in p.get("fields", {})), {})

    async def ready(expected):
        end = time.monotonic() + 14
        while time.monotonic() < end:
            latest = applied_latest()
            if (manager.is_active(ID) and latest.get(KEY) == expected
                    and any(p.get("fields", {}).get("capture_state") == "ok" for p in received)
                    and any(p.get("fields", {}).get("output_ready") is True for p in received)):
                return
            await asyncio.sleep(.05)
        raise AssertionError("Native startup/applied threshold timeout")

    try:
        while not server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(.02)
        assert await manager.start_plugin(ID)
        await ready(75)
        checks.append(dict(stage="first_native_start", applied=75, paused=not applied_latest().get("running")))
        # Actual host storage update + actual live SDK configuration message.
        config.update_plugin_batch(ID, {KEY: "30"})
        adapter = manager._active[ID]
        await adapter.runtime.push_config_changed(KEY, "30")
        end = time.monotonic() + 3
        while time.monotonic() < end and not any(p.get("fields", {}).get("settings_pending") is True for p in received):
            await asyncio.sleep(.03)
        assert any(p.get("fields", {}).get("settings_pending") is True for p in received)
        assert applied_latest()[KEY] == 75
        checks.append(dict(stage="native_saved_pending", saved=30, applied=75))
        write("runtime_progress.json", dict(stage="ready_for_local_controls", fixture=str(fixture),
            native_route=True, applied=75, pending=30, output_disconnected=True))
        # A separate desktop inspection may press F8/click this owned panel.
        # The marker is scoped to this root, never a host setting or permission.
        ui_wait_steps = 160 if os.environ.get("HELLDIVER_NATIVE_WAIT_UI") == "1" else 0
        for _ in range(ui_wait_steps):
            if (ROOT / "controls_done.json").is_file():
                break
            await asyncio.sleep(.25)
        assert applied_latest()[KEY] == 75
        await manager.stop_plugin(ID)
        await asyncio.sleep(.8)
        received.clear()
        assert await manager.start_plugin(ID)
        await ready(30)
        checks.append(dict(stage="native_reenable", applied=30, paused=not applied_latest().get("running")))
        await manager.stop_plugin(ID)
        await asyncio.sleep(.3)
        snapshots = [json.loads(p.read_text(encoding="utf-8")) for p in fixture.rglob("config_snapshot.json")]
        assert sorted(s["settings"][KEY] for s in snapshots) == [30, 75]
        assert all(s["recognition_config"]["trigger"]["low_health_hp_threshold"] == s["settings"][KEY] / 100 for s in snapshots)
        assert not any(p.get("op") in ("trigger", "pulse") or p.get("pct", 0) for p in received)
        report = dict(status="ok", scope="Actual native importer/config, HTTP catalog and WS route, normal final-EXE process; disconnected device",
            exe_sha256=exe_hash, host_exe_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), checks=checks,
            threshold_roundtrips=roundtrips, integer_and_canonical_string_saved=True,
            save_failure_preserved_memory_and_file=True, native_route_tested=True,
            actual_plugin_process_started=True, pending_kept_old_applied=True,
            reenable_applied_new_threshold=True, session_snapshot_thresholds=[75, 30],
            installed_config_modified=False, installed_plugins_modified=False,
            firewall_modified=False, global_host_settings_modified=False, reset_card_used=False,
            user_game_tested=False, phone_app_tested=False, physical_device_used=False,
            native_gui_control_tested=False, real_global_f8_tested=False,
            non_loopback_network_used=False, no_positive_output=True)
        write("runtime_report.json", report)
    finally:
        await manager.stop_all()
        server.should_exit = True
        await asyncio.wait_for(server_task, 3)
        listen.close()
        PluginRuntime._handle_raw = original_handle


def main():
    if (not getattr(sys, "frozen", False) or sys.version_info[:2] != (3, 11)
            or Path(sys.executable).parent.resolve() != ROOT or Path.cwd().resolve() != ROOT):
        raise RuntimeError("Only isolated native runner in owned root")
    try:
        asyncio.run(asyncio.wait_for(run(), 85))
        return 0
    except Exception:
        write("runtime_report.json", dict(status="incomplete", error=traceback.format_exc(),
            installed_config_modified=False, physical_device_used=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
