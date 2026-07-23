"""control_panel.py — 迷你控制面板窗口渲染。

独立于 overlay 模块，始终显示（不依赖 --preview 参数）。
纯英文（OpenCV 默认字体不兼容中文）。
"""
from typing import Optional
import numpy as np
import cv2

PANEL_W, PANEL_H = 260, 150
PANEL_NAME = "Control Panel"

WHITE  = (255, 255, 255)
GRAY   = (160, 160, 160)
GREEN  = (0, 255, 0)
RED    = (0, 0, 255)
YELLOW = (0, 255, 255)


def draw_control_panel(
    health_stable: float = 0.0,
    state: str = "",
    event: str = "none",
    cur_fps: float = 0.0,
    paused: bool = True,
    paused_fps: Optional[float] = None,
) -> np.ndarray:
    """渲染控制面板小窗。返回 (PANEL_H, PANEL_W, 3) BGR 图像。

    Args:
        paused_fps: 暂停前的最后 FPS 值（暂停时冻结显示）；None 表示实时 fps
    """
    panel = np.zeros((PANEL_H, PANEL_W, 3), dtype=np.uint8)
    y = 18

    # 标题
    cv2.putText(panel, "HELLDIVER_DGLAB v0.1", (6, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, WHITE, 1)
    y += 18

    # 运行状态
    if paused:
        cv2.putText(panel, "[PAUSED] Ship mode", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, GRAY, 1)
        y += 22
        cv2.putText(panel, "Press [P] to start", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, GRAY, 1)
        y += 22
    else:
        cv2.putText(panel, "[RUNNING]", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, GREEN, 1)
        y += 22

        # 血量条
        hp = max(0.0, min(1.0, health_stable))
        cv2.putText(panel, "HP:", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        bar_w = 100
        bar_x, bar_y = 30, y - 9
        cv2.rectangle(panel, (bar_x, bar_y), (bar_x + bar_w, bar_y + 12), GRAY, 1)
        fill_w = int(bar_w * hp)
        if fill_w > 0:
            fill_color = GREEN if hp > 0.5 else YELLOW if hp > 0.25 else RED
            cv2.rectangle(panel, (bar_x, bar_y), (bar_x + fill_w, bar_y + 12), fill_color, -1)
        cv2.putText(panel, f"{hp:.0%}", (bar_x + bar_w + 6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        y += 22

        # 状态
        cv2.putText(panel, f"State: {state.upper()}", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        y += 16

    # FPS
    if paused and paused_fps is not None:
        cv2.putText(panel, f"FPS: {paused_fps:.1f} (paused)", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, GRAY, 1)
    else:
        cv2.putText(panel, f"FPS: {cur_fps:.1f}", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
    y += 22

    # 按键提示
    cv2.putText(panel, "[P]Pause  [ESC]Quit", (6, y + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.3, GRAY, 1)

    return panel


def set_panel_topmost(window_title: str = PANEL_NAME):
    """将 OpenCV 窗口设为始终置顶（Windows only）。

    非 Windows 平台或窗口尚未创建时静默降级。
    独占全屏游戏模式下由 DWM 控制，此设置可能不生效。
    """
    import ctypes
    import logging
    _log = logging.getLogger(__name__)
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, window_title)
        if hwnd:
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001,  # HWND_TOPMOST, SWP_NOMOVE|SWP_NOSIZE
            )
        else:
            _log.debug("set_panel_topmost: window not found: %s", window_title)
    except Exception:
        _log.debug("set_panel_topmost: not available on this platform")
