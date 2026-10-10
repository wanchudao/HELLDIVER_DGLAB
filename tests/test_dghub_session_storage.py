import json

import pytest

from dghub_plugin.session_record import SessionRecord
from dghub_plugin.session_storage import record_data_home


def test_real_data_is_outside_replaceable_package_and_stable_across_upgrade(tmp_path):
    plugin = tmp_path / "plugins/helldiver_dglab"
    plugin.mkdir(parents=True)
    local = tmp_path / "local_appdata"
    local.mkdir()
    env = {"LOCALAPPDATA": str(local)}
    home = record_data_home(plugin, capture_source="selected_monitor", environ=env)
    assert home.is_relative_to(local) and not home.is_relative_to(plugin)
    assert home == record_data_home(plugin, capture_source="selected_monitor", environ=env)
    record = SessionRecord(plugin, storage_root=home)
    record.append({"old_session": True})
    assert record.finish("done", timeout=1)
    # Replace only this empty, owned package fixture; user data must stay intact.
    plugin.rmdir()
    plugin.mkdir()
    assert record_data_home(plugin, capture_source="selected_monitor", environ=env) == home
    assert (record.directory / "trigger_log.jsonl").read_text(encoding="utf-8").strip() == '{"old_session": true}'
    metadata = json.loads((record.directory / "session.json").read_text(encoding="utf-8"))
    assert metadata["storage_kind"] == "persistent_user_data"


def test_independent_installs_do_not_share_or_overwrite_sessions(tmp_path):
    local = tmp_path / "local_appdata"
    local.mkdir()
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
    homes = [record_data_home(root, capture_source="selected_monitor", environ={"LOCALAPPDATA": str(local)}) for root in roots]
    assert homes[0] != homes[1]


@pytest.mark.parametrize("source", ["injected_test", "synthetic_replay"])
def test_software_tests_never_use_real_application_data(tmp_path, source):
    assert record_data_home(tmp_path, capture_source=source, environ={"LOCALAPPDATA": "invalid"}) == tmp_path
    assert list(tmp_path.iterdir()) == []


def test_guide_path_resolution_is_readonly(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    home = record_data_home(tmp_path, capture_source="selected_monitor", environ={"LOCALAPPDATA": str(local)}, create=False)
    assert not home.exists() and list(local.iterdir()) == []


@pytest.mark.parametrize("value", [None, "", "relative", 42])
def test_no_silent_fallback_to_replaceable_package_on_bad_environment(tmp_path, value):
    with pytest.raises(ValueError):
        record_data_home(tmp_path, capture_source="selected_monitor", environ={"LOCALAPPDATA": value})
    assert list(tmp_path.iterdir()) == []


def test_unknown_source_does_not_create_data(tmp_path):
    with pytest.raises(ValueError):
        record_data_home(tmp_path, capture_source="unknown")
    assert list(tmp_path.iterdir()) == []
