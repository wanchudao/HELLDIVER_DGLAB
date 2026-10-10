from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

from dghub_plugin.sdk_config import defaults, manifest
from tools import dghub_package_candidate
from tools.dghub_verify_import_contract import verify
from tools.dghub_software_acceptance import (audit_startup_settling, audit_submissions,
                                           strength_request, wave_request)


def write_executable_fixture(path):
    # A real benign PE file avoids security-product false positives on a text
    # file deliberately named .exe. It is archived, never executed by tests.
    path.write_bytes(Path(sys.executable).read_bytes())


def report_fixture():
    return dict(status="ok", installed_host_modified=False, installed_config_modified=False,
        network_used=False, child_process_started=False, real_device_used=False,
        import_ok=True, write_failure_rollback=True, same_id_upgrade_replaces_plugin_directory=True,
        outside_package_data_survived=True, host_config_survived_upgrade=True,
        spawn_contract_ok=True, traversal_rejected=True,
        hud_options=[dict(value=n, label=f"{n / 100:.2f}") for n in range(75, 126, 5)],
        eleven_config_roundtrips=[dict(scale=n, roundtrip_ok=True) for n in range(75, 126, 5)],
        final_configuration=defaults() | dict(hud_scale_percent=125, injury_pulse_strength_b=23),
        invalid_configuration=defaults() | dict(hud_scale_percent=76), host_saved_invalid_hud=76)


def test_source_validator_consumes_actual_native_report_shape():
    result = verify(report_fixture())
    assert result["status"] == "ok" and result["plugin_rejected_invalid_hud"]
    assert result["current_source_roi_matches_saved_hud"] and result["current_source_b_strength"] == 23
    assert not result["native_gui_tested"] and not result["native_plugin_runtime_tested"]


def test_source_validator_accepts_native_browser_saved_string_hud():
    report = report_fixture()
    report["final_configuration"]["hud_scale_percent"] = "125"
    assert verify(report)["current_source_roi_matches_saved_hud"]


@pytest.mark.parametrize("key,value", [("network_used", True), ("installed_config_modified", True),
    ("spawn_contract_ok", False), ("write_failure_rollback", False), ("traversal_rejected", False)])
def test_weaker_or_expanded_scope_cannot_count_as_import_acceptance(key, value):
    report = report_fixture()
    report[key] = value
    with pytest.raises(ValueError):
        verify(report)


def test_all_eleven_values_are_required_not_eleven_copies_of_one():
    report = report_fixture()
    report["eleven_config_roundtrips"] = [dict(scale=100, roundtrip_ok=True)] * 11
    with pytest.raises(ValueError):
        verify(report)


def test_unknown_persisted_value_must_be_rejected_by_plugin():
    report = report_fixture()
    report["invalid_configuration"]["hud_scale_percent"] = 100
    with pytest.raises(ValueError):
        verify(report)


def test_internal_zip_is_root_manifest_and_never_carries_user_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(dghub_package_candidate, "ROOT", tmp_path)
    bundle = tmp_path / "bundle"
    (bundle / "_internal").mkdir(parents=True)
    write_executable_fixture(bundle / "helldiver_dglab.exe")
    (bundle / "_internal/library.dll").write_bytes(b"owned fake library")
    (bundle / "sessions").mkdir()
    (bundle / "sessions/private.json").write_text("must-not-ship")
    destination = tmp_path / "build/internal.zip"
    result = dghub_package_candidate.pack(bundle, destination)
    assert "not user release" in result["scope"]
    with zipfile.ZipFile(destination) as zf:
        assert json.loads(zf.read("manifest.json")) == manifest()
        assert set(zf.namelist()) == {"manifest.json", "CANDIDATE_ONLY.txt", "helldiver_dglab.exe", "_internal/library.dll"}
    with pytest.raises(FileExistsError):
        dghub_package_candidate.pack(bundle, destination)


def test_import_fixture_cannot_be_accidentally_created_as_a_release(tmp_path, monkeypatch):
    monkeypatch.setattr(dghub_package_candidate, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="not a release"):
        dghub_package_candidate.pack(tmp_path, tmp_path / "release/pretend_complete.zip")


