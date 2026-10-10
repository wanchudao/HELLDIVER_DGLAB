"""Opt-in SDK v1 executable acceptance; no screen, socket, host or device access."""
import argparse
from collections import deque
import json
from pathlib import Path
import sys

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))

from dghub_plugin.sdk_config import EVENTS, defaults, manifest
from dghub_plugin.sdk_session import SDKSession
from dghub_plugin.preset_catalog import PresetInfo
from src.config_loader import load_config
from src.state_machine import DetectionResult


class MemoryLog:
    def __init__(self):
        self.rows = deque(maxlen=100)

    def append(self, row):
        self.rows.append(row)

    def close(self):
        pass


def self_check():
    checks = []
    for mode in ("a", "b", "both"):
        now = [0.0]
        config = defaults()
        config["channel_mode"] = mode
        for event, _label, _strength, _wave in EVENTS:
            config[event + "_preset"] = "验收-" + event
        config["low_health_strength_b"] = 7
        session = SDKSession(load_config(ROOT / "config.example.ini"), clock=lambda: now[0],
                             log_writer=MemoryLog(),
                             preset_catalog={"验收-" + event: PresetInfo("验收-" + event, 11, 3, False)
                                             for event, *_ in EVENTS},
                             available_presets={"验收-" + event for event, *_ in EVENTS})

        def require(condition, label):
            if not condition:
                raise RuntimeError("SDK acceptance failed: " + label)
            checks.append({"mode": mode, "check": label})

        def receive(**value):
            return session.receive(json.dumps(value))

        def frame(t, hp, valid=True):
            now[0] = t
            return session.observe(DetectionResult(health_raw=hp, health_stable=hp, valid=valid,
                                   confidence=1, anchor_score=1, state="injured" if valid else "unknown"),
                                   session.frame_ticket())

        def values():
            return session.vision.mock_output()["channels"]

        try:
            hello = session.hello("software-fixture-token")
            require(json.loads(hello.messages[0])["manifest"] == manifest(), "hello_manifest")
            receive(op="hello_ack", accepted=True, sdk_version="1.0")
            receive(op="config", data=config)
            receive(op="device_info", connected=True, device_type="v3", max_strength_a=200, max_strength_b=200)
            require(not session.armed and session.phase == "ready", "startup_paused")
            session.set_running(True)
            frame(0, 1)
            frame(.1, .7)
            frame(.15, .6)
            injured = frame(.31, .6)
            require(values()["A"]["strength"] == (0 if mode == "b" else 18)
                    and values()["B"]["strength"] == (0 if mode == "a" else 15), "cumulative_damage_dual_strength")
            require(any(json.loads(raw).get("preset") == "验收-injury_pulse" for raw in injured.messages), "injury_preset")
            healed = frame(.4, .8)
            require(any(json.loads(raw).get("preset") == "验收-heal_pulse" for raw in healed.messages), "heal_preset")
            dead = frame(.5, 0)
            require(any(json.loads(raw).get("preset") == "验收-death_pulse" for raw in dead.messages), "death_preset")
            frame(.6, 0)
            require(session.vision.trigger._death_latched, "death_latched")
            frame(.7, .3)
            frame(.8, 0, False)
            require(all(item["strength"] == 0 for item in values().values()), "invalid_hud_zero")
            frame(.9, 1)
            require(all(item["strength"] == 0 for item in values().values()), "baseline_rebuild")
            ticket = session.frame_ticket()
            queued = frame(1, .5)
            session.pause()
            require(not session.current(queued), "queued_output_invalidated")
            require(not session.observe(DetectionResult(), ticket).messages, "late_frame_rejected")
            session.close()
            receive(op="config_changed", key="running", value=True)
            require(session.closed and not session.armed, "terminal_stop")
        finally:
            session.close()
    return {"status": "ok", "edition": "dghub-sdk-v1-development-preview",
            "frozen": bool(getattr(sys, "frozen", False)), "checks": checks,
            "channel_modes": ["a", "b", "both"], "protocol": "PLUGIN_DEVELOPMENT.md SDK v1",
            "transport": "in-memory host messages; no WebSocket opened",
            "hardware_enabled": False, "installed_host_registration_enabled": False,
            "physical_device_tested": False, "installed_host_tested": False,
            "host_stop_acknowledgement_available": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--print-manifest", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.self_check:
        result = self_check()
    elif args.print_manifest:
        result = manifest()
    else:
        print("DGHub SDK v1 development preview: host registration is not released.\n"
              "Use --self-check for offline protocol acceptance.\n"
              "This is not an install-and-run hardware plugin; see the bundled guide.")
        return 2
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(content, encoding="utf-8")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
