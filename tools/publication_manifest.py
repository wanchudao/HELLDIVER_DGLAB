"""Explicit source allowlist; never include local settings, logs or binaries."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    files = set()
    for name in ('README.md', '.gitignore', '.gitattributes', 'config.example.ini',
                 'requirements.txt', 'requirements-desktop.txt', 'pytest.ini', 'start.bat',
                 'PLUGIN_DEVELOPMENT.md'):
        if (ROOT/name).is_file():
            files.add(name)
    files.update(p.name for p in ROOT.glob('*.spec'))
    for folder in ('src', 'tools', 'tests', 'dghub_plugin'):
        for path in (ROOT/folder).rglob('*'):
            relative = path.relative_to(ROOT)
            if (path.is_file() and path.suffix.lower() in ('.py', '.json', '.spec', '.md')
                    and not any(part in ('__pycache__','build','dist','debug','sessions','.pytest_cache') for part in relative.parts)):
                files.add(relative.as_posix())
    backend = ROOT/'official_v2/socket/v2/backend'
    for path in backend.rglob('*'):
        if path.is_file() and path.suffix.lower() in ('.js', '.json', '.md') and 'node_modules' not in path.parts:
            files.add(path.relative_to(ROOT).as_posix())
    files.update(path.relative_to(ROOT).as_posix() for path in (ROOT/'packaging/backend').glob('*.json'))
    for name in ('main_official_guide.md', 'main_ui_upgrade_20261010.md', 'main_ui_acceptance_20261010.md',
                 'waveform_sources.md', 'third_party_notices.md', 'build_main.md',
                 'calibration_guide.md', 'dghub_plugin_guide.md'):
        files.add('docs/'+name)
    for folder in ('docs/images', 'docs/releases'):
        files.update(path.relative_to(ROOT).as_posix() for path in (ROOT/folder).glob('*') if path.is_file())
    for folder in ('full_health', 'injured', 'death', 'healing', 'hud_1080p_20261002'):
        for path in (ROOT/'screenshots'/folder).rglob('*'):
            if (path.is_file() and path.suffix.lower() in ('.jpg','.jpeg','.png','.json')
                    and not any(marker in path.name.lower() for marker in ('crop','zoom','thumb'))):
                files.add(path.relative_to(ROOT).as_posix())
    for name in ('20260913154935_1.jpg','20260913160405_1.jpg','20260913160406_1.jpg'):
        files.add('screenshots/20260913/'+name)
    if any(not (ROOT/name).is_file() for name in files):
        raise RuntimeError('Publication source file missing')
    return sorted(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    files = source_files()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(files)+'\n', encoding='utf-8')
    print(json.dumps(dict(files=len(files), source_bytes=sum((ROOT/name).stat().st_size for name in files),
                          binaries=False, user_preferences=False, video=False), indent=2))
