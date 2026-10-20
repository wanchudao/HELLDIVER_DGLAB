"""Main-edition presentation only; the App retains all session ownership."""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from src.ui_theme import (BG, PANEL, ACCENT, MUTED, TEXT, ERROR, BorderDecoration,
                          ScrollArea, UiSettingsStore, install_theme, theme_text, responsive_size,
                          fit_window_to_work_area)
from src.settings_dialog import FeedbackSettingsEditor


class DesktopView:
    def __init__(self, app, base):
        self.app, self.root = app, app.window
        install_theme(self.root)
        self.preferences = UiSettingsStore(base / 'ui_settings.ini')
        self.animation = tk.BooleanVar(value=self.preferences.animation)
        self.current = '任务控制'
        self.status_reader = None
        self.root.title('HELLDIVER / DGLAB · 独立主版本 v1.0.0 Beta 3')
        width, height = responsive_size(1280, 820, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(min(860, width), min(560, height))
        self.root.after_idle(lambda: fit_window_to_work_area(self.root))
        self.top_border = BorderDecoration(self.root)
        self.top_border.pack(fill='x')
        header = ttk.Frame(self.root, padding=(20, 7), style='Background.TFrame')
        header.pack(fill='x')
        tk.Label(header, text='HELLDIVER / DGLAB', bg=BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 21, 'bold')).pack(side='left')
        tk.Label(header, text='独立主版本 · 官方后端', bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 10)).pack(side='left', padx=16)
        ttk.Checkbutton(header, text='待机电弧动画', variable=self.animation,
                        command=self.save_animation).pack(side='right')
        self.footer = ttk.Frame(self.root, padding=(14, 7))
        self.footer.pack(side='bottom', fill='x')
        self.bottom_border = BorderDecoration(self.root, corner='right')
        self.bottom_border.pack(side='bottom', fill='x')
        self.stop_footer = ttk.Button(self.footer, text='停止全部', style='Danger.TButton',
                                      command=app.cancel.set, state='disabled')
        self.stop_footer.pack(side='right', padx=5)
        self.output_status = tk.StringVar(value='软件输出 A -- / B --   |   HP --   |   未开始')
        ttk.Label(self.footer, textvariable=self.output_status).pack(side='right', padx=8)
        app.hud_hint = tk.StringVar()
        ttk.Label(self.footer, textvariable=app.hud_hint, style='Muted.TLabel').pack(side='left')
        self.body = ttk.Frame(self.root, style='Background.TFrame', padding=(12, 2))
        self.body.pack(fill='both', expand=True)
        nav = ttk.Frame(self.body, padding=(4, 8))
        nav.pack(side='left', fill='y', padx=(0, 8))
        self.buttons = {}
        self.preferences_warning = None
        for name in ('任务控制', '反馈设置', 'HUD 设置', '截图检查', '使用指南', '连接权限'):
            button = ttk.Button(nav, text=name, style='Nav.TButton', width=13,
                                command=lambda name=name: self.navigate(name))
            button.pack(fill='x', pady=4)
            self.buttons[name] = button
        ttk.Label(nav, text='F8 开始 / 暂停\n停止后修改设置', style='Muted.TLabel').pack(side='bottom', pady=15)
        self.area = ScrollArea(self.body)
        self.area.pack(fill='both', expand=True)
        self.home = ttk.Frame(self.area.content)
        self.home.pack(fill='both', expand=True)
        self.connection = ttk.LabelFrame(self.home, text='01  连接与部署', padding=14)
        self.feedback = ttk.LabelFrame(self.home, text='02  反馈配置')
        self.connection.grid(row=0, column=0, sticky='new', padx=(0, 10))
        self.feedback.grid(row=0, column=1, sticky='new')
        self.home.columnconfigure(1, weight=1)
        ttk.Label(self.connection, text='游戏所在屏幕').pack(anchor='w', pady=5)
        app.display = ttk.Combobox(self.connection, state='readonly', width=27)
        app.display.pack(fill='x', pady=(0, 10))
        ttk.Label(self.connection, text='手机连接地址').pack(anchor='w', pady=5)
        app.network = ttk.Combobox(self.connection, state='readonly', width=27)
        app.network.pack(fill='x')
        app.refresh = ttk.Button(self.connection, text='重新检测网卡', command=app.refresh_networks)
        app.refresh.pack(fill='x', pady=6)
        app.network_hint = tk.StringVar(value='正在自动读取本机网卡…')
        ttk.Label(self.connection, textvariable=app.network_hint, wraplength=280,
                  style='Muted.TLabel').pack(anchor='w', pady=5)
        app.status = tk.StringVar(value='正在读取显示器…')
        ttk.Label(self.connection, textvariable=app.status, wraplength=280).pack(anchor='w', pady=8)
        app.qr = ttk.Label(self.connection, text='连接后显示配对码', anchor='center')
        app.qr.pack(fill='x', pady=10, ipady=10)
        app.start = ttk.Button(self.connection, text='连接 APP', style='Accent.TButton', command=lambda: app.begin('dglab'))
        app.start.pack(fill='x', pady=5)
        app.mock = ttk.Button(self.connection, text='仅测试识别', command=lambda: app.begin('mock'))
        app.mock.pack(fill='x', pady=5)
        app.stop = ttk.Button(self.connection, text='停止全部', style='Danger.TButton', command=app.cancel.set, state='disabled')
        app.stop.pack(fill='x', pady=5)
        ttk.Label(self.connection, text='进入战场后按 F8 开始 / 暂停\n默认安全锁定；ESC 留给游戏。',
                  wraplength=280, style='Muted.TLabel').pack(anchor='w', pady=12)
        self.editor = FeedbackSettingsEditor(self.feedback, app.settings_store, app.is_running, self.settings_saved, compact=True)
        self.page = ttk.Frame(self.area.content)
        self.page_name = None
        self.hud_editor = None
        self.permissions_editor = None
        self.image_picker = None
        logs = ttk.Frame(self.body)
        # Log widget remains available to the App even while its panel is collapsed.
        self.logs = logs
        app.output = scrolledtext.ScrolledText(logs, height=6, font=('Consolas', 9), state='disabled')
        theme_text(app.output)
        app.output.pack(fill='both', expand=True)
        ttk.Button(nav, text='展开 / 收起日志', command=self.toggle_logs, width=13).pack(side='bottom', pady=4)
        app.image_check = self.buttons['截图检查']
        app.permissions_button = self.buttons['连接权限']
        self.area.canvas.bind('<Configure>', self.layout, add='+')
        self.root.bind('<Unmap>', lambda e: self.update_animation() if e.widget == self.root else None, add='+')
        self.root.bind('<Map>', lambda e: self.update_animation() if e.widget == self.root else None, add='+')
        self.buttons['任务控制'].configure(style='Selected.Nav.TButton')
        if self.preferences.error:
            app.log_lines.append('界面设置读取失败，已回退静态：' + self.preferences.error)
            self.preferences_warning = tk.Label(nav, text='动画设置读取失败\n已回退静态模式',
                                                 bg=PANEL, fg=ERROR, wraplength=140)
            self.preferences_warning.pack(side='bottom', pady=8)

    def settings_saved(self):
        self.app.status.set('反馈设置已保存，下次连接生效')
        self.app.update_controls()

    def confirm_edits(self):
        if not self.editor.is_dirty():
            return True
        choice = messagebox.askyesnocancel('未保存反馈设置', '保存修改？\n是：保存；否：放弃；取消：继续编辑。', parent=self.root)
        if choice is None:
            return False
        if choice:
            return self.editor.save()
        self.editor.discard()
        return True

    def save_animation(self):
        try:
            self.preferences.save(bool(self.animation.get()))
            if self.preferences_warning is not None:
                self.preferences_warning.pack_forget()
        except OSError as exc:
            self.animation.set(self.preferences.animation)
            messagebox.showerror('界面设置未保存', str(exc), parent=self.root)
        self.update_animation()

    def update_animation(self):
        active = (self.animation.get() and self.current == '任务控制' and not self.app.is_running()
                  and not self.app.network_busy and self.root.state() == 'normal' and not self.app.closing)
        self.top_border.set_active(active)
        self.bottom_border.set_active(active)

    def layout(self, event=None):
        width = self.area.canvas.winfo_width()
        if self.current == '任务控制':
            self.connection.grid(row=0, column=0, sticky='new', padx=(0, 10) if width >= 1010 else 0)
            self.feedback.grid(row=0 if width >= 1010 else 1, column=1 if width >= 1010 else 0, sticky='new', pady=(0, 12))
            self.home.columnconfigure(0, weight=0 if width >= 1010 else 1)
            self.home.columnconfigure(1, weight=1 if width >= 1010 else 0)
        elif self.current == '反馈设置':
            self.connection.grid_remove()
            self.feedback.grid(row=0, column=0, sticky='new')
            self.home.columnconfigure(0, weight=1)
            self.home.columnconfigure(1, weight=0)

    def navigate(self, name):
        if name != self.current and not self.confirm_edits():
            return
        if self.app.permissions_busy:
            self.app.status.set('权限操作仍在进行，请先完成或取消管理员授权。')
            return
        self.current = name
        for key, button in self.buttons.items():
            button.configure(style='Selected.Nav.TButton' if key == name else 'Nav.TButton')
        if name in ('任务控制', '反馈设置'):
            self.page.pack_forget()
            self.home.pack(fill='both', expand=True)
            self.layout()
        else:
            self.home.pack_forget()
            self.page.pack(fill='both', expand=True)
            if (self.page_name != name or not self.page.winfo_children()
                    or name == '连接权限' and self.permissions_editor and self.permissions_editor.closed):
                if self.hud_editor is not None:
                    self.hud_editor.close()
                    self.hud_editor = None
                if self.permissions_editor is not None:
                    self.permissions_editor.close()
                    self.permissions_editor = None
                for child in self.page.winfo_children():
                    child.destroy()
                self.page_name = name
                self.build_page(name)
        self.area.canvas.yview_moveto(0)
        self.update_animation()

    def build_page(self, name):
        app = self.app
        if name == 'HUD 设置':
            from src.hud_dialog import HudSettingsDialog
            self.hud_editor = HudSettingsDialog(self.page, app.hud_store, app.is_running,
                                               lambda: (app.update_hud_hint(), app.update_controls()), embedded=True)
        elif name == '连接权限':
            from src.firewall_dialog import FirewallDialog
            def busy(value):
                app.permissions_busy = value
                app.update_controls()
            self.permissions_editor = FirewallDialog(self.page, app.session_active, busy, embedded=True)
            app.permissions_dialog = self.permissions_editor
            self.permissions_editor.close_button.configure(command=lambda: self.navigate('任务控制'))
        elif name == '使用指南':
            from src.desktop_runtime import guide_text
            ttk.Label(self.page, text='使用指南', style='Title.TLabel').pack(anchor='w', pady=10)
            content = scrolledtext.ScrolledText(self.page, wrap='word', height=32, font=('Microsoft YaHei UI', 11))
            theme_text(content)
            content.pack(fill='both', expand=True)
            content.insert('1.0', guide_text())
            content.configure(state='disabled')
        elif name == '截图检查':
            ttk.Label(self.page, text='截图检查', style='Title.TLabel').pack(anchor='w', pady=10)
            ttk.Label(self.page, text='选择本地原始截图，无需连接 APP 或启动后端。\n使用已保存的 HUD 档位；单帧结果不代表事件测试。', wraplength=600).pack(anchor='w', pady=12)
            # Keep the persistent button outside a page that is recreated.
            self.image_picker = ttk.Button(self.page, text='选择原始截图', command=app.check_screenshots)
            self.image_picker.pack(anchor='w', pady=12)
            self.update_controls()

    def toggle_logs(self):
        if self.logs.winfo_manager():
            self.logs.pack_forget()
        else:
            self.logs.pack(side='bottom', fill='x', before=self.area)

    def update_controls(self):
        self.editor.update_enabled()
        self.stop_footer.configure(state='normal' if self.app.thread and self.app.thread.is_alive() else 'disabled')
        if self.image_picker is not None and self.image_picker.winfo_exists():
            self.image_picker.configure(state='disabled' if self.app.is_running() or self.app.hud_store.error else 'normal')
        self.update_animation()

    def poll_status(self):
        if self.status_reader is None:
            return
        record = self.status_reader.read()
        if record is None:
            state = '等待识别状态' if self.status_reader.sequence == 0 else '状态更新中断'
            self.output_status.set('软件输出 A -- / B --   |   HP --   |   ' + state)
            return
        hp = f"{record['hp']:.0%}" if record['valid'] and not record['paused'] else '--'
        status = '暂停' if record['paused'] else '运行' if record['valid'] else 'NO HUD'
        if record.get('death_marked'):
            status += (' / 挑战已触发：F8 重置并暂停' if record.get('death_feedback_mode') == 'until_reset'
                       else ' / 死亡持续至复活')
        if record['mode'] == 'dglab' and not record['connected']:
            status = 'APP 未连接 / 输出未确认'
        if record.get('resolution'):
            status += ' / ' + record['resolution']
        if record.get('hud_source') == 'resolution_extrapolated':
            status += ' 推算·未实机验收'
        values = record['strengths']
        self.output_status.set(f"软件输出 A {values['A'] if values['A'] is not None else '--'} / B {values['B'] if values['B'] is not None else '--'}   |   HP {hp}   |   {status}")

    def close(self):
        self.top_border.set_active(False)
        self.bottom_border.set_active(False)
        if self.hud_editor is not None:
            self.hud_editor.close()
        if self.permissions_editor is not None:
            self.permissions_editor.close()
