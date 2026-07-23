"""扫描各状态截图的 inner ROI，输出列灰度 + filled_score 用于诊断 (v2)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from src.config_loader import load_config
from src.health_detector import HealthDetector


SAMPLES = {
    "full_health": "screenshots/full_health/20260621135510_1.jpg",
    "injured":     "screenshots/injured/20260621135431_1.jpg",
    "death":       "screenshots/death/20260621135532_3.jpg",
    "healing":     "screenshots/healing/group_one/20260621135448_1.jpg",
}

REPORT_COLS = 10


def main():
    config = load_config()
    roi = config["ROI"]
    detector = HealthDetector(config)

    for state_name, rel_path in SAMPLES.items():
        img_path = Path(__file__).resolve().parent.parent / rel_path
        frame = cv2.imread(str(img_path))
        if frame is None:
            print(f"SKIP {state_name}: cannot read {img_path}")
            continue

        inner = frame[roi["inner_y1"]:roi["inner_y2"], roi["inner_x1"]:roi["inner_x2"]]
        col_mean, col_std = detector._extract_column_features(inner)
        scores = detector._compute_filled_scores(col_mean, col_std)

        print(f"\n{'='*70}")
        print(f"  {state_name}  ({rel_path})")
        print(f"  inner shape={inner.shape[1]}x{inner.shape[0]}  "
              f"filled>={detector.filled_threshold}  empty<={detector.empty_threshold}")
        print(f"{'='*70}")
        print(f"{'col':>4s}  {'gray':>6s}  {'score':>7s}  {'verdict'}")
        print(f"{'-'*4}  {'-'*6}  {'-'*7}  {'-'*7}")

        filled_count = empty_count = mid_count = 0
        for col in range(inner.shape[1]):
            gray = col_mean[col]
            score = scores[col]

            if score >= detector.filled_threshold:
                verdict = "FILLED"
                filled_count += 1
            elif score <= detector.empty_threshold:
                verdict = "EMPTY"
                empty_count += 1
            else:
                verdict = "mid"
                mid_count += 1

            if col % REPORT_COLS == 0 or col == inner.shape[1] - 1:
                print(f"{col:4d}  {gray:6.0f}  {score:7.3f}  {verdict}")

        pct = filled_count / inner.shape[1] * 100
        print(f"\n  → filled={filled_count}  empty={empty_count}  mid={mid_count}  "
              f"(total cols={inner.shape[1]})  health≈{pct:.0f}%")

        result = detector.detect(inner)
        print(f"  → health_raw={result.health_raw:.3f}  health_stable={result.health_stable:.3f}  "
              f"confidence={result.confidence:.3f}  boundary_x={result.fill_boundary_x}  "
              f"empty={result.empty}  full={result.full}")

        detector.reset_ema()


if __name__ == "__main__":
    main()
