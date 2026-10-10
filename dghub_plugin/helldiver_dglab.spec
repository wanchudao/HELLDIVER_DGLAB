# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

"""
PyInstaller 打包配置 — HELLDIVER_DGLAB 共享核心离线 Mock 入口

生成单个自包含 .exe；默认拒绝 DGHub 注册，显式 --offline 才打开 Mock。
这是后续构建配置，不表示仓库已有 ZIP/EXE 已更新；不能用于真机验收。

用法:
    cd dghub_plugin
    pyinstaller helldiver_dglab.spec

输出:
    dist/helldiver_dglab.exe  (含 opencv/numpy/mss，不含设备发送器)

分发 (zip 结构):
    helldiver_dglab_v0.5.0_offline.zip
    ├── helldiver_dglab.exe   ← PyInstaller 打包产物
    ├── manifest.json          ← 离线元信息；不发送 hello，不建议安装到宿主
    ├── README.md              ← 本地验证方法及宿主阻塞条件
    └── config.ini             ← 本地 Mock 配置（.exe 优先读同级，其次内置）

调试:
    console=False → 无控制台窗口（生产）
    console=True  → 显示控制台窗口（开发调试用）
"""

PLUGIN_DIR = Path(SPECPATH).resolve()
CORE_DIR = PLUGIN_DIR.parent

a = Analysis(
    # The freezer prepends its entry directory before pathex. An entry inside
    # the plugin folder would shadow root src with its historical src copy.
    [str(CORE_DIR / 'tools' / 'dghub_offline_entry.py')],
    pathex=[str(CORE_DIR), str(PLUGIN_DIR)],
    binaries=[],
    datas=[
        # Do not copy dghub_plugin/src: it is the obsolete independent core.
        # Manifest is informational only; registration is deliberately absent.
        (str(PLUGIN_DIR / 'manifest.json'), '.'),
        (str(PLUGIN_DIR / 'README.md'), '.'),
        # config.ini 随 .exe 打包作为内置默认（运行时优先读 .exe 同级目录的版本）
        (str(PLUGIN_DIR / 'config.ini'), '.'),
        (str(CORE_DIR / 'config.example.ini'), '.'),
    ],
    hiddenimports=[
        # vision 管线模块（显式声明防 PyInstaller 扫描遗漏）
        'src.config_loader',
        'src.roi_locator',
        'src.health_detector',
        'src.effect_detector',
        'src.state_machine',
        'src.screen_capture',
        'src.trigger',
        'src.vision_pipeline',
        'src.feedback_mixer',
        'src.runner_real',
        'src.control_panel',
        'offline_core',
        # 第三方依赖
        'numpy',
        'cv2',
        # Shared capture uses mss inside a bounded worker, not legacy DXGI.
        'mss',
        # stdlib（可能被 tree-shaking 误删）
        'configparser',
        'json',
        'logging',
        'asyncio',
        'pathlib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 不需要的模块（减小 .exe 体积）
        'tkinter',
        'unittest',
        'html',
        'xml',
        'pydoc',
        'distutils',
        'setuptools',
        # Even accidentally changing sender_mode cannot load a device backend
        # from the offline-only bundle. The CLI also always forces mock mode.
        'src.dglab_sender',
        'dxcam',
        'websocket',
        'websockets',
        'qrcode',
    ],
    noarchive=False,
)

# Fail the build instead of quietly shipping the old 50%/permanent src copy.
for module_name, source_path, _kind in a.pure:
    if module_name.startswith('src.') and not Path(source_path).resolve().is_relative_to(CORE_DIR / 'src'):
        raise RuntimeError(f'Legacy core selected by freezer: {module_name}')
required_core = {'src.vision_pipeline', 'src.trigger', 'src.feedback_mixer'}
if not required_core.issubset({name for name, _path, _kind in a.pure}):
    raise RuntimeError('Shared core is missing from the frozen application')
for module_name, _path, _kind in a.pure:
    if module_name.split('.')[0] in {'websocket', 'websockets', 'dxcam', 'qrcode'} or module_name == 'src.dglab_sender':
        raise RuntimeError(f'Forbidden device/legacy backend bundled: {module_name}')

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='helldiver_dglab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep the disabled-host reason visible; no silent hardware launch.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
