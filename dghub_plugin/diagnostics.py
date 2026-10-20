"""Offline screenshot inspection using the selected shared HUD profile only."""
import hashlib
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image

from dghub_plugin.sdk_config import core_config, validate
from src.hud_profiles import SUPPORTED_RESOLUTIONS, source_label
from src.vision_pipeline import VisionPipeline


def inspect_images(paths, settings, base_config):
    settings = validate(settings)
    scale = settings["hud_scale_percent"]
    records = []
    for value in paths:
        path = Path(value).resolve()
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("截图文件过大")
        with Image.open(path) as header:
            if header.width * header.height > 40_000_000:
                raise ValueError("截图像素数量过大")
        encoded = path.read_bytes()
        frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("无法读取截图")
        height, width = frame.shape[:2]
        supported = (width, height) in SUPPORTED_RESOLUTIONS
        config = core_config(base_config, settings, (width, height)) if supported else None
        record = dict(file=path.name, path=str(path), sha256=hashlib.sha256(encoded).hexdigest(),
                      resolution=f"{width}x{height}", hud_scale=scale,
                      hud_source=source_label(scale, (width, height)) if supported else "不支持的分辨率",
                      source=config["hud"]["source"] if config else None,
                      profile_version=config["hud"]["version"] if config else None,
                      base_source=config["hud"].get("base_source", config["hud"]["source"]) if config else None)
        if not supported:
            record.update(health_raw=None, confidence=0., anchor_score=0., hud_valid=False,
                          first_frame_valid=False, first_frame_reason="resolution_mismatch",
                          reject_reason="resolution_mismatch", outer_coords=None, inner_coords=None,
                          bar_bounds=None, cap_positions=None)
        else:
            # Fresh pipeline per original image: no event dispatch or sender.
            result, roi, health = VisionPipeline(config).process(frame, timestamp=0)
            record.update(health_raw=float(health.health_raw), confidence=float(health.confidence),
                          anchor_score=float(roi.anchor_score), hud_valid=bool(roi.valid),
                          first_frame_valid=bool(result.valid),
                          first_frame_reason=result.debug.get("reject_reason", ""),
                          reject_reason=roi.reject_reason or result.debug.get("reject_reason", ""),
                          outer_coords=list(roi.outer_coords), inner_coords=list(roi.inner_coords),
                          bar_bounds=[config["ROI"]["bar_min_x"], config["ROI"]["bar_max_x"]],
                          cap_positions=[config["hud"]["cap_left_x"], config["hud"]["cap_right_x"]])
        records.append(record)
    return dict(status="ok", frozen=bool(getattr(sys, "frozen", False)),
                mode="DGHub shared vision; one original image per fresh pipeline",
                screen_capture_started=False, app_connected=False, images=records)
