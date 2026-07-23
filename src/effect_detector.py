"""
effect_detector.py
全屏特效检测模块。

负责：
1. 红屏检测 (red_score)
2. 青蓝滤镜检测 (cyan_score)
3. 死亡辅助判定 (death_score)
4. 要求多信号同时满足才给出高 score
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EffectResult:
    """特效检测结果"""
    red_score: float  # 红屏强度 0-1
    cyan_score: float  # 青蓝滤镜强度 0-1
    death_score: float  # 死亡辅助置信度 0-1
    edge_samples: dict  # 各采样区的原始值（调试用）


class EffectDetector:
    """全屏特效检测器。"""

    def __init__(self, config: dict):
        filt = config["filters"]
        self.red_threshold = filt["red_threshold"]
        self.red_ratio_threshold = filt["red_ratio_threshold"]
        self.cyan_threshold = filt["cyan_threshold"]
        self.cyan_ratio_threshold = filt["cyan_ratio_threshold"]

        death_cfg = config["death"]
        self.health_near_zero = death_cfg["health_near_zero"]
        self.red_extreme_ratio = death_cfg["red_extreme_ratio"]
        self.death_score_threshold = death_cfg["death_score_threshold"]

        # 解析边缘采样区
        self.edge_regions = []
        for i in range(1, 10):  # 最多支持 10 个区域
            key = f"edge_region_{i}"
            if key not in filt:
                break
            vals = [x.strip() for x in str(filt[key]).split(",")]
            if len(vals) == 5:
                x1 = int(float(vals[0]))
                y1 = int(float(vals[1]))
                x2 = int(float(vals[2]))
                y2 = int(float(vals[3]))
                w = float(vals[4])
                self.edge_regions.append((x1, y1, x2, y2, w))

        if not self.edge_regions:
            # 默认采样区：上下左右四边
            self.edge_regions = [
                (200, 0, 1720, 40, 1.0),
                (200, 1040, 1720, 1080, 1.0),
                (0, 200, 40, 880, 0.8),
                (1880, 200, 1920, 880, 0.8),
            ]

        logger.info(
            "effect_detector ready  edge_regions=%d  red_thresh=%d  cyan_thresh=%d",
            len(self.edge_regions), self.red_threshold, self.cyan_threshold,
        )

    def _safe_crop(self, frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(x1 + 1, min(x2, w))
        y2 = max(y1 + 1, min(y2, h))
        return frame[y1:y2, x1:x2]

    def _calc_red_cyan(self, region_bgr: np.ndarray) -> tuple[float, float]:
        """计算一个区域内红屏和青蓝像素的占比。"""
        b = region_bgr[:, :, 0].astype(np.float32)
        g = region_bgr[:, :, 1].astype(np.float32)
        r = region_bgr[:, :, 2].astype(np.float32)

        total = region_bgr.shape[0] * region_bgr.shape[1]
        if total == 0:
            return 0.0, 0.0

        # 红像素: R - max(G, B) > threshold
        red_diff = r - np.maximum(g, b)
        red_count = np.sum(red_diff > self.red_threshold)
        red_ratio = red_count / total

        # 青蓝像素: B - R > threshold
        cyan_diff = b - r
        cyan_count = np.sum(cyan_diff > self.cyan_threshold)
        cyan_ratio = cyan_count / total

        return red_ratio, cyan_ratio

    def detect(self, frame: np.ndarray, health_raw: float = -1.0) -> EffectResult:
        """检测全屏特效。

        Args:
            frame: BGR 画面
            health_raw: 当前血量（用于死亡判定），-1 表示忽略

        Returns:
            EffectResult
        """
        h, w = frame.shape[:2]

        # 加权平均 red_ratio 和 cyan_ratio
        total_weight = 0.0
        weighted_red = 0.0
        weighted_cyan = 0.0
        edge_samples = {}

        for idx, (x1, y1, x2, y2, weight) in enumerate(self.edge_regions):
            region = self._safe_crop(frame, x1, y1, x2, y2)
            red_ratio, cyan_ratio = self._calc_red_cyan(region)
            weighted_red += red_ratio * weight
            weighted_cyan += cyan_ratio * weight
            total_weight += weight
            edge_samples[f"region_{idx}"] = {
                "coords": (x1, y1, x2, y2),
                "red_ratio": round(red_ratio, 4),
                "cyan_ratio": round(cyan_ratio, 4),
                "weight": weight,
            }

        if total_weight > 0:
            red_score = weighted_red / total_weight
            cyan_score = weighted_cyan / total_weight
        else:
            red_score = 0.0
            cyan_score = 0.0

        # 死亡辅助判定：多信号融合
        death_score = 0.0
        if health_raw >= 0:
            # 信号1: 血量接近 0
            signal_health = 1.0 if health_raw <= self.health_near_zero else 0.0

            # 信号2: 极强红屏
            signal_red = 1.0 if red_score >= self.red_extreme_ratio else 0.0

            # 融合：至少两个信号同时满足
            signals = [signal_health, signal_red]
            signal_sum = sum(signals)
            if signal_sum >= 2:
                death_score = min(1.0, signal_sum / len(signals))
            else:
                death_score = 0.0

        return EffectResult(
            red_score=red_score,
            cyan_score=cyan_score,
            death_score=death_score,
            edge_samples=edge_samples,
        )
