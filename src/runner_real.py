"""runner_real.py — V0.1 实时入口。

mss 屏幕捕获 → 完整视觉管线 → 终端输出 + debug overlay（可选）→ 设备触发（可选）。

用法:
    python src/runner_real.py              # 终端模式（默认，安全）
    python src/runner_real.py --preview    # 终端 + debug overlay
    python src/runner_real.py --enable-trigger  # 终端 + 设备触发

Ctrl+C 安全退出（强度归零 + 清除波形 + 关连接）。

安全设计:
    - 默认不发设备（先 mock 后 real）
    - overlay 默认关闭（避免实时游戏抢焦点）
    - trigger 初始化失败不崩溃（优雅降级）
"""
import argparse
import ctypes
import logging
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from src.config_loader import load_config
from src.roi_locator import RoiLocator
from src.health_detector import HealthDetector
from src.effect_detector import EffectDetector
from src.state_machine import StateMachine
from src.trigger import TriggerDispatch
from src.screen_capture import ScreenCapture
from src.debug_overlay import draw_overlay
from src.control_panel import draw_control_panel, PANEL_NAME, set_panel_topmost

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FPS_WINDOW_SEC = 2.0  # FPS 滑动窗口大小

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("runner_real")


def main():
    parser = argparse.ArgumentParser(description="HELLDIVER_DGLAB 实时检测")
    parser.add_argument("--preview", action="store_true",
                        help="显示 debug overlay 窗口")
    parser.add_argument("--enable-trigger", action="store_true",
                        help="启用设备触发（默认仅终端输出）")
    parser.add_argument("--fps", type=int, default=30,
                        help="目标帧率 (0=不限, 默认30)")
    args = parser.parse_args()

    config = load_config(str(PROJECT_ROOT / "config.ini"))

    # ── 初始化视觉模块 ──
    roi_locator = RoiLocator(config)
    health_detector = HealthDetector(config)
    effect_detector = EffectDetector(config)
    state_machine = StateMachine(config)

    # ── 屏幕捕获 ──
    monitor_idx = int(config.get("capture", {}).get("monitor_index", 1))
    cap = ScreenCapture(monitor_index=monitor_idx)
    if not cap.is_ready:
        print("[runner_real] 屏幕捕获不可用，退出。")
        return

    w, h = cap.monitor_size

    # ── 启动 banner ──
    calibration = config.get("calibration", {})
    is_calibrated = calibration.get("calibrated", 0) == 1
    calib_res = calibration.get("resolution", "unknown")
    roi = config["ROI"]
    bar_w = roi["bar_max_x"] - roi["bar_min_x"]

    print("=" * 50)
    print("  HELLDIVER_DGLAB V0.1 — 实时检测")
    print("=" * 50)
    print(f"  分辨率: {w}x{h}")
    print(f"  校准: {'已校准 (' + calib_res + ')' if is_calibrated else '1080p 默认坐标'}")
    print(f"  血条有效宽度: {bar_w}px")
    print(f"  preview: {'ON' if args.preview else 'OFF'}")
    print(f"  trigger: {'ON' if args.enable_trigger else 'OFF (仅终端)'}")
    if w != 1920 or h != 1080:
        if not is_calibrated:
            print(f"  [警告] 非 1080p 且未校准，请运行: python tools/calibrate.py")
            print(f"         将继续运行，但坐标可能不准。")
        else:
            print(f"  使用校准坐标 ({calib_res})。")
    print()

    # ── FPS 状态（内联，不独立文件）──
    fps_times: deque[float] = deque()
    last_print = 0.0
    frame_count = 0
    event_count = 0

    outer_coords = None
    inner_coords = None
    trigger = None  # 延迟初始化（在 try 内，防止 ConnectionError unhandled）
    paused = True   # 启动时暂停（舰船上有经验条，非血条）

    print("[runner_real] 按 P 开始检测（舰船上暂停，任务中开启）... Ctrl+C 或 Q 退出")
    print()

    try:
        # ── 初始化 trigger（放在 try 内：DG-LAB 连接失败时优雅降级）──
        if args.enable_trigger:
            try:
                trigger = TriggerDispatch(config)
            except (ConnectionError, RuntimeError) as e:
                print(f"[runner_real] 设备连接失败: {e}")
                print("[runner_real] 继续运行（仅终端输出），或 Ctrl+C 退出。")
                trigger = None

        # ── 主循环 ──
        last_fps = 0.0  # 暂停时冻结的 FPS 值

        try:
            while True:
                t0 = time.time()

                frame = cap.grab()
                if frame is None:
                    time.sleep(0.01)
                    continue

                frame_count += 1

                # ── 管线（仅在运行时执行）──
                if not paused:
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
                        logger.exception("管线异常")
                        continue

                    # 设备触发
                    if trigger is not None:
                        trigger.dispatch(result)
                        event_count += 1

                    # FPS
                    now = time.time()
                    fps_times.append(now)
                    cutoff = now - FPS_WINDOW_SEC
                    while fps_times and fps_times[0] < cutoff:
                        fps_times.popleft()

                    # 终端输出（每秒一次）
                    if now - last_print >= 1.0:
                        last_print = now
                        cur_fps = (
                            len(fps_times) / (fps_times[-1] - fps_times[0])
                            if len(fps_times) >= 2 else 0.0
                        )
                        print(
                            f"[{cur_fps:5.1f} fps] "
                            f"state={result.state:12s}  "
                            f"hp={result.health_stable:.1%}  "
                            f"red={result.red_score:.2f}  "
                            f"cyan={result.cyan_score:.2f}  "
                            f"event={'-':14s}  "
                            f"conf={result.confidence:.2f}  "
                            f"frm={frame_count}"
                        )
                    # Debug overlay
                    if args.preview:
                        overlay = draw_overlay(
                            frame, result,
                            outer_coords=roi_result.outer_coords,
                            inner_coords=roi_result.inner_coords,
                            fill_boundary_x=health_result.fill_boundary_x,
                        )
                        cv2.imshow("HELLDIVER_DGLAB", overlay)

                # ── FPS 计算（暂停时复用上一帧值）──
                cur_fps = (
                    len(fps_times) / (fps_times[-1] - fps_times[0])
                    if len(fps_times) >= 2 else 0.0
                )
                if not paused:
                    last_fps = cur_fps

                # ── 控制面板（始终渲染）──
                if not paused:
                    panel = draw_control_panel(
                        health_stable=result.health_stable,
                        state=result.state,
                        event=result.event,
                        cur_fps=cur_fps,
                        paused=False,
                    )
                else:
                    panel = draw_control_panel(
                        paused=True,
                        paused_fps=last_fps,
                    )
                cv2.imshow(PANEL_NAME, panel)

                # ── 键盘（始终响应）──
                key = cv2.waitKey(1) & 0xFF
                if key == ord("p") or key == ord("P"):
                    paused = not paused
                    print(f"[runner_real] {'[PAUSED]' if paused else '[RUNNING]'}")
                    if not paused:
                        state_machine.reset()
                        health_detector.reset_ema()
                        set_panel_topmost(PANEL_NAME)
                elif key == 27:
                    raise KeyboardInterrupt

                # ── 帧率限制 ──
                if args.fps > 0:
                    elapsed = time.time() - t0
                    time.sleep(max(0, 1.0 / args.fps - elapsed))

        except KeyboardInterrupt:
            pass
    finally:
        cap.close()
        cv2.destroyAllWindows()
        if trigger is not None:
            trigger.safe_stop()

        # ── 退出摘要 ──
        cur_fps = (
            len(fps_times) / (fps_times[-1] - fps_times[0])
            if len(fps_times) >= 2 else 0.0
        )
        print(f"\n{'='*50}")
        print(f"  总帧数: {frame_count}")
        print(f"  触发事件: {event_count}")
        print(f"  平均 FPS: {cur_fps:.1f}")
        print(f"{'='*50}")


if __name__ == "__main__":
    main()
