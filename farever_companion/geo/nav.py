"""Navigation math for the compass needle (pure, headless).

Same frame conventions as the minimap: world heading is an atan2 angle
(0 = +x, CCW) and north is low world-Y. `phi` is the minimap's view rotation
(pi/2 - camera yaw when rotating with the camera, 0 = north-up).
"""
from __future__ import annotations

import math

from ..constants import CAM_YAW_SIGN, CAM_YAW_OFFSET


def view_phi(cam_yaw: float | None, heading: float | None) -> float:
    """View rotation so the look direction points up. Prefer the free-look
    camera yaw (mouse orbit); fall back to the movement heading; 0 = north-up.
    Heading is a world atan2 (0=+x, CCW) and north is -y, so facing-up is
    phi = pi/2 + heading (the north-up Y flip in the projection carries the
    sign)."""
    if cam_yaw is not None:
        return math.pi / 2 - (CAM_YAW_SIGN * cam_yaw + CAM_YAW_OFFSET)
    if heading is not None:
        return math.pi / 2 + heading
    return 0.0


# --- engine-camera projection (h3d.Camera.m, row-vector convention) --------
# The exact way the game maps world -> screen. When the matrix reads, the
# needle/ring project through it directly and none of the yaw/pitch heuristics
# below are needed.

def project(M, x: float, y: float, z: float):
    """World point -> (ndc_x, ndc_y, w) through a 4x4 view-proj matrix given
    as a flat row-major list of 16 (heaps: p' = [x y z 1] * M). NDC is [-1,1],
    y up. None when the point is at/behind the camera plane."""
    w = x * M[3] + y * M[7] + z * M[11] + M[15]
    if w <= 1e-6:
        return None
    px = (x * M[0] + y * M[4] + z * M[8] + M[12]) / w
    py = (x * M[1] + y * M[5] + z * M[9] + M[13]) / w
    return (px, py, w)


def ground_view_phi(M, px: float, py: float, pz: float) -> float | None:
    """The minimap/needle view rotation derived from the real camera matrix:
    the ground direction that maps to screen-up, via the screen-space jacobian
    of the projection around the player. Replaces the yaw-field heuristic."""
    p0 = project(M, px, py, pz)
    pe = project(M, px + 1.0, py, pz)
    pn = project(M, px, py + 1.0, pz)
    if p0 is None or pe is None or pn is None:
        return None
    # J maps world (dx, dy) -> screen ndc (du, dv)
    a, b = pe[0] - p0[0], pn[0] - p0[0]      # du/dx, du/dy
    c, d = pe[1] - p0[1], pn[1] - p0[1]      # dv/dx, dv/dy
    det = a * d - b * c
    if abs(det) < 1e-9:
        return None
    # world dir that maps to screen-up (0, +1) in ndc (y up)
    ux = -b / det
    uy = a / det
    # minimap frame: east = +x, north = -y; phi rotates that dir to the top
    alpha = math.atan2(-uy, ux)
    return math.pi / 2 - alpha


GROUND_SQUASH_DEFAULT = 0.55    # when the camera pitch can't be read
GROUND_SQUASH_MIN = 0.20        # never collapse to a line near the horizon
GROUND_SQUASH_MAX = 0.98


def ground_squash(pitch: float | None) -> float:
    """Ground-plane foreshortening from the camera tilt: looking straight down
    (|pitch| = pi/2) shows the ground as a circle (1.0), level with the horizon
    flattens it to ~0. |sin| keeps either sign convention correct."""
    if pitch is None:
        return GROUND_SQUASH_DEFAULT
    return max(GROUND_SQUASH_MIN, min(GROUND_SQUASH_MAX, abs(math.sin(pitch))))


def needle_angle(px: float, py: float, tx: float, ty: float, phi: float) -> float:
    """Screen angle (radians, clockwise from up) from the player at (px, py)
    toward the target at (tx, ty), in the rotated view frame: 0 = walk forward
    (target dead ahead), pi/2 = target to the right."""
    dx = tx - px
    dy = py - ty                      # north-up: low world-Y = up
    c, s = math.cos(phi), math.sin(phi)
    rx = dx * c - dy * s
    ry = dx * s + dy * c
    return math.atan2(rx, ry)
