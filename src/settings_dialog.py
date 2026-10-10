"""Tk feedback preferences and offline guide, separate from session ownership."""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

from src.feedback_settings import EVENTS, default_settings, default_trigger_settings, THRESHOLD_KEY
from src.waveforms import WAVEFORM_NAMES


class FeedbackSettingsEditor:
    def __init__(self, parent, store, is_running, on_saved=lambda: None, *, compact=False):
        self.store, self.is_running, self.on_saved = store, is_running, on_saved
        self.window = parent.winfo_toplevel()
        from src.ui_theme import themed_container
        outer = self.frame = themed_container(parent, padding=12) if isinstance(parent, tk.Toplevel) else ttk.Frame(parent, padding=12)
        outer.pack(fill="both", expand=True)
        if not compact:
            ttk.Label(outer, text="反馈设置", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        self.mode_labels = {"仅 A": "a", "仅 B": "b", "A+B 双通道": "both"}
        self.mode = tk.StringVar()
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(8, 6) if compact else (14, 10))
        ttk.Label(row, text="输出通道：").pack(side="left")
        self.mode_box = ttk.Combobox(row, values=list(self.mode_labels), textvariable=self.mode,
                                    state="readonly", width=14)
        self.mode_box.pack(side="left")
        self.mode_box.bind("<<ComboboxSelected>>", lambda event: self.update_enabled())
        ttk.Label(outer, text="双通道共用波形，强度分别计算").pack(anchor='w', pady=3)
        table = ttk.Frame(outer)
        table.pack(fill="x")
        for column, title in enumerate(("反馈类型", "波形模板", "A 强度", "B 强度")):
            ttk.Label(table, text=title).grid(row=0, column=column, sticky="w", padx=5, pady=5)
        self.variables, self.wave_boxes, self.strength_boxes = {}, [], {"A": [], "B": []}
        self.wave_ids = {label: key for key, label in WAVEFORM_NAMES.items()}
        for index, (event, label, *_rest) in enumerate(EVENTS, 1):
            ttk.Label(table, text=label).grid(row=index, column=0, sticky="w", padx=5, pady=6 if compact else 8)
            wave_var = self.variables[f"{event}_waveform"] = tk.StringVar()
            box = ttk.Combobox(table, values=list(self.wave_ids), textvariable=wave_var, state="readonly", width=18 if compact else 25)
            box.grid(row=index, column=1, sticky="ew", padx=5)
            self.wave_boxes.append(box)
            for column, channel, suffix in ((2, "A", ""), (3, "B", "_b")):
                variable = self.variables[f"{event}_strength{suffix}"] = tk.StringVar()
                spin = ttk.Spinbox(table, from_=0, to=100, textvariable=variable, width=5 if compact else 8)
                spin.grid(row=index, column=column, padx=5)
                self.strength_boxes[channel].append(spin)
        table.columnconfigure(1, weight=1)
        threshold = ttk.Frame(outer)
        threshold.pack(fill="x", pady=(12, 0))
        ttk.Label(threshold, text="残血／濒死触发血量：").pack(side="left", padx=5)
        self.threshold = tk.StringVar()
        self.threshold_box = ttk.Spinbox(threshold, from_=0, to=100, increment=1,
                                        textvariable=self.threshold, width=8)
        self.threshold_box.pack(side="left")
        ttk.Label(threshold, text="%（0% 关闭此反馈）").pack(side="left", padx=5)
        ttk.Label(outer, text="低于此血量持续反馈；这是血量门槛，不是 A/B 输出强度。", wraplength=510 if compact else 730).pack(anchor="w", pady=4)
        limits = ttk.Frame(outer)
        limits.pack(fill="x", pady=12)
        for channel, suffix in (("A", ""), ("B", "_b")):
            ttk.Label(limits, text=f"{channel} 总强度上限：").pack(side="left", padx=(5, 0))
            variable = self.variables[f"max_strength{suffix}"] = tk.StringVar()
            spin = ttk.Spinbox(limits, from_=0, to=100, textvariable=variable, width=8)
            spin.pack(side="left", padx=(0, 20))
            self.strength_boxes[channel].append(spin)
        self.hint = tk.StringVar()
        ttk.Label(outer, textvariable=self.hint, wraplength=510 if compact else 730).pack(anchor="w", pady=6)
        ttk.Label(outer, text="受伤实际强度 = 基准 × 累计掉血比例；各反馈可叠加但不超过各路上限。\n强度 0 表示不贡献该项强度。残血门槛默认 75%，可自定义；等于门槛不触发。",
                  wraplength=510 if compact else 730).pack(anchor="w", pady=6)
        buttons = ttk.Frame(outer)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        self.save_button = ttk.Button(buttons, text="保存设置", command=self.save)
        self.save_button.configure(style='Accent.TButton' if getattr(self.window, 'cutting_edge', False) else 'TButton')
        self.save_button.pack(side="right")
        self.default_button = ttk.Button(buttons, text="恢复默认",
                                        command=lambda: self.fill(default_settings(), default_trigger_settings()))
        self.default_button.pack(side="left")
        self.fill(store.values, store.trigger_values, clean=True)

    def fill(self, values, trigger_values=None, *, clean=False):
        self.threshold.set(str((trigger_values or default_trigger_settings())[THRESHOLD_KEY]))
        self.mode.set(next(label for label, mode in self.mode_labels.items() if mode == values["channel_mode"]))
        for key, var in self.variables.items():
            var.set(WAVEFORM_NAMES[values[key]] if key.endswith("_waveform") else str(values[key]))
        self.update_enabled()
        if clean:
            self.baseline = self.edit_snapshot()

    def edit_snapshot(self):
        return (self.mode.get(), self.threshold.get(), tuple((key, var.get()) for key, var in self.variables.items()))

    def is_dirty(self):
        return self.edit_snapshot() != self.baseline

    def discard(self):
        self.fill(self.store.values, self.store.trigger_values, clean=True)

    def update_enabled(self):
        running = self.is_running()
        mode = self.mode_labels[self.mode.get()]
        self.mode_box.configure(state="disabled" if running else "readonly")
        self.threshold_box.configure(state="disabled" if running else "normal")
        for box in self.wave_boxes:
            box.configure(state="disabled" if running else "readonly")
        for ch, boxes in self.strength_boxes.items():
            enabled = not running and (mode == "both" or mode == ch.lower())
            for box in boxes:
                box.configure(state="normal" if enabled else "disabled")
        for button in (self.save_button, self.default_button):
            button.configure(state="disabled" if running else "normal")
        if running:
            self.hint.set("当前为只读：请关闭本窗口并点击主界面的停止全部后再修改。F8 暂停不会解锁设置。")
        elif self.store.error:
            self.hint.set("原设置读取失败，已显示待确认默认值；请检查并保存。" + self.store.error)
        else:
            self.hint.set("保存后下次连接生效；关闭窗口不会保存未确认的改动。")

    def save(self):
        try:
            values = {key: self.wave_ids[var.get()] if key.endswith("_waveform") else var.get()
                      for key, var in self.variables.items()}
            values["channel_mode"] = self.mode_labels[self.mode.get()]
            self.store.save(values, running=self.is_running(), trigger_settings={THRESHOLD_KEY: self.threshold.get()})
            self.on_saved()
            self.baseline = self.edit_snapshot()
            self.hint.set("已保存。下次连接生效，无需再次设置。")
            return True
        except (OSError, ValueError, KeyError) as error:
            messagebox.showerror("设置未保存", str(error), parent=self.window)
            return False


