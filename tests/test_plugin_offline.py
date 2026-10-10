"""DGHub shared-core contract: no real screens, registration or devices."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "dghub_plugin"


class _NoIOSelector(selectors.SelectSelector):
    """Timer/ready callbacks only: no OS handle may enter this test loop."""

    def register(self, *args, **kwargs):
        raise AssertionError("I/O registration is forbidden in offline adapter tests")

    def select(self, timeout=None):
        # Windows select([], [], [], timeout) is invalid. These fake transports
        # only await tasks/timers, so wait locally without allocating sockets.
        if timeout is None:
            raise AssertionError("Offline adapter test blocked without a timer")
        if timeout > 0:
            threading.Event().wait(timeout)
        return []


class _SocketFreeEventLoop(asyncio.SelectorEventLoop):
    """Keep real asyncio scheduling without its Windows socketpair wakeup."""

    def __init__(self):
        super().__init__(selector=_NoIOSelector())

    def _make_self_pipe(self):
        self._ssock = self._csock = None

    def _close_self_pipe(self):
        pass

    def _write_to_self(self):
        raise AssertionError("Cross-thread loop scheduling is outside this test harness")


def run_async(awaitable):
    # Do not relax tests/conftest.py's network ban or replace asyncio.run with
    # manual coroutine iteration: wait_for, task exceptions and cancellation
    # must all retain the standard event-loop behavior.
    with asyncio.Runner(loop_factory=_SocketFreeEventLoop) as runner:
        return runner.run(awaitable)


def test_async_harness_runs_tasks_and_timeouts_without_creating_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The offline event loop must not allocate a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "socketpair", forbidden)
    events = []

    async def exercise():
        async def pending():
            try:
                await asyncio.sleep(10)
            finally:
                events.append("cancelled")

        await asyncio.sleep(.001)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.create_task(pending()), timeout=.001)
        return "complete"

    assert run_async(exercise()) == "complete"
    assert events == ["cancelled"]


@pytest.fixture(scope="module")
def plugin():
    previous = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location("plugin_offline_tests", PLUGIN / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = previous


@pytest.fixture
def session(plugin):
    now = [0.0]
    records = []
    log = SimpleNamespace(append=records.append, close=lambda: None)
    instance = plugin.MockVisionSession(
        plugin.load_config(ROOT / "config.example.ini"), clock=lambda: now[0], log_writer=log)
    instance.test_clock = now
    instance.test_records = records
    yield instance
    instance.close()


def result(plugin, hp=.5, valid=True):
    return plugin.DetectionResult(
        health_raw=hp, health_stable=hp, state="injured" if valid else "unknown",
        valid=valid, confidence=1, anchor_score=1)


def active_output(plugin, session):
    session.trigger.dispatch(result(plugin, 1))
    session.test_clock[0] = .1
    session.trigger.dispatch(result(plugin, .5))
    assert session.mock_output()["strength"] > 0


def test_self_check_uses_shared_spec(plugin):
    output = plugin.self_check()
    assert output["status"] == "ok"
    assert output["core_root"] == str(ROOT)
    assert output["hardware_enabled"] is False and output["registration_enabled"] is False
    assert output["cumulative_damage_strength_with_low_background"] == 18
    assert output["channel"] == "A" and output["mode"] == "mock"
    assert {"death_once", "revival", "late_frame_rejected", "terminal_close"} <= set(output["checks"])


@pytest.mark.parametrize("mode", ["dghub", "dglab", "mock", "bogus"])
def test_session_forces_mock_and_does_not_mutate_input_config(plugin, mode):
    config = plugin.load_config(ROOT / "config.example.ini")
    config["trigger"]["sender_mode"] = mode
    config["dghub"] = {"channel": "both", "idle_strength": 100, "hardware_enabled": True}
    original = deepcopy(config)
    session = plugin.MockVisionSession(config, log_writer=plugin._MemoryLog())
    try:
        assert config == original
        assert session.config["trigger"]["sender_mode"] == "mock"
        output = session.mock_output()
        assert output["strength"] == 0 and output["mode"] == "mock" and output["channel"] == "A"
    finally:
        session.close()


@pytest.mark.parametrize("terminal", ["stop", "send_stop", "close"])
def test_terminal_client_cannot_be_rearmed_by_late_config(plugin, session, terminal):
    client = plugin.DGHubClient(session)

    async def exercise():
        await client.process_message(json.dumps({"op": "config", "data": {"running": True}}))
        old_ticket = session.frame_ticket()
        active_output(plugin, session)
        if terminal == "stop":
            await client.process_message('{"op":"stop"}')
        else:
            await getattr(client, terminal)()
        original_config = dict(client._config)
        await client.process_message('{"op":"config","data":{"running":true,"sender_mode":"dglab"}}')
        await client.process_message('{"op":"config_changed","key":"running","value":true}')
        assert client._config == original_config
        assert client._stop_requested
        assert not session.set_running(True)
        assert session.frame_ticket() is None
        assert session.process_frame(object(), old_ticket) is None
        assert session.mock_output()["strength"] == 0
        await client.close()

    run_async(exercise())
    assert sum(entry.get("type") == "session_end" for entry in session.test_records) == 1


def test_pause_is_resumable_but_old_frame_ticket_is_not(plugin, session, monkeypatch):
    client = plugin.DGHubClient(session)

    async def exercise():
        await client.process_message('{"op":"config","data":{"running":true}}')
        old_ticket = session.frame_ticket()
        active_output(plugin, session)
        await client.process_message('{"op":"config_changed","key":"running","value":false}')
        assert session.mock_output()["strength"] == 0
        assert session.frame_ticket() is None
        await client.process_message('{"op":"config_changed","key":"running","value":true}')
        assert session.frame_ticket() != old_ticket
        assert session.process_frame(object(), old_ticket) is None
        monkeypatch.setattr(session.pipeline, "process", lambda *a, **k: (result(plugin, .5), None, None))
        accepted = session.process_frame(object(), session.frame_ticket())
        assert accepted is not None and accepted[3]["actions"] == []
        assert session.mock_output()["strength"] == 0

    run_async(exercise())


@pytest.mark.parametrize("restart", ["pause_resume", "close"])
def test_in_flight_detection_cannot_publish_after_generation_change(plugin, session, monkeypatch, restart):
    entered, release = threading.Event(), threading.Event()
    outputs, errors = [], []

    def detect(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test detector was not released")
        return result(plugin, .1), None, None

    monkeypatch.setattr(session.pipeline, "process", detect)
    session.set_running(True)
    ticket = session.frame_ticket()
    active_output(plugin, session)

    def worker():
        try:
            outputs.append(session.process_frame(object(), ticket))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        if restart == "close":
            session.close()
        else:
            session.set_running(False)
            session.set_running(True)
        assert session.mock_output()["strength"] == 0
        release.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert not errors and outputs == [None]
        assert session.mock_output()["strength"] == 0
    finally:
        release.set()
        thread.join(timeout=1)


@pytest.mark.parametrize("failure", ["capture", "reset", "process", "dispatch", "invalid"])
def test_failures_clear_mock_feedback(plugin, session, monkeypatch, failure):
    session.set_running(True)
    ticket = session.frame_ticket()
    active_output(plugin, session)
    if failure == "capture":
        assert session.process_frame(None, ticket) is None
        assert session.frame_ticket() is None
    elif failure in {"reset", "process", "dispatch"}:
        def broken(*args, **kwargs):
            raise RuntimeError(f"fake {failure} failure")
        monkeypatch.setattr(session.pipeline, "process", lambda *a, **k: (result(plugin, .1), None, None))
        target = session.trigger if failure == "dispatch" else session.pipeline
        monkeypatch.setattr(target, failure, broken)
        with pytest.raises(RuntimeError, match=f"fake {failure} failure"):
            session.process_frame(object(), ticket)
        assert session.frame_ticket() is None
        assert session.reason == "vision_error"
        # Faults keep this generation paused; another frame cannot restart it.
        assert session.process_frame(object(), ticket) is None
    else:
        monkeypatch.setattr(session.pipeline, "process", lambda *a, **k: (result(plugin, valid=False), None, None))
        assert session.process_frame(object(), ticket)[3]["actions"] == []
    assert session.mock_output()["strength"] == 0


def test_stale_failure_cannot_pause_new_generation(plugin, session):
    session.set_running(True)
    old_ticket = session.frame_ticket()
    session.set_running(False)
    session.set_running(True)
    new_ticket = session.frame_ticket()
    session.invalidate("late_error", old_ticket)
    assert session.frame_ticket() == new_ticket


@pytest.mark.parametrize("failure", ["reset", "process"])
def test_in_flight_failure_cannot_pause_resumed_generation(plugin, session, monkeypatch, failure):
    entered, release = threading.Event(), threading.Event()
    errors = []

    def broken(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release old-generation detector")
        raise RuntimeError(f"late {failure} failure")

    session.set_running(True)
    old_ticket = session.frame_ticket()
    original = getattr(session.pipeline, failure)
    monkeypatch.setattr(session.pipeline, failure, broken)

    def worker():
        try:
            session.process_frame(object(), old_ticket)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        session.set_running(False)
        session.set_running(True)
        new_ticket = session.frame_ticket()
        assert new_ticket != old_ticket
        release.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert len(errors) == 1 and str(errors[0]) == f"late {failure} failure"
        assert session.frame_ticket() == new_ticket
        assert session.reason == "running_mock"

        # Restore the owner-thread pipeline only after the old call completes.
        monkeypatch.setattr(session.pipeline, failure, original)
        observed = [1.0]
        monkeypatch.setattr(session.pipeline, "process", lambda *a, **k: (result(plugin, observed[0]), None, None))
        assert session.process_frame(object(), new_ticket)[3]["actions"] == []
        observed[0] = .5
        session.test_clock[0] = .1
        assert session.process_frame(object(), new_ticket) is not None
        assert session.mock_output()["strength"] > 0
        assert session.frame_ticket() == new_ticket
    finally:
        release.set()
        thread.join(timeout=1)


class FakeTransport:
    def __init__(self, messages=(), failure=None):
        self.messages = iter(messages)
        self.sent = []
        self.closes = 0
        self.failure = failure

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def close(self):
        self.closes += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration:
            if self.failure:
                raise self.failure
            raise StopAsyncIteration


def test_disabled_client_never_registers_or_sends_output_even_with_transport(plugin, session):
    client = plugin.DGHubClient(session)
    transport = FakeTransport()
    client._ws = transport

    async def exercise():
        assert not await client.connect()
        await client.process_message('{"op":"device_info","connected":true}')
        await client.process_message('{"op":"hello_ack","accepted":true}')
        for op in ("hello", "trigger", "set_strength", "adjust_strength", "pulse", "set_config", "event"):
            await client._send_telemetry({"op": op, "pct": 100})
        response = await client.send_trigger("injury_pulse", {"damage_pct": 1})
        assert response["dispatched"] is False
        await client.send_stop()
        await client.send_log("info", "late")
        await client.send_status("late")
        await client.process_message('{"op":"ping","t":1}')
        assert transport.sent == []
        await client.close()

    run_async(exercise())
    assert transport.closes == 1


@pytest.mark.parametrize("failure", [None, RuntimeError("fake disconnect")])
def test_transport_end_is_terminal_and_cleans_session(plugin, session, failure):
    client = plugin.DGHubClient(session)
    transport = FakeTransport(['{"op":"config","data":{"running":true}}', '{"op":"ping","t":5}'], failure)
    client._ws = transport
    if failure:
        with pytest.raises(RuntimeError, match="fake disconnect"):
            run_async(client.handle_messages())
    else:
        run_async(client.handle_messages())
    assert transport.sent == [{"op": "pong", "t": 5}]
    assert transport.closes == 1 and client._ws is None
    assert session.frame_ticket() is None and not session.set_running(True)


def test_malformed_messages_do_not_enable_session(plugin, session):
    client = plugin.DGHubClient(session)

    async def exercise():
        for raw in ("{", "null", "[]", "5", '{"op":"config","data":null}',
                    '{"op":"config_changed","key":[],"value":true}'):
            await client.process_message(raw)
        assert session.frame_ticket() is None

    run_async(exercise())


def test_offline_cli_delegates_to_root_panel_with_explicit_mock(plugin, monkeypatch, tmp_path):
    calls = []
    fake_runner = SimpleNamespace(main=lambda args: calls.append(args),
                                  __file__=str(ROOT / "src/runner_real.py"))
    monkeypatch.setitem(sys.modules, "src.runner_real", fake_runner)
    assert plugin.run_offline(tmp_path / "config.ini", preview=True, fps=12) == 0
    assert calls == [["--enable-trigger", "--sender-mode", "mock", "--config",
                      str(tmp_path / "config.ini"), "--fps", "12", "--preview"]]


def test_default_entry_refuses_before_capture_or_config(plugin, monkeypatch, caplog):
    monkeypatch.setattr(plugin, "configure_logging", lambda: None)
    monkeypatch.setattr(plugin, "load_config", lambda *a: pytest.fail("must not read configuration"))
    monkeypatch.setattr(plugin, "run_offline", lambda *a, **k: pytest.fail("must not start capture"))
    monkeypatch.setattr(asyncio, "run", lambda *a, **k: pytest.fail("refusal must not create an event loop"))
    monkeypatch.setattr(asyncio, "Runner", lambda *a, **k: pytest.fail("refusal must not create an event loop"))
    assert plugin.main([]) == 2
    assert "Registration is blocked" in caplog.text


def test_manifest_and_freezer_are_explicitly_offline_only():
    manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-offline"
    assert manifest["config_schema"] == []
    assert "Mock" in manifest["name"]
    assert "禁用" in manifest["description"] and "A 通道" in manifest["description"]
    source = (PLUGIN / "helldiver_dglab.spec").read_text(encoding="utf-8")
    assert "required_core = {'src.vision_pipeline', 'src.trigger', 'src.feedback_mixer'}" in source
    assert "Forbidden device/legacy backend bundled" in source


def isolated(code, *, optimize=False):
    # Parent pytest fixtures cannot protect child processes, so guard the child.
    guard = """
