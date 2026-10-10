"""Pinned DGHub 1.9.6 read-only Web API, NOT SDK v1 negotiation.

Query only the numeric loopback endpoint supplied by the validated launcher.
No token acquisition, proxy, redirect, discovery, device or preset mutations.
A bounded startup snapshot validates names and V3 cycle geometry; it cannot
prove live-edit atomicity or the host's playback/stop lifecycle contract.
"""
import asyncio
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from types import MappingProxyType

CATALOG_PATH = "/api/bilibili/pulse-presets?device_family=coyote"
MAX_BYTES = 32 * 1024 * 1024
MAX_HEADER_BYTES = 16384


@dataclass(frozen=True)
class PresetInfo:
    """Read-only timing metadata, not a copy of the host's waveform content.

    V3 frames are 100 ms each. The native random entry has no fixed frames and
    no fixed format version; its zero cycle is never used as a replay interval.
    """

    name: str
    frame_count: int
    format_version: int | None
    random: bool = False

    @property
    def cycle_seconds(self):
        return self.frame_count / 10


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate catalog field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite catalog field")


def _decode_rows(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
        raise ValueError("Invalid catalog size")
    data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)
    if not isinstance(data, dict) or data.get("status") != "success":
        raise ValueError("Invalid catalog response")
    rows = data.get("presets")
    if not isinstance(rows, list) or not 0 < len(rows) <= 10000:
        raise ValueError("Invalid catalog entries")
    names = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("device_family") != "coyote":
            raise ValueError("Invalid catalog device family")
        name = row.get("name")
        if (not isinstance(name, str) or not 1 <= len(name) <= 128 or name != name.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in name) or name in names):
            raise ValueError("Invalid or ambiguous preset name")
        names.add(name)
    return rows


def decode_names(body):
    """Compatibility parser for callers that need only the preset names."""
    return frozenset(row["name"] for row in _decode_rows(body))


def _validate_frames(frames, random):
    if not isinstance(frames, list) or (not random and not frames):
        raise ValueError("Invalid catalog waveform")
    for frame in frames:
        if not isinstance(frame, list) or len(frame) != 2:
            raise ValueError("Invalid catalog frame")
        for values, maximum in zip(frame, (240, 100)):
            if (not isinstance(values, list) or len(values) != 4
                    or any(type(value) is not int or not 0 <= value <= maximum
                           for value in values)):
                raise ValueError("Invalid catalog frame values")


def decode_catalog(body):
    """Validate native Coyote V3 templates and return immutable timing data.

    Fixed presets must contain a nonempty waveform. An empty waveform is only
    valid for the explicitly random native preset. If the API exposes both
    canonical payload frames and its waveform alias, they must agree.
    """
    entries = {}
    for row in _decode_rows(body):
        random = row.get("random", False)
        if type(random) is not bool:
            raise ValueError("Invalid catalog random marker")
        version = row.get("format_version")
        if ((version is not None and (type(version) is not int or version != 3))
                or (not random and version != 3)):
            raise ValueError("Unsupported catalog waveform format")
        if row.get("preset_kind", "waveform") != "waveform":
            raise ValueError("Invalid catalog preset kind")
        frames = row.get("waveform")
        _validate_frames(frames, random)
        if "payload" in row:
            payload = row["payload"]
            if not isinstance(payload, dict):
                raise ValueError("Ambiguous catalog waveform aliases")
            _validate_frames(payload.get("frames"), random)
            if payload["frames"] != frames:
                raise ValueError("Ambiguous catalog waveform aliases")
        name = row["name"]
        entries[name] = PresetInfo(name, len(frames), version, random)
    return MappingProxyType(entries)


async def _retrieve(environment, opener, decoder):
    reader, writer = await opener(environment.host, environment.port, limit=MAX_HEADER_BYTES)
    try:
        address = f"[{environment.host}]" if ":" in environment.host else environment.host
        request = (f"GET {CATALOG_PATH} HTTP/1.1\r\nHost: {address}:{environment.port}\r\n"
                   "Accept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n")
        writer.write(request.encode("ascii"))
        await writer.drain()
        header = await reader.readuntil(b"\r\n\r\n")
        if len(header) > MAX_HEADER_BYTES:
            raise ValueError("Catalog header too large")
        lines = header.decode("ascii").split("\r\n")
        if not re.fullmatch(r"HTTP/1\.[01] 200(?: [^\r\n]*)?", lines[0]):
            raise ValueError("Catalog HTTP status rejected")
        headers = {}
        for line in lines[1:-2]:
            key, separator, value = line.partition(":")
            if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
                raise ValueError("Invalid catalog header")
            key = key.lower()
            if key in headers:
                raise ValueError("Duplicate catalog header")
            headers[key] = value.strip()
        if (headers.get("content-type", "").split(";", 1)[0].lower() != "application/json"
                or "transfer-encoding" in headers
                or headers.get("content-encoding", "identity").lower() != "identity"):
            raise ValueError("Unsupported catalog HTTP format")
        length = headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit() or not 0 < int(length) <= MAX_BYTES:
                raise ValueError("Invalid catalog content length")
            body = await reader.readexactly(int(length))
        else:
            parts, total = [], 0
            while True:
                chunk = await reader.read(min(65536, MAX_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("Catalog body too large")
                parts.append(chunk)
            body = b"".join(parts)
        return decoder(body), hashlib.sha256(body).hexdigest()
    finally:
        writer.close()
        # No unbounded shutdown wait; external cancellation still propagates.
        try:
            await asyncio.wait_for(writer.wait_closed(), .1)
        except Exception:
            pass


async def _fetch(environment, *, opener, timeout, decoder):
    try:
        if (not ipaddress.ip_address(environment.host).is_loopback
                or type(environment.port) is not int or not 1 <= environment.port <= 65535):
            raise ValueError("Invalid local catalog endpoint")
        return await asyncio.wait_for(
            _retrieve(environment, opener or asyncio.open_connection, decoder), timeout)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never publish host exception text, credentials, response or URLs.
        raise ValueError("DGHub 1.9.6 波形目录不可用或格式不兼容；未启动输出") from None


async def fetch_catalog(environment, *, opener=None, timeout=2):
    """Compatibility name-only snapshot, retaining the original tuple shape."""
    return await _fetch(environment, opener=opener, timeout=timeout, decoder=decode_names)


async def fetch_catalog_snapshot(environment, *, opener=None, timeout=2):
    """Strict full timing snapshot plus the SHA-256 of the received API body."""
    return await _fetch(environment, opener=opener, timeout=timeout, decoder=decode_catalog)
