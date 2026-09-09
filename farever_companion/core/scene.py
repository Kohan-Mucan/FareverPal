"""Live scene reader (read-only, batched).

One batched read_many pulls a header block for every unit at once. An enemy is
anything whose type descends from ent.Foe (HL super-chain) and is not hero-owned.

Verified scene path (Farever EA, 2026-05-24):

    pbase (ent.Hero) +0x58  -> st.GameLayer
    st.GameLayer     +0x128 -> ArrayObj  (units: ent.Hero + ent.Foe subclasses)
    st.GameLayer     +0x120 -> ArrayObj  (elements: interactibles, no units)
    per unit: +0x60 owner, +0x98/A0/A8 xyz (f64), +0x250 unit-id, +0x3D0 attributes
"""
from __future__ import annotations

import math
import re
import struct
import time
from dataclasses import dataclass

from . import attributes
from .hl import Hl, is_ptr
from .proc import Proc, ProcError
from ..data.units import canonical_unit_id
from ..constants import (   # offsets live in one place; re-exported for callers
    OFF_GAMELAYER, OFF_UNITS_ARR, OFF_ELEMS_ARR, OFF_POS,
    OFF_UNITID, OFF_ELEMID, OFF_ELEMSTATE, UNIT_BLOCK,
    OFF_BOX_VALUE, CONFIG_SCAN_BYTES,
    OFF_MAIN_ACTIVITY, OFF_UATTR, OFF_LEVEL_UNIT, OFF_HEALTH,
    OFF_HERO_OWNERPLAYER, OFF_FOE_OWNER,    OFF_RIFT_BOOL, OFF_WORLD_MAPID, OFF_WORLDEVENTS_ARR, OFF_LAST_HEROIC,
    OFF_CONFIG_CANDIDATES, OFF_CONFIG_MAPID_CANDIDATES, OFF_CONFIG_DIFF_CANDIDATES,
    OFF_CONFIG_MAPID, OFF_CONFIG_DIFFICULTY,
    OFF_LOOT_ITEM, OFF_LOOT_COUNT, OFF_ITEM_KIND,
)

_ELEM_KINDS = {
    "ent.interactible.Gatherable": "gatherable",
    "ent.interactible.Chest": "chest",
    "ent.interactible.Obelisk": "obelisk",
    "ent.interactible.Npc": "npc",
    "ent.interactible.Refresher": "refresher",
    "ent.interactible.Bumper": "bumper",
    "ent.interactible.MobilePlatform": "platform",
    "ent.interactible.RespawnPoint": "respawn",
    "ent.interactible.InstanceOrb": "orb",
    "ent.interactible.Teleporter": "dungeon",   # dungeon entrances / teleports
}
PLAYER_OWNER_CLASSES = {"ent.Hero", "ent.hero.Warrior", "ent.hero.Rogue", "ent.hero.Mage", "ent.hero.Priest"}

# --- instance detection vocabulary ------------------------------------------
# GameLayer.mainActivity is the game's OWN activity system, so it is the
# primary instance signal. It is matched by NAME SHAPE and word tokens, never
# by one exact HashLink name: a game update may rename `st.activity.Dungeon`
# (or move it to `st.activity.dungeon.*`, or derive `WolfDungeonInstance`), and
# an exact-name check would leave the instance gate permanently shut - hiding
# the Dungeon HUD and emptying the instance overlays with nothing to explain it.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# The reflected GameLayer field names to try for the activity slot. A patch may
# move a member (the config pointer moved twice) or rename the field; probing
# the documented name first is what survives the first, and the constant + scan
# fallbacks cover the second.
ACTIVITY_FIELD_NAMES = ("mainActivity", "activity", "currentActivity")
# Field names to probe for the rift boolean, in order (the reflected name first,
# then the renames a patch is most likely to use).
RIFT_FIELD_NAMES = ("isRift", "rift", "inRift", "isRiftMap", "riftActive")
HEROIC_FIELD_NAMES = ("lastHeroic", "isHeroic", "heroic", "heroicActive")
# Field names to probe for mainActivity's own id/name string.
ACTIVITY_ID_FIELDS = ("activityId", "id", "name", "activity_id", "type", "mapId")
# The word tokens that make an activity class an INSTANCE. Deliberately just
# these two: the game's activity system also carries `st.activity.World*`
# (overworld), and ambiguous ones like `Boss` / `ArenaChest` / `MountRush` that
# may or may not be instanced - matching those would open the Dungeon HUD (and
# start DPS sessions) in the open world. Add a token here only with evidence
# from a live `Scene.instance_probe` dump.
INSTANCE_ACTIVITY_WORDS = ("dungeon", "rift")

# Map-id shape, used by the last-resort config scan. The real ids look like
# "POI_Forest_Z1", "POI/Z2Levels/Z2_POI_Boss_Cleodora", "World/W1_Siagarta",
# "Town_Central", "R1_POI_ManfishRuins_Z1" or "Dungeon_Wolf_Hard": a known
# namespace prefix with its delimiter, or a `_Z<n>` zone suffix. Prose (which is
# what a localisation or chat string looks like) is ruled out by the space test,
# and a bare lookalike like "WorldMap" by the delimiter.
_MAPID_PREFIX = re.compile(r"^(POI|World|Dungeon|Rift|Town|Z[0-9])[/_]", re.I)
_MAPID_ZONE = re.compile(r"_Z[0-9]", re.I)


def _looks_like_map_id(s: str) -> bool:
    if not s or " " in s:
        return False
    return bool(_MAPID_PREFIX.match(s) or _MAPID_ZONE.search(s))


# A config's mapId/activityId, and what disqualifies a string from being one.
# Resource paths end in an extension and are the shape a WRONG object hands back
# (see _looks_like_config_id).
_CONFIG_ID = re.compile(r"^(POI|World|Dungeon|Rift|Z[0-9])[/_]", re.I)
_ASSET_NAME = re.compile(
    r"\.(png|webp|jpg|jpeg|bmp|gif|atlas|json|xml|ttf|otf|fnt|wav|ogg|mp3|dat)$",
    re.I)


def _looks_like_config_id(s: str | None) -> bool:
    """Is this String the CONFIG's own map/activity id, or a resource path?

    LIVE 2026-09-30 (dungeon visit, `mode_probe`): the substring test this
    replaces accepted ``UI/icons/POI_DifficultySkulls_atlas_38PX.png`` — it
    contains "POI" — so the first object the GameLayer scan offered became
    "the config". Every read derived from it was then an asset path: `map_id()`
    returned that icon path and `difficulty()` found no value, which is what
    left the MODE badge blank on the Top DPS HUD and the DPS Analysis rail
    while the Run Timer still named one (the timer resolves it a second way).

    An instance id STARTS with an instance token (or carries a zone suffix) and
    is never a file name. Kept stricter than `_looks_like_map_id`, which also
    accepts town ids: a town string is a fine map id and must not validate as
    the instance config (see test_non_poi_config_ignored).
    """
    if not s or _ASSET_NAME.search(s):
        return False
    return bool(_CONFIG_ID.match(s) or _MAPID_ZONE.search(s))


def _class_words(name: str) -> set[str]:
    """Every word token of a class name, across ALL its dotted segments.

    ``st.activity.WolfDungeonInstance`` -> ``{'st', 'activity', 'wolf',
    'dungeon', 'instance'}`` and ``st.activity.dungeon.Wolf`` -> ``{...,
    'dungeon', 'wolf'}``. All segments, not just the leaf: a rename can move
    the class under a namespace as easily as it can rename the leaf. Tokens (not
    substrings) keep names that merely contain the letters - ``Drift``,
    ``MountRush`` - from matching.
    """
    words: set[str] = set()
    for seg in (name or "").split("."):
        words |= {w.lower()
                  for w in _CAMEL_BOUNDARY.sub(" ", seg).replace("_", " ").split()}
    return words

# Placed-food name hints: st.skill.SkillObject-classed elements only count as
# food when their resolved skill name smells like one (WorldConsumable elements
# are always food — they never carry a string id, the slot is a Skill*).
_FOOD_NAME_HINTS = ("consumable", "feast", "cauldron", "food", "cook")

