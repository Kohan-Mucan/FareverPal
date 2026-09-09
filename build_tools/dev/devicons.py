"""Dev-only icon preview page (never ships).

A testing tool for adding icons and overlays, usable WITHOUT the game running.
It renders the game's icons the way the Entity HUD and Minimap actually do — and
embeds the real HUDs themselves — so you can see how a change looks before it
ships.

Lives here, outside `farever_companion/`, because it is a developer tool rather
than product code: the app loads it by file path (nav_registry.load_dev_page)
and it simply does not exist in a frozen build, which is what hides the Dev
Icons nav item for users. See build_tools/dev/__init__.py.

Contents:
  * Entity HUD (1:1) — a faithful replica of the live HUD with 4 randomized
    example rows (mob / gatherable / loot / activity) rendered from the
    selected source, with the HUD's text colors.
  * Dungeon HUD (1:1) — the REAL Dungeon HUD (boss / players / enemies / loot
    / food / secret orbs / rift) driven by synthetic fixtures, so icon/asset
    changes can be verified without the game.
   * Top DPS HUD (1:1) and SpeedRun HUD (1:1) — the real overlays driven by fake
     combat events / a scripted dungeon, so the meters and both speedrun clocks
     (RUN + BOSS split, their PB / pace rows, the NEW BEST finish) render offline.
   * Test Dummy HUD (1:1) — the real dummy board driven by synthetic hits on a
     training dummy, so the target bar, countdown and per-skill rows render offline.
  * Icon Browser — every minimap marker / UI icon / game-sheet icon, with a
    Compare mode showing atlas vs fallback side-by-side.

Toggle between the compiled atlas (minimap) and the loose fallback folder
(assets/icons2) to confirm a freshly dropped file renders.
"""
from __future__ import annotations

import math
import random
import types
import sys
import importlib

from PySide6 import QtCore, QtGui, QtWidgets

# Absolute imports on purpose: this module lives OUTSIDE the package (see the
# folder docstring) and is loaded from its file path, so there is no package
# context for a relative import to resolve against.
from farever_companion.ui import theme
from farever_companion.data import icons, atlas
from farever_companion import paths
from farever_companion.ui.overlays.entity_rows import RowSpec
from farever_companion.ui.overlays.entity_overlay import EntityOverlay
from farever_companion.ui.overlays.dungeon_overlay import DungeonOverlay
from farever_companion.ui.overlays.dps import DpsOverlay
from farever_companion.ui.overlays.dummy_overlay import DummyOverlay
from farever_companion.ui.overlays.speedrun_overlay import SpeedrunOverlay
from farever_companion.core.damage_events import DamageEvent
from farever_companion.core.group_reader import GroupMember, GroupSnapshot

_FB_DIR = paths.fallback_icons_dir()
_MARKER_SHEET = "minimap"


# Represent the HUD's text palette so the replica reads 1:1.
_C_NAME = theme.TEXT
_C_SUB = theme.MUTED
_C_MOB = theme.DANGER
_C_GATH = "#ff8c42"
_C_LOOT = theme.GOLD
_C_ACT = theme.ACCENT


