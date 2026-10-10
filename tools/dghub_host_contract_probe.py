"""Run with DGHub --plugin-runner, using real SDK code and fake output only.

The caller must use an isolated copy of the host EXE. No ConfigManager or
device controller is constructed; all output is recorded in memory.
"""
import argparse
import asyncio
import inspect
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace


def forbidden(*args, **kwargs):
    raise RuntimeError("Host contract probe forbids network and child processes")


async def lease_case(remote, protocol, baseline):
    adapter = object.__new__(remote.RemoteAdapter)
    token = SimpleNamespace(target_id="fixture-target", route_id="fixture-route")
    writes = []
    adapter.plugin_name = "contract_fixture"
    adapter._baseline_pct = baseline
    adapter._lease_until = {}
    adapter._lease_tokens = {}
    adapter._lease_tasks = set()
    adapter._lease_task_targets = {}
    adapter._lease_task_routes = {}
    adapter.capture_message_work = lambda target=None: token
    adapter._info_for_resolved_target = lambda target: {"max_strength_A": 100, "max_strength_B": 100}
    adapter.is_target_work_current = lambda candidate: candidate is token
    adapter.set_strength = lambda channel, value, **kwargs: writes.append((channel, value)) or True
    adapter._push_event = lambda **kwargs: None
    await adapter.handle_trigger(protocol.TriggerMsg(op="trigger", action="strength", delta_pct=50,
                                 strength_mode="rollback", duration_s=.05, channel="a"))
    active_tasks = len(adapter._lease_tasks)
    adapter.set_strength("a", 0)
    await asyncio.sleep(.16)
    return {"baseline_pct": baseline, "active_lease_after_trigger": active_tasks,
            "writes": writes, "final_strength": writes[-1][1],
            "pause_cancels_lease": active_tasks == 0}


async def unregister_case(remote, protocol):
    from src.adapters.manager import AdapterManager
    adapter = object.__new__(remote.RemoteAdapter)
    token = SimpleNamespace(target_id="fixture-target", route_id="fixture-route")
    writes, removals, config_writes = [], [], []
    adapter.plugin_name = "contract_fixture"
    adapter._baseline_pct = 100
    adapter._lease_until, adapter._lease_tokens = {}, {}
    adapter._lease_tasks = set()
    adapter._lease_task_targets, adapter._lease_task_routes = {}, {}
    adapter.capture_message_work = lambda target=None: token
    adapter._info_for_resolved_target = lambda target: {"max_strength_A": 100, "max_strength_B": 100}
    adapter.is_target_work_current = lambda candidate: candidate is token
    adapter.set_strength = lambda channel, value, **kw: writes.append((channel, value)) or True
    adapter._push_event = lambda **kw: None
    manager = object.__new__(AdapterManager)
    manager.target_update_lock, manager._lock = asyncio.Lock(), asyncio.Lock()
    manager._active = {adapter.plugin_name: adapter}
    manager._remote_registry = {adapter.plugin_name: adapter}
    manager.strength_layers = SimpleNamespace(remove=lambda name: removals.append(name))
    manager.config = SimpleNamespace(set_plugin=lambda *args: config_writes.append(args))
    await adapter.handle_trigger(protocol.TriggerMsg(op="trigger", action="strength", delta_pct=50,
                                 duration_s=.05, channel="a"))
    await manager.unregister_remote(adapter.plugin_name)
    active_after_unregister = len(adapter._lease_tasks)
    await asyncio.sleep(.16)
    return dict(active_lease_after_unregister=active_after_unregister, layer_removals=removals,
                fake_config_writes=config_writes, writes=writes,
                real_target_validity_tested=False,
                scope="real unregister/lease methods, fake current-target token and memory layer")


