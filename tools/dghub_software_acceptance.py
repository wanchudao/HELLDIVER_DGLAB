"""Real loopback SDK submission checks with synthetic capture, never the host.

It validates the candidate runtime wiring, not native DGHub stop semantics,
physical delivery, real screen capture or a releasable frozen bundle.
"""
import argparse
import asyncio
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOFTWARE_PRESET_FRAMES = {
    "连击": 8, "呼吸": 11, "潮汐": 11, "心跳节奏": 16, "CS2-死亡": 6,
    "software-long-cycle": 100,
}
SOFTWARE_RANDOM_PRESET = "software-random-cycle"


def audit_startup_settling(state):
    acknowledged, ready = state.get("hello_ack_at"), state.get("first_output_ready_at")
    elapsed = ready - acknowledged if acknowledged is not None and ready is not None else None
    return dict(startup_settling_verified=elapsed is not None and elapsed >= .49,
                startup_settling_delay_s=elapsed,
                startup_settling_evidence="host hello_ack submission to first output_ready=True receipt; monotonic clock")


def strength_request(packet):
    """Read submitted strength, not a device measurement or native-host ACK."""
    if packet.get("op") == "set_strength":
        return packet.get("channel"), packet.get("pct")
    if packet.get("op") == "trigger" and packet.get("action") in {"strength", "both"}:
        return packet.get("channel"), packet.get("delta_pct")
    return None


def wave_request(packet):
    if packet.get("op") == "pulse" or (packet.get("op") == "trigger"
            and packet.get("action") in {"waveform", "both"}):
        return packet.get("preset")
    return None


def audit_submissions(packets, *, mode="both", cap_a=100, cap_b=100, receipt_times=None):
    """Strictly check the revised SDK policy without pretending to emulate it.

    Native leases, event cards, output routing and device buffers are validated
    separately. A successful WebSocket submission is not a playback ACK.
    """
    strengths = [(index, packet, strength_request(packet)) for index, packet in enumerate(packets)
                 if strength_request(packet) is not None]
    positives = [(index, packet, request) for index, packet, request in strengths
                 if type(request[1]) is int and request[1] > 0]
    expected = {"a", "b"} if mode == "both" else {mode}
    channels_ok = {request[0] for _index, _packet, request in positives} == expected
    strength_ok = all(channel in {"a", "b"} and type(pct) is int
                      and 0 <= pct <= (cap_a if channel == "a" else cap_b)
                      for _index, _packet, (channel, pct) in strengths)
    leases_ok = bool(positives) and all(packet.get("op") == "trigger"
        and packet.get("action") == "strength" and packet.get("strength_mode") == "rollback"
        and type(packet.get("duration_s")) in (int, float) and packet["duration_s"] == .30
        and "preset" not in packet for _index, packet, _request in positives)
    last_positive = positives[-1][0] if positives else -1
    zeros = {request[0] for index, _packet, request in strengths
             if index > last_positive and request[1] == 0}
    full_cycles, long_full_cycles, finite_windows, random_windows, invalid_waves = 0, 0, 0, 0, 0
    for packet in packets:
        preset = wave_request(packet)
        if preset is None:
            continue
        if packet.get("channel") not in {"a", "b", "both"}:
            invalid_waves += 1
        elif packet.get("op") == "pulse":
            if preset not in SOFTWARE_PRESET_FRAMES:
                invalid_waves += 1
            else:
                full_cycles += 1
                long_full_cycles += SOFTWARE_PRESET_FRAMES[preset] > 86
        else:
            duration = packet.get("duration_s")
            maximum = 1 if preset == SOFTWARE_RANDOM_PRESET else SOFTWARE_PRESET_FRAMES.get(preset, 0) / 10
            valid_window = (type(duration) in (int, float) and math.isfinite(duration)
                            and 0 < duration <= maximum)
            if preset != SOFTWARE_RANDOM_PRESET and valid_window:
                valid_window = duration < maximum
            if not valid_window or packet.get("action") != "waveform":
                invalid_waves += 1
            elif preset == SOFTWARE_RANDOM_PRESET:
                random_windows += 1
            else:
                finite_windows += 1
    cadence_ok, renewal_observed, maximum_renewal_gap = True, False, 0.0
    if receipt_times is not None:
        if len(receipt_times) != len(packets):
            raise ValueError("Packet receipt times must cover the complete submission stream")
        active, pending_join, wave_deadline, previous_strength = set(), set(), {}, {}
        for stamp, packet in zip(receipt_times, packets, strict=True):
            request = strength_request(packet)
            if request is not None:
                channel, pct = request
                if type(pct) is int and pct > 0:
                    if channel not in active:
                        pending_join.add(channel)
                    else:
                        last_stamp, last_pct = previous_strength[channel]
                        maximum_renewal_gap = max(maximum_renewal_gap, stamp - last_stamp)
                        renewal_observed |= last_pct == pct and .075 <= stamp - last_stamp <= .16
                    active.add(channel)
                    previous_strength[channel] = stamp, pct
                else:
                    active.discard(channel)
                    pending_join.discard(channel)
                    wave_deadline.pop(channel, None)
                    previous_strength.pop(channel, None)
            preset = wave_request(packet)
            if preset is None:
                continue
            channels = {"a", "b"} if packet.get("channel") == "both" else {packet.get("channel")}
            joining = bool(channels & pending_join)
            duration = SOFTWARE_PRESET_FRAMES.get(preset, 0) / 10 if packet.get("op") == "pulse" else packet.get("duration_s", 0)
            for channel in channels:
                prior = wave_deadline.get(channel)
                if prior and prior[0] == preset and stamp < prior[1] - .05 and not joining:
                    cadence_ok = False
                wave_deadline[channel] = preset, stamp + duration
            pending_join.difference_update(channels)
    return dict(enabled_channels_correct=channels_ok, independent_caps_correct=strength_ok,
        rollback_leases_correct=leases_ok, final_both_zero=zeros == {"a", "b"},
        full_cycle_requests=full_cycles, long_full_cycle_requests=long_full_cycles,
        finite_wave_windows=finite_windows,
        random_wave_windows=random_windows, wave_requests_correct=invalid_waves == 0,
        wave_cadence_correct=cadence_ok, receipt_timing_tested=receipt_times is not None,
        lease_renewal_observed=renewal_observed, maximum_received_strength_gap_s=maximum_renewal_gap,
        json_pong_absent=not any(packet.get("op") == "pong" for packet in packets),
        strength_requests=len(strengths), positive_requests=len(positives))


