"""Verify native isolated catalog evidence against the current plugin parser."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dghub_plugin.preset_catalog import decode_names
from dghub_plugin.sdk_config import DEFAULT_PRESETS

HOST_SHA256 = "86A6FD69060588631D023AE97BB3CB58370CD1CF898B8612C7C52A43A55301A2"


def verify(report):
    for key in ("installed_host_modified", "installed_config_modified", "network_used",
                "child_process_started", "physical_device_used", "native_http_tested", "native_gui_tested"):
        if report.get(key) is not False:
            raise ValueError("Native catalog evidence exceeds the isolated boundary")
    if (report.get("host_exe_sha256", "").upper() != HOST_SHA256
            or report.get("native_asgi_router_tested") is not True
            or report.get("asgi_status") != 200
            or "/api/bilibili/pulse-presets" not in report.get("routes", [])):
        raise ValueError("Native catalog route evidence does not match DGHub 1.9.6")
    fixture = Path(report["fixture"]).resolve()
    expected = ROOT / "build/dghub_import_probe_20261008"
    if (not fixture.is_relative_to(expected) or fixture == expected
            or not report.get("manager_paths")
            or any(not Path(path).resolve().is_relative_to(fixture) for path in report["manager_paths"].values())):
        raise ValueError("Native preset files are outside the owned fixture")
    body = report["asgi_body_utf8"].encode("utf-8")
    headers = report["asgi_headers"]
    if (headers.get("content-type") != "application/json" or int(headers.get("content-length", -1)) != len(body)
            or "transfer-encoding" in headers):
        raise ValueError("Native HTTP response differs from the supported bounded parser")
    names = decode_names(body)
    if json.loads(body) != report["response"] or not set(DEFAULT_PRESETS.values()).issubset(names):
        raise ValueError("Default event presets do not match native catalog")
    source_hash = hashlib.sha256((ROOT / "tools/dghub_catalog_contract_probe.py").read_bytes()).hexdigest()
    if source_hash != report.get("probe_source_sha256"):
        raise ValueError("Native catalog evidence comes from a stale probe")
    return dict(status="ok", scope="actual isolated manager and ASGI route; no native TCP/GUI/device",
        names=sorted(names), default_five_present=True, response_sha256=hashlib.sha256(body).hexdigest(),
        native_http_tested=False, native_asgi_router_tested=True, native_gui_tested=False,
        physical_device_used=False, installed_host_modified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify(json.loads(args.native_report.read_text(encoding="utf-8")))
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(status=result["status"], presets=len(result["names"]), default_five_present=True)))


if __name__ == "__main__":
    main()
