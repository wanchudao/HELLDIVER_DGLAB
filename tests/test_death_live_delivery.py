from copy import deepcopy

import pytest

from tools.finalize_death_live_package import main_death_evidence, safe_names


@pytest.mark.parametrize("name", ["", "/absolute", "../outside", "x/../outside", "x\\outside", "C:/outside"])
def test_delivery_rejects_unsafe_archive_names(name):
    with pytest.raises(ValueError, match="Unsafe"):
        safe_names([name])


def test_delivery_rejects_duplicate_archive_names():
    with pytest.raises(ValueError, match="Duplicate"):
        safe_names(["manifest.json", "manifest.json"])
    safe_names(["manifest.json", "_internal/runtime.dll"])


def death_report():
    return dict(edition="main-official-backend", checks=dict(death=dict(status="ok",
        full_preferences_trigger_protocol_chain=True, final_both_zero=True, no_late_output=True,
        checks=[dict(mode=mode) for mode in ("pulse", "until_revive", "until_reset")])))


def test_delivery_requires_all_three_final_death_modes():
    report = death_report()
    main_death_evidence(report)
    report["checks"]["death"]["checks"].pop()
    with pytest.raises(ValueError, match="Incomplete"):
        main_death_evidence(report)


@pytest.mark.parametrize("key,value", [("status", "incomplete"), ("final_both_zero", False),
                                     ("no_late_output", False), ("full_preferences_trigger_protocol_chain", False)])
def test_delivery_rejects_weaker_final_evidence(key, value):
    report = deepcopy(death_report())
    report["checks"]["death"][key] = value
    with pytest.raises(ValueError, match="Incomplete"):
        main_death_evidence(report)
