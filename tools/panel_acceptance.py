"""Opt-in native panel QA, using only application-owned windows and no output."""
import argparse
import ctypes
from ctypes import wintypes as wt
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(interactive=False, *, theme='legacy'):
    import cv2
    import tkinter as tk
    from src.control_panel import (PANEL_NAME, PanelController, draw_control_panel,
                                   install_panel_controls, update_panel_layout,
                                   set_panel_topmost, panel_window_closed, release_panel_controls)
    from src.panel_window import _guards, WM_MOUSEACTIVATE, WS_EX_NOACTIVATE
    controller = PanelController()
    root = None
    try:
        cv2.imshow(PANEL_NAME, draw_control_panel(paused=True, theme=theme))
        install_panel_controls(controller)
        cv2.waitKey(1)
        guard = _guards[PANEL_NAME]
        user = guard.user
        user.SendMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        user.SendMessageW.restype = ctypes.c_ssize_t
        user.GetForegroundWindow.argtypes = ()
        user.GetForegroundWindow.restype = wt.HWND
        user.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
        user.GetClientRect.restype = wt.BOOL
        user.GetDpiForWindow.argtypes = (wt.HWND,)
        user.GetDpiForWindow.restype = wt.UINT
        child = next(hwnd for hwnd in guard.hooks if hwnd != guard.hwnd)

        def mouse(message, x, y):
            # Send to our real HighGUI child, exercising its image-coordinate
            # mapping. This is not global input and cannot touch another app.
            client = wt.RECT()
            assert user.GetClientRect(child, ctypes.byref(client))
            width, height = (336, 56) if controller.compact else (380, 250)
            px = round(x * client.right / width)
            py = round(y * client.bottom / height)
            user.SendMessageW(child, message, 1 if message == 0x201 else 0,
                              (py << 16) | (px & 0xffff))

        def click(x, y):
            mouse(0x201, x, y)
            mouse(0x202, x, y)
        root = tk.Tk()
        root.title("HELLDIVER - Island QA (no capture / no device)")
        root.geometry("1200x700+80+100")
        root.configure(bg="#142238")
        tk.Label(root, text="悬浮窗测试背景 · 不抓屏 / 不连接 APP / 无设备输出\n\n"
                 "点击面板 ISLAND 折叠；右侧小窗图标展开。\n"
                 "本窗口作为游戏焦点替身，关闭它结束测试。",
                 font=("Microsoft YaHei UI", 18), bg="#142238", fg="white").pack(pady=200)
        status = tk.StringVar()
        tk.Label(root, textvariable=status, bg="#142238", fg="#a4d9c3",
                 font=("Microsoft YaHei UI", 12)).pack()
        root.update()
        before = user.GetForegroundWindow()
        assert before != guard.hwnd, "Focus fixture was not created"
        assert len(guard.hooks) == 2, "Frame and image child must both be protected"
        checks = []
        paused = True
        for compact in (False, True, False, True, False):
            if compact != controller.compact:
                click(*( (302, 28) if controller.compact else (70, 230) ))
                assert controller.consume_layout(), "Native layout button hitbox mismatch"
                assert controller.compact == compact
                assert not controller.consume_toggle(), "Layout changed output state"
            cv2.imshow(PANEL_NAME, draw_control_panel(paused=paused, compact=compact, theme=theme))
            update_panel_layout(controller)
            cv2.waitKey(1)
            for hwnd in guard.hooks:
                assert user.SendMessageW(hwnd, WM_MOUSEACTIVATE, guard.hwnd, 0) == 3
            for _ in range(6):
                set_panel_topmost()
                cv2.waitKey(1)
                root.update()
                assert user.GetForegroundWindow() == before, "Panel stole foreground focus"
            assert user.GetWindowLongW(guard.hwnd, -20) & WS_EX_NOACTIVATE
            bounds = guard._bounds()
            if compact:
                assert bounds[2:] == (336, 56), bounds
            toggle = (247, 28) if compact else (100, 110)
            mouse(0x200, *toggle)
            assert not controller.consume_toggle() and not controller.consume_layout()
            click(*toggle)
            assert controller.consume_toggle(), "Native output button hitbox mismatch"
            mouse(0x201, *toggle)
            mouse(0x200, 5, 5)
            mouse(0x200, *toggle)
            mouse(0x202, *toggle)
            assert not controller.consume_toggle(), "Drag-out rearmed a button"
            mouse(0x201, *toggle)
            user.SendMessageW(child, 0x1F, 0, 0)  # cancel pending gesture
            mouse(0x202, *toggle)
            assert not controller.consume_toggle()
            assert user.GetForegroundWindow() == before, "Click changed foreground focus"
            checks.append({"compact": compact, "bounds": bounds, "foreground_unchanged": True,
                           "dpi": user.GetDpiForWindow(guard.hwnd)})
        deadline = time.monotonic() + (600 if interactive else 0)
        while time.monotonic() < deadline:
            if panel_window_closed():
                break
            controller.consume_layout()
            if controller.consume_toggle():
                paused = not paused
                controller.cancel_pending()
            cv2.imshow(PANEL_NAME, draw_control_panel(paused=paused, compact=controller.compact,
                                                      health_stable=.62, state="injured", cur_fps=30, theme=theme))
            update_panel_layout(controller)
            set_panel_topmost()
            cv2.waitKey(1)
            status.set(f"布局：{'灵动岛' if controller.compact else '完整'} | "
                       f"状态：{'暂停' if paused else '模拟运行'} | "
                       f"焦点：{'背景窗口' if user.GetForegroundWindow() == before else '其他窗口'}")
            try:
                root.update()
            except tk.TclError:
                root = None
                break
            time.sleep(.025)
        if not panel_window_closed():
            cv2.destroyWindow(PANEL_NAME)
        cv2.waitKey(1)
        assert panel_window_closed(), "Closing the panel must be detected before recreation"
        return {"status": "ok", "no_device": True, "native_windows": True,
                "mouseactivate_returns_noactivate": True, "native_button_mapping": True,
                "checks": checks, "close_detected": True,
                "real_game_tested": False, "multiple_physical_dpi_tested": False}
    finally:
        cv2.destroyAllWindows()
        release_panel_controls()
        if root is not None:
            root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local", action="store_true", required=True)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = run(args.interactive)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
