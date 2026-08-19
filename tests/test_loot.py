"""Chest loot-table resolution (headless, no game)."""
import pytest

from farever_companion import paths
from farever_companion.geo import chests


def _game_data_present() -> bool:
    """True if the optional game data this module needs is reachable: the CDB
    sheets (data/sheets/lootTable.json) AND the chest position/index files.
    False when that data isn't present in the checkout -> these data-dependent
    tests skip. They run wherever the data is present (assertions unchanged)."""
    try:
        return ((paths.sheets_dir() / "lootTable.json").exists()
                and paths.chest_positions_path().exists()
                and paths.chest_index_path().exists())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _game_data_present(),
    reason="requires game data (data/sheets/*.json + chest index files)")


def test_chest_table_resolution_is_deterministic():
    # same id -> same table every call (the anti-flicker contract)
    a = chests.loot_table_for("BossChest_6")
    b = chests.loot_table_for("BossChest_6")
    assert a == b


def test_chests_loaded():
    cl = chests.load_chests()
    assert len(cl) > 100
    assert all(c.chest_id for c in cl)


def test_boss_chest_template_is_a_placeholder():
    # The generic `BossChest` template resolves to a placeholder, NOT a real
    # boss table — LiveModel.chest_table re-attributes it to the live dungeon
    # boss. Specific boss-arena chests carry their own boss table.
    assert chests.loot_table_for("BossChest") == "UpgradeItems_Activity"
    assert chests.loot_table_for("BossChest_Crabgantua_1") == "Crabgantua"
    # an unknown instance boss chest still falls back to the placeholder via the
    # longest-prefix match (so the model's boss re-attribution kicks in)
    assert chests.loot_table_for("BossChest_Cleodora_1") == "UpgradeItems_Activity"
