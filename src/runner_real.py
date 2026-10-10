"""runner_real.py — V0.3 实时入口。

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
from src.trigger import TriggerDispatch
from src.screen_capture import BufferedScreenCapture as ScreenCapture
from src.debug_overlay import draw_overlay
from src.control_panel import (
    PANEL_NAME,
    PanelController,
    draw_control_panel,
    install_panel_controls,
    panel_window_closed,
    set_panel_topmost,
    update_panel_layout,
    release_panel_controls,
)
from src.vision_pipeline import VisionPipeline
from src.state_machine import DetectionResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FPS_WINDOW_SEC = 2.0  # FPS 滑动窗口大小
ARM_HOTKEY_VK = 0x77  # F8; intentionally uncommon in normal gameplay/chat.
TOPMOST_REFRESH_SEC = 0.5

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("runner_real")

# ── 全局按键检测（不依赖 OpenCV 窗口焦点）──
_user32 = ctypes.windll.user32

def _key_down(vk_code: int) -> bool:
    """检测按键是否当前被按住（全局，不需要窗口焦点）。"""
    return bool(_user32.GetAsyncKeyState(vk_code) & 0x8000)


def _hardware_calibration_error(config: dict, monitor_size: tuple[int, int]) -> str:
    """硬件入口必须在配对前核对校准；不能仅依赖 start.bat 的检查。"""
    calibration = config.get("calibration", {})
    if calibration.get("calibrated", 0) != 1:
        return "硬件输出需要先在地面任务完成血条校准，请运行 tools/calibrate.py"
    expected = str(calibration.get("resolution", "")).strip().lower().replace(" ", "")
    actual = f"{monitor_size[0]}x{monitor_size[1]}"
    if expected != actual:
        return f"校准分辨率 {expected or 'unknown'} 与当前抓屏 {actual} 不同，请重新校准"
    return ""


def main(argv=None, *, stop_event=None, status_sink=None, panel_theme="legacy"):
    parser = argparse.ArgumentParser(description="HELLDIVER_DGLAB 实时检测")
    parser.add_argument("--preview", action="store_true",
                        help="显示 debug overlay 窗口")
    parser.add_argument("--no-global-escape", action="store_true",
                        help="让 ESC 留给游戏菜单；控制窗关闭仍可退出")
    parser.add_argument("--require-output", action="store_true",
                        help="连接失败时退出，不自动降级为仅监测")
    parser.add_argument("--enable-trigger", action="store_true",
                        help="启用设备触发（默认仅终端输出）")
    parser.add_argument("--fps", type=int, default=30,
                        help="目标帧率 (0=不限, 默认30)")
    parser.add_argument("--config", type=Path, default=None, help="配置文件路径")
    parser.add_argument("--sender-mode", choices=("mock", "dglab"), default=None,
                        help="覆盖发送模式；start.bat 默认强制 mock")
    args = parser.parse_args(argv)
    if stop_event is not None and stop_event.is_set():
        return
    if args.fps < 0:
        parser.error("--fps must be non-negative")

    config = load_config(args.config)
    if args.sender_mode:
        config.setdefault("trigger", {})["sender_mode"] = args.sender_mode

    # ── 初始化视觉模块 ──
    pipeline = VisionPipeline(config)

    # ── 屏幕捕获 ──
    monitor_idx = int(config.get("capture", {}).get("monitor_index", 1))
    cap = ScreenCapture(monitor_index=monitor_idx, target_fps=args.fps,
                        max_frame_age=pipeline.max_frame_gap)
    if not cap.is_ready:
        print("[runner_real] 屏幕捕获不可用，退出。")
        cap.close()
        return

    w, h = cap.monitor_size
    hardware_enabled = (args.enable_trigger
                        and config.get("trigger", {}).get("sender_mode") == "dglab")
    if config.get("hud") and (w, h) != (1920, 1080):
        cap.close()
        parser.error("HUD 档位模式仅支持完整的 1920×1080 抓屏，请停止后核对显示器")
    if hardware_enabled:
        calibration_error = _hardware_calibration_error(config, (w, h))
        if calibration_error:
            cap.close()
            parser.error(calibration_error)

    # ── 启动 banner ──
    calibration = config.get("calibration", {})
    is_calibrated = calibration.get("calibrated", 0) == 1
    calib_res = calibration.get("resolution", "unknown")
    roi = config["ROI"]
    bar_w = roi["bar_max_x"] - roi["bar_min_x"]

    print("=" * 50)
    print("  HELLDIVER_DGLAB V0.3 — 实时检测")
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
    paused = True   # 启动时安全锁定（舰船上有经验条，非血条）
    hotkey_was_down = False
    stop_reason = "Startup safety lock"
    panel_controller = PanelController()
    panel_ready = False
    panel_controls_attempted = False
    last_topmost_refresh = -float("inf")

    print("[runner_real] 进入战场后按 F8 或点击控制窗按钮启用；再次操作立即停止。")
    if args.no_global_escape:
        print("[runner_real] 关闭控制窗或主窗口中点击停止全部退出；全局 ESC 留给游戏。")
    else:
        print("[runner_real] Ctrl+C、ESC 或关闭控制窗均安全退出。")
    print()

    try:
        # ── 初始化 trigger（放在 try 内：DG-LAB 连接失败时优雅降级）──
        if args.enable_trigger:
            try:
                trigger = TriggerDispatch(config)
            except (ConnectionError, RuntimeError) as e:
                if args.require_output:
                    if stop_event is not None and stop_event.is_set():
                        return
                    raise RuntimeError(f"APP 配对失败或超时，请点击连接重新扫码：{e}") from e
                print(f"[runner_real] 设备连接失败: {e}")
                print("[runner_real] 继续运行（仅终端输出），或 Ctrl+C 退出。")
                trigger = None

        # ── 主循环 ──
        last_fps = 0.0  # 暂停时冻结的 FPS 值

        try:
            while True:
                t0 = time.monotonic()
                # Check before imshow; otherwise HighGUI recreates a panel
                # closed between frames and masks the user's exit request.
                if panel_ready and panel_window_closed(PANEL_NAME):
                    raise KeyboardInterrupt
                panel_controller.consume_layout()
                # Poll before capture/processing, including failure paths.
                if ((stop_event is not None and stop_event.is_set())
                        or (not args.no_global_escape and _key_down(0x1B))):
                    raise KeyboardInterrupt
                hotkey_down = _key_down(ARM_HOTKEY_VK)
                # Always drain mouse intent, even when F8 also requests a change.
                # A leftover STOP click must not become ARM on the next frame.
                mouse_toggle_requested = panel_controller.consume_toggle()
                toggle_requested = (
                    (hotkey_down and not hotkey_was_down)
                    or mouse_toggle_requested
                )
                if toggle_requested:
                    paused = not paused
                    panel_controller.cancel_pending()
                    pipeline.reset()
                    if trigger is not None:
                        trigger.reset("paused" if paused else "resumed")
                    stop_reason = "Manual stop (F8/button)" if paused else ""
                    print(f"[runner_real] {'[SAFE LOCKED]' if paused else '[ARMED]'}")
                hotkey_was_down = hotkey_down

                frame = cap.grab()
                if ((hardware_enabled or config.get("hud")) and frame is not None
                        and frame.shape[:2] != (h, w)):
                    # 分辨率切换不能继续沿用旧坐标。finally 会先停止设备，
                    # 操作者需重新校准、重启，而不是按 F8 绕过这项检查。
                    raise RuntimeError("抓屏分辨率已变化，输出已停止；请核对 HUD 与屏幕后重启")
                if frame is None:
                    if not paused:
                        paused = True
                        panel_controller.cancel_pending()
                        stop_reason = "Screen capture failed"
                        pipeline.reset()
                        if trigger is not None:
                            trigger.reset("capture_failed")
                        logger.error("截图失败，已安全锁定并停止输出；恢复后按 F8 重新启用")

                frame_count += int(frame is not None)
                result = DetectionResult()

                # ── 管线（仅在运行时执行）──
                if not paused:
                    try:
                        result, roi_result, health_result = pipeline.process(frame)
                    except Exception:
                        logger.exception("管线异常")
                        paused = True
                        panel_controller.cancel_pending()
                        stop_reason = "Vision pipeline failed"
                        pipeline.reset()
                        if trigger is not None:
                            trigger.reset("pipeline_failed")
                        continue

                    # 设备触发
                    if trigger is not None:
                        outcome = trigger.dispatch(result)
                        event_count += int(bool(outcome and outcome.get("dispatched")))

                    # FPS
                    now = time.monotonic()
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

                if status_sink is not None:
                    try:
                        mode = "dglab" if hardware_enabled else "mock" if trigger is not None else "monitor"
                        if hardware_enabled:
                            from src.dglab_sender import ui_output_snapshot
                            snapshot = ui_output_snapshot()
                        elif trigger is not None:
                            outputs = trigger.mock_output()["channels"]
                            snapshot = {"connected": False, "strengths": {
                                ch: outputs.get(ch, {}).get("strength", 0) for ch in ("A", "B")}}
                        else:
                            snapshot = {"connected": False, "strengths": {"A": 0, "B": 0}}
                        status_sink(paused=paused, valid=bool(result.valid and not paused),
                                    hp=result.health_stable, mode=mode, **snapshot)
                    except Exception:
                        logger.debug("UI status unavailable", exc_info=True)

                # ── 控制面板（始终渲染）──
                if not paused:
                    panel = draw_control_panel(
                        health_stable=result.health_stable,
                        state=result.state,
                        event=result.event,
                        cur_fps=cur_fps,
                        paused=False,
                        compact=panel_controller.compact,
                        valid=result.valid,
                        theme=panel_theme,
                        output_mode=("HARDWARE" if hardware_enabled else
                                     "MOCK" if trigger is not None else "MONITOR"),
                    )
                else:
                    panel = draw_control_panel(
                        paused=True,
                        compact=panel_controller.compact,
                        paused_fps=last_fps,
                        output_mode=("HARDWARE" if hardware_enabled else
                                     "MOCK" if trigger is not None else "MONITOR"),
                        stop_reason=stop_reason,
                        theme=panel_theme,
                    )
                cv2.imshow(PANEL_NAME, panel)

                # ── 控制窗（点击按钮）+ 全局键盘（不依赖窗口焦点）──
                if not panel_controls_attempted:
                    panel_ready = install_panel_controls(panel_controller, PANEL_NAME)
                    panel_controls_attempted = True
                elif panel_window_closed(PANEL_NAME):
                    raise KeyboardInterrupt
                update_panel_layout(panel_controller, PANEL_NAME)
                cv2.waitKey(1)
                topmost_now = time.monotonic()
                if topmost_now - last_topmost_refresh >= TOPMOST_REFRESH_SEC:
                    set_panel_topmost(PANEL_NAME, interactive=True)
                    last_topmost_refresh = topmost_now
                # ── 帧率限制 ──
                if args.fps > 0:
                    elapsed = time.monotonic() - t0
                    time.sleep(max(0, 1.0 / args.fps - elapsed))

        except KeyboardInterrupt:
            pass
    finally:
        # Output shutdown must precede potentially failing GUI/capture cleanup.
        try:
            if trigger is not None:
                trigger.safe_stop()
        finally:
            try:
                cap.close()
            finally:
                try:
                    cv2.destroyAllWindows()
                finally:
                    release_panel_controls(PANEL_NAME)

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
