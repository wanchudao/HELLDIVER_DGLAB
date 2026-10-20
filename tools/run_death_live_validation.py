"""Explicit scoped final-main QA with per-process hard timeouts; no installed settings."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run(exe, *, ui=False, resolution_only=False, report_prefix="death_live_main", stamp="20261014", screenshots=None):
    prefix = ROOT / "build" / report_prefix
    report = lambda name: str(prefix.with_name(prefix.name + "_" + name + "_" + stamp + ".json"))
    shots = Path(screenshots).resolve() if screenshots else ROOT / "build/death_live_20261014/main_theme_final"
    cases = ([
        ("panel", ["--check-panel"], 15),
        ("hud_ui", ["--check-hud-ui", "--hud-samples", str(ROOT / "screenshots/hud_1080p_20261002")], 45),
        ("theme", ["--check-theme", "--ui-screenshots", str(shots)], 45),
    ] if ui else [
        ("selfcheck", ["--self-check"], 15),
        ("images", ["--check-images", *map(str, (ROOT / "screenshots/full_health/20260621135510_1.jpg",
             ROOT / "screenshots/injured/20260621135431_1.jpg", ROOT / "screenshots/death/20260621135533_1.jpg"))], 15),
        ("hud", ["--check-hud", "--hud-samples", str(ROOT / "screenshots/hud_1080p_20261002")], 20),
        ("protocol", ["--check-feedback"], 25),
        ("threshold", ["--check-threshold"], 25),
        ("death", ["--check-death"], 35),
        ("firewall", ["--check-firewall"], 15),
    ])
    if resolution_only:
        if ui:
            raise ValueError("Run UI and resolution checks separately")
        cases = [
            # A batch decodes hundreds of full-size images. This is its total
            # process budget, not a production capture/frame freshness limit.
            ("resolution", ["--check-resolution", "--hud-samples", str(ROOT / "screenshots/hud_1080p_20261002")], 180),
            ("resolution_protocol_1440", ["--check-resolution-protocol", "2560x1440"], 90),
            ("resolution_protocol_2160", ["--check-resolution-protocol", "3840x2160"], 90),
        ]
    for name, arguments, timeout in cases:
        target = Path(report(name))
        if target.exists():
            raise FileExistsError("Preserve existing evidence; choose a fresh report prefix: " + str(target))
        started = time.monotonic()
        try:
            process = subprocess.run([str(exe), *arguments, "--report", str(target)],
                cwd=exe.parent, timeout=timeout, capture_output=True)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Final EXE acceptance exceeded hard timeout: " + name)
        if process.returncode != 0 or not target.is_file():
            raise RuntimeError("Final EXE acceptance failed: " + name + " exit=" + str(process.returncode))
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["status"] == "ok", name
        print(json.dumps(dict(case=name, status="ok", report=str(target),
            process_elapsed_seconds=round(time.monotonic()-started,3),
            process_hard_timeout_seconds=timeout), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--resolution-only", action="store_true")
    parser.add_argument("--report-prefix", default="death_live_main")
    parser.add_argument("--stamp", default="20261014")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    run(args.exe.resolve(), ui=args.ui, resolution_only=args.resolution_only,
        report_prefix=args.report_prefix,stamp=args.stamp,screenshots=args.screenshots)
