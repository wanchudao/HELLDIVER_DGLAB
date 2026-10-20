"""Actual DGHub 1.9.6 methods, isolated settings and a memory-only V3 client.

The analyst launcher copies the EXE into an owned build fixture and invokes only
its --plugin-runner. The native runner forbids networking and further children.
It does not start DGHub's app, HTTP server, installed plugins or real devices.
Native method evidence is not a real WebSocket, process-freeze or GUI test.
"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
HOST_SHA256 = "86a6fd69060588631d023ae97bb3cb58370cd1cf898b8612c7c52a43a55301a2"
VERIFIED_HOSTS = {HOST_SHA256: "1.9.6",
    "4734915664f45476111ea1b7590024f396784af869f71086d721313fbe5a7dc5": "1.9.7"}
NATIVE_ROOT_ENV = "HELLDIVER_NATIVE_STRATEGY_ROOT"
REPORT_ENV = "HELLDIVER_NATIVE_STRATEGY_REPORT"
SOURCE_UNITS = ("dghub_plugin/sdk_session.py", "dghub_plugin/sdk_transport.py", "dghub_plugin/preset_catalog.py")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owned_build_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT / "build").resolve()) or path == (ROOT / "build").resolve():
        raise ValueError("Native strategy artifacts must remain under an owned build directory")
    return path


def validate_report(report):
    """Validate evidence scope, not promote a method probe to host acceptance."""
    if not isinstance(report, dict) or report.get("status") != "observed":
        raise ValueError("Native strategy execution did not complete")
    if report.get("host_exe_sha256", "").lower() not in VERIFIED_HOSTS:
        raise ValueError("Unverified native host build")
    flags = ("installed_host_modified", "installed_config_modified", "network_used",
             "physical_device_used", "native_gui_tested", "real_frozen_process_tested")
    if any(report.get(key) is not False for key in flags):
        raise ValueError("Native strategy report has expanded or ambiguous evidence scope")
    if report.get("actual_route_token_predicate") is not True or report.get("actual_source_layers") is not True:
        raise ValueError("Native route and source-layer evidence is missing")
    required = {"lease", "generation", "retirement", "unregister_restart", "other_source",
                "waveforms", "long_waveform", "runtime_disconnect", "low_level"}
    if not required.issubset(report.get("cases", {})):
        raise ValueError("Native strategy cases are missing")
    lease = report["cases"]["lease"]
    if (lease.get("snapshots") != [dict(a=60,b=25),dict(a=60,b=25),dict(a=20,b=10)]
            or any(lease.get(key)!=dict(a=0,b=0)
                   for key in ("immediate_zero", "after_old_leases", "stopped_renewing"))
            or lease.get("duration_s")!=.3):
        raise ValueError("Independent short-lease and zero evidence is missing")
    if (report["cases"]["generation"].get("old_token_invalidated") is not True
            or report["cases"]["retirement"].get("old_token_invalidated") is not True
            or report["cases"]["waveforms"].get("full_template_preserved") is not True
            or report["cases"]["waveforms"].get("truncated_native_match") is not True
            or report["cases"]["long_waveform"].get("real_frames_preserved") is not True
            or report["cases"]["runtime_disconnect"].get("removed_from_manager") is not True):
        raise ValueError("Actual routing, waveform or disconnect chain evidence is missing")
    return report


def verify_standard(report):
    """Verify selected SDK behavior, retaining counterexamples and scope flags."""
    validate_report(report)
    cases = report["cases"]
    restart, lease = cases["unregister_restart"], cases["lease"]
    presets = cases["waveforms"].get("full_default_presets", {})
    required_presets = {"连击", "呼吸", "潮汐", "心跳节奏", "CS2-死亡"}
    if (set(presets)!=required_presets or not all(row.get("both_channels_preserved") is True
                                                for row in presets.values())):
        raise ValueError("Five full native default templates are not verified")
    if (restart.get("settled_new_layer_maintained") is not True
            or restart.get("guard_elapsed_s",0)<.5
            or restart.get("settled_unexpected_zero_device_writes")!=0
            or restart.get("known_short_lease_settle_wait_s")!=.5):
        raise ValueError("Post-registration short-lease drain barrier is not verified")
    if (not 0<=lease.get("return_delay_from_last_request_s",1)<=.55
            or cases["other_source"].get("other_preserved") is not True
            or cases["long_waveform"].get("other_template_last") is not True
            or cases["long_waveform"].get("pending_after_other_source_replacement")!=0):
        raise ValueError("Chosen lease/source-owner chain is not verified")
    if report.get("source_unchanged_during_probe") is not True:
        raise ValueError("Implementation source changed during the native probe")
    result = deepcopy(report)
    result.update(status="ok", raw_status="observed", sdk_standard_verified=True,
        verification_scope="Chosen SDK messages and post-registration .5s guard in the native method fixture; not final EXE or gameplay",
        source_hash_scope="Provenance binding only; native runner does not execute the plugin source modules")
    return result


class MemoryConfig:
    """Only settings, never the host ConfigManager or installed files."""
    def __init__(self):
        self.plugins = {}
        self.writes = []

    def get_plugin(self, name, key, default=None):
        return self.plugins.get(name, {}).get(key, default)

    def get_plugin_config(self, name):
        return dict(self.plugins.get(name, {}))

    def get_global(self, key, default=None):
        return "v3" if key == "device_type" else default

    def set_plugin(self, name, key, value):
        self.plugins.setdefault(name, {})[key] = value
        self.writes.append((name, key, value))


class MemoryWebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))

    async def receive_text(self):
        value = await self.incoming.get()
        if value is None:
            raise ConnectionError("Owned memory WebSocket disconnected")
        return value

    async def close(self, *args, **kwargs):
        self.closed = True
        self.incoming.put_nowait(None)


class MemoryClient:
    def __init__(self):
        self.rows = []

    @staticmethod
    def channel_name(channel):
        name = getattr(channel, "name", str(channel)).lower()
        return "a" if name == "a" else "b" if name == "b" else name

    async def set_strength(self, channel, operation, value):
        self.rows.append(dict(op="strength", channel=self.channel_name(channel), value=value,
                              t=time.monotonic()))

    async def add_pulses(self, channel, *frames):
        self.rows.append(dict(op="pulse", channel=self.channel_name(channel), frames=deepcopy(list(frames)),
                              count=len(frames), t=time.monotonic()))

    async def clear_pulses(self, channel):
        self.rows.append(dict(op="clear", channel=self.channel_name(channel), t=time.monotonic()))


class MemoryNotifier:
    def __init__(self):
        self.rows = []

    def submit_event(self, name, event, **kwargs):
        self.rows.append(dict(plugin=name, event=deepcopy(event), source_token_present=bool(kwargs)))


def forbidden(*args, **kwargs):
    raise RuntimeError("Native strategy runner forbids network and additional child processes")


async def native_cases(fixture):
    from pydantic import TypeAdapter
    from src.adapters.manager import AdapterManager
    from src.adapters.remote import PluginRuntime, RemoteAdapter
    from src.adapters.sdk import protocol
    from src.api.plugin_ws import _on_disconnect_factory
    from src.device.controller import DGLabV3Controller, PULSE_DATA_MAX_LENGTH
    from src.device.preset_manager import PresetManager

    config, client, notifier = MemoryConfig(), MemoryClient(), MemoryNotifier()
    device = DGLabV3Controller("127.0.0.1")
    device.client, device.is_connected = client, True
    device.max_strength_A = device.max_strength_B = 100
    generation = [1]

    def device_info(target=None):
        return dict(device.get_device_info(target), device_type="v3", binding_generation=generation[0])

    presets = PresetManager(str(fixture))
    manager = AdapterManager(config_manager=config, dglab_queue=device.queue,
        dglab_info_fn=device_info, preset_manager=presets, strength_layers=device.strength_layers,
        event_sound_notifier=notifier)
    processor = asyncio.create_task(device._process_queue())
    adapters, runtimes = [], []
    validator = TypeAdapter(protocol.ClientMsg)
    cases = {}

    async def make(name):
        manifest = protocol.PluginManifest(id=name, name=name, version="0.0.1", sdk="1")
        adapter = RemoteAdapter(config=config, plugin_name=name, command_queue=device.queue,
            device_info_fn=device_info, preset_manager=presets, strength_layers=device.strength_layers,
            manifest=manifest, event_sound_notifier=notifier)
        registered = await manager.register_remote(name, manifest.model_dump(), adapter)
        if registered is not True:
            raise AssertionError("Owned native fixture registration was rejected")
        ws = MemoryWebSocket()
        runtime = PluginRuntime(websocket=ws, adapter=adapter, manager=manager,
            config_manager=config, on_disconnect=_on_disconnect_factory(manager))
        adapter.runtime = runtime
        adapters.append(adapter)
        runtimes.append(runtime)
        return adapter, runtime, ws

    async def send(runtime, packet):
        validator.validate_python(packet)
        await runtime._handle_raw(json.dumps(packet, ensure_ascii=False))
        await asyncio.sleep(.015)

    def strength(name):
        return device.strength_layers.get_plugin_strength(name)

    def strength_packet(value, channel):
        return dict(op="trigger", action="strength", delta_pct=value, channel=channel,
                    strength_mode="rollback", duration_s=.3)

    async def zeros(runtime):
        for channel in ("a", "b"):
            await send(runtime, dict(op="set_strength", channel=channel, pct=0))

    async def wait_until(deadline):
        while time.monotonic() < deadline:
            await asyncio.sleep(max(.005, min(.05, deadline-time.monotonic())))

    try:
        adapter, runtime, _ws = await make("lease_fixture")
        token = adapter.capture_message_work(None)
        assert token is not None and adapter.is_target_work_current(token)
        snapshots = []
        renewals = []
        for a, b in ((60, 25), (60, 25), (20, 10)):
            renewals.append(time.monotonic())
            await send(runtime, strength_packet(a, "a"))
            await send(runtime, strength_packet(b, "b"))
            snapshots.append(strength("lease_fixture"))
            await wait_until(renewals[-1]+.1)
        await zeros(runtime)
        immediate = strength("lease_fixture")
        await asyncio.sleep(.35)
        after_old_leases = strength("lease_fixture")
        started = time.monotonic()
        await send(runtime, strength_packet(45, "a"))
        await send(runtime, strength_packet(15, "b"))
        last_positive_at = time.monotonic()
        await asyncio.sleep(.40)
        expired = strength("lease_fixture")
        zero_rows = [row for row in client.rows if row["op"] == "strength" and row["value"] == 0
                     and row["t"] >= started]
        cases["lease"] = dict(snapshots=snapshots, immediate_zero=immediate,
            after_old_leases=after_old_leases, stopped_renewing=expired, duration_s=.3,
            requested_renewal_step_s=.1, observed_renewal_intervals=[round(y-x, 4) for x,y in zip(renewals, renewals[1:])],
            return_delay_from_last_request_s=round(max(row["t"] for row in zero_rows)-last_positive_at, 4),
            cards_created=len(adapter._recent_events), notifier_submissions=len(notifier.rows),
            scope="Actual host tasks with no more producer requests; not a suspended process")

        await send(runtime, strength_packet(55, "a"))
        old = adapter.capture_message_work(None)
        generation[0] += 1
        invalid = not adapter.is_target_work_current(old)
        await zeros(runtime)
        await asyncio.sleep(.35)
        cases["generation"] = dict(old_token_invalidated=invalid, final_layer=strength("lease_fixture"),
            actual_predicate=True, binding_info_source="Memory software-device generation")

        token = adapter.capture_message_work(None)
        adapter.begin_target_retirement(token.target_id, route_id=token.route_id)
        invalidated = not adapter.is_target_work_current(token)
        await adapter.retire_target(token.target_id, route_id=token.route_id)
        adapter.finish_target_retirement(token.target_id, route_id=token.route_id)
        cases["retirement"] = dict(old_token_invalidated=invalidated, final_layer=strength("lease_fixture"))

        old, old_runtime, _ = await make("restart_fixture")
        await send(old_runtime, strength_packet(65, "a"))
        old_token = old.capture_message_work(None)
        await manager.unregister_remote("restart_fixture")
        removed = strength("restart_fixture")
        new, new_runtime, _ = await make("restart_fixture")
        await send(new_runtime, strength_packet(30, "a"))
        initial_new = strength("restart_fixture")
        old_tasks_after_unregister = len(old._lease_tasks)
        restart_rows_start = len(client.rows)
        held_new = []
        restart_started = time.monotonic()
        next_renewal = restart_started+.1
        while time.monotonic() < restart_started+.45:
            if time.monotonic() >= next_renewal:
                await send(new_runtime, strength_packet(30, "a"))
                next_renewal = time.monotonic()+.1
            held_new.append(dict(t=round(time.monotonic()-restart_started,4),
                                 strength=strength("restart_fixture").get("a",0)))
            await asyncio.sleep(.005)
        old_expiry = strength("restart_fixture")
        unexpected_zero_rows = [row for row in client.rows[restart_rows_start:]
                                if row["op"]=="strength" and row["channel"]=="a" and row["value"]==0]
        cases["unregister_restart"] = dict(layer_after_unregister=removed, new_initial_layer=initial_new,
            new_layer_at_old_expiry=old_expiry, old_token_current_after_unregister=old.is_target_work_current(old_token),
            old_lease_tasks_after_unregister=old_tasks_after_unregister, actual_manager_unregister=True,
            continuous_new_layer_samples=held_new, unexpected_zero_device_writes=len(unexpected_zero_rows),
            same_id_isolation_proven=all(row["strength"]==30 for row in held_new) and not unexpected_zero_rows)
        await zeros(new_runtime)
        await manager.unregister_remote("restart_fixture")
        settled, settled_runtime, _ = await make("restart_fixture")
        await zeros(settled_runtime)
        guard_started = time.monotonic()
        await wait_until(guard_started+.5)
        guard_elapsed = time.monotonic()-guard_started
        settled_rows_start = len(client.rows)
        await send(settled_runtime, strength_packet(30,"a"))
        settled_samples = []
        until = time.monotonic()+.45
        renew_at = time.monotonic()+.1
        while time.monotonic()<until:
            if time.monotonic()>=renew_at:
                await send(settled_runtime,strength_packet(30,"a"))
                renew_at = time.monotonic()+.1
            settled_samples.append(strength("restart_fixture").get("a",0))
            await asyncio.sleep(.005)
        cases["unregister_restart"].update(known_short_lease_settle_wait_s=.5,
            guard_elapsed_s=round(guard_elapsed,4),
            guard_phase="After actual native registration, before first nonzero request",
            settled_new_layer_maintained=all(value==30 for value in settled_samples),
            settled_unexpected_zero_device_writes=len([row for row in client.rows[settled_rows_start:]
                if row["op"]=="strength" and row["channel"]=="a" and row["value"]==0]),
            settle_scope="Only known .3-second leases and a responsive native event loop")
        await zeros(settled_runtime)

        other, other_runtime, _ = await make("other_fixture")
        await send(other_runtime, dict(op="set_strength", channel="a", pct=70))
        await send(runtime, strength_packet(60, "a"))
        await zeros(runtime)
        await asyncio.sleep(.35)
        cases["other_source"] = dict(other_layer=strength("other_fixture"), own_layer=strength("lease_fixture"),
            other_preserved=strength("other_fixture").get("a", 0) == 70)

        before = len(client.rows)
        await send(runtime, dict(op="pulse", preset="连击", channel="both"))
        expected = presets.get_waveform("连击", device_family="coyote")
        full = [row for row in client.rows[before:] if row["op"] == "pulse"]
        before = len(client.rows)
        await send(runtime, dict(op="trigger", action="waveform", preset="呼吸", channel="a", duration_s=.3))
        cropped = [row for row in client.rows[before:] if row["op"] == "pulse"]
        breathing = presets.get_waveform("呼吸", device_family="coyote")
        cases["waveforms"] = dict(full_both_channels=sorted(row["channel"] for row in full),
            full_template_frames=len(expected), full_template_preserved=len(full)==2 and all(row["frames"]==expected for row in full),
            truncated_frame_count=cropped[-1]["count"], truncated_native_match=cropped[-1]["frames"]==RemoteAdapter._build_frames(breathing,.3))
        full_defaults = {}
        for name in ("连击", "呼吸", "潮汐", "心跳节奏", "CS2-死亡"):
            before = len(client.rows)
            await send(runtime, dict(op="pulse", preset=name, channel="both"))
            native_template = presets.get_waveform(name, device_family="coyote")
            observed = [row for row in client.rows[before:] if row["op"]=="pulse"]
            full_defaults[name] = dict(frames=len(native_template), period_s=len(native_template)/10,
                both_channels_preserved=len(observed)==2 and sorted(row["channel"] for row in observed)==["a","b"]
                and all(row["frames"]==native_template for row in observed))
        cases["waveforms"]["full_default_presets"] = full_defaults

        long_wave = [[[10,10,10,10], [index % 101]*4] for index in range(PULSE_DATA_MAX_LENGTH+1)]
        presets.save_preset("fixture_long", long_wave, device_family="coyote")
        before = len(client.rows)
        await send(runtime, dict(op="pulse", preset="fixture_long", channel="a"))
        pending_after_full = len([task for task in device._pulse_tasks.values() if not task.done()])
        await zeros(runtime)
        still_pending = len([task for task in device._pulse_tasks.values() if not task.done()])
        await asyncio.wait_for(asyncio.gather(*tuple(device._pulse_tasks.values())), 12)
        long_rows = [row for row in client.rows[before:] if row["op"]=="pulse"]
        actual = [frame for row in long_rows for frame in row["frames"]]
        before_replace = len(client.rows)
        await send(runtime, dict(op="pulse", preset="fixture_long", channel="a"))
        await send(other_runtime, dict(op="pulse", preset="连击", channel="a"))
        task_after_replace = len([task for task in device._pulse_tasks.values() if not task.done()])
        replacement_rows = [row for row in client.rows[before_replace:] if row["op"]=="pulse"]
        cases["long_waveform"] = dict(batch_sizes=[row["count"] for row in long_rows],
            real_frames_preserved=actual==long_wave, pending_after_full=pending_after_full,
            zero_leaves_pulse_task=still_pending>0, pulse_clear_called=any(row["op"]=="clear" for row in client.rows[before:]),
            source_owner_after_replacement=str(device._pulse_owners.get("a")),
            pending_after_other_source_replacement=task_after_replace,
            other_template_last=replacement_rows[-1]["frames"]==expected,
            real_batch_delay_s=round(long_rows[-1]["t"]-long_rows[0]["t"],4))

        disconnect_adapter, disconnect_runtime, disconnect_ws = await make("disconnect_fixture")
        reader = asyncio.create_task(disconnect_runtime.run())
        await send(disconnect_runtime, dict(op="set_strength", channel="a", pct=40))
        await disconnect_ws.close()
        await asyncio.wait_for(reader, 1)
        cases["runtime_disconnect"] = dict(layer=strength("disconnect_fixture"),
            removed_from_manager="disconnect_fixture" not in manager.get_active_adapters(),
            native_finally_used=True, memory_websocket=True)

        low, low_runtime, _ = await make("low_level_fixture")
        cards_before, sound_before = len(low._recent_events), len(notifier.rows)
        await send(low_runtime, dict(op="set_strength", channel="a", pct=22))
        await send(low_runtime, dict(op="pulse", preset="连击", channel="a"))
        await zeros(low_runtime)
        cases["low_level"] = dict(cards_created=len(low._recent_events)-cards_before,
            notifier_submissions=len(notifier.rows)-sound_before,
            remaining_lease_tasks=len(low._lease_tasks), final_layer=strength("low_level_fixture"))
        return dict(status="observed", cases=cases, actual_route_token_predicate=True, actual_source_layers=True,
            guarded_short_lease_test_passed=cases["unregister_restart"]["settled_new_layer_maintained"],
            unguarded_restart_counterexample_observed=not cases["unregister_restart"]["same_id_isolation_proven"],
            scope="Actual SDK/runtime/manager/layers/V3 queue methods with memory settings, WebSocket and final client",
            actual_network_ws_tested=False, real_frozen_process_tested=False, native_gui_tested=False,
            physical_device_used=False, installed_host_modified=False, installed_config_modified=False,
            network_used=False, physical_pulse_clear_ack_tested=False, actual_audio_playback_tested=False,
            execution_completed_not_full_plugin_acceptance=True,
            native_limitations=["Each strength trigger creates a card and submits to the notifier",
                "Immediate same-ID restart may be zeroed by the retired adapter's lease",
                "Zero strength does not cancel native long-pulse batches",
                "No-request expiry is not a real suspended-process timing test"])
    finally:
        for adapter in adapters:
            await adapter.stop()
        processor.cancel()
        await asyncio.gather(processor, return_exceptions=True)
        for task in tuple(device._pulse_tasks.values()):
            task.cancel()
        await asyncio.gather(*tuple(device._pulse_tasks.values()), return_exceptions=True)
        device.strength_layers.cancel_pending_sync()


def run_native():
    native_root = Path(os.environ[NATIVE_ROOT_ENV]).resolve()
    report_path = Path(os.environ[REPORT_ENV]).resolve()
    if (not getattr(sys, "frozen", False) or sys.version_info[:2]!=(3,11)
            or Path(sys.executable).parent.resolve()!=native_root
            or not native_root.is_relative_to((ROOT/"build").resolve())
            or not report_path.is_relative_to(native_root)
            or sha256(sys.executable) not in VERIFIED_HOSTS):
        raise RuntimeError("Only the owned inspected DGHub copy may run the native probe")
    fixture = Path(tempfile.mkdtemp(prefix="native_data_", dir=native_root))
    os.chdir(fixture)
    source_hashes = {name:sha256(ROOT/name) for name in SOURCE_UNITS}
    probe_hash = sha256(__file__)
    async def guarded():
        # Windows asyncio creates its internal socketpair during loop creation.
        # Block application networking after that internal loop is established.
        socket.socket.connect = socket.socket.connect_ex = forbidden
        socket.create_connection = forbidden
        subprocess.Popen = forbidden
        return await asyncio.wait_for(native_cases(fixture), 25)
    try:
        report = asyncio.run(guarded())
        report.update(host_exe_sha256=sha256(sys.executable), probe_source_sha256=probe_hash,
            host_version=VERIFIED_HOSTS[sha256(sys.executable)],
            implementation_source_sha256=source_hashes,
            source_unchanged_during_probe=source_hashes=={name:sha256(ROOT/name) for name in SOURCE_UNITS})
        report = verify_standard(report)
    except Exception:
        report = dict(status="incomplete", error=traceback.format_exc(),
                      host_exe_sha256=sha256(sys.executable), probe_source_sha256=sha256(__file__))
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"]=="ok" and report.get("sdk_standard_verified") else 1


def launch(report_path, *, host=None, timeout=40):
    report_path = owned_build_path(report_path)
    if report_path.exists():
        raise FileExistsError("Native report is immutable; choose a new build directory")
    native_root = report_path.parent
    native_root.mkdir(parents=True, exist_ok=True)
    local = os.environ.get("LOCALAPPDATA", "")
    host = Path(host or Path(local)/"Programs/DGHub/DGLab-Console.exe").resolve()
    host_hash = sha256(host)
    if host_hash not in VERIFIED_HOSTS or not (host.parent/"_internal").is_dir():
        raise ValueError("Expected an inspected DGHub installation")
    isolated = native_root/"DGLab-Console.exe"
    library = native_root/"_internal"
    if isolated.exists() or library.exists():
        if (not isolated.is_file() or sha256(isolated)!=host_hash
                or not library.is_dir() or library.resolve()!=(host.parent/"_internal").resolve()):
            raise FileExistsError("Existing runner is not the verified owned fixture")
    else:
        shutil.copy2(host, isolated)
        quote = lambda value: "'"+str(value).replace("'", "''")+"'"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "New-Item -ItemType Junction -Path "+quote(library)+" -Target "+quote(host.parent/"_internal")+" | Out-Null"],
            check=True, timeout=8, capture_output=True)
    environment = dict(os.environ)
    environment[NATIVE_ROOT_ENV], environment[REPORT_ENV] = str(native_root), str(report_path)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run([str(isolated), "--plugin-runner", str(Path(__file__).resolve()), "--native-probe"],
            cwd=native_root, env=environment, timeout=timeout, check=False, capture_output=True)
    except subprocess.TimeoutExpired:
        report = dict(status="incomplete", hard_timeout=True, scope="Owned native runner exceeded hard timeout")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    if not report_path.is_file():
        raise RuntimeError("Native runner terminated without evidence")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["runner_exit_code"] = completed.returncode
    if completed.returncode != 0:
        report["status"] = "incomplete"
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def main():
    # DGHub's plugin runner replaces sys.argv with the script path. Its frozen
    # context and guarded environment, not forwarded CLI flags, select probe.
    if getattr(sys, "frozen", False):
        return run_native()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-probe", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT/"build/dghub_native_strategy_20261009/report.json")
    args = parser.parse_args()
    if args.native_probe:
        return run_native()
    report = launch(args.report)
    print(json.dumps(dict(status=report.get("status"), report=str(args.report),
                          runner_exit_code=report.get("runner_exit_code")), ensure_ascii=False))
    return 0 if report.get("status")=="ok" and report.get("sdk_standard_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
