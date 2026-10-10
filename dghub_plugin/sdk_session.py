"""SDK v1 protocol state machine with an injected transport, not a host launcher.

Only documented messages are emitted. Short rollback strength leases are
independent of complete waveform cycles. Send receipts mean submitted bytes,
never an acknowledgement that a physical device's waveform queue was cleared.
"""
import asyncio
from dataclasses import dataclass
import json
import math
import time

from dghub_plugin.offline_core import MockVisionSession
from dghub_plugin.sdk_config import (HOST_CONFIG_KEYS, LEGACY_CONFIG_KEYS, WAVE_TO_EVENT, core_config,
                                     defaults, manifest, validate)
from src.hud_profiles import PROFILE_VERSION, source_label


def _reject_constant(_value):
    raise ValueError("Nonfinite JSON")


@dataclass(frozen=True)
class Delivery:
    kind: str
    channel: str = ""
    strength: int = 0
    waveform: str = ""
    channels: tuple[str, ...] = ()
    duration: float = 0.0
    deadline: float = math.inf
    status: tuple | None = None


@dataclass(frozen=True)
class Batch:
    generation: int
    messages: tuple[str, ...]
    expires_at: float = math.inf
    output_revision: int | None = None
    deliveries: tuple[Delivery | None, ...] = ()


