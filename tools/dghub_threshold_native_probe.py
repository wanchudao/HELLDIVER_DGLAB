"""Owned native host inspection/import/runtime probe; never installed settings."""
import importlib.util
import dis
import io
import json
import os
from pathlib import Path
import sys
from types import CodeType


ROOT = Path("E:/HELLDIVER_DGLAB/build/dghub_threshold_native_host_20261010").resolve()


def main():
    if not getattr(sys, "frozen", False) or Path(sys.executable).parent.resolve() != ROOT:
        raise RuntimeError("Only isolated native runner")
    rows = []
    detail = io.StringIO()
    for name in ("src.api.app", "src.api.bootstrap", "src.api.state", "src.api.plugin_ws", "src.adapters.manager", "src.adapters.plugin_store", "src.adapters.remote"):
        code = importlib.util.find_spec(name).loader.get_code(name)
        def shapes(value):
            return [dict(name=c.co_name, args=c.co_varnames[:c.co_argcount+c.co_kwonlyargcount], names=c.co_names,
                         children=shapes(c)) for c in value.co_consts if isinstance(c, CodeType)]
        rows.append(dict(module=name, names=code.co_names, functions=shapes(code)))
        def selected(value):
            for c in value.co_consts:
                if isinstance(c, CodeType):
                    if c.co_name in ("configure_remote", "discover_installed_plugins", "start_plugin", "stop_plugin", "_init_adapter_manager", "__init__") and name != "src.api.app":
                        print(name, c.co_name, file=detail)
                        dis.dis(c, file=detail)
                    selected(c)
        selected(code)
    (ROOT / "interfaces.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "selected_disassembly.txt").write_text(detail.getvalue(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
