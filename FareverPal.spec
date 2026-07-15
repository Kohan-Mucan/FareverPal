# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for FareverPal — the single source of build truth.

Committed so the exact bundle contents are version controlled and don't drift
between machines. Build with:

    pyinstaller --noconfirm FareverPal.spec

Key properties:
  - one-file, windowed, branded with assets/app_icon.ico.
  - In-repo assets are always bundled. Optional sibling game-data dirs
    (../data/sheets, ../htdocs/assets/...) are added ONLY if present, so a full
    local build ships the data while a bare checkout (e.g. CI) still builds a
    verification artifact.
  - Unused Qt modules are excluded to trim the bundle; only QtCore/QtGui/
    QtWidgets/QtSvg (+ the Windows platform plugin via PySide6's hook) are kept.
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
    ("assets/icons_ui", "assets/icons_ui"),
    ("assets/map", "assets/map"),
    ("assets/app_icon.png", "assets"),
    ("assets/app_icon.ico", "assets"),
]

# Core game database (items, units, loot) is baked into raw_data.py
# by compiler.py to keep the EXE small and fast.
raw_data_path = os.path.join(SPECPATH, "farever_companion", "data", "raw_data.py")
has_raw_data = os.path.exists(raw_data_path) and os.path.getsize(raw_data_path) > 1000

# Only bundle essential location/scan data from assets/data.
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

# --- Atlas vs Individual Icons ---------------------------------------------
# If the Atlas system is present, we bundle it and SKIP the 1800+ individual
# icons to keep the EXE small (~50MB vs ~100MB).
atlas_src = os.path.join(SPECPATH, "assets", "atlas")
using_atlas = os.path.exists(atlas_src) and os.path.exists(os.path.join(atlas_src, "atlas_map.json"))

if using_atlas:
    datas.append(("assets/atlas", "assets/atlas"))
else:
    datas.append(("assets/icons", "assets/icons"))
    datas.append(("assets/map_icons", "assets/map_icons"))

# --- data: optional sibling/local game-data dirs ---------------------------
# Added only if they exist and aren't already covered by the repo assets.
# We skip these in production builds if raw_data.py is in place.
optional_siblings = []

if not has_raw_data:
    optional_siblings.extend([
        (os.path.join("..", "htdocs", "data", "sheets"), "data/sheets"),
        (os.path.join("..", "htdocs", "assets", "data"), "assets/data"),
    ])

if not using_atlas:
    optional_siblings.append((os.path.join("..", "htdocs", "assets", "icons"), "assets/icons"))

for _rel, dst in optional_siblings:
    src = os.path.normpath(os.path.join(SPECPATH, _rel))
    if os.path.exists(src):
        # Avoid bundling large htdocs versions if we have local ones
        local_src = os.path.join(SPECPATH, dst)
        if not os.path.exists(local_src):
            datas.append((src, dst))
        elif dst == "data/sheets":
            # Always bundle sheets if found in htdocs, as they might be newer
            datas.append((src, dst))

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
]


a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["farever_native"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

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
