"""
test_detector.py
离线测试：遍历 screenshots/ 目录，输出每张截图的识别结果。
Phase 1 评估默认只统计原始全屏截图，排除 crop/zoom/thumb 等派生图。
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import load_config
from src.state_machine import DetectionResult
from src.vision_pipeline import VisionPipeline

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")
DERIVED_IMAGE_MARKERS = ("_crop", "crop_", "zoom", "thumb")


@dataclass
class EvaluatedImage:
    """单张图片的评估结果。"""

    path: Path
    expected_state: str
    result: DetectionResult

    @property
    def predicted_state(self) -> str:
        return self.result.state

    @property
    def is_correct(self) -> bool:
        return self.result.valid and self.predicted_state == self.expected_state

    @property
    def is_unknown(self) -> bool:
        return self.predicted_state == "unknown"


def _evaluate_single_image(
    image_path: str,
    pipeline: VisionPipeline,
) -> DetectionResult:
    """测试单张截图。"""
    frame = cv2.imread(image_path)
    if frame is None:
        logger.error("cannot read image: %s", image_path)
        return DetectionResult(state="error", confidence=0.0)

    # 与实时和 Mock 共用门控，不能给离线评估另开宽松路径。
    result, roi_result, health_result = pipeline.process(frame)

    result.debug = {
        "image": Path(image_path).name,
        "anchor_score": round(roi_result.anchor_score, 3),
        "health_raw": round(health_result.health_raw, 3),
        "health_stable": round(health_result.health_stable, 3),
        "red_score": round(result.red_score, 3),
        "cyan_score": round(result.cyan_score, 3),
        "death_score": round(result.death_score, 3),
        "confidence": round(health_result.confidence, 3),
        "event": result.event,
        "fill_boundary_x": health_result.fill_boundary_x,
        "empty": health_result.empty,
        "full": health_result.full,
    }

    return result


def is_image_file(path: Path) -> bool:
    """是否为支持的图片文件。"""
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


def is_original_screenshot(path: Path) -> bool:
    """是否为原始全屏截图。"""
    name = path.stem.lower()
    return not any(marker in name for marker in DERIVED_IMAGE_MARKERS)


def collect_eval_targets(screenshots_dir: Path) -> list[tuple[Path, str, str]]:
    """收集需要评估的目录、期望状态和展示名。"""
    targets: list[tuple[Path, str, str]] = []

    for subdir in sorted(screenshots_dir.iterdir()):
        if not subdir.is_dir():
            continue

        if subdir.name == "healing":
            for healing_subdir in sorted(subdir.iterdir()):
                if healing_subdir.is_dir():
                    targets.append((healing_subdir, "healing", f"healing/{healing_subdir.name}"))
            continue

        targets.append((subdir, subdir.name, subdir.name))

    return targets


def _evaluate_directory(
    dir_path: str,
    expected_state: str,
    pipeline: VisionPipeline,
) -> tuple[list[EvaluatedImage], list[Path]]:
    """测试目录下所有原始截图，并返回被过滤的派生图。"""
    evaluated: list[EvaluatedImage] = []
    image_files = sorted([f for f in Path(dir_path).iterdir() if is_image_file(f)])
    original_files = [f for f in image_files if is_original_screenshot(f)]
    skipped_files = [f for f in image_files if not is_original_screenshot(f)]

    for img_path in original_files:
        result = _evaluate_single_image(
            str(img_path), pipeline
        )
        record = EvaluatedImage(path=img_path, expected_state=expected_state, result=result)
        evaluated.append(record)

        # 打印结果
        tag = "OK" if result.valid else "FAIL"
        match = "MATCH" if record.is_correct else "MISS"
        print(
            f"  {tag} {img_path.name:40s}  "
            f"expected={expected_state:12s}  "
            f"state={result.state:12s}  "
            f"hp={result.health_raw:5.1%}  "
            f"red={result.red_score:.2f}  "
            f"cyan={result.cyan_score:.2f}  "
            f"death={result.death_score:.2f}  "
            f"conf={result.confidence:.2f}  "
            f"event={result.event:12s}  "
            f"match={match}"
        )

    return evaluated, skipped_files


def print_directory_summary(display_name: str, expected_state: str, records: list[EvaluatedImage], skipped_files: list[Path]):
    """打印目录级汇总。"""
    total = len(records)
    valid = sum(1 for r in records if r.result.valid)
    correct = sum(1 for r in records if r.is_correct)
    unknown = sum(1 for r in records if r.is_unknown)
    accuracy = (correct / total) if total else 0.0

    print(f"  Summary ({display_name})")
    print(f"    expected_state={expected_state}")
    print(f"    originals={total}  valid={valid}  correct={correct}  unknown={unknown}  accuracy={accuracy:.1%}")

    if skipped_files:
        print(f"    skipped_derived={len(skipped_files)}")
        for path in skipped_files:
            print(f"      - {path.name}")

    misjudged = [r for r in records if not r.is_correct]
    if misjudged:
        print("    misjudged:")
        for record in misjudged:
            print(
                f"      - {record.path.name}: predicted={record.predicted_state}, "
                f"valid={record.result.valid}, hp={record.result.health_raw:.1%}"
            )


def print_global_summary(records: list[EvaluatedImage], skipped_total: int):
    """打印全局汇总。"""
    total = len(records)
    valid = sum(1 for r in records if r.result.valid)
    correct = sum(1 for r in records if r.is_correct)
    unknown = sum(1 for r in records if r.is_unknown)
    accuracy = (correct / total) if total else 0.0

    print(f"\n{'='*60}")
    print("PHASE 1 BASELINE SUMMARY")
    print(f"{'='*60}")
    print(f"original_images={total}  valid={valid}  correct={correct}  unknown={unknown}  accuracy={accuracy:.1%}")
    print(f"skipped_derived_images={skipped_total}")

    states = ("full_health", "injured", "death", "healing")
    for state_name in states:
        state_records = [r for r in records if r.expected_state == state_name]
        if not state_records:
            continue

        state_total = len(state_records)
        state_correct = sum(1 for r in state_records if r.is_correct)
        state_valid = sum(1 for r in state_records if r.result.valid)
        state_unknown = sum(1 for r in state_records if r.is_unknown)
        state_accuracy = state_correct / state_total
        print(
            f"  {state_name:12s} total={state_total:2d}  valid={state_valid:2d}  "
            f"correct={state_correct:2d}  unknown={state_unknown:2d}  accuracy={state_accuracy:.1%}"
        )



def main():
    """主函数：遍历 screenshots/ 下所有子目录并测试。"""
    config = load_config()

    screenshots_dir = PROJECT_ROOT / "screenshots"
    if not screenshots_dir.exists():
        print(f"screenshots directory not found: {screenshots_dir}")
        return

    eval_targets = collect_eval_targets(screenshots_dir)
    if not eval_targets:
        print(f"no evaluation targets found under: {screenshots_dir}")
        return

    all_records: list[EvaluatedImage] = []
    skipped_total = 0

    root_level_images = [f for f in screenshots_dir.iterdir() if is_image_file(f)]
    root_level_originals = [f for f in root_level_images if is_original_screenshot(f)]
    if root_level_originals:
        print("Note: uncategorized root-level screenshots are not included in Phase 1 baseline:")
        for path in sorted(root_level_originals):
            print(f"  - {path.name}")

    for subdir, expected_state, display_name in eval_targets:
        images = [f for f in subdir.iterdir() if is_image_file(f)]
        if not images:
            continue

        print(f"\n{'='*60}")
        print(f"Directory: {display_name}/")
        print(f"{'='*60}")

        # 每个目录使用独立状态机，避免跨目录污染。
        pipeline = VisionPipeline(config)

        records, skipped_files = _evaluate_directory(
            str(subdir), expected_state, pipeline
        )
        all_records.extend(records)
        skipped_total += len(skipped_files)

        print_directory_summary(display_name, expected_state, records, skipped_files)

    print_global_summary(all_records, skipped_total)


if __name__ == "__main__":
    main()
