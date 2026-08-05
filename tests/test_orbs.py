"""Static secret-orb index + compass-needle math (headless)."""
import math

from farever_companion.geo import nav, orbs


def test_orb_index_loads():
    all_orbs = orbs.load_orbs()
    assert len(all_orbs) == 284
    ids = [o.orb_id for o in all_orbs]
    assert len(set(ids)) == len(ids)
    for o in all_orbs:
        assert isinstance(o.x, float) and isinstance(o.y, float) and isinstance(o.z, float)
        assert o.region in ("Z1", "Z2", "Z3")


def test_region_counts():
    # 99 zone-baked per region count toward the achievement; Z1 carries two
    # extra placements whose region was inferred (empty zoneBaked in prefab),
    # and Z3 now has 84 real placements.
    by_region = {}
    for o in orbs.load_orbs():
        by_region.setdefault(o.region, []).append(o)
    assert len([o for o in by_region["Z1"] if o.zone]) == 99
    assert len([o for o in by_region["Z2"] if o.zone]) == 99
    assert len([o for o in by_region["Z3"] if o.zone]) == 84


def test_by_id_lookup():
    idx = orbs.by_id()
    o = idx["RedOrb_World_201"]
    assert o.region == "Z2"
    assert o.dist(o.x, o.y, o.z) == 0.0


def test_region_progress():
    some = [o.orb_id for o in orbs.load_orbs() if o.region == "Z1"][:5]
    prog = orbs.region_progress(some)
    d, t = prog["Z1"]
    assert d == 5 and t == len([o for o in orbs.load_orbs() if o.region == "Z1"])
    assert prog["Z2"][0] == 0


def test_orb_label():
    assert orbs.orb_label("RedOrb_World_169") == "Secret Orb 169"
    assert orbs.orb_label("RedOrb_World_1") == "Secret Orb 1"
    o = orbs.by_id()["RedOrb_World_201"]
    assert orbs.orb_region_name(o) == "Valley of Eternal Autumn"


def test_view_phi():
    # north-up when neither source reads; pi/2 + heading fallback; camera wins
    assert nav.view_phi(None, None) == 0.0
    assert abs(nav.view_phi(None, 0.0) - math.pi / 2) < 1e-9
    assert abs(nav.view_phi(0.0, 1.0) - math.pi / 2) < 1e-9   # heading ignored


def _flat(rows):
    return [v for row in rows for v in row]


def test_matrix_project_and_phi_north_up():
    # synthetic top-down camera, north-up: ndc.x = 0.1x, ndc.y = -0.1y (y up
    # on screen = north = low world-Y), w = 1
    M = _flat([[0.1, 0, 0, 0], [0, -0.1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]])
    assert nav.project(M, 0, 0, 0) == (0.0, 0.0, 1.0)
    px, py, _ = nav.project(M, 0, -5, 0)        # north -> screen up
    assert px == 0.0 and py > 0
    phi = nav.ground_view_phi(M, 0, 0, 0)
    assert abs(phi) < 1e-9                       # north-up view


def test_matrix_phi_facing_east():
    # camera turned so world-east maps to screen-up, north to screen-left
    M = _flat([[0, 0.1, 0, 0], [0.1, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]])
    px, py, _ = nav.project(M, 5, 0, 0)
    assert px == 0.0 and py > 0                  # east -> up
    phi = nav.ground_view_phi(M, 0, 0, 0)
    assert abs(phi - math.pi / 2) < 1e-9         # same convention as view_phi


def test_project_behind_camera_is_none():
    M = _flat([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, -1]])
    assert nav.project(M, 0, 0, 0) is None


def test_needle_angle_north_up():
    # north-up frame (phi=0): north = low world-Y = dead ahead
    assert abs(nav.needle_angle(0, 0, 0, -10, 0.0)) < 1e-9
    assert abs(nav.needle_angle(0, 0, 10, 0, 0.0) - math.pi / 2) < 1e-9      # east -> right
    assert abs(abs(nav.needle_angle(0, 0, 0, 10, 0.0)) - math.pi) < 1e-9    # south -> behind
    assert abs(nav.needle_angle(0, 0, -10, 0, 0.0) + math.pi / 2) < 1e-9    # west -> left


def test_needle_angle_rotated_frame():
    # facing east (world heading 0 = +x): phi = pi/2 + heading = pi/2, so a
    # target due east is dead ahead and a target due north is to the left
    phi = math.pi / 2
    assert abs(nav.needle_angle(0, 0, 10, 0, phi)) < 1e-9
    assert abs(nav.needle_angle(0, 0, 0, -10, phi) + math.pi / 2) < 1e-9
