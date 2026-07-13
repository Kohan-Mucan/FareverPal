"""Compass-needle track controller.

One tracking target for the whole app, owned by the OverlayManager: secret
orbs (static world position) or live units ("unit" kind locks onto the
*nearest* instance of that unit id each tick, so it follows the top entry of
the entity list). Drives the centre-screen NeedleOverlay on its own fast timer
so the needle stays smooth regardless of which overlays are open. The target
persists in Settings (track_kind / track_id) and resumes on attach.
Read-only: consumes LiveModel only.
"""
from __future__ import annotations

import math
import sys
import time

from PySide6 import QtCore

from .nav_needle import NeedleOverlay
from ..data import names
from ..geo import nav, orbs as geo_orbs

TICK_MS = 33
ARRIVE = 4.0          # world units = "you are here" (matches nav_needle)

# world-space needle decal dimensions (world units, ground plane)
N_TIP = 3.0
N_SHOULDER = 1.2
N_HALF_W = 0.25
N_BASE = 0.65
N_TAIL = 1.3
N_TAIL_W = 0.25
RING_R = 0.7


def _game_window_rect(pid: int):
    """Client-area rect (x, y, w, h) of the game's main window, or None."""
    if not sys.platform.startswith("win"):
        return None
    import ctypes
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    best = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        r = wt.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(r)):
            return True
        w, h = r.right, r.bottom
        if w < 320 or h < 240:
            return True
        pt = wt.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        best.append((w * h, pt.x, pt.y, w, h))
        return True

    user32.EnumWindows(cb, 0)
    if not best:
        return None
    _a, x, y, w, h = max(best)
    return (x, y, w, h)


