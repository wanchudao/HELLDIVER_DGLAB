"""Create a source publication commit without changing the current checkout/index."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

from publication_manifest import ROOT, source_files


def create(parent, branch, output):
    output = output.resolve()
    if not output.is_relative_to(ROOT/'build'):
        raise RuntimeError('Publication staging must remain inside project build/')
    output.mkdir(parents=True, exist_ok=True)
    index = output/'publication.index'
    if index.exists():
        raise RuntimeError('Publication index already exists; inspect previous attempt first')
    env = os.environ | {'GIT_INDEX_FILE':str(index),
                        'GIT_AUTHOR_NAME':'wanchudao',
                        'GIT_AUTHOR_EMAIL':'280326495+wanchudao@users.noreply.github.com',
                        'GIT_COMMITTER_NAME':'wanchudao',
                        'GIT_COMMITTER_EMAIL':'280326495+wanchudao@users.noreply.github.com'}
    def git(*args, data=None):
        return subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),
                                        '-c','core.whitespace=-blank-at-eof', *args],
                                       cwd=ROOT, env=env, input=data)
    files = source_files()
    git('read-tree', parent)
    tracked = [path.decode() for path in git('ls-files','-z').split(b'\0') if path]
    remove = sorted(set(tracked)-set(files))
    if remove:
        git('update-index','--force-remove','-z','--stdin', data='\0'.join(remove).encode()+b'\0')
    for offset in range(0, len(files), 40):
        git('add','-f','--', *files[offset:offset+40])
    git('diff','--cached','--check')
    tree = git('write-tree').decode().strip()
    sha = git('commit-tree', tree, '-p', parent,
              '-m','release: independent Cutting Edge UI main-v1.0.0-beta.1').decode().strip()
    git('update-ref','refs/heads/'+branch, sha, '')
    snapshot = output/'source_check'
    snapshot.mkdir()
    archive = git('archive','--format=zip',sha)
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        assert all(not Path(entry.filename).is_absolute() and '..' not in Path(entry.filename).parts
                   for entry in package.infolist())
        package.extractall(snapshot)
    report = dict(status='ok', commit=sha, tree=tree, parent=parent, branch=branch,
                  files=len(files), tracked_artifacts_removed=len(remove), checkout_modified=False,
                  normal_index_modified=False, snapshot=str(snapshot))
    (output/'source_commit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output/'source_manifest.txt').write_text('\n'.join(files)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',required=True)
    parser.add_argument('--branch',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(create(args.parent,args.branch,args.output),indent=2))