class SDKSession:
    """Single-owner protocol controller; all methods run on its event-loop thread.

    Capture/vision may finish later, but callers must marshal results back to
    this owner with the ticket acquired before capture. No socket is created.
    """

    STRENGTH_LEASE = .30
    STRENGTH_REFRESH = .10

    def __init__(self, base_config, *, clock=None, log_writer=None, available_presets=None,
                 preset_catalog=None):
        self.base_config = base_config
        self.clock = clock or time.monotonic
        self.log_writer = log_writer
        self.available_presets = available_presets
        self.preset_catalog = preset_catalog
        self.catalog_fingerprint = None
        self.phase = "new"
        self.generation = 0
        self.vision = None
        self.settings = None
        self.pending = None
        self.host_configuration = {}
        self.device = None
        self.closed = False
        self.armed = False
        self.last_error = ""
        self.last_pause_reason = "startup_paused"
        self._deadline = math.inf
        self._last_hp = None
        self._last_frame_at = None
        self._last_outputs = {"A": (0, ""), "B": (0, "")}
        self._sent_strengths = {"A": 0, "B": 0}
        self._strength_sent_at = {"A": -math.inf, "B": -math.inf}
        self._sent_waveform = ""
        self._wave_channels = frozenset()
        self._wave_until = 0.0
        self._acknowledged = False
        self._last_status = None
        self._output_revision = 0
        self.activation_pending = False

    def _batch(self, messages=(), *, expires_at=math.inf, output_revision=None, deliveries=()):
        return Batch(self.generation, tuple(json.dumps(message, ensure_ascii=False, allow_nan=False)
                                           for message in messages), expires_at, output_revision,
                     tuple(deliveries))

    def current(self, batch):
        return (batch.generation == self.generation and self.clock() < batch.expires_at
                and (batch.output_revision is None or batch.output_revision == self._output_revision))

    def hello(self, token):
        if self.phase != "new" or self.closed:
            raise RuntimeError("Handshake cannot be restarted on this instance")
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise ValueError("Missing or invalid session token")
        self.phase = "hello"
        self._deadline = self.clock() + 5.0
        return self._batch([{"op": "hello", "token": token, "manifest": manifest()}])

    def _zeros(self):
        self._last_outputs = {"A": (0, ""), "B": (0, "")}
        self._sent_strengths = {"A": 0, "B": 0}
        self._strength_sent_at = {"A": -math.inf, "B": -math.inf}
        self._sent_waveform = ""
        self._wave_channels = frozenset()
        self._wave_until = 0.0
        return [{"op": "set_strength", "channel": channel, "pct": 0} for channel in ("a", "b")]

    def status_fields(self, display_status):
        fields = {"display_status": display_status, "hp": self._last_hp,
                  "running": self.armed, "requested_a_pct": 0, "requested_b_pct": 0,
                  "settings_pending": self.pending is not None}
        fields["output_ready"] = bool(self.settings and self._acknowledged and not self.activation_pending)
        fields.update(recognition_running=self.armed, device_connected=self.device_connected,
                      device_output_enabled=self.device_output_enabled,
                      recognition_only=self.armed and not self.device_output_enabled,
                      output_allowed=self.settings["output_allowed"] if self.settings else False,
                      local_control_only=True, pause_reason=self.last_pause_reason)
        if self.settings is not None:
            scale = self.settings["hud_scale_percent"]
            fields.update(hud_scale=f"{scale / 100:.2f}", hud_scale_percent=scale,
                          hud_source=source_label(scale), hud_profile_version=PROFILE_VERSION,
                          resolution="1920x1080")
            fields["low_health_threshold_percent"] = self.settings["low_health_threshold_percent"]
        record_status = getattr(self.log_writer, "status", None)
        if record_status is not None:
            fields["session_record"] = record_status()
        if self.catalog_fingerprint is not None:
            fields["preset_catalog"] = dict(state="verified_startup_snapshot", device_family="coyote",
                count=len(self.available_presets), sha256=self.catalog_fingerprint,
                contract="DGHub 1.9.6 read-only Web API; not SDK negotiation")
        fields["startup_check"] = {"title": "HELLDIVER 启动检查", "steps": [
            {"key": "plugin", "title": "连接 DGHub", "state": "ok" if self._acknowledged else "pending",
             "detail": "SDK v1 握手已接受" if self._acknowledged else "等待宿主握手"},
            {"key": "config", "title": "HUD 与反馈配置", "state": "ok" if self.settings else "pending",
             "detail": "配置已校验" if self.settings else "等待完整配置"},
            {"key": "hud", "title": "有效 HUD", "state": "ok" if self._last_hp is not None else "pending",
             "detail": "血条已定位" if self._last_hp is not None else "等待 1920×1080 战场画面",
             "hint": "软件 HUD 档位需与游戏一致；F8 开始／暂停"},
        ]}
        return fields

    @property
    def device_connected(self):
        return bool(self.device and self.device["connected"])

    @property
    def device_output_enabled(self):
        return bool(self.armed and not self.closed and self.settings and
                    self.settings["output_allowed"] and self.device_connected and
                    self.host_configuration.get("enabled") is not False)

    def output_snapshot(self):
        """Actual SDK requests, not the unconnected vision mixer's proposals."""
        return {ch: {"strength": value[0], "waveform": value[1]}
                for ch, value in self._last_outputs.items()}

    def _set_output_allowed(self, allowed):
        if type(allowed) is not bool or self.settings is None:
            raise ValueError("Output insurance requires a configured boolean")
        if self.settings["output_allowed"] == allowed:
            return self._batch()
        self.settings["output_allowed"] = allowed
        if self.pending is not None:
            self.pending["output_allowed"] = allowed
        # Reopening insurance is not an arm request. Both transitions leave
        # a fresh baseline and require a new local F8/click before recognition.
        return self.pause("output_safety_locked" if not allowed else "output_safety_unlocked")

    def pause(self, reason="paused"):
        self.generation += 1
        self.armed = False
        self.last_pause_reason = reason
        self._last_hp = self._last_frame_at = None
        self._last_status = None
        if self.vision is not None:
            self.vision.set_running(False, reason)
        messages = self._zeros() if self._acknowledged else []
        if self._acknowledged:
            messages.append({"op": "status", "fields": self.status_fields(reason)})
        return self._batch(messages)

    def close(self, reason="stopped"):
        if self.closed:
            return self._batch()
        batch = self.pause(reason)
        self.closed = True
        self.phase = "closed"
        if self.vision is not None:
            self.vision.close(reason)
        return batch

    def _configure(self, data):
        values = validate(data, available_presets=self.available_presets)
        host_updates = {key: value for key, value in data.items() if key in HOST_CONFIG_KEYS}
        host_changed = any(self.host_configuration.get(key) != value
                           for key, value in host_updates.items())
        self.host_configuration.update(host_updates)
        if self.settings is not None:
            insurance = self._set_output_allowed(values["output_allowed"])
            # Even F8-paused configuration stays immutable for the session.
            if any(values[key] != self.settings[key] for key in values if key != "output_allowed"):
                self.pending = values
            stopped = self.pause("host_target_or_configuration_changed") if host_changed else self._batch()
            return self._batch(tuple(json.loads(raw) for raw in insurance.messages + stopped.messages) +
                               ({"op": "status", "fields": {
                "settings_pending": self.pending is not None,
                "settings_note": "保险立即生效；其他设置停止并重启插件后生效"}},))
        self.settings = values
        applied = core_config(self.base_config, values)
        self.vision = MockVisionSession(applied,
                                       clock=self.clock, log_writer=self.log_writer)
        record_config = getattr(self.log_writer, "configured", None)
        if record_config is not None:
            record_config(values, applied)
        self.phase = "ready"
        self._deadline = math.inf
        # Persisted legacy running is accepted but never controls a session.
        return self.pause("output_safety_locked" if not values["output_allowed"] else "startup_paused")

    def set_running(self, running):
        if type(running) is not bool:
            raise ValueError("running must be a boolean")
        if self.closed:
            return self._batch()
        if not running:
            return self.pause()
        if self.activation_pending:
            return self.pause("等待上一会话短租约结束；稍后按 F8 开始")
        if self.phase != "ready":
            return self.pause("waiting_configuration")
        if not self.settings["output_allowed"] or self.host_configuration.get("enabled") is False:
            return self.pause("output_safety_locked")
        if self.armed:
            return self._batch()
        self.generation += 1
        self.armed = True
        self.last_pause_reason = ""
        self._last_frame_at = self.clock()
        self.vision.set_running(True)
        return self._batch([{"op": "status", "fields": self.status_fields(
            "等待有效 HUD" if self.device_connected else "识别已启动；未连接设备，只识别不输出")}])

    def receive(self, raw):
        if self.closed:
            return self._batch()
        try:
            if not isinstance(raw, str) or len(raw) > 65536:
                raise ValueError("Malformed or oversized host message")
            message = json.loads(raw, parse_constant=_reject_constant)
            if not isinstance(message, dict):
                raise ValueError("Host message must be an object")
            op = message.get("op")
            if op == "stop":
                return self.close("host_stop")
            if op == "hello_ack":
                if self.phase != "hello" or message.get("accepted") is not True:
                    raise ValueError("Handshake rejected or out of order")
                version = message.get("sdk_version")
                if not isinstance(version, str) or version.split(".")[0] != "1":
                    raise ValueError("Unsupported SDK version")
                self._acknowledged = True
                self.phase = "config"
                self._deadline = self.clock() + 5.0
                return self._batch(self._zeros())
            if not self._acknowledged:
                raise ValueError("Message arrived before accepted handshake")
            if op in {"ping", "pong"}:
                stamp = message.get("t")
                if type(stamp) not in (int, float) or not math.isfinite(stamp):
                    raise ValueError("Invalid ping timestamp")
                # 1.9.6 does not accept JSON PongMsg in ClientMsg, despite the
                # guide listing it. The transport uses native WS control frames.
                return self._batch()
            if op == "config":
                return self._configure(message.get("data"))
            if op == "config_changed":
                key = message.get("key")
                if self.settings is None or key not in (set(defaults()) | HOST_CONFIG_KEYS | LEGACY_CONFIG_KEYS):
                    raise ValueError("Configuration change without a valid base")
                if key == "running":
                    if type(message.get("value")) is not bool:
                        raise ValueError("Legacy running must be a boolean")
                    return self._batch()
                if key == "output_allowed":
                    return self._set_output_allowed(message.get("value"))
                if key in HOST_CONFIG_KEYS:
                    candidate = dict(self.settings)
                    candidate.update(self.host_configuration)
                    candidate[key] = message.get("value")
                    validate(candidate, available_presets=self.available_presets)
                    if self.host_configuration.get(key) == message.get("value"):
                        return self._batch()
                    self.host_configuration[key] = message.get("value")
                    return self.pause("host_target_or_configuration_changed")
                values = dict(self.pending or self.settings)
                values[key] = message.get("value")
                return self._configure(values)
            if op == "device_info":
                if type(message.get("connected")) is not bool or message.get("device_type", "") not in ("", "v2", "v3"):
                    raise ValueError("Invalid device state")
                for name in ("max_strength_a", "max_strength_b"):
                    if type(message.get(name, 0)) is not int or not 0 <= message.get(name, 0) <= 1000:
                        raise ValueError("Invalid device maximum")
                device = {"connected": message["connected"], "device_type": message.get("device_type", ""),
                          "max_strength_a": message.get("max_strength_a", 0),
                          "max_strength_b": message.get("max_strength_b", 0)}
                changed = self.device != device
                self.device = device
                return self.pause("device_changed") if changed else self._batch()
            raise ValueError("Unsupported host message")
        except (ValueError, TypeError, KeyError):
            # Never echo untrusted raw packets, tokens or whole config values.
            self.last_error = "Host protocol/configuration validation failed"
            return self.close("protocol_error")

    def frame_ticket(self):
        return self.generation if self.armed and not self.closed else None

    def _frame_current(self, ticket):
        return ticket is not None and ticket == self.frame_ticket()

    def observe(self, result, ticket):
        """Accept a gated DetectionResult; used for deterministic event tests."""
        if not self._frame_current(ticket):
            return self._batch()
        try:
            action = self.vision.trigger.dispatch(result)
            self._last_frame_at = self.clock()
            self._last_hp = (result.health_stable if result.valid and
                             action["reason"] != "invalid_frame" else None)
            return self._output()
        except Exception:
            self.last_error = "Detection/output processing failed"
            return self.close("detection_error")

    def process_frame(self, frame, ticket):
        """Same production vision, with the selected strict 1080p HUD profile."""
        if not self._frame_current(ticket):
            return self._batch()
        if frame is None:
            return self.pause("capture_failed")
        try:
            result = self.vision.process_frame(frame, self.vision.frame_ticket())
            if result is None or not self._frame_current(ticket):
                return self._batch()
            observation = result[0]
            self._last_frame_at = self.clock()
            self._last_hp = observation.health_stable if observation.valid else None
            return self._output()
        except Exception:
            self.last_error = "Vision processing failed"
            return self.close("vision_error")

    def accept_processed_frame(self, processed, ticket, captured_at):
        """Marshal worker-owned vision back to the protocol owner.

        The worker took both tickets before capture. MockVisionSession already
        checks its ticket before dispatch; this check also prevents a late
        result from refreshing transport watchdogs or producing host messages.
        """
        if not self._frame_current(ticket):
            return self._batch()
        now = self.clock()
        gap = float(self.vision.config.get("trigger", {}).get("max_frame_gap_sec", 1.0))
        if (type(captured_at) not in (int, float) or not math.isfinite(captured_at)
                or not 0 <= now - captured_at < gap):
            return self.pause("stale_capture")
        if processed is None:
            return self.pause("capture_failed")
        try:
            observation, _roi, _health, action = processed
            self._last_frame_at = captured_at
            self._last_hp = (observation.health_stable if observation.valid and
                             action.get("reason") != "invalid_frame" else None)
            return self._output()
        except Exception:
            self.last_error = "Worker vision result failed validation"
            return self.close("vision_error")

    def tick(self):
        if self.closed:
            return self._batch()
        if self.clock() >= self._deadline:
            return self.close("handshake_timeout")
        if not self.armed:
            return self._batch()
        gap = self.vision.config.get("trigger", {}).get("max_frame_gap_sec", 1.0)
        if self.clock() - self._last_frame_at >= gap:
            return self.pause("frame_timeout")
        return self._output()

    def _output(self):
        outputs = self.vision.mock_output()["channels"]
        if not self.device_output_enabled:
            outputs = {ch: dict(item, strength=0, waveform="") for ch, item in outputs.items()}
        now = self.clock()
        messages = []
        deliveries = []
        targets = {channel: (item["strength"], item["waveform"]) for channel, item in outputs.items()}
        changed = targets != self._last_outputs
        if changed:
            self._output_revision += 1
            self._last_outputs = targets
        revision = self._output_revision
        active = tuple(channel for channel, (strength, _wave) in targets.items() if strength)
        self._wave_channels = self._wave_channels.intersection(active)
        for channel, (strength, _wave) in targets.items():
            if strength and (strength != self._sent_strengths[channel] or
                    now - self._strength_sent_at[channel] >= self.STRENGTH_REFRESH - 1e-9):
                messages.append({"op": "trigger", "action": "strength", "channel": channel.lower(),
                                 "delta_pct": strength, "strength_mode": "rollback",
                                 "duration_s": self.STRENGTH_LEASE})
                deliveries.append(Delivery("strength", channel=channel, strength=strength))
            elif not strength and self._sent_strengths[channel]:
                messages.append({"op": "set_strength", "channel": channel.lower(), "pct": strength})
                deliveries.append(Delivery("strength", channel=channel))
        if not active:
            # An unsent positive snapshot is superseded even when there has
            # never been a delivery receipt (e.g. HUD loss during a write).
            if changed or self._sent_waveform or any(self._sent_strengths.values()):
                for channel in ("A", "B"):
                    if not any(d.channel == channel for d in deliveries):
                        messages.append({"op": "set_strength", "channel": channel.lower(), "pct": 0})
                        deliveries.append(Delivery("strength", channel=channel))
            self._sent_waveform = ""
            self._wave_channels = frozenset()
            self._wave_until = 0.0
        else:
            wave = targets[active[0]][1]
            if self._wave_due(wave, active, now):
                event = WAVE_TO_EVENT[wave]
                preset = self.settings[event + "_preset"]
                if self.preset_catalog is None or preset not in self.preset_catalog:
                    raise ValueError("Verified waveform timing metadata is required")
                info = self.preset_catalog[preset]
                cycle = 1.0 if info.random else info.cycle_seconds
                remaining = self.vision.waveform_remaining(event)
                duration = min(cycle, remaining)
                # SDK waveform data has 100ms frames. Do not fabricate a last
                # frame that would extend a finite event beyond its deadline.
                frames = math.floor(duration * 10 + 1e-8)
                if frames:
                    duration = frames / 10
                    host_channel = "both" if len(active) == 2 else active[0].lower()
                    if not info.random and duration >= cycle - 1e-9:
                        packet = {"op": "pulse", "preset": preset, "channel": host_channel}
                    else:
                        packet = {"op": "trigger", "action": "waveform", "preset": preset,
                                  "channel": host_channel, "duration_s": duration}
                    messages.append(packet)
                    deliveries.append(Delivery("wave", waveform=wave, channels=active, duration=duration,
                                               deadline=now + remaining))
        status = (self._last_hp, outputs["A"]["strength"], outputs["B"]["strength"])
        if status != self._last_status:
            display = "NO HUD" if self._last_hp is None else (
                "运行中" if self.device_output_enabled else "识别中；未连接设备，无输出")
            fields = self.status_fields(display)
            fields.update(requested_a_pct=status[1], requested_b_pct=status[2])
            messages.append({"op": "status", "fields": fields})
            deliveries.append(Delivery("status", status=status))
        # Every positive batch has a short local expiry. An injected transport
        # must validate it again before each message; this is NOT a host lease.
        return self._batch(messages, expires_at=now + .1, output_revision=revision,
                           deliveries=deliveries)

    def _wave_due(self, wave, channels, now):
        return (wave != self._sent_waveform or not set(channels).issubset(self._wave_channels)
                or now >= self._wave_until - 1e-9)

    def delivery_due(self, delivery):
        """Suppress duplicate queued work after an earlier successful write."""
        if delivery is None:
            return True
        if delivery.kind == "strength":
            return (delivery.strength != self._sent_strengths[delivery.channel] or
                    (delivery.strength > 0 and self.clock() - self._strength_sent_at[delivery.channel]
                     >= self.STRENGTH_REFRESH - 1e-9) or delivery.strength == 0)
        if delivery.kind == "wave":
            return self._wave_due(delivery.waveform, delivery.channels, self.clock())
        return delivery.status != self._last_status

    def delivered(self, batch, delivery):
        """Commit only successful submissions belonging to the current session."""
        if delivery is None or batch.generation != self.generation or self.closed:
            return
        now = self.clock()
        if delivery.kind == "strength":
            if self._last_outputs[delivery.channel][0] != delivery.strength:
                return
            self._sent_strengths[delivery.channel] = delivery.strength
            self._strength_sent_at[delivery.channel] = now
        elif delivery.kind == "wave":
            active = tuple(ch for ch, (strength, _wave) in self._last_outputs.items() if strength)
            if (not active or self._last_outputs[active[0]][1] != delivery.waveform or
                    not set(active).issubset(delivery.channels)):
                return
            self._sent_waveform = delivery.waveform
            self._wave_channels = frozenset(active)
            self._wave_until = min(now + delivery.duration, delivery.deadline)
        elif delivery.kind == "status" and self.current(batch):
            self._last_status = delivery.status

    def prepare_delivery(self, raw, delivery):
        """Recheck a finite waveform at send time, after preceding channel writes."""
        if delivery is None or delivery.kind != "wave" or math.isinf(delivery.deadline):
            return raw, delivery
        frames = math.floor(min(delivery.duration, delivery.deadline - self.clock()) * 10 + 1e-8)
        if frames <= 0:
            return None, delivery
        duration = frames / 10
        if duration >= delivery.duration - 1e-9:
            return raw, delivery
        packet = json.loads(raw)
        packet.update(op="trigger", action="waveform", duration_s=duration)
        from dataclasses import replace
        return json.dumps(packet, ensure_ascii=False, allow_nan=False), replace(delivery, duration=duration)


