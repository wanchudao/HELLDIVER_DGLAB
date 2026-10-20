"""Sequential checks of extracted delivery artifacts; preserve each new record.

No game control, installation, firewall changes or physical device access.
This is an additional acceptance run, not a replacement for earlier evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(edition, bundle, output):
    bundle, output = bundle.resolve(), output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        raise ValueError("Additional evidence must stay under build")
    output.mkdir(parents=True, exist_ok=False)
    exe = bundle / ("HELLDIVER_Main.exe" if edition == "main" else "helldiver_dglab.exe")
    info = json.loads((bundle / ("BUILD_INFO.json" if edition == "main" else "BUILD_MANIFEST.json")).read_text(encoding="utf-8"))
    if digest(exe) != info["exe_sha256"]:
        raise ValueError("Extracted EXE differs from the delivered package")
    archive = ROOT / "release/resolution_20261017" / (
        "HELLDIVER_Main_v1.0.0_Beta3_MultiResolution_20261017.zip" if edition == "main" else
        "HELLDIVER_DGHub_1.0.7_MultiResolution_20261017.zip")
    summary = dict(status="running", edition=edition, exe_sha256=digest(exe),
        delivered_zip_sha256=digest(archive), started_utc=datetime.now(timezone.utc).isoformat(),
        sequential=True, game_controlled=False, physical_device_used=False,
        firewall_modified=False, installed_configuration_modified=False, cases=[])
    samples = str(ROOT / "screenshots/hud_1080p_20261002")
    external = {"protocol_full":ROOT/"tools/dghub_frozen_protocol_acceptance.py",
                "normal_startup":ROOT/"tools/dghub_normal_startup_acceptance.py",
                "process_freeze":ROOT/"tools/dghub_frozen_freeze_acceptance.py"}
    cases = ([
        ("resolution_original_60s", ["--check-resolution", "--hud-samples", samples], 60),
        ("resolution_protocol_1440", ["--check-resolution-protocol", "2560x1440"], 90),
        ("resolution_protocol_2160", ["--check-resolution-protocol", "3840x2160"], 90),
        ("hud_ui_original_45s", ["--check-hud-ui", "--hud-samples", samples,
             "--ui-screenshots", str(output / "hud_ui")], 45),
        ("panel", ["--check-panel"], 15),
        ("theme", ["--check-theme", "--ui-screenshots", str(output / "theme")], 45),
        ("feedback_protocol", ["--check-feedback"], 25),
        ("threshold", ["--check-threshold"], 25),
        ("death", ["--check-death"], 35),
    ] if edition == "main" else [
        ("resolution", ["--check-resolution", "--hud-samples", samples], 60),
        ("live_panel", ["--check-live-panel"], 20),
        ("protocol_full", ["--bundle",str(bundle),"--resolution-cases"], 600),
        ("normal_startup", ["--exe",str(exe)], 25),
        ("process_freeze", ["--bundle",str(bundle)], 25),
    ])
    for name, arguments, timeout in cases:
        report_path = output / (name + ".json")
        started = time.monotonic()
        timed_out = False
        with (output / (name + ".stdout.log")).open("xb") as stdout, (output / (name + ".stderr.log")).open("xb") as stderr:
            try:
                command = ([sys.executable,str(external[name])] if name in external else [str(exe)])
                process = subprocess.run([*command, *arguments, "--report", str(report_path)],
                    cwd=bundle, stdout=stdout, stderr=stderr, timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out, code = True, 124
        record = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        passed = code == 0 and not timed_out and record.get("status") == "ok"
        summary["cases"].append(dict(case=name, status="ok" if passed else "incomplete",
            elapsed_seconds=round(time.monotonic()-started,3), hard_timeout_seconds=timeout,
            hard_timeout=timed_out, exit_code=code, report=report_path.name,
            report_sha256=digest(report_path) if report_path.is_file() else None))
        summary["status"] = "running" if passed else "incomplete"
        (output / "summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(summary["cases"][-1],ensure_ascii=False),flush=True)
        if not passed:
            return 1
    summary.update(status="ok",ended_utc=datetime.now(timezone.utc).isoformat())
    (output / "summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0


def run_regression(output):
    """Retain native diagnostics alongside exact completed test counts."""
    output = output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        raise ValueError("Additional evidence must stay under build")
    output.mkdir(parents=True,exist_ok=False)
    log, junit = output / "regression.log", output / "pytest.xml"
    started = time.monotonic()
    timed_out = False
    with log.open("xb") as stream:
        try:
            completed = subprocess.run([sys.executable,"-X","utf8","-m","pytest","-vv",
                "--capture=tee-sys","--junitxml="+str(junit)],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                timeout=180,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out, code = True, 124
    suites = ET.parse(junit).getroot().findall("testsuite") if junit.is_file() else []
    counts = {key:sum(int(suite.get(key,"0")) for suite in suites)
              for key in ("tests","failures","errors","skipped")}
    lines = log.read_text(encoding="utf-8",errors="replace").splitlines()
    diagnostics = [dict(line=index+1,context=lines[max(0,index-2):index+3])
        for index,line in enumerate(lines) if "fatal exception" in line.lower()
        or "fatal python error" in line.lower() or "access violation" in line.lower()]
    passed = code == 0 and not timed_out and counts["tests"] > 0 and not any(
        counts[key] for key in ("failures","errors","skipped"))
    report = dict(status="passed_with_native_diagnostic" if passed and diagnostics else "ok" if passed else "incomplete",
        exit_code=code,hard_timeout=timed_out,hard_timeout_seconds=180,
        elapsed_seconds=round(time.monotonic()-started,3),counts=counts,tests_passed=passed,
        native_diagnostics=diagnostics,log_sha256=digest(log),
        junit_sha256=digest(junit) if junit.is_file() else None,
        source_regression_not_frozen_exe=True,physical_device_used=False,game_controlled=False)
    (output/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition",choices=("main","dghub","regression"),required=True)
    parser.add_argument("--bundle",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    if args.edition == "regression":
        raise SystemExit(run_regression(args.output))
    if args.bundle is None:
        parser.error("--bundle is required for a frozen edition")
    raise SystemExit(run(args.edition,args.bundle,args.output))
