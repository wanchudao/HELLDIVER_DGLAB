"""Final-EXE normal launch and replay against an owned software SDK peer.

Never launches/imports the installed DGHub or captures the screen. The input
is explicitly synthetic; native host playback/stop semantics remain separate.
"""
import argparse
import asyncio
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


async def run_case(exe, *, mode, scale, scenario, threshold=75, resolution=(1920,1080)):
    import cv2
    import numpy as np
    from websockets.asyncio.server import serve
    from dghub_plugin.sdk_config import DEFAULT_PRESETS, defaults, manifest, validate
    from src.hud_profiles import get_profile
    from tools.dghub_software_acceptance import (audit_startup_settling, audit_submissions, strength_request,
        synthetic_frame, software_catalog_response, wave_request, SOFTWARE_RANDOM_PRESET)

    token = uuid.uuid4().hex
    received, receipt_times = [], []
    state = dict(armed=False, death=False, lost=False, complete=False,
                 pause_stage="not_requested", paused_positive=False, disconnected=False,
                 catalog_requests=0, catalog_verified=False, capture_ready=False, output_ready=False)
    # Real browser dropdown selections are strings in persisted DGHub config.
    # Keep replay geometry integer-based, but test the actual wire format.
    values = defaults() | dict(channel_mode=mode, hud_scale_percent=str(scale), max_strength=35,
        max_strength_b=19, injury_pulse_strength_b=30, low_health_strength_b=3,
        heal_pulse_strength_b=7, healing_sustain_strength_b=2, death_pulse_strength_b=28)
    values["low_health_threshold_percent"] = str(threshold)
    values["allow_experimental_high_resolution"] = resolution != (1920,1080)
    death_mode = {"death_until_revive": "until_revive", "death_until_reset": "until_reset"}.get(scenario, "pulse")
    values["death_feedback_mode"] = death_mode
    if scenario == "wave_variants":
        values.update(low_health_preset=SOFTWARE_RANDOM_PRESET,
                      death_pulse_preset="software-long-cycle")
    elif scenario == "long_cycle":
        values.update(low_health_preset="software-long-cycle",
                      death_pulse_preset=SOFTWARE_RANDOM_PRESET)
    resume_tasks = []

    def local_control(running, source="button"):
        state["control_sequence"] = state.get("control_sequence", 0) + 1
        (root / "controls.json").write_text(json.dumps(dict(sequence=state["control_sequence"],
            running=running, source=source)), encoding="utf-8")

    async def resume(ws):
        await asyncio.sleep(.3)
        state["pause_stage"] = "resuming"
        local_control(True)

    async def handler(ws):
        if parse_qs(urlsplit(ws.request.path).query).get("token") != [token]:
            await ws.close(code=1008)
            return
        async for raw in ws:
            packet = json.loads(raw)
            if packet.get("op") == "hello":
                if packet.get("token") != token or packet.get("manifest") != manifest():
                    await ws.close(code=1008)
                    return
                received.append(packet | {"token": "<redacted>"})
                receipt_times.append(time.monotonic())
                for response in [dict(op="hello_ack", accepted=True, sdk_version="1.0"),
                    dict(op="config", data=values), dict(op="device_info", connected=scenario != "monitor_only",
                        device_type="v3", max_strength_a=200, max_strength_b=200),
                    dict(op="ping", t=123.5)]:
                    if response["op"] == "hello_ack":
                        state["hello_ack_at"] = time.monotonic()
                    await ws.send(json.dumps(response))
                continue
            received.append(packet)
            receipt_times.append(time.monotonic())
            request = strength_request(packet)
            if request is not None and request[1]:
                state["paused_positive"] |= state["pause_stage"] == "paused"
                if scenario == "disconnect" and not state["disconnected"]:
                    state["disconnected"] = True
                    await ws.close(code=1001, reason="software peer disconnected")
                    return
            if wave_request(packet) == values["death_pulse_preset"]:
                state["death"] = True
                if scenario == "pause" and state["pause_stage"] == "not_requested":
                    state["pause_stage"] = "requested"
                    local_control(False)
            if (packet["op"] == "status" and packet["fields"].get("running") is False
                    and state["pause_stage"] == "requested"):
                state["pause_stage"] = "paused"
                resume_tasks.append(asyncio.create_task(resume(ws)))
            if packet["op"] == "status":
                fields = packet["fields"]
                if scenario == "death_until_reset" and state.get("reset_requested") and fields.get("pause_reason") == "challenge_reset":
                    state["reset_paused"] = fields.get("running") is False and fields.get("death_marked") is False
                    await ws.send(json.dumps(dict(op="stop", reason="frozen_challenge_reset_verified")))
                    continue
                state["catalog_verified"] |= fields.get("preset_catalog", {}).get("state") == "verified_startup_snapshot"
                state["capture_ready"] |= fields.get("capture_state") == "ok"
                state["output_ready"] |= fields.get("output_ready") is True
                if fields.get("output_ready") is True and state.get("first_output_ready_at") is None:
                    state["first_output_ready_at"] = time.monotonic()
                if state["capture_ready"] and state["output_ready"] and not state["armed"]:
                    state["armed"] = True
                    local_control(True)
                if scenario == "monitor_only":
                    hp = fields.get("hp")
                    state["seen_low_hp"] = (state.get("seen_low_hp", False)
                        or (type(hp) in (int, float) and hp < .75))
                    # No pulse is emitted in monitoring mode. The stable HP
                    # uses EMA and need not reach exact zero in ten frames;
                    # this test is HUD loss/rebaseline, not death acceptance.
                    state["lost"] |= state["seen_low_hp"] and fields.get("display_status") == "NO HUD"
                    state["recognition_only_seen"] = (state.get("recognition_only_seen", False)
                        or fields.get("recognition_only") is True)
                state["lost"] |= state["death"] and fields.get("display_status") == "NO HUD"
                if state["lost"] and fields.get("hp") == 1 and not state["complete"]:
                    state["complete"] = True
                    if scenario == "death_until_reset":
                        state["reset_requested"] = True
                        local_control(False, "hotkey")
                    else:
                        await ws.send(json.dumps(dict(op="stop", reason="frozen_software_acceptance")))

    with tempfile.TemporaryDirectory(prefix="dghub-frozen-software-") as directory:
        root = Path(directory)
        (root / "manifest.json").write_text(json.dumps(manifest(), ensure_ascii=False), encoding="utf-8")
        frames = []
        sequence = [(1, 10), (.6, 20), (.9, 15), (.95, 55), (0, 10), (None, 4), (1, 8)]
        if death_mode != "pulse":
            sequence = [(1, 10), (.6, 20), (.9, 15), (.95, 55), (0, 165), (.6, 35), (None, 4), (1, 8)]
        if resolution != (1920,1080):
            # Repeated 4K PNG decoding is deliberately part of this replay, not
            # a game capture benchmark. Keep the second healing rise inside
            # the unchanged two-second healing episode. Fifteen decoded 4K
            # frames can exceed that interval and instead start a *new*
            # confirmation, correctly masking the soothing waveform again.
            sequence[2:4] = [(.9, 5), (.95, 65)]
        if scenario == "threshold":
            # Let the higher-priority 2s injury pulse finish, so the low-health
            # waveform itself becomes observable without changing priorities.
            sequence.insert(2, (.2, 55))
        for index, (hp, count) in enumerate(sequence):
            name = f"frame_{index}.png"
            if hp is None:
                frame = np.zeros((resolution[1], resolution[0], 3), np.uint8)
            elif resolution == (1920,1080):
                frame = synthetic_frame(scale, hp)
            else:
                from tools.resolution_fixtures import synthetic_frame as reference_frame
                frame = reference_frame(scale, resolution, hp)
            if not cv2.imwrite(str(root / name), frame):
                raise RuntimeError("Synthetic fixture creation failed")
            frames.extend([name] * count)
        (root / "replay.json").write_text(json.dumps(dict(synthetic=True, frames=frames)), encoding="utf-8")
        async with serve(handler, "127.0.0.1", 0, max_size=65536, close_timeout=.5,
                         process_request=lambda connection, request: software_catalog_response(request, state)) as server:
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("DGHUB_") and key not in {"PYTHONHOME", "PYTHONPATH"}}
            environment.update(PATH="", DGHUB_HOST="127.0.0.1", DGHUB_PORT=str(server.sockets[0].getsockname()[1]),
                DGHUB_TOKEN=token, DGHUB_PLUGIN_ID=manifest()["id"], DGHUB_PLUGIN_ROOT=str(root))
            process_started_at = time.monotonic()
            process = await asyncio.create_subprocess_exec(str(exe), "--software-replay-test", str(root),
                cwd=root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            timed_out = False
            try:
                # Over 300 high-resolution PNG decodes may exceed 45 seconds
                # while *individual* frames still meet the unchanged runtime
                # freshness deadline. Bound the complete replay separately.
                timeout = 90 if resolution != (1920,1080) else 25 if death_mode != "pulse" else 15
                code = await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                timed_out = True
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=2)
                code = 124
            finally:
                for task in resume_tasks:
                    if not task.done():
                        task.cancel()
                if resume_tasks:
                    await asyncio.gather(*resume_tasks, return_exceptions=True)
            process_elapsed_seconds = round(time.monotonic()-process_started_at,3)
        snapshots = list((root / "sessions").glob("*/config_snapshot.json"))
        snapshot = json.loads(snapshots[0].read_text(encoding="utf-8")) if len(snapshots) == 1 else {}
        metadata = json.loads(snapshots[0].with_name("session.json").read_text(encoding="utf-8")) if snapshot else {}
        rows = [json.loads(line) for line in snapshots[0].with_name("trigger_log.jsonl").read_text(encoding="utf-8").splitlines()] if snapshot else []

    audit = audit_submissions(received, mode=mode, cap_a=35, cap_b=19, receipt_times=receipt_times)
    settling = audit_startup_settling(state)
    waves = {wave_request(row) for row in received if wave_request(row) is not None}
    expected_waves = {value for key, value in values.items() if key.endswith("_preset")}
    if threshold == 0:
        expected_waves.discard(values["low_health_preset"])
    record_ok = (metadata.get("state") == "complete" and metadata.get("frozen") is True
        and metadata.get("capture_source") == "synthetic_replay"
        and metadata.get("rows_dropped") == metadata.get("rows_invalid") == 0
        and metadata.get("rows_written") == len(rows)
        and snapshot.get("settings") == validate(values)
        and snapshot.get("recognition_config", {}).get("ROI") == get_profile(scale,resolution)["roi"]
        and snapshot.get("recognition_config", {}).get("hud") == get_profile(scale,resolution)["hud"])
    pause_ok = scenario != "pause" or (state["pause_stage"] == "resuming" and not state["paused_positive"])
    if scenario == "monitor_only":
        ok = (code == 0 and state["complete"] and record_ok and state.get("recognition_only_seen")
              and audit["positive_requests"] == 0 and not waves and audit["final_both_zero"])
    elif scenario == "disconnect":
        ok = code == 1 and state["disconnected"] and record_ok and audit["positive_requests"] > 0
    else:
        ok = (code == 0 and state["complete"] and pause_ok and waves == expected_waves and record_ok
              and all(audit[key] for key in ("enabled_channels_correct", "independent_caps_correct",
                  "rollback_leases_correct", "final_both_zero", "wave_requests_correct", "json_pong_absent",
                  "wave_cadence_correct", "lease_renewal_observed"))
              and audit["full_cycle_requests"] > 0)
        if scenario == "wave_variants":
            ok &= audit["random_wave_windows"] > 0 and audit["finite_wave_windows"] > 0
        elif scenario == "long_cycle":
            ok &= audit["random_wave_windows"] > 0 and audit["long_full_cycle_requests"] > 0
    ok &= state["catalog_requests"] == 1 and state["catalog_verified"] and settling["startup_settling_verified"]
    threshold_observations = [r for r in rows if "frame" in r and .59 < r.get("raw_hp", -1) < .62]
    healing_transitions = []
    previous_raw = None
    for row in rows:
        current_raw = row.get("raw_hp")
        if type(current_raw) in (int,float):
            if previous_raw is not None and current_raw - previous_raw > .01:
                healing_transitions.append({key:row.get(key) for key in
                    ("frame","ts","raw_hp","action","healing_sustain")})
            previous_raw = current_raw
    threshold_ok = (snapshot.get("recognition_config", {}).get("trigger", {}).get("low_health_hp_threshold") == threshold / 100)
    if scenario == "threshold":
        threshold_ok &= bool(threshold_observations) and all(r["low_health"] is (.6 < threshold / 100) for r in threshold_observations)
    ok &= threshold_ok
    death_chain = True
    if death_mode != "pulse":
        confirmed = [r for r in rows if "frame" in r and r.get("reason") not in ("invalid_frame", "baseline")]
        zero_rows = [r for r in confirmed if r.get("raw_hp") == 0]
        alive_rows = [r for r in confirmed if .59 < r.get("raw_hp", -1) < .62
                      and any(prior.get("raw_hp") == 0 for prior in rows[:rows.index(r)])]
        death_chain = (len(zero_rows) >= 140 and all(r.get("death_marked") for r in zero_rows)
            and alive_rows and all(r.get("death_marked") is (death_mode == "until_reset") for r in alive_rows)
            and snapshot.get("recognition_config", {}).get("trigger", {}).get("death_feedback_mode") == death_mode)
        if death_mode == "until_reset":
            death_chain = bool(death_chain and state.get("reset_paused"))
        ok &= bool(death_chain)
    return dict(status="ok" if ok else "incomplete", mode=mode, hud_scale_percent=scale, scenario=scenario,
        resolution=list(resolution), input_source="synthetic_original_pixels_not_gameplay",
        exit_code=code, hard_timeout=timed_out,
        process_elapsed_seconds=process_elapsed_seconds, process_hard_timeout_seconds=timeout,
        five_presets_observed=sorted(waves),
        enabled_channels_correct=audit["enabled_channels_correct"],
        independent_caps_correct=audit["independent_caps_correct"], submission_audit=audit,
        final_both_zero=audit["final_both_zero"], pause_no_positive_after_ack=pause_ok,
        session_record_verified=record_ok, **settling,
        low_health_threshold_percent=threshold, threshold_chain_verified=threshold_ok,
        death_feedback_mode=death_mode, death_chain_verified=bool(death_chain),
        death_chain_observations=({"confirmed_zero_frames": len(zero_rows),
            "marked_zero_frames": sum(bool(r.get("death_marked")) for r in zero_rows),
            "alive_after_death_frames": len(alive_rows),
            "alive_marked_frames": sum(bool(r.get("death_marked")) for r in alive_rows)} if death_mode != "pulse" else {}),
        healing_transitions=healing_transitions,
        state=state, session_metadata=metadata, applied_snapshot=snapshot, messages=received,
        local_control_fixture=True, host_start_commands_used=False,
        native_host_tested=False, real_game_tested=False, physical_device_used=False)


