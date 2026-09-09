"""UI structural guards: utility helpers, mixin/inheritance audit, and
open_overlay persistence hygiene."""
import ast
import os
import pathlib
import sys
from collections.abc import Callable
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import codex, icons, raw_data  # noqa: E402
from farever_companion.data.items import sources  # noqa: E402
from farever_companion.ui.page_history import PageHistory  # noqa: E402
from farever_companion.ui.pages.codex.card import CodexUnitCard  # noqa: E402
from tests import affected  # noqa: E402
from tests.qt_helpers import destroy, destroy_panel  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from build_tools import launcher_check  # noqa: E402


def _path_exists(fn) -> bool:
    try:
        return fn().exists()
    except Exception:
        return False


# data-dependent groups keep their own skip conditions so one missing
# bundle never skips the others
_NEEDS_DROPS_DATA = pytest.mark.skipif(
    not _path_exists(paths.item_drops_path),
    reason="requires bundled data (assets/data/item_drops.json)")
_NEEDS_ATLAS = pytest.mark.skipif(
    not _path_exists(paths.atlas_dir),
    reason="requires bundled atlas sprites (assets/atlas)")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (QPixmap + widgets
    need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- loc shim: unknown locations resolve from the compiled shim -----------
@_NEEDS_DROPS_DATA
def test_loc_shim_carries_mob_locs_and_poi_locs():
    """raw_data.py must be freshly compiled: poi_locs was always there,
    mob_locs is the compiled world-spawn index the Items page reads."""
    assert raw_data.DATA.get("poi_locs"), \
        "raw_data.py is stale — re-run compiler.py (missing poi_locs)"
    mobs = raw_data.DATA.get("mob_locs")
    assert mobs, \
        "raw_data.py is stale — re-run compiler.py (missing mob_locs)"
    assert len(mobs) > 1000
    # rows are pruned to the fields the runtime reads
    assert {"unit", "units", "zone"} <= set(mobs[0])


@_NEEDS_DROPS_DATA
def test_unknown_location_resolution_works_without_loose_json(monkeypatch):
    """With the JSON paths dead (frozen build), soulstone + world-mob rows
    still resolve from the compiled shim."""

    def _boom(*_a, **_k):
        raise OSError("no loose JSON in the frozen build")

    monkeypatch.setattr(paths, "poi_locs_path", _boom)
    monkeypatch.setattr(paths, "mob_locs_path", _boom)
    for fn in (sources._poi_rows, sources._mob_loc_rows,
               sources._soulstone_pois, sources._mob_spawn_zones,
               sources._mob_spawn_zones_normalized,
               sources._resolve_unknown_loc):
        fn.cache_clear()

    # soulstone demon bosses -> their summon-spot zone (poi_locs from shim)
    rows = sources.resolve_drops("DemonGearUpgradeRare_CritToAP")
    by_src = {r["source"]: r["loc"] for r in rows}
    assert by_src["Ariana Grandemon"] == "West Majoram Bridge"
    assert by_src["Baphometal"] == "Isle of Flowers"
    # world-mob variant -> its spawn zones (mob_locs from shim)
    merged = {r["source"]: r for r in sources.merge_drops("Cloth_Z1")}
    assert merged["Kobold Overseer"]["locs"] == ["Gorgon's Hollow"]


# --- page history: back/forward semantics ---------------------------------
def _make() -> tuple[PageHistory, list[str], Callable[[str], None]]:
    """History wired to a fake page switcher that records what was opened."""
    h: PageHistory
    visited: list[str] = []

    def select(key: str) -> None:
        current = visited[-1] if visited else None
        visited.append(key)
        h.record(current, key)

    h = PageHistory(select)
    return h, visited, select


def test_fresh_navigation_builds_back_stack():
    h, visited, nav = _make()
    nav("overlays")
    nav("entity")
    nav("codex")
    assert visited == ["overlays", "entity", "codex"]
    assert h._back == ["overlays", "entity"]


def test_same_page_navigation_is_not_recorded():
    h, visited, nav = _make()
    nav("a")
    nav("a")             # same page: not pushed onto back history
    h.back("a")          # nothing to go back to
    assert visited == ["a", "a"]


def test_back_then_forward_round_trips():
    h, visited, nav = _make()
    nav("a")
    nav("b")
    nav("c")

    h.back("c")
    assert visited[-1] == "b"
    h.back("b")
    assert visited[-1] == "a"
    h.forward("a")
    assert visited[-1] == "b"
    h.forward("b")
    assert visited[-1] == "c"
    h.forward("c")       # nothing to go forward to
    assert visited[-1] == "c"


def test_new_navigation_clears_forward_history():
    h, visited, nav = _make()
    nav("a")
    nav("b")
    h.back("b")
    nav("c")             # fresh jump invalidates the forward stack
    h.forward("c")
    assert visited[-1] == "c"


def test_back_and_forward_do_not_clear_each_other():
    h, visited, nav = _make()
    nav("a")
    nav("b")
    h.back("b")          # forward: [b]
    assert visited[-1] == "a"
    h.forward("a")       # back: [a], forward: []
    assert visited[-1] == "b"
    h.back("b")          # forward: [b] again
    assert visited[-1] == "a"


# --- QSS scan: stylesheet brace balance (pure AST, no Qt) -----------------
ROOT = pathlib.Path(__file__).resolve().parent.parent / "farever_companion"


def _const_parts(node):
    """Constant string parts of a setStyleSheet argument: plain strings and
    f-string literal segments. Returns None when the arg is a runtime
    expression (variable / non-string), which this scan can't inspect."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        consts, has_placeholder = [], False
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                consts.append(v.value)
            else:
                has_placeholder = True
        # placeholders hold colors/numbers — their runtime values never
        # contain braces, so the literal parts must balance on their own
        return consts
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _const_parts(node.left), _const_parts(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _set_style_sheet_args():
    """(path, lineno, const_parts) for every setStyleSheet call whose arg is
    a string-literal concatenation."""
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        if "tmp" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr == "setStyleSheet"):
                continue
            parts = _const_parts(node.args[0])
            if parts is not None:
                out.append((path, node.lineno, parts))
    return out


def test_all_set_style_sheet_strings_are_brace_balanced():
    """Every literal-argument setStyleSheet in the app has balanced braces
    in its constant parts — the regression that spammed 'Could not parse
    stylesheet' for every CRAFTED tag in the In Recipes drawer."""
    calls = _set_style_sheet_args()
    assert calls, "no setStyleSheet calls found — scan is broken?"
    for path, lineno, parts in calls:
        joined = "".join(parts)
        assert joined.count("{") == joined.count("}"), \
            f"{path}:{lineno}: unbalanced stylesheet braces: {joined[:130]!r}"


def test_no_plain_string_doubled_braces():
    """A literal `{{`/`}}` surviving into the constant parts must have come
    from a plain (non-f-string) segment — f-strings already collapsed
    theirs — and doubled braces are never valid QSS (the CRAFTED tag bug
    was exactly this: a plain segment ending in `;}}`)."""
    for path, lineno, parts in _set_style_sheet_args():
        for seg in parts:
            for pair in ("{{", "}}"):
                assert pair not in seg, \
                    f"{path}:{lineno}: plain-string {pair!r} in stylesheet " \
                    f"segment: {seg[:100]!r}"


# --- icon cropping: trim-to-content on real atlas sprites ----------------
def _opaque_area(pm) -> int:
    """Number of pixels with alpha > 8 in a pixmap (its visible content)."""
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    n = 0
    for y in range(h):
        row = memoryview(img.constScanLine(y)).cast("B")
        for x in range(w):
            if row[4 * x + 3] > 8:
                n += 1
    return n


def _raw_bytes(pm) -> bytes:
    return bytes(pm.toImage().constBits())


@_NEEDS_ATLAS
def test_small_boss_sprite_fills_more_after_trim():
    """SpongeBlob's sprite occupies a tiny corner of its 64x64 atlas cell; the
    trimmed version must fill a substantially larger share of the frame."""
    plain = icons.pixmap("Units", "SpongeBlob", 64)
    cropped = icons.pixmap_cropped("Units", "SpongeBlob", 64)
    assert _opaque_area(plain) > 0
    assert _opaque_area(cropped) > _opaque_area(plain) * 2


@_NEEDS_ATLAS
def test_full_fill_sprite_is_unchanged():
    """Nepsilon fills its whole cell — trimming must be a no-op."""
    plain = icons.pixmap("Units", "Nepsilon", 64)
    cropped = icons.pixmap_cropped("Units", "Nepsilon", 64)
    assert _opaque_area(cropped) == _opaque_area(plain)


@_NEEDS_ATLAS
def test_trim_stays_within_frame():
    """Cropped content is scaled to fit (aspect preserved) and centred — never
    larger than the frame and never placed outside it."""
    pm = icons.pixmap_cropped("Units", "SpongeBlob", 64)
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        row = memoryview(img.constScanLine(y)).cast("B")
        for x in range(w):
            if row[4 * x + 3] > 8:
                minx = min(minx, x)
                maxx = max(maxx, x)
                miny = min(miny, y)
                maxy = max(maxy, y)
    # content stays within the canvas bounds (with the frame itself)
    assert minx >= 0 and miny >= 0 and maxx < w and maxy < h
    # aspect ratio preserved: content bbox is at least as wide as tall and vice
    # versa after KeepAspectRatio scaling (within rounding)
    bw, bh = maxx - minx + 1, maxy - miny + 1
    src = icons._content_bbox("Units", "SpongeBlob")
    assert abs(bw / bh - src[2] / src[3]) < 0.06


@_NEEDS_ATLAS
def test_tile_trim_flag():
    """tile(trim=True) actually changes the render for the small sprite
    (SpongeBlob) and leaves a full-fill sprite (Nepsilon) byte-identical —
    the flag only touches sprites that float in empty space."""
    small_plain = icons.tile("Units", "SpongeBlob", 24, "#fbbf24", trim=False)
    small_trim = icons.tile("Units", "SpongeBlob", 24, "#fbbf24", trim=True)
    assert _raw_bytes(small_trim) != _raw_bytes(small_plain)

    full_plain = icons.tile("Units", "Nepsilon", 24, "#fbbf24", trim=False)
    full_trim = icons.tile("Units", "Nepsilon", 24, "#fbbf24", trim=True)
    assert _raw_bytes(full_trim) == _raw_bytes(full_plain)


# --- launcher check: console-script shim classification -------------------
def test_stale_shim_signature_is_broken():
    """Silent nonzero exit is the stale-shim signature."""
    status, detail = launcher_check.classify("pytest", 1, "", False)
    assert status == "broken"
    assert "silent" in detail


def test_clean_exit_is_ok():
    status, _ = launcher_check.classify("pytest", 0, "pytest 9.0.3", False)
    assert status == "ok"


def test_clean_exit_without_output_is_ok():
    status, detail = launcher_check.classify("pyi-tool", 0, "", False)
    assert status == "ok"
    assert detail == "clean exit"


def test_nonzero_with_output_is_warn_not_broken():
    """Tools that reject --help (e.g. lrelease, qmlimportscanner) print an
    error and exit nonzero — the shim ran the real tool, so this is a warn,
    not a launcher failure."""
    status, detail = launcher_check.classify(
        "pyside6-lrelease", 1, 'lrelease: Invalid argument: "--help"', False
    )
    assert status == "warn"
    assert "unsupported" in detail


def test_traceback_is_warn_not_broken():
    """A traceback means the shim launched python and ran the entry point;
    the breakage (if any) is upstream, not the launcher."""
    status, _ = launcher_check.classify(
        "crashtest", 1, "Traceback (most recent call last):\n  ...", False
    )
    assert status == "warn"


def test_still_running_is_ok():
    """GUI tools stay alive in their event loop — launched == launcher works."""
    status, detail = launcher_check.classify("pyside6-designer", None, "", True)
    assert status == "ok"
    assert "still running" in detail


def test_probe_args_version_tools():
    assert launcher_check.probe_args("pytest") == ["--version"]
    assert launcher_check.probe_args("pip3.14") == ["--version"]
    assert launcher_check.probe_args("pygmentize") == ["-V"]


def test_probe_args_default_help():
    assert launcher_check.probe_args("pyi-makespec") == ["--help"]


def test_probe_args_native_qt_tools():
    """GUI-subsystem Qt tools are probed with plain --help; the offscreen
    platform is provided via the environment (see verify_assets.py), not the
    command line, so tools with strict arg parsing aren't tripped up."""
    assert launcher_check.probe_args("pyside6-rcc") == ["--help"]
    assert launcher_check.probe_args("pyside6-qmlimportscanner") == ["--help"]


def test_pyside6_launchers_are_never_probed():
    """All pyside6-* tools open windows / modal help dialogs when launched,
    so they must be static-only — never executed by the check."""
    assert launcher_check.is_static_only("pyside6-designer")
    assert launcher_check.is_static_only("pyside6-rcc")
    assert launcher_check.is_static_only("pyside6-uic")
    assert launcher_check.is_static_only("pyside6-qmlcachegen")
    assert not launcher_check.is_static_only("pytest")
    assert not launcher_check.is_static_only("pyinstaller")
    assert not launcher_check.is_static_only("pip3.14")


def test_wrapped_target_resolution(tmp_path):
    scripts = tmp_path / "Scripts"
    pyside = tmp_path / "Lib" / "site-packages" / "PySide6"
    assert launcher_check.wrapped_target("pyside6-designer", scripts) == (
        pyside / "designer.exe"
    )
    assert launcher_check.wrapped_target("pyside6-rcc", scripts) == (
        pyside / "rcc.exe"
    )
    assert launcher_check.wrapped_target("pyside6-deploy", scripts) == (
        pyside / "scripts" / "deploy.py"
    )
    assert launcher_check.wrapped_target("pyside6-qtpy2cpp", scripts) == (
        pyside / "scripts" / "qtpy2cpp.py"
    )
    assert launcher_check.wrapped_target("pyside6-genpyi", scripts) == (
        pyside / "support" / "generate_pyi.py"
    )
    assert launcher_check.wrapped_target("pytest", scripts) is None
    assert launcher_check.wrapped_target("pyi-makespec", scripts) is None


# --- soulstone badge: demon-boss mounts render the gem --------------------
def _card(uid: str) -> CodexUnitCard:
    """The Collection card for `uid` (Z0 merges mounts/gliders) or the first
    matching row anywhere, so the badge test uses the data the UI renders."""
    for rid in ("Z0", "Mounts", "Gliders"):
        for it in codex.units_by_region(rid, ingame_order=True):
            if it["id"] == uid:
                return CodexUnitCard(uid, it, 0, 1)
    raise AssertionError(f"{uid} not found in codex regions")


# the Niflelian-family mounts/gliders drop from the 4 soulstone demon bosses
_SOULSTONE_IDS = ("Mount_Skunk_06", "Mount_Crab_Demonic",
                  "Glider_Bat_Demon", "Glider_FlyingFish_Demon")


def test_soulstone_drop_predicate_matches_demon_mounts():
    """is_soulstone_drop is True exactly for the soulstone-farmed items."""
    for uid in _SOULSTONE_IDS:
        assert codex.is_soulstone_drop(_card(uid).data), uid
    # a plain mob-drop mount (Ponogian Skunk drops from Skunk_Z1W_FS) and a
    # vendor mount are not soulstone drops
    assert not codex.is_soulstone_drop(_card("Mount_Skunk_05").data)
    assert not codex.is_soulstone_drop(_card("Mount_Goat_06").data)


def test_soulstone_cards_show_gem_instead_of_sword():
    """Soulstone-farmed mounts badge the magenta gem; the plain mob sword
    never doubles up in the same slot."""
    for uid in _SOULSTONE_IDS:
        card = _card(uid)
        assert not card.soulstone_ico.isHidden(), uid      # gem shown
        assert card.mob_ico.isHidden(), uid                # sword suppressed


def test_plain_mob_drop_card_keeps_sword_badge():
    """A regular mob-dropped mount keeps the sword badge and shows no gem."""
    card = _card("Mount_Skunk_01")   # Primevallean Skunk (plain mob drop)
    assert not card.mob_ico.isHidden()
    assert card.soulstone_ico.isHidden()


def test_source_badge_slot_gives_priority_to_the_achievement():
    """One source badge per slot: Mount_Skunk_05 is BOTH an achievement
    reward (Valley of the Eternal Dungeons) and a mob drop, so the slot shows
    the achievement and the sword stays off — the same rule the vendor case
    pins. It gained that reward after the plain-mob case above was written,
    which is why that case uses a drop with no achievement on it."""
    card = _card("Mount_Skunk_05")
    assert not card.achievement_ico.isHidden()
    assert card.mob_ico.isHidden()
    assert card.soulstone_ico.isHidden()


def test_vendor_mount_shows_neither_mob_badge():
    """A vendor-sold mount (Niflelian Goat) carries neither the sword nor the
    gem — its vendor badge takes the source slot."""
    card = _card("Mount_Goat_06")
    assert card.mob_ico.isHidden()
    assert card.soulstone_ico.isHidden()


def test_soulstone_tooltip_names_the_source():
    """The card tooltip surfaces the soulstone source alongside the drop
    count, mirroring the badge ('Dropped by 4 Mobs (Soulstones)')."""
    tip = _card("Mount_Skunk_06").toolTip()
    assert "Dropped by 4 Mobs (Soulstones)" in tip
    tip = _card("Mount_Skunk_05").toolTip()
    assert "Dropped by 4 Mobs" in tip and "Soulstones" not in tip


def test_card_tooltip_skips_visible_state_and_click_hints():
    """The card already shows its name, badges, and hidden state (eye-off
    icon + dimming), so the hover tooltip drops the Status line and the
    left/right-click hints — it keeps the raw id plus drop/achievement info."""
    tip = _card("Mount_Skunk_06").toolTip()
    assert tip.startswith("ID: Mount_Skunk_06"), tip
    assert "Status" not in tip, tip
    assert "Left-click" not in tip and "Right-click" not in tip, tip
    assert "Dropped by 4 Mobs (Soulstones)" in tip
    # a hidden card conveys its state visually — no Status line needed
    card = _card("Mount_Skunk_06")
    card.active = False
    card.set_active(False)
    tip = card.toolTip()
    assert "Status" not in tip, tip
    assert "Hidden" not in tip, tip


def test_cosmetic_font_warning_filter(capsys):
    """The startup message handler drops Qt's cosmetic
    'QFont::setPointSize: Point size <= 0' warning (the app QSS sets
    font-size in px, so every widget font has pointSize -1 and Qt's
    Windows font resolution can internally call setPointSize(-1)) and the
    benign 'QWindowsWindow::setGeometry: Unable to set geometry' clamp
    noise (a top-level window first shown smaller than its layout minimum;
    Windows clamps it and the window displays fine), and forwards every
    other message to stderr."""
    import farever_companion.app as m
    from PySide6 import QtCore

    prev = QtCore.qInstallMessageHandler(m._filter_cosmetic_qt_warnings)
    try:
        QtCore.qWarning(
            "QFont::setPointSize: Point size <= 0 (-1), "
            "must be greater than 0")
        QtCore.qWarning(
            "QFont::setPointSizeF: Point size <= 0 (-1), "
            "must be greater than 0")
        assert "setPointSize" not in capsys.readouterr().err
        QtCore.qWarning(
            "QWindowsWindow::setGeometry: Unable to set geometry "
            "200x64+960+456 on QWidgetWindow on \"AW2725DM\". "
            "Resulting geometry: 200x160+960+456")
        assert "setGeometry" not in capsys.readouterr().err
        # unrelated warnings still reach stderr
        QtCore.qWarning("QPainter::setRenderHint: Painter not active")
        assert "Painter not active" in capsys.readouterr().err
    finally:
        QtCore.qInstallMessageHandler(prev)


# ======================================================================
# test_ui_layering
# ======================================================================

import ast
import pathlib

import pytest

PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "farever_companion"
UI_DIR = PKG_DIR / "ui"

# 800 when control_panel.py was 799; the v0.3.3 craft/loot/codex commit
# grew it past that with real shell code (self-update installer, brand
# tinting, mouse-back nav). Raised to 1200 on 2026-10-06 (see the budget note in AGENTS.md): at
# 840 the cap had stopped being a god-object guard and become a split
# TARGET. Every file it pushed a fragment out of was left 650-840 lines,
# so those fragments (68-630 lines, ONE consumer each) existed only to
# hold the number down, and each paid for itself with delegates, mid-file
# imports and re-export shims pointing back at its own owner. 1200 clears
# the largest single-concern file the app has (the Run Timer window with
# its render + diagnostics halves, 1063) and is still ~40% under the
# 2045-line god-object it was split from. What must stay split still is:
# settings (5226) and the dummy/test family (1950) are separate concerns,
# not budget artifacts.
MAX_UI_LINES = 1200

# Dev-only tools that never ship, exempt from the budget the SHIPPED ui has to
# meet. EMPTY on purpose: the one entry this ever held (the icon/overlay preview
# gallery) now lives outside the package entirely, in build_tools/dev/, loaded by
# file path via nav_registry.load_dev_page - so no ui file needs an exemption for
# it, and a frozen build cannot reach it at all. Add a file here ONLY when it
# provably cannot load in a frozen build.
DEV_ONLY_FILES = ()

# Everything under ui/ except the dev-only tools above.
UI_FILES = [p for p in sorted(UI_DIR.rglob("*.py"))
            if p.name not in DEV_ONLY_FILES]

# The memory layer (matched on the dotted tail so relative and absolute imports
# both count). Pages drive self.model; they never touch these directly.
# game_attach.py owns the session lifecycle (it may import Proc) and is exempt.
FORBIDDEN_TAILS = (
    "core.proc", "core.hl", "core.scene", "core.attributes",
    "core.player",
)
PAGE_FILES = sorted((UI_DIR / "pages").glob("*.py")) + \
    sorted((UI_DIR / "pages" / "codex").glob("*.py")) + \
    sorted((UI_DIR / "pages" / "items").glob("*.py")) + \
    sorted((UI_DIR / "pages" / "craft").glob("*.py"))


def test_dev_pages_live_outside_the_shipped_package():
    """A dev-only page must not sit inside `farever_companion/` again.

    Inside the package it would need the line-budget exemption above, would be
    one `import` away from shipping, and would have to be git-ignored to stay
    local — the state this moved out of. The app finds it by path instead, and
    a frozen build finds nothing (which is what hides the nav item).
    """
    from farever_companion.ui.nav_registry import dev_page_path

    assert DEV_ONLY_FILES == ()
    assert not (UI_DIR / "pages" / "devicons.py").exists()
    path = dev_page_path("devicons")
    if path is not None:            # a checkout without build_tools/dev/ is fine
        assert path.parent.name == "dev" and path.parent.parent.name == "build_tools"
        assert path.is_file()
    assert dev_page_path("nonesuch") is None


@pytest.mark.parametrize("path", UI_FILES, ids=lambda p: p.name)
def test_ui_file_under_line_budget(path):
    n = len(path.read_text(encoding="utf-8").splitlines())
    assert n <= MAX_UI_LINES, (
        f"{path.relative_to(UI_DIR)} is {n} lines (budget {MAX_UI_LINES}). "
        "Move a coherent concern out (a page mixin under ui/pages/, or a "
        "standalone module beside it) rather than letting one file reabsorb it. "
        "Shared widget libraries and overlays split the same way - the budget "
        "measures one file, not one concern.")


def test_dev_only_exemptions_still_exist():
    """A renamed or deleted dev-only file must not leave a silent exemption
    behind - the budget would quietly stop covering whatever replaced it."""
    names = {p.name for p in UI_DIR.rglob("*.py")}
    for name in DEV_ONLY_FILES:
        assert name in names, (
            f"{name} is exempted from the UI line budget but no longer exists "
            "- update DEV_ONLY_FILES")


@pytest.mark.parametrize("path", PAGE_FILES, ids=lambda p: p.name)
def test_pages_do_not_import_memory_layer(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            targets.add(mod)
            targets.update(f"{mod}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            targets.update(a.name for a in node.names)
    offenders = [t for t in targets if any(t.endswith(tail) for tail in FORBIDDEN_TAILS)]
    assert not offenders, (
        f"{path.name} imports the memory layer {sorted(offenders)}; pages read "
        "from LiveModel (self.model) only.")


# ======================================================================
# small single-consumer UI modules
# ======================================================================

# A tiny module with one consumer is not a ban, it is a census.
#
# Measured 2026-10-06: 149 modules under `ui/`, and 95 of them are imported
# by exactly ONE other module. That shape is normal here - a page package
# composes its own facets (`settings/__init__.py` builds one settings mixin
# per file) and `control_panel.py` assembles the pages - so "more than one
# consumer" cannot be the rule. What is NOT normal is doing that while ALSO
# sitting far under the line budget: then the only thing that put the code in
# its own file was a mechanical split. `ui/pages/items/family_glyphs.py` (now
# folded away) was exactly that - 19 lines, one real consumer, filed under the
# package that did not use it, and its own docstring claimed a second consumer
# that existed only as a dead `# noqa: F401` re-export in the parent.
#
# So the modules below are the acknowledged small single-consumer ones, each
# with the reason it is a concern of its own. A NEW module that lands in this
# shape is red until it gains a second consumer, folds into the module that
# imports it, or records its reason here. The importer count comes from
# tests/affected.py (re-export aware), so this census and the narrowed test
# run share ONE definition of "who imports this".
# Deliberately NOT derived from MAX_UI_LINES: that is a god-object guard (a
# size CEILING for one file), this is a fragment test (a size FLOOR for a
# module nobody else needs) - deriving one from the other makes raising the
# ceiling silently widen the census, which is what happened when the cap
# went to 1200 and this became 171.
_FRAGMENT_LINE_CAP = 120

_SMALL_SINGLE_CONSUMER_OK = {
    "farever_companion/ui/pages/log.py":
        "one page mixin - control_panel composes one per NAV entry",
    "farever_companion/ui/pages/craft/tiles.py":
        "the craft recipe-list delegate, split from craft/detail.py at "
        "the budget",
}


@pytest.fixture(scope="module")
def small_single_consumer_ui_modules():
    """{ui module path: sorted consumers} for the modules under the fragment
    cap that at most one other module imports."""
    texts = affected._project_sources()
    importers = affected.module_importers(texts)
    found = {}
    for rel, text in texts.items():
        if not rel.startswith("farever_companion/ui/") or not rel.endswith(".py"):
            continue
        if len(text.splitlines()) >= _FRAGMENT_LINE_CAP:
            continue
        module = affected._module_name(rel)
        consumers = importers.get(module, set()) - {module}
        if len(consumers) <= 1:
            found[rel] = sorted(consumers)
    return found


def test_small_ui_modules_with_one_consumer_are_registered(
        small_single_consumer_ui_modules):
    """A small single-consumer module is an accidental split until it says
    otherwise: give it a second consumer, fold it into its only importer, or
    register why it is a concern of its own."""
    unregistered = sorted(p for p in small_single_consumer_ui_modules
                          if p not in _SMALL_SINGLE_CONSUMER_OK)
    assert not unregistered, (
        "these UI modules are under {} lines and at most one module imports "
        "them, so nothing but a mechanical split explains the file: {}. Move "
        "each into the file that imports it (or give it a second consumer), or "
        "add it to _SMALL_SINGLE_CONSUMER_OK with the reason it is a concern "
        "of its own.".format(_FRAGMENT_LINE_CAP, unregistered))


def test_registered_small_single_consumer_ui_modules_still_qualify(
        small_single_consumer_ui_modules):
    """Both directions: an entry with no reason is a silent exemption, and a
    stale entry must not outlive the module it excuses."""
    for rel, reason in sorted(_SMALL_SINGLE_CONSUMER_OK.items()):
        assert reason.strip(), f"{rel} is registered with no reason"
        assert rel in small_single_consumer_ui_modules, (
            f"{rel} no longer qualifies - it gained a consumer, moved, or grew "
            f"past {_FRAGMENT_LINE_CAP} lines - so drop its entry")


# ======================================================================
# test_open_overlays_hygiene
# ======================================================================

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farever_companion import config
from farever_companion.config import Settings


@pytest.fixture()
def moddata(tmp_path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    (tmp_path / "dps").mkdir(exist_ok=True)
    return tmp_path


def test_every_hud_overlay_declares_where_it_exists():
    """One placement row per HUD window, checked in both directions.

    A registered window with no row is a window whose placement nobody wrote —
    which is how the dummy HUD spent a release appearing inside dungeons — and
    a row with no window is a rule that can never run. The old loop kept this
    same knowledge as code: a tuple here, an `if key == "dps"` there, an
    `if key == "dummy"` after it.
    """
    from farever_companion.ui.overlay_manager import HUD_OVERLAYS
    from farever_companion.ui.overlay_rules import HUD_PLACES

    undeclared = [k for k in HUD_OVERLAYS if k not in HUD_PLACES]
    assert not undeclared, (
        f"HUD_OVERLAYS keys with no HUD_PLACES row: {undeclared}. Every window "
        f"must declare where it exists; one without a row silently inherits "
        f"whatever the loop happens to do for unknown keys.")
    ghost = [k for k in HUD_PLACES if k not in HUD_OVERLAYS]
    assert not ghost, (
        f"HUD_PLACES rows for keys that are not HUD_OVERLAYS: {ghost}. A row "
        f"nothing can ask about reads as coverage while gating nothing.")


def test_every_setting_a_place_row_names_actually_exists():
    """The other half of a row being real: its flags are read with
    `getattr(settings, name, default)`, so a typo'd name is not a crash — it is
    a gate that never fires and never says so. That is the same "quietly
    differs from its neighbours" failure the rows exist to prevent, one field
    deep, so the names are checked against their real homes: the boolean
    Settings fields, and the legacy `open_overlay_<key>` aliases.
    """
    from dataclasses import fields

    from farever_companion.config.model import Settings
    from farever_companion.config.store import _PROP_MAP
    from farever_companion.ui.overlay_rules import HUD_PLACES

    fields_named = {f.name for f in fields(Settings)}
    broken = []
    for key, place in HUD_PLACES.items():
        for name in (place.instances_only, place.dummy_auto):
            if name is not None and name not in fields_named:
                broken.append(f"{key}: {name!r} is not a Settings field")
        if place.card is not None and place.card not in _PROP_MAP:
            broken.append(f"{key}: {place.card!r} has no persisted alias")
    assert not broken, (
        f"HUD_PLACES rows naming settings that do not exist: {broken}. Each "
        f"would read its fallback forever — a row that looks declared while "
        f"gating nothing.")


def test_one_table_decides_where_each_hud_exists():
    """The whole placement spec, read the way the loop reads it.

    Every assertion below used to be a hand-written branch in
    `overlay_manager._combat_tick` (Rule D's tuple, Rule E's `if key == "dps"`,
    Rule F's `if key == "dummy"`) — three neighbours answering one question
    three ways. As rows they can be compared at a glance, and the answers that
    differ now say why.
    """
    from farever_companion.ui.overlay_rules import (hud_allowed_places,
                                                    hud_instance_only,
                                                    hud_place_hidden,
                                                    hud_rule_owns_showing)

    def hidden(key, s, *, inside=False, dummy=False):
        return hud_place_hidden(s, key, in_instance=inside, at_dummy=dummy)

    class _S:
        open_overlay_dungeon = True
        open_overlay_speedrun = True
        open_overlay_dummy = False
        dps_dummy_auto = True
        dps_instances_only = False

    s = _S()

    # the instance-only pair: nowhere but an instance, and the card must be on
    for key in ("dungeon", "speedrun"):
        assert hud_instance_only(key) is True
        assert hud_rule_owns_showing(key) is True      # the rule opens it, not a click
        assert hidden(key, s) is True
        assert hidden(key, s, dummy=True) is True
        assert hidden(key, s, inside=True) is False
    s.open_overlay_dungeon = False                     # card off: never resurrected
    assert hidden("dungeon", s) is True
    assert hidden("dungeon", s, inside=True) is True
    s.open_overlay_dungeon = True

    # entity/map exist everywhere but an instance (Rule C, now a row rather
    # than a `key in ("map", "entity")` test the tick wrote twice): a dungeon
    # hides them, and the user's own card click is what shows them
    for key in ("entity", "map"):
        assert hud_instance_only(key) is False
        assert hud_rule_owns_showing(key) is False
        assert hidden(key, s) is False
        assert hidden(key, s, dummy=True) is False
        assert hidden(key, s, inside=True) is True

    # the meter: everywhere while "Instances Only" is off
    assert hud_rule_owns_showing("dps") is False
    assert hidden("dps", s) is False
    assert hidden("dps", s, inside=True) is False
    assert hidden("dps", s, dummy=True) is False

    # ...narrowed to instances when it is on. No dummy logic: the bench is
    # the Test Dummy HUD's seat, so the meter stays hidden at a dummy whether
    # that board is up or not.
    s.dps_instances_only = True
    assert hidden("dps", s) is True                    # open world, no dummy
    assert hidden("dps", s, inside=True) is False      # a dungeon or a rift
    assert hidden("dps", s, dummy=True) is True        # the bench it keeps no longer...
    s.open_overlay_dummy = True                        # ...either way
    assert hidden("dps", s, dummy=True) is True
    s.open_overlay_dummy = False
    s.dps_instances_only = False

    # the dummy HUD: the bench and nowhere else, never an instance
    assert hidden("dummy", s, dummy=True) is True      # card off
    s.open_overlay_dummy = True
    assert hud_rule_owns_showing("dummy") is True
    assert hidden("dummy", s, dummy=True) is False
    assert hidden("dummy", s) is True                  # open world, no dummy
    assert hidden("dummy", s, dummy=True, inside=True) is True
    assert hidden("dummy", s, inside=True) is True

    # Auto On off: an ordinary overlay outside an instance — the instance half
    # of the row is not that toggle's to give away
    s.dps_dummy_auto = False
    assert hidden("dummy", s) is False
    assert hidden("dummy", s, dummy=True) is False
    assert hidden("dummy", s, inside=True) is True
    s.dps_dummy_auto = True

    # a key with no row has no placement opinion: the needles, anything the
    # menu rules judge on their own
    assert hud_allowed_places(s, "compass") is None
    assert hud_rule_owns_showing("compass") is False
    assert hidden("compass", s) is False
    assert hidden("compass", s, inside=True) is False


def test_at_training_dummy_reads_the_tracker_and_the_setting():
    """The gate's dummy half is the tracker's own radius verdict: it must pass
    the CONFIGURED radius (dps_dummy_range) through, answer False without a
    tracker, and never raise on a half-shaped one — an unreadable radius is
    never evidence of being at a dummy."""
    from farever_companion.ui.overlay_rules import (_at_training_dummy,
                                                    DUMMY_RANGE_M)

    class _T:
        def __init__(self, dist):
            self._d = dist
            self.seen = None

        def dummy_within(self, rng):
            self.seen = rng
            return self._d is not None and self._d <= rng

    class _M:
        def __init__(self, t):
            self.dps = t

    class _S:
        dps_dummy_range = 15

    near = _T(12.0)
    assert _at_training_dummy(_M(near), _S()) is True
    assert near.seen == 15                      # the setting, not the default
    assert _at_training_dummy(_M(_T(40.0)), _S()) is False
    assert _at_training_dummy(_M(None), _S()) is False         # no tracker
    assert _at_training_dummy(None, _S()) is False             # detached model

    class _Broken:
        def dummy_within(self, rng):
            raise RuntimeError("read failed")

    assert _at_training_dummy(_M(_Broken()), _S()) is False
    assert _at_training_dummy(_M(object()), _S()) is False     # no predicate

    # a settings double with no such field falls back to the module default
    fallback = _T(4.0)
    assert _at_training_dummy(_M(fallback), object()) is True
    assert fallback.seen == DUMMY_RANGE_M == 5.0


def test_every_hud_overlay_has_persistence_mapping():
    """A HUD_OVERLAYS key without a _PROP_MAP alias would still be toggled in
    the UI, but request() would silently fail to persist its intent (the
    hasattr check in OverlayManager.request skips it) — the silent-drop half
    of the combat leak."""
    from farever_companion.ui import overlay_manager

    for key in overlay_manager.HUD_OVERLAYS:
        if key == "devscanner":
            continue   # dev tool: deliberately not persisted
        alias = f"open_overlay_{key}"
        assert alias in config._PROP_MAP, (
            f"{alias} has no persistence mapping; toggling it would not "
            f"survive a restart")
        target, sub = config._PROP_MAP[alias]
        assert target == "open_overlays"
        assert sub == key
        assert sub in config.OPEN_STATE_OVERLAYS


def test_hud_embeds_render_at_their_in_game_size(moddata):
    """A dev HUD tab must show the overlay at the size it takes in game.

    Each HUD tab re-parents the REAL overlay into the page's body layout,
    which had nothing constraining it — so Qt handed the widget the whole
    body and every HUD rendered full-bleed (measured 2026-09-25: the SpeedRun
    HUD, 330x200 in game, and the Entity HUD both came out 1376x822). The page
    pins the size the overlay reports for itself and sits it top-left, like
    the floating window sits on the game.
    """
    from farever_companion.ui.nav_registry import load_dev_page

    dev = load_dev_page("devicons")
    if dev is None:
        pytest.skip("no build_tools/dev/devicons.py in this checkout")

    from tests.qt_helpers import drain_deleted

    page = dev.DevIconsPage(None, Settings.load())
    page.resize(1400, 900)
    page.show()
    drain_deleted()
    try:
        for label, attr in (("Entity HUD (1:1)", "_entity_hud"),
                            ("Dungeon HUD (1:1)", "_dungeon_hud"),
                            ("Top DPS HUD (1:1)", "_dps_hud"),
                            ("Run Timer HUD (1:1)", "_speedrun_hud")):
            page._mode_cb.setCurrentText(label)
            for _ in range(6):
                QtWidgets.QApplication.processEvents()
            hud = getattr(page, attr)
            size = hud.ingame_size()
            assert hud.minimumWidth() == hud.maximumWidth() == size.width()
            assert hud.minimumHeight() == hud.maximumHeight() == size.height()
            # not full-bleed: the body layout can no longer stretch it
            assert size.width() < page._body.width() // 2
            # top-left, where the floating window sits
            assert hud.mapTo(page, hud.rect().topLeft()).x() < 40
    finally:
        dev._remove_dev_patches()      # leave no global patch behind
        destroy(page)


def test_speedrun_dev_tab_freezes_its_clock_and_walks_both_scenarios(moddata):
    """The SpeedRun preview must stop ticking, and must show BOTH states.

    One live tick is a full re-render — every label re-styled, then a fit
    resize — so the overlay's 50 ms poll repainted the whole tab forever, which
    reads as flashing. The preview now freezes once its scripted fight is over
    (the last frame stays on screen) and re-opening the tab replays it. The
    scenarios alternate per OPEN, not per _refresh: the HUD is rebuilt on every
    entry, so a counter on the instance never advanced and the finished "kill"
    state could never be seen.
    """
    from farever_companion.ui.nav_registry import load_dev_page

    dev = load_dev_page("devicons")
    if dev is None:
        pytest.skip("no build_tools/dev/devicons.py in this checkout")

    from tests.qt_helpers import drain_deleted

    page = dev.DevIconsPage(None, Settings.load())
    page.resize(1400, 900)
    page.show()          # the HUD's tick starts a run only while it is visible
    drain_deleted()
    try:
        page._mode_cb.setCurrentText("Run Timer HUD (1:1)")
        drain_deleted()
        hud = page._speedrun_hud
        assert hud._scenario == "running"
        assert hud._poll.isActive()                  # live while simulating
        # drain the scripted beats, then the settle timer's own freeze
        while hud._beats:
            hud._beat()
        assert hud._settle.isActive()                # freeze is scheduled
        hud._freeze_clocks()
        assert not hud._poll.isActive()              # …and stops the re-render
        assert "clocks frozen" in hud.scenario_label()
        # the digits are the frozen mid-fight frame
        assert hud.time_lbl.text() != "00:00.000"
        # re-opening the tab replays the other scenario, live again
        page._mode_cb.setCurrentText("Entity HUD (1:1)")
        page._mode_cb.setCurrentText("Run Timer HUD (1:1)")
        drain_deleted()
        hud = page._speedrun_hud
        assert hud._scenario == "kill"
        assert hud._poll.isActive()
        # the scripted beats are real timers (700 + 900 ms) and the kill is
        # detected by the live tick, so let wall-clock time pass
        import time
        deadline = time.monotonic() + 2.8      # 700 + 900 ms beats, then settle
        while time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
        # the kill ended the run, and the short post-finish settle froze it:
        # a finished run has nothing left to watch
        assert hud.timer.last is not None
        assert not hud._poll.isActive()
        # the finished state rendered: gold NEW BEST + its LAST row
        assert "NEW BEST" in hud.time_sub.text()
        assert "LAST" in hud.time_sub.text()
    finally:
        dev._remove_dev_patches()
        destroy(page)


def test_prop_map_covers_exactly_the_declared_keys():
    """The declared open-state key set and the alias table must agree — an
    alias with no key means dead persistence; a key with no alias means an
    unpersistable toggle."""
    mapped = {sub for target, sub in config._PROP_MAP.values()
              if target == "open_overlays"}
    assert mapped == set(config.OPEN_STATE_OVERLAYS)


def test_load_strips_combat_without_experimental(moddata):
    (moddata / "settings.json").write_text(json.dumps({
        "open_overlays": ["entity", "combat"],
    }), encoding="utf-8")
    assert "combat" not in Settings.load().open_overlays
    # the experimental branch that once admitted this key no longer exists


def test_load_strips_unknown_leftover_keys(moddata):
    """A removed/renamed overlay key must never resurrect its window."""
    (moddata / "settings.json").write_text(json.dumps({
        "open_overlays": ["entity", "legacy_radar", "combat"],
    }), encoding="utf-8")
    assert Settings.load().open_overlays == ["entity"]


def test_load_strips_combat_even_under_experimental(moddata, monkeypatch):
    """The dev combat readout was removed outright ("remove old combat state
    dev"), which also deleted the FAREVER_EXPERIMENTAL branch that used to let
    its leftover flag through — so the env var must no longer resurrect it."""
    monkeypatch.setenv("FAREVER_EXPERIMENTAL", "1")
    (moddata / "settings.json").write_text(json.dumps({
        "open_overlays": ["entity", "combat"],
    }), encoding="utf-8")
    assert "combat" not in Settings.load().open_overlays


def test_hygiene_does_not_touch_healthy_settings(moddata):
    (moddata / "settings.json").write_text(json.dumps({
        "open_overlays": ["dps", "entity", "map"],
    }), encoding="utf-8")
    assert Settings.load().open_overlays == ["dps", "entity", "map"]


def test_roundtrip_preserves_user_intent(moddata):
    s = Settings()
    s.open_overlay_entity = True
    s.open_overlay_dps = True
    s.open_overlay_map = False
    s.open_overlay_speedrun = True
    s.save()
    s2 = Settings.load()
    assert s2.open_overlay_entity is True
    assert s2.open_overlay_dps is True
    assert s2.open_overlay_speedrun is True
    assert s2.open_overlay_map is False


def test_activity_log_never_touches_the_widget_from_a_capture_thread(moddata):
    """A log line from a capture thread must be queued, never written to the
    widget from that thread.

    The bridge's UDP loop (first ten hits, then a health line every 50 events),
    the DLL-free scanner and the drain canary all call log() from their own
    threads, and ControlPanel.log appends to a QPlainTextEdit. That off-thread
    append is what corrupted Qt's state and faulted later inside an unrelated
    allocation — the reported access violation in QThread.__init__, mid-fight.
    """
    import threading
    import time

    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    cp = ControlPanel(Settings())
    try:
        view = QtWidgets.QPlainTextEdit()
        cp.log_view = view                      # the log page's widget
        seen = []                               # (line, thread name)
        real = view.appendPlainText

        def spy(line):
            seen.append((str(line), threading.current_thread().name))
            return real(line)

        view.appendPlainText = spy
        worker = threading.Thread(target=cp.log,
                                  args=("bridge: live events streaming",),
                                  name="fake-bridge-ipc")
        worker.start()
        worker.join(5)
        # The worker thread must not have touched the widget at all ...
        assert seen == []
        # ... and the line still lands, on the GUI thread, once the loop spins.
        for _ in range(50):
            QtWidgets.QApplication.processEvents()
            if seen:
                break
            time.sleep(0.02)
        assert seen, "queued log line never reached the Activity Log"
        assert seen[0][1] == "MainThread"
        assert "bridge: live events streaming" in seen[0][0]
    finally:
        try:
            cp.attach_ctl.stop()
        except Exception:
            pass
        destroy_panel(cp)
        drain_deleted()


def test_overlay_window_close_syncs_card_and_setting(moddata):
    """Closing an overlay window must uncheck its card in the same pass.

    _on_closed used to sync_cards() BEFORE flipping the setting to False,
    so the card re-synced to the stale True while the disk went OFF — the
    window looked enabled until the next launch, when it rebuilt as OFF
    and the user had to "turn it back on"."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    import types
    s = Settings()
    s.open_overlay_entity = True
    s.save()
    cp = ControlPanel(s)
    try:
        cp.overlay_mgr.open_startup_overlays()
        drain_deleted()
        # Startup only records intent now (no pre-locate builds): simulate
        # the locate, then drain the staggered restore synchronously.
        assert cp.overlay_mgr.overlays.get("entity") is None
        assert "entity" in cp.overlay_mgr._pending_opens
        cp.overlay_mgr.model = types.SimpleNamespace(
            player_addr=0x1234,
            # Default Settings opens the minimap too; its 33 ms canvas timer
            # reads these. The real model answers None at the menu, which the
            # canvas handles as "Waiting for player" — a stub without them
            # crashed refresh_fast the first time the timer landed inside
            # this test's event-loop window (order-dependent failure).
            player_xyz=lambda: None,
            player_heading=lambda: 0.0)
        cp.overlay_mgr.on_located(True)
        cp.overlay_mgr._drain_stagger_sync()
        drain_deleted()
        cards = cp.overlay_mgr.cards.get("entity", [])
        assert cards and cards[0].isChecked() is True
        ov = cp.overlay_mgr.overlays.get("entity")
        assert ov is not None
        # on_located clears the pre-locate deferral set once the player
        # resolves: an X-close after that is a real user intent-off,
        # not a lifecycle close, so it must persist.
        assert "entity" not in cp.overlay_mgr._pending_opens
        ov.close()
        drain_deleted()
        assert s.open_overlay_entity is False
        assert cards[0].isChecked() is False
        assert "entity" not in Settings.load().open_overlays
    finally:
        # Stop the attach watcher + join its locate worker: timers keep
        # ticking after close() and a worker in flight at interpreter exit
        # aborts the process ("destroyed while still running").
        try:
            cp.attach_ctl.stop()
        except Exception:
            pass
        destroy_panel(cp)
        drain_deleted()


def test_devicons_nav_respects_show_devicons(moddata):
    """_refresh_nav_visibility must not force the Dev Icons nav item visible.

    The item is owned by _apply_sidebar_element_visibility (show_devicons +
    the dev gate); the generic not-in-disabled_pages rule used to re-show it
    on every refresh, making the Settings -> Pages toggle decorative."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    for on, hidden in ((False, True), (True, False)):
        s = Settings()
        s.show_devicons = on
        s.save()
        cp = ControlPanel(s)
        try:
            drain_deleted()
            cp._refresh_nav_visibility()
            di = cp._nav_items.get("devicons")
            assert di is not None
            assert di.isHidden() is hidden
        finally:
            try:
                cp.attach_ctl.stop()
            except Exception:
                pass
            destroy_panel(cp)
            drain_deleted()


def test_settings_audit_logs_saves_and_abnormal_loads(moddata, monkeypatch):
    """Every settings.json write leaves a forensic line (state + call path)
    in settings_audit.log, and a missing/corrupt file at startup leaves a
    load line — while a healthy load stays a pure read with no log I/O.

    Opt-in: normal runs write no log files into the user's moddata folder, so
    a debug session (or this test) asks for the audit explicitly.
    """
    monkeypatch.setenv("FAREVER_SESSION_AUDIT", "1")

    from farever_companion.config import Settings

    log_path = moddata / "settings_audit.log"
    s = Settings()
    s.open_overlay_dps = True
    s.save()
    log = log_path.read_text(encoding="utf-8")
    assert " save " in log and "'dps'" in log and "by=" in log

    (moddata / "settings.json").unlink()
    s2 = Settings.load()          # missing file -> defaults + load line
    assert s2.open_overlay_dps is False
    log = log_path.read_text(encoding="utf-8")
    assert " load " in log and "missing" in log

    s2.open_overlay_dps = True
    s2.save()                     # file exists again from here on
    healthy_stamp = log_path.stat().st_mtime_ns
    Settings.load()               # healthy load: pure read, untouched log
    assert log_path.stat().st_mtime_ns == healthy_stamp

    for _ in range(310):          # bounded: oldest lines roll off
        s.open_overlay_map = not s.open_overlay_map
        s.save()
    assert len(log_path.read_text(encoding="utf-8").splitlines()) <= 300


def test_request_same_value_never_rewrites_settings(moddata):
    """Startup restore re-requests the persisted intent; when nothing changed
    that must not touch the disk — a full-file save from a stale copy is what
    let a second instance's launch wipe another's toggles."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    s = Settings()
    s.open_overlay_entity = True
    s.open_overlay_speedrun = False
    s.save()
    before = (moddata / "settings.json").read_bytes()
    cp = ControlPanel(s)
    try:
        drain_deleted()
        cp.overlay_mgr.request("entity", True)    # already on: no write
        cp.overlay_mgr.request("speedrun", False)  # already off: no write
        drain_deleted()
        assert (moddata / "settings.json").read_bytes() == before
        assert s.open_overlay_entity is True
        assert s.open_overlay_speedrun is False
    finally:
        try:
            cp.attach_ctl.stop()
        except Exception:
            pass
        destroy_panel(cp)
        drain_deleted()


def test_persist_geometry_no_change_never_rewrites_settings(moddata):
    """Teardown paths persist geometry on every close; with nothing moved
    that must be a no-op or a stale copy rewrites newer disk state."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    import types
    s = Settings()
    s.open_overlay_entity = True
    s.save()
    cp = ControlPanel(s)
    try:
        drain_deleted()
        cp.overlay_mgr.open_startup_overlays()
        drain_deleted()
        # Startup defers builds until locate: simulate it, then drain the
        # staggered restore synchronously.
        cp.overlay_mgr.model = types.SimpleNamespace(
            player_addr=0x1234,
            player_xyz=lambda: None,      # minimap timer: at-menu state
            player_heading=lambda: 0.0)
        cp.overlay_mgr.on_located(True)
        cp.overlay_mgr._drain_stagger_sync()
        drain_deleted()
        ov = cp.overlay_mgr.overlays.get("entity")
        assert ov is not None
        ov.persist_geometry()                     # first: may store position
        stamp = (moddata / "settings.json").stat().st_mtime_ns
        ov.persist_geometry()                     # second: nothing moved
        assert (moddata / "settings.json").stat().st_mtime_ns == stamp
    finally:
        try:
            cp.attach_ctl.stop()
        except Exception:
            pass
        destroy_panel(cp)
        drain_deleted()


def test_locate_does_not_burst_build_overlays(moddata):
    """located_changed must not build overlay windows itself.

    The Overlays page's handler used to request every enabled HUD
    synchronously on locate — the all-at-once burst in the same tick as the
    locate + DPS engine start. Post-locate restore is staggered through
    overlay_mgr.on_located (one overlay per tick); this handler must build
    nothing."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    s = Settings()
    s.open_overlay_entity = True
    s.open_overlay_map = True
    s.open_overlay_dps = True
    s.save()
    cp = ControlPanel(s)
    try:
        drain_deleted()
        cp._set_overlay_cards_enabled(True)
        drain_deleted()
        assert cp.overlay_mgr.overlays.get("entity") is None
        assert cp.overlay_mgr.overlays.get("map") is None
        assert cp.overlay_mgr.overlays.get("dps") is None
        assert cp.overlay_mgr.overlays.get("dummy") is None
    finally:
        try:
            cp.attach_ctl.stop()
        except Exception:
            pass
        destroy_panel(cp)
        drain_deleted()


def test_minimap_user_pan_survives_movement(moddata):
    """A right-drag pan is the user's explicit placement: walking must not
    snap the map back to follow. Only TRACKER pans (pan_to) auto-clear on
    movement; right-double-click re-centres manually."""
    import types
    from PySide6 import QtCore
    from farever_companion.config import Settings
    from farever_companion.ui.overlays.minimap import _Canvas

    s = Settings()
    pos = [[0.0, 0.0, 0.0]]
    model = types.SimpleNamespace(
        player_xyz=lambda: tuple(pos[0]),
        player_heading=lambda: 0.0,
        camera_yaw=lambda: 0.0,
        view_matrix=lambda: None,
    )
    canvas = _Canvas(model, s)
    try:
        assert canvas._read_player() is True
        # User pan: sticks across movement.
        canvas._pan_x, canvas._pan_y = 50.0, -30.0
        canvas._tracking_pan = False
        pos[0] = [10.0, 0.0, 0.0]
        assert canvas._read_player() is True
        assert (canvas._pan_x, canvas._pan_y) == (50.0, -30.0)
        # Tracker pan: clears on movement (re-centre on the character).
        canvas._tracking_pan = True
        pos[0] = [20.0, 0.0, 0.0]
        assert canvas._read_player() is True
        assert (canvas._pan_x, canvas._pan_y) == (0.0, 0.0)
        # Right-double-click: manual re-centre of a user pan.
        canvas._pan_x, canvas._pan_y = 50.0, -30.0
        canvas._tracking_pan = False
        ev = types.SimpleNamespace(button=lambda: QtCore.Qt.RightButton)
        canvas.mouseDoubleClickEvent(ev)
        assert (canvas._pan_x, canvas._pan_y) == (0.0, 0.0)
    finally:
        canvas.deleteLater()


def test_on_closed_lifecycle_close_never_persists_off(moddata):
    """A close the user never asked for (detach teardown tracked in
    _pending_opens) must not persist OFF — even past the 500 ms _detaching
    reset, when a late DeferredDelete used to save a stale OFF for every
    overlay at once."""
    from farever_companion.config import Settings
    from farever_companion.ui import overlay_manager as om

    s = Settings()
    s.open_overlay_entity = True
    s.save()
    before = (moddata / "settings.json").read_bytes()
    mgr = om.OverlayManager(s)
    try:
        mgr._pending_opens.add("entity")   # as close_all() does on detach
        mgr._detaching = False             # ...long after the reset fired
        mgr._on_closed("entity")
        assert s.open_overlay_entity is True
        assert (moddata / "settings.json").read_bytes() == before
    finally:
        mgr.deleteLater()


def test_on_closed_skips_save_while_app_tearing_down(moddata, monkeypatch):
    """Console close / Ctrl+C / logoff skips closeEvent, so no detach guard
    is ever armed — dying windows in that phase still must not persist OFF."""
    from farever_companion.config import Settings
    from farever_companion.ui import overlay_manager as om

    s = Settings()
    s.open_overlay_entity = True
    s.save()
    before = (moddata / "settings.json").read_bytes()
    mgr = om.OverlayManager(s)
    try:
        monkeypatch.setattr(
            "farever_companion.ui.overlay_manager._app_tearing_down",
            lambda: True)
        mgr._on_closed("entity")
        assert s.open_overlay_entity is True
        assert (moddata / "settings.json").read_bytes() == before
    finally:
        mgr.deleteLater()


def test_mouse_back_rejects_open_dialog():
    """When a QDialog is open, mouse back (XButton1) rejects/closes the dialog."""
    from PySide6 import QtCore, QtGui
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    s = Settings()
    cp = ControlPanel(s)
    dlg = QtWidgets.QDialog(cp)
    dlg.show()
    assert dlg.isVisible()

    ev = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        QtCore.QPointF(10, 10),
        QtCore.Qt.MouseButton.XButton1,
        QtCore.Qt.MouseButton.XButton1,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    handled = cp.eventFilter(dlg, ev)
    assert handled is True
    assert not dlg.isVisible()
    destroy_panel(cp)


def test_mouse_back_invokes_consume_pane_back_on_stacked_page():
    """When the active page widget provides consume_pane_back(), XButton1 invokes it."""
    from PySide6 import QtCore, QtGui
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    s = Settings()
    cp = ControlPanel(s)
    consumed = []
    cur = cp.stack.currentWidget()
    cur.consume_pane_back = lambda: (consumed.append(True) or True)

    ev = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        QtCore.QPointF(10, 10),
        QtCore.Qt.MouseButton.XButton1,
        QtCore.Qt.MouseButton.XButton1,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    handled = cp.eventFilter(cur, ev)
    assert handled is True
    assert consumed == [True]
    destroy_panel(cp)


def test_craft_recipe_jump_history_back():
    """In Craft page, jumping to a recipe material pushes to history and mouse back restores it."""
    from farever_companion.ui.pages.craft import CraftPageMixin

    class _Craft(QtWidgets.QWidget, CraftPageMixin):
        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            return page, QtWidgets.QVBoxLayout(page)

    c = _Craft()
    page = c._page_craft()
    assert hasattr(page, "consume_pane_back")
    c._craft_shown_id = "RecipeA"
    c._craft_jump_to_recipe("RecipeB")
    assert getattr(c, "_craft_recipe_history", []) == ["RecipeA"]
    res = page.consume_pane_back()
    assert res is True
    assert getattr(c, "_craft_recipe_history", []) == []


def test_settings_subtab_history_back():
    """Settings records subtab transitions in nav history."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    s = Settings()
    cp = ControlPanel(s)
    cp._select_nav("settings")
    cp._on_settings_tab("Layers")
    cp._on_settings_tab("DPS")
    assert "settings:layers" in cp._nav_history._back
    destroy_panel(cp)


def test_codex_tab_history_back():
    """Codex records tab transitions in nav history."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    s = Settings()
    cp = ControlPanel(s)
    cp._select_nav("codex")
    if hasattr(cp, "_codex_tabs"):
        cp._codex_active_tab = "Z1"
        cp._codex_tabs.setCurrentText("Dungeons")
        cp._codex_tab_changed(0)
        assert "codex:Z1" in cp._nav_history._back
    destroy_panel(cp)


def test_mouse_back_walks_ancestor_widgets_for_consume_pane_back():
    """When a deeply nested child widget is clicked, mouse back walks up ancestors
    to invoke consume_pane_back() on any parent widget."""
    from PySide6 import QtCore, QtGui
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    s = Settings()
    cp = ControlPanel(s)
    container = QtWidgets.QWidget(cp)
    nested_parent = QtWidgets.QWidget(container)
    child = QtWidgets.QPushButton(nested_parent)
    called = []
    nested_parent.consume_pane_back = lambda: (called.append(True) or True)

    ev = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        QtCore.QPointF(5, 5),
        QtCore.Qt.MouseButton.XButton1,
        QtCore.Qt.MouseButton.XButton1,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    handled = cp.eventFilter(child, ev)
    assert handled is True
    assert called == [True]
    destroy_panel(cp)


def test_codex_subview_history_back():
    """Codex records sub-view changes in nav history (e.g. Collection Chests/Orbs)."""
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    s = Settings()
    cp = ControlPanel(s)
    cp._select_nav("codex")
    if hasattr(cp, "_codex_tabs"):
        cp._codex_tabs.setCurrentText("Collection")
        cp._codex_tab_changed(0)
        cp._set_dungeon_view_mode("chests")
        assert any("Collection" in h for h in cp._nav_history._back)
    destroy_panel(cp)


def test_subtab_hops_record_history_and_back_returns_to_that_tab():
    """A sub-tab switch INSIDE a page is a navigation step: BACK returns to
    the tab you came from instead of jumping out of the page entirely.

    Gear was the one family that recorded nothing — it read the leaving tab
    off the strip, whose currentChanged fires after the clicked segment is
    already checked, so it always equalled the tab being moved to. Craft,
    Codex and Settings track their own active tab and were already right;
    they are pinned here so the four cannot drift apart again.
    """
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel

    cp = ControlPanel(Settings())
    try:
        # the strip is looked up per case, never captured up front: visiting
        # the other pages evicts these page trees (deleteLater), so a strip
        # captured early is an orphan that answers with its old tab
        strips = {"gear": lambda: cp._items_tabs,
                  "craft": lambda: cp._craft_tabs,
                  "codex": lambda: cp._codex_tabs,
                  "settings": lambda: cp._settings_tabs}
        cases = (("gear", "Loadout", "Items", "gear:Loadout"),
                 ("gear", "Enchants", "Farm", "gear:Enchants"),
                 ("craft", "Recipes", "Craft List", "craft:Recipes"),
                 ("codex", "Dungeons", "Collection", "codex:Dungeons"),
                 ("settings", "Layers", "Rift", "settings:layers"))
        for family, first, second, leaving in cases:
            cp._select_nav(family)          # rebuild the page if it was shed
            strip = strips[family]()
            strip._btns[first].click()
            strip._btns[second].click()
            assert leaving in cp._nav_history._back, (family, first, second)
            cp._nav_history.back(cp._current_page())
            # Codex carries a view level under the tab, so compare by prefix
            assert cp._current_page().startswith(leaving), \
                (family, first, second, cp._current_page())
            # ...and the strip really went back: BACK must not be a page-level
            # no-op that leaves the tab we were on lit
            strip = strips[family]()        # re-entering rebuilt it
            assert strip.currentText() == first, (family, first, second,
                                                  strip.currentText())
    finally:
        try:
            cp.attach_ctl.stop()
        except Exception:
            pass
        destroy_panel(cp)

def test_stray_defaults_instance_cannot_wipe_overlay_intent(moddata):
    """A process that never loaded settings.json must not assert overlay intent.

    Live 2026-09-19: a stray defaults instance saved `open_overlays=[]` and an
    empty `geometry` over a five-overlay list, so every HUD stopped restoring
    and the user saw their settings "turn off" again. Its junk list is never a
    dataclass default, so the per-field preserve loop above cannot see it —
    the intent keys need the first-save guard of their own.
    """
    import json

    from farever_companion.config import Settings

    good = {"open_overlays": ["dps", "dungeon", "entity", "map", "speedrun"],
            "geometry": {"entity": "40,40,350,111", "dps": "34,569,320,420"}}
    (moddata / "settings.json").write_text(json.dumps(good), encoding="utf-8")

    stray = Settings()                 # bare instance: never loaded the file
    stray.open_overlays = []           # what its own open/close cycle left
    stray.geometry = {}
    stray.save()

    on_disk = json.loads((moddata / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["open_overlays"] == good["open_overlays"]
    assert on_disk["geometry"] == good["geometry"]


def test_first_save_owns_the_file_so_a_later_close_still_persists(moddata):
    """The guard covers one save only: an instance that has written the file
    owns it from then on, so closing an overlay must persist.

    Without the first-save-only gate the intent guard would freeze the overlay
    list for anything that did not start by loading settings.json — a fresh
    install could never turn a HUD off (caught by
    test_overlay_window_close_syncs_card_and_setting).
    """
    import json

    from farever_companion.config import Settings

    s = Settings()                     # bare: the guard is active for save #1
    s.open_overlays = ["entity", "map"]
    s.save()                           # writes, and this instance now owns it
    s.open_overlays = ["map"]          # the user closed entity
    s.save()

    on_disk = json.loads((moddata / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["open_overlays"] == ["map"]


def test_the_combat_page_never_shows_a_rail_it_cannot_fit(monkeypatch, tmp_path):
    """The page must either hold the whole rail or hide it — never clip it.

    This is the bug the user reported as "the Avg Hit column is cut off", and
    it was invisible to every width test in the suite, because those built
    `_build_combat_page()` on its own. The Combat page is inserted straight
    into the QStackedWidget, so the page is sized to the stack; when the
    stack is narrower than the roster's minimum plus the rail's, the rail
    overflows and the page clips at its own edge. No scrollbar, no warning —
    the tail of the table and the DMG TYPE bar above it just stop mid-number.

    Two things are asserted because both went wrong in turn: the rail is
    never shown in a page too narrow for it, and the threshold does not
    depend on whether the rail is currently shown. The second is the subtle
    one — `page.minimumSizeHint()` drops a hidden widget's contribution, so
    measuring it that way makes hiding the rail argue for showing it again,
    and the rail stutters on and off as the window crosses the boundary.
    """
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    def spin(n=8):
        for _ in range(n):
            QtWidgets.QApplication.instance().processEvents()

    cp = ControlPanel(Settings())
    try:
        cp.resize(1180, 740)
        cp.show()
        spin()
        cp._select_nav("combat")
        spin()
        page = cp.stack.widget(cp._pages["combat"])

        needs = set()
        for win_w in (1400, 1360, 1340, 1300, 1220, 1180,
                      1220, 1300, 1340, 1400, 1600, 1920, 1180, 1400):
            cp.resize(win_w, 860)
            spin(10)
            need = cp._cp_rail_min_page_width
            needs.add(need)
            if cp._cp_rail_container.isHidden():
                continue
            assert page.width() >= need, (
                f"at a {win_w}px window the rail is shown in a {page.width()}px "
                f"page that needs {need}px — the overflow is clipped silently")
        # One number, not two: if the threshold moved when the rail hid, the
        # rail would flicker as the window crossed it.
        assert len(needs) == 1, f"the threshold moved with the rail: {needs}"
    finally:
        drain_deleted()
        cp.close()
        cp.deleteLater()
        spin()


def test_a_live_combat_refresh_does_not_resurrect_the_hidden_rail():
    """A 250ms refresh must not force the rail back on over a narrow page.

    This is the order-dependent half of the rail bug. `_render_combat_breakdown`
    used to end every damage-view render with `_cp_rail_container.setVisible(
    True)`, and `_refresh_combat_page_live` calls it AFTER the width fit that
    just hid the rail — so whichever 250ms tick happened to land inside a
    narrow window flickered the rail back on over the clipped table. That is
    why the width test above passed in isolation (its sweep fits between ticks)
    and failed in the full suite (a warmed-up process lets a tick in).

    Driving the timer callback directly makes the trigger deterministic instead
    of a race: the rail, hidden because the page is too narrow, must still be
    hidden after both a bare render and a full live refresh.
    """
    from farever_companion.config import Settings
    from farever_companion.ui.control_panel import ControlPanel
    from tests.qt_helpers import drain_deleted

    def spin(n=8):
        for _ in range(n):
            QtWidgets.QApplication.instance().processEvents()

    cp = ControlPanel(Settings())
    try:
        cp.resize(1180, 740)
        cp.show()
        spin()
        cp._select_nav("combat")
        spin()
        page = cp.stack.widget(cp._pages["combat"])
        # Precondition: at this width the page genuinely cannot hold the rail.
        cp._update_combat_rail_for_width()
        assert page.width() < cp._cp_rail_min_page_width
        assert cp._cp_rail_container.isHidden(), \
            "precondition: the rail should already be hidden at 1180px"

        # The damage-view render was the force-show; it must now defer to the
        # width rule.
        cp._render_combat_breakdown()
        assert cp._cp_rail_container.isHidden(), \
            "a damage breakdown render re-showed the rail in a page that cannot fit it"

        # ...and so must the timer callback that renders every 250ms.
        cp._refresh_combat_page_live()
        assert page.width() < cp._cp_rail_min_page_width
        assert cp._cp_rail_container.isHidden(), \
            "the live refresh re-showed the rail in a page that cannot fit it"
    finally:
        drain_deleted()
        cp.close()
        cp.deleteLater()
        spin()


def test_closing_the_app_saves_the_fight_that_was_still_in_progress(moddata):
    """A fight running when the user quits must not vanish.

    Every persist trigger the tracker owns is a fight ENDING (a kill, a wipe,
    the boss engaging, a manual Reset), so an unfinished fight had never
    reached disk: close the app mid-pull and that damage was simply gone when
    it came back up. closeEvent now archives it first - BEFORE the drain
    stops, since that is the moment the last event is gone - and labels it
    "App Closed" rather than a boss outcome, because the fight did not
    conclude and the archive must not claim it did.
    """
    import time

    from farever_companion.config import Settings
    from farever_companion.config.store import config_dir
    from farever_companion.core.damage_events import DamageEvent
    from farever_companion.core.dps_tracker import DpsTracker
    from farever_companion.ui.control_panel import ControlPanel
    from tests.dps_fakes import _FakeModel, _hero, _foe, BOSS, PA

    dps_dir = Path(config_dir()) / "dps"
    m = _FakeModel()
    tr = DpsTracker(m)
    tr.set_history_dir(dps_dir)
    m._units = [_hero(PA), _foe(BOSS, uid="Reblochonk", is_boss=True)]
    tr.update()

    tr.in_boss_fight = True
    tr.boss_session.target_name = "👑 Reblochonk"
    tr.boss_session.target_addr = BOSS
    m.damage.push(DamageEvent(amount=2500.0, skill="A", source_addr=PA,
                              target_addr=BOSS, t=time.time()))
    tr.update()
    assert tr.boss_session.group_damage == 2500.0
    assert not list(dps_dir.rglob("*.json")), "precondition: nothing on disk yet"

    cp = ControlPanel(Settings())
    try:
        # ControlPanel.model is a read-only view of the attach controller's
        # model, so the wiring goes where the real app puts it.
        cp.attach_ctl.model = _FakeModel()
        cp.attach_ctl.model.dps = tr
        cp._flush_dps_on_close()
    finally:
        tr._history_dir = None

    written = list(dps_dir.rglob("*.json"))
    assert written, "the in-progress fight was never written"
    from farever_companion.core.dps_data import read_history_file
    sessions = read_history_file(written[0])
    assert any(abs(s.group_damage - 2500.0) < 1e-6 for s in sessions), \
        f"the live fight's damage is not in the archive: {written[0]}"
    assert any("App Closed" in (s.name or "") for s in sessions), \
        "an unfinished fight must not be filed as a boss outcome"


def test_closing_the_app_leaves_the_activity_log_on_disk(moddata):
    """The Activity Log must outlive the process.

    It lives in a `deque` in RAM by construction - a widget may only be
    written on the GUI thread and every capture engine logs from its own - so
    a normal quit used to end with nothing at all. The dev crash log carries
    the same tail but only with FAREVER_DEV_CRASH_LOG set AND a real fault, so
    a clean exit left no trace of what the meter had been doing.
    """
    from farever_companion.config import Settings
    from farever_companion.config.store import config_dir
    from farever_companion.ui.control_panel import ControlPanel

    cp = ControlPanel(Settings())
    try:
        cp.log("Top DPS: archive the run before the window goes")
        cp.log("shutdown")
        cp._write_activity_log_to_disk()
    finally:
        pass

    path = Path(config_dir()) / "activity_log.txt"
    assert path.exists(), "no Activity Log on disk after a clean exit"
    text = path.read_text(encoding="utf-8")
    assert "===== session" in text, "runs must be separated so the file reads"
    assert "archive the run before the window goes" in text
