"""Candidate self-contained DGHub entry; not a release until lifecycle gates pass."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))


def popup(kind, scale):
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    window = tk.Tk()
    window.title("HELLDIVER DGHub · " + ("使用指南" if kind == "guide" else "截图检查"))
    if kind == "guide":
        text = (ROOT / "docs/dghub_plugin_guide.md").read_text(encoding="utf-8")
        from dghub_plugin.session_storage import record_data_home
        try:
            storage = record_data_home(Path.cwd(), capture_source="selected_monitor", create=False)
            text = f"程序目录：{Path.cwd()}\n正式运行会话目录：{storage / 'sessions'}\n（本指南只显示路径，不创建或迁移数据。）\n\n" + text
        except ValueError:
            text = "当前无法确定正式运行数据目录；请核对 LOCALAPPDATA。\n（指南未写入任何数据。）\n\n" + text
        window.geometry("850x680")
        display = scrolledtext.ScrolledText(window, wrap="word", font=("Microsoft YaHei UI", 11))
        display.pack(fill="both", expand=True, padx=15, pady=15)
        display.insert("end", text)
        display.configure(state="disabled")
        ttk.Button(window, text="关闭", command=window.destroy).pack(pady=8)
        window.mainloop()
        return 0
    window.withdraw()
    paths = filedialog.askopenfilenames(parent=window, title="选择原始 1920×1080 截图（不缩放）",
                                       filetypes=[("游戏截图", "*.png *.jpg *.jpeg *.bmp")])
    if not paths:
        window.destroy()
        return 0
    try:
        from dghub_plugin.diagnostics import inspect_images
        from dghub_plugin.sdk_config import defaults
        from src.config_loader import load_config
        from src.hud_dialog import ScreenshotCheckDialog
        values = defaults() | {"hud_scale_percent": scale}
        report = inspect_images(paths, values, load_config(ROOT / "config.example.ini"))
        dialog = ScreenshotCheckDialog(window, report, paths)
        window.wait_window(dialog.window)
    except Exception:
        messagebox.showerror("截图检查失败", "无法完成截图检查，请核对文件及 HUD 档位。", parent=window)
        window.destroy()
        return 1
    window.destroy()
    return 0


async def run_host(environment, *, capture_factory=None, ui=None, capture_source=None):
    from dghub_plugin.live_driver import LiveDriver
    from dghub_plugin.sdk_session import SDKSession
    from dghub_plugin.sdk_transport import DGHubTransport
    from dghub_plugin.session_record import SessionRecord
    from dghub_plugin.session_storage import record_data_home
    from dghub_plugin.preset_catalog import fetch_catalog_snapshot
    from src.config_loader import load_config
    source = capture_source or ("injected_test" if capture_factory is not None else "selected_monitor")
    storage = record_data_home(environment.plugin_root, capture_source=source)
    recorder = SessionRecord(environment.plugin_root, storage_root=storage, capture_source=source)
    session = driver = transport = None
    code = None
    try:
        session = SDKSession(load_config(ROOT / "config.example.ini"), log_writer=recorder,
                             available_presets=frozenset())
        driver = LiveDriver(capture_factory=capture_factory, ui=ui)
        transport = DGHubTransport(session, environment, driver=driver, catalog_loader=fetch_catalog_snapshot)
        code = await transport.run()
        return code
    finally:
        # Final zeros and capture cleanup precede this bounded disk wait.
        if session is not None:
            session.close("runtime_cleanup")
        if driver is not None:
            driver.close()
        recorder.finish(transport.reason if transport is not None else "initialization_failed",
                        error=code != 0 or session is None or bool(session.last_error),
                        diagnostic=session.last_error if session is not None else "Core initialization failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="HELLDIVER DGHub · 1080p 可调 HUD")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--popup-guide", action="store_true")
    group.add_argument("--popup-check", action="store_true")
    group.add_argument("--inspect-images", nargs="+", type=Path)
    group.add_argument("--software-host-test", action="store_true",
                       help="开发用模拟宿主接入；未进行正式宿主输出验收")
    group.add_argument("--software-replay-test", type=Path,
                       help="开发验收：显式合成原像素帧，不抓屏、不显示控制窗")
    parser.add_argument("--hud-scale", type=int, default=100, choices=range(75, 126, 5))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.popup_guide or args.popup_check:
        return popup("guide" if args.popup_guide else "check", args.hud_scale)
    if args.inspect_images:
        from dghub_plugin.diagnostics import inspect_images
        from dghub_plugin.sdk_config import defaults
        from src.config_loader import load_config
        report = inspect_images(args.inspect_images, defaults() | {"hud_scale_percent": args.hud_scale},
                                load_config(ROOT / "config.example.ini"))
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    from dghub_plugin.panel import DGHubPanel
    from dghub_plugin.sdk_transport import HostEnvironment
    try:
        environment = HostEnvironment.from_environ()
        if args.software_replay_test is not None:
            from dghub_plugin.replay_capture import ReplayCapture, ReplayControls
            return asyncio.run(run_host(environment, capture_factory=lambda **kwargs:
                ReplayCapture(args.software_replay_test, **kwargs),
                ui=ReplayControls(args.software_replay_test), capture_source="synthetic_replay"))
        return asyncio.run(run_host(environment, ui=DGHubPanel(environment.plugin_root)))
    except Exception:
        if sys.stderr:
            print("请从 DGHub 导入并启用插件；启动环境或运行失败，未记录会话凭据。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
