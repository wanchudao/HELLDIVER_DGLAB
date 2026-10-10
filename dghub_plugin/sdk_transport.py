"""One documented SDK WebSocket connection; no discovery or device backend.

This is an implementation component, not installed-host acceptance. The
shipping entry is a separate gate until host output lifecycle is verified.
"""
import asyncio
from dataclasses import dataclass, field
import ipaddress
import logging
import os
import time
import math
from pathlib import Path
from urllib.parse import quote
from websockets.exceptions import ConnectionClosedOK

from dghub_plugin.sdk_config import PLUGIN_ID
from dghub_plugin.sdk_session import SerializedWriter


_WIRE_LOGGER = logging.Logger("helldiver.dghub.protocol", level=logging.CRITICAL)
_WIRE_LOGGER.propagate = False
_WIRE_LOGGER.addHandler(logging.NullHandler())


def default_connector():
    from websockets.asyncio.client import connect

    class LocalConnect(connect):
        def process_redirect(self, exc):
            # The launch environment names one local host. A redirect must
            # never move its URI token to another endpoint (or even a new path).
            return exc

    return LocalConnect


@dataclass(frozen=True)
class HostEnvironment:
    host: str
    port: int
    token: str = field(repr=False)
    plugin_root: Path

    @classmethod
    def from_environ(cls, environ=None):
        data = os.environ if environ is None else environ
        try:
            host = data["DGHUB_HOST"]
            port_text = data["DGHUB_PORT"]
            token = data["DGHUB_TOKEN"]
            root_text = data["DGHUB_PLUGIN_ROOT"]
            plugin_id = data["DGHUB_PLUGIN_ID"]
            if host == "localhost":
                host = "127.0.0.1"
            # A local desktop host needs no DNS lookup, LAN scanning or proxy.
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError()
            if (not isinstance(port_text, str) or not port_text.isascii()
                    or not port_text.isdigit() or not 1 <= int(port_text) <= 65535):
                raise ValueError()
            if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                    or any(ord(c) < 32 for c in token)):
                raise ValueError()
            root = Path(root_text)
            if (plugin_id != PLUGIN_ID or not root.is_absolute() or not root.is_dir()
                    or not (root / "manifest.json").is_file()):
                raise ValueError()
            return cls(host=host, port=int(port_text), token=token, plugin_root=root.resolve())
        except (KeyError, TypeError, ValueError, OSError):
            # Never include the token, raw environment or URI in an error.
            raise ValueError("Missing or invalid DGHub launch environment") from None

    def uri(self):
        address = f"[{self.host}]" if ":" in self.host else self.host
        return f"ws://{address}:{self.port}/ws/plugin?token={quote(self.token, safe='')}"


