"""Offline Cutting Edge theme. No networking, device controls or global hooks."""
import configparser
import math
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk

BG = '#08151B'
PANEL = '#10232B'
FIELD = '#162C35'
ACCENT = '#41C9E8'
TEXT = '#EAF2F4'
MUTED = '#8298A2'
EDGE = '#284551'
ERROR = '#FF897D'


class UiSettingsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.animation = True
        self.error = ''
        if not self.path.exists():
            return
        try:
            cp = configparser.ConfigParser(interpolation=None)
            cp.read_string(self.path.read_text(encoding='utf-8-sig'))
            if cp.sections() != ['ui'] or set(cp['ui']) != {'animation'} or cp['ui']['animation'] not in ('0', '1'):
                raise ValueError('ui_settings.ini 应只包含 [ui] animation=0 或 1')
            self.animation = cp['ui']['animation'] == '1'
        except (OSError, ValueError, configparser.Error) as exc:
            self.animation = False
            self.error = str(exc)

    def save(self, animation):
        if type(animation) is not bool:
            raise ValueError('animation 必须是布尔值')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.ui-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write('[ui]\nanimation=' + ('1' if animation else '0') + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self.animation, self.error = animation, ''


def install_theme(root):
    root.cutting_edge = True
    root.configure(background=BG)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=PANEL, foreground=TEXT, font=('Microsoft YaHei UI', 10),
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, focuscolor=ACCENT)
    style.configure('TFrame', background=PANEL)
    style.configure('Background.TFrame', background=BG)
    style.configure('TLabel', background=PANEL, foreground=TEXT)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Title.TLabel', font=('Microsoft YaHei UI', 17, 'bold'))
    style.configure('TButton', background=FIELD, foreground=TEXT, padding=(12, 8), borderwidth=1)
    style.map('TButton', background=[('disabled', PANEL), ('pressed', EDGE), ('active', '#244B59')],
              foreground=[('disabled', '#627984')], bordercolor=[('focus', ACCENT)])
    style.configure('Accent.TButton', background=ACCENT, foreground=BG)
    style.map('Accent.TButton', background=[('disabled', EDGE), ('pressed', '#2BA4C0'), ('active', '#83E9F6')],
              foreground=[('disabled', MUTED), ('!disabled', BG)])
    style.configure('Danger.TButton', foreground=ERROR)
    style.map('Danger.TButton', foreground=[('disabled', MUTED), ('!disabled', ERROR)])
    style.configure('Nav.TButton', anchor='w', padding=(12, 13))
    style.configure('Selected.Nav.TButton', background=ACCENT, foreground=BG, anchor='w', padding=(12, 13))
    style.map('Selected.Nav.TButton', background=[('active', '#83E9F6')], foreground=[('!disabled', BG)])
    for kind in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(kind, fieldbackground=FIELD, foreground=TEXT, arrowcolor=ACCENT, padding=5,
                        selectbackground=ACCENT, selectforeground=BG, insertcolor=TEXT)
        style.map(kind, fieldbackground=[('disabled', PANEL), ('readonly', FIELD)],
                  foreground=[('disabled', '#627984'), ('readonly', TEXT)], bordercolor=[('focus', ACCENT)])
    style.configure('TLabelframe', background=PANEL, bordercolor=EDGE)
    style.configure('TLabelframe.Label', background=PANEL, foreground=ACCENT, font=('Microsoft YaHei UI', 11, 'bold'))
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
    root.option_add('*TCombobox*Listbox.selectForeground', BG)
    return style


def theme_window(window, parent):
    if not getattr(parent.winfo_toplevel(), 'cutting_edge', False):
        return
    window.cutting_edge = True
    window.configure(background=BG)
    def fit():
        if not window.winfo_exists():
            return
        window.update_idletasks()
        width, height = responsive_size(window.winfo_width(), window.winfo_height(),
                                        window.winfo_screenwidth(), window.winfo_screenheight())
        minimum = window.minsize()
        window.minsize(min(minimum[0], width), min(minimum[1], height))
        window.geometry(f'{width}x{height}')
        fit_window_to_work_area(window)
    window.after_idle(fit)


def themed_container(window, padding=18):
    if getattr(window.winfo_toplevel(), 'cutting_edge', False):
        area = ScrollArea(window)
        area.pack(fill='both', expand=True)
        area.content.configure(padding=padding)
        return area.content
    return ttk.Frame(window, padding=padding)


def theme_text(widget):
    widget.configure(background=BG, foreground=TEXT, insertbackground=TEXT,
                     selectbackground=ACCENT, selectforeground=BG, relief='flat', highlightthickness=1,
                     highlightbackground=EDGE, highlightcolor=ACCENT)


def responsive_size(width, height, available_width, available_height):
    return max(320, min(width, available_width - 24)), max(240, min(height, available_height - 48))


def work_area_geometry(client_width, client_height, outer, work):
    """Keep native decorations and the client inside this monitor's work area."""
    left, top, right, bottom = outer
    x0, y0, x1, y1 = work
    border_w = max(0, right-left-client_width)
    border_h = max(0, bottom-top-client_height)
    width = max(1, min(client_width, x1-x0-border_w))
    height = max(1, min(client_height, y1-y0-border_h))
    x = max(x0, min(left, x1-width-border_w))
    y = max(y0, min(top, y1-height-border_h))
    return width, height, x, y, border_w, border_h


