# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
a = Analysis(
    [str(ROOT / 'tools/dghub_sdk_entry.py')], pathex=[str(ROOT)], binaries=[],
    datas=[(str(ROOT / 'config.example.ini'), '.'),
           (str(ROOT / 'docs/dghub_sdk_development_20260917.md'), '.')],
    hiddenimports=['dghub_plugin.sdk_session', 'dghub_plugin.sdk_config',
                   'src.vision_pipeline', 'src.trigger', 'src.feedback_mixer'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['src.dglab_sender', 'websocket', 'websockets', 'dxcam', 'qrcode', 'tkinter'],
    noarchive=False,
)
required = {'src.vision_pipeline', 'src.trigger', 'src.feedback_mixer',
            'dghub_plugin.sdk_session', 'dghub_plugin.sdk_config'}
if not required.issubset({name for name, _path, _kind in a.pure}):
    raise RuntimeError('Missing SDK/shared-core module')
for name, source, _kind in a.pure:
    if name.startswith('src.') and not Path(source).resolve().is_relative_to(ROOT / 'src'):
        raise RuntimeError('Legacy core selected: ' + name)
    if name == 'src.dglab_sender' or name.split('.')[0] in {'websocket', 'websockets', 'dxcam', 'qrcode'}:
        raise RuntimeError('Device transport in protocol-only preview: ' + name)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='helldiver_dglab_sdk',
          console=True, upx=False, strip=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='HELLDIVER_DGHub_SDK_Preview')
