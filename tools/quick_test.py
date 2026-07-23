"""快速灰度阈值测试：统计每张截图有血/空血列数。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

INNER_Y1, INNER_Y2 = 996, 1004
INNER_X1, INNER_X2 = 106, 300

SAMPLES = {
    "full_health": "screenshots/full_health/20260621135510_1.jpg",
    "injured":     "screenshots/injured/20260621135431_1.jpg",
    "death":       "screenshots/death/20260621135532_3.jpg",
    "healing":     "screenshots/healing/group_one/20260621135448_1.jpg",
}

for name, rel_path in SAMPLES.items():
    frame = cv2.imread(str(Path(__file__).resolve().parent.parent / rel_path))
    inner = frame[INNER_Y1:INNER_Y2, INNER_X1:INNER_X2]
    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)

    # 每列平均灰度
    col_means = np.mean(gray, axis=0)  # (W,)

    # 阈值 100：gray > 100 = 有血
    has_blood = np.sum(col_means > 100)
    no_blood = np.sum(col_means <= 100)
    total = len(col_means)
    pct = has_blood / total * 100

    print(f"{name:15s}  has_blood={has_blood:3d}  no_blood={no_blood:3d}  "
          f"total={total}  ratio={pct:.0f}%")

    # 找分界线：从左到右第一个 gray <= 100 的列
    boundary = None
    for i, v in enumerate(col_means):
        if v <= 100:
            boundary = INNER_X1 + i
            break
    if boundary:
        print(f"            boundary at x={boundary}")
    else:
        print(f"            all columns > 100 (full)")
    print()
