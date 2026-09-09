"""The sidebar registry: which pages exist, and which the user may see.

Split out of `control_panel.py` so the assembly point stays under the ui line
budget. This is pure data plus two predicates, and it is the module both
Settings → Pages and the live nav-visibility refresh read, so it owns the
question "does this page exist for this user?" rather than the window.

It also owns where the DEV-ONLY pages live and whether they are present:
`dev_page_path` / `load_dev_page` resolve a page kept outside the package (in
`build_tools/dev/`), which is what makes "does this page exist" answerable for a
tool that is absent in every frozen build.

`control_panel` re-exports these names, so `from ...control_panel import NAV`
keeps working.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import Settings

# nav: key, icon, label
NAV = [
    ("overlays", "layers", "Overlays"),
    ("settings", "settings", "Settings"),
    ("codex", "layout", "Codex"),
    ("combat", "swords", "DPS Analysis"),
    ("pb", "trophy", "PB History"),
    ("party", "users", "Player Inspect"),
    ("gear", "box", "Gear"),
    ("craft", "settings", "Craft"),
    ("server", "broadcast", "Server Ping"),
    ("log", "terminal", "Log"),
    # dev-only icon-preview tool, loaded from build_tools/dev/; never in a
    # frozen build (see dev_page_path / load_dev_page below)
    ("devicons", "eye", "Dev Icons"),
]


def filtered_nav(settings: Settings) -> list:
    """Nav entries the user may see: this drops the pages the user disabled
    (Settings → Pages) so they never appear or build — plus keys that are
    deliberately stack-only (log, pb)."""
    disabled = set(getattr(settings, "disabled_pages", []) or [])
    return [(k, i, l) for k, i, l in NAV
            if k not in disabled and k not in ("log", "pb")]


# --- dev-only pages ---------------------------------------------------------
# Pages that are NOT part of the shipped app: they live in build_tools/dev/ and
# are loaded from their FILE PATH, so a frozen build cannot reach them (the
# folder is not packaged, so the path resolves to nothing there) and the shipped
# package never imports them. Keyed by page key -> file name.
_DEV_PAGES = {"devicons": "devicons.py"}


def dev_page_path(name: str) -> Path | None:
    """Path of a dev-only page module, or None when it is not on this machine."""
    rel = _DEV_PAGES.get(name)
    if not rel:
        return None
    root = Path(__file__).resolve().parents[2]      # repo root, above the package
    path = root / "build_tools" / "dev" / rel
    return path if path.is_file() else None


def load_dev_page(name: str):
    """Import a dev-only page module by path and return it.

    By path, not by package import: the module lives outside `farever_companion`
    (see build_tools/dev/__init__.py) and imports the package absolutely, so the
    repo root has to be importable — true for every dev run, and irrelevant for
    a frozen build, where the file does not exist and dev_page_path returns None.
    """
    path = dev_page_path(name)
    if path is None:
        raise ImportError(f"dev page '{name}' is not present")
    mod_name = f"farever_dev_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    import importlib.util
    root = str(path.parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)        # the module's `farever_companion.*` imports
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"dev page '{name}' could not be loaded")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def dev_icons_enabled() -> bool:
    """Dev-only icon-preview page gate: present in a dev checkout (the tool file
    is there), never in a frozen build."""
    if getattr(sys, "frozen", False):
        return False
    return dev_page_path("devicons") is not None


__all__ = ["NAV", "filtered_nav", "dev_icons_enabled",
           "dev_page_path", "load_dev_page"]
