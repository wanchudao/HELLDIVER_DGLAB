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
