"""Suspend only an owned frozen plugin child; SDK peer/lease rollback is modeled.

Never launches or suspends installed DGHub, never captures the desktop, and
never changes Windows security settings. Native SDK method evidence remains
separate from this genuine process-suspension/software-peer check.
"""
import argparse
import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OwnedProcessSuspender:
    """Use the already-held child handle; no process lookup or OpenProcess."""
    def __init__(self, child, *, nt_suspend=None, nt_resume=None):
        self.child = child
        self.pid = child.pid
        self.handle = child._handle
        if nt_suspend is None or nt_resume is None:
            if os.name != "nt" or not isinstance(child, subprocess.Popen):
                raise ValueError("This check requires a Windows child spawned by this runner")
            library = ctypes.WinDLL("ntdll")
            nt_suspend, nt_resume = library.NtSuspendProcess, library.NtResumeProcess
            for function in (nt_suspend, nt_resume):
                function.argtypes = (wintypes.HANDLE,)
                function.restype = wintypes.LONG
        self._suspend, self._resume = nt_suspend, nt_resume
        self.suspended = False
        self.suspension_succeeded = self.resume_succeeded = False

    def _check_child(self):
        if self.child.pid != self.pid or self.child._handle != self.handle or self.child.poll() is not None:
            raise RuntimeError("Owned child identity changed or process already exited")

    def suspend(self):
        self._check_child()
        if self.suspended:
            raise RuntimeError("Owned child is already suspended")
        if self._suspend(self.handle) != 0:
            raise RuntimeError("Owned child suspension failed")
        self.suspended = self.suspension_succeeded = True

    def resume(self):
        if not self.suspended:
            return
        self._check_child()
        if self._resume(self.handle) != 0:
            raise RuntimeError("Owned child resume failed")
        self.suspended = False
        self.resume_succeeded = True


class SoftwareLeaseModel:
    """Received-request expiry model; NOT the native RemoteAdapter/device."""
    def __init__(self):
        self.values = {"a": 0, "b": 0}
        self.expires_at = {"a": 0.0, "b": 0.0}
        self.last_positive_at = {"a": None, "b": None}

    def receive(self, packet, now):
        from tools.dghub_software_acceptance import strength_request
        request = strength_request(packet)
        if request is None:
            return
        channel, value = request
        if channel not in self.values or type(value) is not int or not 0 <= value <= 100:
            raise ValueError("Invalid submitted strength")
        if value:
            if (packet.get("op") != "trigger" or packet.get("action") != "strength"
                    or packet.get("strength_mode") != "rollback" or packet.get("duration_s") != .30):
                raise ValueError("Expected revised 0.30-second rollback request")
            self.expires_at[channel] = max(self.expires_at[channel], now + packet["duration_s"])
            self.last_positive_at[channel] = now
        self.values[channel] = value

    def snapshot(self, now):
        return {channel: value if now < self.expires_at[channel] else 0
                for channel, value in self.values.items()}


async def _wait_child(child, timeout):
    deadline = time.monotonic() + timeout
    while child.poll() is None:
        if time.monotonic() >= deadline:
            raise TimeoutError("Owned child did not exit")
        await asyncio.sleep(.02)
    return child.returncode