import sys
class NoHardware:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'src.dglab_sender', 'mss', 'dxcam', 'websocket', 'websockets', 'qrcode'}:
            raise RuntimeError('Hardware import forbidden: ' + fullname)
sys.meta_path.insert(0, NoHardware())
def audit(event, args):
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo'}:
        raise RuntimeError('Network forbidden: ' + event)
sys.addaudithook(audit)
"""
    return subprocess.run([sys.executable, *(["-O"] if optimize else []), "-c", guard + code],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)


@pytest.mark.parametrize("optimize", [False, True])
def test_self_check_executes_all_frames_and_checks_under_optimization(optimize):
    code = """
import dghub_plugin.main as plugin
from src.trigger import TriggerDispatch
original = TriggerDispatch.dispatch
observed = []
def recording(self, result):
    observed.append(result.health_raw)
    return original(self, result)
TriggerDispatch.dispatch = recording
output = plugin.self_check()
if observed != [1, .7, .6, .6, .8, 0, 0, .3, 0]:
    raise RuntimeError('Self-check skipped dispatch: ' + repr(observed))
if output['status'] != 'ok' or output['hardware_enabled']:
    raise RuntimeError('Bad self-check result')
print('verified')
"""
    proc = isolated(code, optimize=optimize)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "verified"


@pytest.mark.parametrize("optimize", [False, True])
def test_self_check_failure_is_not_optimized_away(optimize):
    code = """
