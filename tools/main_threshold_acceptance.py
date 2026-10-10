"""Final main EXE preferences -> Trigger -> owned loopback software APP only."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace


def run():
    import websocket
    from src import dglab_sender as sender
    from src.config_loader import load_config
    from src.desktop_runtime import OfficialBackend, make_config
    from src.feedback_settings import SettingsStore, default_settings
    from src.trigger import TriggerDispatch
    from src.waveforms import WAVEFORM_V3
    checks = []
    old_qr, old_ip = sender._DglabWSClient._show_qrcode, sender._get_lan_ip
    sender._get_lan_ip = lambda: "127.0.0.1"
    try:
        with tempfile.TemporaryDirectory(prefix="helldiver-threshold-") as directory:
            root = Path(directory)
            backend = OfficialBackend(root)
            try:
                port = backend.start()
                for percent in (0, 30, 75, 100):
                    received, ids, errors = [], [], []
                    registered, done = threading.Event(), threading.Event()
                    def show(self, url):
                        ids.append(self.client_id)
                        registered.set()
                    sender._DglabWSClient._show_qrcode = show
                    def peer():
                        app = None
                        try:
                            if not registered.wait(5):
                                raise RuntimeError("No local client registration")
                            app = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=2, http_proxy_host=None)
                            app_id = json.loads(app.recv())["clientId"]
                            app.send(json.dumps(dict(type="bind", clientId=ids[0], targetId=app_id, message="DGLAB")))
                            app.settimeout(.05)
                            while not done.is_set():
                                try:
                                    raw = app.recv()
                                    if not raw:
                                        break
                                    received.append(json.loads(raw))
                                except websocket.WebSocketTimeoutException:
                                    continue
                        except Exception as error:
                            if not done.is_set():
                                errors.append(str(error))
                        finally:
                            if app:
                                app.close()
                    thread = threading.Thread(target=peer, daemon=True)
                    store = SettingsStore(root / f"settings-{percent}.ini")
                    values = default_settings() | dict(channel_mode="both", low_health_strength_b=6,
                        injury_pulse_strength=0, injury_pulse_strength_b=0,
                        heal_pulse_strength=0, heal_pulse_strength_b=0,
                        healing_sustain_strength=0, healing_sustain_strength_b=0)
                    store.save(values, trigger_settings={"low_health_threshold_percent": percent})
                    restored = SettingsStore(store.path)
                    path = make_config(root / f"session-{percent}.ini", 1, (1920, 1080), "dglab", port,
                                       feedback_settings=restored.snapshot(), trigger_settings=restored.trigger_snapshot())
                    config = load_config(path)
                    thread.start()
                    dispatch = None
                    try:
                        dispatch = TriggerDispatch(config, log_writer=SimpleNamespace(append=lambda row: None, close=lambda: None))
                        for hp in (.5, .2):
                            dispatch.reset("new_controlled_plateau")
                            time.sleep(.1)
                            before = len(received)
                            for _ in range(8):
                                dispatch.dispatch(SimpleNamespace(health_raw=hp, health_stable=hp, valid=True, state="injured"))
                                time.sleep(.04)
                            messages = [m.get("message", "") for m in received[before:]]
                            positive = [m for m in messages if isinstance(m, str) and m.startswith("pulse-")]
                            expected = 0 < hp < percent / 100
                            assert bool(positive) == expected, (percent, hp, messages)
                            if expected:
                                assert {m[6] for m in positive} == {"A", "B"}
                                assert all(json.loads(m.split(":", 1)[1]) == WAVEFORM_V3[values["low_health_waveform"]] for m in positive)
                                assert "strength-1+2+10" in messages and "strength-2+2+6" in messages
                            checks.append(dict(percent=percent, raw_hp=hp, low_health=expected,
                                               positive_pulses=len(positive), actual_trigger_ratio=config["trigger"]["low_health_hp_threshold"]))
                        dispatch.reset("stop")
                        time.sleep(.1)
                        before = len(received)
                        time.sleep(.25)
                        assert not any(str(m.get("message", "")).startswith("pulse-") for m in received[before:])
                        texts = [m.get("message", "") for m in received]
                        assert all(f"strength-{ch}+2+0" in texts for ch in (1, 2))
                        assert not errors, errors
                    finally:
                        if dispatch:
                            dispatch.safe_stop()
                        sender.safe_stop()
                        done.set()
                        thread.join(2)
                backend.close()
                assert backend.process.returncode == 0
            finally:
                backend.close()
    finally:
        sender.safe_stop()
        sender._DglabWSClient._show_qrcode, sender._get_lan_ip = old_qr, old_ip
    return dict(status="ok", checks=checks, transport="owned loopback official backend + software APP",
                full_preference_trigger_protocol_chain=True, final_both_zero=True, no_late_output=True,
                physical_device_used=False, user_phone_app_tested=False, firewall_modified=False)