async def run_case(exe):
    import cv2
    from websockets.asyncio.server import serve
    from dghub_plugin.sdk_config import defaults, manifest
    from tools.dghub_software_acceptance import (software_catalog_response, strength_request, synthetic_frame)
    token, packets, receipt_times = uuid.uuid4().hex, [], []
    state = dict(catalog_requests=0, capture_ready=False, output_ready=False, armed=False,
                 pause_seen_after_resume=False, baseline_cleared_after_resume=False,
                 resume_pause_reasons=[], complete=False)
    model = SoftwareLeaseModel()
    child = controller = freeze_task = None
    frozen_snapshots = []
    failure = None

    async def freeze(ws):
        nonlocal controller
        controller = OwnedProcessSuspender(child)
        controller.suspend()
        state["freeze_started_at"] = time.monotonic()
        try:
            # Longer than the plugin's one-second frame watchdog. Continue
            # reading its socket, including bytes already queued at suspend.
            deadline = state["freeze_started_at"] + 1.20
            while time.monotonic() < deadline:
                frozen_snapshots.append(dict(at=time.monotonic(), values=model.snapshot(time.monotonic())))
                await asyncio.sleep(.01)
        finally:
            controller.resume()
            state["resumed_at"] = time.monotonic()
        # No config/running restart is sent. A resumed old frame/timer must not
        # restore output before the stale-frame watchdog pauses the session.
        await asyncio.sleep(.40)
        state["resume_observation_ended_at"] = time.monotonic()
        state["complete"] = True
        await ws.send(json.dumps(dict(op="stop", reason="software_process_freeze_finished")))

    async def handler(ws):
        nonlocal freeze_task
        assert parse_qs(urlsplit(ws.request.path).query).get("token") == [token]
        async for raw in ws:
            packet = json.loads(raw)
            stamp = time.monotonic()
            packets.append(packet | {"token": "<redacted>"} if packet.get("op") == "hello" else packet)
            receipt_times.append(stamp)
            if packet.get("op") == "hello":
                assert packet.get("token") == token and packet.get("manifest") == manifest()
                for reply in [dict(op="hello_ack", accepted=True, sdk_version="1.0"),
                    dict(op="config", data=defaults() | dict(channel_mode="both")),
                    dict(op="device_info", connected=True, device_type="v3", max_strength_a=200, max_strength_b=200)]:
                    await ws.send(json.dumps(reply))
                continue
            model.receive(packet, stamp)
            fields = packet.get("fields", {}) if packet.get("op") == "status" else {}
            state["capture_ready"] |= fields.get("capture_state") == "ok"
            state["output_ready"] |= fields.get("output_ready") is True
            if state["capture_ready"] and state["output_ready"] and not state["armed"]:
                state["armed"] = True
                (root / "controls.json").write_text(json.dumps(dict(sequence=1, running=True)), encoding="utf-8")
            if (freeze_task is None and all(value > 0 for value in model.snapshot(stamp).values())):
                freeze_task = asyncio.create_task(freeze(ws))
            if state.get("resumed_at") is not None:
                # The tool's later host_stop is not watchdog evidence. Only
                # capture/frame expiry before the observation window ends
                # counts, and the baseline must also have been discarded.
                if (stamp < state.get("resume_observation_ended_at", float("inf"))
                        and fields.get("running") is False
                        and fields.get("display_status") in {"frame_timeout", "stale_capture"}):
                    state["pause_seen_after_resume"] = True
                    state["resume_pause_reasons"].append(fields["display_status"])
                    state["baseline_cleared_after_resume"] |= (fields.get("hp") is None
                        and fields.get("requested_a_pct") == fields.get("requested_b_pct") == 0)

    with tempfile.TemporaryDirectory(prefix="dghub-owned-process-freeze-") as directory:
        root = Path(directory)
        (root / "manifest.json").write_text(json.dumps(manifest(), ensure_ascii=False), encoding="utf-8")
        for name, hp in (("full.png", 1), ("low.png", .6)):
            if not cv2.imwrite(str(root / name), synthetic_frame(100, hp)):
                raise RuntimeError("Synthetic fixture creation failed")
        (root / "replay.json").write_text(json.dumps(dict(synthetic=True,
            frames=["full.png"] * 10 + ["low.png"] * 400)), encoding="utf-8")
        async with serve(handler, "127.0.0.1", 0, max_size=65536, close_timeout=.5,
            process_request=lambda _connection, request: software_catalog_response(request, state)) as server:
            environment = {key: value for key, value in os.environ.items()
                if not key.startswith("DGHUB_") and key not in {"PYTHONHOME", "PYTHONPATH"}}
            environment.update(PATH="", DGHUB_HOST="127.0.0.1", DGHUB_PORT=str(server.sockets[0].getsockname()[1]),
                DGHUB_TOKEN=token, DGHUB_PLUGIN_ID=manifest()["id"], DGHUB_PLUGIN_ROOT=str(root))
            child = subprocess.Popen([str(exe), "--software-replay-test", str(root)], cwd=root,
                env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                code = await _wait_child(child, 12)
                if freeze_task is not None:
                    await asyncio.wait_for(freeze_task, .5)
            except Exception as exc:
                failure = type(exc).__name__
                code = 124 if isinstance(exc, TimeoutError) else 1
            finally:
                if controller is not None and controller.suspended:
                    try:
                        controller.resume()
                    except Exception:
                        failure = "OwnedChildResumeFailed"
                if freeze_task is not None and not freeze_task.done():
                    freeze_task.cancel()
                    await asyncio.gather(freeze_task, return_exceptions=True)
                if child.poll() is None:
                    child.terminate()
                    try:
                        await _wait_child(child, 2)
                    except TimeoutError:
                        child.kill()
                        await _wait_child(child, 2)
                child_handle = child._handle
                child_handle.Close()
        records = list((root / "sessions").glob("*/session.json"))
        metadata = json.loads(records[0].read_text(encoding="utf-8")) if len(records) == 1 else {}
    start, resumed = state.get("freeze_started_at"), state.get("resumed_at")
    positive = [(stamp, packet) for stamp, packet in zip(receipt_times, packets, strict=True)
                if strength_request(packet) is not None and strength_request(packet)[1] > 0]
    during = [(stamp, packet) for stamp, packet in positive
              if start is not None and resumed is not None and start + .10 <= stamp < resumed]
    after = [(stamp, packet) for stamp, packet in positive if resumed is not None and stamp >= resumed]
    model_zero = bool(frozen_snapshots) and frozen_snapshots[-1]["values"] == {"a": 0, "b": 0}
    zero_snapshots = [row["at"] for row in frozen_snapshots if row["values"] == {"a": 0, "b": 0}]
    zero_at = zero_snapshots[0] if zero_snapshots else None
    final_zeros = {packet.get("channel") for stamp, packet in zip(receipt_times, packets, strict=True)
                   if resumed is not None and stamp >= resumed and packet.get("op") == "set_strength"
                   and packet.get("pct") == 0}
    suspended = controller is not None and controller.suspension_succeeded and controller.resume_succeeded
    frozen_seconds = resumed - start if start is not None and resumed is not None else None
    ok = (failure is None and code == 0 and suspended and frozen_seconds >= 1.20
        and state["complete"] and not during and model_zero and not after
        and state["pause_seen_after_resume"] and state["baseline_cleared_after_resume"]
        and final_zeros == {"a", "b"}
        and metadata.get("state") == "complete" and metadata.get("runtime_error") is False)
    return dict(status="ok" if ok else "incomplete", exit_code=code, hard_timeout=failure == "TimeoutError",
        failure=failure, true_process_suspension_tested=suspended, suspended_seconds=frozen_seconds,
        owned_child_only=True, suspended_process="owned final plugin EXE child; not DGHub",
        no_new_positive_during_suspension=not during, software_model_both_zero_during_freeze=model_zero,
        software_model_zero_at=zero_at, last_positive_received_at=model.last_positive_at,
        resumed_without_rearming=True, pause_seen_after_resume=state["pause_seen_after_resume"],
        resume_pause_reasons=state["resume_pause_reasons"],
        baseline_cleared_after_resume=state["baseline_cleared_after_resume"],
        no_positive_after_resume=not after, positive_after_resume=after, final_both_zero=final_zeros == {"a", "b"},
        model="software peer received time + SDK duration_s; NOT native host or device rollback",
        native_method_evidence="separate native SDK strategy report; not inferred from this model",
        exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(), path_empty=True, python_environment_removed=True,
        state=state, messages=packets, receipt_times=receipt_times, frozen_snapshots=frozen_snapshots,
        session_metadata=metadata, native_host_tested=False, real_game_tested=False, physical_device_used=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    exe = args.bundle.resolve() / "helldiver_dglab.exe"
    try:
        if os.name != "nt" or not exe.is_file():
            raise ValueError("Owned Windows final EXE is required")
        report = asyncio.run(run_case(exe))
    except Exception as exc:
        report = dict(status="incomplete", failure=type(exc).__name__, true_process_suspension_tested=False,
                      native_host_tested=False, physical_device_used=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                     if key not in {"messages", "receipt_times", "frozen_snapshots", "session_metadata"}},
                     ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