import dghub_plugin.main as plugin
from src.trigger import TriggerDispatch
TriggerDispatch.mock_output = lambda self: {'strength': 99}
plugin.self_check()
"""
    proc = isolated(code, optimize=optimize)
    assert proc.returncode != 0
    assert "Shared-core self-check failed: low_background_plus_damage" in proc.stderr


@pytest.mark.parametrize("pollution", ["path", "package", "submodule"])
def test_legacy_src_cannot_contaminate_shared_entry(pollution):
    setup = """
from pathlib import Path
from types import ModuleType
legacy = Path('dghub_plugin/src').resolve()
"""
    if pollution == "path":
        setup += "sys.path.insert(0, str(legacy.parent))\n"
    elif pollution == "package":
        setup += "old = ModuleType('src'); old.__path__ = [str(legacy)]; sys.modules['src'] = old\n"
    else:
        setup += "old = ModuleType('src.trigger'); old.__file__ = str(legacy / 'trigger.py'); sys.modules['src.trigger'] = old\n"
    setup += """
import dghub_plugin.main as plugin
from src import trigger, vision_pipeline, feedback_mixer
for module in (trigger, vision_pipeline, feedback_mixer):
    if not Path(module.__file__).resolve().is_relative_to(Path('src').resolve()):
        raise RuntimeError('Legacy core was imported')
print('root-only')
"""
    proc = isolated(setup)
    if pollution == "path":
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "root-only"
    else:
        assert proc.returncode != 0
        assert "Refusing" in proc.stderr