def _is_blank(pm: QtGui.QPixmap | None) -> bool:
    if pm is None or pm.isNull():
        return True
    img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    if w == 0 or h == 0:
        return True
    step_x = max(1, w // 4)
    step_y = max(1, h // 4)
    for y in range(0, h, step_y):
        row = memoryview(img.constScanLine(y)).cast("B")
        for x in range(0, w, step_x):
            if row[4 * x + 3] > 8:
                return False
    return True


class _LinkLabel(QtWidgets.QLabel):
    """A QLabel styled like a text link — clickable, no button chrome.

    Used for the dev page's header controls so the bar stays a single compact
    text line instead of a row of push buttons."""
    clicked = QtCore.Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setObjectName("Link")
        self.setStyleSheet("color:%s;" % theme.ACCENT)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


# --- dev name / classification overrides -----------------------------------
# The embedded HUD resolves names & ranks from the REAL game tables. For the
# dev preview we want arbitrary demo entities (e.g. "Gatsbee"), so we patch
# those lookups to recognise a small registry. Patches are installed ONLY
# around the HUD's synchronous _refresh() and removed immediately after, so
# the rest of the app is never affected.
_DEV_NAMES: dict = {}
_DEV_BOSS = set()
_DEV_ELITE = set()
_DEV_SPARK = set()

import farever_companion.data.names as _names_mod
import farever_companion.data.units as _units_mod
_orig_unit_name = _names_mod.unit_name
_orig_is_boss = _units_mod.is_boss
_orig_is_elite = _units_mod.is_elite
_orig_drops_spark = _units_mod.drops_spark


def _patched_unit_name(uid):
    return _DEV_NAMES.get(uid, _orig_unit_name(uid))


def _patched_is_boss(uid):
    if uid in _DEV_BOSS:
        return True
    return _orig_is_boss(uid)


def _patched_is_elite(uid):
    if uid in _DEV_ELITE:
        return True
    return _orig_is_elite(uid)


def _patched_drops_spark(uid):
    if uid in _DEV_SPARK:
        return True
    return _orig_drops_spark(uid)


def _install_dev_patches():
    _names_mod.unit_name = _patched_unit_name
    _units_mod.is_boss = _patched_is_boss
    _units_mod.is_elite = _patched_is_elite
    _units_mod.drops_spark = _patched_drops_spark


def _remove_dev_patches():
    _names_mod.unit_name = _orig_unit_name
    _units_mod.is_boss = _orig_is_boss
    _units_mod.is_elite = _orig_is_elite
    _units_mod.drops_spark = _orig_drops_spark


class _FakeModel:
    """Minimal LiveModel stub so the real HUD runs with no game attached.

    Every method the HUD touches during a render returns benign defaults;
    there is no process handle, no memory reads, no hook. This is what makes
    the Dev Entity tab a true VM (runs offline)."""

    hl = None
    units_ok = True

    def player_xyz(self):
        return (0.0, 0.0, 0.0)

    def player_profile(self):
        return ""

    # Demo "collected" ids so the HUD can show the done-state sprites
    # (chestopen / orbdone) without a live game session.
    _done = set()

    def is_done(self, uid, profile=None):
        return uid in self._done

    def player_zone(self):
        return ""

    def rift_status(self):
        return _FakeRiftStatus()

    def hero_display_name(self, e=None):
        return getattr(e, "unit_id", "Hero")

    def is_in_dungeon(self):
        return False

    def is_in_dungeon_or_rift(self):
        return True

    def is_in_rift(self):
        return False

    def foe_target_line(self, e=None):
        return None


class _FakeEnt:
    """Stand-in for a game unit/entity (enemy, spark, companion, player)."""

    def __init__(self, unit_id, addr, kind, x=0.0, y=0.0, z=0.0, cls="",
                 hp=None, is_hero=False, is_player_owned=False):
        self.unit_id = unit_id
        self.addr = addr
        self.kind = kind
        self.x, self.y, self.z = x, y, z
        self.cls = cls
        self.hp = hp
        self.is_hero = is_hero
        self.is_player_owned = is_player_owned

    def dist2d(self, x, y):
        return math.hypot(self.x - x, self.y - y)


class _FakeOrb:
    def __init__(self, orb_id):
        self.orb_id = orb_id


class _FakeChest:
    def __init__(self, chest_id, x=0.0, y=0.0, z=0.0, dist=10.0,
                 loot_table=None, state=None):
        self.chest_id = chest_id
        self.x, self.y, self.z = x, y, z
        self.dist = dist
        self.loot_table = loot_table
        self.state = state


class _FakeRiftStatus:
    """Stand-in for the live RiftStatus the HUD renders (timer card)."""
    state = "ACTIVE"
    rift_name = "Rift"
    subzone = "Crabgantua's Gorge"
    status_label = None
    in_range = True
    at_any_rift = True
    poi_x = 30.0
    poi_y = 20.0
    formatted_time = "12:34"


class _FakeDrop:
    def __init__(self, item_id, count=1, x=0.0, y=0.0, z=0.0, rarity=None):
        self.item_id = item_id
        self.count = count
        self.x, self.y, self.z = x, y, z
        self.rarity = rarity

    def dist2d(self, x, y):
        return math.hypot(self.x - x, self.y - y)


class _FakeFood:
    def __init__(self, name, addr=0, x=0.0, y=0.0):
        self.name = name
        self.addr = addr
        self.x, self.y = x, y

    def dist2d(self, x, y):
        return math.hypot(self.x - x, self.y - y)


class _DgOrb:
    """Stand-in for an in-dungeon Secret Orb (InstanceOrb element)."""
    def __init__(self, elem_id, x=0.0, y=0.0, z=0.0):
        self.elem_id = elem_id
        self.x, self.y, self.z = x, y, z
        self.addr = 0

    def dist2d(self, x, y):
        return math.hypot(self.x - x, self.y - y)


def _sample_fixtures():
    """Build a synthetic scene covering every Entity HUD section.

    Shapes mirror exactly what game gather code feeds into
    EntityRenderMixin._render_sections, so no rendering logic is re-implemented
    here — we only supply data. The demo mobs below are arbitrary (not real
    game units); the dev name/rank registry (_DEV_NAMES / _DEV_BOSS / ...) is
    populated so the patched names/units lookups resolve them correctly while
    the HUD renders."""
    # --- demo entity registry (consumed by the patched lookups) -----------
    # We use the REAL unit ids so icons resolve from the live atlas; the
    # registry only supplies display names / ranks for these specific picks.
    _DEV_NAMES.clear()
    _DEV_BOSS.clear()
    _DEV_ELITE.clear()
    _DEV_SPARK.clear()
    # spark -> the actual spark mob (drops_spark unit)
    _DEV_NAMES["Crab_Z1W_U"] = "Sparkling Crab"; _DEV_SPARK.add("Crab_Z1W_U")
    # wolf (mob)
    _DEV_NAMES["Wolf_Z1W"] = "Wolf"
    # elite Ram Page
    _DEV_NAMES["Crimson_Z1W_Captain_E"] = "Ram Page"; _DEV_ELITE.add("Crimson_Z1W_Captain_E")
    # boss Gatsbee
    _DEV_NAMES["Gatsbee"] = "Gatsbee"; _DEV_BOSS.add("Gatsbee")
    _DEV_NAMES["Crabgantua"] = "Crabgantua"

    spark = _FakeEnt("Crab_Z1W_U", 0x1001, "spark", x=10, y=5)
    enemies = [
        _FakeEnt("Wolf_Z1W", 0x1002, "enemy", x=20, y=12),
        _FakeEnt("Crimson_Z1W_Captain_E", 0x1003, "enemy", x=22, y=14),
        _FakeEnt("Gatsbee", 0x1004, "enemy", x=24, y=16),
    ]
    players = [
        _FakeEnt("warrior", 0x2001, "hero", cls="ent.hero.warrior"),
        _FakeEnt("rogue", 0x2002, "hero", cls="ent.hero.rogue"),
        _FakeEnt("mage", 0x2003, "hero", cls="ent.hero.mage"),
        _FakeEnt("priest", 0x2004, "hero", cls="ent.hero.priest"),
    ]
    companions = [
        _FakeEnt("Rabbit_Spark", 0x3001, "companion"),
        _FakeEnt("Rabbit_Brown", 0x3002, "companion"),
    ]

    ore_ids = ("copperore", "tinore", "tungstene")
    gath_labels = ["copperore", "tinore", "tungstene",
                   "lavendula", "madrigold", "zealotus", "ancientthyme"]
    gatherables = []
    for i, label in enumerate(gath_labels):
        kind = "ore" if label in ore_ids else "flower"
        gatherables.append(((i * 2, 0.0, 0.0, kind, label, label), float(10 + i * 3)))

    # Use clean ids (no "2"); the opened/collected ones are flagged done so the
    # HUD renders the renamed done-state sprites (chestopen / orbdone).
    orbs = [_FakeOrb("orb"), _FakeOrb("orbdone")]
    chests = [
        _FakeChest("chest", 5.0, 5.0, 0.0, 12.0),
        _FakeChest("chestopen", 6.0, 6.0, 0.0, 14.0, state="OPENED"),
        _FakeChest("recipe", 7.0, 7.0, 0.0, 16.0, loot_table="recipe_table"),
    ]
    # Static POI: (x, y, z, kind, label, id, boss_id, level, dungeon_name)
    static_pois = [((0.0, 0.0, 0.0, "dungeon", "Crabgantua", "Crabgantua",
                     "Gatsbee", 50, "Crabgantua's Gorge"), 40.0)]

    loots = []
    try:
        from farever_companion.data.items import catalog as _cat
        tiers = ("Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic")
        picks = {}
        for it in _cat.items():
            r = it.get("rarity")
            if r in picks or r not in tiers:
                continue
            iid = it.get("id")
            if iid and icons.has_icon("item", iid):
                picks[r] = iid
            if len(picks) == len(tiers):
                break
        # Force the Legendary slot to the requested demo weapon.
        picks["Legendary"] = "Daggers_Demondash"
        loots = [
            _FakeDrop(iid, 1, float(1 + i), float(1 + i),
                      rarity=("Legendary" if r == "Legendary" else None))
            for i, (r, iid) in enumerate(picks.items())
        ]
    except Exception:
        loots = []

    foods = [_FakeFood("Feast", 0x4001, 2.0, 2.0)]

    return dict(spark=spark, enemies=enemies, players=players,
                companions=companions, gatherables=gatherables,
                orbs=orbs, chests=chests, static_pois=static_pois,
                loots=loots, foods=foods)


def _sample_dungeon_fixtures():
    """Build a synthetic dungeon scene covering every Dungeon HUD section.

    Shapes mirror exactly what DungeonOverlay._render_dg_sections consumes
    (self._dg_* attribute groups), so no rendering logic is re-implemented
    here — we only supply data. The same dev name/rank registry used by the
    Entity HUD is populated so the patched names/units lookups resolve these
    specific demo mobs correctly while the HUD renders."""
    _DEV_NAMES.clear()
    _DEV_BOSS.clear()
    _DEV_ELITE.clear()
    _DEV_SPARK.clear()
    _DEV_NAMES["Crab_Z1W_U"] = "Sparkling Crab"; _DEV_SPARK.add("Crab_Z1W_U")
    _DEV_NAMES["Wolf_Z1W"] = "Wolf"
    _DEV_NAMES["Crimson_Z1W_Captain_E"] = "Ram Page"; _DEV_ELITE.add("Crimson_Z1W_Captain_E")
    _DEV_NAMES["Gatsbee"] = "Gatsbee"; _DEV_BOSS.add("Gatsbee")
    _DEV_NAMES["Crabgantua"] = "Crabgantua"

    # enemies -> list of (ent, dist, kind) where kind in
    # {"boss","elite","trash","avoid","clone_real"}
    enemies = [
        (_FakeEnt("Wolf_Z1W", 0x1002, "enemy", x=20, y=12, hp=80.0), 12.0, "trash"),
        (_FakeEnt("Crimson_Z1W_Captain_E", 0x1003, "enemy", x=22, y=14,
                  hp=320.0), 14.0, "elite"),
        (_FakeEnt("Gatsbee", 0x1004, "enemy", x=24, y=16, hp=9999.0), 16.0, "boss"),
    ]
    # players -> list of (ent, dist); is_hero so the group section styles them
    players = [
        (_FakeEnt("warrior", 0x2001, "hero", cls="ent.hero.warrior",
                  is_hero=True), 5.0),
        (_FakeEnt("priest", 0x2004, "hero", cls="ent.hero.priest",
                  is_hero=True), 6.0),
    ]

    loots = []
    try:
        from farever_companion.data.items import catalog as _cat
        tiers = ("Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic")
        picks = {}
        for it in _cat.items():
            r = it.get("rarity")
            if r in picks or r not in tiers:
                continue
            iid = it.get("id")
            if iid and icons.has_icon("item", iid):
                picks[r] = iid
            if len(picks) == len(tiers):
                break
        # Force the Legendary slot to the requested demo weapon.
        picks["Legendary"] = "Daggers_Demondash"
        loots = [
            _FakeDrop(iid, 1, float(1 + i), float(1 + i),
                      rarity=("Legendary" if r == "Legendary" else None))
            for i, (r, iid) in enumerate(picks.items())
        ]
    except Exception:
        loots = []

    foods = [_FakeFood("Feast", 0x4001, 2.0, 2.0)]
    orbs = [
        _DgOrb("Z1_Dungeon_Crabgantua_InstanceOrb_1", 3.0, 3.0, 0.0),
        _DgOrb("Z1_Dungeon_Crabgantua_InstanceOrb_2", 4.0, 4.0, 0.0),
    ]
    return dict(enemies=enemies, players=players, loots=loots,
                foods=foods, orbs=orbs)


class DevEntityHud(EntityOverlay):
    """The REAL Entity HUD, driven by synthetic fixtures instead of the game.

    This is the VM testing ground: it runs the exact same _render_sections /
    RowSpec pipeline the live overlay uses, so icon/asset/colour changes can be
    verified 1:1 without launching the game. Only _refresh is overridden — to
    inject data — never the rendering logic."""

    def __init__(self, settings, parent=None):
        super().__init__(_FakeModel(), settings, parent=None)
        # The dev VM must NOT persist (or sync) borderless state to the shared
        # settings object — otherwise it leaks `dungeon_bare` into the live HUD
        # and saves it to config. Rendering still reads `self.s`, which is left
        # intact; only the persist/sync paths are disabled here.
        self._settings = None
        self._bare_sync_fn = None
        # Embed: strip the top-level window chrome and parent into the dev page.
        self.setParent(parent)
        self.setWindowFlags(QtCore.Qt.Widget)
        self.set_bare(True)
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Preferred)
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        self._fixtures = _sample_fixtures()

    def set_fixtures(self, fx):
        self._fixtures = fx

    def _check_auto_height(self):
        return  # no auto-resize inside the dev scroll area

    def _refresh(self):
        try:
            fx = self._fixtures or _sample_fixtures()
            self._spark_mobs = [(fx["spark"], 12.0)]
            self._enemies = [(e, 24.0) for e in fx["enemies"]]
            self._group_members = [(m, 8.0) for m in fx["players"]]
            self._comps = [(c, 15.0) for c in fx["companions"]]
            self._gatherables = fx["gatherables"]
            self._orbs = [(o, 30.0) for o in fx["orbs"]]
            self._chests = fx["chests"]
            self._static_pois = fx["static_pois"]
            self._loots = fx["loots"]
            self._foods = fx["foods"]
            self._sel = None
            self._owned = None
            profile = self.model.player_profile()
            # Flag the demo "collected" chest/orb so the renamed done sprites
            # (chestopen / orbdone) are exercised by the preview.
            self.model._done = {"chestopen", "orbdone"}
            done_list = ["chestopen"]
            isz = round(self.s.icon_size * self._scale)
            show_e = getattr(self.s, "show_enemies", True)
            show_s = getattr(self.s, "show_spark_mobs", True)
            self._render_sections(profile, done_list, isz, show_e, show_s)
        except Exception:
            import traceback
            traceback.print_exc()


class _IconCell(QtWidgets.QFrame):
    """One icon tile: optional atlas+fallback render, name, missing flag."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(6, 6, 6, 6)
        self._lay.setSpacing(4)
        # Parent every child to THIS frame so they never become top-level
        # (orphan) windows.
        self._atlas = QtWidgets.QLabel(self)
        self._atlas.setAlignment(QtCore.Qt.AlignCenter)
        self._fb = QtWidgets.QLabel(self)
        self._fb.setAlignment(QtCore.Qt.AlignCenter)
        self._name = QtWidgets.QLabel(self)
        self._name.setObjectName("Mono")
        self._name.setAlignment(QtCore.Qt.AlignCenter)
        self._name.setWordWrap(True)
        self._tag = QtWidgets.QLabel(self)
        self._tag.setAlignment(QtCore.Qt.AlignCenter)

    def set_cell(self, name, atlas_pm, fb_pm, present_atlas, present_fb,
                 compare, show_fb):
        self._name.setText(name)
        if compare:
            self._atlas.setPixmap(atlas_pm or QtGui.QPixmap())
            self._fb.setPixmap(fb_pm or QtGui.QPixmap())
            self._atlas.setVisible(True)
            self._fb.setVisible(True)
            self._tag.setText("atlas / fallback")
            self._tag.setStyleSheet("color:#87929a;font-size:9px;")
            if not present_atlas:
                self._atlas.setText("—")
            if not present_fb:
                self._fb.setText("—")
            while self._lay.count():
                self._lay.takeAt(0)
            self._lay.addWidget(self._atlas)
            self._lay.addWidget(self._fb)
            self._lay.addWidget(self._name)
            self._lay.addWidget(self._tag)
            missing = (not present_atlas) and (not present_fb)
        else:
            pm = fb_pm if show_fb else atlas_pm
            self._atlas.setPixmap(pm or QtGui.QPixmap())
            present = present_fb if show_fb else present_atlas
            self._atlas.setVisible(True)
            self._fb.setVisible(False)
            self._tag.setText("missing" if not present else "")
            self._tag.setStyleSheet(
                "color:#ff6b6b;font-size:9px;" if not present
                else "color:#87929a;font-size:9px;")
            if not present:
                self._atlas.setText("?")
            while self._lay.count():
                self._lay.takeAt(0)
            self._lay.addWidget(self._atlas)
            self._lay.addWidget(self._name)
            self._lay.addWidget(self._tag)
            missing = not present
        if missing:
            self.setStyleSheet(
                "QFrame#Card{border:1px solid #ff6b6b;background:%s;}" % theme.PANEL)
        else:
            self.setStyleSheet(
                "QFrame#Card{border:1px solid %s;background:%s;}" % (theme.BORDER, theme.PANEL))


class DevIconsPage(QtWidgets.QWidget):
    """Icon-preview dev tool (see module docstring)."""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.s = settings
        self._source = "atlas"      # "atlas" | "fallback"
        self._compare = False
        self._mode = "entity"       # entity | minimap | ui
        self._names: list[str] = []
        self._kind_of: dict[str, str] = {}
        self._groups: dict[str, list[RowSpec]] = {}
        self._build_ui()
        self._reload_names()
        self._refresh()

    # -- source switching ------------------------------------------------
    def _with_source(self, source: str, fn):
        prev_b = atlas.bypass()
        prev_o = paths.fallback_override()
        if source == "fallback":
            atlas.set_bypass(True)
            paths.set_fallback_override(_FB_DIR)
        else:
            atlas.set_bypass(False)
            paths.set_fallback_override(None)
        try:
            return fn()
        finally:
            atlas.set_bypass(prev_b)
            paths.set_fallback_override(prev_o)

    def _sheet_id(self, name):
        return self._kind_of.get(name, "units|").split("|", 1)

    def _render(self, name: str, kind: str, source: str, size: int = 48):
        sheet, _id = (None, None)
        if kind == "sheet":
            sheet, _id = self._sheet_id(name)
        src_kind = kind

        def fn():
            if src_kind == "marker":
                return icons.marker(name, size, accent=None)
            if src_kind == "ui":
                return icons.ui_icon(name, "#ffffff", size)
            if src_kind == "sheet":
                return icons.tile(sheet, _id, size, theme.ACCENT)
            return icons.marker(name, size)
        return self._with_source(source, fn)

    def _present(self, name: str, kind: str, source: str) -> bool:
        if kind == "sheet":
            sheet, _id = self._sheet_id(name)
            return self._with_source(
                source, lambda: atlas.find_entry(sheet, _id) is not None)
        pm = self._render(name, kind, source, size=16)
        return not _is_blank(pm)

    # -- name lists ------------------------------------------------------
    def _reload_names(self):
        self._names = []
        self._kind_of = {}
        self._rows = []
        if self._mode == "minimap":
            names = set()
            try:
                from farever_companion.ui.overlays import minimap_render as mr
                # Only the real logical->icon mappings the game actually uses.
                names.update(mr._MARKER.values())
                # Done-state variants come straight from the shared DONE_MARKER
                # map, so they always match the real HUD naming (no hardcoding
                # to drift). Update DONE_MARKER and every view follows.
                for _d in mr.DONE_MARKER.values():
                    names.add(_d)
            except Exception:
                pass
            # Real compiled sprites — the source of truth for what exists.
            try:
                names.update(atlas.data().get(_MARKER_SHEET, {}).keys())
            except Exception:
                pass
            for n in sorted(names):
                self._names.append(n)
                self._kind_of[n] = "marker"
        elif self._mode == "ui":
            seen = set()
            for d in (_FB_DIR, paths.fallback_icons_dir(),
                      paths.assets_dir() / "map_icons"):
                if not d.exists():
                    continue
                for f in d.iterdir():
                    if f.suffix.lower() in (".svg", ".webp", ".png"):
                        if f.stem.lower() in seen:
                            continue
                        seen.add(f.stem.lower())
                        self._names.append(f.stem)
                        self._kind_of[f.stem] = "ui"



    def _real_loot_specs(self) -> list:
        """Exactly ONE real item per rarity tier (Common -> Mythic) rendered
        through the live HUD's own builder, so the replica shows the app's
        rarity colors 1:1 without dumping hundreds of rows."""
        from farever_companion.ui.overlays.loot_rows import build_loot_specs
        from farever_companion.data.items import catalog

        tiers = ["Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic"]
        picks: dict[str, str] = {}
        for it in catalog.items():
            rar = it.get("rarity")
            if rar in picks or rar not in tiers:
                continue
            iid = it.get("id")
            if iid and icons.has_icon("item", iid):
                picks[rar] = iid
            if len(picks) == len(tiers):
                break

        class _Drop:
            def __init__(self, iid):
                self.item_id = iid
                self.count = 1
                self.x = self.y = self.z = 0.0
            def dist2d(self, x, y):
                return 0.0

        drops = [_Drop(iid) for iid in picks.values()]
        if not drops:
            return []
        return build_loot_specs(
            drops, (0.0, 0.0, 0.0), "Off",
            is_tracked=lambda *a: False,
            track=lambda *a: None,
            accent=theme.ACCENT)


    # -- UI --------------------------------------------------------------
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QtWidgets.QFrame()
        bar.setObjectName("Card")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(10)

        self._src_lbl = _LinkLabel()
        self._src_lbl.clicked.connect(self._toggle_source)
        bl.addWidget(self._src_lbl)

        self._cmp_lbl = _LinkLabel()
        self._cmp_lbl.clicked.connect(self._toggle_compare)
        bl.addWidget(self._cmp_lbl)

        self._mode_cb = QtWidgets.QComboBox()
        self._mode_cb.addItems(["Entity HUD (1:1)", "Dungeon HUD (1:1)",
                                "Top DPS HUD (1:1)", "Test Dummy HUD (1:1)",
                                "Run Timer HUD (1:1)",
                                "Minimap markers", "Minimap viewer", "UI icons"])
        self._mode_cb.currentTextChanged.connect(self._on_mode)
        bl.addWidget(self._mode_cb)

        self._reload_lbl = _LinkLabel("reload")
        self._reload_lbl.clicked.connect(self._on_reload)
        bl.addWidget(self._reload_lbl)

        self._reload_hud_lbl = _LinkLabel("reload HUD")
        self._reload_hud_lbl.clicked.connect(self._on_reload_hud_code)
        bl.addWidget(self._reload_hud_lbl)

        root.addWidget(bar)

        self._status = QtWidgets.QLabel()
        self._status.setObjectName("Mono")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._body = QtWidgets.QWidget()
        self._body_lay = QtWidgets.QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(12, 12, 12, 12)
        self._body_lay.setSpacing(10)
        self._entity_hud = None
        self._dungeon_hud = None
        self._dps_hud = None
        self._dummy_hud = None
        self._speedrun_hud = None
        self._sr_open = 0          # SpeedRun tab opens: walks _SR_SCENARIOS
        self._minimap_viewer = None
        self._scroll.setWidget(self._body)
        root.addWidget(self._scroll, 1)

        self._update_labels()

    def _update_labels(self):
        self._src_lbl.setText(
            f"source: {self._source}  (click to switch)")
        self._cmp_lbl.setText(
            f"compare: {'on' if self._compare else 'off'}  (click)")

    def _toggle_source(self):
        self._set_source("fallback" if self._source == "atlas" else "atlas")

    def _toggle_compare(self):
        self._on_compare(not self._compare)

    def _set_source(self, src):
        if self._source == src:
            return
        self._source = src
        self._update_labels()
        if src == "fallback":
            atlas.set_bypass(True)
            paths.set_fallback_override(_FB_DIR)
        else:
            atlas.set_bypass(False)
            paths.set_fallback_override(None)
        icons.trim_caches()
        self._refresh()

    def _on_compare(self, on):
        self._compare = on
        self._update_labels()
        self._refresh()

    def _on_mode(self, text):
        mapping = {
            "Entity HUD (1:1)": "entity",
            "Dungeon HUD (1:1)": "dungeon",
            "Top DPS HUD (1:1)": "dps",
            "Test Dummy HUD (1:1)": "dummy",
            "Run Timer HUD (1:1)": "speedrun",
            "Minimap markers": "minimap",
            "Minimap viewer": "minimapmap",
            "UI icons": "ui",
        }
        self._mode = mapping.get(text, "entity")
        self._reload_names()
        self._refresh()

    def _on_reload(self):
        icons.trim_caches()
        atlas.set_bypass(False)
        paths.set_fallback_override(None)
        atlas.data.cache_clear()
        self._reload_names()
        self._refresh()

    def _on_reload_hud_code(self):
        """Hot-reload the HUD rendering modules so Python edits to the live
        overlays show up in the preview without restarting the whole app.

        Reloads the render chain in leaf-first order (so each overlay picks up
        its reloaded helpers), rebinds the embedded dev HUD subclasses' base
        classes to the fresh ones, then rebuilds the current preview."""
        mods = [
            "farever_companion.ui.overlays.entity_render",
            "farever_companion.ui.overlays.minimap_render",
            "farever_companion.ui.overlays.loot_rows",
            "farever_companion.ui.overlays.entity_overlay",
            "farever_companion.ui.overlays.dungeon_overlay",
            "farever_companion.ui.overlays.dummy_overlay",
            "farever_companion.ui.overlays.speedrun_overlay",
        ]
        try:
            for m in mods:
                if m in sys.modules:
                    importlib.reload(sys.modules[m])
                else:
                    importlib.import_module(m)
            from farever_companion.ui.overlays.entity_overlay import EntityOverlay as _EO
            from farever_companion.ui.overlays.dungeon_overlay import DungeonOverlay as _DO
            from farever_companion.ui.overlays.dummy_overlay import DummyOverlay as _DUO
            from farever_companion.ui.overlays.speedrun_overlay import SpeedrunOverlay as _SO
            DevEntityHud.__bases__ = (_EO,)
            DevDungeonHud.__bases__ = (_DO,)
            DevDummyHud.__bases__ = (_DUO,)
            DevSpeedrunHud.__bases__ = (_SO,)
            self._status.setText("HUD code reloaded — rebuilding preview")
            self._refresh()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._status.setText(f"HUD reload failed: {exc}")

    # -- render ----------------------------------------------------------
    def _embed_hud(self, hud) -> None:
        """Embed a HUD at the size it takes in game, then render it.

        A top-level overlay owns its size: `apply_scale` in __init__ sets
        base * scale, and the content-driven HUDs grow that to fit their rows.
        Dropped into this page's body layout with nothing constraining it, Qt
        hands the widget the whole body instead — measured 2026-09-25, the
        SpeedRun HUD (330x200 in game) and the Entity HUD both rendered
        1376x822, i.e. full-bleed rather than 1:1. Pin the size the overlay
        reports for itself and sit at the top-left, as the floating window
        sits on the game.

        The pin runs twice: the content-driven HUDs (entity, dungeon) measure
        their body before their rows have been through a layout pass, so their
        first reading is short (the entity HUD read 710 where it settles at
        911). The live window gets that pass from the event loop before anyone
        sees it; here the deferred call is that pass.
        """
        self._body_lay.addWidget(hud, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        hud._refresh()
        self._pin_hud(hud)
        QtCore.QTimer.singleShot(
            0, lambda h=hud: self._pin_hud(h, restate=True))

    def _pin_hud(self, hud, restate: bool = False) -> None:
        """Freeze an embed at its in-game size."""
        try:
            hud.setFixedSize(hud.ingame_size())
        except RuntimeError:
            return            # the tab was switched away mid-turn
        if restate:
            self._set_hud_status()

    def _set_hud_status(self) -> None:
        """Status line for the embedded HUD modes: its live size + wiring."""
        names = {"entity": "Entity HUD", "dungeon": "Dungeon HUD",
                 "dps": "Top DPS HUD", "dummy": "Test Dummy HUD",
                 "speedrun": "Run Timer HUD"}
        hud = {"entity": self._entity_hud, "dungeon": self._dungeon_hud,
               "dps": self._dps_hud, "dummy": self._dummy_hud,
               "speedrun": self._speedrun_hud}.get(self._mode)
        if hud is None or self._mode not in names:
            return
        size = hud.ingame_size()
        extra = (f" · {hud.scenario_label()}" if self._mode == "speedrun"
                 else "")
        self._status.setText(
            f"{names[self._mode]} (live code) · 1:1 {size.width()}x{size.height()}"
            f"{extra} · source={self._source} · fallback={_FB_DIR.name}")

    def _refresh(self):
        try:
            self._do_refresh()
        except Exception as exc:  # never let a render glitch kill the page
            import traceback
            traceback.print_exc()
            lbl = QtWidgets.QLabel(f"Preview error:\n{exc}")
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            while self._body_lay.count():
                w = self._body_lay.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            self._body_lay.addWidget(lbl)

    def _do_refresh(self):
        if self._mode in ("entity", "dungeon", "dps", "dummy", "speedrun", "minimapmap"):
            # Embed the REAL HUD / viewer (VM). We only supply data, never logic.
            # Patches stay installed for the HUD-tab sessions so any later
            # re-render (resize, randomize) still resolves our demo names.
            if self._mode != "minimapmap":
                _install_dev_patches()
            # Clear any previously embedded widget (entity <-> dungeon <-> map).
            self._entity_hud = None
            self._dungeon_hud = None
            self._dps_hud = None
            self._dummy_hud = None
            self._speedrun_hud = None
            self._minimap_viewer = None
            while self._body_lay.count():
                w = self._body_lay.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            if self._mode == "entity":
                self._entity_hud = DevEntityHud(self.s, self)
                self._embed_hud(self._entity_hud)
                self._set_hud_status()
            elif self._mode == "dungeon":
                self._dungeon_hud = DevDungeonHud(self.s, self)
                self._embed_hud(self._dungeon_hud)
                self._set_hud_status()
            elif self._mode == "dps":
                self._dps_hud = DevDpsHud(self.s, self)
                self._embed_hud(self._dps_hud)
                self._set_hud_status()
            elif self._mode == "dummy":
                self._dummy_hud = DevDummyHud(self.s, self)
                self._embed_hud(self._dummy_hud)
                self._set_hud_status()
            elif self._mode == "speedrun":
                self._sr_open += 1
                self._speedrun_hud = DevSpeedrunHud(
                    self.s, self,
                    scenario=_SR_SCENARIOS[(self._sr_open - 1) % len(_SR_SCENARIOS)])
                self._speedrun_hud.frozen.connect(self._set_hud_status)
                self._embed_hud(self._speedrun_hud)
                self._set_hud_status()
            else:
                self._minimap_viewer = DevMinimapViewer(self._source, self.s, self)
                self._body_lay.addWidget(self._minimap_viewer)
                self._status.setText(
                    f"Minimap viewer · source={self._source} · "
                    f"fallback={_FB_DIR.name}")
            return
        else:
            # Leaving a HUD mode: drop the embedded instance.
            _remove_dev_patches()
            self._entity_hud = None
            self._dungeon_hud = None
            self._dps_hud = None
            self._dummy_hud = None
            self._speedrun_hud = None
            self._minimap_viewer = None

        while self._body_lay.count():
            w = self._body_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(8)
        cols = 6
        r = c = 0
        shown = 0
        total = 0
        cap = 600  # never render thousands of sheet icons at once (freeze guard)
        for name in self._names:
            total += 1
            if total > cap:
                break
            kind = self._kind_of.get(name, "marker")
            if self._compare:
                a_pm = self._render(name, kind, "atlas", 44)
                f_pm = self._render(name, kind, "fallback", 44)
                pres_a = self._present(name, kind, "atlas")
                pres_f = self._present(name, kind, "fallback")
                cell = _IconCell(self)
                cell.set_cell(name, a_pm, f_pm, pres_a, pres_f, True, False)
            else:
                src = self._source
                pm = self._render(name, kind, src, 44)
                pres = self._present(name, kind, src)
                cell = _IconCell(self)
                cell.set_cell(name, pm, None, pres, pres, False,
                              src == "fallback")
            grid.addWidget(cell, r, c)
            c += 1
            if c >= cols:
                c = 0
                r += 1
            shown += 1

        holder = QtWidgets.QWidget(self)
        holder.setLayout(grid)
        self._body_lay.addWidget(holder)
        if total > cap:
            note = QtWidgets.QLabel(
                f"Showing first {shown} of {total} matches — type in the filter "
                f"to narrow (e.g. a specific item/unit id).")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self._body_lay.addWidget(note)
        self._body_lay.addStretch(1)
        self._status.setText(
            f"{shown} icons · {self._mode} · "
            f"{'atlas<->fallback' if self._compare else 'source=' + self._source} "
            f"· fallback={_FB_DIR.name}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for hud in (getattr(self, "_entity_hud", None),
                    getattr(self, "_dungeon_hud", None),
                    getattr(self, "_dummy_hud", None)):
            if hud is not None and self.isVisible():
                try:
                    hud._refresh()
                except Exception:
                    pass

    # Keep the global icon-source switch from leaking into the rest of the app:
    # reset to defaults when hidden, re-apply the chosen source when shown.
    def hideEvent(self, e):
        atlas.set_bypass(False)
        paths.set_fallback_override(None)
        _remove_dev_patches()
        super().hideEvent(e)

    def showEvent(self, e):
        if (self._mode in ("entity", "dungeon")
                and (getattr(self, "_entity_hud", None)
                     or getattr(self, "_dungeon_hud", None)) is not None):
            _install_dev_patches()
        if self._source == "fallback":
            atlas.set_bypass(True)
            paths.set_fallback_override(_FB_DIR)
        else:
            atlas.set_bypass(False)
            paths.set_fallback_override(None)
        super().showEvent(e)


class DevDungeonHud(DungeonOverlay):
    """The REAL Dungeon HUD, driven by synthetic fixtures instead of the game.

    Like DevEntityHud this is the VM testing ground: it runs the exact same
    _render_dg_sections / RowSpec pipeline the live dungeon overlay uses, so
    icon/asset/colour changes can be verified 1:1 without launching the game.
    Only _refresh is overridden — to inject data — never the rendering logic."""

    def __init__(self, settings, parent=None):
        self._fixtures = None
        super().__init__(_FakeModel(), settings, parent=None)
        # Mirror the DevEntityHud guard: stop any geometry/bare persistence from
        # leaking the dev preview's state into the real dungeon config.
        self._settings = None
        self._bare_sync_fn = None
        self.setParent(parent)
        self.setWindowFlags(QtCore.Qt.WindowType.Widget)

    def set_fixtures(self, fx):
        self._fixtures = fx

    def _refresh(self):
        try:
            fx = self._fixtures or _sample_dungeon_fixtures()
            # Show every section for a complete offline preview.
            self.s.dungeon_show_players = True
            self.s.dungeon_show_orbs = True
            self._dg_xyz = (0.0, 0.0, 0.0)
            self._dg_enemies = fx["enemies"]
            self._dg_players = fx["players"]
            self._dg_loot = fx["loots"]
            self._dg_food = fx["foods"]
            self._dg_orbs = fx["orbs"]
            self._dg_hp_peaks = {}
            self._dg_death_counts = {}
            self._render_dg_sections(self.model.player_profile())
        except Exception:
            import traceback
            traceback.print_exc()


# --- Top DPS HUD VM --------------------------------------------------------

_DPS_FAKES = None


def _dps_fakes():
    """The shared DPS test fakes (tests/dps_fakes.py), imported on demand.

    Same LiveModel stand-in the suite drives the meter with, so the preview
    can never drift from what the tests actually exercise. Imported lazily
    and with QT_QPA_PLATFORM restored: that module defaults the Qt platform
    to offscreen for the suite, and this page runs INSIDE the app, where
    forcing it would make the whole companion windowless.
    """
    global _DPS_FAKES
    if _DPS_FAKES is None:
        import importlib
        import os
        prev = os.environ.get("QT_QPA_PLATFORM")
        try:
            _DPS_FAKES = importlib.import_module("tests.dps_fakes")
        finally:
            if prev is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = prev
    return _DPS_FAKES


class _DpsFakeSource:
    """DamageSourceManager stand-in: a fixed event ring + live status."""

    def __init__(self, events):
        self._evs = list(events)
        self._status = "live"
        self._calib_s = 0.4
        self._map_s = 0.2
        self._first_event_s = 1.1
        self.damage_stale = False
        self.has_decoded = True

    def set_events(self, events):
        self._evs = list(events)

    def ensure_started(self):
        pass

    def events_after(self, cursor):
        evs = self._evs[cursor:]
        return len(self._evs), evs

    def timing_text(self):
        return (f"calib {self._calib_s:.1f}s · map {self._map_s:.1f}s · "
                f"1st event {self._first_event_s:.1f}s")

    def status(self):
        return self._status

    def counts(self):
        return 1, 0

    def live_source_name(self):
        return "damage numbers"

    def diagnostics(self):
        return {}


def _dps_fake_model():
    """The shared tests/dps_fakes model, dressed as this page's fight: a
    two-player party (Me + Brit) with a live event ring, and NO game combat
    state (the fake's default reports in_combat, which would light the
    meter's own combat badge)."""
    f = _dps_fakes()
    return f._FakeModel(
        names={f.PA: "Me", f.ALLY: "Brit"},
        damage=_DpsFakeSource(_sample_dps_events()),
        combat_state=None,
        roster=GroupSnapshot(group=0x5000, members=[
            GroupMember(player=0, hero=f.ALLY, name="Brit"),
            GroupMember(player=0, hero=f.PA, name="Me", is_me=True),
        ]),
        units=[
            f._hero(f.PA, cls="ent.hero.Warrior", hp=1000.0),
            f._hero(f.ALLY, cls="ent.hero.Priest", hp=1000.0),
            f._foe(f.FOE, "Trash_Pig", hp=100.0),
        ],
    )


def _sample_dps_events():
    """Synthetic damage feed: Me (10 skills) vs Brit (4 skills).

    Tagged with affinities so the rows' TYPE / PD-MD cells render (base
    attacks read Physical, the rest Fire) — the same split the Test Dummy
    preview exercises.
    """
    import time
    f = _dps_fakes()
    t0 = time.time()
    evs = []
    for i, amt in enumerate((12000, 9000, 7000, 5000, 3000, 2000, 1000, 500, 300, 100)):
        evs.append(DamageEvent(amount=amt, skill=f"Skill_{i + 1}",
                               source_addr=f.PA, target_addr=f.FOE, t=t0 + i * 0.01,
                               affinity="Physical" if i < 2 else "Fire"))
    for i, amt in enumerate((8000, 6000, 4000, 2000)):
        evs.append(DamageEvent(amount=amt, skill=f"AllySkill_{i + 1}",
                               source_addr=f.ALLY, target_addr=f.FOE, t=t0 + 0.1 + i * 0.01,
                               affinity="Physical" if i < 1 else "Light"))
    return evs


class DevDpsHud(DpsOverlay):
    """The REAL Top DPS HUD, driven by synthetic combat events instead of the
    game. Same 1:1 VM contract as DevEntityHud / DevDungeonHud: the exact live
    _tick -> tracker.update -> _update_meters pipeline runs, only the data
    source is fake. Me starts expanded (10 skills), Brit collapsed."""

    def __init__(self, settings, parent=None):
        self._fixtures = None
        super().__init__(_dps_fake_model(), settings, parent=None)
        # Mirror the DevEntityHud guard: no geometry/bare persistence leaks.
        self._settings = None
        self._bare_sync_fn = None
        self.setParent(parent)
        self.setWindowFlags(QtCore.Qt.WindowType.Widget)
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        # Preview the expanded look: the skills table (with its TYPE / PD-MD
        # cells) is what this page exists to inspect, and the live default
        # (grouped meters collapse every row) would hide it behind a click.
        # The row's own chevron still works; this only sets the INITIAL look.
        self._row_collapsed["Me (You)"] = False
        # No size pin here: the real meter sizes itself (320x420 in its own
        # __init__) and the page's _embed_hud freezes that in-game size.

    def set_fixtures(self, fx):
        self._fixtures = fx

    def _refresh(self):
        try:
            if self._fixtures is not None:
                self.model.damage.set_events(self._fixtures)
                self.reset()          # fresh session so events re-flow
            self._tick()
            # The embed ticks this instance before layout activation shows
            # it, and _tick returns early while hidden — so the first pass
            # drains the demo events while the rows stay unrendered. The
            # preview timer is stopped, so nothing would re-tick (the same
            # trap DevDummyHud._retick_once fixes for the dummy board).
            # One deferred pass lands after the show and paints the meter.
            QtCore.QTimer.singleShot(0, self._retick_once)
        except Exception:
            import traceback
            traceback.print_exc()

    def _retick_once(self) -> None:
        try:
            if self.isVisible():
                self._tick()
        except RuntimeError:
            pass            # the tab was switched away mid-turn


# --- Test Dummy HUD VM -----------------------------------------------------

def _sample_dummy_events():
    """Synthetic hits on the training dummy: Me (10 skills), one session.

    Tagged with affinities so the board's PD / MD split renders: base attacks
    read Physical, the rest Fire."""
    import time
    f = _dps_fakes()
    t0 = time.time()
    evs = []
    for i, amt in enumerate((12000, 9000, 7000, 5000, 3000, 2000, 1000, 500, 300, 100)):
        evs.append(DamageEvent(amount=amt, skill=f"Skill_{i + 1}",
                               source_addr=f.PA, target_addr=f.FOE, t=t0 + i * 0.01,
                               affinity="Physical" if i < 4 else "Fire"))
    return evs


class DevDummyHud(DummyOverlay):
    """The REAL Test Dummy HUD, driven by synthetic dummy hits instead of the
    game. Same 1:1 VM contract as DevDpsHud: the exact live _tick pipeline
    runs, only the data source is fake. Me's 10-skill parse lands on the
    training dummy, so the target bar, countdown and skill rows all render."""

    def __init__(self, settings, parent=None):
        f = _dps_fakes()
        model = f._FakeModel(
            names={f.PA: "Me", f.ALLY: "Brit"},
            combat_state=None,
            roster=GroupSnapshot(group=0x5000, members=[
                GroupMember(player=0, hero=f.ALLY, name="Brit"),
                GroupMember(player=0, hero=f.PA, name="Me", is_me=True),
            ]),
            units=[
                f._hero(f.PA, cls="ent.hero.Warrior", hp=1000.0),
                f.training_dummy(f.FOE, uid="Dummy", hp=100000.0),
            ],
        )
        for ev in _sample_dummy_events():
            model.damage.push(ev)
        super().__init__(model, settings, parent=None)
        # Mirror the DevEntityHud guard: no geometry/bare persistence leaks.
        self._settings = None
        self._bare_sync_fn = None
        self.setParent(parent)
        self.setWindowFlags(QtCore.Qt.WindowType.Widget)
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()

    def _refresh(self):
        try:
            self._tick()
            # The embed ticks this instance before layout activation shows
            # it, so the first pass only drains events while the labels stay
            # blank — and the preview timer is stopped, so nothing re-ticks.
            # One deferred pass lands after the show and paints the board.
            QtCore.QTimer.singleShot(0, self._retick_once)
        except Exception:
            import traceback
            traceback.print_exc()

    def _retick_once(self) -> None:
        try:
            if self.isVisible():
                self._tick()
        except RuntimeError:
            pass            # the tab was switched away mid-turn


# --- SpeedRun HUD VM -------------------------------------------------------

# A REAL dungeon boss id (data/codex "Bosses", which data.encounters resolves),
# so the HUD's own name and unit-icon lookups resolve exactly as they do live.
_SR_BOSS = "Crabgantua"
_SR_FULL_PB = 92.40     # seconds: the RUN tile's PB and the pace it is read against
_SR_BOSS_PB = 41.80     # seconds: the BOSS split carries its own PB

# The fight as the HUD's own detector should see it: (delay after the previous
# beat, boss HP). "kill"'s second beat is the death the run ends on.
_SR_SCRIPTS = {
    "running": ((700, 88.0),),
    "kill": ((700, 88.0), (900, 0.0)),
}

# Both scenarios, walked one per tab open (the page owns the counter — the HUD
# is rebuilt on every entry, so a counter on the instance never advanced and
# the finished "kill" state was unreachable).
_SR_SCENARIOS = ("running", "kill")


class _SpeedrunFakeSettings:
    """Settings stand-in for the SpeedRun VM: PBs live in memory, never on disk.

    Accents and bare flags come from the real settings (everything else is
    delegated), but the PB store and the kill log are OURS: a synthetic kill
    must never land in the player's profile or in the shared kill history.
    """

    def __init__(self, real):
        self._real = real
        self.best = {_SR_BOSS: _SR_FULL_PB}
        self.boss_best = {_SR_BOSS: _SR_BOSS_PB}
        self.history = []

    def get_speedrun_best(self, profile):
        return dict(self.best)

    def save_speedrun_best(self, profile, best):
        self.best = dict(best)

    def get_speedrun_boss_best(self, profile):
        return dict(self.boss_best)

    def save_speedrun_boss_best(self, profile, best):
        self.boss_best = dict(best)

    def get_speedrun_history(self):
        return list(self.history)

    def save_speedrun_history(self, rows):
        self.history = list(rows)

    def save(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


class _SpeedrunFakeModel:
    """LiveModel stub for the SpeedRun VM: the instance flag + one boss fight.

    Only the reads the overlay's own tick makes are implemented — the zone edge,
    the encounter feed and the kill detection are the LIVE code, untouched.
    """

    dps = None                  # no tracker: no wipe marker, no hero HP read

    def __init__(self, boss_id=_SR_BOSS):
        self._boss = boss_id
        self.reset()

    def reset(self):
        self.inside = True      # the scripted run starts INSIDE the instance
        self.present = True
        self.hp = 100.0
        self.player_addr = 0    # no hero address -> no death edge

    def encounter_state(self):
        return ({self._boss}, self._boss,
                [(self._boss, self.present, self.hp)], False)

    def is_in_dungeon_or_rift(self):
        return self.inside

    def is_in_dungeon(self):
        return self.inside

    def is_in_rift(self):
        return False

    def dungeon_mode(self):
        return "hard"

    def detected_mode(self):
        return "hard"

    def player_xyz(self):
        return (0.0, 0.0, 0.0)      # standing still: no re-arm teleport

    def player_profile(self):
        return "DevRun"

    def player_name(self, addr):
        return "MunsterChuck"

    def player_class(self):
        return "Rogue"


class DevSpeedrunHud(SpeedrunOverlay):
    """The REAL SpeedRun HUD, driven by a scripted dungeon instead of the game.

    Same 1:1 VM contract as DevEntityHud / DevDungeonHud / DevDpsHud — the live
    ZoneEdge -> Encounter -> feed_boss -> PB pipeline runs untouched, only the
    model is fake. Its subject IS a state machine, though, so the preview is
    scripted rather than static: opening the tab primes the zone edge and the
    HUD's own tick starts RUN, then a beat later the first damage arms the BOSS
    split. Two scenarios alternate on each open — "running" counts both clocks
    for a moment (watch the pace row move against the seeded PB) and "kill"
    drops the boss so the finished gold NEW BEST tile and its LAST row render.

    The clocks then FREEZE (see `frozen`): one live tick is a full re-render —
    every label re-styled, then a fit resize — and at 50 ms inside this page
    that repaints the whole tab, which reads as the UI flashing. Freezing keeps
    the last rendered state (the digits a real fight would be showing) and
    re-opening the tab replays it.
    """

    # How long the live poll keeps running after the last scripted beat, so the
    # frozen digits are a mid-fight value rather than ~00:00.70. A run that has
    # already FINISHED (the kill scenario) has nothing left to watch, so it only
    # needs a beat for its last frame to land.
    _SETTLE_MS = 2500
    _SETTLE_DONE_MS = 500

    frozen = QtCore.Signal()

    def __init__(self, settings, parent=None, scenario: str = _SR_SCENARIOS[0]):
        self._model = _SpeedrunFakeModel()
        self._scenario = (scenario if scenario in _SR_SCENARIOS
                          else _SR_SCENARIOS[0])
        self._beats = []
        super().__init__(self._model, _SpeedrunFakeSettings(settings),
                         parent=None)
        # Mirror the DevEntityHud guard: no geometry/bare persistence here, and
        # no PB or kill-log write can reach the real profile (the PB store is
        # the in-memory stand-in above).
        self._settings = None
        self._bare_sync_fn = None
        self.setParent(parent)
        self.setWindowFlags(QtCore.Qt.WindowType.Widget)
        self._script = QtCore.QTimer(self)
        self._script.setSingleShot(True)
        self._script.timeout.connect(self._beat)
        self._settle = QtCore.QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._freeze_clocks)

    def scenario_label(self):
        clocks = "live" if self._poll.isActive() else "frozen"
        return (f"scenario={self._scenario} · clocks {clocks} "
                f"(re-open the tab to replay)")

    def _freeze_clocks(self) -> None:
        """Stop the preview's 50 ms re-render; the last frame stays on screen."""
        self._poll.stop()
        self.frozen.emit()

    def _refresh(self):
        """Replay this instance's scenario: zone-in -> first damage -> (kill)."""
        try:
            self._poll.start()            # un-freeze: a replay is live again
            self._model.reset()
            self.s.best = {_SR_BOSS: _SR_FULL_PB}       # in-memory PBs only
            self.s.boss_best = {_SR_BOSS: _SR_BOSS_PB}
            self.s.history = []
            # Prime the zone edge, then let the HUD's own tick start the run.
            # Proven outside -> the next inside tick is a zone-in (the VM has
            # never been outside; without this `None` would only arm).
            self.reset(prime_zone=True)
            self._tick()                  # the zone-in edge fires: RUN starts
            # A second tick WITHOUT damage: the encounter tracker folds the boss
            # in at FULL HP, which is the peak its first hit is measured against.
            # Skip it and the split never arms - the same reason a live fight has
            # to be watched before it is hit, and the tick that starts the run
            # deliberately clears whatever fight it walked in on.
            self._tick()
            self._beats = list(_SR_SCRIPTS[self._scenario])
            self._play()
        except Exception:
            import traceback
            traceback.print_exc()

    def _play(self):
        """Queue the next beat. Each beat waits its own delay first, so the
        fight is watched at full HP for a moment — exactly as in game — before
        the first hit lands."""
        if self._beats:
            self._script.start(self._beats[0][0])

    def _beat(self):
        """Apply the next scripted hit, then queue the one after it."""
        try:
            if not self._beats:
                return
            _delay, hp = self._beats.pop(0)
            self._model.hp = hp             # first damage, or the kill
            self._tick()
            self._play()
            if not self._beats:             # fight over: let it run, then freeze
                # A run that has already FINISHED (the kill scenario — `last`
                # is the clock's frozen result; the state itself is back to
                # READY by now, the overlay re-arms on a finish) has nothing
                # left to watch, so it only needs its last frame to land.
                finished = self.timer.last is not None
                self._settle.start(self._SETTLE_DONE_MS if finished
                                   else self._SETTLE_MS)
        except Exception:
            import traceback
            traceback.print_exc()


class _DevMapCanvas(QtWidgets.QWidget):
    """Map background with the marker sprites scattered as clickable pins."""

    marker_clicked = QtCore.Signal(int)

    def __init__(self, markers, parent=None):
        super().__init__(parent)
        self._markers = markers
        self._sel = -1
        from farever_companion.ui.overlays import minimap_render as mr
        self._map_pm = mr.map_pixmap()
        self.setMinimumSize(380, 380)
        self._layout_markers()

    def set_selected(self, idx):
        self._sel = idx
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_markers()

    def _layout_markers(self):
        w, h = max(1, self.width()), max(1, self.height())
        n = len(self._markers)
        if n == 0:
            return
        cols = max(1, int(math.ceil(math.sqrt(n))))
        rows = math.ceil(n / cols)
        cw, ch = w / cols, h / rows
        for i, m in enumerate(self._markers):
            r, c = divmod(i, cols)
            m["x"] = int(cw * (c + 0.5))
            m["y"] = int(ch * (r + 0.5))

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#0a0f12"))
        if self._map_pm is not None and not self._map_pm.isNull():
            scaled = self._map_pm.scaled(
                self.width(), self.height(),
                QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            p.drawPixmap((self.width() - scaled.width()) // 2,
                        (self.height() - scaled.height()) // 2, scaled)
        for i, m in enumerate(self._markers):
            pm = m["pm"]
            if i == self._sel:
                rad = max(pm.width(), pm.height())
                p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 2))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawEllipse(m["x"] - rad, m["y"] - rad, rad * 2, rad * 2)
            p.drawPixmap(m["x"] - pm.width() // 2, m["y"] - pm.height() // 2, pm)

    def mousePressEvent(self, e):
        pos = e.position()
        best, bd = -1, 1e9
        for i, m in enumerate(self._markers):
            d = math.hypot(m["x"] - pos.x(), m["y"] - pos.y())
            if d < bd:
                bd, best = d, i
        if best >= 0 and bd < 48:
            self.marker_clicked.emit(best)


class DevMinimapViewer(QtWidgets.QWidget):
    """Offline clickable minimap viewer.

    Draws every minimap marker type (mob / gathering / dungeon / chest / orb /
    rift / soulstone / ...) as its real sprite on a map background, reusing
    minimap_render.poi_pixmap so the sprites match the live HUD exactly.
    Click a marker to inspect its display name, kind and atlas key."""

    _ENTRIES = [
        ("chest", "", "Chest", False),
        ("chest", "", "Chest (opened)", True),
        ("flower", "lavendula", "Flower", False),
        ("ore", "copperore", "Ore", False),
        ("obelisk", "", "Obelisk", False),
        ("checkpoint", "", "Checkpoint", False),
        ("orb", "", "Secret Orb", False),
        ("orb", "", "Secret Orb (collected)", True),
        ("activity", "", "World Event", False),
        ("dungeon", "", "Dungeon Gate", False),
        ("respawn", "", "Respawn Point", False),
        ("recipe", "", "Recipe", False),
        ("rift", "", "Rift", False),
        ("vendor", "", "Vendor / Shop", False),
        ("soulstone", "", "Soulstone", False),
        ("spark_enemy", "Crab_Z1W_U", "Spark Enemy", False),
        ("enemy", "Wolf_Z1W", "Enemy", False),
        ("companion", "Rabbit_Spark", "Companion", False),
    ]

    def __init__(self, source, settings, parent=None):
        super().__init__(parent)
        self._source = source
        self.s = settings
        self._sel = -1
        self._markers = []
        self._build_markers()
        self._build_ui()

    def _build_markers(self):
        from farever_companion.ui.overlays import minimap_render as mr
        cv = types.SimpleNamespace(s=self.s)
        for kind, label, disp, done in self._ENTRIES:
            try:
                pm = mr.poi_pixmap(cv, kind, label, 36, done=done, tracked=False)
            except Exception:
                pm = None
            if pm is None or pm.isNull():
                continue
            self._markers.append(dict(kind=kind, label=label, disp=disp,
                                      done=done, pm=pm))

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        self._canvas = _DevMapCanvas(self._markers, self)
        self._canvas.marker_clicked.connect(self._on_marker)
        root.addWidget(self._canvas, 1)

        side = QtWidgets.QFrame()
        side.setObjectName("Card")
        side.setFixedWidth(210)
        sv = QtWidgets.QVBoxLayout(side)
        sv.setContentsMargins(8, 8, 8, 8)
        sv.setSpacing(6)
        self._big = QtWidgets.QLabel()
        self._big.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        sv.addWidget(self._big)
        self._info = QtWidgets.QLabel("Click a marker to inspect")
        self._info.setWordWrap(True)
        self._info.setStyleSheet(f"color:{theme.TEXT};")
        sv.addWidget(self._info)
        sv.addStretch(1)
        root.addWidget(side)

        if self._markers:
            self._on_marker(0)

    def _on_marker(self, idx):
        if idx < 0 or idx >= len(self._markers):
            return
        self._sel = idx
        m = self._markers[idx]
        self._canvas.set_selected(idx)
        from farever_companion.ui.overlays import minimap_render as mr
        cv = types.SimpleNamespace(s=self.s)
        big = mr.poi_pixmap(cv, m["kind"], m["label"], 64,
                            done=m["done"], tracked=False)
        key = mr._MARKER.get(m["kind"], m["kind"])
        if m["done"]:
            key = mr.done_marker(key)
        txt = (f"<b>{m['disp']}</b><br>"
               f"<span style='color:{theme.MUTED}'>kind:</span> {m['kind']}<br>"
               f"<span style='color:{theme.MUTED}'>atlas key:</span> {key}")
        if m["label"]:
            txt += f"<br><span style='color:{theme.MUTED}'>id:</span> {m['label']}"
        self._info.setText(txt)
        if big is not None and not big.isNull():
            self._big.setPixmap(big)
        else:
            self._big.clear()
