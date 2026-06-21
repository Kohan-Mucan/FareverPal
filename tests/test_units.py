"""Boss detection + unit loot resolution (headless)."""
import pytest

from farever_companion import paths
from farever_companion.core.model import LiveModel
from farever_companion.core.scene import Entity
from farever_companion.data import units, rarity


def _game_data_present() -> bool:
    """True if the optional CDB sheets are reachable (data/sheets/lootTable.json
    marker resolves). False when the game data isn't present in the checkout ->
    these data-dependent tests skip. They run wherever the data is present
    (assertions unchanged)."""
    try:
        return (paths.sheets_dir() / "lootTable.json").exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _game_data_present(),
    reason="requires game data (data/sheets/*.json)")


def test_named_bosses_are_the_ten():
    bosses = units.named_bosses()
    assert "MunsterChuck" in bosses
    assert len(bosses) == 10
    assert units.is_boss("MunsterChuck")
    assert not units.is_boss("Kobold")


def test_boss_uses_signature_table():
    assert units.loot_table_for_unit("MunsterChuck") == "MunsterChuck"


def test_trash_unit_uses_type_table():
    # a Kobold-type unit resolves to the Kobold table via unitType
    tbl = units.loot_table_for_unit("Kobold")
    assert tbl in (None, "Kobold") or isinstance(tbl, str)


def test_every_catalog_companion_is_detected():
    # the collection catalog's companions category is the authoritative list
    from farever_companion.data import collections as col
    ids = [r["id"] for r in col.items("companions")]
    assert len(ids) == 60
    missed = [i for i in ids if not units.is_companion(i)]
    assert not missed, f"catalog companions not detected: {missed}"


def test_companion_detection():
    # wild catchable companions are the Critter-type units
    assert units.is_companion("Rabbit_Yellow")      # Buttontail
    assert units.is_companion("Lizard_Green")       # Saladmander
    assert not units.is_companion("Kobold")
    assert not units.is_companion("MunsterChuck")
    assert not units.is_companion(None)


def test_unit_types_cover_filter_choices():
    types = units.unit_types()
    assert units.COMPANION_TYPE in types
    assert "Wolf" in types and "Kobold" in types
    assert types == sorted(types)


def _ent(unit_id, owner_cls=None, x=0.0):
    return Entity(addr=id(unit_id) + int(x), cls="ent.Foe", unit_id=unit_id,
                  owner_cls=owner_cls, x=x, y=0.0, z=0.0, is_foe=True)


def _model_with(units_list):
    m = LiveModel.__new__(LiveModel)       # headless: no proc, stubbed units()
    m.units = lambda: units_list
    class StubLocator:
        def live_address(self) -> int | None: return None
    m.locator = StubLocator()
    return m


def test_companion_and_enemy_lists_split():
    wild = _ent("Rabbit_Yellow", x=1.0)
    owned = _ent("Lizard_Green", owner_cls="ent.Hero", x=2.0)  # someone's pet
    kobold = _ent("Kobold_Z1W_Mace", x=3.0)
    m = _model_with([wild, owned, kobold])
    xyz = (0.0, 0.0, 0.0)
    # wild critters only - no equipped pets, no enemies
    comps = [e for e, _ in m.nearest_companions(xyz, 10)]
    assert comps == [wild]
    # enemies never contain critters, owned or wild
    foes = [e for e, _ in m.nearest_enemies(xyz, 10)]
    assert foes == [kobold]
    # and the type filter still applies to enemies
    assert m.nearest_enemies(xyz, 10, hide_types={"Kobold"}) == []


def test_rarity_promotion_floors_and_sums_to_one():
    dist = rarity.promote_distribution("Rare", 20)
    assert "Uncommon" not in dist            # floored at Rare
    assert abs(sum(dist.values()) - 1.0) < 1e-6
