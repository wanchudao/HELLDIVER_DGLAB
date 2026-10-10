"""Actual frozen DGHub importer/config methods, only inside this owned probe.

Run with the isolated EXE --plugin-runner. No normal app bootstrap, sockets,
child processes, installed user plugins or installed configuration are used.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import zipfile


EXPECTED_ROOT = Path("E:/HELLDIVER_DGLAB/build/dghub_import_probe_20261008").resolve()


def forbidden(*args, **kwargs):
    raise RuntimeError("Import probe forbids network and child processes")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if not getattr(sys, "frozen", False) or sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use isolated DGHub 1.9.6 runner")
    root = Path(sys.executable).parent.resolve()
    if root != EXPECTED_ROOT:
        raise RuntimeError("Refusing an installed or unknown host directory")
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    subprocess.Popen = forbidden
    from src.adapters.plugin_store import PluginStore
    from src.config.config_manager import ConfigManager
    from src.adapters.sdk.protocol import PluginManifest

    archive_name = os.environ.get("HELLDIVER_IMPORT_FIXTURE", "candidate_import.zip")
    if Path(archive_name).name != archive_name or not archive_name.endswith(".zip"):
        raise ValueError("Probe ZIP must be one filename in its owned root")
    archive = root / archive_name
    with zipfile.ZipFile(archive) as zf:
        golden = json.loads(zf.read("manifest.json"))
        executable_hash = hashlib.sha256(zf.read(golden["entry"])).hexdigest()
    PluginManifest.model_validate(golden)
    defaults = {field["key"]: field["default"] for section in golden["config_schema"]
                for field in section["fields"]}
    fixture = Path(tempfile.mkdtemp(prefix="fixture_", dir=root))
    plugins = fixture / "plugins"
    plugins.mkdir()
    store = PluginStore(plugins_dir=plugins)
    installed_id = store.install_zip(archive)
    installed = store.get(installed_id)
    normalized = installed.manifest.model_dump()
    fields = {field["key"]: field for section in normalized["config_schema"]
              for field in section["fields"]}
    import_ok = (installed_id == golden["id"] and installed.entry_path.is_file()
                 and digest(installed.entry_path) == executable_hash
                 and installed.plugin_dir.resolve().is_relative_to(fixture))
    hud_options = fields["hud_scale_percent"]["options"]
    # Real configuration class gets an explicit absolute file in the fixture;
    # its defaults/migrations/save code never reads the installed config.
    config_path = fixture / "config.json"
    manager = ConfigManager(config_path=str(config_path))
    manager.update_plugin_batch(installed_id, defaults | {"idle_strength": 0, "enabled": False})
    saved = []
    for scale in range(75, 126, 5):
        updates = dict(hud_scale_percent=str(scale), channel_mode="both", max_strength=35,
                       max_strength_b=19, injury_pulse_strength=17, injury_pulse_strength_b=23,
                       heal_pulse_preset="潮汐", panel_mode="island")
        manager.update_plugin_batch(installed_id, updates)
        reloaded = ConfigManager(config_path=str(config_path))
        current = reloaded.get_plugin_config(installed_id)
        saved.append(dict(scale=scale, roundtrip_ok=all(current[key] == value for key, value in updates.items())))
    before = manager.get_plugin_config(installed_id)
    disk_before = config_path.read_bytes()
    original_save = manager._save

    def save_failure():
        raise OSError("controlled write failure")

    manager._save = save_failure
    try:
        manager.update_plugin_batch(installed_id, dict(hud_scale_percent=100))
    except OSError:
        pass
    finally:
        manager._save = original_save
    rollback_ok = manager.get_plugin_config(installed_id) == before and config_path.read_bytes() == disk_before
    # Host stores arbitrary config values. The actual plugin must reject these.
    manager.set_plugin(installed_id, "hud_scale_percent", 76)
    invalid_saved = ConfigManager(config_path=str(config_path)).get_plugin_config(installed_id)
    manager.set_plugin(installed_id, "hud_scale_percent", "125")
    valid_saved = ConfigManager(config_path=str(config_path)).get_plugin_config(installed_id)
    logs = installed.plugin_dir / "sessions"
    logs.mkdir()
    (logs / "old_log.txt").write_text("owned fake old session", encoding="utf-8")
    external = fixture / "persistent_data"
    external.mkdir()
    marker = external / "old_log.txt"
    marker.write_text("owned persistent session", encoding="utf-8")
    store.install_zip(archive)
    replacement = store.get(installed_id)
    directory_replaced = not (replacement.plugin_dir / "sessions/old_log.txt").exists()
    external_retained = marker.read_text(encoding="utf-8") == "owned persistent session"
    config_retained = ConfigManager(config_path=str(config_path)).get_plugin_config(installed_id) == valid_saved
    # Intercept spawn to record argv/cwd/environment, never create a process.
    import src.adapters.plugin_store as store_module
    captured = {}

    class Child:
        pid = 123456

        def poll(self):
            return None

    def spawn(*args, **kwargs):
        captured.update(argv=args[0], cwd=str(kwargs.get("cwd")),
                        env_keys=sorted(key for key in kwargs.get("env", {}) if key.startswith("DGHUB_")))
        return Child()

    store_module.spawn_managed_subprocess = spawn
    spawned = store.spawn(installed_id, host="127.0.0.1", port=1, token="fake-not-recorded")
    spawn_ok = spawned and captured.get("argv") == [str(replacement.entry_path)]
    bad_zip = fixture / "bad_path.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../outside.txt", "controlled test")
    try:
        store.install_zip(bad_zip)
        traversal_rejected = False
    except ValueError:
        traversal_rejected = not (fixture / "outside.txt").exists()
    result = dict(status="ok" if import_ok and all(row["roundtrip_ok"] for row in saved)
                  and rollback_ok and config_retained and traversal_rejected and spawn_ok else "incomplete",
        scope="actual DGHub importer/schema/config classes in isolated filesystem; no native GUI/runtime",
        host_python=sys.version.split()[0], archive_sha256=digest(archive), exe_sha256=executable_hash,
        import_ok=import_ok, plugin_id=installed_id, hud_options=hud_options,
        fields=list(fields), eleven_config_roundtrips=saved, write_failure_rollback=rollback_ok,
        host_saved_invalid_hud=invalid_saved["hud_scale_percent"], invalid_configuration=invalid_saved,
        final_configuration=valid_saved, same_id_upgrade_replaces_plugin_directory=directory_replaced,
        outside_package_data_survived=external_retained, host_config_survived_upgrade=config_retained,
        spawn_intercept=captured, spawn_contract_ok=spawn_ok, traversal_rejected=traversal_rejected,
        installed_host_modified=False, installed_config_modified=False,
        network_used=False, child_process_started=False, real_device_used=False)
    (root / "import_contract_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        if Path(sys.executable).parent.resolve() == EXPECTED_ROOT:
            (EXPECTED_ROOT / "import_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
