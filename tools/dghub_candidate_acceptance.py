"""Verify frozen source provenance, offline HUD checks and launcher policy."""
import argparse
import hashlib
import json
import marshal
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.inspect_frozen_archive import read_archive, read_pyz
from tools.package_main_release import normalized
from src.hud_profiles import MEASURED_SCALES, SCALES


def run(bundle, reports):
    bundle, reports = Path(bundle).resolve(), Path(reports).resolve()
    reports.mkdir(parents=True, exist_ok=True)
    exe = bundle / "helldiver_dglab.exe"
    version, _entries, extract = read_archive(exe)
    if version != sys.version_info.major * 100 + sys.version_info.minor:
        raise RuntimeError("Candidate bytecode version differs from verifier")
    _magic, modules, extract_module = read_pyz(extract("PYZ.pyz"))
    required = {"src.hud_profiles", "src.vision_pipeline", "src.screen_capture", "src.panel_window",
                "dghub_plugin.sdk_transport", "dghub_plugin.live_driver", "dghub_plugin.capture_worker",
                "dghub_plugin.diagnostics", "dghub_plugin.panel", "mss", "websockets.asyncio.client"}
    required.update({"dghub_plugin.session_record", "dghub_plugin.session_storage", "dghub_plugin.replay_capture"})
    required.add("dghub_plugin.preset_catalog")
    if not required.issubset(modules):
        raise RuntimeError("Candidate missing implementation component")
    forbidden = {"src.dglab_sender", "src.desktop_runtime", "tools.desktop_app", "websocket", "qrcode"}
    if forbidden.intersection(modules) or (bundle / "_internal/runtime/node.exe").exists():
        raise RuntimeError("Official backend present in DGHub candidate")
    checked = []
    for name, (kind, _offset, _length) in modules.items():
        if kind not in (0, 1) or not (name == "src" or name.startswith("src.")
                                     or name == "dghub_plugin" or name.startswith("dghub_plugin.")):
            continue
        path = ROOT.joinpath(*name.split(".")).with_suffix(".py")
        if not path.is_file():
            path = path.with_suffix("") / "__init__.py"
        frozen = marshal.loads(extract_module(name))
        source = compile(path.read_bytes(), str(path), "exec", dont_inherit=True, optimize=0)
        if normalized(frozen) != normalized(source):
            raise RuntimeError("Frozen source mismatch: " + name)
        checked.append(name)
    frozen = marshal.loads(extract("dghub_live_entry"))
    source = compile((ROOT / "tools/dghub_live_entry.py").read_bytes(), "dghub_live_entry", "exec",
                     dont_inherit=True, optimize=0)
    if normalized(frozen) != normalized(source):
        raise RuntimeError("Frozen entry mismatch")
    checked.append("dghub_live_entry")
    guide = (bundle / "_internal/docs/dghub_plugin_guide.md").read_bytes()
    if guide != (ROOT / "docs/dghub_plugin_guide.md").read_bytes():
        raise RuntimeError("Frozen guide mismatch")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("DGHUB_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment["PATH"] = ""

    def execute(args):
        result = subprocess.run([str(exe), *args], cwd=bundle, env=environment,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                timeout=8, check=False, capture_output=True)
        return result.returncode

    # The shipping entry is now usable with SDK launcher variables. Outside
    # DGHub it must report missing environment, not the old candidate gate (2).
    if execute([]) != 1:
        raise RuntimeError("Normal entry did not reject missing SDK environment explicitly")
    samples = ROOT / "screenshots/hud_1080p_20261002"
    checks = []
    images = [samples / f"hud_{scale:03d}_full.jpg" for scale in MEASURED_SCALES]
    for selected in SCALES:
        destination = reports / f"hud_{selected:03d}.json"
        if execute(["--inspect-images", *map(str, images), "--hud-scale", str(selected),
                    "--report", str(destination)]) != 0:
            raise RuntimeError("Frozen screenshot check failed")
        report = json.loads(destination.read_text(encoding="utf-8"))
        if not report["frozen"] or report["app_connected"] or report["screen_capture_started"]:
            raise RuntimeError("Screenshot candidate used wrong runtime")
        for actual, record in zip(MEASURED_SCALES, report["images"], strict=True):
            if record["hud_valid"] != (actual == selected):
                raise RuntimeError("Frozen HUD profile mismatch")
            if actual == selected and record["health_raw"] != 1:
                raise RuntimeError("Frozen full-health measurement mismatch")
            checks.append(dict(actual=actual, selected=selected, hud_valid=record["hud_valid"],
                               source="original_full_health_photo", passed=True))
    auxiliary = reports / "wrong_size.json"
    if execute(["--inspect-images", str(samples / "hud_100_aux_1919x1079.png"), "--hud-scale", "100",
                "--report", str(auxiliary)]) != 0:
        raise RuntimeError("Frozen wrong-size inspection failed")
    record = json.loads(auxiliary.read_text(encoding="utf-8"))["images"][0]
    if record["hud_valid"] or record["health_raw"] is not None or record["reject_reason"] != "resolution_mismatch":
        raise RuntimeError("Frozen candidate rescaled wrong-sized input")
    return dict(status="ok", scope="frozen provenance and offline HUD; host runtime is separate",
                exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(), modules_verified=sorted(checked),
                original_photo_checks=checks, wrong_size_rejected=True, guide_source_matched=True,
                path_empty=True, python_home_removed=True, clean_windows_tested=False,
                normal_launch_enabled=True, missing_host_environment_rejected=True,
                native_host_tested=False, real_game_tested=False,
                physical_device_used=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.bundle, args.report.parent / "dghub_frozen_images_20261009")
    except Exception:
        report = dict(status="incomplete", scope="frozen provenance and offline HUD")
        args.report.write_text(json.dumps(report), encoding="utf-8")
        raise
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(status=report["status"], modules_verified=len(report["modules_verified"]),
                         photo_checks=len(report["original_photo_checks"]), exe_sha256=report["exe_sha256"])))


if __name__ == "__main__":
    main()
