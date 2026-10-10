"""Run each existing Mock video replay in an owned, time-bounded process.

The decoder and recognition algorithm stay in tools.video_acceptance. A stalled
native decoder can only block its child process, not the regression test runner.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def _stop_owned(process):
    """Terminate only the process created by this call, then reap it."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def evaluate_isolated(path, config, sample=None, timeout_seconds=120):
    """Return a compact result; timeout/crash/failed assertions never become ok."""
    timeout_seconds = float(timeout_seconds)
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
        raise ValueError("Video timeout must be positive and at most 120 seconds")
    path = Path(path).resolve()
    copied = deepcopy(config)
    copied.setdefault("trigger", {})["sender_mode"] = "mock"
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="helldiver_mock_video_") as directory:
        directory = Path(directory)
        request = directory / "request.json"
        response = directory / "response.json"
        request.write_text(json.dumps({"video": str(path), "config": copied,
                                       "sample": sample or {}}, ensure_ascii=False,
                                      allow_nan=False), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker", str(request), str(response)],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                _stop_owned(process)
                return {"status": "timeout", "file": str(path), "offline_only": True,
                        "sender_mode": "mock", "timeout_seconds": timeout_seconds,
                        "elapsed_seconds": round(time.monotonic() - start, 3),
                        "regression_passed": False, "worker_reaped": process.poll() is not None,
                        "error": "Mock replay exceeded its hard timeout; video validation incomplete"}
            except BaseException:
                _stop_owned(process)
                raise
        try:
            result = json.loads(response.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result = {"status": "failed", "file": str(path), "offline_only": True,
                      "sender_mode": "mock", "regression_passed": False,
                      "error": f"Worker returned no valid result: {type(exc).__name__}"}
        result["worker_exit_code"] = returncode
        result["elapsed_seconds"] = round(time.monotonic() - start, 3)
        if returncode != 0:
            result["status"] = "failed"
            result["regression_passed"] = False
        return result


def _worker(request, response):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools import video_acceptance as video

    try:
        payload = json.loads(Path(request).read_text(encoding="utf-8"))
        payload["config"].setdefault("trigger", {})["sender_mode"] = "mock"
        rows, summary, logs = video.evaluate_video(Path(payload["video"]), payload["config"])
        checks = video.check_expectations(rows, summary, payload["sample"])
        complete_logs = len(logs) == len(rows) + 2
        passed = all(check["passed"] for check in checks) and complete_logs and summary["final_strength"] == 0
        result = {"status": "ok" if passed else "failed", "offline_only": True,
                  "sender_mode": "mock", "summary": summary, "checks": checks,
                  "rows": len(rows), "log_entries": len(logs),
                  "regression_passed": passed}
    except Exception as exc:
        result = {"status": "failed", "offline_only": True, "sender_mode": "mock",
                  "regression_passed": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(response).write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                        allow_nan=False), encoding="utf-8")
    return 0 if result["status"] == "ok" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", nargs="*", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.ini")
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--worker", nargs=2, metavar=("REQUEST", "RESPONSE"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return _worker(*args.worker)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.config_loader import load_config
    samples = {}
    if args.regression:
        if args.videos:
            parser.error("--regression uses pinned fixtures; omit positional videos")
        manifest = json.loads((ROOT / "tests/fixtures/video_acceptance.json").read_text(encoding="utf-8"))
        samples = {sample["file"]: sample for sample in manifest["samples"]}
        args.videos = [ROOT / "screenshots/video" / name for name in samples]
    if not args.videos:
        parser.error("Pass local video paths or --regression")
    cfg = load_config(args.config)
    results = [evaluate_isolated(path, cfg, samples.get(path.name), args.timeout) for path in args.videos]
    report = {"status": "ok" if all(result["status"] == "ok" for result in results) else "failed",
              "offline_only": True, "sender_mode": "mock", "timeout_seconds": args.timeout,
              "videos": results}
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    print(rendered)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