def software_catalog_response(request, state):
    """Owned software peer, not evidence for actual native HTTP routing."""
    from websockets.datastructures import Headers
    from websockets.http11 import Response
    from dghub_plugin.preset_catalog import CATALOG_PATH
    if request.path != CATALOG_PATH:
        return None
    state["catalog_requests"] = state.get("catalog_requests", 0) + 1
    rows = [dict(name=name, device_family="coyote", format_version=3, random=False,
                 waveform=[[[10, 20, 30, 40], [20, 30, 40, 50]]] * count)
            for name, count in SOFTWARE_PRESET_FRAMES.items()]
    rows.append(dict(name=SOFTWARE_RANDOM_PRESET, device_family="coyote", random=True, waveform=[]))
    body = json.dumps(dict(status="success", presets=rows), ensure_ascii=False).encode("utf-8")
    return Response(200, "OK", Headers({"Content-Type": "application/json",
        "Content-Length": str(len(body)), "Connection": "close"}), body)


def synthetic_frame(scale, hp):
    import numpy as np
    from src.hud_profiles import get_profile
    profile = get_profile(scale)
    roi, hud = profile["roi"], profile["hud"]
    frame = np.full((1080, 1920, 3), 20, np.uint8)
    top, bottom, thickness = hud["cap_top"], hud["cap_bottom"], hud["cap_stem_width"]
    for x, inward in ((hud["cap_left_x"], 1), (hud["cap_right_x"], -1)):
        left, right = x - thickness // 2, x + (thickness + 1) // 2
        frame[top:bottom, left:right] = 240
        tip_left, tip_right = (left, right + thickness) if inward == 1 else (left - thickness, right)
        frame[top:top + 1, tip_left:tip_right] = 240
        frame[bottom - 1:bottom, tip_left:tip_right] = 240
    start, stop = roi["bar_min_x"], roi["bar_max_x"]
    frame[hud["slot_y1"]:hud["slot_y2"], start:stop] = 30
    frame[hud["slot_y1"]:hud["slot_y2"], start:start + round((stop - start) * hp)] = 230
    return frame


