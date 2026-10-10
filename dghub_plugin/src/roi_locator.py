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

        # 锚点阈值
        min_anchor = float(config.get("unknown", {}).get("min_anchor_score", 0.5))
        self.min_anchor_score = min_anchor

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

    def _check_anchor(self, outer_img: np.ndarray) -> float:
        """HUD 锚点校验。

        检查外层 ROI 内是否有血条端帽特征（高对比度竖线或边框结构）。
        返回 0-1，越高表示锚点越可靠。
        """
        if outer_img.size == 0:
            return 0.0

        gray = cv2.cvtColor(outer_img, cv2.COLOR_BGR2GRAY)

        # 方法1：检测竖线端帽 — 左右两侧应有较高的亮度对比
        h, w = gray.shape
        if w < 10:
            return 0.0

        # 取左端帽区域和右端帽区域
        cap_w = max(3, self.cap_ignore_left)
        left_cap = gray[:, :cap_w]
        right_cap = gray[:, -cap_w:]

        # 端帽区域应有较高灰度（亮色边框）
        left_mean = np.mean(left_cap)
        right_mean = np.mean(right_cap)

        # 端帽越亮 → 锚点越可靠
        cap_score = min(1.0, max(left_mean, right_mean) / 180.0)

        # 方法2：检测主条区域内的纹理变化
        inner_x1_rel = self.inner_x1 - self.outer_x1
        inner_x2_rel = self.inner_x2 - self.outer_x1
        inner_y1_rel = self.inner_y1 - self.outer_y1
        inner_y2_rel = self.inner_y2 - self.outer_y1

        inner_x1_rel = max(0, min(inner_x1_rel, w - 1))
        inner_x2_rel = max(inner_x1_rel + 1, min(inner_x2_rel, w))
        inner_y1_rel = max(0, min(inner_y1_rel, h - 1))
        inner_y2_rel = max(inner_y1_rel + 1, min(inner_y2_rel, h))

        inner_region = gray[inner_y1_rel:inner_y2_rel, inner_x1_rel:inner_x2_rel]
        if inner_region.size > 0:
            # 主条区域应有较高方差（亮暗交替）
            inner_std = np.std(inner_region)
            texture_score = min(1.0, inner_std / 40.0)
        else:
            texture_score = 0.0

        # 方法3：主条区域平均亮度不应过暗（HUD 不存在时通常很暗）
        if inner_region.size > 0:
            inner_mean = np.mean(inner_region)
            brightness_score = min(1.0, inner_mean / 80.0)
        else:
            brightness_score = 0.0

        # 综合锚点分数
        anchor_score = 0.4 * cap_score + 0.35 * texture_score + 0.25 * brightness_score

        return min(1.0, max(0.0, anchor_score))

    def locate(self, frame: np.ndarray) -> RoiResult:
        """定位 ROI 并校验锚点。

        Args:
            frame: BGR 游戏画面

        Returns:
            RoiResult 包含裁剪结果和锚点分数
        """
        # 外层裁剪
        outer_img = self._crop(frame, self.outer_x1, self.outer_y1, self.outer_x2, self.outer_y2)

        # 内层裁剪
        inner_img = self._crop(frame, self.inner_x1, self.inner_y1, self.inner_x2, self.inner_y2)

        # 锚点校验
        anchor_score = self._check_anchor(outer_img)

        valid = anchor_score >= self.min_anchor_score

        return RoiResult(
            valid=valid,
            anchor_score=anchor_score,
            outer_img=outer_img,
            inner_img=inner_img,
            outer_coords=(self.outer_x1, self.outer_y1, self.outer_x2, self.outer_y2),
            inner_coords=(self.inner_x1, self.inner_y1, self.inner_x2, self.inner_y2),
        )
