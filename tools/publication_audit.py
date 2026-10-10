"""Read-only credential-pattern scan for selected source files and reachable history.

Only locations are reported, never matching credential values. This is a focused
pre-publication check, not a guarantee that all possible sensitive data is absent.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'github_token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'aws_access_key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'openai_key': re.compile(rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}'),
    'credential_url': re.compile(rb'https?://[^\s/@:]{1,80}:[^\s/@]{4,120}@'),
}


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory='+ROOT.as_posix(), *args], cwd=ROOT)


def locations(data, filename):
    if len(data) > 4_000_000 or b'\x00' in data[:4096]:
        return []
    return [dict(file=filename, line=data[:match.start()].count(b'\n')+1, kind=name)
            for name, pattern in PATTERNS.items() for match in pattern.finditer(data)]


def audit(revision, paths):
    findings, blobs = [], {}
    commits = git('rev-list', revision).decode().splitlines()
    for commit in commits:
        for entry in git('ls-tree', '-rz', commit).split(b'\x00'):
            if not entry:
                continue
            meta, path = entry.split(b'\t', 1)
            _, kind, oid = meta.split()
            if kind != b'blob' or oid in blobs:
                continue
            filename = path.decode('utf-8', errors='replace')
            blobs[oid] = filename
            findings.extend(locations(git('cat-file', 'blob', oid.decode()), 'history:'+filename))
    checked = 0
    for relative in paths:
        path = ROOT/relative
        if path.is_file():
            checked += 1
            findings.extend(locations(path.read_bytes(), relative))
    return dict(status='ok' if not findings else 'review_required', revision=revision,
                history_commits=len(commits), history_blobs=len(blobs), source_files=checked,
                findings=findings, scope='known high-confidence credential patterns; no value output')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.revision, args.manifest.read_text(encoding='utf-8').splitlines())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['findings']))
