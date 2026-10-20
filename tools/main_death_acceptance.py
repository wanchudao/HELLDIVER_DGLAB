"""Frozen main preferences -> Trigger -> owned official backend/software APP."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace


def run(*, resolution=(1920, 1080), frame_input=False):
    import websocket
    from src import dglab_sender as sender
    from src.config_loader import load_config
    from src.desktop_runtime import OfficialBackend, make_config
    from src.feedback_settings import SettingsStore, default_settings
    from src.trigger import TriggerDispatch
    from src.waveforms import WAVEFORM_V3
    checks = []
    original_qr = sender._DglabWSClient._show_qrcode
    original_ip = sender._get_lan_ip
    sender._get_lan_ip = lambda: "127.0.0.1"
    try:
        with tempfile.TemporaryDirectory(prefix="helldiver-death-") as directory:
            root = Path(directory)
            backend = OfficialBackend(root)
            try:
                port = backend.start()
                for mode in ("pulse", "until_revive", "until_reset"):
                    channel_mode = {"pulse":"a", "until_revive":"b", "until_reset":"both"}[mode] if frame_input else "both"
                    def enabled(values):
                        return {ch: value if channel_mode == "both" or channel_mode == ch.lower() else 0
                                for ch, value in values.items()}
                    packets, ids, errors = [], [], []
                    registered, done = threading.Event(), threading.Event()
                    def qr(client, url):
                        ids.append(client.client_id)
                        registered.set()
                    sender._DglabWSClient._show_qrcode = qr
                    def peer():
                        app = None
                        try:
                            assert registered.wait(5), "No local registration"
                            app = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=2, http_proxy_host=None)
                            app_id = json.loads(app.recv())["clientId"]
                            app.send(json.dumps(dict(type="bind", clientId=ids[0], targetId=app_id, message="DGLAB")))
                            app.settimeout(.05)
                            while not done.is_set():
                                try:
                                    raw = app.recv()
                                    if not raw:
                                        break
                                    packets.append(json.loads(raw).get("message", ""))
                                except websocket.WebSocketTimeoutException:
                                    continue
                        except Exception as error:
                            if not done.is_set():
                                errors.append(type(error).__name__)
                        finally:
                            if app is not None:
                                app.close()
                    store = SettingsStore(root / (mode + ".ini"))
                    values = default_settings() | dict(channel_mode=channel_mode, max_strength=55, max_strength_b=32,
                        death_pulse_strength_b=20, low_health_strength_b=6, heal_pulse_strength_b=7,
                        healing_sustain_strength_b=2)
                    store.save(values, trigger_settings={"death_feedback_mode": mode})
                    restored = SettingsStore(store.path)
                    hud = None
                    if frame_input:
                        from src.hud_settings import HudSettingsStore, EXPERIMENTAL_KEY
                        hud_store = HudSettingsStore(root / (mode + "-hud.ini"))
                        hud_store.save(dict(mode="manual", scale_percent=100, **{EXPERIMENTAL_KEY:True}))
                        hud = HudSettingsStore(hud_store.path).snapshot()
                    config = load_config(make_config(root / (mode + "-session.ini"), 1, resolution,
                        "dglab", port, feedback_settings=restored.snapshot(),
                        trigger_settings=restored.trigger_snapshot(), hud_settings=hud))
                    if frame_input:
                        from src.vision_pipeline import VisionPipeline
                        from tools.resolution_fixtures import synthetic_frame
                        import numpy as np
                        pipeline = VisionPipeline(config)
                    thread = threading.Thread(target=peer, daemon=True)
                    thread.start()
                    dispatch = None
                    try:
                        dispatch = TriggerDispatch(config, log_writer=SimpleNamespace(append=lambda row: None, close=lambda: None))
                        def observe(hp, count=1, valid=True):
                            for _ in range(count):
                                if frame_input:
                                    frame = synthetic_frame(100, resolution, hp) if valid else np.zeros((resolution[1],resolution[0],3),np.uint8)
                                    observation = pipeline.process(frame)[0]
                                else:
                                    observation = SimpleNamespace(health_raw=hp, health_stable=hp, valid=valid, state="injured")
                                dispatch.dispatch(observation)
                                time.sleep(.04)
                        observe(1, 2)
                        observe(0, 140)
                        strengths = sender.ui_output_snapshot()["strengths"]
                        assert strengths == enabled({"A": 0, "B": 0} if mode == "pulse" else {"A": 40, "B": 20}), strengths
                        death_waves = [message for message in packets if isinstance(message, str) and message.startswith("pulse-")
                            and json.loads(message.split(":", 1)[1]) == WAVEFORM_V3[values["death_pulse_waveform"]]]
                        assert death_waves and len(death_waves) < 20
                        observe(.5, 3)
                        expected = enabled({"A": 55, "B": 32} if mode == "until_reset" else {"A": 30, "B": 15})
                        assert sender.ui_output_snapshot()["strengths"] == expected
                        if mode == "until_reset":
                            observe(.5, valid=False)
                            assert sender.ui_output_snapshot()["strengths"] == {"A": 0, "B": 0}
                            assert dispatch.challenge_failed
                            observe(.5, 2 if frame_input else 1)  # warmup + recovery baseline cannot output
                            assert sender.ui_output_snapshot()["strengths"] == {"A": 0, "B": 0}
                            observe(.5)
                            assert sender.ui_output_snapshot()["strengths"] == enabled({"A": 50, "B": 26})
                            dispatch.reset("button_pause")
                            assert dispatch.challenge_failed
                            dispatch.reset_challenge()
                            assert not dispatch.challenge_failed
                        dispatch.reset("stop")
                        time.sleep(.08)
                        start = len(packets)
                        time.sleep(.25)
                        assert not any(isinstance(m, str) and m.startswith("pulse-") for m in packets[start:])
                        assert all(f"strength-{channel}+2+0" in packets for channel in (1, 2))
                        assert not errors, errors
                        if frame_input:
                            allowed = {"1","2"} if channel_mode == "both" else {"1" if channel_mode == "a" else "2"}
                            for packet in packets:
                                if isinstance(packet,str) and packet.startswith("strength-"):
                                    channel, operation, value = packet.removeprefix("strength-").split("+")
                                    if operation == "2" and int(value) > 0:
                                        assert channel in allowed
                        checks.append(dict(mode=mode, held_after_five_seconds=strengths,
                            revived_strengths=expected, death_wave_submissions=len(death_waves),
                            saved_session_mode=config["trigger"]["death_feedback_mode"], both_stopped=True,
                            channel_mode=channel_mode, resolution=list(resolution),
                            profile_source=config.get("hud",{}).get("source"),
                            original_pixel_synthetic_frame_input=frame_input))
                    finally:
                        if dispatch is not None:
                            dispatch.safe_stop()
                        done.set()
                        thread.join(2)
            finally:
                backend.close()
    finally:
        sender.safe_stop()
        sender._DglabWSClient._show_qrcode = original_qr
        sender._get_lan_ip = original_ip
    report = dict(status="ok", checks=checks, full_preferences_trigger_protocol_chain=True,
        final_both_zero=True, no_late_output=True, physical_device_used=False,
        user_phone_app_tested=False, firewall_modified=False)
    if frame_input:
        import hashlib, sys
        report.update(resolution=list(resolution), frozen=bool(getattr(sys,"frozen",False)),
            exe_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() if getattr(sys,"frozen",False) else None,
            source="synthetic_original_pixels_not_real_gameplay", full_vision_preferences_protocol_chain=True)
    return report
