"""Stable per-install user data, outside DGHub's replace-on-import directory."""
import hashlib
import os
from pathlib import Path

from dghub_plugin.sdk_config import PLUGIN_ID


def record_data_home(plugin_root, *, capture_source, environ=None, create=True):
    plugin_root = Path(plugin_root)
    if not plugin_root.is_absolute() or not plugin_root.is_dir():
        raise ValueError("Invalid plugin root for session storage")
    plugin_root = plugin_root.resolve()
    if capture_source in {"injected_test", "synthetic_replay"}:
        # Test modes never write the real user's application-data directory.
        return plugin_root
    if capture_source != "selected_monitor":
        raise ValueError("Invalid session capture source")
    data = os.environ if environ is None else environ
    value = data.get("LOCALAPPDATA")
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError("LOCALAPPDATA is unavailable; refusing replaceable session storage")
    local = Path(value).resolve()
    if not local.is_dir() or local == Path(local.anchor):
        raise ValueError("Invalid user application-data directory")
    identity = hashlib.sha256(str(plugin_root).casefold().encode("utf-8")).hexdigest()[:20]
    home = local / "HELLDIVER_DGLAB" / "DGHub" / f"{PLUGIN_ID}-{identity}"
    if not home.resolve().is_relative_to(local):
        raise ValueError("Session data must remain in this user's application data")
    if create:
        home.mkdir(parents=True, exist_ok=True)
        if not home.resolve().is_relative_to(local):
            raise ValueError("Session data directory resolves outside application data")
    return home
