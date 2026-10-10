"""Standalone main version: official SOCKET v2 + APP pairing + live vision."""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.desktop_runtime import (
    ROOT, NO_WINDOW, OfficialBackend, backend_paths, make_config,
    worker_command, stop_worker,
)
from src.network_addresses import discover_network_addresses, validate_advertised_ipv4
from src.feedback_settings import SettingsStore, validate_settings, validate_trigger_settings
from src.hud_settings import HudSettingsStore, validate_settings as validate_hud_settings


def run_worker(config, session):
    """Windowed EXEs cannot rely on sys.stdin/stdout; use local session files."""
    session = Path(session)
    stopped, done = threading.Event(), threading.Event()
    output = (session / "runner.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = output
    from src import dglab_sender, trigger
    trigger.LOG_PATH = session / "trigger.jsonl"
    dglab_sender._DglabWSClient._show_qrcode = lambda self, url: print("HD_QR=" + url, flush=True)

    def watch_parent():
        while not done.wait(.1):
            try:
                expired = time.time() - (session / "heartbeat").stat().st_mtime > 4
            except OSError:
                expired = True
            if (session / "STOP").exists() or expired:
                stopped.set()
                # Repeat through an in-flight sender initialization until the
                # runner has left pairing/capture and performed its cleanup.
                dglab_sender.safe_stop()

    watcher = threading.Thread(target=watch_parent, daemon=True)
    watcher.start()
    from src.ui_status import StatusPublisher
    publisher = StatusPublisher(session / "ui_status.json", session.name)
    try:
        from src.runner_real import main as run
        run(["--config", str(config), "--enable-trigger", "--no-global-escape",
             "--require-output"], stop_event=stopped, status_sink=publisher.publish, panel_theme="cutting_edge")
        return 0
    except BaseException:
        traceback.print_exc()
        return 1
    finally:
        done.set()
        dglab_sender.safe_stop()
        publisher.close()
        watcher.join(timeout=2)
        output.flush()


def self_check():
    """No screen capture, sockets, APP or external image viewer."""
    import tempfile
    import qrcode
    from src.config_loader import load_config
    from src.state_machine import DetectionResult
    from src.trigger import TriggerDispatch
    from types import SimpleNamespace
    from src.feedback_settings import default_settings
    from src.feedback_mixer import MockFeedbackSender
    from src.waveforms import WAVEFORM_V3, validate_waveforms
    from src.desktop_runtime import guide_text
    validate_waveforms()
    if "防火墙" not in guide_text():
        raise RuntimeError("Bundled guide missing")
    node, backend = backend_paths()
    if not node.is_file() or not backend.is_file():
        raise RuntimeError("Bundled backend missing")
    from src.firewall_access import policy
    firewall_policy = policy(node)
    if firewall_policy['profile'] != 'Private' or firewall_policy['remote_address'] != 'LocalSubnet':
        raise RuntimeError('Invalid LAN permission scope')
    with tempfile.TemporaryDirectory(prefix="helldiver-selfcheck-") as folder:
        path = make_config(Path(folder) / "session.ini", 1, (1920,1080), "mock")
        now = [0.0]
        dispatch = TriggerDispatch(load_config(path), clock=lambda:now[0],
                                   log_writer=SimpleNamespace(append=lambda v:None, close=lambda:None))
        try:
            for timestamp, hp in [(0,1),(.1,.7),(.15,.6),(.31,.6)]:
                now[0] = timestamp
                dispatch.dispatch(DetectionResult(health_raw=hp, health_stable=hp, valid=True,
                                                  state="injured", confidence=1, anchor_score=1))
            if dispatch.mock_output()["strength"] != 18:
                raise RuntimeError("Shared SPEC check failed")
            dispatch.reset("selfcheck")
            if dispatch.mock_output()["strength"] != 0:
                raise RuntimeError("Stop check failed")
        finally:
            dispatch.safe_stop()
        make_config(Path(folder) / "selected-ip.ini", 1, (1920,1080), "dglab", qr_host="192.0.2.10")
        settings = SettingsStore(Path(folder) / "feedback_settings.ini")
        settings.save(default_settings() | {"channel_mode": "both", "low_health_strength_b": 6})
        settings = SettingsStore(settings.path)
        configured = make_config(Path(folder) / "feedback.ini", 1, (1920,1080), "mock",
                                 feedback_settings=settings.snapshot())
        mock = MockFeedbackSender(load_config(configured), clock=lambda: 1)
        out = mock.update_continuous(low_health=True, healing=False, hp=.5)["channels"]
        if out["A"]["strength"] != 10 or out["B"]["strength"] != 6:
            raise RuntimeError("Bundled dual-channel check failed")
        from src.hud_profiles import SCALES
        hud_store = HudSettingsStore(Path(folder)/"hud_settings.ini")
        if hud_store.snapshot()["mode"] != "legacy":
            raise RuntimeError("Legacy HUD compatibility check failed")
        for scale in SCALES:
            settings = {"mode":"manual","scale_percent":scale}
            hud_store.save(settings)
            restored = HudSettingsStore(hud_store.path).snapshot()
            if restored != settings:
                raise RuntimeError("HUD settings restore failed")
            configured = make_config(Path(folder)/f"hud-{scale}.ini",1,(1920,1080),"mock",
                                     hud_settings=restored)
            if load_config(configured)["hud"]["scale_percent"] != scale:
                raise RuntimeError("HUD selection did not reach the session")
        qr = qrcode.make("HELlDIVER local QR rendering check")
        if qr.size[0] <= 0:
            raise RuntimeError("QR generation failed")
    return {"status":"ok", "edition":"main-official-backend", "node_bundled":True,
            "qr":True, "ip_selection":True, "shared_feedback":True, "screen_capture_started":False,
            "app_connected":False, "physical_device_tested":False,
            "waveform_count":len(WAVEFORM_V3), "feedback_settings":True,
            "dual_channel":True, "offline_guide":True,"hud_settings":True,
            "hud_scales":list(SCALES),"firewall_opt_in":True,"firewall_scope":firewall_policy,
            "firewall_modified":False}


def inspect_images(paths, *, hud_settings=None):
    """Read originals with this EXE's production vision core, one observation each."""
    os.environ.setdefault('OPENCV_IO_MAX_IMAGE_PIXELS',str(40_000_000))
    import cv2
    import numpy as np
    import tempfile
    import hashlib
    from src.config_loader import load_config
    from src.vision_pipeline import VisionPipeline
    hud_settings = validate_hud_settings(hud_settings) if hud_settings is not None else None
    manual = hud_settings is not None and hud_settings["mode"] == "manual"
    profile = None
    if manual:
        from src.hud_profiles import get_profile, source_label
        profile = get_profile(hud_settings["scale_percent"])
    records=[]
    with tempfile.TemporaryDirectory(prefix='helldiver-image-check-') as folder:
        for index,path in enumerate(paths):
            path=Path(path)
            if path.stat().st_size>64*1024*1024:
                raise ValueError('截图文件过大')
            encoded=path.read_bytes()
            frame=cv2.imdecode(np.frombuffer(encoded,dtype=np.uint8),cv2.IMREAD_COLOR)
            if frame is None: raise ValueError(f'无法读取截图：{path.name}')
            height,width=frame.shape[:2]
            record = {'file':path.name, 'path':str(path.resolve()),
                      'sha256':hashlib.sha256(encoded).hexdigest(),
                      'resolution':f'{width}x{height}',
                      'hud_scale':hud_settings["scale_percent"] if manual else None,
                      'hud_source':source_label(hud_settings["scale_percent"]) if manual else "旧版兼容预设",
                      'source':profile["hud"]["source"] if manual else "legacy"}
            if manual and (width,height) != (1920,1080):
                record.update(health_raw=None, confidence=0., anchor_score=0., hud_valid=False,
                              first_frame_valid=False, first_frame_reason="resolution_mismatch",
                              reject_reason="resolution_mismatch", outer_coords=None,
                              inner_coords=None, bar_bounds=None, cap_positions=None)
                records.append(record)
                continue
            config=make_config(Path(folder)/f'{index}.ini',1,(width,height),'mock',
                               hud_settings=hud_settings)
            cfg = load_config(config)
            result,roi,health=VisionPipeline(cfg).process(frame,timestamp=0)
            record.update({'health_raw':float(health.health_raw),
                            'confidence':float(health.confidence),'anchor_score':float(roi.anchor_score),
                            'hud_valid':bool(roi.valid),'first_frame_valid':bool(result.valid),
                            'first_frame_reason':result.debug.get('reject_reason',''),
                            'reject_reason':getattr(roi,'reject_reason','') or result.debug.get('reject_reason',''),
                            'outer_coords':list(roi.outer_coords),'inner_coords':list(roi.inner_coords),
                            'bar_bounds':[cfg['ROI']['bar_min_x'],cfg['ROI']['bar_max_x']],
                            'cap_positions':[cfg['hud']['cap_left_x'],cfg['hud']['cap_right_x']]
                                            if manual else None})
            records.append(record)
    return {'status':'ok','frozen':bool(getattr(sys,'frozen',False)),
            'mode':'production vision core; one original image per fresh pipeline',
            'screen_capture_started':False,'app_connected':False,'images':records}


def launch(*, on_ready=None):
    import tkinter as tk
    from tkinter import ttk, scrolledtext
    from PIL import ImageTk
    import qrcode
    import mss

    class App:
        def __init__(self):
            self.window = tk.Tk()
            self.window.title("HELLDIVER 主版本 · 官方后端 / 自动 IP 选择")
            self.window.geometry("940x740")
            self.window.minsize(860,700)
            self.events = queue.Queue()
            self.cancel = threading.Event()
            self.thread = None
            self.closing = False
            self.session_failed = False
            self.photo = None
            self.monitors = []
            self.networks = []
            self.network_busy = False
            base = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
            self.settings_store = SettingsStore(base / "feedback_settings.ini")
            self.hud_store = HudSettingsStore(base / "hud_settings.ini")
            self.inspection_busy = False
            self.permissions_busy = False
            self.permissions_dialog = None
            self.log_lines = deque(maxlen=600)
            from src.desktop_view import DesktopView
            self.view = DesktopView(self, base)
            self.update_hud_hint()
            self.window.protocol("WM_DELETE_WINDOW", self.close)
            self.start.configure(state="disabled")
            self.mock.configure(state="disabled")
            threading.Thread(target=self.read_displays, daemon=True).start()
            self.refresh_networks()
            self.window.after(80,self.poll)

        def is_running(self):
            return self.session_active() or self.permissions_busy

        def session_active(self):
            return bool(self.thread and self.thread.is_alive()) or self.closing or self.inspection_busy

        def open_permissions(self):
            if self.is_running():
                self.status.set("请先停止全部再配置连接权限；F8 暂停不会解锁。")
                return
            if self.permissions_dialog is not None and not self.permissions_dialog.closed:
                self.permissions_dialog.window.lift()
                return
            from src.firewall_dialog import FirewallDialog
            def busy(value):
                self.permissions_busy = value
                self.update_controls()
            self.permissions_dialog = FirewallDialog(self.window,self.session_active,busy)

        def open_settings(self):
            self.view.navigate('反馈设置')

        def open_help(self):
            self.view.navigate('使用指南')

        def update_hud_hint(self):
            if self.hud_store.error:
                self.hud_hint.set("HUD 设置损坏：请打开 HUD 设置修正并保存。")
            elif self.hud_store.values["mode"] == "legacy":
                self.hud_hint.set("旧版兼容预设，尚未选择 HUD 档位")
            else:
                from src.hud_profiles import source_label
                percent = self.hud_store.values["scale_percent"]
                self.hud_hint.set(f"HUD {percent/100:.2f} · 1920×1080 · {source_label(percent)}")

        def open_hud_settings(self):
            self.view.navigate('HUD 设置')

        def check_screenshots(self):
            if self.is_running() or self.inspection_busy:
                return
            from tkinter import filedialog, messagebox
            try:
                selected_hud = self.hud_store.snapshot()
            except ValueError as error:
                messagebox.showerror("截图检查",str(error),parent=self.window)
                return
            paths = filedialog.askopenfilenames(parent=self.window,title="选择原始游戏截图",
                                                filetypes=[("游戏截图","*.png *.jpg *.jpeg *.bmp"),
                                                           ("所有文件","*.*")])
            if not paths:
                return
            self.inspection_busy = True
            self.update_controls()
            self.status.set("正在检查原图…")
            def inspect():
                try:
                    self.events.put(("image_report",(inspect_images(paths,hud_settings=selected_hud),paths)))
                except Exception as error:
                    self.events.put(("image_error",str(error)))
            threading.Thread(target=inspect,daemon=True).start()

        def update_controls(self):
            running = bool(self.thread and self.thread.is_alive()) or self.closing or self.permissions_busy
            ready = (bool(self.monitors) and not running and not self.inspection_busy
                     and not self.settings_store.error and not self.hud_store.error)
            self.start.configure(state="normal" if ready and self.networks and not self.network_busy else "disabled")
            self.mock.configure(state="normal" if ready else "disabled")
            self.display.configure(state="readonly" if ready else "disabled")
            self.network.configure(state="readonly" if not running and not self.network_busy else "disabled")
            self.refresh.configure(state="normal" if not running and not self.network_busy else "disabled")
            self.image_check.configure(state="normal" if not running and not self.inspection_busy
                                       and not self.hud_store.error else "disabled")
            self.permissions_button.configure(state="disabled" if self.is_running() else "normal")
            self.view.update_controls()

        def refresh_networks(self):
            if self.network_busy or (self.thread and self.thread.is_alive()):
                return
            selected=self.network.current()
            previous=(self.networks[selected].interface_index,self.networks[selected].address) if selected>=0 else None
            self.network_busy=True
            self.network_hint.set("正在自动读取本机网卡，无需联网或手动填写 IP…")
            self.update_controls()
            def detect():
                try:
                    self.events.put(("networks",(discover_network_addresses(),previous,None)))
                except Exception as error:
                    self.events.put(("networks",([],previous,str(error))))
            threading.Thread(target=detect,daemon=True).start()

        def read_displays(self):
            try:
                with mss.MSS() as capture:
                    monitors = [(i,m["width"],m["height"]) for i,m in enumerate(capture.monitors[1:],1)]
                self.events.put(("monitors",monitors))
            except Exception as error:
                self.events.put(("error",f"无法读取显示器：{error}"))

        def begin(self, mode):
            selected = self.display.current()
            if selected < 0 or self.is_running() or self.inspection_busy:
                return
            if not self.view.confirm_edits():
                return
            try:
                feedback_settings = self.settings_store.snapshot()
                trigger_settings = self.settings_store.trigger_snapshot()
                hud_settings = self.hud_store.snapshot()
                from src.desktop_runtime import validate_display
                validate_display(self.monitors[selected][1:],hud_settings)
            except ValueError as error:
                self.status.set(str(error))
                return
            qr_host=None
            if mode=="dglab":
                network_index=self.network.current()
                if self.network_busy or network_index<0 or network_index>=len(self.networks):
                    self.status.set("请等待自动检测完成，然后选择与手机同一局域网的网卡")
                    return
                qr_host=validate_advertised_ipv4(self.networks[network_index].address)
            self.cancel.clear()
            self.session_failed = False
            self.view.status_reader = None
            self.view.output_status.set('软件输出 A -- / B --   |   HP --   |   正在启动')
            self.photo = None
            self.qr.configure(image="", text="正在生成连接…" if mode=="dglab" else "本地 Mock，无需扫码")
            self.start.configure(state="disabled")
            self.mock.configure(state="disabled")
            self.display.configure(state="disabled")
            self.network.configure(state="disabled")
            self.refresh.configure(state="disabled")
            self.stop.configure(state="normal")
            self.status.set("正在启动内置后端…" if mode=="dglab" else "正在启动实时识别…")
            monitor = self.monitors[selected]
            self.thread = threading.Thread(target=self.run_session,args=(mode,monitor,qr_host,feedback_settings,hud_settings,trigger_settings),daemon=True)
            self.thread.start()
            self.update_controls()

        def run_session(self, mode, monitor, qr_host, feedback_settings, hud_settings, trigger_settings):
            backend = process = None
            reader = None
            try:
                feedback_settings = validate_settings(feedback_settings)
                trigger_settings = validate_trigger_settings(trigger_settings)
                hud_settings = validate_hud_settings(hud_settings)
                base = Path(sys.executable).parent if getattr(sys,"frozen",False) else ROOT
                folder = base / "sessions" / (time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6])
                folder.mkdir(parents=True)
                self.events.put(('session_status', folder))
                self.events.put(("log",f"本次日志：{folder}"))
                port = 9999
                if mode == "dglab":
                    backend = OfficialBackend(folder,lambda line:self.events.put(("log",line)))
                    port = backend.start(self.cancel)
                if self.cancel.is_set():
                    return
                index,width,height = monitor
                config = make_config(folder/"config.ini",index,(width,height),mode,port,qr_host=qr_host,
                                     feedback_settings=feedback_settings,hud_settings=hud_settings,
                                     trigger_settings=trigger_settings)
                self.events.put(("log", "HUD 已固定到本次会话：" +
                                 (f"{hud_settings['scale_percent']/100:.2f}" if hud_settings['mode']=='manual'
                                  else "旧版兼容预设")))
                self.events.put(("log", "反馈通道模式：" + feedback_settings["channel_mode"] +
                                 "；配置已固定到本次会话，F8 暂停不会更改设置。"))
                self.events.put(("log", f"残血／濒死触发血量：{trigger_settings['low_health_threshold_percent']}%（0% 关闭此反馈）"))
                if mode=="dglab":
                    self.events.put(("log",f"手机连接地址：ws://{qr_host}:{port}；若要更换网卡，请停止后重新选择。"))
                (folder/"heartbeat").touch()
                process = subprocess.Popen(worker_command(config,folder), cwd=folder,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,creationflags=NO_WINDOW)
                self.events.put(("status","等待 APP 扫码配对…" if mode=="dglab" else "实时 Mock 已启动：进入战场按 F8"))
                while process.poll() is None and not self.cancel.wait(.08):
                    (folder/"heartbeat").touch()
                    if reader is None and (folder/"runner.log").is_file():
                        reader = (folder/"runner.log").open(encoding="utf-8",errors="replace")
                    if reader:
                        for line in reader:
                            line=line.rstrip()
                            if line.startswith("HD_QR="):
                                self.events.put(("qr",line.partition("=")[2]))
                            else:
                                self.events.put(("log",line))
                                if "OK 配对成功" in line:
                                    self.events.put(("status","APP 已配对：进入战场后按 F8 开始识别"))
                                elif "dglab ws: partner disconnected" in line or "dglab ws: closed" in line:
                                    self.events.put(("status","APP 连接已断开；等待重新配对，或停止后重试"))
                    if backend and backend.process.poll() is not None:
                        raise RuntimeError("后端已退出，识别会话已停止。")
                if process.poll() not in (None,0) and not self.cancel.is_set():
                    raise RuntimeError(f"识别进程退出（{process.returncode}），请查看 {folder / 'runner.log'}")
            except Exception as error:
                self.events.put(("error",str(error)))
            finally:
                try:
                    if process is not None:
                        stop_worker(process,folder)
                finally:
                    try:
                        if reader:
                            reader.close()
                        if backend:
                            backend.close()
                    finally:
                        self.events.put(("finished",None))

        def poll(self):
            changed = False
            for _ in range(300):
                try:
                    kind,value=self.events.get_nowait()
                except queue.Empty:
                    break
                if kind=="monitors":
                    self.monitors=value
                    self.display["values"]=[f"屏幕 {i} — {w}×{h}" for i,w,h in value]
                    if value:
                        self.display.current(0)
                        error = self.settings_store.error or self.hud_store.error
                        self.status.set("设置损坏：请打开对应设置修正并保存。" + error
                                        if error else "就绪：选择 HUD、屏幕和网卡后启动，或检查截图")
                    self.update_controls()
                elif kind == 'session_status':
                    from src.ui_status import StatusReader
                    self.view.status_reader = StatusReader(value / 'ui_status.json', value.name)
                elif kind=="networks":
                    self.networks,previous,error=value
                    self.network_busy=False
                    self.network["values"]=[item.label for item in self.networks]
                    if self.networks:
                        index=next((i for i,item in enumerate(self.networks)
                                    if (item.interface_index,item.address)==previous),0)
                        self.network.current(index)
                        self.network_hint.set("已自动检测；选择与手机同一局域网的网卡。检测到 IP 不代表手机一定可达；换网后点重新检测。")
                    else:
                        self.network.set("")
                        self.network_hint.set(error or "未检测到可用的局域网 IPv4。恢复电脑的局域网连接后点重新检测；仅测试识别仍可用。")
                    self.update_controls()
                elif kind=="qr":
                    self.status.set("等待 APP 扫码配对（两分钟内）…")
                    self.photo=ImageTk.PhotoImage(qrcode.make(value).resize((240,240)))
                    self.qr.configure(image=self.photo,text="")
                elif kind in ("image_report","image_error"):
                    self.inspection_busy = False
                    self.update_controls()
                    if not self.closing:
                        if kind == "image_report":
                            from src.hud_dialog import ScreenshotCheckDialog
                            report,paths = value
                            ScreenshotCheckDialog(self.window,report,paths)
                            self.status.set("截图检查完成：单帧结果仅用于定位和血量估计")
                        else:
                            from tkinter import messagebox
                            messagebox.showerror("截图检查失败",value,parent=self.window)
                            self.status.set("截图检查失败：" + value)
                elif kind in ("status","error"):
                    self.status.set(value)
                    if kind=="error":
                        self.session_failed = True
                        self.log_lines.append("错误："+value)
                        changed=True
                elif kind=="log":
                    self.log_lines.append(value)
                    changed=True
                elif kind=="finished":
                    self.view.status_reader = None
                    self.view.output_status.set('软件输出 A -- / B --   |   HP --   |   会话已结束')
                    self.stop.configure(state="disabled")
                    self.thread=None
                    self.update_controls()
                    self.photo=None
                    self.qr.configure(image="",text="连接已结束，二维码已失效")
                    if self.cancel.is_set():
                        self.status.set("已停止识别与后端，可以重新启动")
                    elif not self.session_failed:
                        self.status.set("会话已结束，识别与后端均已关闭")
            if changed:
                self.output.configure(state="normal")
                self.output.delete("1.0","end")
                self.output.insert("end","\n".join(self.log_lines))
                self.output.see("end")
                self.output.configure(state="disabled")
            if self.closing and not (self.thread and self.thread.is_alive()):
                self.view.close()
                self.window.destroy()
                return
            self.view.poll_status()
            self.window.after(80,self.poll)

        def close(self):
            if self.permissions_busy:
                self.status.set("权限操作仍在进行，请先在 Windows 授权窗口取消或等待完成。")
                return
            if not self.view.confirm_edits():
                return
            self.closing=True
            self.view.update_animation()
            self.cancel.set()
            self.status.set("正在关闭识别与内置后端…")

    app=App()
    if on_ready is not None:
        app.window.after(300,lambda:on_ready(app))
    app.window.mainloop()


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--worker",type=Path)
    parser.add_argument("--firewall-helper",choices=("configure","remove"),help=argparse.SUPPRESS)
    parser.add_argument("--session",type=Path)
    parser.add_argument("--self-check",action="store_true")
    parser.add_argument("--check-images",type=Path,nargs='+')
    parser.add_argument("--hud-scale",choices=[f"{p/100:.2f}" for p in range(75,126,5)],
                        help="HUD size for image checks; exact 0.05 steps at 1920x1080")
    parser.add_argument("--check-hud",action="store_true")
    parser.add_argument("--check-hud-ui",action="store_true")
    parser.add_argument('--check-theme', action='store_true', help='Opt-in own-window theme checks; no game or device')
    parser.add_argument("--check-firewall",action="store_true",
                        help="Readonly bundled path, rule state, script syntax and Windows API checks; no UAC")
    parser.add_argument("--ui-screenshots",type=Path)
    parser.add_argument("--hud-samples",type=Path)
    parser.add_argument("--check-feedback", action="store_true",
                        help="Opt-in localhost software APP acceptance; never connects to the user's APP")
    parser.add_argument("--check-threshold", action="store_true",
                        help="Opt-in threshold preferences to loopback software APP; no physical device")
    parser.add_argument("--check-panel", action="store_true",
                        help="Opt-in own-window focus acceptance; no capture or device connection")
    parser.add_argument("--interactive-panel", action="store_true")
    parser.add_argument("--report",type=Path)
    args=parser.parse_args(argv)
    if args.firewall_helper:
        from src.firewall_access import run_helper
        return run_helper(args.firewall_helper)
    if args.worker:
        if not args.session:
            parser.error("worker requires session directory")
        return run_worker(args.worker,args.session)
    if args.self_check or args.check_images or args.check_feedback or args.check_threshold or args.check_panel or args.check_hud or args.check_hud_ui or args.check_firewall or args.check_theme:
        if args.check_theme:
            from tools.main_theme_acceptance import run
            result = run(launch, args.ui_screenshots)
        elif args.check_threshold:
            from tools.main_threshold_acceptance import run
            result = run()
        elif args.check_firewall:
            from tools.main_firewall_acceptance import run
            result = run()
        elif args.check_hud_ui:
            if not args.hud_samples:
                parser.error("--check-hud-ui requires --hud-samples")
            from tools.main_hud_ui_acceptance import run
            result=run(inspect_images,args.hud_samples,args.ui_screenshots,launch_app=launch)
        elif args.check_hud:
            if not args.hud_samples:
                parser.error("--check-hud requires --hud-samples")
            from tools.main_hud_acceptance import run
            result = run(inspect_images,args.hud_samples)
        elif args.check_panel:
            from tools.panel_acceptance import run
            result = run(args.interactive_panel, theme='cutting_edge')
        elif args.check_feedback:
            from tools.main_feedback_acceptance import run
            result = run()
        else:
            hud = {"mode":"manual","scale_percent":int(args.hud_scale.replace('.',''))} if args.hud_scale else None
            result=inspect_images(args.check_images,hud_settings=hud) if args.check_images else self_check()
        content=json.dumps(result,ensure_ascii=False,indent=2)
        if args.report:
            args.report.write_text(content,encoding="utf-8")
        if sys.stdout is not None:
            print(content)
        return 0
    launch()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
