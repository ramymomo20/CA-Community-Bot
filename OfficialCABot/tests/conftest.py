"""
Shared test infrastructure.

ios_bot is a package whose __init__.py has real side effects on import (it
builds the live Discord bot client and reads required env vars), and
ios_bot/signup_manager.py makes a multiprocessing.Manager() call at module
level that isn't safe to trigger from a plain test process on Windows. That
makes `import ios_bot.whatever` unusable in a test run.

load_module_from_file() sidesteps this by loading a single target file
directly via its path, without ever importing the ios_bot package __init__
chain. This only works for modules with no relative imports back into
ios_bot's own package init (checked per-module below) -- which happens to
cover the pure-logic modules most worth unit testing anyway (cache, crypto,
the sync layer's data-transform helpers).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_load_counter = 0


def load_module_from_file(relative_path: str):
    """Load `relative_path` (relative to the repo root) as a fresh, uniquely
    named module every call -- so tests never share module-level state
    (e.g. credential_crypto's cached Fernet instance) with each other."""
    global _load_counter
    _load_counter += 1
    path = REPO_ROOT / relative_path
    module_name = f"_test_loaded_{path.stem}_{_load_counter}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