async def pulse_zero_case():
    from src.device import controller
    from src.device.commands import DeviceCommand
    device = object.__new__(controller.DGLabV3Controller)
    device.is_connected = True
    device._pulse_tasks, device._pulse_owners = {}, {}
    writes = []
    first_batch = asyncio.Event()
    release_delay = asyncio.Event()
    original_sleep = asyncio.sleep

    async def fake_delay(seconds):
        if seconds >= 1:
            first_batch.set()
            await release_delay.wait()
        else:
            await original_sleep(0)

    async def add_pulses(channel, *frames):
        writes.append(dict(op="pulse", count=len(frames), first=frames[0]))

    async def set_strength(channel, operation, value):
        writes.append(dict(op="strength", value=value))

    async def clear_pulses(channel):
        writes.append(dict(op="clear"))

    device.client = SimpleNamespace(add_pulses=add_pulses, set_strength=set_strength, clear_pulses=clear_pulses)
    asyncio.sleep = fake_delay
    try:
        chunk = controller.PULSE_DATA_MAX_LENGTH
        # Frame sentinels are trace identifiers accepted by the fake client,
        # not real waveform/protocol format validation.
        await device._execute_command(DeviceCommand(type="pulse", channel="a",
                      waveform=list(range(chunk + 1)), source_id="contract_fixture"))
        await asyncio.wait_for(first_batch.wait(), 1)
        await device._execute_command(DeviceCommand(type="strength_set", channel="a", value=0))
        pending = len([task for task in device._pulse_tasks.values() if not task.done()])
        release_delay.set()
        await asyncio.gather(*list(device._pulse_tasks.values()))
        return dict(pending_pulse_tasks_after_zero=pending, writes=writes,
                    pulse_clear_called=any(row["op"] == "clear" for row in writes),
                    real_frame_format_tested=False, delay_is_controlled=True,
                    scope="real V3 execute/send-batches methods with fake device client")
    finally:
        asyncio.sleep = original_sleep
        for task in list(device._pulse_tasks.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*list(device._pulse_tasks.values()), return_exceptions=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path,
                        default=Path(sys.executable).parent / "contract_report.json")
    args = parser.parse_args()
    if not getattr(sys, "frozen", False) or sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use the isolated DGHub 1.9.6 plugin runner")
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    subprocess.Popen = forbidden
    from src.adapters.sdk import protocol
    from src.adapters import remote
    from pydantic import TypeAdapter
    schema = TypeAdapter(protocol.ClientMsg).json_schema()
    models = {name: schema["$defs"][name].get("properties", {})
              for name in schema.get("$defs", {}) if name.endswith("Msg")}
    result = {"status": "ok", "host_runtime_python": sys.version.split()[0],
              "remote_module": remote.__file__, "client_message_fields": {
                  name: list(fields) for name, fields in models.items()},
              "device_info_schema": protocol.DeviceInfoMsg.model_json_schema(),
              "manifest_schema": protocol.PluginManifest.model_json_schema(),
              "remote_constructor": str(inspect.signature(remote.RemoteAdapter)),
              "lease_cases": [await lease_case(remote, protocol, baseline) for baseline in (0, 100)],
              "unregister_case": await unregister_case(remote, protocol),
              "pulse_zero_case": await pulse_zero_case(),
              "short_waveform_renewal": {
                  "first": remote.RemoteAdapter._build_frames(list(range(25)), .25),
                  "second": remote.RemoteAdapter._build_frames(list(range(25)), .25),
                  "scope": "real _build_frames using distinguishable in-memory frame sentinels"},
              "real_device_used": False, "network_used": False, "host_config_modified": False}
    validation_path = Path(sys.executable).parent.parent / "dghub_software_runtime_20261008.json"
    if validation_path.is_file():
        validation = []
        for row in json.loads(validation_path.read_text(encoding="utf-8"))["messages"]:
            try:
                TypeAdapter(protocol.ClientMsg).validate_python(row)
                validation.append({"op": row.get("op"), "valid": True})
            except Exception:
                validation.append({"op": row.get("op"), "valid": False})
        result["sdk_message_validation"] = validation
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if sys.stdout is not None:
        print("DGHub SDK contract probe report:", args.report)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        import traceback
        (Path(sys.executable).parent / "contract_error.txt").write_text(
            traceback.format_exc(), encoding="utf-8")
        raise