class SerializedWriter:
    """Serialize an injected async send function; stop invalidates queued batches.

    Nothing in this class connects to a host. In-flight network writes cannot
    be recalled; a real host must provide its own tested cancellation semantics.
    """
    def __init__(self, session, send):
        self.session = session
        self.send = send
        self._lock = asyncio.Lock()

    async def write(self, batch):
        async with self._lock:
            try:
                for index, message in enumerate(batch.messages):
                    if batch.generation != self.session.generation:
                        return False
                    if batch.output_revision is not None and batch.output_revision != self.session._output_revision:
                        return False
                    if not self.session.current(batch):
                        raise TimeoutError("Output batch expired before delivery")
                    delivery = batch.deliveries[index] if batch.deliveries else None
                    if not self.session.delivery_due(delivery):
                        continue
                    message, delivery = self.session.prepare_delivery(message, delivery)
                    if message is None:
                        continue
                    await asyncio.wait_for(self.send(message), timeout=.25)
                    if batch.generation != self.session.generation:
                        return False
                    if self.session.clock() >= batch.expires_at:
                        raise TimeoutError("Output batch expired during delivery")
                    self.session.delivered(batch, delivery)
                return True
            except (Exception, asyncio.CancelledError):
                self.session.close("send_failed")
                # Try each channel independently; failing A must not skip B.
                for channel in ("a", "b"):
                    try:
                        await asyncio.wait_for(self.send(json.dumps(
                            {"op": "set_strength", "channel": channel, "pct": 0})), timeout=.25)
                    except Exception:
                        pass
                raise