class FeedbackSettingsDialog(FeedbackSettingsEditor):
    """Compatibility entry point; the same editor also lives on the main page."""
    def __init__(self, parent, store, is_running, on_saved=lambda: None):
        window = tk.Toplevel(parent)
        window.title("反馈设置 · 波形 / A、B 强度")
        window.geometry("820x590")
        window.minsize(790, 570)
        window.transient(parent)
        window.grab_set()
        from src.ui_theme import theme_window
        theme_window(window, parent)
        super().__init__(window, store, is_running, on_saved)
        ttk.Button(self.frame, text="关闭", command=self.close).pack(side="bottom", pady=5)
        window.protocol('WM_DELETE_WINDOW', self.close)

    def close(self):
        if self.is_dirty() and not self.is_running():
            choice = messagebox.askyesnocancel('未保存设置', '保存修改后关闭？\n是：保存；否：放弃；取消：继续编辑。', parent=self.window)
            if choice is None or choice and not self.save():
                return
        self.window.destroy()


def open_guide(parent, text):
    window = tk.Toplevel(parent)
    window.title("使用指南 · HELLDIVER 主版本")
    window.geometry("850x660")
    window.minsize(650, 450)
    window.transient(parent)
    from src.ui_theme import theme_window, theme_text
    theme_window(window, parent)
    content = scrolledtext.ScrolledText(window, wrap="word", font=("Microsoft YaHei UI", 11), padx=16, pady=12)
    content.pack(fill="both", expand=True)
    content.insert("1.0", text)
    content.configure(state="disabled")
    if getattr(window, 'cutting_edge', False):
        theme_text(content)
    ttk.Button(window, text="关闭", command=window.destroy).pack(pady=8)
    return window
