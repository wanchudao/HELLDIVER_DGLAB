"""test_calibrate.py — calibrate.py 纯函数单元测试。"""
import sys
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 手动导入 calibrate 模块（避免触发 argparse）
import importlib.util
_cp = PROJECT_ROOT / "tools" / "calibrate.py"
_spec = importlib.util.spec_from_file_location("calibrate", str(_cp))
calibrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibrate)

compute_roi_params = calibrate.compute_roi_params
validate_roi = calibrate.validate_roi


class TestComputeRoiParams:
    """验证 compute_roi_params 在各种框选下输出合理。"""

    def test_1080p_baseline_reproduces_config(self):
        """用 config.ini 的 1080p 默认外框，应该还原出已知内框值。"""
        params = compute_roi_params(x=100, y=993, w=205, h=14)

        assert params["outer_x1"] == 100
        assert params["outer_y1"] == 993
        assert params["outer_x2"] == 305
        assert params["outer_y2"] == 1007

        # 允许 ±1px 的 round 误差
        assert abs(params["inner_x1"] - 106) <= 1
        assert abs(params["inner_y1"] - 996) <= 1
        assert abs(params["inner_x2"] - 300) <= 1
        assert abs(params["inner_y2"] - 1004) <= 1

        assert params["bar_min_x"] == params["inner_x1"]
        assert params["bar_max_x"] == params["inner_x2"]

    def test_scaled_for_4k_produces_proportional_coords(self):
        """4K 外框（宽高翻倍），内框也等比例翻倍。"""
        params = compute_roi_params(x=200, y=1986, w=410, h=28)

        assert params["inner_x1"] > params["outer_x1"]
        assert params["inner_x2"] < params["outer_x2"]
        assert params["inner_y1"] > params["outer_y1"]
        assert params["inner_y2"] < params["outer_y2"]

        bar_w = params["bar_max_x"] - params["bar_min_x"]
        total_w = params["outer_x2"] - params["outer_x1"]
        assert bar_w > 0.9 * total_w, f"bar_w={bar_w} too small vs total_w={total_w}"

    def test_minimal_valid_box(self):
        """最小合理框选（720p 缩放）仍产出有效内框。"""
        params = compute_roi_params(x=10, y=500, w=100, h=7)
        assert params["inner_x1"] < params["inner_x2"]
        assert params["inner_y1"] < params["inner_y2"]
        assert params["bar_min_x"] < params["bar_max_x"]


class TestValidateRoi:
    """validate_roi 应该在合理输入通过，在异常输入拒绝。"""

    def test_valid_1080p_aspect_passes(self):
        ok, msg = validate_roi(w=205, h=14)
        assert ok, f"Expected pass, got: {msg}"

    def test_too_narrow_fails(self):
        ok, msg = validate_roi(w=60, h=10)
        assert not ok

    def test_too_short_fails(self):
        ok, msg = validate_roi(w=200, h=3)
        assert not ok

    def test_zero_dimensions_fails(self):
        ok, msg = validate_roi(w=0, h=14)
        assert not ok

    def test_too_wide_rejects_overly_stretched_box(self):
        ok, msg = validate_roi(w=500, h=10)
        assert not ok

    def test_tall_narrow_box_rejects_vertical_strip(self):
        ok, msg = validate_roi(w=20, h=80)
        assert not ok

    def test_aspect_boundary_min_passes(self):
        """宽高比恰好等于下限 8.0 应该通过。"""
        ok, msg = validate_roi(w=80, h=10)
        assert ok, f"aspect=8.0 should pass, got: {msg}"

    def test_aspect_boundary_max_passes(self):
        """宽高比恰好等于上限 22.0 应该通过。"""
        ok, msg = validate_roi(w=220, h=10)
        assert ok, f"aspect=22.0 should pass, got: {msg}"
