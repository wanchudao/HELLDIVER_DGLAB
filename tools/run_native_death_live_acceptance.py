"""Stage an owned copy of inspected DGHub 1.9.7 and run import/config/entry QA."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(bundle, fixture_name="native_runtime197"):
    from tools.dghub_native_strategy_acceptance import sha256
    from tools.dghub_package_candidate import pack
    if not fixture_name.isascii() or not fixture_name.replace('_', '').isalnum():
        raise ValueError("Use a simple owned fixture name")
    native = ROOT / "build/death_live_20261014" / fixture_name
    if native.exists():
        raise FileExistsError("Preserve native runtime evidence; choose a new fixture")
    host = Path(os.environ["LOCALAPPDATA"]) / "Programs/DGHub/DGLab-Console.exe"
    assert sha256(host) == "4734915664f45476111ea1b7590024f396784af869f71086d721313fbe5a7dc5"
    native.mkdir(parents=True)
    isolated = native / host.name
    shutil.copy2(host, isolated)
    library = native / "_internal"
    assert library.resolve().is_relative_to((ROOT / "build").resolve())
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "New-Item -ItemType Junction -Path " + quote(library) + " -Target " + quote(host.parent / "_internal") + " | Out-Null"],
        timeout=8, check=True, capture_output=True)
    packed = pack(bundle, native / "candidate_import.zip")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("DGHUB_")}
    environment.update(HELLDIVER_NATIVE_RUNTIME_ROOT=str(native), PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run([str(isolated), "--plugin-runner", str(ROOT / "tools/dghub_threshold_native_runtime.py")],
        cwd=native, env=environment, capture_output=True, timeout=60)
    report = json.loads((native / "runtime_report.json").read_text(encoding="utf-8"))
    assert completed.returncode == 0 and report["status"] == "ok", report
    assert report["exe_sha256"] == packed["exe_sha256"]
    print(json.dumps({key: report[key] for key in ("status", "exe_sha256", "host_exe_sha256",
        "death_mode_roundtrips", "live_mode_pending_and_reenable_verified", "no_positive_output")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--fixture-name", default="native_runtime197")
    args = parser.parse_args()
    run(args.bundle.resolve(), args.fixture_name)
