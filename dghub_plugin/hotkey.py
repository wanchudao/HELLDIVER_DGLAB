"""Queued, F8-only Windows hotkey input; no SDK or device operations."""
from collections import deque
import ctypes
from ctypes import wintypes
import sys
import threading
import time


VK_F8 = 0x77
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x4844
MAX_EVENT_AGE = .25


class _Win32F8API:
    """All registration and message-queue calls belong to the same thread."""
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows hotkeys are unavailable")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                              wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                            wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.kernel32.GetTickCount.argtypes = []
        self.kernel32.GetTickCount.restype = wintypes.DWORD

    def register(self):
        # PeekMessage creates this thread's queue before NULL-window registration.
        message = wintypes.MSG()
        self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        return bool(self.user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, VK_F8))

    def next_message(self):
        message = wintypes.MSG()
        if not self.user32.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, 1):
            return None
        # MSG.time is the time the key was posted, not the time this pump reads it.
        # DWORD subtraction also handles the Windows uptime counter wrapping.
        age = ((int(self.kernel32.GetTickCount()) - int(message.time)) & 0xffffffff) / 1000
        return message.message, int(message.wParam), (int(message.lParam) >> 16) & 0xffff, age

    def unregister(self):
        if not self.user32.UnregisterHotKey(None, HOTKEY_ID):
            raise RuntimeError("Hotkey removal failed")


class RegisteredF8Hotkey:
    """Retain one fresh press until the UI owner consumes it, with bounded exit.

    A dedicated thread receives queued WM_HOTKEY messages, so a press shorter
    than a render tick is not lost. MOD_NOREPEAT suppresses key-hold repeats.
    Registration failures are latched until a new instance/session is created.
    """
    def __init__(self, *, api_factory=None, clock=None, startup_timeout=.5, close_timeout=.5):
        self._api_factory = api_factory or _Win32F8API
        self.clock = clock or time.monotonic
        self.startup_timeout = startup_timeout
        self.close_timeout = close_timeout
        self._lock = threading.Lock()
        self._events = deque(maxlen=1)
        self._stop = threading.Event()
        self._started = threading.Event()
        self._thread = None
        self._closed = False
        self._registered = False
        self._diagnostic = ""
        self._discard_before = -float("inf")

    @property
    def ready(self):
        with self._lock:
            return self._registered and not self._closed and not self._diagnostic

    @property
    def diagnostic(self):
        with self._lock:
            return self._diagnostic

    def _fail(self, diagnostic):
        with self._lock:
            self._diagnostic = diagnostic
            self._registered = False
            self._events.clear()

    def start(self):
        with self._lock:
            if self._closed or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="HELLDIVER-F8", daemon=True)
            thread = self._thread
        try:
            thread.start()
        except Exception:
            self._fail("F8 listener failed; use local button")
            self._started.set()
            return
        if not self._started.wait(self.startup_timeout):
            self._fail("F8 startup timed out; use local button")
            self._stop.set()

    def _run(self):
        api = None
        registered = False
        try:
            api = self._api_factory()
            registered = api.register()
            if not registered:
                self._fail("F8 registration failed; use local button")
                return
            with self._lock:
                self._registered = not self._closed and not self._diagnostic
            self._started.set()
            while not self._stop.is_set():
                # Bound a queue burst; close never waits for an unbounded drain.
                for _ in range(32):
                    if self._stop.is_set():
                        break
                    message = api.next_message()
                    if message is None:
                        break
                    kind, identifier, key, age = message
                    if kind != WM_HOTKEY or identifier != HOTKEY_ID or key != VK_F8:
                        continue
                    if not 0 <= age <= MAX_EVENT_AGE:
                        continue
                    posted_at = self.clock() - age
                    with self._lock:
                        if (self._registered and not self._closed and not self._diagnostic
                                and posted_at > self._discard_before):
                            self._events.append(posted_at)
                self._stop.wait(.01)
        except Exception:
            self._fail("F8 listener failed; use local button")
        finally:
            if registered:
                try:
                    api.unregister()
                except Exception:
                    self._fail("F8 listener failed; use local button")
            with self._lock:
                self._registered = False
                if self._diagnostic or self._closed:
                    self._events.clear()
            self._started.set()

    def consume(self):
        with self._lock:
            if not self._registered or self._closed or self._diagnostic:
                self._events.clear()
                return False
            pressed = bool(self._events and
                           0 <= self.clock() - self._events[-1] <= MAX_EVENT_AGE)
            self._events.clear()
            return pressed

    def discard(self):
        with self._lock:
            self._events.clear()
            self._discard_before = self.clock()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._registered = False
            self._events.clear()
            thread = self._thread
        self._stop.set()
        if thread is not None and thread.ident is not None:
            thread.join(self.close_timeout)
            if thread.is_alive():
                self._fail("F8 listener could not stop")
