"""Check documented rollback and actual native waveform alternatives safely.

Only a guarded DGHub EXE copy, isolated preset files and memory-only output.
No installed app, configuration, HTTP service, client or hardware is started.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace

EXPECTED_ROOT = Path("E:/HELLDIVER_DGLAB/build/dghub_host_probe_20261008").resolve()


def forbidden(*args, **kwargs):
    raise RuntimeError("Alternative probe forbids networking and children")


def adapter_fixture(remote):
    adapter = object.__new__(remote.RemoteAdapter)
    token = SimpleNamespace(target_id="fixture-target", route_id="fixture-route")
    writes = []
    adapter.plugin_name = "alternatives_fixture"
    adapter._baseline_pct = 0
    adapter._lease_until, adapter._lease_tokens = {}, {}
    adapter._lease_tasks = set()
    adapter._lease_task_targets, adapter._lease_task_routes = {}, {}
    adapter.capture_message_work = lambda target=None: token
    adapter._info_for_resolved_target = lambda target: {"max_strength_A": 100, "max_strength_B": 100}
    adapter.is_target_work_current = lambda candidate: candidate is token
    adapter.set_strength = lambda channel, value, **kwargs: writes.append(dict(channel=channel, value=value)) or True
    adapter._push_event = lambda **kwargs: None
    return adapter, writes


async def rollback_case(remote, protocol, resume):
    adapter, writes = adapter_fixture(remote)
    await adapter.handle_trigger(protocol.TriggerMsg(op="trigger", action="strength", delta_pct=50,
        strength_mode="rollback", duration_s=.08, channel="a"))
    if resume:
        adapter.set_strength("a", 0)
        await adapter.handle_trigger(protocol.TriggerMsg(op="trigger", action="strength", delta_pct=20,
            strength_mode="rollback", duration_s=.02, channel="a"))
    # Gather actual host lease tasks: the plugin sends nothing more (a frozen
    # producer model, not a suspended real process or physical TTL test).
    await asyncio.wait_for(asyncio.gather(*tuple(adapter._lease_tasks)), 1)
    return dict(writes=writes, final_value=writes[-1]["value"], remaining_tasks=len(adapter._lease_tasks),
        baseline=0, host_return_to_zero=writes[-1]["value"] == 0,
        real_frozen_process_tested=False, real_target_validity_tested=False)


async def main():
    if (not getattr(sys, "frozen", False) or sys.version_info[:2] != (3, 11)
            or Path(sys.executable).parent.resolve() != EXPECTED_ROOT):
        raise RuntimeError("Use only the isolated DGHub 1.9.6 runner")
    socket.socket.connect = socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    subprocess.Popen = forbidden
    fixture = Path(tempfile.mkdtemp(prefix="alternatives_fixture_", dir=EXPECTED_ROOT))
    os.chdir(fixture)
    from src.adapters import remote
    from src.adapters.sdk import protocol
    from src.device.preset_manager import PresetManager
    from pydantic import TypeAdapter
    manager = PresetManager(str(fixture))
    wanted = {"连击", "呼吸", "潮汐", "心跳节奏", "CS2-死亡"}
    waves = []
    for row in manager.get_all(device_family="coyote"):
        if row["name"] not in wanted:
            continue
        template = row["waveform"]
        first = remote.RemoteAdapter._build_frames(template, .25)
        second = remote.RemoteAdapter._build_frames(template, .25)
        full = remote.RemoteAdapter._build_frames(template, len(template) / 10)
        waves.append(dict(name=row["name"], template_frames=len(template), short_request_frames=len(first),
            two_short_requests_same_prefix=first == second == template[:len(first)],
            complete_request_preserves_template=full == template,
            bytes=first, source="actual native built-in Coyote waveform and _build_frames"))
    validation = TypeAdapter(protocol.ClientMsg)
    unsupported = []
    for packet in [dict(op="stop_output", channel="both"), dict(op="pulse_clear", channel="both"),
        dict(op="set_strength", channel="both", pct=10, ttl_s=.1),
        dict(op="trigger", action="waveform", preset="呼吸", channel="both", phase_offset=1),
        dict(op="pong", t=1.0)]:
        try:
            validation.validate_python(packet)
            accepted = True
        except Exception:
            accepted = False
        unsupported.append(dict(packet=packet, accepted=accepted))
    leases = dict(no_more_plugin_requests=await rollback_case(remote, protocol, False),
                  pause_then_new_request=await rollback_case(remote, protocol, True))
    report = dict(status="ok" if len(waves) == 5 and all(item["host_return_to_zero"] for item in leases.values())
                  and all(not row["accepted"] for row in unsupported) else "incomplete",
        scope="actual native methods with isolated presets and memory-only output; not full native runtime",
        host_exe_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        probe_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        rollback_zero_baseline=leases, native_waveforms=waves, unsupported_client_packets=unsupported,
        installed_host_modified=False, installed_config_modified=False,
        network_used=False, child_process_started=False, physical_device_used=False)
    (EXPECTED_ROOT / "sdk_alternatives_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception:
        import traceback
        if Path(sys.executable).parent.resolve() == EXPECTED_ROOT:
            (EXPECTED_ROOT / "sdk_alternatives_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
