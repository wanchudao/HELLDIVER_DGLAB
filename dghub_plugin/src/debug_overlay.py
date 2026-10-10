"""
debug_overlay.py
调试可视化模块。

在帧上绘制 ROI 框、血量边界、状态文字、置信度。
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 颜色常量 (BGR)
COLOR_ROI = (0, 255, 0)  # 绿色 ROI
COLOR_INNER = (255, 255, 0)  # 青色 inner
COLOR_BOUNDARY = (0, 0, 255)  # 红色边界
COLOR_TEXT = (255, 255, 255)  # 白色文字
COLOR_BG = (0, 0, 0)  # 黑色背景
COLOR_UNKNOWN = (0, 165, 255)  # 橙色 unknown
COLOR_FULL = (0, 255, 0)  # 绿色满血
COLOR_INJURED = (0, 0, 255)  # 红色受伤
COLOR_DEATH = (0, 0, 139)  # 暗红死亡
COLOR_HEALING = (255, 255, 0)  # 青色治疗


def _state_color(state: str) -> tuple:
    """根据状态返回对应颜色。"""
    colors = {
        "unknown": COLOR_UNKNOWN,
        "full_health": COLOR_FULL,
        "injured": (0, 0, 255),
        "death": COLOR_DEATH,
        "healing": COLOR_HEALING,
    }
    return colors.get(state, COLOR_UNKNOWN)


def draw_overlay(
    frame: np.ndarray,
    result,  # DetectionResult
    outer_coords: tuple = None,
    inner_coords: tuple = None,
    fill_boundary_x: int = -1,
) -> np.ndarray:
    """在帧上绘制调试 overlay。

    Args:
        frame: BGR 画面
        result: DetectionResult
        outer_coords: 外层 ROI 坐标 (x1,y1,x2,y2)
        inner_coords: 内层 ROI 坐标 (x1,y1,x2,y2)
        fill_boundary_x: 血量边界 x 坐标

    Returns:
        绘制后的帧
    """
    overlay = frame.copy()
    h, w = overlay.shape[:2]

    # 绘制 ROI 框
    if outer_coords:
        x1, y1, x2, y2 = outer_coords
        cv2.rectangle(overlay, (x1, y1), (x2, y2), COLOR_ROI, 1)
        cv2.putText(
            overlay, "ROI", (x1, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.3, COLOR_ROI, 1,
        )

    if inner_coords:
        x1, y1, x2, y2 = inner_coords
        cv2.rectangle(overlay, (x1, y1), (x2, y2), COLOR_INNER, 1)

    # 绘制血量边界线
    if fill_boundary_x >= 0 and result.valid:
        cv2.line(
            overlay,
            (fill_boundary_x, 0),
            (fill_boundary_x, h),
            COLOR_BOUNDARY, 1,
        )

    # 状态文字背景
    state = result.state
    conf = result.confidence
    event = result.event

    # 顶部信息面板
    panel_y = 25
    cv2.rectangle(overlay, (5, 5), (300, 100), COLOR_BG, -1)
    cv2.rectangle(overlay, (5, 5), (300, 100), _state_color(state), 1)

    # 状态行
    cv2.putText(
        overlay, f"State: {state.upper()}",
        (10, panel_y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, _state_color(state), 1,
    )
    panel_y += 15

    # 置信度
    cv2.putText(
        overlay, f"Conf: {conf:.2f}  Anchor: {result.anchor_score:.2f}",
        (10, panel_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, COLOR_TEXT, 1,
    )
    panel_y += 15

    # 血量
    cv2.putText(
        overlay,
        f"HP: raw={result.health_raw:.2f}  stable={result.health_stable:.2f}",
        (10, panel_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, COLOR_TEXT, 1,
    )
    panel_y += 15

    # 滤镜分数
    cv2.putText(
        overlay,
        f"Red: {result.red_score:.2f}  Cyan: {result.cyan_score:.2f}  Death: {result.death_score:.2f}",
        (10, panel_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, COLOR_TEXT, 1,
    )
    panel_y += 15

    # 事件
    if event != "none":
        cv2.putText(
            overlay, f"Event: {event.upper()}",
            (10, panel_y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 165, 255), 1,
        )

    return overlay
