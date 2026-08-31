"""PyInstaller / direct entry point."""
import os
import sys


def _profile_raw_data() -> None:
    """Time each raw_* payload load at startup and log the durations.

    Opt-in: only runs when FAREVER_PROFILE=1 is set, so normal startups stay
    lazy — each consumer imports only the raw_X module it actually uses.
    Every `raw_X.py` shim now embeds its payload (zlib+base85-compressed
    JSON), so the per-module import time IS the per-file load cost (one
    decompress + json.load). Size is the embedded compressed payload, the
    exact bytes that ship in a frozen build.

    A module that fails to load logs as FAILED and is left for the app's own
    `try: from . import raw_X except ImportError` fallbacks to handle.

    The frozen build is windowed (console=False), so prints are invisible
    there. When FAREVER_PROFILE_LOG points at a writable path, the same lines
    are mirrored to that file — handy for timing a packaged exe.
    """
    import importlib
    import time

    names = ("raw_codex", "raw_units", "raw_items", "raw_skills",
             "raw_craft", "raw_item_drops", "raw_locs", "raw_data")
    total_ms = 0.0
    total_kb = 0.0
    lines: list[str] = []
    for name in names:
        t0 = time.perf_counter()
        try:
            mod = importlib.import_module(f"farever_companion.data.{name}")
        except Exception as exc:
            lines.append(f"[startup] {name}: FAILED ({exc})")
            continue
        ms = (time.perf_counter() - t0) * 1000.0
        total_ms += ms
        kb = mod._payload_size_kb() if hasattr(mod, "_payload_size_kb") else 0.0
        total_kb += kb
        lines.append(f"[startup] {name}: {ms:7.1f} ms ({kb:6.1f} KB embedded)")
    lines.append(f"[startup] total raw data: {total_ms:7.1f} ms ({total_kb:6.1f} KB)")
    for line in lines:
        print(line)
    log_path = os.environ.get("FAREVER_PROFILE_LOG")
    if log_path:
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass


def _preload_qt_platform_plugin() -> None:
    """Frozen-build hardening: preload the Qt windows platform plugin chain
    before QApplication is created.

    In the one-file exe, Qt loads qwindows.dll from the temp extraction when
    QApplication is created, and that load can transiently fail (antivirus
    scanning the just-extracted DLLs, dependency resolution through PATH).
    Qt 6.11 then shows a cryptic native error box - "This application failed
    to start because no Qt platform plugin could be initialized" - instead
    of the app. Preloading the chain ourselves, in dependency order, makes
    the load deterministic: by the time QApplication is created the DLLs are
    already mapped into the process and Qt reuses them.

    No-op in dev (site-packages resolves plugins normally) and on non-Windows.
    Failures are logged and ignored - worst case the app falls back to Qt's
    own (rare) error box.
    """
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("win"):
        return
    mei = getattr(sys, "_MEIPASS", None)
    if not mei:
        return
    from pathlib import Path
    import time

    pyside = Path(mei) / "PySide6"
    chain = [pyside / "Qt6Core.dll",
             pyside / "Qt6Gui.dll",
             pyside / "plugins" / "platforms" / "qwindows.dll"]
    if not all(p.exists() for p in chain):
        return
    # Also pin Qt to the bundled plugin dir, so platform / image / style
    # plugins resolve even if the DLL search path is ever off.
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                          str(pyside / "plugins"))
    import ctypes
    for attempt in range(3):
        try:
            for p in chain:
                ctypes.WinDLL(str(p))
            return
        except OSError:
            if attempt < 2:
                time.sleep(1.0 + attempt)   # let AV / extraction settle
    print("[startup] warning: Qt platform plugin preload failed; "
          "falling back to Qt's own loader", file=sys.stderr)


try:
    if os.environ.get("FAREVER_PROFILE") == "1":
        _profile_raw_data()

    _preload_qt_platform_plugin()

    from farever_companion import paths
    icons_dir = paths.icons_dir()
    atlas_dir = paths.atlas_dir()
    
    has_icons = icons_dir.exists() and (icons_dir / "Items").exists() and (icons_dir / "Units").exists() and (icons_dir / "Skills").exists()
    has_atlas = atlas_dir.exists() and (any(atlas_dir.glob("*.webp")) or any(atlas_dir.glob("*.json")))
    
    if not (has_icons or has_atlas):
        msg = (
            f"ERROR: missing atlas/icon folder\n\n"
            f"Icons folder: {icons_dir} ({'Found' if has_icons else 'Missing/Incomplete Items, Units, or Skills subfolders'})\n"
            f"Atlas folder: {atlas_dir} ({'Found' if has_atlas else 'Missing atlas *.webp or *.json files'})"
        )
        raise RuntimeError(msg)

    from farever_companion.app import main
except Exception as e:
    import traceback
    tb = traceback.format_exc()
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        QtWidgets.QMessageBox.critical(
            None,
            "Startup Error",
            f"An error occurred during startup:\n\n{e}\n\nTraceback:\n{tb}"
        )
    except Exception:
        traceback.print_exc()
    sys.exit(1)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Anything that slips past the import-time try/except above (e.g. an
        # exception while building the UI) would otherwise surface as the
        # bootloader's raw error box - show a clear dialog instead.
        import traceback
        tb = traceback.format_exc()
        try:
            from PySide6 import QtWidgets
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            QtWidgets.QMessageBox.critical(
                None, "Startup Error",
                f"Farever Pal hit an error during startup:\n\n{e}\n\n{tb}")
        except Exception:
            traceback.print_exc()
        sys.exit(1)