# --- drifting element/item field names (the OFF_ELEM_FX lesson) --------------
# 2026-09-18: OFF_ELEM_FX drifted and a garbage read at the stale slot marked
# every orb collected. The fix (model.orb_fx_present) reflects 'currentFx' per
# type and degrades to "no opinion" — these tuples extend that treatment to
# every element/unit layout offset the scene reads, in probe order (the
# documented name first, then the renames a patch is most likely to use). The
# constants stay as the calibrated fallback; a reflected miss + a constant
# read that fails validation degrades to None, never a confident lie.
ELEM_ID_FIELDS = ("kind", "elemId", "id", "name")        # ent.Element.kind (String)
ELEM_STATE_FIELDS = ("currentVisualState", "visualState", "state")
LOOT_ITEM_FIELDS = ("item", "itemPtr", "lootItem")        # LootDrop.item (st.Item*)
LOOT_COUNT_FIELDS = ("count", "stack", "amount")          # LootDrop.count (i32)
ITEM_KIND_FIELDS = ("kind", "id", "itemId")               # st.Item.kind (String)
CONSUMABLE_SKILL_FIELDS = ("skill", "baseSkill", "sourceSkill")
ATTR_HEALTH_FIELDS = ("hp", "life", "health", "currentHealth")  # *Attributes

# i32 sanity bound for stack counts (a stale-slot read must not invent a
# 2-billion stack; the game caps stacks far below this).
_MAX_STACK = 100_000


@dataclass
class Entity:
    addr: int
    cls: str | None          # leaf class, e.g. "ent.Foe", "ent.foe.Boss"
    unit_id: str | None
    x: float
    y: float
    z: float
    level: int = 0
    hp: float = 0.0
    owner_addr: int = 0
    owner_cls: str | None = None
    is_foe: bool = False     # descends from ent.Foe (super-chain)
    is_hero: bool = False    # descends from ent.Hero

    @property
    def is_player_owned(self) -> bool:
        if not self.owner_cls:
            return False
        if self.owner_cls == "st.Player":
            return True
        # Match any Hero class or generic ent.Hero
        return self.owner_cls == "ent.Hero" or self.owner_cls.startswith("ent.hero.")

    @property
    def is_enemy(self) -> bool:
        return self.is_foe and not self.is_player_owned

    @property
    def kind(self) -> str:
        if self.is_hero:
            return "hero"
        if self.is_foe:
            return "companion" if self.is_player_owned else "enemy"
        return self.cls or "?"

    @property
    def hero_class(self) -> str:
        """Canonical hero class (e.g. 'Warrior', 'Priest'), matching Entity HUD."""
        from ..data.units import resolve_hero_class
        return resolve_hero_class(self.cls, self.unit_id)

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


@dataclass
class Element:
    addr: int
    cls: str | None
    elem_id: str | None
    state: str | None
    x: float
    y: float
    z: float
    type_ptr: int = 0       # hl_type of the instance (0 = unknown/reflection off)

    @property
    def kind(self) -> str:
        k = _ELEM_KINDS.get(self.cls or "", (self.cls or "")
                               .replace("ent.interactible.", "").replace("ent.", ""))
        if self.elem_id:
            eid_l = self.elem_id.lower()
            if self.is_chest:
                return "chest"
            if self.is_orb:
                if "chestorb" in eid_l or "chest_orb" in eid_l or "timercollectrun" in eid_l:
                    return "chest_orb"
                return "orb"
            if self.is_obelisk:
                if "checkpoint" in eid_l or "finish_" in eid_l or "start_" in eid_l:
                    if self.state and self.state.lower() == "completed":
                        return "chest_orb"
                if "respawn" in eid_l:
                    return "respawn"
                return "obelisk"
            if self.is_gatherable:
                return "gatherable"
            if self.is_teleporter:
                return "dungeon"
        return k

    @property
    def is_gatherable(self) -> bool:
        if self.cls == "ent.interactible.Gatherable":
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return "gather" in eid_l or "ore" in eid_l or "flower" in eid_l
        return False

    @property
    def is_chest(self) -> bool:
        if self.cls == "ent.interactible.Chest":
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return ("chest" in eid_l or "crate" in eid_l or "recipe" in eid_l) and "orb" not in eid_l and "checkpoint" not in eid_l and "levelup" not in eid_l
        return False

    @property
    def is_obelisk(self) -> bool:
        if self.cls in ("ent.interactible.Obelisk", "ent.interactible.RespawnPoint"):
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return "obelisk" in eid_l or "respawn" in eid_l or "checkpoint" in eid_l or "start_" in eid_l or "finish_" in eid_l
        return False

    @property
    def is_orb(self) -> bool:
        if self.cls == "ent.interactible.InstanceOrb":
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return ("orb" in eid_l or "secretorb" in eid_l or "timercollectrun" in eid_l) and "instanceorb" not in eid_l
        return False

    @property
    def is_teleporter(self) -> bool:
        if not self.cls:
            return False
        cls_lower = self.cls.lower()
        if "teleporter" in cls_lower or "portal" in cls_lower or "dungeon" in cls_lower:
            return True
        if self.cls == "ent.Element" and self.elem_id:
            eid_l = self.elem_id.lower()
            return "teleporter" in eid_l or "portal" in eid_l or "dungeon" in eid_l or "instanceorb" in eid_l
        return False

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


@dataclass
class LootDrop:
    addr: int
    item_id: str | None      # st.Item.kind, e.g. 'MoteOfChaos'
    count: int
    x: float
    y: float
    z: float
    rarity: str | None = None  # live rolled rarity (dungeon gear rolls up)

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


@dataclass
class FoodStation:
    addr: int
    name: str | None         # e.g. 'Plainswalker Feast', resolved off st.skill.Skill
    x: float
    y: float
    z: float

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


