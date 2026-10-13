"""Publish the verified DGHub 1.0.5 ZIP with unchanged runtime bytes."""
import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import zipfile

from prepare_public_release import redacted
from publication_audit import locations

ROOT = Path(__file__).resolve().parents[1]
TAG = 'dghub-v1.0.5'
VERSIONS = {'numpy': '2.5.2', 'opencv-python': '5.0.0.93', 'Pillow': '12.3.0',
            'mss': '10.2.0', 'websockets': '17.1', 'PyInstaller': '6.22.2'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def license_assets():
    require(sys.version_info[:3] == (3, 14, 7), 'Use the verified Python build environment')
    result = {'LICENSES/Python/LICENSE.txt': (Path(sys.base_prefix) / 'LICENSE.txt').read_bytes()}
    for package, version in VERSIONS.items():
        dist = importlib.metadata.distribution(package)
        require(dist.version == version, 'Build dependency changed: ' + package)
        for entry in dist.files or []:
            name = str(entry)
            if 'license' in name.lower() or 'copying' in name.lower():
                path = dist.locate_file(entry)
                if path.is_file():
                    result['LICENSES/' + package + '/' + name.replace('..', '_')] = path.read_bytes()
    # Reuse the already preserved Tcl/Tk notices from the same tested environment.
    previous = ROOT / 'release/github_main_20261010/HELLDIVER_Main_Windows_x64_main-v1.0.0-beta.1.zip'
    with zipfile.ZipFile(previous) as archive:
        for package in ('tcl', 'tk'):
            result['LICENSES/' + package + '/license.terms'] = archive.read(
                'HELLDIVER_Main/LICENSES/' + package + '/license.terms')
    return result


def prepare(source, destination):
    source, destination = source.resolve(), destination.resolve()
    require(source.is_relative_to(ROOT / 'release'), 'Source must be a project release artifact')
    require(destination.is_relative_to(ROOT / 'release'), 'Output must be a new release artifact')
    require(not destination.exists() and destination != source, 'Preserve existing artifacts')
    original_hash = digest(source.read_bytes())
    with zipfile.ZipFile(source) as archive:
        require(archive.testzip() is None, 'Original ZIP CRC failed')
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Duplicate ZIP paths')
        require(all(not Path(n).is_absolute() and '..' not in Path(n).parts and '\\' not in n
                    and ':' not in n for n in names), 'Unsafe ZIP path')
        manifest = json.loads(archive.read('manifest.json'))
        require((manifest['id'], manifest['version'], manifest['sdk'], manifest['entry']) ==
                ('helldiver_dglab', '1.0.5', '1', 'helldiver_dglab.exe'), 'Wrong plugin edition/version')
        info = json.loads(archive.read('BUILD_MANIFEST.json'))
        runtime = {n for n in names if n.startswith('_internal/') or n == manifest['entry']}
        require(runtime and info['plugin'] == manifest, 'Incomplete package manifest')
        exe_hash = digest(archive.read(manifest['entry']))
        require(exe_hash == info['exe_sha256'], 'EXE differs from verified build')
        for item in info['files']:
            data = archive.read(item['path'])
            require(len(data) == item['size'] and digest(data) == item['sha256'],
                    'Runtime manifest mismatch: ' + item['path'])
        for item in list(info['evidence'].values()) + list(info['attachments'].values()):
            require(digest(archive.read(item['file'])) == item['sha256'], 'Evidence hash mismatch')
        for kind in ('frozen', 'protocol'):
            report = json.loads(archive.read(info['evidence'][kind]['file']))
            require(report['status'] == 'ok' and report['exe_sha256'] == exe_hash,
                    'Unverified final EXE report: ' + kind)
        native = json.loads(archive.read(info['evidence']['native']['file']))
        require(native['status'] == 'ok' and native['sdk_standard_verified'] is True,
                'Native SDK evidence is incomplete')
        for name, expected in native['implementation_source_sha256'].items():
            require(digest((ROOT / name).read_bytes()) == expected, 'SDK source changed: ' + name)
        guide = archive.read('_internal/docs/dghub_plugin_guide.md')
        require(guide == (ROOT / 'docs/dghub_plugin_guide.md').read_bytes(), 'Guide differs from verified EXE')
        require(not any(Path(n).name.casefold() in {'node.exe', 'feedback_settings.ini', 'hud_settings.ini',
                    'ui_settings.ini', 'config.ini', 'config.json', 'trigger_log.jsonl'}
                    or 'sessions' in Path(n).parts for n in names), 'Unexpected user data or backend')

        replacements = {}
        for name in names:
            if name in runtime or name == 'manifest.json' or name == 'BUILD_MANIFEST.json':
                continue
            if name.endswith(('.json', '.md', '.xml', '.txt')):
                original = archive.read(name)
                if name.endswith('.json'):
                    value = json.loads(original)
                    clean = redacted(value)
                    content = json.dumps(clean, ensure_ascii=False, indent=2).encode('utf-8') if clean != value else original
                else:
                    content = redacted(original.decode('utf-8')).encode('utf-8')
                if content != original:
                    replacements[name] = content
        info = redacted(info)
        for item in list(info['evidence'].values()) + list(info['attachments'].values()):
            item['sha256'] = digest(replacements.get(item['file'], archive.read(item['file'])))
        publication = dict(tag=TAG, published_date='2026-10-13',
                           original_zip_sha256=original_hash, exe_sha256=exe_hash,
                           runtime_files_preserved=len(runtime), manifest_unchanged=True,
                           runtime_bytes_unchanged=True, sdk_source_hashes_matched=True,
                           metadata_files_redacted=sorted(replacements),
                           function_tests_reused_from='2026-10-10 verified artifact',
                           real_game_tested=False, physical_device_tested=False,
                           marketplace_submission_completed=False)
        info['publication'] = publication
        replacements['BUILD_MANIFEST.json'] = json.dumps(info, ensure_ascii=False, indent=2).encode('utf-8')
        additions = license_assets()
        license_count = len(additions)
        additions.update({
            'THIRD_PARTY_NOTICES.md': (ROOT / 'docs/third_party_notices.md').read_bytes(),
            'RELEASE_NOTES_ZH.md': (ROOT / 'docs/releases/dghub-v1.0.5.md').read_bytes(),
            'USER_GUIDE_ZH.md': guide,
            'waveform_sources.md': (ROOT / 'docs/waveform_sources.md').read_bytes(),
            'PUBLICATION_INFO.json': json.dumps(publication, ensure_ascii=False, indent=2).encode('utf-8'),
        })
        require(not set(additions).intersection(names), 'Publication additions collide with original files')
        findings = []
        for name in names:
            if name.endswith(('.json', '.md', '.xml', '.ini', '.txt')):
                findings.extend(locations(replacements.get(name, archive.read(name)), name))
        for name, content in additions.items():
            findings.extend(locations(content, name))
        require(not findings, 'Credential-pattern review required at: ' + json.dumps(findings))

        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as result:
            for entry in archive.infolist():
                result.writestr(copy.copy(entry), replacements.get(entry.filename, archive.read(entry.filename)))
            for name, content in additions.items():
                result.writestr(name, content)
        with zipfile.ZipFile(destination) as result:
            require(result.testzip() is None, 'Published ZIP CRC failed')
            require(result.read('manifest.json') == archive.read('manifest.json'), 'Plugin manifest changed')
            require(all(result.read(name) == archive.read(name) for name in runtime), 'Runtime bytes changed')
            rebuilt = json.loads(result.read('BUILD_MANIFEST.json'))
            for item in list(rebuilt['evidence'].values()) + list(rebuilt['attachments'].values()):
                require(digest(result.read(item['file'])) == item['sha256'], 'Published report hash mismatch')
        require(digest(source.read_bytes()) == original_hash, 'Original ZIP changed')
    checksum = digest(destination.read_bytes())
    report = dict(publication, status='ok', zip=destination.name, sha256=checksum,
                  bytes=destination.stat().st_size, original_zip_preserved=True,
                  extra_license_files=license_count)
    (destination.parent / 'SHA256SUMS.txt').write_text(checksum + '  ' + destination.name + '\n', encoding='ascii')
    (destination.parent / 'PUBLICATION_INFO.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in ('RELEASE_NOTES_ZH.md', 'USER_GUIDE_ZH.md', 'THIRD_PARTY_NOTICES.md'):
        (destination.parent / name).write_bytes(additions[name])
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination), ensure_ascii=False, indent=2))
