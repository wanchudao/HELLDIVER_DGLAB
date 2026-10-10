"""Package an internal fixture, or an explicitly verified SDK v1 release ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dghub_plugin.sdk_config import manifest


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _release_evidence(reports, exe_sha256):
    if not isinstance(reports, dict) or set(reports) != {"frozen", "protocol", "native"}:
        raise ValueError("Release requires frozen, protocol and native SDK evidence")
    result = {}
    for kind, location in reports.items():
        path = Path(location).resolve()
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("status") != "ok":
            raise ValueError("Incomplete release evidence: " + kind)
        result[kind] = path, value
    frozen, protocol, native = (result[kind][1] for kind in ("frozen", "protocol", "native"))
    if (frozen.get("exe_sha256") != exe_sha256 or protocol.get("exe_sha256") != exe_sha256
            or frozen.get("normal_launch_enabled") is not True
            or not frozen.get("modules_verified") or frozen.get("guide_source_matched") is not True
            or protocol.get("normal_entry_case", {}).get("status") != "ok"
            or not protocol.get("cases") or any(case.get("status") != "ok" for case in protocol["cases"])
            or native.get("sdk_standard_verified") is not True
            or native.get("physical_device_used") is not False):
        raise ValueError("Release evidence does not match the chosen SDK standard and EXE")
    sources = native.get("implementation_source_sha256")
    expected_sources = {"dghub_plugin/sdk_session.py", "dghub_plugin/sdk_transport.py",
                        "dghub_plugin/preset_catalog.py"}
    if (not isinstance(sources, dict) or set(sources) != expected_sources
            or any(not (ROOT / name).is_file() or sources[name] != _file_hash(ROOT / name)
                   for name in expected_sources)):
        raise ValueError("Native SDK evidence refers to different implementation sources")
    return result


def _attachments(attachments, exe_sha256):
    allowed = {"freeze.json", "startup.json", "regression.xml", "验收记录.md", "native_runtime.json"}
    if not isinstance(attachments, dict) or not set(attachments) <= allowed:
        raise ValueError("Unknown acceptance attachment")
    result = {}
    for name, location in attachments.items():
        path = Path(location).resolve()
        if not path.is_file() or path.is_symlink():
            raise ValueError("Acceptance attachment must be an ordinary file")
        if name == "freeze.json":
            report = json.loads(path.read_text(encoding="utf-8"))
            if (report.get("status") != "ok" or report.get("exe_sha256") != exe_sha256
                    or any(report.get(key) is not True for key in
                           ("true_process_suspension_tested", "owned_child_only",
                            "no_new_positive_during_suspension", "resumed_without_rearming",
                            "baseline_cleared_after_resume", "no_positive_after_resume",
                            "final_both_zero"))
                    or report.get("native_host_tested") is not False
                    or report.get("physical_device_used") is not False):
                raise ValueError("Freeze attachment did not pass the scoped final EXE check")
        elif name == "startup.json":
            report = json.loads(path.read_text(encoding="utf-8"))
            state = report.get("state", {})
            if (report.get("status") != "ok" or report.get("exe_sha256") != exe_sha256
                    or report.get("exit_code") != 0 or report.get("failure") is not None
                    or any(state.get(key) is not True for key in
                           ("panel_visible", "capture_ready", "output_ready", "paused"))
                    or report.get("no_positive_output") is not True
                    or report.get("final_both_zero") is not True
                    or report.get("snapshot_written") is not True
                    or report.get("supplied_hud_type") != "str"
                    or type(report.get("normalized_hud")) is not int
                    or report.get("installed_config_modified") is not False
                    or report.get("native_host_tested") is not False
                    or report.get("physical_device_used") is not False):
                raise ValueError("Startup attachment did not verify actual panel/config initialization")
        elif name == "native_runtime.json":
            report = json.loads(path.read_text(encoding="utf-8"))
            if (report.get("status") != "ok" or report.get("exe_sha256") != exe_sha256
                    or report.get("host_exe_sha256") != "86a6fd69060588631d023ae97bb3cb58370cd1cf898b8612c7c52a43a55301a2"
                    or report.get("threshold_roundtrips") != list(range(101))
                    or report.get("session_snapshot_thresholds") != [75, 30]
                    or any(report.get(key) is not True for key in
                           ("native_route_tested", "actual_plugin_process_started",
                            "pending_kept_old_applied", "reenable_applied_new_threshold",
                            "save_failure_preserved_memory_and_file", "no_positive_output"))
                    or any(report.get(key) is not False for key in
                           ("installed_config_modified", "installed_plugins_modified",
                            "firewall_modified", "global_host_settings_modified", "reset_card_used",
                            "physical_device_used", "non_loopback_network_used"))):
                raise ValueError("Native runtime attachment did not pass isolated import/config/process checks")
        elif name == "regression.xml":
            root = ET.parse(path).getroot()
            suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
            if (not suites or sum(int(suite.get("tests", "0")) for suite in suites) <= 0
                    or any(int(suite.get(key, "0")) != 0 for suite in suites
                           for key in ("failures", "errors", "skipped"))):
                raise ValueError("Regression attachment contains incomplete or failed tests")
        result[name] = path
    return result


def pack(bundle, destination, *, release=False, acceptance_reports=None,
         acceptance_attachments=None):
    bundle, destination = Path(bundle).resolve(), Path(destination).resolve()
    target_root = ROOT / ("release" if release else "build")
    if not destination.is_relative_to(target_root):
        raise ValueError("Explicit release must stay in release" if release else
                         "Internal import fixture must stay in build; not a release")
    if destination.exists():
        raise FileExistsError("Preserve existing import fixtures; choose a new filename")
    exe = bundle / manifest()["entry"]
    if not exe.is_file() or not (bundle / "_internal").is_dir():
        raise ValueError("Candidate bundle is incomplete")
    paths = [exe, *sorted((bundle / "_internal").rglob("*"))]
    if any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
           or not path.resolve().is_relative_to(bundle) for path in paths):
        raise ValueError("No linked/outside dependency in candidate archive")
    if any(path.name.casefold() == "node.exe" for path in paths):
        raise ValueError("Official Node backend must not ship in a DGHub package")
    if any(path.name.casefold() in {"feedback_settings.ini", "hud_settings.ini", "trigger_log.jsonl"}
           or "sessions" in {part.casefold() for part in path.relative_to(bundle).parts}
           for path in paths):
        raise ValueError("User settings and session logs must not ship in the package")
    exe_sha256 = _file_hash(exe)
    evidence = _release_evidence(acceptance_reports, exe_sha256) if release else {}
    attachments = _attachments(acceptance_attachments or {}, exe_sha256) if release else {}
    if not release and acceptance_attachments:
        raise ValueError("Acceptance attachments are only allowed on an explicit release")
    guide_path = bundle / "_internal/docs/dghub_plugin_guide.md"
    if release and (not guide_path.is_file() or guide_path.read_bytes() !=
                    (ROOT / "docs/dghub_plugin_guide.md").read_bytes()):
        raise ValueError("Release guide missing or differs from frozen bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest(), ensure_ascii=False, indent=2))
        if release:
            archive.writestr("使用指南.md", guide_path.read_bytes())
            for kind, (path, _value) in evidence.items():
                archive.writestr(f"acceptance/{kind}.json", path.read_bytes())
            for name, path in attachments.items():
                archive.writestr(f"acceptance/{name}", path.read_bytes())
            archive.writestr("BUILD_MANIFEST.json", json.dumps(dict(
                plugin=manifest(), host_target="DGHub 1.9.6 Windows x64 SDK v1",
                exe_sha256=exe_sha256, user_runtime_dependencies=[],
                dependencies="Bundled Python runtime, vision libraries and WebSocket client in _internal; no Node backend",
                evidence={kind: dict(file=f"acceptance/{kind}.json", sha256=_file_hash(path))
                          for kind, (path, _value) in evidence.items()},
                attachments={name: dict(file=f"acceptance/{name}", sha256=_file_hash(path))
                             for name, path in attachments.items()},
                files=[dict(path=path.relative_to(bundle).as_posix(), size=path.stat().st_size,
                            sha256=_file_hash(path)) for path in paths if path.is_file()]),
                ensure_ascii=False, indent=2))
        else:
            archive.writestr("CANDIDATE_ONLY.txt", "INTERNAL IMPORT CONTRACT FIXTURE. NOT A RELEASE.\n")
        for path in paths:
            if path.is_file():
                archive.write(path, path.relative_to(bundle).as_posix())
    return dict(scope="verified SDK v1 release; live game/device acceptance separate" if release else
                "internal import fixture; not user release", archive=str(destination),
                archive_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                exe_sha256=exe_sha256)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--release", action="store_true", help="显式创建已验收正式包；默认只建内部测试包")
    parser.add_argument("--frozen-report", type=Path)
    parser.add_argument("--protocol-report", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--freeze-report", type=Path)
    parser.add_argument("--startup-report", type=Path)
    parser.add_argument("--native-runtime-report", type=Path)
    parser.add_argument("--regression-report", type=Path)
    parser.add_argument("--acceptance-note", type=Path)
    args = parser.parse_args(argv)
    reports = {"frozen": args.frozen_report, "protocol": args.protocol_report, "native": args.native_report}
    if args.release and any(path is None for path in reports.values()):
        parser.error("--release requires all three acceptance reports")
    print(json.dumps(pack(args.bundle, args.destination, release=args.release,
                          acceptance_reports=reports if args.release else None,
                          acceptance_attachments={name: path for name, path in
                              (("freeze.json", args.freeze_report), ("startup.json", args.startup_report),
                               ("native_runtime.json", args.native_runtime_report),
                               ("regression.xml", args.regression_report),
                               ("验收记录.md", args.acceptance_note)) if path is not None})))


if __name__ == "__main__":
    main()
