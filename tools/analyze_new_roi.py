"""用新坐标 (y=995-1005, x=105-300) 分析 4 状态血条。
从亮度和颜色两个维度输出每列数据。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

# ── 新坐标 ──────────────────────────────────────────
# 外框 (含端帽): y=993-1007, x=100-305
# 内框主条: y=996-1004, x=108-298
# 这里用偏宽松的 inner ROI 确保不丢像素
INNER_Y1, INNER_Y2 = 996, 1004   # 8px 高
INNER_X1, INNER_X2 = 106, 300    # 194px 宽

SAMPLES = {
    "full_health": "screenshots/full_health/20260621135510_1.jpg",
    "injured":     "screenshots/injured/20260621135431_1.jpg",
    "death":       "screenshots/death/20260621135532_3.jpg",
    "healing":     "screenshots/healing/group_one/20260621135448_1.jpg",
}

OUT_DIR = Path(__file__).resolve().parent.parent / "debug"


def analyze(name, rel_path):
    img_path = Path(__file__).resolve().parent.parent / rel_path
    frame = cv2.imread(str(img_path))
    if frame is None:
        print(f"SKIP {name}: cannot read {img_path}")
        return

    inner = frame[INNER_Y1:INNER_Y2, INNER_X1:INNER_X2]
    h, w = inner.shape[:2]

    # ── 保存裁剪图 ──
    save_path = OUT_DIR / f"new_roi_{name}.png"
    cv2.imwrite(str(save_path), inner)

    # ── 亮度/颜色 逐列分析 ──
    # BGR
    b = inner[:, :, 0].astype(np.float32)
    g = inner[:, :, 1].astype(np.float32)
    r = inner[:, :, 2].astype(np.float32)
    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY).astype(np.float32)

    print(f"\n{'='*70}")
    print(f"  {name}  ({rel_path})")
    print(f"  ROI: ({INNER_X1},{INNER_Y1})-({INNER_X2},{INNER_Y2})  {w}x{h}")
    print(f"  saved: {save_path}")
    print(f"{'='*70}")

    # 每隔10列打印一行
    print(f"{'col':>4s}  {'R':>5s}  {'G':>5s}  {'B':>5s}  {'gray':>5s}  {'R-max(G,B)':>10s}  {'bright?':>7s}  {'reddish?':>8s}")
    print(f"{'---':>4s}  {'---':>5s}  {'---':>5s}  {'---':>5s}  {'-----':>5s}  {'----------':>10s}  {'-------':>7s}  {'--------':>8s}")

    bright_cols = 0
    dark_cols = 0
    red_cols = 0

    for col in range(0, w, 10):
        col_r = np.mean(r[:, col])
        col_g = np.mean(g[:, col])
        col_b = np.mean(b[:, col])
        col_gray = np.mean(gray[:, col])
        redness = col_r - max(col_g, col_b)

        bright = "YES" if col_gray > 120 else "   NO"
        reddish = "YES" if redness > 15 else "   NO"

        if col_gray > 120:
            bright_cols += 1
        else:
            dark_cols += 1
        if redness > 15:
            red_cols += 1

        print(f"{col:4d}  {col_r:5.0f}  {col_g:5.0f}  {col_b:5.0f}  {col_gray:5.0f}  {redness:10.1f}  {bright:>7s}  {reddish:>8s}")

    # ── 全局统计 ──
    print(f"\n  Global stats:")
    print(f"    R  mean={np.mean(r):.0f}  min={np.min(r):.0f}  max={np.max(r):.0f}")
    print(f"    G  mean={np.mean(g):.0f}  min={np.min(g):.0f}  max={np.max(g):.0f}")
    print(f"    B  mean={np.mean(b):.0f}  min={np.min(b):.0f}  max={np.max(b):.0f}")
    print(f"    gray mean={np.mean(gray):.0f}  min={np.min(gray):.0f}  max={np.max(gray):.0f}  std={np.std(gray):.0f}")
    redness_map = r - np.maximum(g, b)
    print(f"    redness mean={np.mean(redness_map):.1f}  min={np.min(redness_map):.1f}  max={np.max(redness_map):.1f}")

    # ── 左右分区对比 ──
    mid = w // 2
    left_gray = np.mean(gray[:, :mid])
    right_gray = np.mean(gray[:, mid:])
    left_red = np.mean(redness_map[:, :mid])
    right_red = np.mean(redness_map[:, mid:])
    print(f"    LEFT half  (x={INNER_X1}-{INNER_X1+mid}):  gray={left_gray:.0f}  redness={left_red:.1f}")
    print(f"    RIGHT half (x={INNER_X1+mid}-{INNER_X2}): gray={right_gray:.0f}  redness={right_red:.1f}")

    # ── 灰度直方图 (简单分桶) ──
    bins = [(0, 50), (50, 80), (80, 120), (120, 160), (160, 200), (200, 255)]
    flat = gray.flatten()
    print(f"    Gray histogram:")
    for lo, hi in bins:
        cnt = np.sum((flat >= lo) & (flat < hi))
        pct = cnt / len(flat) * 100
        bar = "#" * int(pct / 2)
        print(f"      [{lo:3d}-{hi:3d}): {cnt:5d} px ({pct:5.1f}%) {bar}")


def main():
    OUT_DIR.mkdir(exist_ok=True)
    for name, rel_path in SAMPLES.items():
        analyze(name, rel_path)


if __name__ == "__main__":
    main()
