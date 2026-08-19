"""UI-utility / infra tests, merged from six small files:

* loc shim — the Items page's unknown-location resolution reads poi_locs /
  mob_locs from the compiled raw_data.py shim, not loose JSON (frozen build).
* page history — back/forward navigation semantics (mouse X buttons).
* QSS scan — every setStyleSheet call's constant parts are brace-balanced
  (plain strings AND f-string literal segments), so Qt never logs "Could not
  parse stylesheet".
* icon cropping — pixmap_cropped / tile(trim=True) cut transparent margins
  and scale visible content to fill the frame, pinning the contract on real
  atlas data (headless Qt).
* launcher check — the console-script launcher classifier's broken-shim
  signature ("exit nonzero with no output at all").
* soulstone badge — soulstone-farmed mounts/gliders render the magenta gem
  instead of the plain mob sword (headless Qt).
"""
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
    card = _card("Mount_Skunk_05")   # Ponogian Skunk (Skunk_Z1W_FS drop)
    assert not card.mob_ico.isHidden()
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
    Windows font resolution can internally call setPointSize(-1)) and
    forwards every other message to stderr."""
    import farever_companion.app as m
    from PySide6 import QtCore

    prev = QtCore.qInstallMessageHandler(m._filter_cosmetic_font_warning)
    try:
        QtCore.qWarning(
            "QFont::setPointSize: Point size <= 0 (-1), "
            "must be greater than 0")
        QtCore.qWarning(
            "QFont::setPointSizeF: Point size <= 0 (-1), "
            "must be greater than 0")
        assert "setPointSize" not in capsys.readouterr().err
        # unrelated warnings still reach stderr
        QtCore.qWarning("QPainter::setRenderHint: Painter not active")
        assert "Painter not active" in capsys.readouterr().err
    finally:
        QtCore.qInstallMessageHandler(prev)
