"""Chest/orb classifier used by the minimap + entity HUD (headless, no game)."""

from farever_companion.core.chest_resolver import activity_base_id, is_event_orb_id


def test_activity_orbs_classify_as_goldorbs():
    # Only the orbs an activity SPAWNS render as goldorbs — they carry an orb
    # name segment (_Orb_ / _StartOrb_ / _Start_).
    assert is_event_orb_id("Z1_World_Greenlands_ChestOrb_3_Orb_11") is True
    assert is_event_orb_id("Z1_World_Greenlands_ChestOrb_3_Orb_10") is True
    assert is_event_orb_id("Z1_World_Greenlands_TimerCollectRun_3_StartOrb_1") is True
    assert is_event_orb_id("Z1_World_Greenlands_TimerCollectRun_3_Orb_1") is True


def test_base_activity_id_is_main_chest_not_goldorb():
    # The bare base id (e.g. ..._ChestOrb_3) is the MAIN chest that spawns the
    # orbs — it must render as a chest box, never as a goldorb.
    assert is_event_orb_id("Z1_World_Greenlands_ChestOrb_3") is False
    assert is_event_orb_id("Z1_World_Greenlands_ChestOrb_6") is False
    assert is_event_orb_id("Z1_World_Greenlands_TimerCollectRun_3") is False


def test_end_chests_classify_as_chest_boxes():
    # The reward chest that spawns when an activity completes is a real loot
    # chest and must render as a chest box, never a goldorb.
    assert is_event_orb_id("Z1_World_Greenlands_ChestOrb_3_Chest_7") is False
    assert is_event_orb_id("Z1_World_Greenlands_ChestOrb_6_Chest_3") is False
    assert is_event_orb_id("Z1_World_Greenlands_ChestOrb_3_Chest") is False
    assert is_event_orb_id("Z1_World_Greenlands_TimerCollectRun_3_Chest_5") is False


def test_normal_chests_and_unrelated_orbs():
    assert is_event_orb_id("Z1_World_Greenlands_WorldChest_30") is False
    assert is_event_orb_id("Z1_World_Greenlands_WorldChest_30_Chest") is False
    assert is_event_orb_id("Z1_World_Greenlands_SecretOrb_2") is False
    # FightStone is a fight-spot chest, not an event orb — chest box, never goldorb.
    assert is_event_orb_id("FightStone") is False
    assert is_event_orb_id("Z1_World_Greenlands_FightStone_16") is False
    assert is_event_orb_id(None) is False
    assert is_event_orb_id("") is False


def test_activity_base_id_extracts_real_end_chest_bases():
    # Real end-chest suffixes (numbered and bare) map to their id prefix.
    assert activity_base_id("Z1_World_Greenlands_ChestOrb_3_Chest_7") == "z1_world_greenlands_chestorb_3"
    assert activity_base_id("Z1_World_Greenlands_ChestOrb_2_Chest") == "z1_world_greenlands_chestorb_2"
    assert activity_base_id("Z1_World_Greenlands_TimerCollectRun_3_Chest_5") == "z1_world_greenlands_timercollectrun_3"


def test_activity_base_id_rejects_bare_bases_and_non_end_chests():
    # Bare activity bases contain "_chest" only as a substring of "chestorb" —
    # a naive rsplit would yield a garbage zone prefix (the bug that hid every
    # activity base in a zone once any ChestOrb id was marked done).
    assert activity_base_id("Z1_World_Greenlands_ChestOrb_3") is None
    assert activity_base_id("Z1_World_Greenlands_ChestOrb_12") is None
    assert activity_base_id("Z1_World_Greenlands_FightStone_16") is None
    assert activity_base_id("FightStone") is None
    assert activity_base_id("Z1_World_Greenlands_WorldChest_30") is None
    assert activity_base_id(None) is None
    assert activity_base_id("") is None
