"""Explicit consent UI. No permission changes occur on open or refresh."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from src import firewall_access as access


class FirewallDialog:
    def __init__(self, parent, is_running, on_busy=lambda value: None, *, service=access, embedded=False):
        self.service, self.is_running, self.on_busy = service, is_running, on_busy
        self.busy = False
        self.closed = False
        self.events = queue.Queue()
        self.window = ttk.Frame(parent) if embedded else tk.Toplevel(parent)
        from src.ui_theme import theme_window
        if embedded:
            self.window.pack(fill='both', expand=True)
        else:
            theme_window(self.window, parent)
            self.window.title("连接权限 · Windows 防火墙")
            self.window.geometry("780x420")
            self.window.minsize(730, 400)
            self.window.transient(parent)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self.close)
        from src.ui_theme import themed_container
        outer = themed_container(self.window) if not embedded else ttk.Frame(self.window, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="一键配置连接权限", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(outer, text="只放行本包 node.exe 的专用网络、本地子网 TCP 入站连接。\n"
                  "不会关闭防火墙、切换网络类型、开放公用网络或删除你的手动规则。",
                  wraplength=735).pack(anchor="w", pady=(10, 12))
        self.path = tk.StringVar()
        self.supported = False
        try:
            self.path.set(str(service.bundled_node()))
            self.supported = True
        except (OSError, RuntimeError) as error:
            self.path.set(str(error))
        ttk.Label(outer, text="实际后端路径（可选中复制）：").pack(anchor="w")
        ttk.Entry(outer, textvariable=self.path, state="readonly").pack(fill="x", pady=6)
        self.status = tk.StringVar(value="正在读取规则状态…" if self.supported else self.path.get())
        ttk.Label(outer, textvariable=self.status, wraplength=735).pack(anchor="w", pady=12)
        ttk.Label(outer, text="首次配置／撤销会请求 Windows 管理员授权，取消授权不修改规则。\n"
                  "请仅在自己信任的专用网络使用。访客隔离、其他拦截规则或网络故障仍可能阻止连接。\n"
                  "保持安装目录不变可复用规则；移动后需要在新目录重新配置。",
                  wraplength=735).pack(anchor="w")
        buttons = ttk.Frame(outer)
        buttons.pack(side="bottom", fill="x", pady=(14, 0))
        self.configure_button = ttk.Button(buttons, text="一键配置连接权限", command=lambda: self.request("configure"))
        self.configure_button.pack(side="left")
        self.remove_button = ttk.Button(buttons, text="撤销本程序规则", command=lambda: self.request("remove"))
        self.remove_button.pack(side="left", padx=8)
        self.refresh_button = ttk.Button(buttons, text="刷新状态", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.close_button = ttk.Button(buttons, text="关闭", command=self.close)
        if embedded:
            self.close_button.configure(text='返回任务控制')
        self.close_button.pack(side="right")
        self.update_enabled()
        self._after = self.window.after(80, self.poll)
        if self.supported:
            self.refresh()

    def update_enabled(self):
        enabled = self.supported and not self.busy and not self.is_running()
        for button in (self.configure_button, self.remove_button, self.refresh_button):
            button.configure(state="normal" if enabled else "disabled")
        self.close_button.configure(state="disabled" if self.busy else "normal")

    def _start(self, action):
        self.busy = True
        self.on_busy(True)
        self.update_enabled()
        def worker():
            try:
                self.events.put((True, action()))
            except Exception as error:
                self.events.put((False, str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        if self.busy or not self.supported or self.is_running():
            return
        self.status.set("正在读取规则状态（不会修改防火墙）…")
        self._start(lambda: self.service.status_text(self.service.query_status()))

    def request(self, operation):
        if operation not in access.OPERATIONS:
            raise ValueError("Unsupported firewall operation")
        if self.busy or not self.supported or self.is_running():
            return
        text = ("添加／修正本包后端的专用网络、本地子网 TCP 入站规则。\n"
                "仅此规则会被修改；接下来 Windows 会请求管理员授权。"
                if operation == "configure" else
                "仅撤销本按钮为当前后端路径创建的规则。\n"
                "原有手动规则保持不变；撤销不保证所有连接都被阻断。")
        if not messagebox.askyesno("确认连接权限操作", text, parent=self.window):
            return
        if self.is_running():
            return
        hwnd = self.window.winfo_toplevel().winfo_id()
        self.status.set("等待 Windows 授权／系统操作…可在授权窗口点“否”取消。")
        def action():
            self.service.authorize(operation, hwnd)
            value = self.service.query_status()
            if operation == "configure" and not value["matches"]:
                raise access.FirewallError("操作返回后未确认规则范围，请刷新状态或手动排查。")
            if operation == "remove" and value["present"]:
                raise access.FirewallError("操作返回后规则仍存在，不能确认已撤销。")
            prefix = "配置完成。" if operation == "configure" else "本按钮管理的规则已撤销；手动规则未删除。"
            return prefix + self.service.status_text(value)
        self._start(action)

    def poll(self):
        try:
            ok, value = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.on_busy(False)
            self.status.set(value if ok else "未确认完成：" + value)
        if not self.busy and self.is_running():
            self.status.set("请先停止全部；运行中不修改连接权限，F8 暂停不会解锁。")
        self.update_enabled()
        self._after = self.window.after(80, self.poll)

    def close(self):
        if self.busy or self.closed:
            return
        self.closed = True
        self.window.after_cancel(self._after)
        self.window.destroy()
