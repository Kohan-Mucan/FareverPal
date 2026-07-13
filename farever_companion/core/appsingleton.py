"""GameApp-singleton anchor: scan-free, session-stable player locate.

Anchor on the GameApp singleton and pointer-chase to the local Hero, instead of
scanning the heap for ent.Hero every zone change. The anchor is cached for the
session; hero() then just re-reads a field, following character/zone changes.

The chain, validated live 2026-06-02:

    GameApp hl_type --(super, +0x18)--> App hl_type
    App hl_type_obj --(global_value, +0x38)--> &globals_data slot for $App
        *slot --> $App static object
            .inst (a GameApp)            == App.inst, the singleton
                GameApp.hero  : ent.Hero      <- the local player
                GameApp.me    : st.Player
                GameApp.layer : st.GameLayer

The only scan is finding the GameApp type once (one pass for the class name, then
a region-bounded pass for its type_obj). Offsets are re-resolved by class name so
they survive patches; resolved values are cached.
"""
from __future__ import annotations

import struct

from .hl import Hl, is_ptr, utf16z as _utf16z
from ..constants import TO_NAME, TO_SUPER, TO_GLOBALVAL
from .proc import Proc, ProcError

# Anchor class names.
CLS_GAMEAPP = "GameApp"
CLS_APP = "App"
CLS_HERO = "ent.Hero"
CLS_PLAYER = "st.Player"
CLS_LAYER = "st.GameLayer"
CLS_CAMERA = "client.GameCamera"   # GameApp.camera/gameCamera; orbit yaw follows the mouse
CLS_UI = "ui.GameUI"


# How far into an object to look for the pointer fields we resolve by class.
_FIELD_SCAN_BYTES = 0x400


