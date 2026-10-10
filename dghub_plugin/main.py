"""DGHub plugin entry: shared local Mock only until the host lifecycle is fixed.

The audited host can restore an old nonzero baseline after stop/disconnect.
Even hello registration is therefore disabled. --offline opens the root safety
panel; --self-check runs a deterministic, network-free shared-core smoke test.
The small message pump below is retained for future protocol adapter tests,
but it cannot register with a host or send trigger/strength/config messages.
"""
import argparse
import asyncio
from collections import deque
import json
import logging
import os
from pathlib import Path
import sys

BUNDLE_DIR = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
              else Path(__file__).resolve().parent)
EXE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_DIR
sys.path.insert(0, str(BUNDLE_DIR))

from offline_core import (
    CORE_ROOT, HOST_BLOCK_REASON, MockVisionSession, assert_shared_core, load_config,
)
from src.state_machine import DetectionResult

logger = logging.getLogger("dghub_plugin")


def configure_logging():
    """Keep the existing flushed plugin log, but do not create files on import."""
    handlers = [logging.StreamHandler(sys.stderr)]
    for base in (EXE_DIR, Path(os.environ.get("TEMP", os.environ.get("TMP", "")))):
        if str(base) == ".":
            continue
        try:
            log_dir = base / "debug"
            log_dir.mkdir(exist_ok=True)
            # FileHandler.emit flushes every record, preserving crash diagnostics.
            handlers.append(logging.FileHandler(log_dir / "plugin.log", encoding="utf-8"))
            break
        except OSError:
            continue
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
    )


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return isinstance(value, str) and value.lower() in ("true", "1", "yes", "on")


class DGHubClient:
    """Disabled transport; the retained incoming-message pump is Mock-only."""

    def __init__(self, session=None):
        self._ws = None
        self._config = {}
        self._stop_requested = False
        self._last_status = ""
        self._log_queue = deque(maxlen=200)
        self.session = session

    async def connect(self):
        # No socket, token reading, hello, or capability probing: registration
        # itself may restore the host's cached nonzero baseline.
        logger.error(HOST_BLOCK_REASON)
        return False

    async def send_trigger(self, action_name, payload=None):
        logger.error("DGHub trigger rejected: %s", HOST_BLOCK_REASON)
        return {"dispatched": False, "reason": "unsupported_host", "mode": "disabled"}

    async def send_stop(self):
        # set_strength=0 cannot cancel that host's leases; don't pretend it can.
        self._terminate_session("host_stop")
        return {"dispatched": False, "reason": "unsupported_host", "mode": "disabled"}

    def _terminate_session(self, reason):
        # Stop/disconnect ends this instance permanently. A late config must
        # never reinterpret terminal shutdown as a temporary user pause.
        self._stop_requested = True
        if self.session is not None:
            self.session.close(reason)

    async def _send_telemetry(self, payload):
        # The public protocol helper cannot become a backdoor for strength,
        # trigger, configuration, registration, or preset messages.
        if (self._stop_requested or payload.get("op") not in {"status", "log", "pong"}
                or self._ws is None):
            return
        try:
            await asyncio.wait_for(self._ws.send(json.dumps(payload)), timeout=2.0)
        except Exception:
            logger.debug("DGHub telemetry unavailable", exc_info=True)

    async def send_status(self, display_status):
        if display_status == self._last_status:
            return
        await self._send_telemetry({"op": "status", "fields": {"display_status": display_status}})
        self._last_status = display_status

    async def send_log(self, level, message):
        await self._send_telemetry({"op": "log", "level": level, "message": message})

    async def flush_logs(self):
        while self._log_queue:
            await self.send_log(*self._log_queue.popleft())

    async def process_message(self, raw):
        """Retain config/ping/stop behavior without ever authorizing hardware."""
        if self._stop_requested:
            return
        try:
            message = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("DGHub ignored malformed JSON")
            return
        if not isinstance(message, dict):
            return
        op = message.get("op")
        if op == "config":
            data = message.get("data")
            if not isinstance(data, dict):
                return
            self._config.update(data)
            running = _parse_bool(self._config.get("running", False))
            if self.session is not None:
                self.session.set_running(running, "host_pause")
        elif op == "config_changed":
            key = message.get("key")
            if isinstance(key, str):
                self._config[key] = message.get("value")
                if key == "running" and self.session is not None:
                    self.session.set_running(_parse_bool(message.get("value")), "host_pause")
        elif op == "stop":
            self._terminate_session("host_stop")
        elif op == "ping":
            await self._send_telemetry({"op": "pong", "t": message.get("t")})
        elif op == "device_info":
            # Host acknowledgements and config values cannot lift the block.
            logger.info("Device status ignored: plugin is offline-only")

    async def handle_messages(self):
        """Only a caller-supplied test transport can currently reach this pump."""
        if self._ws is None:
            raise RuntimeError(HOST_BLOCK_REASON)
        try:
            async for raw in self._ws:
                await self.process_message(raw)
                if self._stop_requested:
                    break
        finally:
            await self.close()

    async def close(self):
        self._terminate_session("host_disconnect")
        if self._ws is not None:
            # Detach before awaiting: concurrent closes cannot act on a newer
            # transport or close this one twice.
            ws, self._ws = self._ws, None
            try:
                await asyncio.wait_for(ws.close(), timeout=2.0)
            except Exception:
                logger.debug("DGHub test transport close failed", exc_info=True)