@pytest.mark.parametrize("extra", ["runtime/node.exe", "sessions/private.json", "hud_settings.ini"])
def test_package_rejects_embedded_official_backend_or_user_data(tmp_path, monkeypatch, extra):
    monkeypatch.setattr(dghub_package_candidate, "ROOT", tmp_path)
    bundle = tmp_path / "bundle"
    location = bundle / "_internal" / extra
    location.parent.mkdir(parents=True)
    location.write_bytes(b"must not ship")
    write_executable_fixture(bundle / "helldiver_dglab.exe")
    with pytest.raises(ValueError):
        dghub_package_candidate.pack(bundle, tmp_path / "build/rejected.zip")


def release_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(dghub_package_candidate, "ROOT", tmp_path)
    bundle = tmp_path / "build/bundle"
    (bundle / "_internal/docs").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    write_executable_fixture(bundle / "helldiver_dglab.exe")
    guide = "SDK v1 guide; no physical-device acceptance claim"
    (bundle / "_internal/docs/dghub_plugin_guide.md").write_text(guide)
    (tmp_path / "docs/dghub_plugin_guide.md").write_text(guide)
    digest = hashlib.sha256((bundle / "helldiver_dglab.exe").read_bytes()).hexdigest()
    source_hashes = {}
    for name in ("sdk_session.py", "sdk_transport.py", "preset_catalog.py"):
        source = tmp_path / "dghub_plugin" / name
        source.parent.mkdir(exist_ok=True)
        source.write_text("# owned implementation fixture\n", encoding="utf-8")
        source_hashes[f"dghub_plugin/{name}"] = hashlib.sha256(source.read_bytes()).hexdigest()
    reports = dict(frozen=dict(status="ok", exe_sha256=digest, normal_launch_enabled=True,
        guide_source_matched=True, modules_verified=["sdk_session"]),
        protocol=dict(status="ok", exe_sha256=digest, normal_entry_case=dict(status="ok"),
                      cases=[dict(status="ok")]),
        native=dict(status="ok", sdk_standard_verified=True, physical_device_used=False,
                    implementation_source_sha256=source_hashes))
    paths = {}
    for kind, value in reports.items():
        path = tmp_path / f"{kind}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[kind] = path
    return bundle, reports, paths


def test_release_requires_explicit_successful_matched_evidence(tmp_path, monkeypatch):
    bundle, _reports, paths = release_fixture(tmp_path, monkeypatch)
    target = tmp_path / "release/HELLDIVER_DGHub_1.0.0_HUD_20261009.zip"
    with pytest.raises(ValueError, match="requires frozen"):
        dghub_package_candidate.pack(bundle, target, release=True)
    assert not target.exists()
    result = dghub_package_candidate.pack(bundle, target, release=True, acceptance_reports=paths)
    assert "verified SDK v1 release" in result["scope"]
    with zipfile.ZipFile(target) as archive:
        assert "CANDIDATE_ONLY.txt" not in archive.namelist()
        assert {"manifest.json", "使用指南.md", "BUILD_MANIFEST.json", "acceptance/frozen.json",
                "acceptance/protocol.json", "acceptance/native.json"} <= set(archive.namelist())
        build = json.loads(archive.read("BUILD_MANIFEST.json"))
        assert build["user_runtime_dependencies"] == []
        assert build["exe_sha256"] == result["exe_sha256"]


@pytest.mark.parametrize("kind,key,value", [
    ("frozen", "exe_sha256", "wrong-exe"),
    ("protocol", "status", "incomplete"),
    ("native", "sdk_standard_verified", False),
    ("native", "physical_device_used", True),
])
def test_release_rejects_incomplete_or_different_evidence(tmp_path, monkeypatch, kind, key, value):
    bundle, reports, paths = release_fixture(tmp_path, monkeypatch)
    reports[kind][key] = value
    paths[kind].write_text(json.dumps(reports[kind]), encoding="utf-8")
    target = tmp_path / "release/rejected.zip"
    with pytest.raises(ValueError):
        dghub_package_candidate.pack(bundle, target, release=True, acceptance_reports=paths)
    assert not target.exists()


