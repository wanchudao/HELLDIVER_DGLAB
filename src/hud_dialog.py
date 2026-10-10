"""HUD preferences and an offline, report-only original-image viewer."""
import math
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from src.hud_settings import SCALES, default_settings
from src.hud_profiles import source_label


LEGACY_LABEL = "旧版兼容预设，尚未选择 HUD 档位"
HUD_CHOICES = (LEGACY_LABEL,) + tuple(f"{scale / 100:.2f}" for scale in SCALES)


def selected_values(label):
    if label == LEGACY_LABEL:
        return default_settings()
    for scale in SCALES:
        if label == f"{scale / 100:.2f}":
            return {"mode": "manual", "scale_percent": scale}
    raise ValueError("请选择列表中的 HUD 档位")


class HudSettingsDialog:
    def __init__(self, parent, store, is_running, on_saved=lambda: None, *, embedded=False):
        self.store, self.is_running, self.on_saved = store, is_running, on_saved
        self.window = ttk.Frame(parent) if embedded else tk.Toplevel(parent)
        from src.ui_theme import theme_window
        if embedded:
            self.window.pack(fill='both', expand=True)
        else:
            theme_window(self.window, parent)
            self.window.title("HUD 设置 · 1080p 血条适配")
            self.window.geometry("690x340")
            self.window.minsize(630, 320)
            self.window.transient(parent)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = None
        self._last_running = None
        from src.ui_theme import themed_container
        outer = themed_container(self.window) if not embedded else ttk.Frame(self.window, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="HUD 设置", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(outer, text="这里选择的是游戏 HUD 大小，不是分辨率或 Windows 缩放。",
                  wraplength=640).pack(anchor="w", pady=(10, 12))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Label(row, text="游戏 HUD 大小：").pack(side="left")
        self.selection = tk.StringVar(value=LEGACY_LABEL if store.values["mode"] == "legacy"
                                      else f'{store.values["scale_percent"] / 100:.2f}')
        self.scale_box = ttk.Combobox(row, values=HUD_CHOICES, textvariable=self.selection,
                                     state="readonly", width=40)
        self.scale_box.pack(side="left", padx=5)
        self.scale_box.bind("<<ComboboxSelected>>", lambda event: self.update_source())
        self.source = tk.StringVar()
        ttk.Label(outer, textvariable=self.source, wraplength=640).pack(anchor="w", pady=12)
        self.hint = tk.StringVar()
        ttk.Label(outer, textvariable=self.hint, wraplength=640).pack(anchor="w", pady=8)
        buttons = ttk.Frame(outer)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        if not embedded:
            ttk.Button(buttons, text="关闭", command=self.close).pack(side="right")
        self.save_button = ttk.Button(buttons, text="保存设置", command=self.save)
        self.save_button.pack(side="right", padx=10)
        self.update_source()
        self.update_enabled()
        self._poll_running()

    def update_source(self):
        values = selected_values(self.selection.get())
        if values["mode"] == "legacy":
            self.source.set("旧版兼容预设：保持原有识别位置，未将历史坐标解释为 HUD 1.00。")
        else:
            self.source.set(f"适用画面：1920×1080，游戏完整铺满所选屏幕。\n"
                            f"来源：{source_label(values['scale_percent'])}。")

    def update_enabled(self):
        running = bool(self.is_running())
        self._last_running = running
        self.scale_box.configure(state="disabled" if running else "readonly")
        self.save_button.configure(state="disabled" if running else "normal")
        if running:
            self.hint.set("当前为只读：请关闭本窗口并点击主界面的停止全部后再修改。F8 暂停不会解锁设置。")
        elif self.store.error:
            self.hint.set("原设置读取失败，已显示待确认兼容值；请检查并保存。" + self.store.error)
        else:
            self.hint.set("请选择与游戏相同的档位。保存后下次启动会话生效；关闭窗口不保存未确认的改动。")

    def _poll_running(self):
        if bool(self.is_running()) != self._last_running:
            self.update_enabled()
        self._after_id = self.window.after(300, self._poll_running)

    def save(self):
        try:
            self.store.save(selected_values(self.selection.get()), running=self.is_running())
            self.on_saved()
            self.hint.set("已保存，下次启动会话生效。")
        except (OSError, ValueError) as error:
            messagebox.showerror("HUD 设置未保存", str(error), parent=self.window)

    def close(self):
        if not self.window.winfo_exists():
            return
        if self._after_id is not None:
            self.window.after_cancel(self._after_id)
            self._after_id = None
        self.window.destroy()


def _percent(value):
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1:
        return f"{value:.1%}"
    return "--"


def diagnostic_summary(record):
    """Never turn an invalid HUD's placeholder zero into a health reading."""
    hud_valid = record.get("hud_valid") is True
    raw = _percent(record.get("health_raw")) if hud_valid else "--（定位无效，候选读数不作血量）"
    scale = record.get("hud_scale")
    if type(scale) is int and scale in SCALES:
        scale_text = f"{scale / 100:.2f}"
    else:
        scale_text = LEGACY_LABEL if scale is None else str(scale)
    source = record.get("hud_source") or "旧版兼容预设"
    reason = record.get("reject_reason") or record.get("first_frame_reason") or ""
    reason_labels = {"observation_warmup": "单帧尚未完成连续确认（正常预热）",
                     "hud_geometry_mismatch": "HUD 位置或大小与所选档位不符",
                     "hud_missing": "未找到有效 HUD",
                     "hud_cap_missing": "未找到完整的左右端帽，请核对游戏 HUD 档位或遮挡",
                     "hud_cap_ambiguous": "端帽结构有歧义，当前画面拒识",
                     "hud_cap_position_mismatch": "端帽位置与所选 HUD 档位不符",
                     "hud_cap_center_mismatch": "端帽高度位置与所选 HUD 档位不符",
                     "hud_cap_spacing_mismatch": "血槽长度与所选 HUD 档位不符",
                     "hud_frame_size_mismatch": "图片尺寸与 1920×1080 配置不符",
                     "resolution_mismatch": "图片尺寸与 1920×1080 配置不符",
                     "frame_resolution_mismatch": "图片尺寸与 1920×1080 配置不符"}
    return (f"实际图片尺寸：{record.get('resolution', '--')}\n"
            f"当前 HUD：{scale_text}　来源：{source}\n"
            f"HUD 定位：{'通过' if hud_valid else '拒识'}　原始血量估计：{raw}\n"
            f"置信度：{_percent(record.get('confidence'))}　端帽得分：{_percent(record.get('anchor_score'))}\n"
            f"首帧有效：{'是' if record.get('first_frame_valid') is True else '否'}\n"
            f"原因：{reason_labels.get(reason, reason) or '无'}")


def _coords(value):
    if (isinstance(value, (list, tuple)) and len(value) == 4
            and all(type(item) is int for item in value)
            and value[0] < value[2] and value[1] < value[3]):
        return tuple(value)
    return None


def diagnostic_crop(path, record, *, max_size=(920, 260)):
    """Draw only coordinates already supplied by the production diagnostic."""
    outer = _coords(record.get("outer_coords"))
    inner = _coords(record.get("inner_coords"))
    if outer is None:
        raise ValueError("报告未提供测量框；尺寸不匹配或定位配置不可用")
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("截图文件过大")
    with Image.open(path) as original:
        if original.width * original.height > 40_000_000:
            raise ValueError("截图像素数量过大")
        left = max(0, outer[0] - 24)
        top = max(0, outer[1] - 16)
        right = min(original.width, outer[2] + 24)
        bottom = min(original.height, outer[3] + 16)
        if left >= right or top >= bottom:
            raise ValueError("测量框位于图片范围外")
        cropped = original.crop((left, top, right, bottom)).convert("RGB")
    drawing = ImageDraw.Draw(cropped)

    def rectangle(coords, color):
        x1, y1, x2, y2 = coords
        drawing.rectangle((x1 - left, y1 - top, x2 - 1 - left, y2 - 1 - top),
                          outline=color, width=1)

    rectangle(outer, "#00ddff")
    if inner is not None:
        rectangle(inner, "#ffb347")
        # These are reported calibration ranges, not newly detected endcaps.
        if outer[0] < inner[0]:
            rectangle((outer[0], outer[1], inner[0], outer[3]), "#ee66ff")
        if inner[2] < outer[2]:
            rectangle((inner[2], outer[1], outer[2], outer[3]), "#ee66ff")
        bounds = record.get("bar_bounds")
        if (isinstance(bounds, (list, tuple)) and len(bounds) == 2
                and all(type(item) is int for item in bounds) and bounds[0] < bounds[1]):
            rectangle((bounds[0], inner[1], bounds[1], inner[3]), "#4cff71")
    caps = record.get("cap_positions")
    if isinstance(caps, (list, tuple)) and len(caps) == 2:
        for x in caps:
            if type(x) is int:
                drawing.line((x - left, outer[1] - top, x - left, outer[3] - 1 - top),
                             fill="#ee66ff", width=1)
    scale = min(max_size[0] / cropped.width, max_size[1] / cropped.height, 6)
    enlarged = cropped.resize((max(1, round(cropped.width * scale)),
                               max(1, round(cropped.height * scale))), Image.Resampling.NEAREST)
    return enlarged, (left, top, right, bottom)


class ScreenshotCheckDialog:
    """Display a completed offline report without invoking analysis or networking."""
    def __init__(self, parent, report, paths):
        self.report, self.paths = report, list(paths)
        self.records = list(report.get("images", []))
        self.window = tk.Toplevel(parent)
        from src.ui_theme import theme_window
        theme_window(self.window, parent)
        self.window.title("截图检查 · 单帧血条定位")
        self.window.geometry("990x620")
        self.window.minsize(750, 520)
        self.window.transient(parent)
        self.window.grab_set()
        from src.ui_theme import themed_container
        outer = themed_container(self.window)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="截图检查", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        names = tuple(f"{index + 1}. {record.get('file', '截图')}" for index, record in enumerate(self.records))
        self.selection = tk.StringVar(value=names[0] if names else "无截图结果")
        self.image_box = ttk.Combobox(outer, values=names, textvariable=self.selection,
                                     state="readonly", width=80)
        self.image_box.pack(fill="x", pady=10)
        self.image_box.bind("<<ComboboxSelected>>", lambda event: self.show_record(self.image_box.current()))
        self.details = tk.StringVar()
        ttk.Label(outer, textvariable=self.details, wraplength=940,
                  justify="left").pack(anchor="w", pady=(0, 10))
        self.preview = ttk.Label(outer, anchor="center")
        self.preview.pack(fill="both", expand=True)
        ttk.Label(outer, text="青色：外框　橙色：采样内框　紫色：端帽校验区　绿色：有效血量测量区",
                  wraplength=940).pack(anchor="w", pady=6)
        ttk.Label(outer, text="这是原始图片的单帧检查。首帧可能处于预热；此结果不验证受伤、治疗或死亡事件。\n"
                  "仅为便于观看放大局部预览，识别使用原图像素；定位无效不表示死亡。",
                  wraplength=940).pack(anchor="w", pady=6)
        ttk.Button(outer, text="关闭", command=self.window.destroy).pack(anchor="e", pady=8)
        self.photo = None
        self.show_record(0)

    def show_record(self, index):
        if not 0 <= index < len(self.records):
            self.details.set("没有可显示的截图结果")
            return
        record = self.records[index]
        self.details.set(diagnostic_summary(record))
        path = record.get("path") or (self.paths[index] if index < len(self.paths) else "")
        try:
            preview, bounds = diagnostic_crop(path, record)
            self.photo = ImageTk.PhotoImage(preview)
            self.preview.configure(image=self.photo, text="")
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            self.photo = None
            self.preview.configure(image="", text=f"局部预览不可用：{error}")
