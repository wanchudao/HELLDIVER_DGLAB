"""calibrate.py — 血条坐标校准工具。

用法:
    # 实时校准（需要游戏运行中）
    python tools/calibrate.py

    # 离线测试（从已有截图校准）
    python tools/calibrate.py --image debug/all_samples/full_01.jpg

流程:
    1. 倒计时（实时模式 5 秒，--image 模式跳过）
    2. mss 截取主屏 / 加载图片
    3. 弹出 OpenCV 窗口，用户鼠标框选血条外框（含端帽）
    4. 校验框选宽高比是否合理
    5. 按比例推导外框/内框/端帽等全部参数
    6. 备份 config.ini → 写入坐标 + calibration 标记
"""
import argparse
import configparser
import shutil
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import sys

# 确保项目根目录在 sys.path 中（与 runner_mock.py 保持一致）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CONFIG_PATH = PROJECT_ROOT / "config.ini"

# ── 坐标推导比例 — 来源: config.ini (100,993)-(305,1007) vs (106,996)-(300,1004) ──
# 注意: HD2 HUD 是锚点布局，16:9 下等比缩放成立；21:9 带鱼屏未验证
_LEFT_CAP_RATIO = 0.0293       # 左端帽占外框宽度比例 (6/205)
_RIGHT_CAP_RATIO = 0.0244      # 右端帽占外框宽度比例 (5/205)
_TOP_PAD_RATIO = 0.2143        # 内框上边距占外框高度比例 (3/14)
_BOTTOM_PAD_RATIO = 0.2143     # 内框下边距占外框高度比例 (3/14)
_CAP_IGNORE_RATIO = 0.0195     # 端帽忽略宽度占外框宽度比例 (4/205)
_EXPECTED_ASPECT_MIN = 8.0     # 血条外框宽高比下限 (w/h)
_EXPECTED_ASPECT_MAX = 22.0    # 血条外框宽高比上限
_COUNTDOWN_SEC = 5             # 倒计时秒数


def compute_roi_params(x: int, y: int, w: int, h: int) -> dict:
    """从用户框选的外框 (x, y, w, h) 推导全部 ROI 参数。

    Args:
        x, y, w, h: 用户用 cv2.selectROI 框选的血条外框（像素坐标，含端帽）

    Returns:
        dict: config.ini [ROI] 节所需全部参数
    """
    outer_x1 = x
    outer_y1 = y
    outer_x2 = x + w
    outer_y2 = y + h

    inner_x1 = outer_x1 + round(w * _LEFT_CAP_RATIO)
    inner_x2 = outer_x2 - round(w * _RIGHT_CAP_RATIO)
    inner_y1 = outer_y1 + round(h * _TOP_PAD_RATIO)
    inner_y2 = outer_y2 - round(h * _BOTTOM_PAD_RATIO)

    cap_ignore = max(1, round(w * _CAP_IGNORE_RATIO))

    return {
        "outer_x1": outer_x1,
        "outer_y1": outer_y1,
        "outer_x2": outer_x2,
        "outer_y2": outer_y2,
        "inner_x1": inner_x1,
        "inner_y1": inner_y1,
        "inner_x2": inner_x2,
        "inner_y2": inner_y2,
        "bar_min_x": inner_x1,
        "bar_max_x": inner_x2,
        "cap_ignore_left": cap_ignore,
        "cap_ignore_right": cap_ignore,
    }


def validate_roi(w: int, h: int) -> tuple:
    """校验框选尺寸是否合理。

    Returns:
        (is_valid, message)
    """
    if w <= 0 or h <= 0:
        return False, "框选区域无效（宽或高 ≤ 0）"

    aspect = w / h

    if w < 80:
        return False, f"框选太窄（宽={w}px < 80px），可能未包含完整血条"
    if h < 6:
        return False, f"框选太矮（高={h}px < 6px），可能只框了边框线"

    if aspect < _EXPECTED_ASPECT_MIN:
        return False, f"宽高比过低 ({aspect:.1f} < {_EXPECTED_ASPECT_MIN})，疑似框了竖条而非血条"
    if aspect > _EXPECTED_ASPECT_MAX:
        return False, f"宽高比过高 ({aspect:.1f} > {_EXPECTED_ASPECT_MAX})，疑似框了过多背景"

    return True, "OK"


def write_config(params: dict, resolution: tuple, config_path: Path):
    """将校准参数写入 config.ini。

    写入前自动备份原文件（configparser.write() 会丢失注释）。
    """
    # 写入前备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = config_path.with_suffix(f".ini.bak.{timestamp}")
    shutil.copy2(str(config_path), str(backup_path))

    cp = configparser.ConfigParser()
    cp.read(str(config_path), encoding="utf-8")

    # 写 ROI 坐标参数
    for key, value in params.items():
        cp.set("ROI", key, str(value))

    # 写 calibration 标记
    if not cp.has_section("calibration"):
        cp.add_section("calibration")
    cp.set("calibration", "calibrated", "1")
    cp.set("calibration", "resolution", f"{resolution[0]}x{resolution[1]}")

    with open(config_path, "w", encoding="utf-8") as f:
        cp.write(f)

    print(f"  [备份] 原配置已备份到: {backup_path.name}")