def test_release_rejects_stale_native_implementation_evidence(tmp_path, monkeypatch):
    bundle, _reports, paths = release_fixture(tmp_path, monkeypatch)
    (tmp_path / "dghub_plugin/sdk_session.py").write_text("# revised implementation\n")
    target = tmp_path / "release/stale.zip"
    with pytest.raises(ValueError, match="different implementation"):
        dghub_package_candidate.pack(bundle, target, release=True, acceptance_reports=paths)
    assert not target.exists()


def test_release_ships_scoped_freeze_and_regression_records(tmp_path, monkeypatch):
    bundle, reports, paths = release_fixture(tmp_path, monkeypatch)
    freeze = dict(status="ok", exe_sha256=reports["frozen"]["exe_sha256"],
                  native_host_tested=False, physical_device_used=False)
    for key in ("true_process_suspension_tested", "owned_child_only",
                "no_new_positive_during_suspension", "resumed_without_rearming",
                "baseline_cleared_after_resume", "no_positive_after_resume", "final_both_zero"):
        freeze[key] = True
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps(freeze))
    regression = tmp_path / "regression.xml"
    regression.write_text('<testsuites><testsuite tests="8" failures="0" errors="0" skipped="0"/></testsuites>')
    note = tmp_path / "note.md"
    note.write_text("Owned process freeze; not physical device acceptance")
    target = tmp_path / "release/attachments.zip"
    dghub_package_candidate.pack(bundle, target, release=True, acceptance_reports=paths,
        acceptance_attachments={"freeze.json": freeze_path, "regression.xml": regression, "验收记录.md": note})
    with zipfile.ZipFile(target) as archive:
        build = json.loads(archive.read("BUILD_MANIFEST.json"))
        assert set(build["attachments"]) == {"freeze.json", "regression.xml", "验收记录.md"}
        assert json.loads(archive.read("acceptance/freeze.json"))["native_host_tested"] is False
    freeze["no_positive_after_resume"] = False
    freeze_path.write_text(json.dumps(freeze))
    with pytest.raises(ValueError, match="Freeze attachment"):
        dghub_package_candidate.pack(bundle, tmp_path / "release/failed-freeze.zip", release=True,
            acceptance_reports=paths, acceptance_attachments={"freeze.json": freeze_path})


@pytest.mark.parametrize("visible", [True, False])
def test_release_startup_evidence_requires_real_visible_panel_and_string_config(tmp_path, monkeypatch, visible):
    bundle, reports, paths = release_fixture(tmp_path, monkeypatch)
    startup = dict(status="ok", exe_sha256=reports["frozen"]["exe_sha256"],
                   exit_code=0, failure=None, supplied_hud_type="str", normalized_hud=100,
                   state=dict(panel_visible=visible, capture_ready=True, output_ready=True, paused=True),
                   no_positive_output=True, final_both_zero=True, snapshot_written=True,
                   installed_config_modified=False, native_host_tested=False, physical_device_used=False)
    source = tmp_path / "startup.json"
    source.write_text(json.dumps(startup))
    if not visible:
        with pytest.raises(ValueError, match="Startup attachment"):
            dghub_package_candidate.pack(bundle, tmp_path / "release/startup.zip", release=True,
                acceptance_reports=paths, acceptance_attachments={"startup.json": source})
    else:
        dghub_package_candidate.pack(bundle, tmp_path / "release/startup.zip", release=True,
            acceptance_reports=paths, acceptance_attachments={"startup.json": source})
        with zipfile.ZipFile(tmp_path / "release/startup.zip") as archive:
            assert json.loads(archive.read("acceptance/startup.json"))["state"]["panel_visible"]


@pytest.mark.parametrize("name", ["../outside.md", "native.json", "unexpected.md"])
def test_release_attachment_names_are_explicit(tmp_path, monkeypatch, name):
    bundle, _reports, paths = release_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Unknown acceptance"):
        dghub_package_candidate.pack(bundle, tmp_path / "release/rejected.zip", release=True,
            acceptance_reports=paths, acceptance_attachments={name: paths["native"]})