async def run():
    import numpy as np
    from websockets.asyncio.server import serve
    from dghub_plugin.sdk_config import DEFAULT_PRESETS, defaults, manifest
    from dghub_plugin.sdk_transport import HostEnvironment
    from tools.dghub_live_entry import run_host
    frames = [synthetic_frame(100, hp) if hp is not None else np.zeros((1080, 1920, 3), np.uint8)
              for hp, count in [(1, 10), (.6, 20), (.9, 15), (.95, 55), (0, 10), (None, 4), (1, 8)]
              for _ in range(count)]
    token = uuid.uuid4().hex
    received, receipt_times = [], []
    state = dict(armed=False, death=False, lost_after_death=False, complete=False,
                 catalog_requests=0, catalog_verified=False, capture_ready=False, output_ready=False)
    capture = SimpleNamespace(is_ready=True, monitor_size=(1920, 1080), index=0, closed=False)

    def grab():
        image = frames[min(capture.index, len(frames) - 1)]
        capture.index += 1
        return image

    capture.grab = grab
    capture.close = lambda: setattr(capture, "closed", True)

    async def handler(ws):
        assert parse_qs(urlsplit(ws.request.path).query).get("token") == [token]
        async for raw in ws:
            message = json.loads(raw)
            if message["op"] == "hello":
                assert message["token"] == token and message["manifest"] == manifest()
                # Only the report copy is redacted; real handshake used token.
                received.append(message | {"token": "<redacted>"})
                receipt_times.append(time.monotonic())
                state["hello_ack_at"] = time.monotonic()
                await ws.send(json.dumps({"op": "hello_ack", "accepted": True, "sdk_version": "1.0"}))
                await ws.send(json.dumps({"op": "config", "data": defaults() | {"channel_mode": "both"}}))
                await ws.send(json.dumps({"op": "device_info", "connected": True, "device_type": "v3",
                                          "max_strength_a": 200, "max_strength_b": 200}))
                await ws.send(json.dumps({"op": "ping", "t": 123.5}))
                continue
            received.append(message)
            receipt_times.append(time.monotonic())
            if wave_request(message) == DEFAULT_PRESETS["death_pulse"]:
                state["death"] = True
            if message["op"] == "status":
                fields = message["fields"]
                state["catalog_verified"] |= fields.get("preset_catalog", {}).get("state") == "verified_startup_snapshot"
                state["capture_ready"] |= fields.get("capture_state") == "ok"
                state["output_ready"] |= fields.get("output_ready") is True
                if fields.get("output_ready") is True and state.get("first_output_ready_at") is None:
                    state["first_output_ready_at"] = time.monotonic()
                if state["capture_ready"] and state["output_ready"] and not state["armed"]:
                    state["armed"] = True
                    (root / "controls.json").write_text(json.dumps(dict(sequence=1, running=True)), encoding="utf-8")
                hp = fields.get("hp")
                state["death"] |= hp == 0
                state["lost_after_death"] |= state["death"] and fields.get("display_status") == "NO HUD"
                if state["lost_after_death"] and hp == 1 and not state["complete"]:
                    state["complete"] = True
                    await ws.send(json.dumps({"op": "stop", "reason": "software_acceptance_finished"}))

    with tempfile.TemporaryDirectory(prefix="dghub-software-host-") as directory:
        root = Path(directory)
        (root / "manifest.json").write_text(json.dumps(manifest(), ensure_ascii=False), encoding="utf-8")
        async with serve(handler, "127.0.0.1", 0, max_size=65536,
                         process_request=lambda connection, request: software_catalog_response(request, state)) as server:
            port = server.sockets[0].getsockname()[1]
            environment = HostEnvironment.from_environ(dict(DGHUB_HOST="127.0.0.1", DGHUB_PORT=str(port),
                DGHUB_TOKEN=token, DGHUB_PLUGIN_ID=manifest()["id"], DGHUB_PLUGIN_ROOT=str(root)))
            try:
                from dghub_plugin.replay_capture import ReplayControls
                code = await asyncio.wait_for(run_host(environment, capture_factory=lambda **kwargs: capture,
                    ui=ReplayControls(root)), 12)
            except TimeoutError:
                code = 124
        snapshots = list((root / "sessions").glob("*/config_snapshot.json"))
        records = [json.loads(path.with_name("session.json").read_text(encoding="utf-8")) for path in snapshots]
        snapshot = json.loads(snapshots[0].read_text(encoding="utf-8")) if len(snapshots) == 1 else {}
        record_ok = (len(records) == 1 and records[0]["state"] == "complete"
                     and records[0]["rows_dropped"] == records[0]["rows_invalid"] == 0
                     and snapshot.get("settings", {}).get("hud_scale_percent") == 100)
    waves = {wave_request(row) for row in received if wave_request(row) is not None}
    audit = audit_submissions(received, receipt_times=receipt_times)
    settling = audit_startup_settling(state)
    ok = (code == 0 and state["complete"] and waves == set(DEFAULT_PRESETS.values())
          and all(audit[key] for key in ("enabled_channels_correct", "independent_caps_correct",
              "rollback_leases_correct", "final_both_zero", "wave_requests_correct", "json_pong_absent",
              "wave_cadence_correct", "lease_renewal_observed"))
          and audit["full_cycle_requests"] > 0 and capture.closed and record_ok
          and state["catalog_requests"] == 1 and state["catalog_verified"]
          and settling["startup_settling_verified"])
    return dict(status="ok" if ok else "incomplete", transport="real loopback WebSocket", peer="software-only SDK host",
                source="synthetic 1080p capture; no screen reading", runtime_exit=code,
                five_preset_names=sorted(waves), both_channels_requested=audit["enabled_channels_correct"],
                final_both_zero=audit["final_both_zero"], submission_audit=audit, **settling,
                hud_loss_and_rebaseline=state["complete"], captured_frames=capture.index, messages=received,
                state=state, capture_closed=capture.closed,
                session_record_verified=record_ok, session_records=records,
                native_host_tested=False, real_game_tested=False, physical_device_used=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    report = asyncio.run(run())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "messages"}, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
