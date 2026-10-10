"""HELLDIVER_DGLAB V0.3 screenshot/video acceptance GUI (strictly offline).

Video replay uses the production trigger in forced Mock mode. It does not open
a socket, start DGHub, or access a physical device.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import sys
import threading
import traceback


if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys._MEIPASS)
    EXE_DIR = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    EXE_DIR = APP_ROOT
sys.path.insert(0, str(APP_ROOT))

# Bound image decode memory before importing OpenCV. This is an offline tester,
# but screenshot files are still untrusted input.
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(40_000_000))
import cv2
import numpy as np

from src.config_loader import load_config
from src.vision_pipeline import VisionPipeline


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
DERIVED_MARKERS = ("_crop", "crop_", "zoom", "thumb")
LABEL_ALIASES = {
    "full_health": "full_health",
    "full": "full_health",
    "满血": "full_health",
    "injured": "injured",
    "injury": "injured",
    "受伤": "injured",
    "death": "death",
    "dead": "death",
    "死亡": "death",
    "healing": "healing",
    "heal": "healing",
    "治疗": "healing",
}


def default_config_path() -> Path:
    """Prefer an operator-provided sidecar, otherwise use bundled safe defaults."""
    sidecar = EXE_DIR / "config.ini"
    if sidecar.is_file():
        return sidecar
    return APP_ROOT / "config.example.ini"


def _read_image(path: Path):
    """Unicode-safe OpenCV read on Windows."""
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            return None
        encoded = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except (OSError, ValueError, cv2.error):
        return None


def collect_images(root: Path) -> list[Path]:
    """Collect original images recursively while excluding generated crop helpers."""
    if not root.is_dir():
        raise ValueError(f"截图目录不存在：{root}")
    paths = []
    for path in root.rglob("*"):
        if (path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
                and not any(marker in path.stem.lower() for marker in DERIVED_MARKERS)):
            paths.append(path)
    return sorted(paths, key=lambda item: str(item.relative_to(root)).casefold())


def infer_expected_and_group(path: Path, root: Path) -> tuple[str, str]:
    """Infer an optional truth label and sequence group from folder names."""
    relative = path.relative_to(root)
    directories = list(relative.parts[:-1])
    expected = ""
    label_index = -1
    for index, part in enumerate(directories):
        normalized = LABEL_ALIASES.get(part.casefold())
        if normalized:
            expected, label_index = normalized, index
            break
    if expected == "healing" and label_index + 1 < len(directories):
        group = "/".join(directories[:label_index + 2])
    elif expected:
        group = "/".join(directories[:label_index + 1])
    else:
        group = "/".join(directories) or "未标注"
    return expected, group


def evaluate_screenshots(root: Path, config_path: Path, progress=None) -> tuple[list[dict], dict]:
    """Run the real vision pipeline over each ordered screenshot sequence."""
    images = collect_images(root)
    if not images:
        raise ValueError("所选目录及子目录中没有可读取的截图")
    config = load_config(config_path)
    grouped: dict[str, list[tuple[Path, str]]] = {}
    for path in images:
        expected, group = infer_expected_and_group(path, root)
        grouped.setdefault(group, []).append((path, expected))

    records: list[dict] = []
    completed = 0
    for group, members in grouped.items():
        pipeline = VisionPipeline(config)
        for index, (path, expected) in enumerate(members):
            frame = _read_image(path)
            if frame is None:
                # A missing/corrupt observation breaks the sequence just as an
                # unknown live frame does. Do not compare the next good image
                # with stale health from before the decode failure.
                pipeline.reset()
                record = {
                    "file": str(path.relative_to(root)), "group": group,
                    "expected": expected, "state": "error", "valid": False,
                    "health_raw": None, "health_stable": None,
                    "confidence": 0.0, "anchor_score": 0.0,
                    "red_score": 0.0, "death_score": 0.0,
                    "match": False if expected else None,
                    "error": "无法读取图片",
                }
            else:
                # A deterministic 30fps sequence is adequate for visual state debounce.
                # Trigger timing (including its 0.2s damage window) is tested separately.
                try:
                    result, roi, health = pipeline.process(frame, timestamp=index / 30.0)
                    record = {
                        "file": str(path.relative_to(root)), "group": group,
                        "expected": expected, "state": result.state,
                        "valid": bool(result.valid),
                        "health_raw": round(float(result.health_raw), 6),
                        "health_stable": round(float(result.health_stable), 6),
                        "confidence": round(float(result.confidence), 6),
                        "anchor_score": round(float(roi.anchor_score), 6),
                        "red_score": round(float(result.red_score), 6),
                        "death_score": round(float(result.death_score), 6),
                        "match": (bool(result.valid) and result.state == expected)
                        if expected else None,
                        "error": "",
                    }
                except Exception as exc:
                    pipeline.reset()
                    record = {
                        "file": str(path.relative_to(root)), "group": group,
                        "expected": expected, "state": "error", "valid": False,
                        "health_raw": None, "health_stable": None,
                        "confidence": 0.0, "anchor_score": 0.0,
                        "red_score": 0.0, "death_score": 0.0,
                        "match": False if expected else None,
                        "error": f"处理失败：{type(exc).__name__}: {exc}",
                    }
            records.append(record)
            completed += 1
            if progress:
                progress(completed, len(images), record)

    labelled = [record for record in records if record["expected"]]
    matched = sum(record["match"] is True for record in labelled)
    unknown = sum(record["state"] == "unknown" for record in records)
    errors = sum(bool(record["error"]) for record in records)
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "screenshot_root": str(root.resolve()),
        "config": str(config_path.resolve()),
        "total_images": len(records),
        "labelled_images": len(labelled),
        "matched_images": matched,
        "label_match_rate": round(matched / len(labelled), 6) if labelled else None,
        "valid_images": sum(record["valid"] for record in records),
        "unknown_images": unknown,
        "read_errors": errors,
        "offline_only": True,
        "limitations": [
            "不测试屏幕实时捕获、0.2秒伤害窗口、设备输出或DGHub",
            "目录标签不是独立逐帧人工标注，不能单独证明SPEC准确率目标",
        ],
    }
    return records, summary


def write_reports(root: Path, records: list[dict], summary: dict) -> tuple[Path, Path]:
    """Persist UTF-8 CSV/JSON under the explicitly selected screenshot tree."""
    report_dir = root / "v03_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    csv_path = report_dir / f"v03_screenshot_report_{stamp}.csv"
    json_path = report_dir / f"v03_screenshot_report_{stamp}.json"
    with csv_path.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    with json_path.open("x", encoding="utf-8") as stream:
        json.dump({"summary": summary, "records": records}, stream,
                  ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return csv_path, json_path


def evaluate_video_file(path: Path, config_path: Path, progress=None,
                        *, output_dir: Path | None = None):
    """Replay a selected local MP4 and save its strictly Mock event reports."""
    from tools.video_acceptance import evaluate_video, write_reports as write_video_reports

    config = load_config(config_path)
    config.setdefault("trigger", {})["sender_mode"] = "mock"
    rows, summary, logs = evaluate_video(path, config, progress=progress)
    report_dir = output_dir if output_dir is not None else path.parent / "v03_video_reports"
    paths = write_video_reports(report_dir, path.stem,
                                rows, summary, logs)
    return summary, paths


def _window_type():
    """Keep Tk construction separate so worker/UI transitions can be tested headlessly."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    class AcceptanceWindow(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("HELLDIVER_DGLAB V0.3 截图与视频验收（离线）")
            self.geometry("920x620")
            self.minsize(760, 480)
            self.config_path = default_config_path()
            self.messages: queue.Queue = queue.Queue()
            self.running = False

            header = ttk.Label(
                self, text="V0.3 截图与视频验收 — 不连接 DGHub，不连接设备",
                font=("Microsoft YaHei UI", 14, "bold"),
            )
            header.pack(anchor="w", padx=14, pady=(14, 5))
            ttk.Label(
                self,
                text="选择分类截图目录，或选择 MP4 回放血量变化与 Mock 反馈事件。",
            ).pack(anchor="w", padx=14)

            config_row = ttk.Frame(self)
            config_row.pack(fill="x", padx=14, pady=8)
            ttk.Label(config_row, text="配置：").pack(side="left")
            self.config_label = ttk.Label(config_row, text=str(self.config_path))
            self.config_label.pack(side="left", fill="x", expand=True)
            self.config_button = ttk.Button(config_row, text="选择配置", command=self.choose_config)
            self.config_button.pack(side="right")

            action_row = ttk.Frame(self)
            action_row.pack(fill="x", padx=14)
            self.run_button = ttk.Button(action_row, text="选择截图目录并开始", command=self.choose_and_run)
            self.run_button.pack(side="left")
            self.video_button = ttk.Button(action_row, text="选择 MP4 并开始", command=self.choose_video)
            self.video_button.pack(side="left", padx=(8, 0))
            self.progress = ttk.Progressbar(action_row, orient="horizontal", mode="determinate")
            self.progress.pack(side="left", fill="x", expand=True, padx=12)
            self.status = ttk.Label(action_row, text="就绪")
            self.status.pack(side="right")

            self.output = ScrolledText(self, wrap="none", font=("Consolas", 10))
            self.output.pack(fill="both", expand=True, padx=14, pady=12)
            self.output.insert("end", "纯离线测试。截图报告：v03_reports；视频报告：视频旁的 v03_video_reports。\n")
            self.output.configure(state="disabled")
            self.after(80, self.poll_messages)

        def append(self, text):
            self.output.configure(state="normal")
            self.output.insert("end", text + "\n")
            self.output.see("end")
            self.output.configure(state="disabled")

        def choose_config(self):
            selected = filedialog.askopenfilename(
                title="选择V0.3配置文件", filetypes=[("INI配置", "*.ini"), ("所有文件", "*")],
                initialdir=str(self.config_path.parent),
            )
            if selected:
                self.config_path = Path(selected)
                self.config_label.configure(text=selected)

        def choose_and_run(self):
            if self.running:
                return
            initial = EXE_DIR / "screenshots"
            selected = filedialog.askdirectory(
                title="选择截图根目录", initialdir=str(initial if initial.is_dir() else EXE_DIR),
            )
            if not selected:
                return
            self.start_worker(Path(selected))

        def choose_video(self):
            if self.running:
                return
            initial = EXE_DIR / "screenshots" / "video"
            selected = filedialog.askopenfilename(
                title="选择本地 MP4 视频", filetypes=[("MP4 视频", "*.mp4")],
                initialdir=str(initial if initial.is_dir() else EXE_DIR),
            )
            if selected:
                self.start_worker(Path(selected), video=True)

        def set_running(self, running):
            self.running = running
            state = "disabled" if running else "normal"
            for button in (self.run_button, self.video_button, self.config_button):
                button.configure(state=state)

        def start_worker(self, path, video=False):
            if self.running:
                return
            self.set_running(True)
            self.progress["value"] = 0
            self.status.configure(text="读取中…")
            self.append(f"\n开始{'视频 Mock 回放' if video else '截图识别'}：{path}")
            try:
                threading.Thread(
                    target=self.worker, args=(path, self.config_path, video),
                    daemon=True, name="OfflineAcceptanceWorker",
                ).start()
            except Exception as exc:
                # Thread creation can fail too. Reuse the normal UI failure path
                # so all three input buttons become available again.
                self.messages.put(("error", str(exc), traceback.format_exc()))

        def worker(self, root, config_path, video=False):
            try:
                if video:
                    def video_progress(done, total):
                        self.messages.put(("video_progress", done, total))
                    summary, paths = evaluate_video_file(root, config_path, video_progress)
                    self.messages.put(("video_done", summary, paths))
                    return
                def progress(done, total, record):
                    self.messages.put(("progress", done, total, record))
                records, summary = evaluate_screenshots(root, config_path, progress)
                csv_path, json_path = write_reports(root, records, summary)
                self.messages.put(("done", summary, csv_path, json_path))
            except Exception as exc:
                self.messages.put(("error", str(exc), traceback.format_exc()))

        def poll_messages(self):
            try:
                while True:
                    message = self.messages.get_nowait()
                    kind = message[0]
                    if kind == "video_progress":
                        _, done, total = message
                        self.progress["maximum"] = max(done, total, 1)
                        self.progress["value"] = done
                        self.status.configure(text=f"{done}/{total if total > 0 else '?'} 帧")
                    elif kind == "video_done":
                        _, summary, paths = message
                        self.progress["maximum"] = summary["frames"]
                        self.progress["value"] = summary["frames"]
                        self.append(
                            f"视频完成：{summary['frames']} 帧，有效 {summary['valid_frames']} 帧，"
                            f"{summary['fps']:.2f} FPS，分辨率 {summary['resolution']}"
                        )
                        self.append(f"Mock 事件：{summary['action_counts'] or '无'}")
                        self.append(
                            f"Mock 非零输出帧：{summary['nonzero_frames']}，"
                            f"结束强度：{summary['final_strength']}，"
                            f"时间戳回退帧：{summary['timestamp_fallback_frames']}"
                        )
                        self.append("事件统计不等于准确率；判断误报/漏报需要结合视频画面与人工标注。")
                        for label, path in zip(("CSV", "JSON", "Mock 日志"), paths):
                            self.append(f"{label}：{path}")
                        self.status.configure(text="完成")
                        self.set_running(False)
                        messagebox.showinfo("离线验收完成", "视频回放完成。CSV、JSON 和 Mock 事件日志已保存。")
                    elif kind == "progress":
                        _, done, total, record = message
                        self.progress["maximum"] = total
                        self.progress["value"] = done
                        self.status.configure(text=f"{done}/{total}")
                        expected = record["expected"] or "-"
                        hp = "-" if record["health_raw"] is None else f"{record['health_raw']:.1%}"
                        self.append(
                            f"[{done:04d}/{total:04d}] {record['file']} | "
                            f"expected={expected} state={record['state']} valid={record['valid']} hp={hp}"
                        )
                    elif kind == "done":
                        _, summary, csv_path, json_path = message
                        rate = summary["label_match_rate"]
                        rate_text = "无标签" if rate is None else f"{rate:.1%}"
                        self.append(
                            f"完成：总数={summary['total_images']} 有效={summary['valid_images']} "
                            f"unknown={summary['unknown_images']} 标签匹配={rate_text}"
                        )
                        self.append(f"CSV：{csv_path}")
                        self.append(f"JSON：{json_path}")
                        self.status.configure(text="完成")
                        self.set_running(False)
                        messagebox.showinfo("离线验收完成", "截图处理完成。详细结果已保存为 CSV 和 JSON。")
                    elif kind == "error":
                        _, error, detail = message
                        self.append(f"失败：{error}\n{detail}")
                        self.status.configure(text="失败")
                        self.set_running(False)
                        messagebox.showerror("离线验收失败", error)
            except queue.Empty:
                pass
            self.after(80, self.poll_messages)

    return AcceptanceWindow


