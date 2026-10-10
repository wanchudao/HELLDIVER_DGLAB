"""Deterministic, strictly Mock MP4 regression using the production core.

Run ``python tools/video_acceptance.py --regression`` for the supplied fixtures,
or pass local video paths. No capture, hardware sender, DGHub or socket is used.
Reports are new files; existing trigger logs/configuration are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import json
import logging
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2

from src.config_loader import load_config
from src.trigger import TriggerDispatch
from src.vision_pipeline import VisionPipeline


class ReplayLog:
    """Keep every frame in memory synchronously; no shared log writer/thread."""

    def __init__(self):
        self.entries = []
        self.closed = False

    def append(self, entry):
        self.entries.append(dict(entry))

    def close(self):
        self.closed = True


def video_config(config: dict, width: int, height: int) -> dict:
    """Scale calibration coordinates for offline video ONLY, never live capture.

    Nonuniform resizing would misrepresent the calibrated HUD, so reject it.
    A copied config is always forced to Mock, including user hardware configs.
    """
    result = deepcopy(config)
    resolution = str(result.get("calibration", {}).get("resolution", ""))
    try:
        source_w, source_h = map(int, resolution.lower().split("x"))
    except ValueError as exc:
        raise ValueError("Video replay requires calibration.resolution = WIDTHxHEIGHT") from exc
    if min(width, height, source_w, source_h) <= 0:
        raise ValueError("Video/calibration dimensions must be positive")
    sx, sy = width / source_w, height / source_h
    if not math.isclose(sx, sy, rel_tol=0.002):
        raise ValueError("Video aspect ratio differs from calibration; provide a matching config")
    roi = result["ROI"]
    for name in ("outer_x1", "outer_x2", "inner_x1", "inner_x2", "bar_min_x", "bar_max_x"):
        roi[name] = round(float(roi[name]) * sx)
    for name in ("outer_y1", "outer_y2", "inner_y1", "inner_y2"):
        roi[name] = round(float(roi[name]) * sy)
    for name in ("cap_ignore_left", "cap_ignore_right"):
        roi[name] = round(float(roi[name]) * sx)
    for name, value in result.get("filters", {}).items():
        if name.startswith("edge_region_"):
            parts = [float(part.strip()) for part in str(value).split(",")]
            if len(parts) != 5:
                raise ValueError(f"Invalid {name}")
            scaled = [round(parts[0] * sx), round(parts[1] * sy),
                      round(parts[2] * sx), round(parts[3] * sy), parts[4]]
            result["filters"][name] = ", ".join(map(str, scaled))
    result.setdefault("trigger", {})["sender_mode"] = "mock"
    # Scaling is not a human calibration and must not become hardware permission.
    result["calibration"] = {"resolution": f"{width}x{height}", "calibrated": 0}
    return result


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_video(path: Path, config: dict, progress=None):
    """Replay all decodable frames against presentation timestamps and Mock time."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"Local video not found: {path}")
    capture = cv2.VideoCapture(str(path))
    trigger = None
    log = ReplayLog()
    rows = []
    now = 0.0
    fallback_count = 0
    try:
        if not capture.isOpened():
            raise ValueError(f"Cannot decode video: {path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Video does not expose a finite positive frame rate")
        expected_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError("Video contains no decodable frames")
        height, width = frame.shape[:2]
        scaled = video_config(config, width, height)
        pipeline = VisionPipeline(scaled)
        trigger = TriggerDispatch(scaled, clock=lambda: now, log_writer=log)
        previous_time = None
        origin = None
        while ok and frame is not None:
            index = len(rows)
            if frame.shape[:2] != (height, width):
                raise ValueError(f"Frame dimensions changed at frame {index}")
            pts = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
            if origin is None:
                origin = pts if math.isfinite(pts) and pts >= 0 else 0.0
            candidate = pts - origin
            if (not math.isfinite(candidate) or candidate < 0
                    or (previous_time is not None and candidate <= previous_time)):
                # Record rather than hide codec timestamp fallback. Advance from
                # the last accepted time; never move backwards in a VFR stream.
                now = 0.0 if previous_time is None else previous_time + 1 / fps
                fallback_count += 1
            else:
                now = candidate
            previous_time = now
            result, roi, health = pipeline.process(frame, timestamp=now)
            event = trigger.dispatch(result)
            output = trigger.mock_output()
            debug = getattr(result, "debug", {})
            record = {
                "frame": index, "time_sec": round(now, 6),
                "state": result.state, "raw_state": debug.get("raw_state", ""),
                "valid": bool(result.valid),
                "health_raw": round(float(result.health_raw), 6),
                "health_observed": round(float(debug.get("health_observed", health.health_raw)), 6),
                "health_stable": round(float(result.health_stable), 6),
                "confidence": round(float(result.confidence), 6),
                "anchor_score": round(float(roi.anchor_score), 6),
                "death_score": round(float(result.death_score), 6),
                "reject_reason": debug.get("reject_reason", ""),
                "actions": ",".join(event.get("actions", [])),
                "reason": event.get("reason", ""),
                "damage_pct": log.entries[-1].get("damage_pct", 0.0),
                "low_health": bool(event.get("low_health", False)),
                "healing_sustain": bool(event.get("healing_sustain", False)),
                "strength": output["strength"], "waveform": output["waveform"],
            }
            rows.append(record)
            if progress and (index % 300 == 0):
                progress(index + 1, expected_frames)
            ok, frame = capture.read()
        if expected_frames > 0 and len(rows) != expected_frames:
            raise ValueError(f"Incomplete decode: {len(rows)}/{expected_frames} frames")
    finally:
        try:
            if trigger is not None:
                trigger.safe_stop()
        finally:
            capture.release()
    actions = Counter(action for row in rows for action in row["actions"].split(",") if action)
    summary = {
        "file": str(path), "sha256": file_hash(path), "offline_only": True,
        "sender_mode": "mock", "frames": len(rows), "fps": fps,
        "resolution": [width, height], "timestamp_fallback_frames": fallback_count,
        "valid_frames": sum(row["valid"] for row in rows),
        "state_counts": dict(Counter(row["state"] for row in rows)),
        "action_counts": dict(actions),
        "nonzero_frames": sum(row["strength"] > 0 for row in rows),
        "max_strength": max(row["strength"] for row in rows),
        "final_strength": trigger.mock_output()["strength"],
        "effective_config": scaled,
        "source_sha256": {name: (file_hash(ROOT / "src" / name)
                                 if (ROOT / "src" / name).is_file() else None) for name in (
            "roi_locator.py", "health_detector.py", "effect_detector.py",
            "vision_pipeline.py", "state_machine.py", "trigger.py", "feedback_mixer.py")},
        "limitations": "Known sample regression only, not general accuracy or device acceptance.",
    }
    return rows, summary, log.entries


