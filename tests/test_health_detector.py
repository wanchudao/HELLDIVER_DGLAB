"""test_health_detector.py — v3 _compute_filled_scores 纯函数测试。"""
import sys
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import load_config
from src.health_detector import HealthDetector


def _make_detector():
    config = load_config(str(PROJECT_ROOT / "config.ini"))
    return HealthDetector(config)


class TestComputeFilledScores:
    """v3 公式: score = (mean/255) x (1 - std/50)"""

    def test_filled_column(self):
        """真有血：高 mean + 低 std → 高分"""
        det = _make_detector()
        mean = np.array([150.0], dtype=np.float32)
        std = np.array([0.8], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert 0.55 < scores[0] < 0.62, f"expected ~0.58, got {scores[0]:.3f}"

    def test_empty_darkred_column(self):
        """暗红空槽：低 mean + 高 std → 低分"""
        det = _make_detector()
        mean = np.array([75.0], dtype=np.float32)
        std = np.array([27.0], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert 0.10 < scores[0] < 0.18, f"expected ~0.14, got {scores[0]:.3f}"

    def test_empty_graywhite_column(self):
        """灰白条纹空槽：中 mean + 高 std → 低分"""
        det = _make_detector()
        mean = np.array([139.0], dtype=np.float32)
        std = np.array([25.0], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert 0.23 < scores[0] < 0.32, f"expected ~0.27, got {scores[0]:.3f}"

    def test_full_health_column(self):
        """满血：高 mean + 低 std → 极高分"""
        det = _make_detector()
        mean = np.array([254.0], dtype=np.float32)
        std = np.array([0.5], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert scores[0] > 0.95, f"expected ~0.99, got {scores[0]:.3f}"

    def test_max_penalty(self):
        """std=50 时惩罚归零"""
        det = _make_detector()
        mean = np.array([200.0], dtype=np.float32)
        std = np.array([50.0], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert scores[0] < 0.05, f"expected ~0, got {scores[0]:.3f}"

    def test_smooth_does_not_distort(self):
        """平滑不改变分数排序：高→低→高 的形状应保留"""
        det = _make_detector()
        mean = np.array([254, 139, 75, 139, 254], dtype=np.float32)
        std  = np.array([0.5, 25, 27, 25, 0.5], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert scores[0] > scores[1] > scores[2]
        assert scores[4] > scores[3] > scores[2]