def refuse_host():
    """Reject without even creating an event loop's platform wakeup sockets."""
    logger.error(HOST_BLOCK_REASON)
    return 2


async def main_async():
    """Compatibility entry for callers with an existing test event loop."""
    return refuse_host()


def default_config_path():
    # Sidecar overrides remain supported for local Mock testing only.
    for path in (EXE_DIR / "config.ini", BUNDLE_DIR / "config.ini",
                 CORE_ROOT / "config.example.ini"):
        if path.is_file():
            return path
    raise FileNotFoundError("No bundled Mock configuration found")


class _MemoryLog:
    def __init__(self):
        self.entries = []

    def append(self, entry):
        self.entries.append(dict(entry))

    def close(self):
        pass


def self_check():
    """Check original SPEC through the very same root Trigger and Mixer.

    Synthetic confirmed HP values test feedback composition, not image accuracy.
    No capture, transport, log file, or installed-host configuration is accessed.
    """
    assert_shared_core()
    config = load_config(CORE_ROOT / "config.example.ini")
    clock_value = [0.0]
    records = _MemoryLog()
    session = MockVisionSession(config, clock=lambda: clock_value[0], log_writer=records)

    def require(condition, check):
        # Never use assert for executable acceptance checks: Python -O removes
        # the expression itself, including dispatch/frame-processing calls.
        if not condition:
            raise RuntimeError(f"Shared-core self-check failed: {check}")

    def frame(timestamp, hp, state="injured", valid=True):
        clock_value[0] = timestamp
        return session.trigger.dispatch(DetectionResult(
            health_raw=hp, health_stable=hp, state=state, valid=valid,
            confidence=1, anchor_score=1,
        ))

    try:
        require(session.frame_ticket() is None, "startup_paused")
        frame(0, 1)
        frame(.1, .7)
        frame(.15, .6)
        injury = frame(.31, .6)
        injury_strength = session.mock_output()["strength"]
        require(injury["actions"] == ["injury_pulse"], "cumulative_damage")
        require(injury_strength == 18, "low_background_plus_damage")  # 10 + 40% * 20
        heal = frame(.4, .8)
        require(heal["actions"] == ["heal_pulse"] and heal["healing_sustain"], "healing_sustain")
        require(session.mock_output()["strength"] == 28, "healing_composition")
        require(frame(.5, 0)["actions"] == ["death_pulse"], "death")
        require(frame(.6, 0)["actions"] == [], "death_once")
        require(frame(.7, .3)["actions"] == ["heal_pulse"], "revival")
        frame(.8, 0, state="unknown", valid=False)
        require(session.mock_output()["strength"] == 0, "invalid_zero")
        session.set_running(True)
        ticket = session.frame_ticket()
        session.set_running(False)
        require(session.process_frame(object(), ticket) is None, "late_frame_rejected")
        session.close()
        require(not session.set_running(True) and session.frame_ticket() is None, "terminal_close")
        return {
            "status": "ok", "mode": "mock", "channel": "A",
            "core_root": str(CORE_ROOT), "hardware_enabled": False,
            "registration_enabled": False, "injury_window_sec": .2,
            "low_health_hp_threshold": .75, "healing_sustain": True,
            "cumulative_damage_strength_with_low_background": injury_strength,
            "checks": ["shared_core", "cumulative_damage", "low_health",
                       "healing_sustain", "death_once", "revival", "invalid_zero",
                       "late_frame_rejected", "terminal_close"],
        }
    finally:
        session.close()


def run_offline(config_path, *, preview=False, fps=30):
    # Lazy import: self-check/default blocked launch never starts screen capture
    # and works without importing the Windows-only real-time entry point.
    assert_shared_core()
    from src.runner_real import main as run_shared_panel
    args = ["--enable-trigger", "--sender-mode", "mock",
            "--config", str(config_path), "--fps", str(fps)]
    if preview:
        args.append("--preview")
    run_shared_panel(args)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="HELLDIVER_DGLAB: shared-core local Mock only")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Open the local Mock safety panel")
    mode.add_argument("--self-check", action="store_true", help="Run a network-free SPEC smoke test")
    parser.add_argument("--config", type=Path, help="Configuration for the offline panel")
    parser.add_argument("--preview", action="store_true", help="Show local detection preview")
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args(argv)
    if args.fps < 0:
        parser.error("--fps must be non-negative")
    if args.self_check:
        print(json.dumps(self_check(), ensure_ascii=False, indent=2))
        return 0
    configure_logging()
    if args.offline:
        logger.info("Local Mock only; DGHub registration and hardware remain disabled")
        return run_offline(args.config or default_config_path(), preview=args.preview, fps=args.fps)
    return refuse_host()


if __name__ == "__main__":
    raise SystemExit(main())