class DGHubTransport:
    """Protocol, UI intent and ticks share one asyncio owner, not capture.

    A driver polls bounded worker results and the UI without blocking. It
    returns batches which are checked again by SerializedWriter. No reconnect
    is attempted: host/device changes leave output paused for user action.
    """
    def __init__(self, session, environment, *, connector=None, driver=None,
                 catalog_loader=None, tick_interval=.02, activation_delay=.5):
        if not .005 <= tick_interval <= .1:
            raise ValueError("Invalid control tick interval")
        if type(activation_delay) not in (int, float) or not math.isfinite(activation_delay) or not 0 <= activation_delay <= 5:
            raise ValueError("Invalid startup settling interval")
        self.session = session
        self.environment = environment
        self.connector = connector
        self.driver = driver
        self.catalog_loader = catalog_loader
        self.tick_interval = tick_interval
        self.activation_delay = activation_delay
        self._activation_until = math.inf
        self.writer = None
        self.connected = False
        self.started = False
        self.finished = False
        self.reason = "not_started"
        self._loop = None
        self._stop = None

    def request_stop(self):
        """May be called from UI/another thread; never writes from that thread."""
        if self._loop is not None and not self.finished:
            try:
                owner = asyncio.get_running_loop() is self._loop
            except RuntimeError:
                owner = False
            if owner:
                self._stop.set()
            else:
                try:
                    self._loop.call_soon_threadsafe(self._stop.set)
                except RuntimeError:
                    pass

    async def _write(self, batch):
        if batch.messages:
            await self.writer.write(batch)

    async def _read(self, ws):
        async for raw in ws:
            acknowledged = self.session._acknowledged
            batch = self.session.receive(raw)
            if not acknowledged and self.session._acknowledged:
                self.session.activation_pending = self.activation_delay > 0
                self._activation_until = time.monotonic() + self.activation_delay
            terminal_host_stop = (self.session.closed
                                  and self.session.last_pause_reason == "host_stop"
                                  and not self.session.last_error)
            try:
                await self._write(batch)
            except ConnectionClosedOK:
                if not terminal_host_stop:
                    raise
                # DGHub sends StopMsg then closes immediately without waiting
                # for zero/status replies. Only this terminal batch may accept
                # a normal close; it doesn't confirm zero delivery. The writer
                # still attempts both zeros and invalidates local output.
                return
            if self.session.closed:
                return
        if not self.session.closed:
            self.reason = "connection_lost"

    async def _tick(self):
        while not self.session.closed and not self._stop.is_set():
            if self.session.activation_pending and time.monotonic() >= self._activation_until:
                self.session.activation_pending = False
                if self.session.settings is not None:
                    await self._write(self.session._batch([{"op": "status", "fields":
                        self.session.status_fields("已连接，暂停；按 F8 开始")}]))
            if self.driver is not None:
                for batch in self.driver.poll(self.session):
                    await self._write(batch)
            await self._write(self.session.tick())
            await asyncio.sleep(self.tick_interval)

    async def run(self):
        if self.started:
            raise RuntimeError("Transport instances cannot be restarted")
        self.started = True
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        connect = self.connector
        if connect is None:
            connect = default_connector()
        tasks = []
        try:
            if self.catalog_loader is not None:
                async def load():
                    catalog, fingerprint = await self.catalog_loader(self.environment)
                    if not self.session.closed and not self._stop.is_set():
                        self.session.available_presets = frozenset(catalog)
                        if hasattr(catalog, "values"):
                            self.session.preset_catalog = catalog
                        self.session.catalog_fingerprint = fingerprint
                tasks = [asyncio.create_task(load()), asyncio.create_task(self._stop.wait())]
                done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if self._stop.is_set():
                    self.reason = "user_exit"
                    return 0
                for task in done:
                    task.result()
                tasks[1].cancel()
                await asyncio.gather(tasks[1], return_exceptions=True)
                tasks = []
            async with connect(self.environment.uri(), open_timeout=3, close_timeout=.25,
                               ping_interval=2, ping_timeout=2, max_size=65536,
                               max_queue=16, proxy=None, logger=_WIRE_LOGGER) as ws:
                self.connected = True
                self.reason = "connected"
                self.writer = SerializedWriter(self.session, ws.send)
                try:
                    await self._write(self.session.hello(self.environment.token))
                    tasks = [asyncio.create_task(self._read(ws)), asyncio.create_task(self._tick()),
                             asyncio.create_task(self._stop.wait())]
                    done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        if not task.cancelled():
                            task.result()
                    if self._stop.is_set():
                        self.reason = "user_exit"
                    elif self.session.closed:
                        self.reason = "session_stopped"
                except asyncio.CancelledError:
                    self.reason = "cancelled"
                    raise
                finally:
                    # Stop producers first and send zeros before context exit
                    # closes the socket. Cancellation cannot skip this path.
                    final_batch = self.session.close(self.reason)
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    results = []
                    if tasks:
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                    await self._write(final_batch)
                    # Don't discard a concurrent non-terminal producer fault
                    # when the reader completed an expected host stop first.
                    for result in results:
                        if isinstance(result, Exception):
                            raise result
        except asyncio.CancelledError:
            self.reason = "cancelled"
            raise
        except Exception:
            # The connector may put the secret URI in its exception message.
            if self.catalog_loader is not None and self.session.catalog_fingerprint is None:
                self.reason = "catalog_failed"
                self.session.last_error = "DGHub 波形目录校验失败；未启动输出"
            else:
                self.reason = "transport_failed"
                self.session.last_error = "DGHub connection or send failed"
        finally:
            self.session.close(self.reason)
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self.driver is not None:
                self.driver.close()
            self.connected = False
            self.finished = True
        return 0 if self.reason in {"session_stopped", "user_exit"} and not self.session.last_error else 1
