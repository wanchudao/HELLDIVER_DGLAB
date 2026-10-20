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


class MinMaxInfo(ctypes.Structure):
    _fields_ = [("reserved", wt.POINT), ("max_size", wt.POINT), ("max_position", wt.POINT),
                ("min_track", wt.POINT), ("max_track", wt.POINT)]


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
        (user.GetClientRect, wt.BOOL, (wt.HWND, ctypes.POINTER(wt.RECT))),
        (user.MonitorFromWindow, wt.HANDLE, (wt.HWND, wt.DWORD)),
        (user.GetMonitorInfoW, wt.BOOL, (wt.HANDLE, ctypes.POINTER(MonitorInfo))),
        (user.MonitorFromPoint, wt.HANDLE, (wt.POINT, wt.DWORD)),
        (user.ClientToScreen, wt.BOOL, (wt.HWND, ctypes.POINTER(wt.POINT))),
        (user.SetCapture, wt.HWND, (wt.HWND,)),
        (user.ReleaseCapture, wt.BOOL, ()),
        (user.GetDpiForWindow, wt.UINT, (wt.HWND,)),
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
        self.kind = "full"
        self.live_bounds = None
        self.drag_origin = None
        self._releasing_capture = False
        self.monitor_bounds = getattr(controller, "monitor_bounds", None)
        self.hooks = set()
        self.callback = callback_type(self._message)
        self.original_style = self.user.GetWindowLongW(self.hwnd, -16)
        self.full_bounds = self._bounds()
        if getattr(controller, "live_small", False):
            x, y, w, h = self.full_bounds
            self.full_bounds = (x, y, w - controller.live_size + 380, h - controller.live_size + 250)
        try:
            # HighGUI uses a top-level frame plus one image child. Do not
            # subclass any foreign process, even if it uses the same title.
            child = self.user.FindWindowExW(self.hwnd, None, "HighGUI class", None)
            if not child:
                raise RuntimeError("Own control image window was not found")
            self.image_child = child
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
            result = self.common.DefSubclassProc(hwnd, message, wparam, lparam)
            position = ctypes.cast(lparam, ctypes.POINTER(WindowPos)).contents
            position.flags |= SWP_NOACTIVATE
            if getattr(self, "kind", "full") == "live" and hwnd == self.hwnd and not position.flags & 0x1:
                # HighGUI applies its old decorated-window size during imshow.
                # The tiny borderless control must keep its exact client size.
                position.cx = position.cy = self.controller.live_size
            elif hwnd == self.hwnd and getattr(self, "kind", "full") == "full" and hasattr(self, "full_bounds") and not position.flags & 0x1:
                # imshow may still hold the previous capsule's image geometry.
                # Restoring the frame must not shrink the complete controls.
                position.cx, position.cy = self.full_bounds[2:]
            return result
        if message == WM_NCCALCSIZE and self.compact and hwnd == self.hwnd:
            return 0  # the borderless pill uses the whole frame as client area
        if message == 0x24 and getattr(self, "kind", "full") == "live" and lparam:
            limits = ctypes.cast(lparam, ctypes.POINTER(MinMaxInfo)).contents
            limits.min_track.x = limits.min_track.y = self.controller.live_size
            limits.max_track.x = limits.max_track.y = self.controller.live_size
            return 0
        if message == WM_CANCELMODE:
            self.controller.cancel_pending()
        if message == 0x215 and getattr(self, "kind", "full") == "live" and not self._releasing_capture:
            self.controller.cancel_pending()
        if getattr(self, "kind", "full") == "live" and message in (0x200, 0x201, 0x202):
            point = wt.POINT(ctypes.c_short(lparam & 0xffff).value,
                             ctypes.c_short((lparam >> 16) & 0xffff).value)
            if self.user.ClientToScreen(hwnd, ctypes.byref(point)):
                if message == 0x201:
                    self.drag_origin = self._bounds()[:2]
                    self.user.SetCapture(hwnd)
                # OpenCV constants: move=0, down=1, up=4.
                x, y, width, height = self._bounds()
                outside = not (x <= point.x < x + width and y <= point.y < y + height)
                if message == 0x202 and outside and not self.controller._live_dragged:
                    self.controller.cancel_pending()
                else:
                    self.controller.on_live_pointer({0x200: 0, 0x201: 1, 0x202: 4}[message], point.x, point.y)
                if message == 0x202:
                    self._releasing_capture = True
                    try:
                        self.user.ReleaseCapture()
                    finally:
                        self._releasing_capture = False
            return 0
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
        if self.monitor_bounds is not None:
            left, top, right, bottom = self.monitor_bounds
            monitor = self.user.MonitorFromPoint(wt.POINT((left + right) // 2, (top + bottom) // 2), 2)
        else:
            monitor = self.user.MonitorFromWindow(self.hwnd, 2)
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise RuntimeError("Could not read control window monitor")
        return info.work.left, info.work.top, info.work.right, info.work.bottom

    def topmost(self):
        return not self.closed and bool(self.user.SetWindowPos(
            self.hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | SWP_NOACTIVATE))

    def move_live(self, dx, dy):
        if self.closed or self.kind != "live" or self.drag_origin is None:
            return
        _, _, width, height = self._bounds()
        x, y = clamp_position(self.drag_origin[0] + dx, self.drag_origin[1] + dy,
                              width, height, self._work_area())
        if not self.user.SetWindowPos(self.hwnd, wt.HWND(-1), x, y, width, height, SWP_NOACTIVATE):
            raise RuntimeError("Could not move live button")
        self.live_bounds = (x, y, width, height)

    def _sync_image_child(self):
        rect = wt.RECT()
        if not self.user.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise RuntimeError("Could not read control client size")
        if not self.user.SetWindowPos(self.image_child, None, 0, 0, rect.right, rect.bottom,
                                      SWP_NOACTIVATE | 0x4):
            raise RuntimeError("Could not synchronize control image size")

    def layout(self, compact, size, *, kind=None, monitor_bounds=None):
        if self.closed:
            return
        kind = kind or ("island" if compact else "full")
        if kind not in ("full", "island", "live"):
            raise ValueError("Invalid window layout")
        self.monitor_bounds = monitor_bounds
        old_kind = self.kind
        if kind == "live":
            self.controller.live_size = max(32, round(32 * (self.user.GetDpiForWindow(self.hwnd) or 96) / 96))
            self.controller.live_drag_threshold = max(4, round(self.controller.live_size / 8))
            self.controller.move_live = self.move_live
        if kind == old_kind and (kind != "live" or self._bounds()[2:] == size):
            if not compact:
                self.full_bounds = self._bounds()
            self._sync_image_child()
            return
        if old_kind == "live":
            self.live_bounds = self._bounds()
        elif old_kind == "full" and (self.live_bounds is not None or not getattr(self.controller, "live_small", False)):
            self.full_bounds = self._bounds()
        self.kind = kind
        self.compact = compact
        work = self._work_area()
        if compact:
            width, height = size
            if kind == "live" and self.live_bounds is not None:
                x, y = self.live_bounds[:2]
            else:
                x, y = (work[0] + work[2] - width) // 2, work[1] + (round(20 * width / 32) if kind == "live" else 20)
            style = self.original_style & ~0x00C40000  # caption + resizing border
        else:
            x, y, width, height = self.full_bounds
            style = self.original_style
        x, y = clamp_position(x, y, width, height, work)
        self._set_style(-16, style)
        if not self.user.SetWindowPos(self.hwnd, wt.HWND(-1), x, y, width, height,
                                      SWP_NOACTIVATE | 0x20):
            raise RuntimeError("Could not resize control window")
        self._sync_image_child()
        if compact:
            radius = max(8, width // 4) if kind == "live" else height
            region = self.gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
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


def update_layout(title, compact, size, *, kind=None, monitor_bounds=None):
    guard = _guards.get(title)
    if guard:
        guard.layout(compact, size, kind=kind, monitor_bounds=monitor_bounds)


def guard_closed(title):
    guard = _guards.get(title)
    return bool(guard and guard.closed)


def release_guard(title):
    guard = _guards.get(title)
    if guard:
        guard.release()
        del _guards[title]
