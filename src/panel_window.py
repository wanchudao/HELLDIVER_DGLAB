"""Win32 policy for our own HighGUI panel only: topmost without activation.

WS_EX_NOACTIVATE alone does not cover Windows' hover-to-activate setting.
Subclass both frame and image child, reject WM_MOUSEACTIVATE, and add
SWP_NOACTIVATE to every position change (including HighGUI's autosizing).
No global hooks, game injection, focus forcing or system setting changes.
"""
import ctypes
from ctypes import wintypes as wt
import logging
import os


log = logging.getLogger(__name__)
WM_MOUSEACTIVATE, WM_WINDOWPOSCHANGING = 0x21, 0x46
WM_NCDESTROY, WM_NCCALCSIZE, WM_CANCELMODE = 0x82, 0x83, 0x1F
MA_NOACTIVATE = 3
SWP_NOACTIVATE = 0x10
WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x08000000, 0x80
_guards = {}
_native = None


class WindowPos(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("insert_after", wt.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wt.UINT)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT),
                ("work", wt.RECT), ("flags", wt.DWORD)]


def _api():
    global _native
    if _native is not None:
        return _native
    user = ctypes.WinDLL("user32", use_last_error=True)
    common = ctypes.WinDLL("comctl32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                                 wt.WPARAM, wt.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
    signatures = (
        (user.FindWindowExW, wt.HWND, (wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR)),
        (user.GetWindowThreadProcessId, wt.DWORD, (wt.HWND, ctypes.POINTER(wt.DWORD))),
        (user.IsWindow, wt.BOOL, (wt.HWND,)),
        (user.GetWindowLongW, ctypes.c_long, (wt.HWND, ctypes.c_int)),
        (user.SetWindowLongW, ctypes.c_long, (wt.HWND, ctypes.c_int, ctypes.c_long)),
        (user.SetWindowPos, wt.BOOL, (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.UINT)),
        (user.GetWindowRect, wt.BOOL, (wt.HWND, ctypes.POINTER(wt.RECT))),
        (user.MonitorFromWindow, wt.HANDLE, (wt.HWND, wt.DWORD)),
        (user.GetMonitorInfoW, wt.BOOL, (wt.HANDLE, ctypes.POINTER(MonitorInfo))),
        (user.SetWindowRgn, ctypes.c_int, (wt.HWND, wt.HRGN, wt.BOOL)),
        (gdi.CreateRoundRectRgn, wt.HRGN, (ctypes.c_int,) * 6),
        (gdi.DeleteObject, wt.BOOL, (wt.HANDLE,)),
        (common.SetWindowSubclass, wt.BOOL, (wt.HWND, callback, ctypes.c_size_t, ctypes.c_size_t)),
        (common.RemoveWindowSubclass, wt.BOOL, (wt.HWND, callback, ctypes.c_size_t)),
        (common.DefSubclassProc, ctypes.c_ssize_t, (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)),
    )
    for function, result, args in signatures:
        function.restype, function.argtypes = result, args
    _native = user, common, gdi, callback
    return _native


def _own_frame(user, title):
    after = None
    while True:
        hwnd = user.FindWindowExW(None, after, None, title)
        if not hwnd:
            return None
        pid = wt.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return hwnd
        after = hwnd


def clamp_position(x, y, width, height, work):
    left, top, right, bottom = work
    return (max(left, min(x, right - width)), max(top, min(y, bottom - height)))


class PanelWindowGuard:
    def __init__(self, title, controller):
        self.user, self.common, self.gdi, callback_type = _api()
        self.hwnd = _own_frame(self.user, title)
        if not self.hwnd:
            raise RuntimeError("Own control window was not found")
        self.controller = controller
        self.closed, self.compact = False, False
        self.hooks = set()
        self.callback = callback_type(self._message)
        self.original_style = self.user.GetWindowLongW(self.hwnd, -16)
        self.full_bounds = self._bounds()
        try:
            # HighGUI uses a top-level frame plus one image child. Do not
            # subclass any foreign process, even if it uses the same title.
            child = self.user.FindWindowExW(self.hwnd, None, "HighGUI class", None)
            if not child:
                raise RuntimeError("Own control image window was not found")
            for hwnd in (self.hwnd, child):
                if hwnd and not self.common.SetWindowSubclass(hwnd, self.callback, 1, 0):
                    raise RuntimeError("Could not protect control window focus")
                if hwnd:
                    self.hooks.add(hwnd)
            style = self.user.GetWindowLongW(self.hwnd, -20)
            self._set_style(-20, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            if not self.topmost():
                raise RuntimeError("Could not set non-activating topmost")
        except Exception:
            self.release()
            raise

    def _message(self, hwnd, message, wparam, lparam, subclass_id, reference):
        if message == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE  # keep clicks; never activate on hover or click
        if message == WM_WINDOWPOSCHANGING and lparam:
            ctypes.cast(lparam, ctypes.POINTER(WindowPos)).contents.flags |= SWP_NOACTIVATE
        if message == WM_NCCALCSIZE and self.compact and hwnd == self.hwnd:
            return 0  # the borderless pill uses the whole frame as client area
        if message == WM_CANCELMODE:
            self.controller.cancel_pending()
        if message == WM_NCDESTROY:
            self.common.RemoveWindowSubclass(hwnd, self.callback, subclass_id)
            self.hooks.discard(hwnd)
            self.closed = True
            self.controller.cancel_pending()
        return self.common.DefSubclassProc(hwnd, message, wparam, lparam)

    def _bounds(self):
        rect = wt.RECT()
        if not self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            raise RuntimeError("Could not read control window position")
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    def _set_style(self, index, style):
        ctypes.set_last_error(0)
        previous = self.user.SetWindowLongW(self.hwnd, index, style)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())

    def _work_area(self):
        monitor = self.user.MonitorFromWindow(self.hwnd, 2)
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise RuntimeError("Could not read control window monitor")
        return info.work.left, info.work.top, info.work.right, info.work.bottom

    def topmost(self):
        return not self.closed and bool(self.user.SetWindowPos(
            self.hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | SWP_NOACTIVATE))

    def layout(self, compact, size):
        if self.closed:
            return
        if compact == self.compact:
            if not compact:
                self.full_bounds = self._bounds()
            return
        self.compact = compact
        work = self._work_area()
        if compact:
            width, height = size
            x, y = (work[0] + work[2] - width) // 2, work[1] + 20
            style = self.original_style & ~0x00C40000  # caption + resizing border
        else:
            x, y, width, height = self.full_bounds
            style = self.original_style
        x, y = clamp_position(x, y, width, height, work)
        self._set_style(-16, style)
        if not self.user.SetWindowPos(self.hwnd, wt.HWND(-1), x, y, width, height,
                                      SWP_NOACTIVATE | 0x20):
            raise RuntimeError("Could not resize control window")
        if compact:
            region = self.gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, height, height)
            if not region:
                raise RuntimeError("Could not create island shape")
            if not self.user.SetWindowRgn(self.hwnd, region, True):
                self.gdi.DeleteObject(region)
                raise RuntimeError("Could not apply island shape")
            # Windows owns region after a successful SetWindowRgn.
        else:
            if not self.user.SetWindowRgn(self.hwnd, None, True):
                raise RuntimeError("Could not restore control window shape")

    def release(self):
        for hwnd in tuple(self.hooks):
            if self.user.IsWindow(hwnd):
                if not self.common.RemoveWindowSubclass(hwnd, self.callback, 1):
                    raise RuntimeError("Could not release control window callback")
            self.hooks.discard(hwnd)


def install_guard(title, controller):
    if title in _guards:
        release_guard(title)
    _guards[title] = PanelWindowGuard(title, controller)
    return True


def set_topmost(title):
    guard = _guards.get(title)
    return bool(guard and guard.topmost())


def update_layout(title, compact, size):
    guard = _guards.get(title)
    if guard:
        guard.layout(compact, size)


def guard_closed(title):
    guard = _guards.get(title)
    return bool(guard and guard.closed)


def release_guard(title):
    guard = _guards.get(title)
    if guard:
        guard.release()
        del _guards[title]
