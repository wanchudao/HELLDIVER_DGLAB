"""Observe actual-host state around external Windows F8 input; never send SDK intents.

Run ready -> use Computer Use F8 -> running -> F8 -> paused -> disable -> stopped.
No device, other active plugin, credentials, or account/system settings permitted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.dghub_installed_runtime_acceptance import plugin, request, safe_status


def collect():
    current, installed = safe_status(), plugin()
    fields = current.get("plugins_status", {}).get("helldiver_dglab", {})
    if any(row.get("active") for row in request("/api/plugins")["plugins"]
           if row["name"] != "helldiver_dglab"):
        raise RuntimeError("Refuse other active plugins")
    selected = {key: fields.get(key) for key in
                ("capture_state", "output_ready", "running", "recognition_running",
                 "pause_reason", "hp", "requested_a_pct", "requested_b_pct",
                 "hotkey_ready", "hotkey_diagnostic", "session_record")}
    selected.update(active=installed.get("active"), enabled=installed.get("enabled"),
                    device_connected=False, strength_a=current.get("strength_a"),
                    strength_b=current.get("strength_b"))
    if selected["strength_a"] != 0 or selected["strength_b"] != 0:
        raise RuntimeError("Refuse nonzero host output")
    return selected


def observe(phase, report_path):
    if not report_path.resolve().is_relative_to(ROOT / "build"):
        raise ValueError("Keep test report under build")
    if phase == "ready":
        if report_path.exists():
            raise FileExistsError("Preserve prior evidence")
        installed = Path(os.environ["LOCALAPPDATA"]) / "Programs/DGHub/plugins/helldiver_dglab"
        report = dict(status="incomplete", native_host_tested=True, physical_device_used=False,
                      real_game_tested=False, physical_keyboard_used=False,
                      input_method="Computer Use Windows sky.press_key F8; not injected SDK/UI intent",
                      exe_sha256=hashlib.sha256((installed / "helldiver_dglab.exe").read_bytes()).hexdigest(),
                      observations=[])
    else:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        previous = {row["phase"] for row in report["observations"]}
        if phase not in {"running", "paused", "stopped"} or phase in previous:
            raise ValueError("Invalid or repeated phase")
    state, began, matched = None, time.monotonic(), False
    while time.monotonic() - began < 4:
        state = collect()
        common = state["active"] and state["capture_state"] == "ok" and state["hotkey_ready"] is True
        if phase in {"ready", "paused"}:
            matched = common and state["running"] is False and state["recognition_running"] is False
            if phase == "paused":
                matched &= state["pause_reason"] == "paused"
        elif phase == "running":
            matched = common and state["running"] is True and state["recognition_running"] is True
        else:
            matched = state["active"] is False and state["enabled"] is False
        if matched:
            break
        time.sleep(.05)
    report["observations"].append(dict(phase=phase, matched=matched, state=state))
    passed = {row["phase"] for row in report["observations"] if row["matched"]}
    report["native_desktop_f8_tested"] = {"ready", "running", "paused"} <= passed
    report["final_both_zero"] = state["strength_a"] == state["strength_b"] == 0
    if passed == {"ready", "running", "paused", "stopped"}:
        report["status"] = "ok"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(phase=phase, matched=matched, state=state), ensure_ascii=False))
    return 0 if matched else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("ready", "running", "paused", "stopped"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    return observe(args.phase, args.report.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
