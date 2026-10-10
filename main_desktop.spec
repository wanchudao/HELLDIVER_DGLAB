# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import shutil

ROOT = Path(SPECPATH)
NODE = shutil.which('node')
BACKEND = ROOT / 'build/main_backend_minimal'
if not NODE or not (BACKEND / 'node_modules/ws/package.json').is_file():
    raise RuntimeError('Stage production Node dependencies before building')

a = Analysis(
    [str(ROOT / 'tools/desktop_app.py')], pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / 'config.example.ini'), '.'),
           (str(ROOT / 'docs/main_official_guide.md'), 'docs'),
           (str(ROOT / 'docs/waveform_sources.md'), 'docs'),
           (str(BACKEND / 'src'), 'backend/src'),
           (str(BACKEND / 'node_modules'), 'backend/node_modules'),
           (NODE, 'runtime')],
    hiddenimports=['src.runner_real','src.desktop_runtime','src.dglab_sender',
                   'src.vision_pipeline','src.trigger','src.feedback_mixer',
                   'mss','qrcode','PIL.ImageTk','tkinter','websocket'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['dxcam','websockets'], noarchive=False,
)
for name, source, kind in a.pure:
    if name.startswith('src.') and not Path(source).resolve().is_relative_to(ROOT / 'src'):
        raise RuntimeError(f'Unexpected shared core: {name}')
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,
          name='HELLDIVER_Main',console=False,upx=False,strip=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='HELLDIVER_Main')