class Scene:
    CONFIG_RESCAN_TTL = 8.0    # throttle fallback scans to at most once every 8s
    ACTIVITY_SCAN_BYTES = 0x200   # GameLayer header window hunted for mainActivity

    # GameLayer.config resolution (2026-09-19). Both the slot and the fields
    # inside it are looked up BY NAME first; the OFF_CONFIG_* constants are the
    # calibrated fallback. The 2026-09-19 build moved the slot (the constant
    # 0xc8 landed on an unrelated object, so `_cfg_off` never validated and
    # map_id/difficulty both went dead outside the overworld).
    CONFIG_FIELD = "config"
    CONFIG_MAPID_FIELDS = ("mapId", "mapID", "activityId", "activityID", "map", "zoneId", "level")
    CONFIG_DIFF_FIELDS = ("difficulty", "difficultyLevel", "diff", "hard")
    # Last-resort window hunted for a mapId-shaped String inside a config whose
    # own layout can't be reflected (the old probe only looked at 0x40 bytes).
    CONFIG_MAPID_SCAN_BYTES = 0x200

    def __init__(self, proc: Proc, hl: Hl):
        self.proc = proc
        self.hl = hl
        self._cfg_off: int | None = None   # discovered GameLayer.config offset
        self._cfg_type: int | None = None  # its declared type (reflected)
        self._cfg_slot_cache: dict[int, int | None] = {}   # gl type -> slot
        # cfg type (0 = unknown) -> the slot the mapId hunt last hit at
        self._cfg_mapid_cache: dict[int, int] = {}
        self._cfg_scan_at = 0.0            # last full config scan (throttle)
        self._act_off: int | None = None   # discovered GameLayer.mainActivity offset
        self._act_via: str | None = None   # how _act_off was resolved (diagnostic)
        self._act_scan_at = 0.0            # last activity-slot scan (throttle)
        # shared element scan (see ELEMS_TTL below)
        self._elems: list[Element] = []
        self._elems_key: int | None = None
        self._elems_at = 0.0
        # Per-type resolved element-field offsets, cached by type_ptr        # (type pointers are stable for the process life — same reasoning as        # hl._field_off_cache). Maps field name -> offset | None.
        self._elem_off_cache: dict[int, dict[str, int | None]] = {}

    def gamelayer(self, pbase: int | None) -> int | None:
        if not pbase:
            return None
        return self.hl.ptr(pbase + OFF_GAMELAYER)

    # --- drifting element/unit offsets (the OFF_ELEM_FX rule) ----------------
    def _elem_field_offsets(self, type_ptr: int | None,
                            names: tuple[str, ...],
                            fallback: int | None) -> dict[str, int | None]:
        """Per-type resolved offsets for one drifting field, cached by type_ptr.

        `names` are probed in order via hl.field_offset (reflection across the
        whole super-chain); `fallback` is the calibrated constant. Returns
        {reflected: off|None, fallback: off} — callers validate what they read
        and treat a failed read as 'no data', never as a confident value (the
        rule orb_fx_present learned the hard way on 2026-09-18: a drifted
        hardcoded slot read garbage that passed a plausibility gate and lied).
        """
        key = type_ptr if is_ptr(type_ptr) else 0
        sig = (names, fallback is not None)
        cached = self._elem_off_cache.get(key)
        if cached is not None and cached.get("_sig") == sig:
            return cached
        reflected: int | None = None
        if is_ptr(type_ptr):
            try:
                for nm in names:
                    off = self.hl.field_offset(type_ptr, nm)
                    if off:
                        reflected = off
                        break
            except ProcError:
                reflected = None
        out = {"_sig": sig, "reflected": reflected, "fallback": fallback}
        self._elem_off_cache[key] = out
        return out

    def _read_ptr_field(self, obj: int, offs: dict[str, int | None]) -> int | None:
        """A pointer field via its resolved offset. When reflection resolved
        the field, that offset is AUTHORITATIVE — the constant is tried only
        when reflection had no answer (a moved field leaves junk at the old
        slot, and reading it would be the 2026-09-18 lie again)."""
        order = ((offs["reflected"],) if offs.get("reflected")
                 else (offs.get("reflected"), offs.get("fallback")))
        for off in order:
            if not off:
                continue
            try:
                raw = self.proc.try_read(obj + off, 8)
            except ProcError:
                continue
            if raw is not None:
                return struct.unpack_from("<Q", raw, 0)[0]
        return None

    def _read_i32_field(self, obj: int, offs: dict[str, int | None],
                        lo: int, hi: int) -> int | None:
        """An i32 field via its resolved offset, validated against [lo, hi] —
        an out-of-range value is 'no data', not a number. Same authority rule
        as _read_ptr_field: the constant is only a fallback when reflection
        had no answer."""
        order = ((offs["reflected"],) if offs.get("reflected")
                 else (offs.get("reflected"), offs.get("fallback")))
        for off in order:
            if not off:
                continue
            try:
                raw = self.proc.try_read(obj + off, 4)
            except ProcError:
                continue
            if raw is None:
                continue
            v = struct.unpack_from("<i", raw, 0)[0]
            if lo <= v <= hi:
                return v
        return None


    # --- GameLayer.config discovery (reflection-first, 2026-09-19) ----------
    def _config_slot(self, gl: int) -> int | None:
        """Byte offset of GameLayer.config, resolved by FIELD NAME.

        Cached per GameLayer type pointer (types are stable for the process
        life). None when reflection can't resolve it — callers then fall back
        to the calibrated OFF_CONFIG_CANDIDATES.
        """
        tip = self.hl.ptr(gl)
        if not tip:
            return None
        if tip in self._cfg_slot_cache:
            return self._cfg_slot_cache[tip]
        slot = None
        try:
            slot = self.hl.field_offset(tip, self.CONFIG_FIELD)
        except ProcError:
            slot = None
        self._cfg_slot_cache[tip] = slot
        return slot

    def _config(self, gl: int | None) -> tuple[int | None, int | None]:
        """(config_ptr, declared config type ptr) for this GameLayer.

        The slot comes from the `config` field's own offset, and the declared
        type from the Hl field table — the latter is what lets mapId /
        difficulty resolve by NAME without trusting a class read on the
        instance. A candidate is accepted only when it validates (a mapId-ish
        String is reachable, or the pointer's class name verifies as a config
        type); otherwise the next candidate is tried and (None, None) returned.
        """
        if not gl:
            return (None, None)
        tip = self.hl.ptr(gl)
        slot = self._config_slot(gl)
        ctip = self.hl.field_type_ptr(tip, self.CONFIG_FIELD) if tip else None
        order: list[int] = []
        if slot:
            order.append(slot)
        order.extend(o for o in OFF_CONFIG_CANDIDATES if o not in order)
        # A slot the throttled SCAN previously validated is PROVEN on this
        # build: try it before declaring failure. Live 2026-09-20: the
        # reflected `config` slot points at a non-config object and the old
        # constant is a null slot, so the scan fallback is the ONLY resolver
        # — and wiping its discovery here made difficulty() read valid once
        # per 8s scan and None for every tick between (the MODE badge
        # flicker). Still value-validated below, so a stale slot self-heals.
        if self._cfg_off is not None and self._cfg_off not in order:
            order.append(self._cfg_off)
        for off in order:
            try:
                cfg = self.hl.ptr(gl + off)
            except ProcError:
                continue
            if not cfg:
                continue
            t = ctip if off == slot else None
            ok = self._is_config_struct(cfg, t)
            if not ok and off == slot:
                # The slot came from reflection, so it IS the config field. When
                # the calibrated mapId probe can't see the struct (the layout
                # inside it moved too), its declared type or its own runtime
                # class is the evidence - runtime class verification, never a
                # guess. Only the NAMED slot gets this: a constant/scan hit is
                # still value-validated, because those offsets can be stale.
                ok = self._config_type_name(t) is not None
                if not ok:
                    try:
                        cls = self.hl.class_of(cfg)
                    except ProcError:
                        cls = None
                    ok = bool(cls and "config" in cls.lower())
            if ok:
                self._cfg_off = off
                self._cfg_type = t
                return (cfg, t)
        if self._cfg_off is not None:
            self._cfg_off = None       # stale slot: forget it
            self._cfg_type = None
        return (None, None)

    def _mapid_scan(self, cfg: int) -> str | None:
        """Last resort: the config's own map id, found by SHAPE, for a build
        whose st.Config layout can't be reflected.

        Only called once the reflected/calibrated mapId, the activity and the
        world have all failed, so a miss here means "no map id" anyway. A hit is
        remembered per config type, so the tick path pays the walk once and then
        one read (and the remembered slot is dropped if it ever stops hitting,
        so the next shift self-heals). The shape test is what stops it trusting
        an unrelated String in the struct (a server name, a localisation key).
        """
        key = self._cfg_type or 0
        hit = self._cfg_mapid_cache.get(key)
        if hit is not None:
            s = self._mapid_at(cfg, hit)
            if s:
                return s
            del self._cfg_mapid_cache[key]        # shifted again: rescan
        for off in range(0, self.CONFIG_MAPID_SCAN_BYTES, 8):
            s = self._mapid_at(cfg, off)
            if s:
                self._cfg_mapid_cache[key] = off
                return s
        return None

    def _mapid_at(self, cfg: int, off: int) -> str | None:
        """One slot of the last-resort hunt: the map id there, or None."""
        sptr = self.hl.ptr(cfg + off)
        if not is_ptr(sptr):
            return None
        try:
            if self.hl.class_of(sptr) != "String":
                return None
        except ProcError:
            return None
        s = self.hl.hl_string(sptr)
        return s if s and _looks_like_map_id(s) else None

    def _config_type_name(self, ctip: int | None) -> str | None:
        """The declared config type's class name, when it looks like a config
        type (so an unreadable instance class can't veto a reflected slot)."""
        if not ctip:
            return None
        try:
            name = self.hl.type_name(ctip)
        except ProcError:
            return None
        if name and "config" in name.lower():
            return name
        return None

    def _config_field_offsets(self, ctip: int | None,
                              names: tuple[str, ...]) -> list[int]:
        """Reflected offset of the first of `names` that resolves on the
        config type (super-chain aware). Empty when reflection is unavailable,
        so the calibrated constants take over."""
        if not ctip:
            return []
        for nm in names:
            try:
                off = self.hl.field_offset(ctip, nm)
            except ProcError:
                off = None
            if off:
                return [off]
        return []

    def difficulty(self, pbase: int | None) -> int | None:
        """Instance difficulty from GameLayer.config: 0=Normal, 1=Hard, None
        outside an instance. Independent of enemy levels."""
        gl = self.gamelayer(pbase)
        if gl is None:
            return None

        # 1. Reflection-first: the config slot by name, and the difficulty
        #    field by name inside it (constants are the fallback for both).
        cfg, ctip = self._config(gl)
        if cfg:
            d = self._difficulty_at(cfg, ctip)
            if d is not None:
                return d

        # 2. Fallback: Scan GameLayer for the config struct
        now = time.monotonic()
        if now - self._cfg_scan_at < self.CONFIG_RESCAN_TTL:
            return None
        self._cfg_scan_at = now
        blk = self.proc.try_read(gl, CONFIG_SCAN_BYTES)
        if blk is None:
            return None

        # Using a while loop to bypass environment linter issues with 'range'
        i = 0
        while i < (len(blk) - 7):
            p = struct.unpack_from("<Q", blk, i)[0]
            if is_ptr(p) and self._is_config_struct(p):
                self._cfg_off = i
                self._cfg_type = None
                return self._difficulty_at(p)
            i += 8
        return None

    def _is_config_struct(self, cfg: int, ctip: int | None = None) -> bool:
        """Validates that cfg points to a st.Config-like struct.

        With the config type known (`ctip`), its reflected mapId-ish field is
        tried FIRST; the calibrated candidate offsets and the documented
        classic layout (mapId at +0x10) follow, so both the live build and
        older builds validate."""
        reflected = self._config_field_offsets(ctip, self.CONFIG_MAPID_FIELDS)
        for off in (*reflected, *OFF_CONFIG_MAPID_CANDIDATES, OFF_CONFIG_MAPID):
            try:
                mp = self.hl.ptr(cfg + off)
                if is_ptr(mp) and self.hl.class_of(mp) == "String":
                    if _looks_like_config_id(self.hl.hl_string(mp)):
                        return True
            except Exception:
                pass
        return False

    def _difficulty_at(self, cfg: int | None,
                       ctip: int | None = None) -> int | None:
        """Read difficulty from a candidate config struct.

        Two eras:
          * current (InstanceLobby): plain I32 at reflected ``difficulty`` —
            read directly (0xC4 was that slot's offset; DEAD on 2026-09-20,
            where it reads garbage 507/32766).
          * legacy (st.Config): boxed Null<Int> at reflected / 0x08, where
            box+0x08 -> i32.  A null box means outside an instance.
            LIVE 2026-09-20 (dmode probe, all three modes): the BOXED path is
            the one that works, with 0=Normal, 1=Hard, 2=Heroic (box=2 read
            on a heroic run; box=0/1 on normal/hard).
        Plain I32 is tried first at the reflected offset; legacy boxed is the
        fallback.  A raw I32 fallback outside reflection is NOT used — an
        unset slot reads as 0 and would fake "Normal" (see test_null_box)."""
        if not cfg:
            return None

        reflected = self._config_field_offsets(ctip, self.CONFIG_DIFF_FIELDS)
        # 1) plain I32 at the reflected offset (current InstanceLobby)
        for off in reflected:
            try:
                raw = self.proc.try_read(cfg + off, 4)
                if raw:
                    v = struct.unpack("<i", raw)[0]
                    if v in (0, 1, 2):   # 2 = heroic (live-verified 2026-09-20)
                        return v
            except Exception:
                pass
        # 2) boxed Null<Int> at reflected / calibrated offsets (legacy;
        #    the path that actually works on the 2026-09-20 build)
        for off in (*reflected, *OFF_CONFIG_DIFF_CANDIDATES, OFF_CONFIG_DIFFICULTY):
            try:
                box = self.hl.ptr(cfg + off)
            except Exception:
                continue
            if is_ptr(box):
                raw = self.proc.try_read(box + OFF_BOX_VALUE, 4)
                if raw:
                    v = struct.unpack("<i", raw)[0]
                    if v in (0, 1, 2):   # 2 = heroic (live-verified 2026-09-20)
                        return v
        return None

    def map_id(self, pbase: int | None) -> str | None:
        """The internal map/zone ID (e.g. 'POI_Forest_Z1' or 'World_Z1')."""
        gl = self.gamelayer(pbase)
        if not gl: return None
        
        # 1. Try Config.mapId — the reflected mapId-ish field first (the
        #    2026-09-19 build moved the layout), then the calibrated offsets.
        cfg, ctip = self._config(gl)
        if cfg:
            reflected = self._config_field_offsets(ctip, self.CONFIG_MAPID_FIELDS)
            for off in (*reflected, *OFF_CONFIG_MAPID_CANDIDATES):
                mid_ptr = self.hl.ptr(cfg + off)
                if mid_ptr:
                    s = self.hl.hl_string(mid_ptr)
                    if s and len(s) > 1:
                        return s

        # 2. Try MainActivity.activityId (Common in dungeons)
        act = self.hl.ptr(gl + OFF_MAIN_ACTIVITY)
        if act:
            tp_act = self.hl.ptr(act)
            if tp_act:
                # Try many common field names for IDs in activity objects
                for fn in ("activityId", "mapId", "id", "name", "type"):
                    off = self.hl.field_offset(tp_act, fn)
                    if off:
                        sptr = self.hl.ptr(act + off)
                        if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                            s = self.hl.hl_string(sptr)
                            if s and len(s) > 1: return s

        # 3. Try World object (Common in open world)
        world = self.hl.ptr(gl + OFF_WORLD_MAPID)
        if world:
            tp_world = self.hl.ptr(world)
            if tp_world:
                for fn in ("mapId", "id", "name", "zoneId"):
                    off = self.hl.field_offset(tp_world, fn)
                    if off:
                        sptr = self.hl.ptr(world + off)
                        if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                            s = self.hl.hl_string(sptr)
                            if s and len(s) > 1: return s
        
        # 4. Last resort: a config string that reads like a map id (the layout
        #    inside st.Config moved, or the struct isn't reflectable at all).
        cfg, _ctip = self._config(gl)
        if cfg:
            s = self._mapid_scan(cfg)
            if s:
                return s
        return None

    def activity_id(self, pbase: int | None) -> str | None:
        """The current activity ID (e.g. 'Dungeon_Wolf_Hard')."""
        gl = self.gamelayer(pbase)
        if not gl: return None
        
        # 1. Try Config object
        self.difficulty(pbase)
        if self._cfg_off:
            cfg = self.hl.ptr(gl + self._cfg_off)
            if cfg:
                # activityId probes: the classic head slots (0/8/16) plus the
                # InstanceLobby layout's own id slot (0xac — 'Dungeon_…_Hard'
                # lives there; live 2026-09-20 probe showed activity=None
                # because only the head slots were tried).
                for off in (0, 8, 16, *OFF_CONFIG_MAPID_CANDIDATES):
                    sptr = self.hl.ptr(cfg + off)
                    if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                        s = self.hl.hl_string(sptr)
                        if s and len(s) > 1 and "shard" not in s.lower(): return s
        
        # 2. Try MainActivity directly
        act = self.hl.ptr(gl + OFF_MAIN_ACTIVITY)
        if act:
            tp_act = self.hl.ptr(act)
            if tp_act:
                for fn in ("activityId", "id", "name", "activity_id", "type"):
                    off = self.hl.field_offset(tp_act, fn)
                    if off:
                        sptr = self.hl.ptr(act + off)
                        if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                            s = self.hl.hl_string(sptr)
                            if s and len(s) > 1: return s
        return None


    def get_live_worldevent_rift_zone(self, pbase: int | None) -> str | None:
        """Reads active Rift zone from GameLayer.worldEvents -> currentEvents -> activeRift."""
        gl = self.gamelayer(pbase)
        if not gl:
            return None
        try:
            we = self.hl.ptr(gl + OFF_WORLDEVENTS_ARR)
            if not is_ptr(we):
                return None
            
            # st.event.WorldEvents.currentEvents (+0x0098)
            ce_proxy = self.hl.ptr(we + 0x0098)
            if not is_ptr(ce_proxy):
                return None
            
            # hxbit.ArrayProxyData.array (+0x0028) -> ArrayDyn
            arr_dyn = self.hl.ptr(ce_proxy + 0x0028)
            if not is_ptr(arr_dyn):
                return None
            
            # ArrayDyn.array (+0x0008) -> ArrayObj
            arr_obj = self.hl.ptr(arr_dyn + 0x0008)
            if not is_ptr(arr_obj):
                return None
            
            # ArrayObj length (+0x08) & array pointer (+0x10) -> NativeArray
            length = self.proc.read_i32(arr_obj + 0x08)
            if length <= 0 or length > 100:
                return None
            
            na_ptr = self.hl.ptr(arr_obj + 0x10) or self.hl.ptr(arr_obj + 0x0C)
            if not is_ptr(na_ptr):
                return None
            
            # NativeArray elements start at +0x18
            for i in range(min(length, 10)):
                evt_ptr = self.hl.ptr(na_ptr + 0x18 + (i * 8))
                if is_ptr(evt_ptr):
                    cls_name = self.hl.class_of(evt_ptr) or ""
                    if "Rift" in cls_name or "WorldEvent" in cls_name:
                        for str_off in (0x0100, 0x00F4, 0x00EC, 0x00B0):
                            active_rift_ptr = self.hl.ptr(evt_ptr + str_off)
                            if is_ptr(active_rift_ptr):
                                s = self.hl.hl_string(active_rift_ptr)
                                if s and ("POI" in s or "Rift" in s or "Z1" in s or "Z2" in s):
                                    return s
        except Exception:
            pass
        return None

        # 2. Try Config.shardId / lobbyId
        self.difficulty(pbase)
        if self._cfg_off:
            cfg = self.hl.ptr(gl + self._cfg_off)
            if cfg:
                tp_cfg = self.hl.ptr(cfg)
                if tp_cfg:
                    for fn in ("shardId", "lobbyId", "instanceId", "channel"):
                        off_cfg = self.hl.field_offset(tp_cfg, fn)
                        if off_cfg:
                            # Try as string first, then int
                            val = self.hl.ptr(cfg + off_cfg)
                            if val and self.hl.class_of(val) == "String":
                                return self.hl.hl_string(val)
                            return self.hl.i32(cfg + off_cfg)
        return None

    # --- instance detection -------------------------------------------------
    # Layered and deliberately redundant, cheapest first. Every layer is
    # name/shape based rather than exact-string based, so a game update that
    # renames an activity class, moves OFF_MAIN_ACTIVITY or moves the rift byte
    # cannot shut the gate with no way to tell why (see instance_probe).
    def _safe_ptr(self, addr: int) -> int:
        """A pointer read that folds an unreadable address into 0."""
        try:
            return self.hl.ptr(addr) or 0
        except ProcError:
            return 0

    def _class_chain(self, addr: int) -> list[str]:
        """A super-chain that always has at least the leaf class name."""
        try:
            chain = self.hl.super_chain(addr, limit=8)
        except ProcError:
            chain = []
        if chain:
            return chain
        try:
            leaf = self.hl.class_of(addr)
        except ProcError:
            leaf = None
        return [leaf] if leaf else []

    def _looks_like_skill(self, addr: int) -> bool:
        """Class guard for a candidate st.skill.Skill pointer (super-chain,
        not an exact name — a renamed/derived skill class must still pass)."""
        try:
            return "skill" in _class_words(self.hl.class_of(addr) or "")
        except ProcError:
            return False

    def _looks_like_activity(self, addr: int) -> bool:
        """True when `addr` is an object in the game's activity namespace."""
        if not is_ptr(addr):
            return False
        try:
            return any("activity" in _class_words(n)
                       for n in self._class_chain(addr))
        except Exception:
            return False

    def _activity_offset(self, gl: int) -> int | None:
        """GameLayer member holding `mainActivity`, discovered if it moved.

        `OFF_MAIN_ACTIVITY` can drift like `OFF_CONFIG` did, and a stale value
        here does not raise - it silently yields a non-activity object, which an
        exact-name gate then reads as "not a dungeon". So the slot is resolved
        the way the rest of this reader resolves drifting layout: reflect the
        documented field name first, fall back to the calibrated constant, and
        only when BOTH come up empty hunt the bounded GameLayer header for an
        activity-classed pointer (throttled - a healthy build pays two reads,
        not a scan). Records how it was found in `_act_via` for the diagnostic.
        """
        # 1. a slot already proven on this build
        if self._act_off is not None and self._looks_like_activity(
                self._safe_ptr(gl + self._act_off)):
            return self._act_off     # `_act_via` keeps the ORIGINAL resolution
        self._act_off = None

        # 2. reflection on st.GameLayer (survives a moved offset, not a rename)
        reflected = None
        tp = self._safe_ptr(gl)
        if tp:
            try:
                for fname in ACTIVITY_FIELD_NAMES:
                    reflected = self.hl.field_offset(tp, fname)
                    if reflected:
                        break
            except ProcError:
                reflected = None
        if reflected:
            if self._looks_like_activity(self._safe_ptr(gl + reflected)):
                self._act_off, self._act_via = reflected, "reflected"
                return reflected
            if not self._safe_ptr(gl + reflected):
                # The name still resolves and the slot is genuinely empty: this
                # is the overworld between activities, not a broken layout.
                # Recorded as "empty" rather than None so the diagnostic can
                # tell a NULL slot (normal, everywhere outside an instance)
                # from a slot that could not be resolved at all - which is the
                # case that actually means an offset moved. Reporting the
                # alarming one for both sent a reader hunting a drift that did
                # not exist: 0xf0 was correct and the pointer was simply NULL.
                self._act_via = "empty"
                return None

        # 3. the calibrated constant
        if self._looks_like_activity(self._safe_ptr(gl + OFF_MAIN_ACTIVITY)):
            self._act_off, self._act_via = OFF_MAIN_ACTIVITY, "constant"
            return OFF_MAIN_ACTIVITY

        # 4. the field name is gone (rename) or the slot holds something that is
        #    not an activity (restructure) -> scan the GameLayer header
        now = time.monotonic()
        if now - self._act_scan_at < self.CONFIG_RESCAN_TTL:
            return None
        self._act_scan_at = now
        blk = self.proc.try_read(gl, self.ACTIVITY_SCAN_BYTES)
        if blk is None:
            return None
        off = 0
        while off + 8 <= len(blk):
            cand = struct.unpack_from("<Q", blk, off)[0]
            if is_ptr(cand) and self._looks_like_activity(cand):
                self._act_off, self._act_via = off, "scanned"
                return off
            off += 8
        return None

    def _activity_kind(self, gl: int) -> str | None:
        """'dungeon' | 'rift' | None from mainActivity's class super-chain.

        The game's own activity system, so this needs no map id and no config.
        Matched by word tokens over every dotted segment, so `st.activity.Dungeon`,
        `st.activity._DungeonBase`, `st.activity.dungeon.Wolf` and a renamed
        `WolfDungeonInstance` all count, while `st.activity.WorldCamp` does not.
        """
        off = self._activity_offset(gl)
        if off is None:
            return None
        act = self._safe_ptr(gl + off)
        if not act:
            return None
        words: set[str] = set()
        for name in self._class_chain(act):
            words |= _class_words(name)
        for kind in INSTANCE_ACTIVITY_WORDS:
            if kind in words:
                return kind
        return None

    def _rift_flag(self, gl: int) -> bool:
        """st.GameLayer's rift bool: reflected field first, then OFF_RIFT_BOOL."""
        try:
            tp = self.hl.ptr(gl)
            if tp:
                for fname in RIFT_FIELD_NAMES:
                    off = self.hl.field_offset(tp, fname)
                    if off and self.proc.try_read(gl + off, 1) == b"\x01":
                        return True
        except ProcError:
            pass
        try:
            # Strict 1-byte check: only 0x01 counts, so a stray flag value
            # elsewhere in the layer can't be mistaken for "in a rift".
            return self.proc.try_read(gl + OFF_RIFT_BOOL, 1) == b"\x01"
        except ProcError:
            return False

    def _heroic_flag(self, gl: int) -> bool:
        """st.GameLayer's heroic bool (heroic dungeons are a FLAG on the layer,
        NOT difficulty=2 in InstanceLobby.difficulty — the config read stays
        0=Normal/1=Hard even on a heroic run). Reflected field first, then
        OFF_LAST_HEROIC. Same strict 1-byte pattern as _rift_flag."""
        try:
            tp = self.hl.ptr(gl)
            if tp:
                for fname in HEROIC_FIELD_NAMES:
                    off = self.hl.field_offset(tp, fname)
                    if off and self.proc.try_read(gl + off, 1) == b"\x01":
                        return True
        except ProcError:
            pass
        try:
            return self.proc.try_read(gl + OFF_LAST_HEROIC, 1) == b"\x01"
        except ProcError:
            return False

    def _activity_id_cheap(self, gl: int) -> str | None:
        """mainActivity's own id/name string - NO config scan.

        The gate must not depend on `_cfg_off` ever being discovered, which is
        what made the old fallback die whenever config discovery failed.
        """
        slot = self._activity_offset(gl)
        if slot is None:
            return None
        act = self._safe_ptr(gl + slot)
        if not act:
            return None
        try:
            tp = self.hl.ptr(act)
            if not tp:
                return None
            for fname in ACTIVITY_ID_FIELDS:
                off = self.hl.field_offset(tp, fname)
                if not off:
                    continue
                sptr = self.hl.ptr(act + off)
                if is_ptr(sptr) and self.hl.class_of(sptr) == "String":
                    s = self.hl.hl_string(sptr)
                    if s and len(s) > 1:
                        return s
        except ProcError:
            pass
        return None

    def instance_flags(self, pbase: int | None) -> tuple[bool, bool]:
        """`(in_dungeon, in_rift)` in ONE pass, so the two can't disagree.

        Layers, cheapest first:
          1. mainActivity's class chain (no map id, no config). Matched by word
             tokens, not an exact class name, so a renamed `st.activity.Dungeon`
             still opens the gate.
          2. GameLayer's rift flag (reflected field, then OFF_RIFT_BOOL).
          3. mainActivity's own id string - independent of `_cfg_off`, so a
             failed config discovery can no longer take the fallback down.
          4. the discovered config's map id (last resort; only once `_cfg_off`
             is known, which is what keeps the 16KB config scan off the tick
             path while wandering the overworld).

        Every layer reads through `_activity_offset`, which re-resolves the slot
        by reflection/constant/bounded scan, so a moved `OFF_MAIN_ACTIVITY` no
        longer silently downgrades layer 1 and 3 to "not a dungeon".
        """
        try:
            gl = self.gamelayer(pbase)
            if gl is None:
                return False, False

            kind = self._activity_kind(gl)
            in_dungeon, in_rift = kind == "dungeon", kind == "rift"

            if not in_rift:
                in_rift = self._rift_flag(gl)

            if not (in_dungeon or in_rift):
                aid = (self._activity_id_cheap(gl) or "").lower()
                in_dungeon = "dungeon" in aid
                in_rift = "rift" in aid

            if not (in_dungeon or in_rift) and self._cfg_off is not None:
                mid = (self.map_id(pbase) or "").lower()
                in_rift = "rift" in mid
                in_dungeon = ("poi_" in mid or "dungeon" in mid)

            return in_dungeon, in_rift
        except ProcError:
            return False, False

    def in_dungeon(self, pbase: int | None) -> bool:
        """True when the player is inside a dungeon instance."""
        return self.instance_flags(pbase)[0]

    def is_rift(self, pbase: int | None) -> bool:
        """True when the current layer is a Rift."""
        return self.instance_flags(pbase)[1]

    def instance_probe(self, pbase: int | None) -> dict:
        """Diagnostic: the raw inputs the instance gate collapsed to a bool.

        ``in_dungeon()`` / ``is_rift()`` fold every failure into False, which
        makes a gate that stays closed silent from the outside - the Dungeon
        HUD simply never appears. This reports what each link actually saw so a
        closed gate can say WHY (mainActivity slot empty, activity class not
        instance-like, map id not recognised, rift byte gone), and which slot
        the activity was read from (``activity_off`` / ``activity_via``) so a
        Moved offset is visible too.

        Read-only; call it on a state CHANGE, never per tick (``map_id`` may
        fall back to a throttled config scan).
        """
        info: dict = {"gamelayer": None, "activity_class": None,
                      "activity_chain": [], "activity_kind": None,
                      "activity_off": None, "activity_via": None,
                      "activity_id": None, "map_id": None,
                      "cfg_off": None, "cfg_type": None, "rift_byte": None,
                      "in_dungeon": False, "in_rift": False}
        gl = self.gamelayer(pbase)
        info["gamelayer"] = gl
        if gl is None:
            return info
        try:
            info["activity_off"] = self._activity_offset(gl)
        except ProcError:
            pass
        if info["activity_off"] is not None:
            try:
                act = self._safe_ptr(gl + info["activity_off"])
                if act:
                    info["activity_class"] = self.hl.class_of(act)
                    info["activity_chain"] = self.hl.super_chain(act, limit=8)
                    info["activity_kind"] = self._activity_kind(gl)
            except ProcError:
                pass
        info["activity_via"] = self._act_via
        try:
            raw = self.proc.try_read(gl + OFF_RIFT_BOOL, 1)
            info["rift_byte"] = raw[0] if raw else None
        except ProcError:
            pass
        try:
            info["activity_id"] = self.activity_id(pbase)
        except ProcError:
            pass
        try:
            info["map_id"] = self.map_id(pbase)
        except ProcError:
            pass
        info["cfg_off"] = self._cfg_off
        if self._cfg_type:
            info["cfg_type"] = self.hl.type_name(self._cfg_type)
        info["in_dungeon"] = self.in_dungeon(pbase)
        info["in_rift"] = self.is_rift(pbase)
        return info

    def _clean_id(self, raw_id: str | None) -> str | None:
        """Canonical form of a unit/element id - see data.units.canonical_unit_id.

        ONE rule, in the data layer, because this reader is no longer the only
        one: the bridge's kill notification carries the same `unitId` field off
        the wire and the event path compares it against what this walk stored,
        so both sides have to reduce it identically. Kept as a method because
        the walk reads better for it, and it delegates rather than repeating
        the rule.
        """
        return canonical_unit_id(raw_id)

    def units(self, pbase: int | None) -> list[Entity]:
        gl = self.gamelayer(pbase)
        if gl is None:
            return []
        ptrs = [p for p in self.hl.array(self.hl.ptr(gl + OFF_UNITS_ARR)) if is_ptr(p)]
        if not ptrs:
            return []
        blocks = self.proc.read_many(ptrs, UNIT_BLOCK)

        # HP can't come out of the unit block: the value lives on a SEPARATE
        # allocation (ent.GameObject.attributes, a pointer at OFF_UATTR, whose
        # own +OFF_HEALTH holds the f64), so no UNIT_BLOCK width reaches it. It
        # needn't be one read per unit either - the attribute pointers come out
        # of the block already read, so ONE batched read of every HP field
        # replaces N Python-level try_read calls (and N bytes allocations, each
        # its own FFI crossing) with a single native call that releases the GIL
        # across the whole batch. See native/src/lib.rs `read_many`.
        #
        # With the owner-class memo below, a walk is now a CONSTANT number of
        # process reads: 11 measured at 40, 120 AND 250 units, where it used to
        # be ~4.2 per unit (169 for a 40-unit scene, ~1000 for 250). A busy
        # instance runs this walk ~10 times a second.
        attr_ptrs = []
        for blk in blocks:
            if blk and len(blk) >= OFF_UATTR + 8:
                ap = struct.unpack_from("<Q", blk, OFF_UATTR)[0]
                attr_ptrs.append(ap if is_ptr(ap) else 0)
            else:
                attr_ptrs.append(0)
        ranged = [i for i, ap in enumerate(attr_ptrs) if ap]
        # Health offset resolved per attributes type (reflected, cached; falls
        # back to OFF_HEALTH) — same answer `attributes.health()` gets, so the
        # batched scan and the single-unit reader can never disagree.
        hp_off = {ap: attributes.health_offset(self.hl, ap)
                  for ap in {attr_ptrs[i] for i in ranged}}
        hp_reads = (self.proc.read_many([attr_ptrs[i] + hp_off[attr_ptrs[i]]
                                         for i in ranged], 8) if ranged else [])
        # A unit whose attributes object is gone or reads as garbage keeps 0.0 -
        # every consumer already reads 0.0 as "unreadable" (the DPS death
        # watcher only fires on a prev>0 -> hp<=0 edge, the Dungeon HUD skips
        # it when pegging HP).
        hp_by_unit = {i: attributes.decode_health(hp_reads[n])
                      for n, i in enumerate(ranged)}

        # Every unit in the scene names one of a handful of owners (a single
        # LayerChunk owns the world's mobs, one Hero owns its pets), and
        # `class_of` is a read on the instance. Memoising it per walk is what
        # takes a unit walk from ~1 read per unit to a constant: 40 units and
        # 120 units both cost the same handful after the batch.
        owner_cls: dict[int, str | None] = {}

        def owner_class(cand: int) -> str | None:
            if cand in owner_cls:
                return owner_cls[cand]
            cls = self.hl.class_of(cand)
            owner_cls[cand] = cls
            return cls

        out: list[Entity] = []
        for unit_i, (ptr, blk) in enumerate(zip(ptrs, blocks)):
            if blk is None or len(blk) < UNIT_BLOCK:
                continue
            type_ptr = struct.unpack_from("<Q", blk, 0)[0]
            x, y, z = struct.unpack_from("<ddd", blk, OFF_POS)
            uid_ptr = struct.unpack_from("<Q", blk, OFF_UNITID)[0]
            lvl = struct.unpack_from("<i", blk, OFF_LEVEL_UNIT)[0]
            
            hp = hp_by_unit.get(unit_i) or 0.0

            anc = self.hl.ancestors(type_ptr)
            
            unit_id = self.hl.hl_string(uid_ptr) if is_ptr(uid_ptr) else None
            unit_id = self._clean_id(unit_id)
            
            # Determine the owner:
            # o2 = OFF_HERO_OWNERPLAYER (0x498): Hero's ownerPlayer -> st.Player (highest authority)
            # o1 = OFF_FOE_OWNER (0x78):  Foe/pet's player field -> ent.Hero owner (confirmed via dump_elements)
            o1 = struct.unpack_from("<Q", blk, OFF_FOE_OWNER)[0]       # Foe/pet -> Hero owner
            o2 = struct.unpack_from("<Q", blk, OFF_HERO_OWNERPLAYER)[0] if len(blk) > OFF_HERO_OWNERPLAYER else 0  # Hero -> st.Player
            
            # Resolved once per candidate per WALK rather than once per pass per
            # unit: `class_of` is a read (the instance's +0 type pointer, then the
            # cached type name), and the three passes below ask about the same
            # one or two pointers. Same answers - within a walk a given
            # instance's type pointer cannot change.
            cls_of = {c: (owner_class(c) if is_ptr(c) else None)
                      for c in (o1, o2)}

            final_owner_ptr = 0
            final_owner_cls = None

            # 1. Try to find a st.Player directly (highest authority)
            for cand in (o2, o1):
                if is_ptr(cand) and cls_of[cand] == "st.Player":
                    final_owner_ptr, final_owner_cls = cand, cls_of[cand]
                    break

            # 2. If no Player found, look for an ent.Hero subclass (typical for pets)
            if not final_owner_ptr:
                for cand in (o2, o1):
                    cls = cls_of[cand] if is_ptr(cand) else None
                    if cls == "ent.Hero" or (cls and cls.startswith("ent.hero.")):
                        final_owner_ptr, final_owner_cls = cand, cls
                        break

            # 3. Last resort: any valid pointer (for engine-owned objects)
            if not final_owner_ptr:
                for cand in (o1, o2): # Prefer o1 (GameObject.owner) for non-hero units
                    if is_ptr(cand):
                        final_owner_ptr = cand
                        final_owner_cls = cls_of[cand]
                        break

            out.append(Entity(
                addr=ptr,
                cls=self.hl.type_name(type_ptr),
                unit_id=unit_id,
                owner_addr=final_owner_ptr,
                owner_cls=final_owner_cls,
                x=x, y=y, z=z,
                level=lvl,
                hp=hp,
                is_foe="ent.Foe" in anc,
                is_hero="ent.Hero" in anc,
            ))
        return out

    # One element walk serves every reader for this long. The element array is
    # re-derivable stateless, but it is NOT free: a walk is a gamelayer resolve,
    # an array walk and one batched read of the whole element header block plus
    # two struct-unpacked objects per element. Measured on the shipping readers,
    # the minimap (300 ms heavy rescan), the Entity HUD (350 ms), the Dungeon HUD
    # (350 ms) and the tracker (33 ms) all call into it, and ten different
    # accessors filter the same list - gatherables / obelisks / live orbs / chest
    # orbs / chests / teleporters / loot / food / world orb fx. Simulated over
    # one second of those cadences on a 150-element scene, that is 81 walks and
    # 567 process reads, each walk rebuilding the same 150-element list. The
    # cache collapses those to at most one walk per window, so within a window
    # the minimap and the Dungeon HUD are literally handed the SAME list and
    # cannot disagree about an orb that despawned between their two walks.
    #
    # A TTL is the right tool here and a per-tick snapshot is not: the overlays
    # tick on their own QTimers (33-350 ms) while the snapshot is published by
    # the overlay manager's tick (500 ms), so a snapshot'd scan would make the
    # minimap lag up to half a second behind the game. 150 ms is under one heavy
    # rescan interval, so a reader always sees a scan at most one tick old - and
    # the same 150-element scene costs 7 walks and 49 reads a second instead.
    ELEMS_TTL = 0.15

    def elements(self, pbase: int | None, *, fresh: bool = False) -> list[Element]:
        """Every loaded interactible element.

        Cached for `ELEMS_TTL` per `pbase`, so the many readers that filter this
        list in the same frame share ONE walk (see the note above). `fresh=True`
        forces a new walk - for a test, or a caller that must see a scene change
        the moment it happens.

        An empty result is never cached: it is what a loading screen and a
        failed gamelayer resolve both look like, and pinning that for 150 ms
        would blank the minimap's POIs well after the scene came back.
        """
        now = time.monotonic()
        if (not fresh and self._elems_key == pbase and self._elems
                and now - self._elems_at < self.ELEMS_TTL):
            return self._elems
        out = self._scan_elements(pbase)
        self._elems, self._elems_key, self._elems_at = out, pbase, now
        return out

    def _scan_elements(self, pbase: int | None) -> list[Element]:
        gl = self.gamelayer(pbase)
        if gl is None:
            return []
        ptrs = [p for p in self.hl.array(self.hl.ptr(gl + OFF_ELEMS_ARR)) if is_ptr(p)]
        if not ptrs:
            return []
        # Field offsets resolved per concrete element type. The 2026-09-18
        # orb regression (OFF_ELEM_FX drifted; a garbage read marked every
        # orb collected) applies to kind/currentVisualState exactly the same
        # way, so both reflect first and keep the constants as fallback.
        # The batched header block stays the fast path: a field whose resolved
        # offset IS its constant (the healthy build) reads from the block as
        # before; only a genuinely moved field pays a per-element read.
        block = OFF_ELEMSTATE + 8
        blocks = self.proc.read_many(ptrs, block)
        out: list[Element] = []
        for ptr, blk in zip(ptrs, blocks):
            if blk is None or len(blk) < block:
                continue
            type_ptr = struct.unpack_from("<Q", blk, 0)[0]
            x, y, z = struct.unpack_from("<ddd", blk, OFF_POS)
            id_offs = self._elem_field_offsets(type_ptr, ELEM_ID_FIELDS,
                                               OFF_ELEMID)
            state_offs = self._elem_field_offsets(type_ptr, ELEM_STATE_FIELDS,
                                                  OFF_ELEMSTATE)
            elem_id = self._scan_string(blk, ptr, id_offs, OFF_ELEMID)
            elem_id = self._clean_id(elem_id)
            state = self._scan_string(blk, ptr, state_offs, OFF_ELEMSTATE)

            out.append(Element(
                addr=ptr,
                cls=self.hl.type_name(type_ptr),
                elem_id=elem_id,
                state=state,
                x=x, y=y, z=z,
                type_ptr=type_ptr,
            ))
        return out

    def _scan_string(self, blk: bytes, obj: int,
                     offs: dict[str, int | None], const: int) -> str | None:
        """One String field for the element walk: from the batched block when
        the resolved offset is the constant (fast path), else one read at the
        reflected offset. Only a read whose value really classifies as String
        is returned — a stale slot's non-string garbage is 'no data'."""
        ref = offs.get("reflected")
        try:
            if ref is None or ref == const:
                v = struct.unpack_from("<Q", blk, const)[0]
                if is_ptr(v):
                    s = self.hl.hl_string(v)
                    return s if (s and self._is_string_obj(v)) else None
                return None
            sp = self.hl.ptr(obj + ref)
            if is_ptr(sp) and self.hl.class_of(sp) == "String":
                return self.hl.hl_string(sp)
        except ProcError:
            return None
        return None

    def _is_string_obj(self, strobj: int) -> bool:
        """Cheap classifier for a candidate String object. The batched-block
        fast path trusts the heap's own +0 type pointer instead of paying a
        full class_of; class_of (used on the slow path) is stricter and
        remains the tie-breaker."""
        try:
            tp = self.hl.ptr(strobj)
        except ProcError:
            return False
        if not is_ptr(tp):
            return False
        return self.hl.type_is_a(tp, "String") or self.hl.class_of(strobj) == "String"

    _RARITY_NAMES = ("Common", "Uncommon", "Rare", "Epic", "Legendary")

    def _loot_rarity(self, item_ptr: int) -> str | None:
        """Live rolled rarity off the drop's st.Item. Dungeon/rift gear spawns
        at a rolled rarity above the authored template's, so the static
        catalog alone reads low (Epic drops showing as Rare). Reflection
        'rarity' first; a String field reads directly, a small int maps to
        the Common..Legendary enum."""
        try:
            tp = self.hl.ptr(item_ptr)
            off = self.hl.field_offset(tp, "rarity") if tp else None
            if not off:
                return None
            raw = self.proc.try_read(item_ptr + off, 8)
            if raw is None:
                return None
            v = struct.unpack("<Q", raw)[0]
            if is_ptr(v):
                try:
                    if self.hl.class_of(v) == "String":
                        s = self.hl.hl_string(v)
                        return s or None
                except ProcError:
                    pass
                return None
            if v < len(self._RARITY_NAMES):
                return self._RARITY_NAMES[v]
        except ProcError:
            pass
        return None

    def loot_drops(self, pbase: int | None) -> list[LootDrop]:
        """Dropped-loot entities with their item resolved.

        ent.interactible.LootDrop extends ent.Interactible with its own fields:
        item (st.Item*) whose kind is the item id ('MoteOfChaos'), count (i32),
        players (ArrayObj). All shifted +0x20 in the 2026-09-11 patch (2026-08-23
        constants). Those slots sit on a DIFFERENT class from ent.Element's, so
        they drift independently — item and count reflect per type first
        (LOOT_*_FIELDS / ITEM_KIND_FIELDS) with the constants as fallback, and a
        failed read degrades to no data (count -> 1) instead of a stale-slot lie.
        Confirmed live 2026-08-23 (ai/workspace/opencode/scan_probe capture #2)."""
        out: list[LootDrop] = []
        for el in self.elements(pbase):
            if el.cls != "ent.interactible.LootDrop":
                continue
            item_id: str | None = None
            item_ptr = 0
            item_offs = self._elem_field_offsets(
                el.type_ptr, LOOT_ITEM_FIELDS, OFF_LOOT_ITEM)
            try:
                item_ptr = self._read_ptr_field(el.addr, item_offs) or 0
                if is_ptr(item_ptr):
                    itp = self.hl.ptr(item_ptr)
                    kind_offs = self._elem_field_offsets(
                        itp, ITEM_KIND_FIELDS, OFF_ITEM_KIND)
                    kptr = self._read_ptr_field(item_ptr, kind_offs)
                    if is_ptr(kptr) and self.hl.class_of(kptr) == "String":
                        item_id = self.hl.hl_string(kptr)
            except ProcError:
                item_id = None
            count_offs = self._elem_field_offsets(
                el.type_ptr, LOOT_COUNT_FIELDS, OFF_LOOT_COUNT)
            try:
                count = self._read_i32_field(el.addr, count_offs, 1, _MAX_STACK)
            except ProcError:
                count = None
            out.append(LootDrop(
                addr=el.addr,
                item_id=item_id or None,
                count=count if (count or 0) > 0 else 1,
                x=el.x, y=el.y, z=el.z,
                rarity=(self._loot_rarity(item_ptr)
                        if is_ptr(item_ptr) else None),
            ))
        return out

    def food_stations(self, pbase: int | None) -> list[FoodStation]:
        """Player-placed consumables (Plainswalker Feast, alchemist cauldrons).

        Placed food shows up under EITHER class depending on context:
          - st.skill.object.WorldConsumable (dedicated consumable element)
          - st.skill.SkillObject (same layout as combat casts, so those only
            count when the resolved skill name smells like food)
        Both reuse the ent.Element.kind slot (@0x290) as an st.skill.Skill*;
        the per-food name is resolved via Skill.originItem.kind (the generic
        skill kind alone reads identically for every food)."""
        out: list[FoodStation] = []
        for el in self.elements(pbase):
            clsl = (el.cls or "").lower()
            is_consumable = "worldconsumable" in clsl
            is_skill_obj = (not is_consumable) and "skillobject" in clsl
            if not (is_consumable or is_skill_obj):
                continue
            name: str | None = None
            try:
                # The skill slot usually rides ent.Element.kind (OFF_ELEMID)
                # on a WorldConsumable, but the class may declare its own
                # 'skill' field — probe BOTH vocabularies, reflected first.
                skill_offs = self._elem_field_offsets(
                    el.type_ptr, CONSUMABLE_SKILL_FIELDS, OFF_ELEMID)
                skill_ptr = self._read_ptr_field(el.addr, skill_offs)
                if is_ptr(skill_ptr) and self._looks_like_skill(skill_ptr):
                    name = self._skill_display_name(skill_ptr)
                if not name and is_consumable:
                    skill_offs = self._elem_field_offsets(
                        el.type_ptr, ELEM_ID_FIELDS, OFF_ELEMID)
                    skill_ptr = self._read_ptr_field(el.addr, skill_offs)
                    if is_ptr(skill_ptr) and self._looks_like_skill(skill_ptr):
                        name = self._skill_display_name(skill_ptr)
                    else:
                        name = self._skill_display_name(el.addr)
            except ProcError:
                name = None
            if is_skill_obj and not (
                    name and any(h in name.lower()
                                 for h in _FOOD_NAME_HINTS)):
                continue   # a combat cast, not placed food
            out.append(FoodStation(
                addr=el.addr,
                name=name,
                x=el.x, y=el.y, z=el.z,
            ))
        return out

    def _skill_display_name(self, skill_ptr: int) -> str | None:
        """Pull a readable display string off an st.skill.Skill object.

        Specific identity FIRST: every placed food shares the generic skill
        kind ('ConsumeFood'/'PrepareWorldConsumable'), but BaseSkill.originItem
        points at the real st.Item whose kind is the per-food id ('Feast',
        'Cook_1', ...). Only falls back to the generic skill strings."""
        try:
            tp = self.hl.ptr(skill_ptr)
        except ProcError:
            return None
        if not is_ptr(tp):
            return None
        # per-item identity: BaseSkill.originItem.kind (e.g. 'Feast')
        try:
            o_off = self.hl.field_offset(tp, "originItem")
            item_ptr = self.hl.ptr(skill_ptr + o_off) if o_off else None
            if is_ptr(item_ptr):
                itp = self.hl.ptr(item_ptr)
                k_off = self.hl.field_offset(itp, "kind") if itp else None
                kp = self.hl.ptr(item_ptr + k_off) if k_off else None
                if is_ptr(kp) and self.hl.class_of(kp) == "String":
                    s = self.hl.hl_string(kp)
                    if s:
                        return s
        except ProcError:
            pass
        # reflection pass over the usual id-ish fields
        try:
            fields = [(nm, self.hl.field_offset(tp, nm)) for nm in
                      ("kind", "id", "skillId", "name")]
            for _nm, off in fields:
                if not off:
                    continue
                try:
                    sp = self.hl.ptr(skill_ptr + off)
                    if is_ptr(sp) and self.hl.class_of(sp) == "String":
                        s = self.hl.hl_string(sp)
                        if s:
                            return s
                except ProcError:
                    continue
        except ProcError:
            pass
        # fallback: sweep the head of the block for ANY String field
        try:
            blk = self.proc.try_read(skill_ptr, 0x80)
            if blk:
                for off in range(8, len(blk) - 7, 8):
                    v = struct.unpack_from("<Q", blk, off)[0]
                    if not is_ptr(v):
                        continue
                    try:
                        if self.hl.class_of(v) == "String":
                            s = self.hl.hl_string(v)
                            if s:
                                return s
                    except ProcError:
                        continue
        except ProcError:
            pass
        return None
