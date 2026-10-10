"""
roi_locator.py
ROI 定位与 HUD 锚点校验模块。

负责：
1. 从 config 加载 ROI 参数
2. 外层 ROI 裁剪
3. inner ROI 裁剪
4. HUD 锚点校验（A1 端帽特征检测）
"""

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RoiResult:
    """ROI 校验结果"""
    valid: bool
    anchor_score: float  # 锚点匹配度 0-1
    outer_img: np.ndarray  # 外层 ROI 裁剪结果 (BGR)
    inner_img: np.ndarray  # 内层 ROI 裁剪结果 (BGR)
    outer_coords: tuple  # (x1, y1, x2, y2)
    inner_coords: tuple  # (x1, y1, x2, y2)
    reject_reason: str = ""
    cap_positions: tuple = ()


class RoiLocator:
    """ROI 定位器：校验 HUD 是否存在并裁剪 ROI。"""

    def __init__(self, config: dict):
        roi = config["ROI"]
        self.outer_x1 = roi["outer_x1"]
        self.outer_y1 = roi["outer_y1"]
        self.outer_x2 = roi["outer_x2"]
        self.outer_y2 = roi["outer_y2"]
        self.inner_x1 = roi["inner_x1"]
        self.inner_y1 = roi["inner_y1"]
        self.inner_x2 = roi["inner_x2"]
        self.inner_y2 = roi["inner_y2"]
        self.cap_ignore_left = roi["cap_ignore_left"]
        self.cap_ignore_right = roi["cap_ignore_right"]
        self.bar_min_x = roi["bar_min_x"]
        self.bar_max_x = roi["bar_max_x"]
        self.hud = config.get("hud")
        if self.hud is not None:
            from src.hud_profiles import validate_hud_section
            validate_hud_section(self.hud, roi)

        # 锚点阈值
        min_anchor = float(config.get("unknown", {}).get("min_anchor_score", 0.5))
        self.min_anchor_score = min_anchor
        self.min_structure_contrast = float(config.get("unknown", {}).get("min_structure_contrast", 8.0))

        if not math.isfinite(min_anchor) or not 0 <= min_anchor <= 1:
            raise ValueError("min_anchor_score must be finite and within [0, 1]")
        if (not math.isfinite(self.min_structure_contrast)
                or not 0 < self.min_structure_contrast <= 255):
            raise ValueError("min_structure_contrast must be finite and within (0, 255]")

        if not (0 <= self.outer_x1 <= self.inner_x1 < self.inner_x2 <= self.outer_x2
                and 0 <= self.outer_y1 <= self.inner_y1 < self.inner_y2 <= self.outer_y2):
            raise ValueError("ROI must contain a positive-size inner bar within the outer rectangle")

        logger.info(
            "roi_locator ready  outer=(%d,%d)-(%d,%d)  inner=(%d,%d)-(%d,%d)",
            self.outer_x1, self.outer_y1, self.outer_x2, self.outer_y2,
            self.inner_x1, self.inner_y1, self.inner_x2, self.inner_y2,
        )

    def _crop(self, frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
        """安全裁剪：自动裁剪到画面边界内。"""
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(x1 + 1, min(x2, w))
        y2 = max(y1 + 1, min(y2, h))
        return frame[y1:y2, x1:x2]

    def _cap_contrast(self, frame: np.ndarray, start: int, stop: int) -> float:
        """两条采样带内同一竖边都必须高于左右邻列，填充本身不作锚点。

        HD2 的端帽伸出主条上下边界；飞船经验条的水平亮线没有该结构。
        按 B/G/R 分别比较再取最强通道，使暗红死亡端帽也保留结构证据。
        """
        height, width = frame.shape[:2]
        bar_height = self.inner_y2 - self.inner_y1
        pad = max(1, round(bar_height * .5))
        offset = max(3, round(bar_height * .6))
        start, stop = max(offset, start), min(width - offset, stop)
        if (start >= stop or self.inner_y1 < pad
                or self.inner_y2 + pad > height):
            return 0.0

        bands = []
        for top, bottom in ((self.inner_y1 - pad, self.inner_y1),
                            (self.inner_y2, self.inner_y2 + pad)):
            patch = frame[top:bottom, start - offset:stop + offset].astype(np.float32)
            center = patch[:, offset:-offset]
            left = patch[:, :-2 * offset]
            right = patch[:, 2 * offset:]
            # 同时具有左右两条边，才能排除宽亮面/单条背景边缘。
            bands.append(np.minimum(center - left, center - right).mean(axis=0))
        return max(0.0, float(np.minimum(bands[0], bands[1]).max()))

    def _check_anchor(self, frame: np.ndarray) -> float:
        """HUD 锚点校验。

        检查校准主条两端是否有成对的竖线端帽，不依赖条内亮度或血量。
        返回 0-1，越高表示锚点越可靠。
        """
        if frame.size == 0:
            return 0.0
        # 旧校准 outer ROI 可能只含端帽中段。围绕主条按比例查找完整竖边，
        # 不修改操作者的测量 ROI，也不扩大到任意屏幕位置寻找亮块。
        bar_width = self.inner_x2 - self.inner_x1
        left_pad = max(3, round(bar_width * .06), self.cap_ignore_left * 3,
                       self.inner_x1 - self.outer_x1)
        right_pad = max(3, round(bar_width * .06), self.cap_ignore_right * 3,
                        self.outer_x2 - self.inner_x2)
        left = self._cap_contrast(frame, self.inner_x1 - left_pad, self.inner_x1)
        right = self._cap_contrast(frame, self.inner_x2, self.inner_x2 + right_pad)
        contrast = min(left, right)  # 缺少任一端帽都不能用另一端的亮度补分。
        if contrast < self.min_structure_contrast:
            return 0.0
        return min(1.0, contrast / (4.0 * self.min_structure_contrast))

    @staticmethod
    def _runs(mask):
        """Half-open runs, separating independent cap candidates."""
        indices = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(np.int8)))
        return list(zip(indices[::2], indices[1::2]))

    def _strict_cap(self, frame, expected_x):
        """Locate a stem and its vertical extent in a bounded local search.

        Brightness is compared to both neighbours per B/G/R channel, so a dark
        red death cap can still contribute structure. The slot itself never
        contributes to the upper/lower stem evidence.
        """
        hud = self.hud
        thickness = hud["cap_stem_width"]
        top, bottom = hud["cap_top"] - 5, hud["cap_bottom"] + 5
        start, stop = expected_x - 8, expected_x + 9
        offset = thickness + 2
        patch = frame[top:bottom, start - offset:stop + offset].astype(np.float32)
        center = patch[:, offset:-offset]
        contrast = np.minimum(center - patch[:, :-2 * offset],
                              center - patch[:, 2 * offset:]).max(axis=2)
        upper = contrast[5: max(6, hud["slot_y1"] - top - 1)]
        lower = contrast[min(len(contrast) - 6, hud["slot_y2"] - top + 1): -5]
        scores = np.minimum(upper.mean(axis=0), lower.mean(axis=0))
        maximum = float(scores.max())
        if maximum < self.min_structure_contrast:
            return None, "hud_cap_missing"
        runs = self._runs(scores >= max(self.min_structure_contrast, maximum * .65))
        # Thin JPEG noise cannot be a second stem; two substantial structures
        # in this bounded region are ambiguous and must be rejected.
        candidates = [(a, b) for a, b in runs if b - a >= max(2, thickness - 2)]
        if len(candidates) != 1:
            return None, "hud_cap_ambiguous"
        first, last = candidates[0]
        # Bracket tips make the two sides differ in brightness. Locate the
        # stem geometrically, so asymmetric light does not bias its center.
        center_x = int((2 * start + first + last) // 2)
        column = center_x - start
        line = contrast[:, column]
        threshold = max(self.min_structure_contrast, float(line.max()) * .30)
        upper_runs = [(a, b) for a, b in self._runs(line[:hud["slot_y1"] - top] > threshold)
                      if b - a >= 2]
        lower_start = hud["slot_y2"] - top
        lower_runs = [(a + lower_start, b + lower_start)
                      for a, b in self._runs(line[lower_start:] > threshold) if b - a >= 2]
        if len(upper_runs) != 1 or len(lower_runs) != 1:
            return None, "hud_cap_ambiguous"
        cap_top = top + upper_runs[0][0]
        cap_bottom = top + lower_runs[0][1]
        center_y = math.floor((cap_top + cap_bottom - 1) / 2 + .5)
        if cap_bottom - cap_top < hud["cap_bottom"] - hud["cap_top"] - 4:
            return None, "hud_cap_missing"
        return (center_x, center_y, maximum), ""

    def _strict_anchor(self, frame):
        left, reason = self._strict_cap(frame, self.hud["cap_left_x"])
        if reason:
            return 0.0, reason, ()
        right, reason = self._strict_cap(frame, self.hud["cap_right_x"])
        if reason:
            return 0.0, reason, (left[:2],)
        positions = (left[:2], right[:2])
        hud = self.hud
        if (abs(left[0] - hud["cap_left_x"]) > hud["position_tolerance_px"]
                or abs(right[0] - hud["cap_right_x"]) > hud["position_tolerance_px"]):
            return 0.0, "hud_cap_position_mismatch", positions
        if (abs(left[1] - hud["cap_center_y"]) > hud["center_tolerance_px"]
                or abs(right[1] - hud["cap_center_y"]) > hud["center_tolerance_px"]):
            return 0.0, "hud_cap_center_mismatch", positions
        expected_spacing = hud["cap_right_x"] - hud["cap_left_x"]
        if abs(right[0] - left[0] - expected_spacing) > hud["spacing_tolerance_px"]:
            return 0.0, "hud_cap_spacing_mismatch", positions
        contrast = min(left[2], right[2])
        return min(1.0, contrast / (4 * self.min_structure_contrast)), "", positions

    def locate(self, frame: np.ndarray) -> RoiResult:
        """定位 ROI 并校验锚点。

        Args:
            frame: BGR 游戏画面

        Returns:
            RoiResult 包含裁剪结果和锚点分数
        """
        if (not isinstance(frame, np.ndarray) or frame.ndim != 3
                or frame.shape[2] != 3 or frame.size == 0 or frame.dtype != np.uint8):
            raise ValueError("Expected a non-empty uint8 BGR frame")
        h, w = frame.shape[:2]
        geometry_valid = self.outer_x2 <= w and self.outer_y2 <= h
        reject_reason = "" if geometry_valid else "roi_outside_frame"
        if self.hud is not None and (w != self.hud["frame_width"] or h != self.hud["frame_height"]):
            geometry_valid = False
            reject_reason = "hud_frame_size_mismatch"

        # 外层裁剪（越界裁剪仅供调试，不允许作为有效测量）
        outer_img = self._crop(frame, self.outer_x1, self.outer_y1, self.outer_x2, self.outer_y2)

        # 内层裁剪
        inner_img = self._crop(frame, self.inner_x1, self.inner_y1, self.inner_x2, self.inner_y2)

        # 锚点校验
        cap_positions = ()
        if self.hud is not None and geometry_valid:
            anchor_score, reject_reason, cap_positions = self._strict_anchor(frame)
        else:
            anchor_score = self._check_anchor(frame) if geometry_valid else 0.0

        valid = geometry_valid and anchor_score > 0 and anchor_score >= self.min_anchor_score
        if not valid and not reject_reason:
            reject_reason = "hud_anchor_missing"

        return RoiResult(
            valid=valid,
            anchor_score=anchor_score,
            outer_img=outer_img,
            inner_img=inner_img,
            outer_coords=(self.outer_x1, self.outer_y1, self.outer_x2, self.outer_y2),
            inner_coords=(self.inner_x1, self.inner_y1, self.inner_x2, self.inner_y2),
            reject_reason=reject_reason,
            cap_positions=cap_positions,
        )
