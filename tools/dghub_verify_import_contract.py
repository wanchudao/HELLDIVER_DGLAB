"""Verify actual isolated host output against the current plugin configuration."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dghub_plugin.sdk_config import core_config, manifest, validate
from src.config_loader import load_config
from src.hud_profiles import get_profile


def verify(report):
    if (report.get("status") != "ok" or any(report.get(key) is not False for key in
            ("installed_host_modified", "installed_config_modified", "network_used", "child_process_started", "real_device_used"))):
        raise ValueError("Actual host import contract probe did not pass")
    options = next(field["options"] for section in manifest()["config_schema"]
                   for field in section["fields"] if field["key"] == "hud_scale_percent")
    if (report["hud_options"] != options or [row["scale"] for row in report["eleven_config_roundtrips"]]
            != list(range(75, 126, 5))):
        raise ValueError("Native HUD options/roundtrip evidence mismatch")
    if not all(row["roundtrip_ok"] for row in report["eleven_config_roundtrips"]):
        raise ValueError("Native settings did not roundtrip")
    if not all(report.get(key) is True for key in ("import_ok", "write_failure_rollback",
            "same_id_upgrade_replaces_plugin_directory", "outside_package_data_survived",
            "host_config_survived_upgrade", "spawn_contract_ok", "traversal_rejected")):
        raise ValueError("Native import/upgrade/rollback evidence incomplete")
    saved = validate(report["final_configuration"])
    mapped = core_config(load_config(ROOT / "config.example.ini"), saved)
    if mapped["ROI"] != get_profile(125)["roi"] or saved["injury_pulse_strength_b"] != 23:
        raise ValueError("Actual native configuration did not enter current shared mapping")
    try:
        validate(report["invalid_configuration"])
    except ValueError:
        invalid_rejected = True
    else:
        raise ValueError("Plugin accepted host-persisted invalid HUD")
    return dict(status="ok", scope="actual importer/config methods plus current source validation; not native app runtime",
                native_schema_accepted=True, eleven_native_roundtrips=True,
                host_saved_invalid_hud=report["host_saved_invalid_hud"], plugin_rejected_invalid_hud=invalid_rejected,
                current_source_roi_matches_saved_hud=True, current_source_b_strength=23,
                actual_host_directory_replaced=report["same_id_upgrade_replaces_plugin_directory"],
                host_config_retained=report["host_config_survived_upgrade"],
                native_gui_tested=False, native_plugin_runtime_tested=False,
                physical_device_used=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify(json.loads(args.input.read_text(encoding="utf-8")))
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
