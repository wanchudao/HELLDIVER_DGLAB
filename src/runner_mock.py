"""
runner_mock.py — Phase 6 Mock 实时入口。

按期望状态分组循环播放 debug/all_samples/ 中的截图，
每帧跑完整视觉管线 (roi→health→effect→state_machine)，
输出状态和事件到控制台。SHOW_PREVIEW=True 时绘制 debug overlay。

用法: python src/runner_mock.py   (Ctrl+C 退出)
"""

import logging
import argparse
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

# ── 项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 可调参数 ──
IMAGE_DIR = PROJECT_ROOT / "debug" / "all_samples"
FRAME_INTERVAL = 0.5  # 帧间隔秒数
LOOP = False           # 默认离线单轮，--loop 时重复
SHOW_PREVIEW = False  # --preview 时显示 debug overlay 窗口
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("runner_mock")


def _group_key(filename: str) -> str:
    """从文件名提取分组键。例: death_01.jpg ->death, healing_g1_02.jpg ->healing_g1"""
    # 去掉扩展名后去掉末尾的 _NN 数字后缀
    stem = Path(filename).stem
    # healing_g1_01 ->healing_g1, death_01 ->death
    return re.sub(r"_\d+$", "", stem)


def _collect_images(image_dir: Path) -> list[tuple[str, list[Path]]]:
    """收集图片并按期望状态分组。返回 [(group_name, [paths]), ...]"""
    paths_by_group: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(image_dir.iterdir()):
        if path.suffix.lower() in IMAGE_SUFFIXES:
            group = _group_key(path.name)
            paths_by_group[group].append(path)

    # 按组名排序保证 deterministic
    return sorted(paths_by_group.items())


def main(argv=None):
    from src.config_loader import load_config
    from src.trigger import TriggerDispatch
    from src.vision_pipeline import VisionPipeline

    parser = argparse.ArgumentParser(description="Offline replay; always mock, never hardware")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--image-dir", type=Path, default=IMAGE_DIR)
    parser.add_argument("--preview", action="store_true", default=SHOW_PREVIEW)
    parser.add_argument("--loop", action="store_true", default=LOOP)
    parser.add_argument("--interval", type=float, default=FRAME_INTERVAL)
    args = parser.parse_args(argv)
    if args.interval < 0:
        parser.error("--interval must be non-negative")
    config = load_config(args.config)
    config.setdefault("trigger", {})["sender_mode"] = "mock"

    pipeline = VisionPipeline(config)
    groups = _collect_images(args.image_dir)
    if not groups:
        print(f"[runner_mock] no images found in {args.image_dir}")
        return
    trigger = None
    try:
        trigger = TriggerDispatch(config)
    except (ConnectionError, RuntimeError) as e:
        print(f"[runner_mock] 设备连接失败: {e}")
        print("[runner_mock] 将继续运行（仅终端输出）。")
        trigger = None

    total_images = sum(len(paths) for _, paths in groups)
    print(f"[runner_mock] {len(groups)} groups, {total_images} images from {args.image_dir}")
    print(f"[runner_mock] FRAME_INTERVAL={args.interval}s  SHOW_PREVIEW={args.preview}")
    print(f"[runner_mock] Ctrl+C to stop\n")

    # ── 统计 ──
    transition_log: list[str] = []  # 状态转换记录

    try:
        outer_coords = None
        inner_coords = None

        while True:  # 循环播放 (Ctrl+C 退出)
            for group_name, paths in groups:
                # ── 每组之间重置状态机 + EMA ──
                pipeline.reset()
                if trigger is not None:
                    trigger.reset("replay_group")
                prev_raw_state = None  # 追踪组内状态转换

                for i, path in enumerate(paths):
                    t0 = time.monotonic()

                    frame = cv2.imread(str(path))
                    if frame is None:
                        logger.warning("cannot read: %s", path.name)
                        continue

                    # ── 完整管线 ──
                    try:
                        result, roi_result, health_result = pipeline.process(frame)
                    except Exception:
                        logger.exception("pipeline error: %s", path.name)
                        pipeline.reset()
                        if trigger is not None:
                            trigger.reset("replay_error")
                        continue

                    # ── 输出 ──
                    label = f"[{group_name} {i+1}/{len(paths)}]"
                    print(
                        f"{label:22s} {path.name:30s}  "
                        f"state={result.state:12s}  hp={result.health_raw:5.1%}  "
                        f"stable={result.health_stable:5.1%}  "
                        f"red={result.red_score:.2f}  cyan={result.cyan_score:.2f}  "
                        f"death={result.death_score:.2f}  conf={result.confidence:.2f}  "
                        f"event={'-':14s}"
                    )

                    # ── 触发设备 ──
                    if trigger is not None:
                        trigger.dispatch(result)

                    # ── 状态转换记录 ──
                    raw_state = result.debug.get("raw_state", "?")
                    if prev_raw_state is not None and raw_state != prev_raw_state:
                        transition_log.append(
                            f"{label:22s} {prev_raw_state} ->{raw_state}  "
                            f"(hp={result.health_stable:.1%})"
                        )
                    prev_raw_state = raw_state

                    # ── 调试覆盖 ──
                    if args.preview:
                        from src.debug_overlay import draw_overlay

                        outer_coords = roi_result.outer_coords
                        inner_coords = roi_result.inner_coords
                        overlay = draw_overlay(
                            frame,
                            result,
                            outer_coords=outer_coords,
                            inner_coords=inner_coords,
                            fill_boundary_x=health_result.fill_boundary_x,
                        )
                        cv2.imshow("runner_mock (q=quit)", overlay)
                        key = cv2.waitKey(1) & 0xFF
                        if key == ord("q") or key == 27:  # q or Esc
                            raise KeyboardInterrupt

                    # ── 帧间隔 (扣除处理时间) ──
                    elapsed = time.monotonic() - t0
                    time.sleep(max(0, args.interval - elapsed))

            # 一轮播完
            if not args.loop:
                break
            print(f"\n[runner_mock] === loop complete, restarting (Ctrl+C to stop) ===\n")

    except KeyboardInterrupt:
        pass
    finally:
        if trigger is not None:
            trigger.safe_stop()
        if args.preview:
            cv2.destroyAllWindows()

        # ── 退出摘要 ──
        print(f"\n{'='*60}")
        print("State transitions:")
        for line in transition_log:
            print(f"  {line}")
        if not transition_log:
            print("  (none)")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
