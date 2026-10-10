"""Fixture/unit coverage only; these tests never run an installed DGHub."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from tools import dghub_native_strategy_acceptance as native
from test_plugin_offline import run_async


def report_fixture():
    cases = {key:{} for key in ("generation", "retirement", "unregister_restart", "other_source",
        "waveforms", "long_waveform", "runtime_disconnect", "low_level")}
    cases["lease"] = dict(snapshots=[dict(a=60,b=25),dict(a=60,b=25),dict(a=20,b=10)],
        immediate_zero=dict(a=0,b=0),after_old_leases=dict(a=0,b=0),stopped_renewing=dict(a=0,b=0),duration_s=.3)
    cases["generation"]["old_token_invalidated"] = True
    cases["retirement"]["old_token_invalidated"] = True
    cases["waveforms"] = dict(full_template_preserved=True,truncated_native_match=True)
    cases["long_waveform"]["real_frames_preserved"] = True
    cases["runtime_disconnect"]["removed_from_manager"] = True
    cases["unregister_restart"]["same_id_isolation_proven"] = False
    return dict(status="observed",host_exe_sha256=native.HOST_SHA256,cases=cases,
        actual_route_token_predicate=True,actual_source_layers=True,
        installed_host_modified=False,installed_config_modified=False,network_used=False,
        physical_device_used=False,native_gui_tested=False,real_frozen_process_tested=False)


def test_native_method_report_keeps_observed_failure_not_release_acceptance():
    result = native.validate_report(report_fixture())
    assert result["cases"]["unregister_restart"]["same_id_isolation_proven"] is False
    assert not result["native_gui_tested"] and not result["real_frozen_process_tested"]


def standard_fixture():
    report=report_fixture()
    report["source_unchanged_during_probe"]=True
    report["cases"]["lease"]["return_delay_from_last_request_s"]=.36
    report["cases"]["unregister_restart"].update(settled_new_layer_maintained=True,
        guard_elapsed_s=.51,settled_unexpected_zero_device_writes=0,known_short_lease_settle_wait_s=.5)
    report["cases"]["waveforms"]["full_default_presets"]={name:{"both_channels_preserved":True}
        for name in ("连击","呼吸","潮汐","心跳节奏","CS2-死亡")}
    report["cases"]["other_source"]["other_preserved"]=True
    report["cases"]["long_waveform"].update(other_template_last=True,pending_after_other_source_replacement=0)
    return report


def test_guarded_standard_verifier_keeps_counterexample_and_scope():
    result=native.verify_standard(standard_fixture())
    assert result["status"]=="ok" and result["sdk_standard_verified"]
    assert result["raw_status"]=="observed"
    assert result["cases"]["unregister_restart"]["same_id_isolation_proven"] is False
    assert "not final EXE" in result["verification_scope"]
    assert "Provenance binding only" in result["source_hash_scope"]


@pytest.mark.parametrize("mutation",["short_guard","guarded_zero","new_layer_dipped","long_expiry",
    "another_layer_erased","old_pulse_survived","missing_template","source_drift"])
def test_selected_strategy_is_not_verified_when_required_check_is_missing(mutation):
    report=standard_fixture()
    cases=report["cases"]
    if mutation=="short_guard":
        cases["unregister_restart"]["guard_elapsed_s"]=.499
    elif mutation=="guarded_zero":
        cases["unregister_restart"]["settled_unexpected_zero_device_writes"]=1
    elif mutation=="new_layer_dipped":
        cases["unregister_restart"]["settled_new_layer_maintained"]=False
    elif mutation=="long_expiry":
        cases["lease"]["return_delay_from_last_request_s"]=.9
    elif mutation=="another_layer_erased":
        cases["other_source"]["other_preserved"]=False
    elif mutation=="old_pulse_survived":
        cases["long_waveform"]["pending_after_other_source_replacement"]=1
    elif mutation=="missing_template":
        del cases["waveforms"]["full_default_presets"]["潮汐"]
    else:
        report["source_unchanged_during_probe"]=False
    with pytest.raises(ValueError):
        native.verify_standard(report)


@pytest.mark.parametrize("field", ["installed_host_modified","installed_config_modified","network_used",
    "physical_device_used","native_gui_tested","real_frozen_process_tested"])
@pytest.mark.parametrize("value", [True,None])
def test_extended_or_unspecified_scope_cannot_be_accepted(field,value):
    report = report_fixture()
    report[field] = value
    with pytest.raises(ValueError,match="scope"):
        native.validate_report(report)


@pytest.mark.parametrize("field", ["actual_route_token_predicate","actual_source_layers"])
def test_fake_or_constant_route_predicate_is_not_native_evidence(field):
    report = report_fixture()
    report[field] = False
    with pytest.raises(ValueError,match="route"):
        native.validate_report(report)


def test_all_native_cases_are_required():
    report = report_fixture()
    del report["cases"]["low_level"]
    with pytest.raises(ValueError,match="cases"):
        native.validate_report(report)


@pytest.mark.parametrize("mutation", ["unknown_build","failed_runner","weaker_lease","no_truncation","no_retirement"])
def test_weaker_native_evidence_is_rejected(mutation):
    report = report_fixture()
    if mutation=="unknown_build":
        report["host_exe_sha256"]="0"*64
    elif mutation=="failed_runner":
        report["status"]="incomplete"
    elif mutation=="weaker_lease":
        report["cases"]["lease"]["after_old_leases"]={"a":50,"b":0}
    elif mutation=="no_truncation":
        report["cases"]["waveforms"]["truncated_native_match"]=False
    else:
        report["cases"]["retirement"]["old_token_invalidated"]=False
    with pytest.raises(ValueError):
        native.validate_report(report)


@pytest.mark.parametrize("relative", ["release/report.json","outside.json","build"])
def test_probe_target_cannot_escape_owned_build(tmp_path,monkeypatch,relative):
    monkeypatch.setattr(native,"ROOT",tmp_path)
    with pytest.raises(ValueError,match="owned build"):
        native.owned_build_path(tmp_path/relative)


def test_probe_does_not_overwrite_historical_report(tmp_path,monkeypatch):
    monkeypatch.setattr(native,"ROOT",tmp_path)
    path=tmp_path/"build/probe/report.json"
    path.parent.mkdir(parents=True)
    path.write_text("existing evidence")
    with pytest.raises(FileExistsError,match="immutable"):
        native.launch(path)
    assert path.read_text()=="existing evidence"


def test_memory_websocket_records_protocol_and_disconnect_without_socket():
    async def run():
        ws=native.MemoryWebSocket()
        await ws.send_text('{"op":"config","data":{}}')
        assert ws.sent==[{"op":"config","data":{}}]
        ws.incoming.put_nowait('{"op":"set_strength","pct":0}')
        assert json.loads(await ws.receive_text())["pct"]==0
        await ws.close()
        with pytest.raises(ConnectionError):
            await ws.receive_text()
        assert ws.closed
    run_async(run())


def test_memory_client_copies_frames_and_never_mutates_input():
    async def run():
        client=native.MemoryClient()
        frames=[[[10]*4,[20]*4]]
        await client.add_pulses("a",*frames)
        frames[0][1][0]=99
        assert client.rows[0]["frames"][0][1][0]==20
        assert client.rows[0]["count"]==1 and client.rows[0]["t"]>0
    run_async(run())


def test_frozen_runner_does_not_depend_on_dghub_forwarding_cli(monkeypatch):
    monkeypatch.setattr(native.sys,"frozen",True,raising=False)
    monkeypatch.setattr(native,"run_native",lambda:17)
    assert native.main()==17


def test_native_timeout_is_incomplete_not_a_pass(tmp_path,monkeypatch):
    monkeypatch.setattr(native,"ROOT",tmp_path)
    host=tmp_path/"host/DGLab-Console.exe"
    host.parent.mkdir()
    host.write_bytes(b"owned fixture")
    (host.parent/"_internal").mkdir()
    monkeypatch.setattr(native,"sha256",lambda path:native.HOST_SHA256)
    calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs))
        if argv[0]=="powershell.exe":
            return subprocess.CompletedProcess(argv,0)
        raise subprocess.TimeoutExpired(argv,kwargs["timeout"])
    monkeypatch.setattr(native.subprocess,"run",run)
    result=native.launch(tmp_path/"build/probe/report.json",host=host,timeout=3)
    assert result["status"]=="incomplete" and result["hard_timeout"]
    assert calls[-1][0][0]!=str(host) and "--plugin-runner" in calls[-1][0]
    assert calls[-1][1]["timeout"]==3
    assert json.loads((tmp_path/"build/probe/report.json").read_text())["status"]=="incomplete"
