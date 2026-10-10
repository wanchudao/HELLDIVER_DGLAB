"""Render only a verified application-owned window, never the user's desktop."""
import ctypes
from ctypes import wintypes as wt
import os


def capture_owned_window(window):
    from PIL import Image
    user = ctypes.WinDLL('user32', use_last_error=True)
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    user.GetAncestor.argtypes = (wt.HWND, wt.UINT)
    user.GetAncestor.restype = wt.HWND
    hwnd = user.GetAncestor(window.winfo_toplevel().winfo_id(), 2)
    pid = wt.DWORD()
    user.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value != os.getpid():
        raise RuntimeError('Refusing to capture a window owned by another process')
    rect = wt.RECT()
    user.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
    if not user.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError('Could not measure our own client window')
    width, height = rect.right, rect.bottom
    user.GetDC.argtypes = (wt.HWND,)
    user.GetDC.restype = wt.HDC
    user.ReleaseDC.argtypes = (wt.HWND, wt.HDC)
    gdi.CreateCompatibleDC.argtypes = (wt.HDC,)
    gdi.CreateCompatibleDC.restype = wt.HDC
    gdi.CreateCompatibleBitmap.argtypes = (wt.HDC, ctypes.c_int, ctypes.c_int)
    gdi.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi.SelectObject.argtypes = (wt.HDC, wt.HGDIOBJ)
    gdi.SelectObject.restype = wt.HGDIOBJ
    gdi.DeleteObject.argtypes = (wt.HGDIOBJ,)
    gdi.DeleteDC.argtypes = (wt.HDC,)
    user.PrintWindow.argtypes = (wt.HWND, wt.HDC, wt.UINT)
    dc = user.GetDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    class Header(ctypes.Structure):
        _fields_ = [('size', wt.DWORD), ('width', wt.LONG), ('height', wt.LONG),
                    ('planes', wt.WORD), ('bits', wt.WORD), ('compression', wt.DWORD),
                    ('image_size', wt.DWORD), ('xres', wt.LONG), ('yres', wt.LONG),
                    ('used', wt.DWORD), ('important', wt.DWORD)]
    class Info(ctypes.Structure):
        _fields_ = [('header', Header), ('colors', wt.DWORD*3)]
    info = Info()
    info.header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buffer = ctypes.create_string_buffer(width*height*4)
    gdi.GetDIBits.argtypes = (wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.POINTER(Info), wt.UINT)
    try:
        if not user.PrintWindow(hwnd, memory, 1):
            raise RuntimeError('Own-window rendering failed')
        gdi.SelectObject(memory, previous)
        if not gdi.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(info), 0):
            raise RuntimeError('Own-window pixels unavailable')
        return Image.frombytes('RGB', (width, height), buffer.raw, 'raw', 'BGRX')
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)
