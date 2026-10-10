# -*- mode: python ; coding: utf-8 -*-
"""Build the strictly offline V0.3 screenshot/video acceptance GUI.

Only the pure Mock trigger/mixer is bundled. Capture and device transports are
explicitly excluded; copying a hardware config beside the EXE cannot enable them.
"""

core_sources = [
    "roi_locator.py", "health_detector.py", "effect_detector.py",
    "vision_pipeline.py", "state_machine.py", "trigger.py", "feedback_mixer.py",
]

a = Analysis(
    ["tools/offline_acceptance_app.py"],
    pathex=["."],
    binaries=[],
    datas=[("config.example.ini", ".")]
          + [("src/" + name, "src") for name in core_sources],
    hiddenimports=[
        "src.config_loader", "src.roi_locator", "src.health_detector",
        "src.effect_detector", "src.state_machine", "src.vision_pipeline",
        "src.trigger", "src.feedback_mixer", "tools.video_acceptance",
        "numpy", "cv2", "tkinter", "tkinter.filedialog",
        "tkinter.messagebox", "tkinter.scrolledtext", "tkinter.ttk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "src.dglab_sender", "src.screen_capture",
        "websocket", "websockets", "mss", "dxcam",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HELLDIVER_DGLAB_V0.3_OfflineTester",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
