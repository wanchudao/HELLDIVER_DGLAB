"""Import/enable/stop this plugin through the running installed DGHub API.

Only this plugin is touched. Refuse any connected device or other active plugin.
No key/mouse injection, source fixtures, private credentials, firewall changes,
or system/account settings. Restore original plugin enablement at the end.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import time
import uuid
import zipfile

PLUGIN = "helldiver_dglab"
BASE_PATH = "/api/plugins/" + PLUGIN


def request(path, *, method="GET", body=None, content_type="application/json"):
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=25)
    headers = {"Origin": "http://127.0.0.1:8000"}
    if body is not None:
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        if response.status != 200 or data.get("status") == "error":
            raise RuntimeError("Installed DGHub request did not succeed: " + path)
        return data
    finally:
        connection.close()


def post(path, data):
    return request(path, method="POST", body=json.dumps(data).encode("utf-8"))


def plugin():
    matches = [row for row in request("/api/plugins")["plugins"] if row["name"] == PLUGIN]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one installed HELLDIVER plugin")
    return matches[0]


def safe_status():
    current = request("/api/status")
    if current.get("connected") is not False:
        raise RuntimeError("Refuse real-device runtime test")
    return current


def run(archive, installed, sessions):
    before = plugin()
    if before.get("active") or before.get("enabled"):
        raise RuntimeError("Start this acceptance with the plugin disabled")
    if any(row.get("active") or row.get("enabled") for row in request("/api/plugins")["plugins"]
           if row["name"] != PLUGIN):
        raise RuntimeError("Refuse to affect a host with other active plugins")
    safe_status()
    original_settings = dict(before["config"])
    old_sessions = {path.name for path in sessions.iterdir() if path.is_dir()}
    boundary = "helldiver-" + uuid.uuid4().hex
    data = archive.read_bytes()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{archive.name}"\r\n'
            'Content-Type: application/zip\r\n\r\n').encode("ascii") + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    report = dict(status="incomplete", installed_host_tested=True,
                  actual_import_endpoint_tested=False, actual_launcher_tested=False,
                  real_global_f8_tested=False, real_game_tested=False, physical_device_used=False,
                  no_positive_output=True, observations=[])
    imported = request("/api/plugins/_install_zip", method="POST", body=body,
                       content_type="multipart/form-data; boundary=" + boundary)
    if imported.get("plugin_id") != PLUGIN:
        raise RuntimeError("Native import returned wrong plugin")
    with zipfile.ZipFile(archive) as source:
        manifest = json.loads(source.read("manifest.json"))
        expected_hash = hashlib.sha256(source.read(manifest["entry"])).hexdigest()
    if hashlib.sha256((installed / manifest["entry"]).read_bytes()).hexdigest() != expected_hash:
        raise RuntimeError("Native import did not install verified EXE")
    report.update(actual_import_endpoint_tested=True, archive_sha256=hashlib.sha256(data).hexdigest(),
                  exe_sha256=expected_hash, plugin_version=manifest["version"])
    began = time.monotonic()
    configured = None
    try:
        post(BASE_PATH + "/toggle", {"enabled": True})
        report["actual_launcher_tested"] = True
        stable_since = None
        while time.monotonic() - began < 12:
            state = safe_status()
            live = plugin()
            statuses = state.get("plugins_status", {})
            if isinstance(statuses, list):
                fields = next((row for row in statuses if row.get("name") == PLUGIN), {})
            else:
                fields = statuses.get(PLUGIN, {})
            # Record only fixed public diagnostics, never token/URI/config dumps.
            selected = {key: fields.get(key) for key in
                        ("display_status", "capture_state", "output_ready", "running",
                         "recognition_running", "hp", "pause_reason", "requested_a_pct",
                         "requested_b_pct", "hud_scale", "hud_scale_percent")}
            selected.update(hotkey_ready=fields.get("hotkey_ready"),
                            hotkey_diagnostic=fields.get("hotkey_diagnostic"))
            selected.update(active=live.get("active"), connected=False,
                            strength_a=state.get("strength_a"), strength_b=state.get("strength_b"))
            if not report["observations"] or report["observations"][-1] != selected:
                report["observations"].append(selected)
            report["no_positive_output"] &= state.get("strength_a") == state.get("strength_b") == 0
            new_sessions = [path for path in sessions.iterdir() if path.is_dir() and path.name not in old_sessions]
            if len(new_sessions) == 1 and (new_sessions[0] / "config_snapshot.json").is_file():
                configured = json.loads((new_sessions[0] / "config_snapshot.json").read_text(encoding="utf-8"))
            if live.get("active") and configured is not None and fields.get("capture_state") == "ok":
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 3:
                    report["startup_remained_live_seconds"] = time.monotonic() - stable_since
                    report["applied_hud"] = configured["settings"]["hud_scale_percent"]
                    report["applied_hud_is_integer"] = type(report["applied_hud"]) is int
                    break
            elif not live.get("active") and time.monotonic() - began >= 2:
                raise RuntimeError("Installed native launcher/plugin startup exited")
            time.sleep(.15)
        else:
            raise RuntimeError("Installed native startup observation timed out")
    finally:
        post(BASE_PATH + "/toggle", {"enabled": False})
    ended = time.monotonic()
    terminal = None
    while time.monotonic() - ended < 5:
        new_sessions = [path for path in sessions.iterdir() if path.is_dir() and path.name not in old_sessions]
        if len(new_sessions) == 1 and (new_sessions[0] / "session.json").is_file():
            terminal = json.loads((new_sessions[0] / "session.json").read_text(encoding="utf-8"))
            if terminal.get("state") == "complete":
                break
        time.sleep(.1)
    after = plugin()
    settings_preserved = all(after["config"].get(key) == value for key, value in original_settings.items())
    status = safe_status()
    report.update(original_settings_preserved=settings_preserved,
                  original_enablement_restored=after.get("enabled") is False and after.get("active") is False,
                  final_both_zero=status.get("strength_a") == status.get("strength_b") == 0,
                  session_metadata=terminal)
    if (settings_preserved and report["original_enablement_restored"] and report["no_positive_output"]
            and report["applied_hud_is_integer"] and report["final_both_zero"]
            and terminal and terminal.get("runtime_error") is False):
        report["status"] = "ok"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.archive.resolve(), args.installed.resolve(), args.sessions.resolve())
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"observations", "session_metadata"}},
                     ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