class TrackController(QtCore.QObject):
    changed = QtCore.Signal()     # target set/cleared -> overlays re-render rows

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.model = None
        self._needle: NeedleOverlay | None = None
        self._lock_addr: int | None = None    # sticky unit instance (hysteresis)
        self._hard_lock = False               # True if set via manual HUD click
        self._rect = None                     # game window rect cache
        self._rect_at = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    # --- lifecycle ---------------------------------------------------------
    def set_model(self, model) -> None:
        """Model on attach, None on detach (needle hides, target persists)."""
        self.model = model
        if model is None:
            self._timer.stop()
            if self._needle is not None:
                self._needle.hide()
        elif self.s.track_kind and self.s.track_id:
            self._start()

    def shutdown(self) -> None:
        self._timer.stop()
        if self._needle is not None:
            self._needle.close()
            self._needle = None

    # --- target ------------------------------------------------------------
    def is_tracked(self, kind: str, key: str) -> bool:
        return bool(key) and self.s.track_kind == kind and self.s.track_id == key

    def toggle(self, kind: str, key: str, addr=None) -> None:
        if self.is_tracked(kind, key) and (addr is None or self._lock_addr == addr):
            self.clear()
        else:
            self.track(kind, key)
            # Seed the lock immediately so the HUD badge appears on the clicked
            # entity right away, not whichever instance is closest on the next tick
            if addr is not None:
                self._lock_addr = addr
                self._hard_lock = True

    def track(self, kind: str, key: str, addr=None) -> None:
        if kind == "orb" and key not in geo_orbs.by_id():
            return
        self.s.track_kind, self.s.track_id = kind, key
        self._lock_addr = addr
        self._hard_lock = addr is not None
        self.s.save()
        if self.model is not None:
            self._start()
        self.changed.emit()

    def clear(self) -> None:
        self.s.track_kind = self.s.track_id = ""
        self._lock_addr = None
        self._hard_lock = False
        self.s.save()
        self._timer.stop()
        if self._needle is not None:
            self._needle.hide()
        self.changed.emit()

    @property
    def locked_addr(self) -> int | None:
        """The live unit instance the needle is locked on (see _target)."""
        return self._lock_addr

    def set_opacity(self, value: float) -> None:
        if self._needle is not None:
            self._needle.set_opacity(value)

    # --- tick --------------------------------------------------------------
    def _start(self) -> None:
        if not getattr(self.s, "show_compass", False):
            if self._needle is not None:
                self._needle.hide()
            self._timer.stop()
            return
        if self._needle is None:
            self._needle = NeedleOverlay(self.s)
        self._needle.show()
        self._timer.start(TICK_MS)
        self._tick()

    def _target(self):
        """(x, y, z, label) of the current target, or (None, label) shapes:
        returns None when the target is gone for good, ('searching', label)
        when a tracked unit just isn't in the loaded scene right now."""
        kind, key = self.s.track_kind, self.s.track_id
        if kind == "orb":
            o = geo_orbs.by_id().get(key)
            if o is None:
                return None
            return (o.x, o.y, o.z,
                    f"{geo_orbs.orb_label(key)} · {geo_orbs.orb_region_name(o)}")
        if kind in ("pos", "gather"):
            # fixed waypoint: "x,y,z|label" (any minimap marker)
            try:
                coords, _, label = key.partition("|")
                x, y, z = (float(v) for v in coords.split(","))
            except ValueError:
                return None
            return (x, y, z, label or ("Gatherable" if kind == "gather" else "Waypoint"))
        if kind in ("unit", "hero"):
            label = names.unit_name(key) or key
            xyz = self.model.player_xyz()
            cands = [e for e in self.model.units()
                     if e.unit_id == key
                     and (kind != "hero" or e.addr != self.model.player_addr)]
            if not cands:
                self._lock_addr = None
                return ("searching", label)
            if xyz is None:
                e = cands[0]
            else:
                e = min(cands, key=lambda e: e.dist(*xyz))
                # hysteresis: stay locked on the current instance unless a
                # clearly closer one appears, so near-ties don't flip the needle
                cur = next((c for c in cands if c.addr == self._lock_addr), None)
                if cur is not None:
                    if self._hard_lock:
                        e = cur
                    elif cur.dist(*xyz) <= e.dist(*xyz) * 1.25:
                        e = cur
            self._lock_addr = e.addr
            return (e.x, e.y, e.z, label)
        
        # All other kinds (chest, enemy, gather, pos) are coordinate-based
        try:
            coords, _, label = key.partition("|")
            x, y, z = (float(v) for v in coords.split(","))
            return (x, y, z, label or "Waypoint")
        except ValueError:
            return None

    def _game_rect(self):
        now = time.monotonic()
        if now - self._rect_at > 2.0:
            self._rect_at = now
            try:
                self._rect = _game_window_rect(self.model.proc.pid)
            except Exception:
                self._rect = None
        return self._rect

    def _tick(self) -> None:
        m = self.model
        if m is None or self._needle is None:
            return
        if m.player_addr is None:
            self.clear()
            return
        tgt = self._target()
        if tgt is None:
            self.clear()
            return
        xyz = m.player_xyz()
        searching = tgt[0] == "searching"
        if xyz is None:
            self._needle.set_searching(tgt[1] if searching else "")
            return
        # projected mode: draw through the game's own camera matrix, exactly
        # on the ground under the player
        M = m.view_matrix()
        rect = self._game_rect()
        if M and rect and self._tick_projected(M, rect, xyz, tgt, searching):
            return
        # fallback: centred pivot + yaw/pitch heuristics
        if searching:
            self._needle.set_searching(tgt[1])
            return
        px, py, pz = xyz
        tx, ty, tz, label = tgt
        phi = nav.view_phi(m.camera_yaw(), m.player_heading())
        ang = nav.needle_angle(px, py, tx, ty, phi)
        dist = ((tx - px) ** 2 + (ty - py) ** 2 + (tz - pz) ** 2) ** 0.5
        squash = nav.ground_squash(m.camera_pitch())
        self._needle.set_state(ang, dist, tz - pz, label, squash)

    def _tick_projected(self, M, rect, xyz, tgt, searching: bool) -> bool:
        rx, ry, rw, rh = rect
        px, py, pz = xyz

        def scr(wx, wy, wz):
            p = nav.project(M, wx, wy, wz)
            if p is None:
                return None
            return ((p[0] + 1) / 2 * rw, (1 - p[1]) / 2 * rh)

        ring = []
        for i in range(16):
            a = i * math.tau / 16
            pt = scr(px + math.cos(a) * RING_R, py + math.sin(a) * RING_R, pz)
            if pt is None:
                return False
            ring.append(pt)

        head = tail = None
        if searching:
            dist, dz, label = -1.0, 0.0, tgt[1]
        else:
            tx, ty, tz, label = tgt
            dx, dy = tx - px, ty - py
            dist = (dx * dx + dy * dy + (tz - pz) ** 2) ** 0.5
            dz = tz - pz
            flat = math.hypot(dx, dy)
            if dist > ARRIVE and flat > 1e-6:
                ux, uy = dx / flat, dy / flat
                nx, ny = -uy, ux
                slope = dz / dist
                slope = max(-1.0, min(1.0, slope))
                head = [
                    scr(px + ux * N_TIP, py + uy * N_TIP, pz + N_TIP * slope),
                    scr(px + ux * N_SHOULDER + nx * N_HALF_W, py + uy * N_SHOULDER + ny * N_HALF_W, pz + N_SHOULDER * slope),
                    scr(px + ux * N_BASE, py + uy * N_BASE, pz + N_BASE * slope),
                    scr(px + ux * N_SHOULDER - nx * N_HALF_W, py + uy * N_SHOULDER - ny * N_HALF_W, pz + N_SHOULDER * slope),
                ]
                tail = [
                    scr(px - ux * N_BASE + nx * N_TAIL_W, py - uy * N_BASE + ny * N_TAIL_W, pz - N_BASE * slope),
                    scr(px - ux * N_TAIL, py - uy * N_TAIL, pz - N_TAIL * slope),
                    scr(px - ux * N_BASE - nx * N_TAIL_W, py - uy * N_BASE - ny * N_TAIL_W, pz - N_BASE * slope),
                ]
                if any(p is None for p in head) or any(p is None for p in tail):
                    return False
        # window covers the game client area; coords above are window-local
        n = self._needle
        if (n.x(), n.y(), n.width(), n.height()) != (rx, ry, rw, rh):
            n.setGeometry(rx, ry, rw, rh)
        all_pts = ring + (head or []) + (tail or [])
        text_xy = (sum(p[0] for p in ring) / len(ring),
                   max(p[1] for p in all_pts) + 6)
        n.set_scene(head, tail, ring, text_xy, dist, dz, label)
        return True
