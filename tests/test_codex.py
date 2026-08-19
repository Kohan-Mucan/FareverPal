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


def test_find_unit_region():
    """find_unit_region resolves a codex card to its region — the lookup
    that powers the Drops From codex jumps."""
    from farever_companion.data import codex
    # the Mount Tamer vendor's card lives on the Others tab
    assert codex.find_unit_region("TODO_StableMaster") == "Z0"
    assert codex.find_unit_region("TODO_Slime_King") == "Z0"
    # non-codex ids (vendor spawn ids, arbitrary strings) never resolve
    assert codex.find_unit_region("MountTamer_NPC_1") is None
    assert codex.find_unit_region("") is None
    assert codex.find_unit_region(None) is None


def test_stable_master_plots_perinas_shop_spots():
    """The Mount Tamer card resolves Perina Wann's real shop coordinates —
    taken from the drop records of the mounts/gliders she sells — instead
    of the todo placeholder's 'No Map Locations'."""
    from farever_companion.data import codex
    coords, title, is_rift = codex.resolve_locations("TODO_StableMaster")
    assert len(coords) == 2
    assert {round(c["x"], 1) for c in coords} == {1218.4, -397.3}
    assert {round(c["y"], 1) for c in coords} == {221.6, 1502.1}
    assert title == "Vendor: Perina Wann (2 Locations)"
    assert is_rift is False


def test_vendor_card_coords_only_for_real_vendors():
    """Plain todo placeholders still resolve to nothing — only the Stable
    Master (whose shop spots the codex data carries on the items she sells)
    gets coordinates. The Wandering Merchant card has no vendor records in
    the codex data, so it stays unplotted."""
    from farever_companion.data import codex
    assert codex.resolve_locations("TODO_Slime_King") == ((), "", False)
    assert codex.resolve_locations("TODO_WanderingMerchant") == ((), "", False)


def test_vendor_sold_glider_plots_only_its_own_spot():
    """A mount/glider card resolves exactly the vendor spot(s) where IT is
    sold — the Almazean Owl sells from one of Perina Wann's two shops, so
    its card plots one pin (this is why the Drops From row jumps to the
    item's card, not the vendor's aggregated one)."""
    from farever_companion.data import codex
    coords, title, is_rift = codex.resolve_locations("Glider_Owl_Brown02")
    assert len(coords) == 1
    assert round(coords[0]["x"], 1) == -397.3
    assert round(coords[0]["y"], 1) == 1502.1
    assert title == "Vendor: Perina Wann"
    assert is_rift is False
    # the wolf (sold from the OTHER shop) pins its own single spot
    coords2, title2, _ = codex.resolve_locations("Mount_Wolf_01")
    assert len(coords2) == 1
    assert round(coords2[0]["x"], 1) == 1218.4
    assert round(coords2[0]["y"], 1) == 221.6
    assert title2 == "Vendor: Perina Wann"