def run_gui() -> None:
    window = _window_type()()
    window.mainloop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-check", action="store_true",
                      help="Verify the bundled vision and Mock core without opening a window")
    mode.add_argument("--video", type=Path, help="Replay one local video without opening a window")
    parser.add_argument("--config", type=Path, help="Configuration for --video or --self-check")
    parser.add_argument("--output-dir", type=Path, help="Report directory for --video")
    args = parser.parse_args(argv)
    if args.output_dir is not None and args.video is None:
        parser.error("--output-dir requires --video")
    if args.config is not None and not (args.self_check or args.video):
        parser.error("--config requires --video or --self-check; use the configuration button in the GUI")
    if args.self_check or args.video:
        try:
            config_path = args.config or default_config_path()
            if args.self_check:
                # Exercise the bundled modules that MP4 replay needs, with no
                # shared log file and no hardware import/connection.
                from tools.video_acceptance import ReplayLog
                from src.trigger import TriggerDispatch
                config = load_config(config_path)
                config.setdefault("trigger", {})["sender_mode"] = "mock"
                VisionPipeline(config)
                trigger = TriggerDispatch(config, log_writer=ReplayLog())
                trigger.safe_stop()
                result = {"ok": True, "offline_only": True, "sender_mode": "mock",
                          "final_strength": trigger.mock_output()["strength"],
                          "config": str(config_path)}
            else:
                summary, paths = evaluate_video_file(
                    args.video, config_path, output_dir=args.output_dir,
                )
                result = {"ok": True, "summary": summary,
                          "reports": [str(path) for path in paths]}
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
