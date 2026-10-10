# -*- mode: python ; coding: utf-8 -*-
"""Self-contained SDK v1 plugin; release packaging requires acceptance reports."""
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
a = Analysis(
    [str(ROOT / 'tools/dghub_live_entry.py')], pathex=[str(ROOT)], binaries=[],
    datas=[(str(ROOT / 'config.example.ini'), '.'),
           (str(ROOT / 'docs/dghub_plugin_guide.md'), 'docs')],
    hiddenimports=['dghub_plugin.sdk_session', 'dghub_plugin.sdk_config',
                   'dghub_plugin.sdk_transport', 'dghub_plugin.capture_worker',
                   'dghub_plugin.live_driver', 'dghub_plugin.panel',
                   'dghub_plugin.session_record',
                   'dghub_plugin.session_storage',
                   'dghub_plugin.hotkey',
                   'dghub_plugin.replay_capture',
                   'dghub_plugin.preset_catalog',
                   'dghub_plugin.diagnostics', 'src.panel_window', 'src.screen_capture',
                   'src.hud_dialog', 'mss', 'websockets.asyncio.client'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['src.dglab_sender', 'src.desktop_runtime', 'tools.desktop_app',
              'websocket', 'qrcode', 'dxcam'], noarchive=False,
)
required = {'src.vision_pipeline', 'src.trigger', 'src.feedback_mixer',
            'src.hud_profiles', 'src.screen_capture', 'dghub_plugin.sdk_transport',
            'dghub_plugin.capture_worker', 'dghub_plugin.live_driver',
            'dghub_plugin.session_record',
            'dghub_plugin.session_storage',
            'dghub_plugin.hotkey',
            'dghub_plugin.preset_catalog',
            'dghub_plugin.panel', 'dghub_plugin.diagnostics', 'websockets.asyncio.client', 'mss'}
if not required.issubset({name for name, _path, _kind in a.pure}):
    raise RuntimeError('Missing candidate runtime component')
for name, source, _kind in a.pure:
    if name.startswith('src.') and not Path(source).resolve().is_relative_to(ROOT / 'src'):
        raise RuntimeError('Legacy core selected: ' + name)
    if name in {'src.dglab_sender', 'src.desktop_runtime', 'tools.desktop_app'}:
        raise RuntimeError('Official-backend code in DGHub candidate: ' + name)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='helldiver_dglab',
          console=False, upx=False, strip=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='HELLDIVER_DGHub_Candidate')
