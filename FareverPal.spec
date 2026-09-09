# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for FareverPal — the single source of build truth.

Committed so the exact bundle contents are version controlled and don't drift
between machines. Build with:

    pyinstaller --noconfirm FareverPal.spec

Key properties:
  - one-file, windowed, branded with assets/app_icon.ico.
  - In-repo assets are always bundled. A full
    local build ships the data while a bare checkout (e.g. CI) still builds a
    verification artifact.
  - Unused Qt modules are excluded AND the PySide6 binaries are whitelisted
    (see QT_KEEP below) so only what the app actually loads ships:
    Qt6Core/Gui/Widgets/Svg, the qwindows + qoffscreen platform plugins (the
    latter powers the headless FAREVER_OPEN_PAGE/FAREVER_SCREENSHOT smoke
    runs), the qwebp + qico image plugins (atlas + app icon),
    qmodernwindowsstyle, and the shiboken6/pyside6 runtimes. Validated
    against PySide6 6.x — re-run the loaded-modules probe (see QT_KEEP
    comment) after every PySide6/Qt upgrade.
"""
import os

from PyInstaller.utils.hooks import get_module_file_attribute

# --- the native memory reader (built by maturin into the env) --------------
# Since farever_native is a package containing __init__.py and farever_native.pyd,
# we need to collect the compiled .pyd binary under the package directory.
try:
    import farever_native
    import os
    pkg_dir = os.path.dirname(farever_native.__file__)
    pyd_path = os.path.join(pkg_dir, "farever_native.pyd")
    if os.path.exists(pyd_path):
        binaries = [(pyd_path, "farever_native")]
    else:
        _native = get_module_file_attribute("farever_native")
        binaries = [(_native, ".")] if _native else []
except Exception:
    binaries = []

# --- data: in-repo assets (always present) ---------------------------------
datas = [
    ("assets/fonts", "assets/fonts"),
    ("assets/icons", "assets/icons"),
    ("assets/map", "assets/map"),
    ("assets/app_icon.png", "assets"),
    ("assets/app_icon.ico", "assets"),
]

# Core game database (items, units, loot, locations) is embedded INSIDE the
# raw_*.py shims by compiler.py as zlib+base85-compressed JSON (smaller
# payloads, faster import than the old dict literals). The shims are picked
# up as modules by hiddenimports below.
raw_data_path = os.path.join(SPECPATH, "farever_companion", "data", "raw_data.py")
has_raw_data = os.path.exists(raw_data_path) and os.path.getsize(raw_data_path) > 1000

# Only bundle location/scan data if raw_data.py is missing (fallback mode).
# In the normal build every loc sheet is compiled into raw_data.py (poi_locs,
# mob_locs, chest_locs, critter_locs, orb_positions, gatherable_locs), so the
# loose JSONs are compile inputs only — the Items page reads poi_locs/mob_locs
# from the shim at runtime (data/items/sources.py). The fallback list below
# exists for raw_data-less builds, where sources.py falls back to the JSONs.
if not has_raw_data:
    data_src = os.path.join(SPECPATH, "assets", "data")
    essential_data = [
        "poi_locs.json", "chest_locs.json", "critter_locs.json",
        "orb_positions.json", "gatherable_locs.json", "_version.json",
        "mob_locs.json"
    ]
    for f in essential_data:
        src = os.path.join(data_src, f)
        if os.path.exists(src):
            datas.append((src, "assets/data"))

# --- data: active native combat bridge binaries ---------------------------
# The native build keeps outputs beside the C sources, then publishes only
# the active bridge set here for development and packaging. version.dll is
# retired: its source stays in GameFiles/Farever/hooks, but it is not built,
# copied, packaged, or offered by the app.
#
# farever_dps.exe is the only helper binary the app ever RUNS (Option 2's hot
# attach), and the only one shipped. A second copy also shipped as
# farever_uninject.exe: injector.c used to pick inject-vs-uninject from its own
# filename, so identically built bytes under that name were a DIFFERENT
# program, and handing it to the launch list made Option 2 uninject instead of
# inject. Both halves of that are now gone - the filename dispatch was removed
# from injector.c, where a mode needs an explicit --uninject argument, and the
# copy is neither published nor packaged. Removal runs through the crash-safe
# Python path (core.proc.safe_uninject_hook, which calls the DLL's SafeUnload
# export). test_dps_capture.py pins that the uninjector is neither launched nor
# shipped and that no name can select a mode. The dev hot-uninject scripts are
# unaffected: they run `farever_dps.exe --uninject` from the hooks source
# folder.
dps_bridge_src = os.path.join(SPECPATH, "dps_bridge")
if os.path.isdir(dps_bridge_src):
    for _bridge_f in ("dinput8.dll", "userenv.dll", "farever_dps.dll",
                      "farever_dps.exe"):
        _bridge_src = os.path.join(dps_bridge_src, _bridge_f)
        if os.path.isfile(_bridge_src):
            datas.append((_bridge_src, "dps_bridge"))

atlas_src = os.path.join(SPECPATH, "assets", "atlas")
using_atlas = os.path.exists(atlas_src) and any(f.startswith("atlas_") for f in os.listdir(atlas_src))

if using_atlas:
    for f in os.listdir(atlas_src):
        # Never pack atlas_map.json (unused debug map)
        if f == "atlas_map.json":
            continue
        # Skip atlas JSON coordinate maps when raw_data.py is active (already compiled)
        if has_raw_data and f.endswith(".json"):
            continue
        datas.append((os.path.join(atlas_src, f), "assets/atlas"))
else:
    for folder in ["icons", "map_icons"]:
        src_path = os.path.join(SPECPATH, "assets", folder)
        if os.path.exists(src_path):
            datas.append((f"assets/{folder}", f"assets/{folder}"))

# --- data: optional local game-data dirs -----------------------------------
# (Optional sibling htdocs fallback removed)

# --- PySide6 DLL whitelist (trim the bundle) -------------------------------
# PySide6's hook collects EVERY Qt DLL in the package (~17MB unused: QML/Quick,
# Pdf, OpenGL, Network, VirtualKeyboard, opengl32sw...). The app only loads
# QtCore/Gui/Widgets/Svg + the qwindows/qoffscreen platforms + qwebp/qico
# image plugins + the qmodernwindowsstyle style. Verified at runtime: launched
# the frozen exe and enumerated loaded modules — everything not in this
# whitelist never loads. Applied to a.binaries below, which is where PySide6's
# hook puts the DLLs.
#
# UPGRADE CHECK: the whitelist silently drops any PySide6 binary not listed
# here, so after every PySide6/Qt upgrade re-verify by launching the built exe
# and enumerating loaded modules (PowerShell):
#   $p = Get-Process FareverPal; $p.Modules | ? {$_.ModuleName -match '^Qt|opengl'} | % {$_.ModuleName}
# Only the modules in QT_KEEP (plus shiboken6/pyside6 runtimes) may load.
QT_KEEP = {
    "PySide6/Qt6Core.dll", "PySide6/Qt6Gui.dll", "PySide6/Qt6Widgets.dll",
    "PySide6/Qt6Svg.dll",
    "PySide6/QtCore.pyd", "PySide6/QtGui.pyd", "PySide6/QtWidgets.pyd",
    "PySide6/QtSvg.pyd",
    "PySide6/pyside6.abi3.dll",
    "PySide6/MSVCP140.dll", "PySide6/MSVCP140_1.dll", "PySide6/MSVCP140_2.dll",
    "PySide6/VCRUNTIME140.dll", "PySide6/VCRUNTIME140_1.dll",
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/plugins/platforms/qoffscreen.dll",
    "PySide6/plugins/imageformats/qwebp.dll",
    "PySide6/plugins/imageformats/qico.dll",
    "PySide6/plugins/styles/qmodernwindowsstyle.dll",
}

def _trim_qt_binaries(items):
    """Drop PySide6 DLLs not in QT_KEEP; leave everything else untouched."""
    out, dropped = [], 0
    for dest, src, kind in items:
        norm = dest.replace("\\", "/")
        if norm.startswith("PySide6/") and norm not in QT_KEEP:
            dropped += 1
            continue
        out.append((dest, src, kind))
    if dropped:
        print(f"[spec] trimmed {dropped} unused PySide6 binaries")
    return out

def _trim_qt_datas(items):
    """Drop the 96 Qt .qm translation files (app never installs a QTranslator)
    from datas; leave everything else untouched."""
    out, dropped = [], 0
    for dest, src, kind in items:
        norm = dest.replace("\\", "/")
        if norm.startswith("PySide6/translations/") and norm.endswith(".qm"):
            dropped += 1
            continue
        out.append((dest, src, kind))
    if dropped:
        print(f"[spec] trimmed {dropped} unused Qt translation files")
    return out

# --- excludes: Qt modules the app never uses (trim the bundle) -------------
# Keep only QtCore, QtGui, QtWidgets, QtSvg and the windows platform plugin
# (provided by PySide6's PyInstaller hook). We do NOT use --collect-all, so the
# hook + these excludes are what determine the Qt footprint.
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngine",
    "PySide6.QtWebChannel",
    "PySide6.QtWebView",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSql",
    "PySide6.QtWebSockets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtQuickWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtLocation",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtRemoteObjects",
    "PySide6.QtSensors",
    "PySide6.QtTextToSpeech",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtStateMachine",
    "PySide6.QtScxml",
    "PySide6.QtXml",
    "PySide6.QtPrintSupport",
    "PySide6.QtDBus",
    "PySide6.QtTest",
    "PySide6.QtConcurrent",
    "pymem",
    "pillow",
    "PIL",
    "tkinter",
    "unittest",
    "pydoc",
    "lib2to3",
    "test",
    "msilib",
    "antigravity",
    "pytest",
    "pytest_qt",
    "maturin",
    "pyinstaller",
    "pefile",
    "pygments",
    "setuptools",
    "pip",
    "pkg_resources",
    "distutils",
]


a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "farever_native",
        "farever_companion.data.raw_data",
        "farever_companion.data.raw_codex",
        "farever_companion.data.raw_units",
        "farever_companion.data.raw_items",
        "farever_companion.data.raw_skills",
        "farever_companion.data.raw_craft",
        "farever_companion.data.raw_item_drops",
        "farever_companion.data.raw_locs",
        # HUD overlays are imported BY RUNTIME STRING in ui/overlay_manager.py
        # (_overlay_class -> import_module(f".overlays.{mod_name}")), so
        # PyInstaller's static analysis never sees them - ui/overlays/__init__.py
        # is empty, so there is no static edge to follow either. Without these
        # the frozen exe raises ModuleNotFoundError the moment
        # open_startup_overlays tries to restore the user's remembered
        # overlays: the toggles stay checked (settings persist) but the windows
        # never open.
        #
        # Keep these in step with overlay_manager._OVERLAY_SOURCES, in both
        # directions. The DPS entry is the PACKAGE `overlays.dps`, not a module
        # inside it: _OVERLAY_SOURCES["dps"] is ("dps", "DpsOverlay") and
        # DpsOverlay is re-exported from ui/overlays/dps/__init__.py. Listing a
        # non-existent `dps_overlay` module fails silently in a release - the
        # request() safety net logs "Overlay 'dps' failed to build" and reverts
        # the toggle, so only a user who had the DPS HUD on at launch ever sees
        # it. test_every_hiddenimport_resolves_and_covers_the_overlay_registry
        # in tests/test_repo_hygiene.py is what keeps the list honest.
        "farever_companion.ui.overlays.entity_overlay",
        "farever_companion.ui.overlays.minimap",
        "farever_companion.ui.overlays.speedrun_overlay",
        "farever_companion.ui.overlays.dungeon_overlay",
        "farever_companion.ui.overlays.dps",
        "farever_companion.ui.overlays.dummy_overlay",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# Trim the ~17MB of unused PySide6 DLLs (verified at runtime: nothing outside
# QT_KEEP ever loads) and the 96 Qt translation files. Applied here, after
# Analysis, where the hook's binaries/datas live; shiboken6 and everything
# non-PySide6 pass through untouched.
a.binaries = _trim_qt_binaries(a.binaries)
a.datas = _trim_qt_datas(a.datas)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FareverPal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app_icon.ico",
)
