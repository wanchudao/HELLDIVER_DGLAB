"""
runner_mock.py — Phase 6 Mock 实时入口。

按期望状态分组循环播放 debug/all_samples/ 中的截图，
每帧跑完整视觉管线 (roi→health→effect→state_machine)，
输出状态和事件到控制台。SHOW_PREVIEW=True 时绘制 debug overlay。

用法: python src/runner_mock.py   (Ctrl+C 退出)
"""

import logging
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
LOOP = True            # 播完是否循环 (False = 只播一轮)
SHOW_PREVIEW = True   # 显示 debug overlay 窗口
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


def main():
    from src.config_loader import load_config
    from src.roi_locator import RoiLocator
    from src.health_detector import HealthDetector
    from src.effect_detector import EffectDetector
    from src.state_machine import StateMachine
    from src.trigger import TriggerDispatch

    config = load_config(str(PROJECT_ROOT / "config.ini"))

    roi_locator = RoiLocator(config)
    health_detector = HealthDetector(config)
    effect_detector = EffectDetector(config)
    state_machine = StateMachine(config)
    trigger = None
    try:
        trigger = TriggerDispatch(config)
    except (ConnectionError, RuntimeError) as e:
        print(f"[runner_mock] 设备连接失败: {e}")
        print("[runner_mock] 将继续运行（仅终端输出）。")
        trigger = None

    groups = _collect_images(IMAGE_DIR)
    if not groups:
        print(f"[runner_mock] no images found in {IMAGE_DIR}")
        return

    total_images = sum(len(paths) for _, paths in groups)
    print(f"[runner_mock] {len(groups)} groups, {total_images} images from {IMAGE_DIR}")
    print(f"[runner_mock] FRAME_INTERVAL={FRAME_INTERVAL}s  SHOW_PREVIEW={SHOW_PREVIEW}")
    print(f"[runner_mock] Ctrl+C to stop\n")

    # ── 统计 ──
    transition_log: list[str] = []  # 状态转换记录

    try:
        outer_coords = None
        inner_coords = None

        while True:  # 循环播放 (Ctrl+C 退出)
            for group_name, paths in groups:
                # ── 每组之间重置状态机 + EMA ──
                state_machine.reset()
                health_detector.reset_ema()
                prev_raw_state = None  # 追踪组内状态转换

                for i, path in enumerate(paths):
                    t0 = time.time()

                    frame = cv2.imread(str(path))
                    if frame is None:
                        logger.warning("cannot read: %s", path.name)
                        continue

                    # ── 完整管线 ──
                    try:
                        roi_result = roi_locator.locate(frame)
                        health_result = health_detector.detect(roi_result.inner_img)
                        effect_result = effect_detector.detect(
                            frame, health_raw=health_result.health_raw
                        )
                        result = state_machine.update(
                            health_raw=health_result.health_raw,
                            health_stable=health_result.health_stable,
                            red_score=effect_result.red_score,
                            cyan_score=effect_result.cyan_score,
                            death_score=effect_result.death_score,
                            anchor_score=roi_result.anchor_score,
                            confidence=health_result.confidence,
                            valid=roi_result.valid,
                        )
                    except Exception:
                        logger.exception("pipeline error: %s", path.name)
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
                    if trigger is not None and result.event != "none":
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
                    if SHOW_PREVIEW:
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
                    elapsed = time.time() - t0
                    time.sleep(max(0, FRAME_INTERVAL - elapsed))

            # 一轮播完
            if not LOOP:
                break
            print(f"\n[runner_mock] === loop complete, restarting (Ctrl+C to stop) ===\n")

    except KeyboardInterrupt:
        pass
    finally:
        if trigger is not None:
            trigger.safe_stop()
        if SHOW_PREVIEW:
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
