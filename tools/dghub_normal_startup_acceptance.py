"""Real final-EXE panel/capture initialization using an isolated SDK peer.

Accepts a read-only installed config sample; never writes installed settings,
connects a device, presses keys, or starts the installed DGHub application.
No injected replay entry/UI/capture is used. This checks desktop startup, not
global F8 or real game recognition or actual native host/device output.
"""
import argparse
import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def panel_visible_for_pid(pid):
    """Read window identity/visibility only; never activate or send input."""
    api = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found = []

    @callback_type
    def inspect(hwnd, _):
        owner = wintypes.DWORD()
        api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and api.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            api.GetWindowTextW(hwnd, title, len(title))
            if title.value == "HELLDIVER DGLAB - Safety Control":
                found.append(True)
        return True

    api.EnumWindows(inspect, 0)
    return bool(found)


async def run(exe, values, *, source):
    from websockets.asyncio.server import serve
    from dghub_plugin.sdk_config import manifest
    from tools.dghub_software_acceptance import software_catalog_response

    token = uuid.uuid4().hex
    received = []
    state = dict(catalog_requests=0, capture_ready=False, output_ready=False,
                 panel_visible=False, paused=True, connected_device=False)
    process = None

    async def handler(ws):
        assert parse_qs(urlsplit(ws.request.path).query).get("token") == [token]
        async for raw in ws:
            packet = json.loads(raw)
            if packet.get("op") == "hello":
                assert packet.get("manifest") == manifest() and packet.get("token") == token
                received.append(packet | {"token": "<redacted>"})
                for response in (dict(op="hello_ack", accepted=True, sdk_version="1.0"),
                                 dict(op="config", data=values),
                                 dict(op="device_info", connected=False, device_type="v3",
                                      max_strength_a=0, max_strength_b=0)):
                    await ws.send(json.dumps(response, ensure_ascii=False))
            else:
                received.append(packet)
            fields = packet.get("fields", {})
            state["capture_ready"] |= fields.get("capture_state") == "ok"
            state["output_ready"] |= fields.get("output_ready") is True
            if "running" in fields:
                state["paused"] &= fields["running"] is False
            state["connected_device"] |= fields.get("device_connected") is True

    with tempfile.TemporaryDirectory(prefix="dghub-normal-config-") as directory:
        root = Path(directory)
        local = root / "isolated_appdata"
        local.mkdir()
        (root / "manifest.json").write_text(json.dumps(manifest(), ensure_ascii=False), encoding="utf-8")
        async with serve(handler, "127.0.0.1", 0, max_size=65536, close_timeout=.5,
                         process_request=lambda _connection, request: software_catalog_response(request, state)) as server:
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("DGHUB_") and key not in {"PYTHONHOME", "PYTHONPATH"}}
            environment.update(PATH="", LOCALAPPDATA=str(local), DGHUB_HOST="127.0.0.1",
                DGHUB_PORT=str(server.sockets[0].getsockname()[1]), DGHUB_TOKEN=token,
                DGHUB_PLUGIN_ID=manifest()["id"], DGHUB_PLUGIN_ROOT=str(root))
            process = await asyncio.create_subprocess_exec(str(exe), cwd=root, env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            failure = None
            try:
                async with asyncio.timeout(12):
                    stable_since = None
                    while process.returncode is None:
                        state["panel_visible"] |= panel_visible_for_pid(process.pid)
                        if state["capture_ready"] and state["output_ready"] and state["panel_visible"]:
                            stable_since = stable_since or time.monotonic()
                            if time.monotonic() - stable_since >= 1:
                                for connection in list(server.connections):
                                    await connection.send(json.dumps(dict(op="stop", reason="owned_startup_complete")))
                                break
                        await asyncio.sleep(.05)
                    code = await process.wait()
            except TimeoutError:
                failure = "hard_timeout"
                if process.returncode is None:
                    process.kill()
                await asyncio.wait_for(process.wait(), 2)
                code = 124
            finally:
                if process.returncode is None:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 2)
        metadata_files = list(root.rglob("session.json"))
        snapshot_files = list(root.rglob("config_snapshot.json"))
        metadata = json.loads(metadata_files[0].read_text(encoding="utf-8")) if len(metadata_files) == 1 else {}
        snapshot = json.loads(snapshot_files[0].read_text(encoding="utf-8")) if len(snapshot_files) == 1 else {}
    no_positive = not any(p.get("op") in {"trigger", "pulse"} or p.get("pct", 0) for p in received)
    zeros = {p.get("channel") for p in received if p.get("op") == "set_strength" and p.get("pct") == 0}
    normalized = snapshot.get("settings", {}).get("hud_scale_percent")
    ok = (code == 0 and failure is None and state["panel_visible"] and state["capture_ready"]
          and state["output_ready"] and state["paused"] and not state["connected_device"]
          and type(normalized) is int and normalized == int(values["hud_scale_percent"])
          and no_positive and zeros == {"a", "b"} and metadata.get("runtime_error") is False)
    return dict(status="ok" if ok else "incomplete", source=source, exit_code=code, failure=failure,
        scope="normal final EXE entry + real desktop panel and capture initialization; software SDK peer",
        exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(), state=state,
        supplied_hud_type=type(values["hud_scale_percent"]).__name__, normalized_hud=normalized,
        no_positive_output=no_positive, final_both_zero=zeros == {"a", "b"},
        snapshot_written=bool(snapshot), session_metadata=metadata,
        path_empty=True, isolated_appdata=True, installed_config_modified=False,
        native_host_tested=False, real_global_f8_tested=False, real_game_tested=False, physical_device_used=False)


def main():
    from dghub_plugin.sdk_config import defaults
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--installed-config", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt" or not args.exe.is_file():
        raise ValueError("Owned Windows final executable required")
    if args.installed_config:
        saved = json.loads(args.installed_config.read_text(encoding="utf-8"))
        values = dict(saved["plugins"]["helldiver_dglab"])
        source = "read-only installed plugin configuration; enabled in isolated fixture only"
    else:
        values = defaults() | {"hud_scale_percent": "100", "running": False}
        source = "browser string dropdown fixture"
    # Do not change installed enablement. This separate host fixture must run.
    values.update(enabled=True, panel_mode="full")
    report = asyncio.run(run(args.exe.resolve(), values, source=source))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "session_metadata"}, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
