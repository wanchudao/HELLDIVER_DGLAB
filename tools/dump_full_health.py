"""打印指定截图的 inner ROI 原始像素数据。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from src.config_loader import load_config

config = load_config()
roi = config["ROI"]

img = cv2.imread(str(Path(__file__).resolve().parent.parent / "screenshots/full_health/20260621135510_1.jpg"))
if img is None:
    print("cannot read image")
    sys.exit(1)

inner = img[roi["inner_y1"]:roi["inner_y2"], roi["inner_x1"]:roi["inner_x2"]]
h, w = inner.shape[:2]
print(f"inner ROI: ({roi['inner_x1']},{roi['inner_y1']})-({roi['inner_x2']},{roi['inner_y2']})")
print(f"inner shape: {w}x{h} (BGR)\n")

# 每隔20列打印一列的原始 BGR + 灰度值
print(f"{'col':>4s}  {'B':>4s}  {'G':>4s}  {'R':>4s}  {'gray':>5s}  {'R-max(G,B)':>10s}")
print(f"{'---':>4s}  {'---':>4s}  {'---':>4s}  {'---':>4s}  {'-----':>5s}  {'----------':>10s}")

for col in range(0, w, 10):
    col_pixels = inner[:, col, :]  # (h, 3) BGR
    b = np.mean(col_pixels[:, 0])
    g = np.mean(col_pixels[:, 1])
    r = np.mean(col_pixels[:, 2])
    gray = np.mean(col_pixels)
    redness = r - max(g, b)
    print(f"{col:4d}  {b:4.0f}  {g:4.0f}  {r:4.0f}  {gray:5.0f}  {redness:10.1f}")

# 全局统计
gray_full = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
print(f"\nglobal: gray_mean={np.mean(gray_full):.0f}  gray_min={np.min(gray_full):.0f}  gray_max={np.max(gray_full):.0f}  gray_std={np.std(gray_full):.0f}")
print(f"B mean={np.mean(inner[:,:,0]):.0f}  G mean={np.mean(inner[:,:,1]):.0f}  R mean={np.mean(inner[:,:,2]):.0f}")

# 保存裁剪图用于发给 GPT-4o 验证
out_path = Path(__file__).resolve().parent.parent / "debug" / "full_health_inner_roi.png"
out_path.parent.mkdir(exist_ok=True)
cv2.imwrite(str(out_path), inner)
print(f"\ncropped ROI saved to: {out_path}")
