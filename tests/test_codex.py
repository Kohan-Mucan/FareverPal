"""Codex enemy filter: only bestiary types/units, no internals (headless)."""
from farever_companion.data import units


def test_codex_types_exclude_internals():
    ids = {tid for tid, _ in units.codex_types()}
    # nameless internals + non-attackable categories never reach the filter
    for bad in ("Totem", "Environment", "Human", "Mount", "Critter"):
        assert bad not in ids
    # real bestiary types stay
    for good in ("Wolf", "Manfish", "Kobold", "Slime", "Bee", "Golem"):
        assert good in ids


def test_codex_types_have_display_names():
    for tid, name in units.codex_types():
        assert name.strip(), tid
    # sorted by display name
    names_ = [n for _, n in units.codex_types()]
    assert names_ == sorted(names_)


def test_codex_unit_ids():
    ids = units.codex_unit_ids()
    assert len(ids) == len(set(ids))
    ctypes = {tid for tid, _ in units.codex_types()}
    for u in ids:
        # Bosses and uniques are allowed even if their type is internal/nameless
        assert units.unit_type(u) in ctypes or units.is_boss(u) or units.is_unique(u), u
    # templates are excluded, real (even TODO_-prefixed) enemies are not
    assert "BaseMob" not in ids
    assert "Base_Critter" not in ids
    assert "Golem_Base" not in ids
    assert "Crimson_Base" not in ids
    assert "TODO_SnowPanther_White" in ids       # type Wolf, "Snow Leopard"
    # no mounts / totems / companions slip in
    assert not any(units.unit_type(u) in ("Mount", "Totem", "Critter") for u in ids)


def test_type_name():
    assert units.type_name("Manfish") == "Nepsids"
    assert units.type_name("Wolf") == "Wolves"