def fit_window_to_work_area(window):
    """Query only our window's monitor; no DPI, desktop or focus changes."""
    if os.name != 'nt' or not window.winfo_exists():
        return
    import ctypes
    from ctypes import wintypes as wt
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wt.DWORD), ('monitor', wt.RECT), ('work', wt.RECT), ('flags', wt.DWORD)]
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetAncestor.argtypes, user.GetAncestor.restype = (wt.HWND, wt.UINT), wt.HWND
    user.MonitorFromWindow.argtypes, user.MonitorFromWindow.restype = (wt.HWND, wt.DWORD), wt.HANDLE
    user.GetMonitorInfoW.argtypes = (wt.HANDLE, ctypes.POINTER(MonitorInfo))
    user.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
    user.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
    window.update_idletasks()
    hwnd = user.GetAncestor(window.winfo_id(), 2)
    info, outer = MonitorInfo(), wt.RECT()
    info.size = ctypes.sizeof(info)
    if not user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), ctypes.byref(info)) or not user.GetWindowRect(hwnd, ctypes.byref(outer)):
        return
    geometry = work_area_geometry(window.winfo_width(), window.winfo_height(),
                                  (outer.left, outer.top, outer.right, outer.bottom),
                                  (info.work.left, info.work.top, info.work.right, info.work.bottom))
    width, height, x, y, border_w, border_h = geometry
    minimum = window.minsize()
    window.minsize(min(minimum[0], width), min(minimum[1], height))
    user.SetWindowPos(hwnd, None, x, y, width+border_w, height+border_h, 0x0010 | 0x0004)


class ScrollArea(ttk.Frame):
    """Scroll only this page, never bind wheel events globally (or to games)."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=PANEL, highlightthickness=0)
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        horizontal.pack(side='bottom', fill='x')
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set, xscrollcommand=horizontal.set)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = ttk.Frame(self.canvas, padding=12)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', self.resize)
        self.canvas.bind('<Configure>', self.resize)
        self.wheel_owner = self.winfo_toplevel()
        self.wheel_binding = self.wheel_owner.bind('<MouseWheel>', self.wheel, add='+')

    def wheel(self, event):
        widget = event.widget
        if widget.winfo_class() in ('Text', 'TCombobox', 'TSpinbox'):
            return
        while widget is not None and widget is not self:
            widget = getattr(widget, 'master', None)
        if widget is self:
            self.canvas.yview_scroll(-int(event.delta / 120), 'units')

    def destroy(self):
        if self.wheel_owner.winfo_exists():
            self.wheel_owner.unbind('<MouseWheel>', self.wheel_binding)
        super().destroy()

    def resize(self, event=None):
        self.canvas.itemconfigure(self.item, width=max(self.canvas.winfo_width(), self.content.winfo_reqwidth()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))


class BorderDecoration(tk.Canvas):
    """Vector-only perimeter decoration; timed callbacks touch arc colors only."""
    def __init__(self, parent, *, corner='left'):
        super().__init__(parent, background=BG, highlightthickness=0, height=18)
        self.after_id = None
        self.enabled = False
        self.ticks = 0
        self.corner = corner
        self.bind('<Configure>', self.draw)

    def draw(self, event=None):
        self.delete('all')
        width = self.winfo_width()
        for offset in (0, 8, 16):
            self.create_line(60+offset, 18, 76+offset, 3, 200+offset, 3, 215+offset, 18, fill=EDGE)
            self.create_line(width-220-offset, 18, width-205-offset, 3, width-75-offset, 3,
                             width-60-offset, 18, fill=EDGE)
        for x in range(245, max(245, width-245), 9):
            for y in (5, 12):
                self.create_oval(x, y, x+1, y+1, outline='#1E4351')
        if self.corner == 'left':
            self.create_line(0, 15, 9, 10, 16, 13, 22, 4, 30, 7, 39, 1, fill='#387A8C', tags='arc')
            self.create_line(16, 13, 20, 17, 26, 15, fill='#387A8C', tags='arc')
        else:
            self.create_line(width-42, 17, width-33, 10, width-26, 13, width-18, 5,
                             width-10, 8, width-1, 1, fill='#387A8C', tags='arc')

    def set_active(self, value):
        value = bool(value)
        self.enabled = value
        if not value:
            if self.after_id is not None:
                self.after_cancel(self.after_id)
                self.after_id = None
            self.itemconfigure('arc', fill='#387A8C')
        elif self.after_id is None:
            self.tick()

    def tick(self):
        self.after_id = None
        if not self.enabled:
            return
        level = (1 + math.sin(time.monotonic() * 2 * math.pi / 6)) / 2
        self.itemconfigure('arc', fill='#%02x%02x%02x' % (round(38+20*level), round(93+42*level), round(108+45*level)))
        self.ticks += 1
        self.after_id = self.after(100, self.tick)

    def destroy(self):
        self.set_active(False)
        super().destroy()