def check_expectations(rows, summary, sample):
    """Explicit annotated assertions; filenames alone never imply ground truth."""
    checks = []
    for key in ("sha256", "frames"):
        if key in sample:
            checks.append({"check": key, "passed": summary[key] == sample[key],
                           "actual": summary[key], "expected": sample[key]})
    for rule in sample.get("checks", []):
        selected = [row for row in rows
                    if rule.get("start", 0) <= row["time_sec"] < rule.get("end", math.inf)]
        field = rule["field"]
        if field == "action":
            actual = sum(rule["value"] in row["actions"].split(",") for row in selected)
        else:
            actual = sum(row[field] == rule["value"] for row in selected)
        passed = bool(selected) and rule.get("min", 0) <= actual <= rule.get("max", math.inf)
        if rule.get("all", False):
            passed = passed and actual == len(selected)
        checks.append({"check": rule.get("name", field), "passed": passed,
                       "actual": actual, "selected_frames": len(selected), "rule": rule})
    return checks


def write_reports(output_dir: Path, stem: str, rows, summary, logs):
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / f"{stem}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    csv_path, json_path, log_path = (Path(str(prefix) + suffix)
                                    for suffix in (".csv", ".json", "_mock.jsonl"))
    with csv_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with json_path.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
    with log_path.open("x", encoding="utf-8") as stream:
        for entry in logs:
            stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
    return csv_path, json_path, log_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", nargs="*", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.ini")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build" / "video_acceptance")
    parser.add_argument("--regression", action="store_true", help="Check the two supplied, hash-pinned MP4s")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)
    samples = {}
    if args.regression:
        manifest = json.loads((ROOT / "tests" / "fixtures" / "video_acceptance.json").read_text(encoding="utf-8"))
        samples = {entry["file"]: entry for entry in manifest["samples"]}
        if args.videos:
            parser.error("--regression uses the pinned fixtures; omit positional videos")
        args.videos = [ROOT / "screenshots" / "video" / name for name in samples]
    if not args.videos:
        parser.error("Pass local video paths or --regression")
    config = load_config(args.config)
    failed = False
    for path in args.videos:
        try:
            rows, summary, logs = evaluate_video(path, config)
            summary["checks"] = check_expectations(rows, summary, samples.get(path.name, {}))
            summary["regression_passed"] = (all(check["passed"] for check in summary["checks"])
                                              if summary["checks"] else None)
            failed |= summary["regression_passed"] is False
            paths = write_reports(args.output_dir, path.stem, rows, summary, logs)
            print(json.dumps({key: summary[key] for key in (
                "file", "frames", "valid_frames", "action_counts", "nonzero_frames",
                "final_strength", "regression_passed")}, ensure_ascii=False))
            print(f"Report: {paths[1]}", flush=True)
        except Exception as exc:
            failed = True
            print(f"FAILED {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
