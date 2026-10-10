"""Freeze from outside the legacy plugin directory so root ``src`` wins.

PyInstaller prepends its entry script's directory ahead of explicit pathex.
Using dghub_plugin/main.py directly can therefore freeze dghub_plugin/src even
though offline_core selects the shared root correctly at runtime.
"""
from dghub_plugin.main import main


if __name__ == "__main__":
    raise SystemExit(main())