def test_failed_regression_cannot_be_attached_as_release_acceptance(tmp_path, monkeypatch):
    bundle, _reports, paths = release_fixture(tmp_path, monkeypatch)
    regression = tmp_path / "failed.xml"
    regression.write_text('<testsuite tests="8" failures="1"/>')
    with pytest.raises(ValueError, match="Regression attachment"):
        dghub_package_candidate.pack(bundle, tmp_path / "release/failed.zip", release=True,
            acceptance_reports=paths, acceptance_attachments={"regression.xml": regression})


def packets_fixture():
    return [dict(op="trigger", action="strength", strength_mode="rollback", duration_s=.30,
                 channel=channel, delta_pct=value) for channel, value in (("a", 20), ("b", 7))] + [
        dict(op="pulse", channel="both", preset="呼吸"),
        dict(op="trigger", action="waveform", channel="both", preset="连击", duration_s=.4),
        dict(op="trigger", action="waveform", channel="both", preset="software-random-cycle", duration_s=1),
        dict(op="set_strength", channel="a", pct=0), dict(op="set_strength", channel="b", pct=0)]


def test_software_peer_audits_short_leases_full_cycles_and_finite_windows():
    packets = packets_fixture()
    result = audit_submissions(packets, mode="both", cap_a=20, cap_b=7)
    assert result["rollback_leases_correct"] and result["final_both_zero"]
    assert result["enabled_channels_correct"] and result["independent_caps_correct"]
    assert result["full_cycle_requests"] == result["finite_wave_windows"] == result["random_wave_windows"] == 1
    assert result["wave_requests_correct"] and result["json_pong_absent"]
    assert strength_request(packets[0]) == ("a", 20) and wave_request(packets[0]) is None
    assert wave_request(packets[2]) == "呼吸" and strength_request(packets[2]) is None


def test_monitor_only_peer_can_confirm_zeros_without_claiming_positive_output():
    packets = [dict(op="set_strength", channel=channel, pct=0) for channel in ("a", "b")]
    result = audit_submissions(packets)
    assert result["final_both_zero"] and result["positive_requests"] == 0
    assert not result["rollback_leases_correct"] and not result["enabled_channels_correct"]


def test_old_nonzero_absolute_strength_cannot_pass_new_lease_acceptance():
    packets = packets_fixture()
    packets[0] = dict(op="set_strength", channel="a", pct=20)
    assert not audit_submissions(packets)["rollback_leases_correct"]


@pytest.mark.parametrize("mutation,key", [
    (dict(duration_s=.25), "rollback_leases_correct"),
    (dict(strength_mode="permanent"), "rollback_leases_correct"),
    (dict(delta_pct=101), "independent_caps_correct"),
])
def test_submission_audit_rejects_weaker_strength_policy(mutation, key):
    packets = packets_fixture()
    packets[0].update(mutation)
    assert not audit_submissions(packets)[key]


def test_submission_audit_rejects_json_pong_and_overlong_random_window():
    packets = packets_fixture()
    packets[4]["duration_s"] = 1.01
    packets.append(dict(op="pong", t=5))
    result = audit_submissions(packets)
    assert not result["wave_requests_correct"] and not result["json_pong_absent"]


def test_wave_cadence_audit_catches_strength_refresh_restarting_full_template():
    packets = [packets_fixture()[0], dict(op="pulse", channel="a", preset="呼吸"),
               packets_fixture()[0], dict(op="pulse", channel="a", preset="呼吸")]
    result = audit_submissions(packets, mode="a", receipt_times=[0, .01, .10, .11])
    assert result["lease_renewal_observed"] and not result["wave_cadence_correct"]


def test_wave_cadence_audit_allows_actual_cycle_end_or_new_channel_sync():
    a, b = packets_fixture()[:2]
    packets = [a, dict(op="pulse", channel="a", preset="呼吸"), b,
               dict(op="pulse", channel="both", preset="呼吸"),
               dict(op="pulse", channel="both", preset="呼吸")]
    assert audit_submissions(packets, receipt_times=[0, .01, .20, .21, 1.31])["wave_cadence_correct"]


@pytest.mark.parametrize("ready,expected", [(10.48, False), (10.49, True), (10.8, True), (None, False)])
def test_startup_settling_requires_measured_delay_not_just_ready_flag(ready, expected):
    audit = audit_startup_settling(dict(hello_ack_at=10, first_output_ready_at=ready))
    assert audit["startup_settling_verified"] is expected
