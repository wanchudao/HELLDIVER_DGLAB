"""Create a publication archive without changing the tested EXE or Node runtime."""
import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXE = 'HELLDIVER_Main/HELLDIVER_Main.exe'
NODE = 'HELLDIVER_Main/_internal/runtime/node.EXE'


def redacted(value):
    if isinstance(value, dict):
        return {key: redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redacted(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)E:[\\/]HELLDIVER_DGLAB', '<project>', value)
        value = re.sub(r'(?i)C:[\\/]Users[\\/][^\\/]+', '<user>', value)
    return value


def license_assets(node_version):
    files = {'Python/LICENSE.txt': (Path(sys.base_prefix)/'LICENSE.txt').read_bytes()}
    url = 'https://raw.githubusercontent.com/nodejs/node/'+node_version+'/LICENSE'
    with urllib.request.urlopen(url, timeout=30) as response:
        files['Node/LICENSE.txt'] = response.read()
    if b'Node.js' not in files['Node/LICENSE.txt']:
        raise RuntimeError('Node license response was not the expected document')
    for package in ('Pillow', 'opencv-python', 'mss', 'qrcode', 'websocket-client', 'PyInstaller'):
        distribution = importlib.metadata.distribution(package)
        for entry in distribution.files or []:
            name = str(entry)
            if 'license' in name.lower() or 'copying' in name.lower():
                path = distribution.locate_file(entry)
                if path.is_file():
                    files[package+'/'+name.replace('..', '_')] = path.read_bytes()
    for package in ('tcl', 'tk'):
        url = 'https://raw.githubusercontent.com/tcltk/'+package+'/main/license.terms'
        with urllib.request.urlopen(url, timeout=30) as response:
            files[package+'/license.terms'] = response.read()
    return files


def prepare(source, destination):
    with zipfile.ZipFile(source) as archive:
        assert archive.testzip() is None
        info = json.loads(archive.read('HELLDIVER_Main/BUILD_INFO.json'))
        files = license_assets(info['dependencies']['node'])
        info = redacted(info)
        info['publication'] = dict(tag='main-v1.0.0-beta.1', metadata_redacted=True,
                                  original_zip_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                                  executable_changed=False, node_changed=False,
                                  user_report='2026-10-10: 新版连接和使用一切正常；没有分项或实体设备数据。')
        extras = {
            'HELLDIVER_Main/THIRD_PARTY_NOTICES.md': (ROOT/'docs/third_party_notices.md').read_bytes(),
            'HELLDIVER_Main/发布说明.md': (ROOT/'docs/releases/main-v1.0.0-beta.1.md').read_bytes(),
        }
        extras.update({'HELLDIVER_Main/LICENSES/'+name: content for name, content in files.items()})
        metadata = 'HELLDIVER_Main/BUILD_INFO.json'
        extras[metadata] = json.dumps(info, ensure_ascii=False, indent=2).encode('utf-8')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as result:
            for entry in archive.infolist():
                if entry.filename not in extras:
                    result.writestr(copy.copy(entry), archive.read(entry.filename))
            for name, content in extras.items():
                result.writestr(name, content)
        with zipfile.ZipFile(destination) as result:
            assert result.testzip() is None
            assert result.read(EXE) == archive.read(EXE)
            assert result.read(NODE) == archive.read(NODE)
            names = result.namelist()
            assert not any(Path(name).name in ('feedback_settings.ini','hud_settings.ini','ui_settings.ini') for name in names)
            for name in names:
                if name.endswith('.json') and 'node_modules' not in name:
                    assert b'E:\\HELLDIVER_DGLAB' not in result.read(name)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum_file = destination.parent/'SHA256SUMS.txt'
    checksum_file.write_text(checksum+'  '+destination.name+'\n', encoding='ascii')
    report = dict(status='ok', zip=destination.name, sha256=checksum, bytes=destination.stat().st_size,
                  original_zip_preserved=True, unchanged_exe_sha256=info['exe_sha256'],
                  extra_license_files=len(files), metadata_redacted=True)
    (destination.parent/'PUBLICATION_INFO.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination), indent=2))