class GameAppLocator:
    def __init__(self, proc: Proc, hl: Hl | None = None):
        self.proc = proc
        self.hl = hl or Hl(proc)
        self._gameapp_obj: int | None = None   # GameApp hl_type_obj
        self._static_obj: int | None = None    # $App static object (globals_data slot)
        self._addr: int | None = None          # GameApp singleton instance (re-read live)
        self.off_inst: int | None = None       # $App static obj -> inst
        self.off_hero: int | None = None
        self.off_me: int | None = None
        self.off_layer: int | None = None
        self.off_camera: int | None = None     # GameApp.camera (client.BaseCamera)
        self.off_ui: int | None = None


    # --- low-level safe reads --------------------------------------------
    def _u64(self, addr: int) -> int | None:
        b = self.proc.try_read(addr, 8)
        return struct.unpack("<Q", b)[0] if b else None

    # --- type_obj lookup by name (the only scan) -------------------------
    # The HL type metadata + globals_data live in small RW regions clustered low
    # in the address space (~0x2aa47… in this build) and are hot (in the working
    # set), whereas the multi-GB GC object heap sits higher and is largely paged
    # out. So we walk regions ASCENDING, scan only writable ones, and early-exit
    # the moment the chain validates - we never touch the cold heap. Big regions
    # are skipped during the name search (type-name pools are small).
    _MAX_NAME_REGION = 0x4000000   # 64 MB - metadata regions are far smaller

    def _committed_ranges_near(self, addr: int, span: int = 0x1000000
                               ) -> list[tuple[int, int]]:
        """Committed regions overlapping [addr-span, addr+span], each as its own
        (base, size) so a scan never straddles an unmapped gap."""
        lo, hi = addr - span, addr + span
        out = []
        for (base, size, _prot, _w) in self.proc.regions():
            if base + size <= lo or base >= hi:
                continue
            out.append((base, size))
        return out

    def _typeobj_candidates(self, name_addr: int) -> list[int]:
        """Every structure with a pointer to `name_addr` at +0x10, candidate
        hl_type_objs. Several spots reference a type-name string, so this is just
        a candidate set; the real one is decided by running the chain."""
        out = []
        for ref in self.proc.find_qword_in(
                name_addr, self._committed_ranges_near(name_addr), max_hits=64):
            out.append(ref - TO_NAME)
        return out

    def _iter_gameapp_type_objs(self):
        """Yield candidate GameApp hl_type_objs by an ascending, early-ish walk
        over small writable regions (skips the cold multi-GB GC heap)."""
        if not self.proc.has_scan:
            raise ProcError("locate needs the farever_native scan backend")
        needle = _utf16z(CLS_GAMEAPP)
        regs = sorted(self.proc.regions(), key=lambda r: r[0])
        for (base, size, _prot, writable) in regs:
            if not writable or size > self._MAX_NAME_REGION:
                continue
            for name_addr in self.proc.find_bytes_in(needle, [(base, size)],
                                                     align=2, max_hits=8):
                if self.hl._read_utf16(name_addr) != CLS_GAMEAPP:
                    continue
                yield from self._typeobj_candidates(name_addr)

    def _typeobj_name(self, type_obj: int) -> str | None:
        nm = self._u64(type_obj + TO_NAME)
        return self.hl._read_utf16(nm) if nm else None

    # --- the no-scan chain -----------------------------------------------
    def _static_app_obj_from(self, gameapp_obj: int) -> int | None:
        """$App static object via GameApp.super(App).global_value, for a specific
        candidate GameApp type_obj. Returns None if the candidate isn't a real
        type_obj (the chain dereferences garbage and bails)."""
        app_type = self._u64(gameapp_obj + TO_SUPER)              # App hl_type
        if not is_ptr(app_type):
            return None
        if self.hl.type_name(app_type) != CLS_APP:               # super must be App
            return None
        app_obj = self._u64(app_type + 8)                        # App hl_type_obj
        if not is_ptr(app_obj):
            return None
        slot = self._u64(app_obj + TO_GLOBALVAL)                 # &globals_data[..]
        if not is_ptr(slot):
            return None
        static_obj = self._u64(slot)                             # $App static object
        return static_obj if is_ptr(static_obj) else None

    def _scan_for_class(self, base: int, wanted: dict[str, str]) -> dict[str, int]:
        """Map {class_name: attr} -> {attr: offset} by reading `base`'s pointer
        slots and matching the class each points at. Returns first match each."""
        out: dict[str, int] = {}
        raw = self.proc.try_read(base, _FIELD_SCAN_BYTES)
        if raw is None:
            return out
        for off in range(8, len(raw) - 8, 8):
            ptr = struct.unpack_from("<Q", raw, off)[0]
            if not is_ptr(ptr):
                continue
            cls = self.hl.class_of(ptr)
            attr = wanted.get(cls) if cls else None
            if attr and attr not in out:
                out[attr] = off
                if len(out) == len(wanted):
                    break
        return out

    def _resolve_fields(self, singleton: int) -> None:
        """Resolve the hero/me/layer field offsets by class from a live singleton.
        Only resolves offsets still unknown, so it's a cheap no-op once anchored;
        one 0x400 read otherwise. At the main menu the hero field is null (no
        instance to match), so off_hero stays None until the player loads in -
        this is then re-attempted each call so the anchor self-heals in-world."""
        wanted: dict[str, str] = {}
        if self.off_hero is None:
            wanted[CLS_HERO] = "hero"
        if self.off_me is None:
            wanted[CLS_PLAYER] = "me"
        if self.off_layer is None:
            wanted[CLS_LAYER] = "layer"
        if self.off_camera is None:
            # GameApp holds both `camera` (active) and `gameCamera`; in normal play
            # both point at the GameCamera, so the first (lower-offset) match wins.
            wanted[CLS_CAMERA] = "camera"
        if self.off_ui is None:
            wanted[CLS_UI] = "ui"
        if not wanted:
            return
        found = self._scan_for_class(singleton, wanted)
        self.off_hero = found.get("hero", self.off_hero)
        self.off_me = found.get("me", self.off_me)
        self.off_layer = found.get("layer", self.off_layer)
        self.off_camera = found.get("camera", self.off_camera)
        self.off_ui = found.get("ui", self.off_ui)


    def _try_chain(self, gameapp_obj: int) -> int | None:
        """Full chain for one candidate type_obj -> validated GameApp singleton,
        or None. The chain (GameApp -> super App -> $App static -> .inst, whose
        class must be GameApp) uniquely identifies the singleton WITHOUT needing
        a hero, so it commits at the main menu too; the hero/me/layer offsets are
        resolved opportunistically and may stay None until the player is in-world."""
        static_obj = self._static_app_obj_from(gameapp_obj)
        if static_obj is None:
            return None
        inst_m = self._scan_for_class(static_obj, {CLS_GAMEAPP: "inst"})
        off_inst = inst_m.get("inst")
        if off_inst is None:
            return None
        singleton = self._u64(static_obj + off_inst)
        if not is_ptr(singleton) or self.hl.class_of(singleton) != CLS_GAMEAPP:
            return None
        # commit. `static_obj` is a globals_data slot (stable for the process), so
        # cache it: re-reading `static_obj + off_inst` each frame yields the CURRENT
        # singleton, which the game RECREATES on a world reload - so caching the
        # singleton itself would go stale, but the static slot does not.
        self._gameapp_obj = gameapp_obj
        self._static_obj = static_obj
        self.off_inst = off_inst
        self._resolve_fields(singleton)
        return singleton

    # --- locate ----------------------------------------------------------
    def locate(self) -> int | None:
        # cached singleton stays valid for the session
        if self._addr is not None and self.hl.class_of(self._addr) == CLS_GAMEAPP:
            self._resolve_fields(self._addr)   # pick up the hero field once in-world
            return self._addr
        self._addr = None
        # fast path: we already know the right type_obj from a previous locate
        if self._gameapp_obj is not None:
            singleton = self._try_chain(self._gameapp_obj)
            if singleton is not None:
                self._addr = singleton
                return singleton
            self._gameapp_obj = None
        # cold path: find the type_obj whose chain reaches the GameApp singleton
        for cand in self._iter_gameapp_type_objs():
            singleton = self._try_chain(cand)
            if singleton is not None:
                self._addr = singleton
                return singleton
        return None

    # --- reads (pure field follow, no scan) ------------------------------
    @property
    def reachable(self) -> bool:
        """True once the stable `$App` static slot + the inst offset are resolved,
        i.e. we can re-resolve the live GameApp singleton on demand with no scan -
        even at the main menu, before any hero exists. This is the signal to stop
        the expensive heap-scan fallback: the chain is found, just wait in-world."""
        return self._static_obj is not None and self.off_inst is not None

    @property
    def anchored(self) -> bool:
        """`reachable` AND the hero field offset is resolved, i.e. live_hero() can
        actually read a hero. (Resolved the first time the player is in-world.)"""
        return self.reachable and self.off_hero is not None

    def hero(self) -> int | None:
        if not self.reachable:
            if self.locate() is None:
                return None
        return self.live_hero()

    def live_hero(self) -> int | None:
        """Live `GameApp.hero`, re-resolved through the STABLE `$App.inst` slot
        with NO scan. Re-reading `$App.inst` each call follows a singleton the
        game RECREATES on a world reload (caching the singleton would go stale);
        re-reading `.hero` follows a character change. Returns None when not yet
        reachable, or when there's no live hero right now (main menu / loading) -
        either because the singleton or the hero field is currently null. While
        the hero offset is still unknown (only the menu has been seen) it's
        re-resolved here, so the anchor self-heals the moment the player loads."""
        if not self.reachable:
            return None
        singleton = self._u64(self._static_obj + self.off_inst)
        if not is_ptr(singleton) or self.hl.class_of(singleton) != CLS_GAMEAPP:
            self._addr = None
            return None
        self._addr = singleton                      # refresh cache for me()/layer()
        # self-heal field offsets once in-world; keep retrying while ANY is still
        # unknown (the camera can be null the instant the hero first resolves, so
        # gating only on off_hero could leave off_camera permanently unset).
        if self.off_hero is None or self.off_camera is None:
            self._resolve_fields(singleton)
        if self.off_hero is None:
            return None
        hero = self._u64(singleton + self.off_hero)
        return hero if is_ptr(hero) else None

    def me(self) -> int | None:
        if self._addr is None or self.off_me is None:
            return None
        return self.hl.ptr(self._addr + self.off_me)

    def layer(self) -> int | None:
        if self._addr is None or self.off_layer is None:
            return None
        return self.hl.ptr(self._addr + self.off_layer)

    def camera(self) -> int | None:
        """The active gameplay camera (client.BaseCamera). Relies on `_addr` being
        the current singleton - live_hero() refreshes it each tick before the
        minimap reads the yaw."""
        if self._addr is None or self.off_camera is None:
            return None
        return self.hl.ptr(self._addr + self.off_camera)

    def ui(self) -> int | None:
        if not self.reachable:
            if self.locate() is None:
                return None
        return self.live_ui()

    def live_ui(self) -> int | None:
        if not self.reachable:
            return None
        singleton = self._u64(self._static_obj + self.off_inst)
        if not is_ptr(singleton) or self.hl.class_of(singleton) != CLS_GAMEAPP:
            self._addr = None
            return None
        self._addr = singleton
        if self.off_ui is None:
            self._resolve_fields(singleton)
        if self.off_ui is None:
            return None
        ui_ptr = self._u64(singleton + self.off_ui)
        return ui_ptr if is_ptr(ui_ptr) else None

