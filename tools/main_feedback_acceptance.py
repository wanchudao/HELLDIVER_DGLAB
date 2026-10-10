"""Software APP acceptance for the exact frozen feedback sender and backend."""
import json
from pathlib import Path
import tempfile
import threading
import time


def run():
    import websocket
    from src import dglab_sender as sender
    from src.desktop_runtime import OfficialBackend
    from src.feedback_settings import default_settings
    from src.waveforms import WAVEFORM_V3
    checks = []
    original_qr = sender._DglabWSClient._show_qrcode
    original_ip = sender._get_lan_ip
    sender._get_lan_ip = lambda: "127.0.0.1"
    try:
        with tempfile.TemporaryDirectory(prefix="helldiver-feedback-check-") as folder:
            backend = OfficialBackend(folder)
            try:
                port = backend.start()
                for mode in ("a", "b", "both"):
                    app = reader = None
                    done, registered = threading.Event(), threading.Event()
                    received = []
                    sender._DglabWSClient._show_qrcode = lambda self, url: registered.set()
                    values = default_settings() | dict(channel_mode=mode, injury_pulse_strength_b=40,
                                                       low_health_strength_b=6, heal_pulse_strength_b=9,
                                                       healing_sustain_strength_b=3, death_pulse_strength_b=51)
                    config = {"sender": values | {"ws_host": "127.0.0.1", "ws_port": port}}
                    try:
                        client = sender.init_sender(config)
                        if not registered.wait(5):
                            raise RuntimeError("Local sender registration failed")
                        app = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=2, http_proxy_host=None)
                        app_id = json.loads(app.recv())["clientId"]
                        app.send(json.dumps(dict(type="bind", clientId=client.client_id,
                                                 targetId=app_id, message="DGLAB")))
                        app.settimeout(.1)
                        def read():
                            while not done.is_set():
                                try:
                                    raw = app.recv()
                                    if not raw:
                                        return
                                    received.append(json.loads(raw))
                                except websocket.WebSocketTimeoutException:
                                    continue
                                except (websocket.WebSocketConnectionClosedException, OSError):
                                    return
                        reader = threading.Thread(target=read, daemon=True)
                        reader.start()
                        if not sender.wait_for_pair(5):
                            raise RuntimeError("Software APP pairing failed")
                        active = {"a": ("A",), "b": ("B",), "both": ("A", "B")}[mode]
                        for event, strength_a, strength_b in (
                                ("injury_pulse", 10, 20), ("low_health", 10, 6),
                                ("heal_pulse", 15, 9), ("healing_sustain", 5, 3), ("death_pulse", 40, 51)):
                            sender.stop_output()
                            time.sleep(.08)
                            before = len(received)
                            if event in ("low_health", "healing_sustain"):
                                result = sender.update_continuous(low_health=event == "low_health",
                                                                  healing=event == "healing_sustain", hp=.5)
                            else:
                                result = sender.send_action(event, {"damage_pct": .5})
                            assert result["dispatched"], result
                            expected_wave = WAVEFORM_V3[values[f"{event}_waveform"]]
                            deadline = time.monotonic() + 2
                            while time.monotonic() < deadline:
                                messages = [m.get("message", "") for m in received[before:]]
                                complete = all(any(m.startswith(f"pulse-{ch}:") for m in messages)
                                               and f"strength-{1 if ch == 'A' else 2}+2+{strength_a if ch == 'A' else strength_b}" in messages
                                               for ch in active)
                                if complete:
                                    break
                                time.sleep(.01)
                            assert complete, (mode, event, messages)
                            pulses = [m for m in messages if m.startswith("pulse-")]
                            assert {m[6] for m in pulses} == set(active)
                            assert all(json.loads(m.split(":", 1)[1]) == expected_wave for m in pulses)
                            assert all(len(json.dumps(m)) <= 1950 for m in received[before:])
                            inactive = {"A", "B"} - set(active)
                            assert not any(m.startswith(f"strength-{1 if ch == 'A' else 2}+") and not m.endswith("+0")
                                           for m in messages for ch in inactive)
                            checks.append(dict(mode=mode, event=event, waveform=values[f"{event}_waveform"],
                                               expected_strength={ch: strength_a if ch == "A" else strength_b for ch in active}))
                        sender.stop_output()
                        time.sleep(.3)
                        before = len(received)
                        time.sleep(1.1)
                        assert not any(str(m.get("message", "")).startswith("pulse-") for m in received[before:])
                        messages = [m.get("message") for m in received]
                        assert all(f"clear-{ch}" in messages and f"strength-{ch}+2+0" in messages for ch in (1, 2))
                    finally:
                        sender.safe_stop()
                        done.set()
                        if reader:
                            reader.join(2)
                        if app:
                            app.close()
                backend.close()
                assert backend.process.returncode == 0
            finally:
                backend.close()
    finally:
        sender.safe_stop()
        sender._DglabWSClient._show_qrcode = original_qr
        sender._get_lan_ip = original_ip
    return dict(status="ok", transport="real localhost WebSocket", peer="software APP only",
                channel_modes=["a", "b", "both"], checks=checks, stop_both=True,
                no_late_output=True, physical_device_used=False, user_phone_app_tested=False)