def _fit_to_screen(img: np.ndarray) -> tuple[np.ndarray, float]:
    """如果图像大于主屏，缩小至主屏尺寸的 90%。

    解决 4K 截图在 1080p 显示器上无法完整显示的问题。
    缩放后的坐标会在 select_roi_loop 返回后自动乘回。

    Returns:
        (display_img, scale)  — scale=1.0 表示无需缩放
    """
    try:
        import mss
        with mss.MSS() as sct:
            monitor = sct.monitors[1]
            screen_w, screen_h = monitor["width"], monitor["height"]
    except Exception:
        return img, 1.0

    img_h, img_w = img.shape[:2]
    scale = min(1.0, screen_w / img_w, screen_h / img_h)

    if scale >= 1.0:
        return img, 1.0

    scale *= 0.9  # 仅在确实需要缩小时留 10% 边距

    new_w, new_h = int(img_w * scale), int(img_h * scale)
    display = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return display, scale


def select_roi_loop(img: np.ndarray) -> tuple | None:
    """交互式框选循环：校验不通过时自动重新弹框选窗口。

    框选失败后直接循环回 cv2.selectROI，不做中间 waitKey
    （已 destroyAllWindows，waitKey 会死锁）。
    用户可在 selectROI 弹出窗口中按 ESC 取消。

    Returns:
        (x, y, w, h) 或 None（用户按 ESC 取消）

    注意：返回的坐标相对于传入的 img 图像。如果 img 已缩放，调用方负责还原。
    """
    while True:
        roi = cv2.selectROI(
            "框选血条外框（含左右端帽）→ 回车确认 → ESC 退出",
            img, showCrosshair=True,
        )
        cv2.destroyAllWindows()

        x, y, w, h = roi
        if w == 0 or h == 0:
            return None

        is_valid, msg = validate_roi(w, h)
        if is_valid:
            return x, y, w, h

        # 校验不通过 → 打印警告，自动循环回 selectROI 让用户重框
        print(f"\n  [警告] {msg}")
        print(f"         宽={w}px 高={h}px  宽高比={w/h:.1f}")
        print(f"         期望宽高比范围: {_EXPECTED_ASPECT_MIN}-{_EXPECTED_ASPECT_MAX}")
        print( "         请重新框选..." )
        print()


def main():
    parser = argparse.ArgumentParser(description="HELLDIVER_DGLAB 血条坐标校准")
    parser.add_argument(
        "--image", type=str, default=None,
        help="从本地截图校准（离线测试用），不指定则使用 mss 实时截图",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("  HELLDIVER_DGLAB — 血条坐标校准")
    print("=" * 50)
    print()

    # ── 获取截图 ──
    if args.image:
        img_path = Path(args.image)
        if not img_path.exists():
            print(f"  [错误] 文件不存在: {img_path}")
            return
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"  [错误] 无法读取: {img_path}")
            return
        print(f"  [镜像模式] 从文件加载: {img_path.name}")
    else:
        print("  准备截取主屏幕。请切换到游戏全屏画面（飞船舱内/满血菜单即可）。")
        print("  HUD Scale 请保持在 100%（默认值）。")
        print()

        # 倒计时
        for i in range(_COUNTDOWN_SEC, 0, -1):
            print(f"  {i}...")
            time.sleep(1)

        print("\a", end="", flush=True)  # 终端蜂鸣
        print("  >>> 截图完成! 请查看弹出的窗口 <<<")
        print()

        # 复用项目已有的屏幕捕获
        try:
            from src.screen_capture import ScreenCapture
        except ImportError:
            print("  [错误] 无法导入 ScreenCapture，请确认在项目根目录运行。")
            return

        cap = ScreenCapture(monitor_index=1)
        if not cap.is_ready:
            print("  [错误] 屏幕捕获初始化失败（mss 未安装？运行: pip install mss）")
            return
        img = cap.grab()
        cap.close()
        if img is None:
            print("  [错误] 截图失败。")
            return

    resolution = (img.shape[1], img.shape[0])
    print(f"  分辨率: {resolution[0]}x{resolution[1]}")

    # ── 自动缩放到屏幕大小（4K 截图在 1080p 显示器上也能框选）──
    display_img, display_scale = _fit_to_screen(img)
    if display_scale < 1.0:
        print(f"  [适配] 图像大于屏幕，已缩放至 {display_scale:.1%} 供框选，坐标自动还原")
    print()
    print("  请用鼠标框选血条外框（含左右端帽，勿多出背景）")
    print("  按 回车 确认 | 按 ESC 取消")
    print()

    # ── 交互式框选 ──
    roi = select_roi_loop(display_img)
    if roi is None:
        print("  [取消] 校准已中止。")
        return

    # 还原到原始图像坐标
    if display_scale < 1.0:
        roi = (
            round(roi[0] / display_scale),
            round(roi[1] / display_scale),
            round(roi[2] / display_scale),
            round(roi[3] / display_scale),
        )

    x, y, w, h = roi

    # ── 推导参数 ──
    params = compute_roi_params(x, y, w, h)

    # ── 展示结果 ──
    print()
    print(f"  外框: ({params['outer_x1']},{params['outer_y1']}) - ({params['outer_x2']},{params['outer_y2']})")
    print(f"  内框: ({params['inner_x1']},{params['inner_y1']}) - ({params['inner_x2']},{params['inner_y2']})")
    print(f"  有效宽度: {params['bar_max_x'] - params['bar_min_x']}px")
    print()

    # ── 写入配置 ──
    write_config(params, resolution, CONFIG_PATH)

    print(f"  [OK] 校准完成! 坐标已写入 config.ini")
    print(f"    分辨率: {resolution[0]}x{resolution[1]}")
    print(f"    如需重新校准，再次运行: python tools/calibrate.py")
    print(f"    注释已被清除（configparser 限制），备份文件中有原注释。")
    print()


if __name__ == "__main__":
    main()
