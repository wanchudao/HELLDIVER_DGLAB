"""Opt-in native own-window UI validation. No game, APP or firewall mutations."""
from pathlib import Path
import tempfile
import time
import json


def run(launch_app, screenshot_dir=None):
    from PIL import ImageTk
    from src.ui_screenshots import capture_owned_window
    import qrcode
    from tkinter import messagebox
    from src.feedback_settings import SettingsStore
    from src.hud_settings import HudSettingsStore
    from src.ui_status import StatusReader
    from src.control_panel import draw_control_panel, draw_island
    from types import SimpleNamespace
    import cv2
    checks, shots, performance = [], [], []
    outcome = {}
    output_dir = Path(screenshot_dir) if screenshot_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='helldiver-ui-') as directory:
        base = Path(directory)
        def inspect(app):
            root, view = app.window, app.view
            old_question = messagebox.askyesnocancel
            try:
                app.settings_store = SettingsStore(base/'feedback_settings.ini')
                app.hud_store = HudSettingsStore(base/'hud_settings.ini')
                view.editor.store = app.settings_store
                view.editor.discard()
                view.preferences.path = base/'ui_settings.ini'
                app.update_hud_hint()
                root.lift()
                def settle(seconds=.15):
                    until = time.monotonic()+seconds
                    while time.monotonic() < until:
                        root.update()
                        time.sleep(.005)
                def capture(name):
                    settle()
                    assert root.winfo_viewable()
                    if output_dir:
                        path = output_dir/(name+'.png')
                        capture_owned_window(root).save(path)
                        shots.append(str(path.resolve()))
                    checks.append(name)

                # Avoid relying on detection taking a particular amount of time.
                app.network_busy = False
                settle()
                app.network['values'] = ('以太网 — 192.168.2.100 / 与手机同一局域网',)
                app.network.current(0)
                app.network_hint.set('已自动检测；选择与手机同一局域网的网卡。')
                app.status.set('就绪：选择 HUD、屏幕和网卡后启动，或检查截图')
                capture('main_window')
                editor = view.editor
                editor.threshold.set('30')
                assert editor.is_dirty()
                messagebox.askyesnocancel = lambda *a, **k: None
                view.navigate('使用指南')
                assert view.current == '任务控制' and editor.is_dirty()
                messagebox.askyesnocancel = lambda *a, **k: False
                view.navigate('使用指南')
                assert view.current == '使用指南' and not editor.is_dirty()
                capture('guide_page')
                view.navigate('反馈设置')
                editor.threshold.set('30')
                messagebox.askyesnocancel = lambda *a, **k: True
                view.navigate('HUD 设置')
                assert app.settings_store.trigger_snapshot()['low_health_threshold_percent'] == 30
                capture('hud_page')
                view.hud_editor.selection.set('1.00')
                view.hud_editor.scale_box.event_generate('<<ComboboxSelected>>')
                settle()
                assert '1080p 来源' in view.hud_editor.source.get()
                assert not view.hud_editor.experimental.get()
                view.hud_editor.save()
                assert app.hud_store.snapshot()['scale_percent'] == 100
                capture('hud_saved')
                view.navigate('截图检查')
                assert app.thread is None and not app.inspection_busy
                capture('screenshot_page')
                view.navigate('任务控制')
                app.thread = SimpleNamespace(is_alive=lambda: True)
                app.update_controls()
                assert str(editor.threshold_box.cget('state')) == 'disabled'
                assert not view.top_border.enabled and view.top_border.after_id is None
                assert not view.bottom_border.enabled and view.bottom_border.after_id is None
                before = view.top_border.ticks
                settle(.35)
                assert view.top_border.ticks == before
                performance.append({'mode':'session_active','decoration_ticks':0})
                app.thread = None
                app.update_controls()
                for enabled in (False, True):
                    view.animation.set(enabled)
                    view.save_animation()
                    before = view.top_border.ticks
                    cpu, wall = time.process_time(), time.monotonic()
                    settle(.65)
                    performance.append({'mode':'idle_animation_'+str(enabled),
                                        'decoration_ticks':view.top_border.ticks-before,
                                        'cpu_seconds':round(time.process_time()-cpu, 4),
                                        'wall_seconds':round(time.monotonic()-wall, 4)})
                    assert (view.top_border.ticks > before) == enabled
                app.inspection_busy = True
                app.update_controls()
                assert view.top_border.after_id is None
                app.inspection_busy = False
                app.permissions_busy = True
                app.update_controls()
                assert view.top_border.after_id is None
                app.permissions_busy = False
                app.update_controls()
                root.iconify()
                settle(.2)
                assert view.top_border.after_id is None
                root.deiconify()
                settle()
                checks.extend(['dirty_cancel_discard_save', 'readonly_during_session',
                               'animation_stopped_session_inspection_permissions_minimized'])
                status_path = base/'ui_status.json'
                status = dict(session='new', sequence=1, written_at=time.time(), paused=False,
                              valid=True, hp=.5, connected=True, strengths={'A': 10, 'B': 7}, mode='dglab')
                status_path.write_text(json.dumps(status), encoding='utf-8')
                view.status_reader = StatusReader(status_path, 'new')
                view.poll_status()
                assert 'HP 50%' in view.output_status.get() and 'B 7' in view.output_status.get()
                capture('live_telemetry_display_only')
                status.update(sequence=2, valid=False, hp=None, strengths={'A':0,'B':0})
                status_path.write_text(json.dumps(status), encoding='utf-8')
                view.poll_status()
                assert 'NO HUD' in view.output_status.get() and 'HP --' in view.output_status.get()
                view.status_reader.received_at -= 2
                view.poll_status()
                assert '状态更新中断' in view.output_status.get() and 'A --' in view.output_status.get()
                view.status_reader = None
                checks.append('telemetry_valid_nohud_stale')
                app.photo = ImageTk.PhotoImage(qrcode.make('https://localhost/preview-only').resize((240,240)))
                app.qr.configure(image=app.photo, text='')
                capture('qr_display_preview_only')
                app.photo = None
                app.qr.configure(image='', text='连接后显示配对码')
                original_scale = float(root.tk.call('tk', 'scaling'))
                for scale in (1, 1.25, 1.5, 2):
                    root.tk.call('tk', 'scaling', original_scale*scale)
                    root.geometry(f'{int(1280/scale)}x{max(560, int(820/scale))}')
                    view.navigate('HUD 设置')
                    settle()
                    area = view.area
                    assert area.canvas.bbox('all')[3] >= area.content.winfo_height()
                    assert view.stop_footer.winfo_rooty()+view.stop_footer.winfo_height() <= root.winfo_rooty()+root.winfo_height()
                    area.canvas.yview_moveto(1)
                    capture('logical_scale_'+str(scale))
                    view.navigate('任务控制')
                root.tk.call('tk', 'scaling', original_scale)
                root.geometry('1280x820')
                view.navigate('连接权限')
                deadline = time.monotonic()+40
                while app.permissions_dialog.busy and time.monotonic() < deadline:
                    settle(.05)
                assert app.permissions_dialog and not app.permissions_dialog.busy
                capture('permissions_readonly_page')
                view.navigate('任务控制')
                view.close()
                outcome.update(status='ok', frozen=bool(__import__('sys').frozen) if hasattr(__import__('sys'),'frozen') else False,
                               native_windows=True, checks=checks, screenshots=shots, performance=performance,
                               windows_scale_testing='Tk logical layout simulation, not OS DPI switching',
                               software_telemetry_injected=True, backend_started=False,
                               firewall_modified=False, real_game_tested=False, physical_device_used=False)
                if output_dir:
                    for compact in (False, True):
                        panel = draw_island(paused=True, theme='cutting_edge') if compact else draw_control_panel(theme='cutting_edge')
                        path = output_dir/('island.png' if compact else 'control_panel.png')
                        cv2.imwrite(str(path), panel)
                        shots.append(str(path.resolve()))
            except BaseException as exc:
                outcome['error'] = exc
            finally:
                messagebox.askyesnocancel = old_question
                app.thread = None
                app.inspection_busy = app.permissions_busy = False
                view.editor.discard()
                try:
                    view.close()
                finally:
                    root.destroy()
        launch_app(on_ready=inspect)
    if 'error' in outcome:
        raise outcome['error']
    if outcome.get('status') != 'ok':
        raise RuntimeError('Theme acceptance did not complete')
    return outcome
