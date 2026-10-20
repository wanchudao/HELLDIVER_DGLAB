"""Shared EXE offline checks: independent synthetic and resampled-derived evidence.

Does not capture a screen, connect an APP, create a sender or alter preferences.
"""
import gc
import hashlib
from pathlib import Path
import sys
import tempfile
import time

import cv2
import numpy as np

from src.config_loader import load_config
from src.hud_profiles import RESOLUTION, SCALES, SUPPORTED_RESOLUTIONS, get_profile
from src.vision_pipeline import VisionPipeline
from tools.resolution_fixtures import synthetic_frame

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def _memory():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (key, ctypes.c_size_t) for key in ("PeakWorkingSetSize", "WorkingSetSize",
                "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return dict(private_bytes=counters.PrivateUsage, working_set_bytes=counters.WorkingSetSize)


def run(inspect, *, samples=None, edition="main"):
    checks, derived, performance = [], [], []
    with tempfile.TemporaryDirectory(prefix="helldiver-resolution-") as directory:
        root = Path(directory)
        for resolution in SUPPORTED_RESOLUTIONS:
            paths = []
            for scale in SCALES:
                path = root / f"synthetic_{resolution[0]}_{scale}.png"
                assert cv2.imwrite(str(path), synthetic_frame(scale, resolution))
                paths.append(path)
            for selected in SCALES:
                records = inspect(paths, selected)["images"]
                for actual, record in zip(SCALES, records, strict=True):
                    assert record["hud_valid"] is (actual == selected), record
                    if actual == selected:
                        assert record["health_raw"] == 1 and not record["first_frame_valid"]
                    checks.append(dict(resolution=list(resolution), selected=selected, actual=actual,
                                       passed=True, source="independent_reference_synthetic"))
            profile = get_profile(100, resolution)
            config = load_config(ROOT / "config.example.ini")
            config["ROI"], config["hud"] = profile["roi"], profile["hud"]
            pipeline = VisionPipeline(config)
            frame = synthetic_frame(100, resolution, .3)
            times, memory = [], []
            for index in range(160):
                started = time.perf_counter()
                result, roi, health = pipeline.process(frame, index / 30)
                times.append((time.perf_counter() - started) * 1000)
                assert roi.valid and abs(health.health_raw - .3) < .01
                if index % 20 == 0:
                    gc.collect()
                    memory.append(_memory())
            known = [row["private_bytes"] for row in memory[2:] if row is not None]
            assert not known or max(known) - min(known) < 64 * 1024 * 1024
            performance.append(dict(resolution=list(resolution), frames=len(times), source="synthetic_not_capture",
                median_ms=float(np.median(times[10:])), p95_ms=float(np.percentile(times[10:], 95)),
                memory_samples=memory, bounded_window_growth=True, observation_history_capacity=3))
            del frame, pipeline
        wrong = root / "unsupported.png"
        assert cv2.imwrite(str(wrong), np.zeros((1079, 1919, 3), np.uint8))
        assert inspect([wrong], 100)["images"][0]["reject_reason"] == "resolution_mismatch"
        if samples is not None:
            for scale in (75, 80, 85, 90, 95, 100, 125):
                original = cv2.imread(str(Path(samples) / f"hud_{scale:03d}_full.jpg"))
                assert original is not None and original.shape == (1080, 1920, 3)
                for resolution in SUPPORTED_RESOLUTIONS[1:]:
                    path = root / f"derived_{resolution[0]}_{scale}.png"
                    # Acceptance input generation only. Production never resizes frames.
                    assert cv2.imwrite(str(path), cv2.resize(original, resolution, interpolation=cv2.INTER_LINEAR))
                    for selected in SCALES:
                        record = inspect([path], selected)["images"][0]
                        assert record["hud_valid"] is (selected == scale), record
                        if selected == scale:
                            assert record["health_raw"] > .99
                        derived.append(dict(resolution=list(resolution), selected=selected, actual=scale,
                            passed=True, source="resampled_1080p_photo_not_high_res_gameplay"))
    return dict(status="ok", edition=edition, frozen=bool(getattr(sys, "frozen", False)),
        exe_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() if getattr(sys, "frozen", False) else None,
        resolutions=[list(size) for size in SUPPORTED_RESOLUTIONS], profile_combinations=33,
        synthetic_cross_checks=checks, resampled_photo_checks=derived, performance=performance,
        unsupported_size_rejected=True, no_screen_capture=True, no_app=True, physical_device_used=False,
        high_resolution_real_game_tested=False, windows_display_settings_modified=False)
