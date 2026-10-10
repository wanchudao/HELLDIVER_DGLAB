"""Read actual preset/API methods in the owned frozen 1.9.6 probe only.

No normal application entry, installed config, sockets, children or devices.
All preset files are explicitly inside a fresh fixture and cwd is that fixture.
"""
import asyncio
import hashlib
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

EXPECTED_ROOT = Path("E:/HELLDIVER_DGLAB/build/dghub_import_probe_20261008").resolve()


def forbidden(*args, **kwargs):
    raise RuntimeError("Catalog probe forbids network and child processes")


async def main():
    if (not getattr(sys, "frozen", False) or sys.version_info[:2] != (3, 11)
            or Path(sys.executable).parent.resolve() != EXPECTED_ROOT):
        raise RuntimeError("Use only the isolated DGHub 1.9.6 runner")
    socket.socket.connect = socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    subprocess.Popen = forbidden
    from src.device.preset_manager import PresetManager
    from src.api import routes_presets
    fixture = Path(tempfile.mkdtemp(prefix="catalog_fixture_", dir=EXPECTED_ROOT))
    os.chdir(fixture)
    manager = PresetManager(str(fixture))
    routes_presets.get_preset_mgr = lambda: manager
    payload = await routes_presets.get_bilibili_pulse_presets(device_family="coyote")
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(routes_presets.router)
    responses = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        responses.append(message)

    await app(dict(type="http", asgi={"version": "3.0", "spec_version": "2.3"},
        http_version="1.1", method="GET", scheme="http", path="/api/bilibili/pulse-presets",
        raw_path=b"/api/bilibili/pulse-presets", query_string=b"device_family=coyote",
        headers=[], client=("127.0.0.1", 1), server=("127.0.0.1", 1)), receive, send)
    start = next(row for row in responses if row["type"] == "http.response.start")
    body = b"".join(row.get("body", b"") for row in responses if row["type"] == "http.response.body")
    headers = {key.decode("ascii"): value.decode("ascii") for key, value in start["headers"]}
    paths = sorted(route.path for route in routes_presets.router.routes)
    report = dict(scope="actual native preset manager and API method; isolated files, no HTTP server",
        manager_signature=str(inspect.signature(PresetManager)), routes=paths,
        response=payload, native_http_tested=False, native_asgi_router_tested=True, native_gui_tested=False,
        host_exe_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        probe_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        fixture=str(fixture), manager_paths={key: str(value) for key, value in vars(manager).items()
            if key.endswith("_path")},
        asgi_status=start["status"], asgi_headers=headers,
        asgi_body_utf8=body.decode("utf-8"),
        installed_host_modified=False, installed_config_modified=False,
        network_used=False, child_process_started=False, physical_device_used=False)
    (EXPECTED_ROOT / "catalog_contract_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception:
        import traceback
        if Path(sys.executable).parent.resolve() == EXPECTED_ROOT:
            (EXPECTED_ROOT / "catalog_probe_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
