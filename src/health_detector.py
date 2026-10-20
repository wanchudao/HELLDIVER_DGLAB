"""
health_detector.py
血条主条分析模块 (v3 — 灰度 × 纹理惩罚)。

负责：
1. 对 inner ROI 做列均值灰度提取
2. 归一化 → filled_score
3. 找血量边界 → 计算 health_pct
4. EMA 平滑 → health_stable
5. 输出置信度

v2 变更 (2026-07-18)：
- 废弃混合特征 (redness/texture/gradient)，红屏滤镜下 redness 无区分力
- 改用纯列均值灰度：filled≈120-155, empty≈68-80, 阈值 100 左右即可分离
- _extract_column_features 向量化，去掉逐列 for 循环
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class HealthResult:
    """血量检测结果"""
    health_raw: float  # 原始血量 0-1
    health_stable: float  # EMA 平滑后血量 0-1
    filled_scores: np.ndarray  # 每列 filled_score
    fill_boundary_x: int  # 血量边界 x 坐标 (全局坐标)
    confidence: float  # 置信度 0-1
    empty: bool  # 是否为全空
    full: bool  # 是否为全满


class HealthDetector:
    """血条分析器：列均值灰度 + 边界扫描。"""

    def __init__(self, config: dict):
        from src.hud_profiles import pixel_units
        self._unit = lambda pixels: pixel_units(config, pixels)
        feat = config["features"]
        self.filled_threshold = feat["gray_filled_threshold"]
        self.empty_threshold = feat["gray_empty_threshold"]

        health_cfg = config["health"]
        self.ema_alpha = health_cfg["ema_alpha"]
        self.dead_zone_low = health_cfg["dead_zone_low"]
        self.dead_zone_high = health_cfg["dead_zone_high"]
        self.near_zero = health_cfg["near_zero"]
        self.min_confidence = float(config["unknown"]["min_confidence"])
        parameters = (self.filled_threshold, self.empty_threshold, self.ema_alpha,
                      self.dead_zone_low, self.dead_zone_high, self.near_zero,
                      self.min_confidence)
        if not all(np.isfinite(value) and 0 <= value <= 1 for value in parameters):
            raise ValueError("health thresholds and EMA alpha must be finite values in [0, 1]")
        if not (self.empty_threshold < self.filled_threshold
                and self.dead_zone_low < self.dead_zone_high and self.ema_alpha > 0):
            raise ValueError("health thresholds must be ordered and EMA alpha must be positive")

        # ROI 坐标 (用于全局 x 转换)
        self.bar_min_x = config["ROI"]["bar_min_x"]
        self.bar_max_x = config["ROI"]["bar_max_x"]
        self.inner_x1 = config["ROI"]["inner_x1"]
        self.inner_x2 = config["ROI"]["inner_x2"]
        self.cap_ignore_left = config["ROI"]["cap_ignore_left"]
        self.cap_ignore_right = config["ROI"]["cap_ignore_right"]
        if not self.inner_x1 <= self.bar_min_x < self.bar_max_x <= self.inner_x2:
            raise ValueError("health bar bounds must lie within the inner ROI")

        # EMA 状态
        self._ema_value: float = -1.0  # -1 表示尚未初始化

        logger.info(
            "health_detector v3 ready  ema_alpha=%.2f  filled>%.2f  empty<%.2f",
            self.ema_alpha, self.filled_threshold, self.empty_threshold,
        )

    # ── 特征提取 ───────────────────────────────────────

    def _extract_column_features(self, img_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """提取每列均值灰度 + 列内标准差（纹理）。

        Returns:
            col_mean: (W,) 每列平均灰度 0-255
            col_std:  (W,) 每列标准差（检测灰白条纹空槽）
        """
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        return np.mean(gray, axis=0), np.std(gray, axis=0)

    # ── 分数计算 ───────────────────────────────────────

    def _compute_filled_scores(self, col_mean: np.ndarray, col_std: np.ndarray) -> np.ndarray:
        """v3 混合特征：亮度 × 纹理惩罚。

        亮度项: mean/255（同 v2）
        纹理惩罚: 1 - std/50（std≥25 的灰白条纹空槽列被大幅降权）
        """
        luma = col_mean / 255.0
        penalty = 1.0 - col_std / 50.0
        penalty = np.clip(penalty, 0.0, 1.0)
        scores = luma * penalty
        scores = self._smooth_scores(scores)
        return np.clip(scores, 0.0, 1.0)

    def _smooth_scores(self, scores: np.ndarray) -> np.ndarray:
        """对列分数做轻量平滑，减少单列噪点导致的误判。"""
        if len(scores) < 3:
            return scores

        radius = self._unit(1)
        kernel = np.zeros(radius * 2 + 1, dtype=np.float32)
        kernel[0], kernel[radius], kernel[-1] = .2, .6, .2
        padded = np.pad(scores, (radius, radius), mode="edge")
        return np.convolve(padded, kernel, mode="valid")

    # ── 边界查找 ───────────────────────────────────────

    def _measurement_scores(self, filled_scores: np.ndarray) -> np.ndarray:
        """显式 bar 范围已排除端帽；不能再次裁掉最后几个真实血量像素。"""
        start = max(0, self.bar_min_x - self.inner_x1)
        end = min(len(filled_scores), self.bar_max_x - self.inner_x1)
        return filled_scores[start:end]

    def _find_boundary(self, filled_scores: np.ndarray) -> tuple[int, float]:
        """找血量边界。

        利用"血量从左侧连续填充，右侧为空槽"的先验，
        从左到右扫描，容忍少量 gap 后截断。

        Returns:
            (boundary_x_global, fill_ratio)
        """
        n = len(filled_scores)
        if n == 0:
            return self.bar_min_x, 0.0

        bar_width = self.bar_max_x - self.bar_min_x
        if bar_width <= 0:
            return self.bar_min_x, 0.0

        effective_scores = self._measurement_scores(filled_scores)
        effective_width = len(effective_scores)
        if effective_width <= 0:
            return self.bar_min_x, 0.0

        # 从左到右扫描，遇到连续 gap 列后截断
        gap_tolerance = self._unit(4)
        started = False
        gap_count = 0
        last_filled_rel = -1

        for i, score in enumerate(effective_scores):
            if score >= self.filled_threshold:
                started = True
                gap_count = 0
                last_filled_rel = i
                continue

            if not started:
                # 左侧连续为空却右侧出现亮块，不符合左端填充的血条。
                # 不能越过整段空槽去寻找背景亮块并把它计成血量。
                gap_count = gap_count + 1 if score <= self.empty_threshold else 0
                if gap_count >= gap_tolerance:
                    break
                continue

            if score <= self.empty_threshold:
                gap_count += 1
                if gap_count >= gap_tolerance:
                    break
            else:
                # 过渡区域不推进边界，但不触发截断
                gap_count = 0

        if last_filled_rel < 0:
            return self.bar_min_x, 0.0

        filled_width = last_filled_rel + 1
        if filled_width < effective_width:
            # 阈值只确定边缘邻域，不把最后一列跨过亮度阈值直接当成位移。
            # 抗锯齿/视频压缩会让同一边缘列在 .38 两侧持续摆动；其填充→
            # 空槽的主要下降边仍在原处。只在这几列内找最大下降边，真实
            # 的单像素边缘移动仍会完整保留，不增加 HP 变化量门槛。
            start = max(0, filled_width - self._unit(2))
            stop = min(effective_width - 1, filled_width + self._unit(3))
            drops = effective_scores[start:stop] - effective_scores[start + 1:stop + 1]
            if len(drops) and np.max(drops) > 0:
                filled_width = start + int(np.argmax(drops)) + 1

        boundary_x_global = self.bar_min_x + filled_width - 1

        fill_ratio = filled_width / effective_width

        return boundary_x_global, fill_ratio

    # ── 置信度 ─────────────────────────────────────────

    def _compute_confidence(self, filled_scores: np.ndarray) -> float:
        """基于 filled/empty 列分离度计算置信度。"""
        filled_scores = self._measurement_scores(filled_scores)
        n = len(filled_scores)
        if n == 0 or not np.all(np.isfinite(filled_scores)):
            return 0.0

        filled_mask = filled_scores >= self.filled_threshold
        empty_mask = filled_scores <= self.empty_threshold

        # 一块右侧亮区也有极高分离度，但不是由左往右填充的血条。
        if np.any(filled_mask) and not np.any(filled_mask[:self._unit(4)]):
            return 0.0

        # 连续空槽之后再出现填充不是一个左端连续的血条。
        # 与边界扫描共享 4 列 gap 容忍度，不能把背景亮块当成可靠零/低血。
        gap = self._unit(4)
        if n >= gap:
            empty_runs = np.flatnonzero(
                np.convolve(empty_mask.astype(np.int8), np.ones(gap, dtype=np.int8),
                            mode="valid") == gap)
            if len(empty_runs) and np.any(filled_mask[empty_runs[0] + gap:]):
                return 0.0

        n_filled = np.sum(filled_mask)
        n_empty = np.sum(empty_mask)
        classified_fraction = float(n_filled + n_empty) / n
        if classified_fraction == 0:
            return 0.0  # 全部落在灰区，不能把“不知道”当成高置信度空血。

        if n_filled == 0 or n_empty == 0:
            # 全空或全满：内部一致性为置信度
            mean_score = np.mean(filled_scores)
            if n_filled == 0:
                conf = 1.0 - mean_score
            else:
                conf = mean_score
            return max(0.0, min(1.0, float(conf))) * classified_fraction

        # 分离度：filled 均值 - empty 均值
        mean_filled = np.mean(filled_scores[filled_mask])
        mean_empty = np.mean(filled_scores[empty_mask])
        separation = mean_filled - mean_empty

        return max(0.0, min(1.0, float(separation) * 2.0)) * classified_fraction

    # ── 主入口 ─────────────────────────────────────────

    def detect(self, inner_img: np.ndarray, update_ema: bool = True) -> HealthResult:
        """分析血量。

        Args:
            inner_img: ROI 裁剪后的血条主条区域 (BGR)

        Returns:
            HealthResult
        """
        if inner_img.size == 0:
            return HealthResult(
                health_raw=0.0,
                health_stable=0.0,
                filled_scores=np.array([]),
                fill_boundary_x=self.bar_min_x,
                confidence=0.0,
                empty=True,
                full=False,
            )

        # 1. 列均值灰度 + 标准差
        col_mean, col_std = self._extract_column_features(inner_img)

        # 2. 混合特征 → filled_score
        filled_scores = self._compute_filled_scores(col_mean, col_std)

        # 3. 找血量边界
        boundary_x, health_raw = self._find_boundary(filled_scores)

        # 4. 死区处理
        if health_raw <= self.dead_zone_low:
            health_raw = 0.0
        elif health_raw >= self.dead_zone_high:
            health_raw = 1.0

        # 5. 先判置信度，再提交 EMA。探测/被拒帧不能污染历史。
        confidence = self._compute_confidence(filled_scores)
        if update_ema and confidence > 0 and confidence >= self.min_confidence:
            health_stable = self.commit_health(health_raw)
        else:
            health_stable = self._ema_value if self._ema_value >= 0 else health_raw

        # 7. 特殊状态标记
        empty = health_raw <= self.near_zero
        full = health_raw >= self.dead_zone_high

        return HealthResult(
            health_raw=health_raw,
            health_stable=health_stable,
            filled_scores=filled_scores,
            fill_boundary_x=boundary_x,
            confidence=confidence,
            empty=empty,
            full=full,
        )

    def commit_health(self, health_raw: float) -> float:
        """仅在完整视觉门控通过后提交一次观测值。"""
        if not np.isfinite(health_raw) or not 0.0 <= health_raw <= 1.0:
            raise ValueError("health must be finite and within [0, 1]")
        if self._ema_value < 0:
            self._ema_value = health_raw
        else:
            self._ema_value = self.ema_alpha * health_raw + (1.0 - self.ema_alpha) * self._ema_value
        return self._ema_value

    def reset_ema(self):
        """重置 EMA（例如切换截图目录时调用）。"""
        self._ema_value = -1.0
