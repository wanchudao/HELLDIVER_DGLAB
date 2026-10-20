import hashlib
import json
from pathlib import Path
import zipfile

from tools import prepare_public_release as publication
from tools.publication_audit import locations


def test_public_archive_retains_binary_bytes_and_original_archive(tmp_path, monkeypatch):
    docs = tmp_path/'project/docs'
    (docs/'releases').mkdir(parents=True)
    (docs/'third_party_notices.md').write_text('Third-party sources', encoding='utf-8')
    (docs/'releases/main-v1.0.0-beta.1.md').write_text('Public release', encoding='utf-8')
    monkeypatch.setattr(publication, 'ROOT', tmp_path/'project')
    monkeypatch.setattr(publication, 'license_assets', lambda version: {'Node/LICENSE.txt': b'Node license'})
    source = tmp_path/'original.zip'
    info = dict(dependencies={'node':'v24.19.0'}, exe_sha256=hashlib.sha256(b'exe').hexdigest(),
                path=r'E:\HELLDIVER_DGLAB\release\private')
    with zipfile.ZipFile(source, 'x', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(publication.EXE, b'exe')
        archive.writestr(publication.NODE, b'node')
        archive.writestr('HELLDIVER_Main/BUILD_INFO.json', json.dumps(info))
    original = source.read_bytes()
    result = publication.prepare(source, tmp_path/'public.zip')
    assert source.read_bytes() == original and result['original_zip_preserved']
    with zipfile.ZipFile(tmp_path/'public.zip') as archive:
        assert archive.testzip() is None
        assert archive.read(publication.EXE) == b'exe'
        assert archive.read(publication.NODE) == b'node'
        assert b'<project>' in archive.read('HELLDIVER_Main/BUILD_INFO.json')


def test_metadata_redaction_keeps_results_not_user_paths():
    result = publication.redacted({'valid':True, 'path':r'C:\Users\private_name\AppData\file',
                                   'paths':[r'E:\HELLDIVER_DGLAB\build\result.json']})
    assert result['valid'] is True
    assert 'private_name' not in str(result) and 'HELLDIVER_DGLAB' not in str(result)


def test_secret_scan_only_reports_locations():
    value = 'ghp_'+'A'*36
    result = locations(('example\n'+value).encode(), 'fixture.txt')
    assert result == [{'file':'fixture.txt','line':2,'kind':'github_token'}]
    assert value not in json.dumps(result)


def test_public_source_includes_current_guides_without_private_artifacts():
    from tools.publication_manifest import source_files

    files = set(source_files())
    assert {
        'docs/resolution_acceptance_20261017.md',
        'docs/resolution_testing_20261017.md',
        'docs/postgame_retest_20261018.md',
        'docs/death_live_acceptance_20261014.md',
        'docs/death_live_testing_20261014.md',
        'docs/releases/main-v1.0.0-beta.3.md',
        'docs/releases/dghub-v1.0.7.md',
    } <= files
    assert not any(Path(name).parts[0] in {'build', 'release', 'debug', 'sessions'}
                   or Path(name).name in {'config.ini', 'feedback_settings.ini',
                                          'hud_settings.ini', 'ui_settings.ini'}
                   for name in files)


def test_source_commit_preserves_parent_files_and_normal_index(tmp_path, monkeypatch):
    import subprocess
    from tools import create_publication_commit as commit_tool

    project = tmp_path / 'project'
    project.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-c', 'safe.directory=' + project.as_posix(),
                                        *args], cwd=project)

    git('init')
    git('config', 'user.name', 'Publication Test')
    git('config', 'user.email', 'publication@example.invalid')
    (project / 'README.md').write_text('old\n', encoding='utf-8')
    (project / 'unrelated.txt').write_text('preserve this remote file\n', encoding='utf-8')
    git('add', 'README.md', 'unrelated.txt')
    git('commit', '-m', 'parent')
    parent = git('rev-parse', 'HEAD').decode().strip()
    (project / 'README.md').write_text('new release\n', encoding='utf-8')
    original_index = (project / '.git/index').read_bytes()
    monkeypatch.setattr(commit_tool, 'ROOT', project)
    monkeypatch.setattr(commit_tool, 'source_files', lambda: ['README.md'])

    report = commit_tool.create(parent, 'codex/publication-test', project / 'build/publication',
                                message='release: two new test packages', preserve_parent=True)

    assert report['tracked_artifacts_removed'] == 0 and report['parent_files_preserved']
    assert git('show', report['commit'] + ':unrelated.txt') == b'preserve this remote file\n'
    assert git('show', report['commit'] + ':README.md') == b'new release\n'
    assert git('log', '-1', '--format=%s', report['commit']).decode().strip() == 'release: two new test packages'
    assert git('rev-parse', 'HEAD').decode().strip() == parent
    assert (project / '.git/index').read_bytes() == original_index
    assert (project / 'README.md').read_text(encoding='utf-8') == 'new release\n'
