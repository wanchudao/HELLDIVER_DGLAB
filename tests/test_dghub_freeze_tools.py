from types import SimpleNamespace

import pytest

from tools.dghub_frozen_freeze_acceptance import OwnedProcessSuspender, SoftwareLeaseModel


def lease(channel="a", value=20):
    return dict(op="trigger", action="strength", channel=channel, delta_pct=value,
                strength_mode="rollback", duration_s=.30)


def test_model_expires_received_short_lease_without_new_request():
    model = SoftwareLeaseModel()
    model.receive(lease("a", 20), 10)
    model.receive(lease("b", 7), 10.1)
    assert model.snapshot(10.2) == {"a": 20, "b": 7}
    assert model.snapshot(10.31) == {"a": 0, "b": 7}
    assert model.snapshot(10.41) == {"a": 0, "b": 0}


def test_model_manual_zero_cannot_rebound_positive_and_new_lease_refreshes():
    model = SoftwareLeaseModel()
    model.receive(lease(), 0)
    model.receive(lease(value=7), .1)
    assert model.snapshot(.35)["a"] == 7
    model.receive(dict(op="set_strength", channel="a", pct=0), .36)
    assert model.snapshot(.37)["a"] == model.snapshot(1)["a"] == 0


@pytest.mark.parametrize("change", [dict(duration_s=1), dict(strength_mode="permanent"), dict(channel="both")])
def test_model_does_not_validate_a_different_output_policy(change):
    model = SoftwareLeaseModel()
    packet = lease() | change
    with pytest.raises(ValueError):
        model.receive(packet, 1)


def test_suspender_only_uses_already_held_child_handle_and_resumes_once():
    child = SimpleNamespace(pid=123, _handle=456, poll=lambda: None)
    calls = []
    suspend = lambda handle: calls.append(("suspend", handle)) or 0
    resume = lambda handle: calls.append(("resume", handle)) or 0
    controller = OwnedProcessSuspender(child, nt_suspend=suspend, nt_resume=resume)
    controller.suspend()
    try:
        assert controller.suspended
        raise RuntimeError("fixture failure")
    except RuntimeError:
        pass
    finally:
        controller.resume()
    controller.resume()
    assert calls == [("suspend", 456), ("resume", 456)]
    assert controller.suspension_succeeded and controller.resume_succeeded and not controller.suspended


def test_suspender_rejects_changed_or_exited_process_without_native_call():
    child = SimpleNamespace(pid=123, _handle=456, poll=lambda: None)
    calls = []
    controller = OwnedProcessSuspender(child, nt_suspend=lambda handle: calls.append(handle) or 0,
                                      nt_resume=lambda handle: calls.append(handle) or 0)
    child.pid = 999
    with pytest.raises(RuntimeError):
        controller.suspend()
    assert calls == []


def test_failed_resume_retains_suspended_state_for_cleanup_retry():
    child = SimpleNamespace(pid=123, _handle=456, poll=lambda: None)
    controller = OwnedProcessSuspender(child, nt_suspend=lambda handle: 0, nt_resume=lambda handle: -1)
    controller.suspend()
    with pytest.raises(RuntimeError):
        controller.resume()
    assert controller.suspended and not controller.resume_succeeded
