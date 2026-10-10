"""Original-photo HUD matrix for source and the exact frozen desktop edition."""
from pathlib import Path


PHOTO_SCALES = (75, 80, 85, 90, 95, 100, 125)


def run(inspect_images, samples_dir):
    from src.hud_profiles import SCALES, PROFILE_VERSION, get_profile
    samples_dir = Path(samples_dir)
    checks = []
    for actual in PHOTO_SCALES:
        path = samples_dir / f"hud_{actual:03d}_full.jpg"
        if not path.is_file():
            raise ValueError(f"HUD 原始素材缺失：{path}")
        for selected in SCALES:
            report = inspect_images([path],hud_settings={"mode":"manual","scale_percent":selected})
            assert not report["app_connected"] and not report["screen_capture_started"]
            reading = report["images"][0]
            if actual == selected:
                assert reading["hud_valid"], reading
                assert reading["health_raw"] >= .99, reading
                assert not reading["first_frame_valid"], reading
                assert reading["first_frame_reason"] == "observation_warmup", reading
            else:
                assert not reading["hud_valid"] and not reading["first_frame_valid"], reading
            checks.append({"actual_scale":actual,"selected_scale":selected,
                           "source":"original_full_health_photo","sha256":reading["sha256"],
                           "hud_valid":reading["hud_valid"],"health_raw":reading["health_raw"],
                           "reject_reason":reading["reject_reason"],"passed":True})
    aux = samples_dir / "hud_100_aux_1919x1079.png"
    auxiliary_check = None
    if aux.is_file():
        reading = inspect_images([aux],hud_settings={"mode":"manual","scale_percent":100})["images"][0]
        assert reading["resolution"] == "1919x1079" and reading["reject_reason"] == "resolution_mismatch"
        assert not reading["hud_valid"] and reading["health_raw"] is None
        auxiliary_check = {"resolution":reading["resolution"],"sha256":reading["sha256"],
                           "reject_reason":reading["reject_reason"],"passed":True}
    return {"status":"ok","scales":list(SCALES),"profile_version":PROFILE_VERSION,
            "photo_profiles":list(PHOTO_SCALES),"interpolated_profiles":[105,110,115,120],
            "wrong_profile_rejected":True,"no_app":True,"no_capture":True,
            "checks":checks,"auxiliary_size_check":auxiliary_check,
            "profiles":[get_profile(scale) for scale in SCALES],
            "real_game_tested":False,"user_phone_app_tested":False}
