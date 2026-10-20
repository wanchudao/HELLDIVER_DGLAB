"""Add offline licenses and release notes, preserving every tested runtime byte.

This creates local artifacts only: no network, installation or GitHub operation.
The historical publication tools remain specific to their historical versions.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_public_release import redacted
from tools.publication_audit import locations


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_names(names):
    require(len(names) == len(set(names)), "Duplicate ZIP paths")
    for name in names:
        require(bool(name) and not PurePosixPath(name).is_absolute()
                and ".." not in PurePosixPath(name).parts and "\\" not in name
                and ":" not in name, "Unsafe ZIP path")


def main_death_evidence(info):
    report = info.get("checks", {}).get("death", {})
    require(info.get("edition") == "main-official-backend"
            and report.get("status") == "ok"
            and report.get("full_preferences_trigger_protocol_chain") is True
            and report.get("final_both_zero") is True and report.get("no_late_output") is True
            and {r.get("mode") for r in report.get("checks", [])}
                == {"pulse", "until_revive", "until_reset"}, "Incomplete main death evidence")


def plugin_death_evidence(archive, info):
    plugin = json.loads(archive.read("manifest.json"))
    require((plugin.get("id"), plugin.get("sdk"), plugin.get("entry"))
            == ("helldiver_dglab", "1", "helldiver_dglab.exe")
            and plugin.get("version") in {"1.0.6","1.0.7"}, "Wrong plugin version")
    require(info.get("plugin") == plugin, "Manifest mismatch")
    cases = json.loads(archive.read(info["evidence"]["protocol"]["file"]))["cases"]
    by_name = {r.get("scenario"): r for r in cases}
    for name in ("death_until_revive", "death_until_reset"):
        require(by_name.get(name, {}).get("status") == "ok"
                and by_name[name].get("death_chain_verified") is True, "Missing sustained death protocol test")
    for name in ("freeze.json", "startup.json", "native_runtime.json", "live_panel.json", "regression.xml", "验收记录.md"):
        require(name in info["attachments"], "Missing final attachment: " + name)
    native = json.loads(archive.read(info["attachments"]["native_runtime.json"]["file"]))
    require(native.get("death_mode_roundtrips") == ["pulse", "until_revive", "until_reset"]
            and native.get("live_mode_pending_and_reenable_verified") is True,
            "Missing native death/live configuration check")
    if plugin['version']=='1.0.7':
        require('resolution.json' in info['attachments'], 'Missing resolution evidence')
        high=[row for row in cases if row.get('resolution') in ([2560,1440],[3840,2160])]
        require({tuple(row['resolution']) for row in high}=={(2560,1440),(3840,2160)}
                and all(row.get('status')=='ok' and row.get('session_record_verified') is True for row in high),
                'Missing high-resolution final EXE protocol evidence')
        require(native.get('experimental_resolution_pending_and_reenable_verified') is True,
                'Missing native experimental setting application evidence')


def licenses(edition, info):
    require(sys.version_info[:3] == (3, 14, 7), "Use the verified build interpreter")
    result = {"Python/LICENSE.txt": (Path(sys.base_prefix) / "LICENSE.txt").read_bytes()}
    packages = ("numpy", "opencv-python", "Pillow", "mss", "PyInstaller")
    packages += ("websockets",) if edition == "dghub" else ("qrcode", "websocket-client")
    for package in packages:
        dist = importlib.metadata.distribution(package)
        if edition == "main" and package in info["dependencies"]:
            require(dist.version == info["dependencies"][package], "Dependency version mismatch")
        for entry in dist.files or []:
            name = str(entry)
            if "license" in name.lower() or "copying" in name.lower():
                path = dist.locate_file(entry)
                if path.is_file():
                    result[package + "/" + name.replace("..", "_")] = path.read_bytes()
    # These notices were preserved with the already verified runtime. No download.
    old = ROOT / "release/github_main_20261010/HELLDIVER_Main_Windows_x64_main-v1.0.0-beta.1.zip"
    with zipfile.ZipFile(old) as archive:
        for package in ("tcl", "tk"):
            result[package + "/license.terms"] = archive.read("HELLDIVER_Main/LICENSES/" + package + "/license.terms")
        if edition == "main":
            old_info = json.loads(archive.read("HELLDIVER_Main/BUILD_INFO.json"))
            require(old_info["dependencies"]["node"] == info["dependencies"]["node"], "Node license version mismatch")
            result["Node/LICENSE.txt"] = archive.read("HELLDIVER_Main/LICENSES/Node/LICENSE.txt")
    return result


def finalize(source, destination, edition, *, date="2026-10-14", notes_document=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    require(source.is_relative_to(ROOT / "release") and destination.is_relative_to(ROOT / "release"),
            "Archives must stay in the project release directory")
    require(source != destination and not destination.exists(), "Preserve existing archives")
    original_hash = digest(source.read_bytes())
    prefix = "HELLDIVER_Main/" if edition == "main" else ""
    info_name = prefix + ("BUILD_INFO.json" if edition == "main" else "BUILD_MANIFEST.json")
    with zipfile.ZipFile(source) as archive:
        require(archive.testzip() is None, "Source CRC failed")
        names = archive.namelist()
        safe_names(names)
        info = json.loads(archive.read(info_name))
        main_death_evidence(info) if edition == "main" else plugin_death_evidence(archive, info)
        exe_name = prefix + ("HELLDIVER_Main.exe" if edition == "main" else "helldiver_dglab.exe")
        runtime = {n for n in names if n.startswith(prefix + "_internal/") or n == exe_name}
        require(digest(archive.read(exe_name)) == info["exe_sha256"], "EXE mismatch")
        if edition == "dghub":
            require({item["path"] for item in info["files"]} == runtime, "Incomplete runtime manifest")
            for item in info["files"]:
                data = archive.read(item["path"])
                require(len(data) == item["size"] and digest(data) == item["sha256"], "Runtime hash mismatch")
        require(not any(PurePosixPath(n).name in {"feedback_settings.ini", "hud_settings.ini", "ui_settings.ini", "trigger_log.jsonl"}
                        or "sessions" in PurePosixPath(n).parts for n in names), "User data in archive")
        replacements = {}
        for name in names:
            if name in runtime or name == info_name or name == "manifest.json":
                continue
            if name.endswith((".json", ".xml", ".md", ".txt")):
                raw = archive.read(name)
                if name.endswith(".json"):
                    value = json.loads(raw)
                    clean = redacted(value)
                    data = json.dumps(clean, ensure_ascii=False, indent=2).encode("utf-8") if clean != value else raw
                else:
                    data = redacted(raw.decode("utf-8")).encode("utf-8")
                if data != raw:
                    replacements[name] = data
        info = redacted(info)
        if edition == "dghub":
            for item in list(info["evidence"].values()) + list(info["attachments"].values()):
                item["sha256"] = digest(replacements.get(item["file"], archive.read(item["file"])))
        additions = {prefix + "LICENSES/" + name: data for name, data in licenses(edition, info).items()}
        additions[prefix + "THIRD_PARTY_NOTICES.md"] = (ROOT / "docs/third_party_notices.md").read_bytes()
        additions[prefix + "新版测试与回退.md"] = (Path(notes_document) if notes_document else ROOT / "docs/death_live_testing_20261014.md").read_bytes()
        require(not set(additions).intersection(names), "Added files collide with source")
        info["local_delivery"] = dict(date=date, version=info.get('version','v1.0.0 Beta 2') if edition == "main" else info['plugin']['version'],
            source_zip_sha256=original_hash, runtime_bytes_unchanged=True,
            installed_version_modified=False, github_uploaded=False, reset_card_used=False,
            licenses={name: digest(data) for name, data in additions.items() if "/LICENSES/" in "/" + name},
            real_game_tested=False, phone_app_tested=False, physical_device_tested=False)
        replacements[info_name] = json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8")
        findings = []
        for name in names:
            if name not in runtime and name.endswith((".json", ".xml", ".md", ".txt")):
                findings.extend(locations(replacements.get(name, archive.read(name)), name))
        for name, data in additions.items():
            findings.extend(locations(data, name))
        require(not findings, "Review credential patterns before delivery: " + json.dumps(findings))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            for item in archive.infolist():
                output.writestr(copy.copy(item), replacements.get(item.filename, archive.read(item.filename)))
            for name, data in additions.items():
                output.writestr(name, data)
        with zipfile.ZipFile(destination) as output:
            require(output.testzip() is None, "Final CRC failed")
            require(all(output.read(name) == archive.read(name) for name in runtime), "Tested runtime bytes changed")
            final_info = json.loads(output.read(info_name))
            if edition == "dghub":
                require(output.read("manifest.json") == archive.read("manifest.json"), "Plugin manifest changed")
                for item in list(final_info["evidence"].values()) + list(final_info["attachments"].values()):
                    require(digest(output.read(item["file"])) == item["sha256"], "Final evidence hash mismatch")
        require(digest(source.read_bytes()) == original_hash, "Original archive changed")
    report = dict(status="ok", edition=edition, zip=str(destination), zip_sha256=digest(destination.read_bytes()),
        bytes=destination.stat().st_size, runtime_files_preserved=len(runtime), exe_sha256=info["exe_sha256"],
        runtime_bytes_unchanged=True, original_zip_preserved=True, github_uploaded=False)
    report_path = destination.with_suffix(".validation.json")
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--edition", choices=("main", "dghub"), required=True)
    parser.add_argument("--date", default="2026-10-14")
    parser.add_argument("--notes-document", type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.source, args.destination, args.edition,date=args.date,notes_document=args.notes_document), ensure_ascii=False, indent=2))