async def run_catalog_gate_case(exe, scenario, *, normal_entry=False):
    """Negative final-EXE startup gates; no desktop/HUD/device is involved."""
    from websockets.asyncio.server import serve
    from websockets.datastructures import Headers
    from websockets.http11 import Response
    from dghub_plugin.preset_catalog import CATALOG_PATH
    from dghub_plugin.sdk_config import defaults, manifest
    from tools.dghub_software_acceptance import software_catalog_response
    received, state = [], dict(catalog_requests=0)
    token = uuid.uuid4().hex

    def request(connection, request):
        if request.path == CATALOG_PATH and scenario == "catalog_unavailable":
            state["catalog_requests"] += 1
            return Response(503, "Unavailable", Headers({"Content-Type": "application/json",
                "Content-Length": "2", "Connection": "close"}), b"{}")
        return software_catalog_response(request, state)

    async def handler(ws):
        assert parse_qs(urlsplit(ws.request.path).query).get("token") == [token]
        async for raw in ws:
            packet = json.loads(raw)
            received.append(packet | {"token": "<redacted>"} if packet["op"] == "hello" else packet)
            if packet["op"] == "hello":
                await ws.send(json.dumps(dict(op="hello_ack", accepted=True, sdk_version="1.0")))
                await ws.send(json.dumps(dict(op="config", data=defaults() | {
                    "injury_pulse_preset": "missing-software-fixture-preset"})))

    with tempfile.TemporaryDirectory(prefix="dghub-frozen-catalog-") as directory:
        root = Path(directory)
        (root / "manifest.json").write_text(json.dumps(manifest(), ensure_ascii=False), encoding="utf-8")
        # The gate must reject before capture is created, so no replay frames.
        async with serve(handler, "127.0.0.1", 0, max_size=65536, close_timeout=.5,
                         process_request=request) as server:
            environment = {key: value for key, value in os.environ.items()
                if not key.startswith("DGHUB_") and key not in {"PYTHONHOME", "PYTHONPATH"}}
            environment.update(PATH="", DGHUB_HOST="127.0.0.1", DGHUB_PORT=str(server.sockets[0].getsockname()[1]),
                DGHUB_TOKEN=token, DGHUB_PLUGIN_ID=manifest()["id"], DGHUB_PLUGIN_ROOT=str(root))
            arguments = [] if normal_entry else ["--software-replay-test", str(root)]
            local = root / "isolated_appdata"
            local.mkdir()
            environment["LOCALAPPDATA"] = str(local)
            process = await asyncio.create_subprocess_exec(str(exe), *arguments,
                cwd=root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            timed_out = False
            try:
                code = await asyncio.wait_for(process.wait(), 8)
            except TimeoutError:
                timed_out = True
                process.kill()
                await asyncio.wait_for(process.wait(), 2)
                code = 124
        files = list(root.rglob("session.json"))
        metadata = json.loads(files[0].read_text(encoding="utf-8")) if len(files) == 1 else {}
        no_snapshot = not list(root.rglob("config_snapshot.json"))
    no_output = not any(row["op"] in {"trigger", "pulse"} or row.get("pct", 0) for row in received)
    expected_reason = "catalog_failed" if scenario == "catalog_unavailable" else "session_stopped"
    hello_count = sum(row["op"] == "hello" for row in received)
    ok = (code == 1 and not timed_out and state["catalog_requests"] == 1 and no_snapshot and no_output
        and hello_count == (0 if scenario == "catalog_unavailable" else 1)
        and metadata.get("reason") == expected_reason and metadata.get("runtime_error") is True)
    return dict(status="ok" if ok else "incomplete", scenario=scenario,
        entry="normal_host_entry" if normal_entry else "synthetic_replay_entry", exit_code=code,
        hard_timeout=timed_out, state=state, no_output=no_output, no_configuration_snapshot=no_snapshot,
        hello_count=hello_count, metadata=metadata, native_host_tested=False, physical_device_used=False)


async def run_normal_entry_case(exe):
    """Use the real launcher entry, stop before config so no capture/UI begins."""
    from websockets.asyncio.server import serve
    from dghub_plugin.sdk_config import manifest
    from tools.dghub_software_acceptance import software_catalog_response
    token, received = uuid.uuid4().hex, []
    state = dict(catalog_requests=0)

    async def handler(ws):
        assert parse_qs(urlsplit(ws.request.path).query).get("token") == [token]
        async for raw in ws:
            packet = json.loads(raw)
            if packet.get("op") == "hello":
                assert packet.get("token") == token and packet.get("manifest") == manifest()
                received.append(packet | {"token": "<redacted>"})
                await ws.send(json.dumps(dict(op="hello_ack", accepted=True, sdk_version="1.0")))
                await ws.send(json.dumps(dict(op="stop", reason="software_normal_entry_stop")))
            else:
                received.append(packet)

    with tempfile.TemporaryDirectory(prefix="dghub-normal-entry-") as directory:
        root = Path(directory)
        local = root / "isolated_appdata"
        local.mkdir()
        (root / "manifest.json").write_text(json.dumps(manifest(), ensure_ascii=False), encoding="utf-8")
        async with serve(handler, "127.0.0.1", 0, max_size=65536, close_timeout=.5,
                         process_request=lambda _connection, request: software_catalog_response(request, state)) as server:
            environment = {key: value for key, value in os.environ.items()
                if not key.startswith("DGHUB_") and key not in {"PYTHONHOME", "PYTHONPATH"}}
            environment.update(PATH="", LOCALAPPDATA=str(local), DGHUB_HOST="127.0.0.1",
                DGHUB_PORT=str(server.sockets[0].getsockname()[1]), DGHUB_TOKEN=token,
                DGHUB_PLUGIN_ID=manifest()["id"], DGHUB_PLUGIN_ROOT=str(root))
            process = await asyncio.create_subprocess_exec(str(exe), cwd=root, env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            timed_out = False
            try:
                code = await asyncio.wait_for(process.wait(), 8)
            except TimeoutError:
                timed_out = True
                process.kill()
                await asyncio.wait_for(process.wait(), 2)
                code = 124
        files = list(root.rglob("session.json"))
        metadata = json.loads(files[0].read_text(encoding="utf-8")) if len(files) == 1 else {}
        no_snapshot = not list(root.rglob("config_snapshot.json"))
    no_output = not any(packet.get("op") in {"trigger", "pulse"} or packet.get("pct", 0)
                        for packet in received)
    zeros = {packet.get("channel") for packet in received
             if packet.get("op") == "set_strength" and packet.get("pct") == 0}
    no_capture_report = not any("capture_state" in packet.get("fields", {}) for packet in received)
    ok = (code == 0 and not timed_out and state["catalog_requests"] == 1
        and sum(packet.get("op") == "hello" for packet in received) == 1
        and no_output and zeros == {"a", "b"} and no_snapshot and no_capture_report
        and metadata.get("state") == "complete" and metadata.get("runtime_error") is False)
    return dict(status="ok" if ok else "incomplete", entry="normal_host_entry", exit_code=code,
        hard_timeout=timed_out, handshake_completed=code == 0 and not timed_out,
        final_both_zero=zeros == {"a", "b"},
        no_configuration_or_capture=no_snapshot and no_capture_report, no_positive_output=no_output,
        isolated_appdata=True, metadata=metadata, messages=received,
        native_host_tested=False, real_game_tested=False, physical_device_used=False)


async def run(bundle, *, resolution_cases=False):
    exe = (Path(bundle).resolve() / "helldiver_dglab.exe")
    if not exe.is_file():
        raise ValueError("Internal candidate EXE not found")
    cases = []
    for mode, scale, scenario in [("a", 75, "normal"), ("b", 125, "normal"),
                                   ("both", 100, "pause"), ("both", 100, "disconnect"),
                                   ("both", 100, "wave_variants"), ("both", 100, "long_cycle"),
                                   ("both", 100, "monitor_only")]:
        case = await run_case(exe, mode=mode, scale=scale, scenario=scenario)
        cases.append(case)
        print(json.dumps({key: case[key] for key in ("status", "mode", "hud_scale_percent", "scenario", "exit_code")}), flush=True)
    for scenario in ("death_until_revive", "death_until_reset"):
        case = await run_case(exe, mode="both", scale=100, scenario=scenario)
        cases.append(case)
        print(json.dumps({key: case[key] for key in ("status", "scenario", "death_chain_verified", "exit_code")}), flush=True)
    gates = []
    for threshold in (0, 30, 75):
        case = await run_case(exe, mode="both", scale=100, scenario="threshold", threshold=threshold)
        cases.append(case)
        print(json.dumps({key: case[key] for key in ("status", "scenario", "low_health_threshold_percent", "exit_code")}), flush=True)
    if resolution_cases:
        for resolution in ((2560,1440),(3840,2160)):
            for mode, scale, scenario in (("a",75,"normal"),("b",125,"disconnect"),
                    ("both",110,"pause"),("both",100,"death_until_revive"),("both",100,"death_until_reset")):
                case = await run_case(exe, mode=mode, scale=scale, scenario=scenario,resolution=resolution)
                cases.append(case)
                print(json.dumps({key:case[key] for key in ("status","resolution","mode","scenario","exit_code")}),flush=True)
    for scenario in ("catalog_unavailable", "unknown_saved_preset"):
        gate = await run_catalog_gate_case(exe, scenario)
        gates.append(gate)
        print(json.dumps({key: gate[key] for key in ("status", "scenario", "exit_code")}), flush=True)
    normal_entry = await run_normal_entry_case(exe)
    gates.append(await run_catalog_gate_case(exe, "catalog_unavailable", normal_entry=True))
    return dict(status="ok" if all(case["status"] == "ok" for case in cases + gates + [normal_entry]) else "incomplete",
        scope="final internal EXE; synthetic original-pixel frames and real software loopback peer",
        exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(), path_empty=True,
        python_environment_removed=True, clean_windows_tested=False, cases=cases,
        normal_entry_case=normal_entry, catalog_startup_gates=gates,
        native_host_tested=False, real_game_tested=False, physical_device_used=False)


async def retry_incomplete(bundle, previous):
    """Preserve passed evidence from the identical EXE; retry only failed cases.

    The resulting report carries the earlier report hash and original failure
    summaries. This is explicitly a combined acceptance record, not a claim
    that all cases passed in one uninterrupted run.
    """
    exe = Path(bundle).resolve() / "helldiver_dglab.exe"
    earlier = json.loads(previous.read_text(encoding="utf-8"))
    if (earlier.get("exe_sha256") != hashlib.sha256(exe.read_bytes()).hexdigest()
            or earlier.get("normal_entry_case", {}).get("status") != "ok"
            or not earlier.get("catalog_startup_gates")
            or any(gate.get("status") != "ok" for gate in earlier["catalog_startup_gates"])):
        raise ValueError("Retry requires the identical EXE and completed startup gates")
    failures, reused = [], 0
    for index, case in enumerate(earlier["cases"]):
        if case.get("status") == "ok":
            reused += 1
            continue
        if (case.get("resolution") not in ([2560,1440],[3840,2160])
                or case.get("scenario") not in {"normal","disconnect","pause","death_until_revive","death_until_reset"}):
            raise ValueError("Retry is limited to the known high-resolution acceptance cases")
        failures.append({key:case.get(key) for key in
                         ("status","mode","scenario","resolution","hard_timeout","exit_code")})
        replacement = await run_case(exe, mode=case["mode"], scale=case["hud_scale_percent"],
            scenario=case["scenario"], threshold=case["low_health_threshold_percent"],
            resolution=tuple(case["resolution"]))
        earlier["cases"][index] = replacement
        print(json.dumps({key:replacement[key] for key in
              ("status","mode","scenario","resolution","exit_code")}),flush=True)
    if not failures:
        raise ValueError("Previous report has no incomplete cases")
    earlier["retry_evidence"] = dict(previous_report=previous.name,
        previous_sha256=hashlib.sha256(previous.read_bytes()).hexdigest(),
        original_incomplete_cases=failures, reused_passed_cases_same_exe=reused,
        newly_executed_cases=len(failures), combined_record_not_single_run=True,
        high_resolution_process_hard_timeout_seconds=90,
        production_frame_timeout_modified=False)
    earlier["status"] = "ok" if all(case["status"] == "ok" for case in earlier["cases"]) else "incomplete"
    return earlier


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--resolution-cases", action="store_true")
    parser.add_argument("--retry-incomplete-from", type=Path)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(retry_incomplete(args.bundle,args.retry_incomplete_from)
            if args.retry_incomplete_from else run(args.bundle,resolution_cases=args.resolution_cases))
    except Exception:
        report = dict(status="incomplete", scope="frozen software protocol; runner failed")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report), encoding="utf-8")
        raise
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
