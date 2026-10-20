"""Opt-in checks of this app's native windows, using temporary preferences."""
from pathlib import Path
import tempfile


def run(inspect_images, samples_dir, screenshot_dir=None, *, launch_app):
    from src.ui_screenshots import capture_owned_window
    from src.hud_dialog import HUD_CHOICES, HudSettingsDialog, ScreenshotCheckDialog
    from src.hud_settings import HudSettingsStore
    from src.desktop_runtime import guide_text
    from src.settings_dialog import open_guide, FeedbackSettingsDialog
    from src.feedback_settings import SettingsStore, default_settings, default_trigger_settings
    from src.firewall_dialog import FirewallDialog
    from src.firewall_access import AuthorizationCancelled
    from types import SimpleNamespace
    from tkinter import messagebox
    import time
    samples_dir = Path(samples_dir)
    shots = []
    checks = []
    outcome = {}
    if screenshot_dir:
        screenshot_dir = Path(screenshot_dir)
        screenshot_dir.mkdir(parents=True,exist_ok=True)

    def show(root, window, name):
        if window.winfo_toplevel() != window:
            # New embedded pages are verified inside the real main window.
            window = window.winfo_toplevel()
        window.lift()
        window.attributes("-topmost",True)
        root.update()
        root.after(200,root.quit)
        root.mainloop()
        assert window.winfo_viewable(), name
        # Packed widgets must stay within the visible client area.
        def verify(widget):
            for child in widget.winfo_children():
                if child.winfo_toplevel() != window:
                    continue
                if child.winfo_viewable() and child.winfo_manager():
                    x=child.winfo_rootx()-window.winfo_rootx()
                    y=child.winfo_rooty()-window.winfo_rooty()
                    assert x>=0 and y>=0, (name,str(child),x,y)
                    assert x+child.winfo_width()<=window.winfo_width()+1, (name,str(child),"width")
                    assert y+child.winfo_height()<=window.winfo_height()+1, (name,str(child),"height")
                verify(child)
        verify(window)
        if screenshot_dir:
            bbox=(window.winfo_rootx(),window.winfo_rooty(),
                  window.winfo_rootx()+window.winfo_width(),
                  window.winfo_rooty()+window.winfo_height())
            path=screenshot_dir/(name+".png")
            capture_owned_window(window).save(path)
            shots.append(str(path))
        checks.append({"name":name,"mapped":True,"controls_inside":True})

    with tempfile.TemporaryDirectory(prefix="helldiver-hud-ui-") as directory:
        store=HudSettingsStore(Path(directory)/"hud_settings.ini")
        def inspect_app(app):
            root=app.window
            dialog=None
            try:
                assert app.thread is None and not app.inspection_busy
                show(root,root,"main_window")
                saved=[]
                running=[False]
                dialog=HudSettingsDialog(root,store,lambda:running[0],lambda:saved.append(store.snapshot()))
                assert dialog.selection.get()==HUD_CHOICES[0]
                assert tuple(dialog.scale_box.cget("values"))==HUD_CHOICES
                assert str(dialog.scale_box.cget("state"))=="readonly"
                dialog.selection.set("1.25")
                dialog.update_source()
                dialog.save()
                assert saved==[{"mode":"manual","scale_percent":125,"allow_experimental_high_resolution":False}]
                assert not dialog.experimental.get()
                dialog.experimental.set(True)
                dialog.save()
                assert HudSettingsStore(store.path).snapshot()["allow_experimental_high_resolution"] is True
                assert HudSettingsStore(store.path).snapshot()==saved[-1]
                show(root,dialog.window,"hud_settings")
                running[0]=True
                dialog.update_enabled()
                assert str(dialog.scale_box.cget("state"))=="disabled"
                assert "disabled" in dialog.save_button.state()
                assert "disabled" in dialog.experimental_box.state()
                assert "F8 暂停不会解锁" in dialog.hint.get()
                show(root,dialog.window,"hud_readonly")
                dialog.close()
                image=samples_dir/"hud_100_full.jpg"
                for scale,name,expected in ((100,"screenshot_valid","100.0%"),
                                            (75,"screenshot_rejected","原始血量估计：--")):
                    report=inspect_images([image],hud_settings={"mode":"manual","scale_percent":scale})
                    assert not report["app_connected"] and not report["screen_capture_started"]
                    dialog=ScreenshotCheckDialog(root,report,[image])
                    assert expected in dialog.details.get()
                    assert dialog.photo is not None
                    show(root,dialog.window,name)
                    dialog.window.destroy()
                content=guide_text()
                assert "HUD 设置" in content and "hud_settings.ini" in content and "相同位置" in content
                guide=open_guide(root,content)
                show(root,guide,"guide")
                guide.destroy()
                feedback = SettingsStore(Path(directory)/"feedback_settings.ini")
                running[0] = False
                dialog = FeedbackSettingsDialog(root, feedback, lambda: running[0])
                from src.feedback_settings import DEATH_MODE_LABELS
                assert tuple(dialog.death_mode_box.cget("values")) == tuple(DEATH_MODE_LABELS)
                for label, mode in DEATH_MODE_LABELS.items():
                    dialog.death_mode.set(label)
                    assert dialog.save()
                    assert SettingsStore(feedback.path).trigger_snapshot()["death_feedback_mode"] == mode
                dialog.threshold.set("30")
                dialog.save()
                assert SettingsStore(feedback.path).trigger_snapshot()["low_health_threshold_percent"] == 30
                dialog.mode.set("仅 B")
                dialog.update_enabled()
                assert str(dialog.threshold_box.cget("state")) == "normal"
                show(root, dialog.window, "feedback_threshold")
                running[0] = True
                dialog.update_enabled()
                assert str(dialog.death_mode_box.cget("state")) == "disabled"
                dialog.update_enabled()
                assert str(dialog.threshold_box.cget("state")) == "disabled"
                show(root, dialog.window, "feedback_threshold_readonly")
                running[0] = False
                dialog.update_enabled()
                dialog.default_button.invoke()
                assert dialog.threshold.get() == "75"
                assert feedback.trigger_snapshot()["low_health_threshold_percent"] == 30
                dialog.window.destroy()
                # Real window and readonly rule query, never a real elevation.
                assert app.permissions_button.cget('text')=='连接权限'
                app.permissions_button.invoke()
                dialog=app.permissions_dialog
                def settle(dialog):
                    deadline=time.monotonic()+40
                    while dialog.busy and time.monotonic()<deadline:
                        root.update()
                        time.sleep(.01)
                    assert not dialog.busy,'permission check timeout'
                settle(dialog)
                readonly_status=dialog.status.get()
                show(root,dialog.window,'permissions_readonly')
                dialog.close()
                # Exercise consent/cancel/configure/removal with a service
                # double. All rule writes and UAC are deliberately mocked.
                value=dict(present=False,owned=False,matches=False,active=False)
                calls=[]
                cancel=[False]
                def authorize(operation,hwnd):
                    calls.append(operation)
                    if cancel[0]: raise AuthorizationCancelled('已取消 Windows 授权',1223)
                    value.update({key:operation=='configure' for key in value})
                from src.firewall_access import status_text
                service=SimpleNamespace(bundled_node=lambda:Path(directory)/'node.exe',
                    query_status=lambda:dict(value),authorize=authorize,status_text=status_text)
                running[0]=False
                dialog=FirewallDialog(root,lambda:running[0],service=service)
                settle(dialog)
                assert calls==[]
                old_ask=messagebox.askyesno
                try:
                    messagebox.askyesno=lambda *a,**kw:False
                    dialog.request('configure')
                    assert calls==[] and not dialog.busy
                    running[0]=True
                    dialog.update_enabled()
                    assert 'disabled' in dialog.configure_button.state()
                    dialog.request('configure')
                    assert calls==[]
                    running[0]=False
                    messagebox.askyesno=lambda *a,**kw:True
                    dialog.request('configure')
                    assert dialog.busy and 'disabled' in dialog.close_button.state()
                    settle(dialog)
                    assert value['matches'] and '配置完成' in dialog.status.get()
                    show(root,dialog.window,'permissions_configured_mock')
                    cancel[0]=True
                    dialog.request('configure')
                    settle(dialog)
                    assert '已取消' in dialog.status.get() and value['matches']
                    show(root,dialog.window,'permissions_cancelled_mock')
                    cancel[0]=False
                    dialog.request('remove')
                    settle(dialog)
                    assert not value['present'] and '手动规则未删除' in dialog.status.get()
                    show(root,dialog.window,'permissions_removed_mock')
                    dialog.close()
                finally:
                    messagebox.askyesno=old_ask
                outcome.update(status="ok",no_app=True,game_capture_started=False,
                               temporary_preferences=True,hud_choices=list(HUD_CHOICES),
                               persisted=True,running_readonly=True,checks=checks,screenshots=shots,
                               real_game_tested=False,firewall_modified=False,
                               real_uac_tested=False,permission_mock_operations=calls,
                               permission_readonly_status=readonly_status)
            except BaseException as error:
                outcome["error"]=error
            finally:
                for child in root.winfo_children():
                    if child.winfo_toplevel()!=root:
                        try: child.destroy()
                        except Exception: pass
                app.close()
                root.destroy()
        launch_app(on_ready=inspect_app)
    if "error" in outcome:
        raise outcome["error"]
    if outcome.get("status")!="ok":
        raise RuntimeError("HUD window checks did not complete")
    return outcome
