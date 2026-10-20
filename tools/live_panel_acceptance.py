"""Own-window native integration test: no SDK, capture, APP or device output."""
import ctypes
from ctypes import wintypes as wt


def run():
    import cv2
    import tkinter as tk
    import numpy as np
    from dghub_plugin.panel import DGHubPanelController
    from src.control_panel import (PANEL_NAME, draw_control_panel, install_panel_controls,
        release_panel_controls, update_panel_layout, set_panel_topmost)
    from src.panel_window import _guards, WM_MOUSEACTIVATE
    controller = DGHubPanelController()
    controller.live_mode = True
    checks = []
    root = None
    try:
        cv2.imshow(PANEL_NAME, draw_control_panel(paused=True))
        assert install_panel_controls(controller)
        cv2.waitKey(1)
        guard = _guards[PANEL_NAME]
        user = guard.user
        user.SendMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        user.SendMessageW.restype = ctypes.c_ssize_t
        user.GetForegroundWindow.argtypes = ()
        user.GetForegroundWindow.restype = wt.HWND
        user.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
        user.GetClientRect.restype = wt.BOOL
        child = next(hwnd for hwnd in guard.hooks if hwnd != guard.hwnd)
        root = tk.Tk()
        root.title("HELLDIVER Live Button QA - no device")
        root.geometry("900x500+80+100")
        tk.Label(root, text="直播按钮焦点验收替身 · 不连接设备", font=("Microsoft YaHei UI", 18)).pack(pady=180)
        root.update()
        before = user.GetForegroundWindow()
        assert before != guard.hwnd
        full = guard._bounds()

        def mouse(event, x, y):
            rect = wt.RECT()
            assert user.GetClientRect(child, ctypes.byref(rect))
            width = controller.live_size if controller.live_small else 380
            height = controller.live_size if controller.live_small else 250
            px, py = round(x * rect.right / width), round(y * rect.bottom / height)
            user.SendMessageW(child, event, 1 if event == 0x201 else 0, (py << 16) | (px & 0xffff))

        def show():
            size = controller.live_size
            image = np.full((size, size, 3), (22, 25, 30), np.uint8) if controller.live_small else draw_control_panel(paused=True)
            cv2.imshow(PANEL_NAME, image)
            update_panel_layout(controller)
            cv2.waitKey(1)
            root.update()

        for index in range(3):
            controller.live_small = True
            controller.cancel_pending()
            show()
            show()
            small = guard._bounds()
            assert small[2:] == (controller.live_size,) * 2, (small, controller.live_size, guard.kind)
            for hwnd in guard.hooks:
                assert user.SendMessageW(hwnd, WM_MOUSEACTIVATE, guard.hwnd, 0) == 3
            mouse(0x200, 16, 16)
            assert not controller.consume_layout() and not controller.consume_toggle()
            for _ in range(5):
                set_panel_topmost()
                cv2.waitKey(1)
                assert user.GetForegroundWindow() == before
            mouse(0x201, 16, 16)
            mouse(0x200, 28, 16)
            mouse(0x202, 16, 16)
            assert not controller.consume_layout() and not controller.consume_toggle()
            moved = guard._bounds()
            assert moved[0] != small[0]
            mouse(0x201, 16, 16)
            mouse(0x202, 16, 16)
            assert controller.consume_layout() and not controller.live_small
            assert not controller.consume_toggle()
            show()
            assert guard._bounds() == full
            mouse(0x201, 70, 230)
            mouse(0x202, 70, 230)
            assert controller.consume_layout() and controller.live_small
            show()
            assert guard._bounds()[:2] == moved[:2]
            assert user.GetForegroundWindow() == before
            checks.append(dict(iteration=index, tiny_size=small[2:], dragged=True,
                drag_did_not_expand=True, click_only_expanded=True, full_position_restored=True,
                tiny_position_remembered=True, foreground_unchanged=True))
        return dict(status="ok", checks=checks, native_windows=True, actual_dpi=user.GetDpiForWindow(guard.hwnd),
            native_button_mapping=True, physical_device_used=False, real_game_tested=False,
            actual_multi_monitor_tested=False, os_dpi_changes_tested=False)
    finally:
        cv2.destroyAllWindows()
        release_panel_controls(PANEL_NAME)
        if root is not None:
            root.destroy()
