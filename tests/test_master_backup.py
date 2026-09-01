"""Unit tests for master backup persistence, 5-slot rotation, and data loss recovery."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from farever_companion import master_backup as mb
from farever_companion.persist import atomic_write_json


@pytest.fixture(autouse=True)
def _moddata(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    yield


def test_snapshot_creation_and_rotation_limit():
    cdir = mb.config_dir()
    
    # Setup initial mock profile and collection
    atomic_write_json(cdir / "collection.json", {"pets": ["p1", "p2"], "mounts": ["m1"], "gliders": []})
    atomic_write_json(cdir / "progress_Paladin.json", {"poi_done": ["poi_1", "poi_2"], "entity_hidden_units": ["m1"]})
    
    # 1. Create first snapshot
    added = mb.create_snapshot_if_changed(max_backups=5)
    assert added is True
    
    data = mb.load_master_backup()
    assert len(data["backups"]) == 1
    snap1 = data["backups"][0]
    assert snap1["collection"]["pets"] == ["p1", "p2"]
    assert "Paladin" in snap1["profiles"]
    
    # 2. No changes -> skip snapshot
    assert mb.create_snapshot_if_changed(max_backups=5) is False
    assert len(mb.load_master_backup()["backups"]) == 1
    
    # 3. Add multiple snapshots to verify 5-slot rotation cap
    for i in range(10):
        # modify data so change is detected
        atomic_write_json(cdir / "progress_Paladin.json", {"poi_done": [f"poi_{i}"], "entity_hidden_units": []})
        mb.create_snapshot_if_changed(max_backups=5)
        
    final_data = mb.load_master_backup()
    assert len(final_data["backups"]) == 5


def test_data_loss_detection():
    cdir = mb.config_dir()
    
    # Baseline with 10 POIs and 10 pets
    atomic_write_json(cdir / "collection.json", {"pets": [f"pet_{i}" for i in range(10)], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json", {"poi_done": [f"poi_{i}" for i in range(10)], "entity_hidden_units": []})
    
    mb.create_snapshot_if_changed(max_backups=5)
    
    # Normal minor update (1 POI uncheck = 10% change) -> no loss warning
    atomic_write_json(cdir / "progress_Mage.json", {"poi_done": [f"poi_{i}" for i in range(9)], "entity_hidden_units": []})
    assert mb.check_for_data_loss(threshold_pct=20.0) is None
    
    # Big wipe (drop from 10 to 3 POIs = 70% loss) -> flagged!
    atomic_write_json(cdir / "progress_Mage.json", {"poi_done": ["poi_1", "poi_2", "poi_3"], "entity_hidden_units": []})
    loss = mb.check_for_data_loss(threshold_pct=20.0)
    assert loss is not None
    assert loss["has_loss"] is True
    assert any("Mage" in r for r in loss["reasons"])
    
    # Missing profile entirely -> flagged!
    (cdir / "progress_Mage.json").unlink()
    loss = mb.check_for_data_loss(threshold_pct=20.0)
    assert loss is not None
    assert any("missing" in r for r in loss["reasons"])


def test_lossy_newest_snapshot_does_not_mask_loss():
    """Regression: a snapshot written by a session that ALREADY lost data
    must not hide the loss. Detection compares live data against the
    historical maximum across ALL snapshots, never just the newest one -
    otherwise a mid-session wipe followed by a normal exit bakes the loss
    into snapshot[0] and the recovery dialog never appears."""
    cdir = mb.config_dir()

    # 1. Healthy state, snapshot holds 10 POIs
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": [f"poi_{i}" for i in range(10)],
                       "entity_hidden_units": []})
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1"], "mounts": [], "gliders": []})
    mb.create_snapshot_if_changed(max_backups=5)
    assert mb.check_for_data_loss(threshold_pct=20.0) is None

    # 2. Wipe happens mid-session; the exit then snapshots the LOSSY state
    #    (this is exactly what happened: the wipe's own session wrote the
    #    newest snapshot already containing the loss)
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["poi_1", "poi_2"],
                       "entity_hidden_units": []})
    mb.create_snapshot_if_changed(max_backups=5)
    assert len(mb.load_master_backup()["backups"]) == 2

    # 3. Live file is still the lossy one - detection MUST flag it even
    #    though the newest snapshot matches the lossy live state exactly
    loss = mb.check_for_data_loss(threshold_pct=20.0)
    assert loss is not None
    assert any("Mage" in r and "10" in r for r in loss["reasons"])
    # recovery points at the fullest (healthy) snapshot, not the lossy one
    assert loss["latest_snapshot"]["profiles"]["Mage"]["poi_done"] \
        == [f"poi_{i}" for i in range(10)]


def test_restore_from_snapshot():
    cdir = mb.config_dir()
    
    # Create backup state
    atomic_write_json(cdir / "collection.json", {"pets": ["p1", "p2"], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Warrior.json", {"poi_done": ["w1", "w2", "w3"], "entity_hidden_units": []})
    atomic_write_json(cdir / "progress_Priest.json", {"poi_done": ["pr1"], "entity_hidden_units": []})
    
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]
    
    # Simulate wipe of Priest only
    (cdir / "progress_Priest.json").unlink()
    
    # Single profile restore
    res = mb.restore_from_snapshot(snap, target_profile="Priest")
    assert res is True
    assert (cdir / "progress_Priest.json").exists()
    assert json.loads((cdir / "progress_Priest.json").read_text(encoding="utf-8"))["poi_done"] == ["pr1"]
    
    # Wipe entire directory
    for f in cdir.glob("*.json"):
        if f.name != "master_backup.json":
            f.unlink()
            
    # Full restore
    res_full = mb.restore_from_snapshot(snap)
    assert res_full is True
    assert (cdir / "collection.json").exists()
    assert (cdir / "progress_Warrior.json").exists()
    assert (cdir / "progress_Priest.json").exists()


def test_restore_missing_merges_only_missing_collection():
    cdir = mb.config_dir()
    # snapshot holds 10 pets; current lost 4 of them but gained 1 new
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2", "p3"], "mounts": ["m1"], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["a"], "entity_hidden_units": []})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]
    bak_pets = set(snap["collection"]["pets"])

    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2", "p3", "p_new"], "mounts": ["m1"],
                       "gliders": []})
    # simulate the reported loss: only a few remain
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p_new"], "mounts": ["m1"], "gliders": []})

    notes = mb.restore_missing_from_snapshot(snap)
    assert any("Collection pets" in n for n in notes)
    merged = json.loads((cdir / "collection.json").read_text(encoding="utf-8"))
    # current p_new is preserved; all backup pets restored
    assert "p_new" in merged["pets"]
    assert bak_pets <= set(merged["pets"])

    # idempotent: a second run has nothing left to add
    assert mb.restore_missing_from_snapshot(snap) == []


def test_restore_missing_preserves_profile_fields_and_adds_only_missing():
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1"], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Warrior.json",
                      {"poi_done": ["w1", "w2", "w3"],
                       "entity_hidden_units": [], "waypoints": ["keepme"],
                       "speedrun_best": {"zone": 12.3}})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]

    # current file dropped w2 and w3 but still has waypoints/speedrun fields
    atomic_write_json(cdir / "progress_Warrior.json",
                      {"poi_done": ["w1"], "entity_hidden_units": [],
                       "waypoints": ["keepme"], "speedrun_best": {"zone": 9.9}})

    notes = mb.restore_missing_from_snapshot(snap)
    assert any("Warrior" in n for n in notes)
    cur = json.loads((cdir / "progress_Warrior.json").read_text(encoding="utf-8"))
    assert set(cur["poi_done"]) == {"w1", "w2", "w3"}
    # untouched non-merge fields survive byte-for-byte
    assert cur["waypoints"] == ["keepme"]
    assert cur["speedrun_best"] == {"zone": 9.9}   # newer best NOT downgraded


def test_restore_missing_recreates_missing_profile_and_merges_planner():
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1"], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Priest.json",
                      {"poi_done": ["pr1", "pr2"], "entity_hidden_units": []})
    atomic_write_json(cdir / "planner.json", {"farm": [{"id": "a"}, "b"]})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]

    # Priest profile gone; planner lost item b
    (cdir / "progress_Priest.json").unlink()
    atomic_write_json(cdir / "planner.json", {"farm": [{"id": "a"}, "c"]})

    notes = mb.restore_missing_from_snapshot(snap)
    assert any("Priest" in n and "missing" in n for n in notes)
    assert any("farm" in n.lower() for n in notes)
    assert (cdir / "progress_Priest.json").exists()
    plan = json.loads((cdir / "planner.json").read_text(encoding="utf-8"))
    ids = [x["id"] if isinstance(x, dict) else x for x in plan["farm"]]
    assert "a" in ids and "b" in ids and "c" in ids   # merged, nothing removed


def test_restore_missing_nothing_to_do_when_data_intact():
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2"], "mounts": ["m1"], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["a", "b"], "entity_hidden_units": []})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]
    # live files match the snapshot - a false-positive loss check should merge nothing
    assert mb.restore_missing_from_snapshot(snap) == []


def test_save_collection_never_wipes_populated_file(tmp_settings_dir=None):
    import farever_companion.config as cfg
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2"], "mounts": [], "gliders": []})
    s = cfg.Settings.load()
    assert s.pet_hidden_units == ["p1", "p2"]
    # simulate the startup case: in-memory lists are empty, disk is populated
    s.pet_hidden_units = []
    s.mount_hidden_units = []
    s.glider_hidden_units = []
    s.save_collection()
    on_disk = json.loads((cdir / "collection.json").read_text(encoding="utf-8"))
    assert on_disk["pets"] == ["p1", "p2"]     # not clobbered by the empty state


# --- write-time loss guard (state_guard.json) -----------------------------


def _full_prof(pois: int, orbs: int = 0, hidden: int = 0) -> dict:
    return {
        "poi_done": [f"poi_{i}" for i in range(pois)],
        "dungeon_orb_done": [f"orb_{i}" for i in range(orbs)],
        "entity_hidden_units": [f"u_{i}" for i in range(hidden)],
    }


def test_guard_seeds_best_on_first_write():
    """No reference anywhere -> the first write becomes the best-known state
    and is never flagged (fresh install / brand-new character)."""
    assert mb.guard_profile_write("Mage", _full_prof(10, 2)) is None
    guards = mb._load_safety_guards()
    assert len(guards["best"]["Mage"]["poi_done"]) == 10
    assert "Mage" not in guards["guards"]


def test_guard_preserves_best_on_lossy_write():
    """The Kartik scenario: backups hold 448 POIs, a write tries to land 17.
    The good state is preserved to state_guard.json and returned, and the
    safety file's best is NEVER downgraded to the lossy write."""
    cdir = mb.config_dir()
    atomic_write_json(cdir / "progress_Mage.json", _full_prof(448, 9, 220))
    mb.create_snapshot_if_changed(max_backups=5)

    preserved = mb.guard_profile_write("Mage", _full_prof(17))
    assert preserved is not None
    assert len(preserved["poi_done"]) == 448      # the good state, returned
    assert len(preserved["dungeon_orb_done"]) == 9

    guards = mb._load_safety_guards()
    g = guards["guards"]["Mage"]
    assert len(g["preserved"]["poi_done"]) == 448
    assert g["attempted"] == {"poi_done": 17, "dungeon_orb_done": 0,
                               "entity_hidden_units": 0}
    # best still holds the GOOD state, not the attempted lossy one
    assert len(guards["best"]["Mage"]["poi_done"]) == 448


def test_guard_tracks_growth_and_clears_on_recovery():
    """Healthy growth updates best; a later healthy save clears the pending
    alert once the profile is back above the threshold."""
    mb.guard_profile_write("Mage", _full_prof(100, 5))      # seed best
    mb.guard_profile_write("Mage", _full_prof(500, 6))      # grows
    preserved = mb.guard_profile_write("Mage", _full_prof(20))   # wipe attempt
    assert preserved is not None
    assert len(preserved["poi_done"]) == 500

    guards = mb._load_safety_guards()
    assert "Mage" in guards["guards"]
    assert len(guards["best"]["Mage"]["poi_done"]) == 500

    # restore writes 500 back -> healthy again -> guard cleared
    assert mb.guard_profile_write("Mage", _full_prof(500, 6)) is None
    guards = mb._load_safety_guards()
    assert "Mage" not in guards["guards"]
    assert len(guards["best"]["Mage"]["poi_done"]) == 500


def test_guard_uses_safety_file_best_even_without_backups():
    """state_guard.json stands alone: a wipe + crash before any exit snapshot
    must still preserve the good state (no master_backup needed)."""
    mb.guard_profile_write("Mage", _full_prof(448, 9, 220))   # healthy session
    preserved = mb.guard_profile_write("Mage", _full_prof(17))
    assert preserved is not None
    assert len(preserved["poi_done"]) == 448


def test_check_for_data_loss_reports_still_lossy_guarded_profile():
    """Startup surfacing: with a guard recorded and the live file STILL lossy,
    check_for_data_loss names state_guard.json; once restored it stays quiet."""
    cdir = mb.config_dir()
    atomic_write_json(cdir / "progress_Mage.json", _full_prof(448, 9, 220))
    mb.create_snapshot_if_changed(max_backups=5)

    # the wipe lands on disk, then the app writes it (guard fires)
    atomic_write_json(cdir / "progress_Mage.json", _full_prof(17))
    mb.guard_profile_write("Mage", _full_prof(17))
    loss = mb.check_for_data_loss()
    assert loss is not None
    assert any("state_guard.json" in r for r in loss["reasons"])

    # user restores the preserved state -> healthy, no guard reason
    atomic_write_json(cdir / "progress_Mage.json", _full_prof(448, 9, 220))
    loss = mb.check_for_data_loss()
    assert loss is None or not any(
        "state_guard.json" in r for r in loss["reasons"])


def test_save_profile_data_hooks_the_guard(tmp_settings_dir=None):
    """config.save_profile_data runs the guard before every profile write:
    a lossy in-memory state triggers preservation, a healthy one doesn't."""
    import farever_companion.config as cfg
    cdir = mb.config_dir()
    atomic_write_json(cdir / "progress_Mage.json", _full_prof(448, 9, 220))
    mb.create_snapshot_if_changed(max_backups=5)

    s = cfg.Settings.load()
    s._ensure_profile_loaded("Mage")
    s._profile_progress["Mage"] = _full_prof(17)
    s.save_profile_data("Mage")                       # would wipe 448 -> 17
    guards = mb._load_safety_guards()
    assert len(guards["guards"]["Mage"]["preserved"]["poi_done"]) == 448
    # the write itself landed (the guard preserves, it does not block saves)
    on_disk = json.loads((cdir / "progress_Mage.json").read_text(encoding="utf-8"))
    assert len(on_disk["poi_done"]) == 17

    # healthy save afterwards clears the pending alert
    s._profile_progress["Mage"] = _full_prof(448, 9, 220)
    s.save_profile_data("Mage")
    guards = mb._load_safety_guards()
    assert "Mage" not in guards["guards"]


def test_dismiss_profile_alert_silences_unwanted_profile():
    """When a user dismisses a profile alert, it is cleared from guards, added to
    ignored_profiles, and check_for_data_loss no longer warns about it."""
    cdir = mb.config_dir()
    atomic_write_json(cdir / "progress_TempChar.json", _full_prof(50))
    mb.create_snapshot_if_changed(max_backups=5)

    # User deletes the unwanted profile
    (cdir / "progress_TempChar.json").unlink()
    loss = mb.check_for_data_loss()
    assert loss is not None
    assert any("TempChar" in r for r in loss["reasons"])

    # User clicks "Don't ask again" / forgets the profile
    mb.dismiss_profile_alert("TempChar", purge_from_backups=True)
    loss2 = mb.check_for_data_loss()
    assert loss2 is None or not any("TempChar" in r for r in (loss2.get("reasons") or []))


def test_accept_current_moddata_state_clears_all_warnings():
    """Accepting current state silences both reduced counts and missing profiles."""
    cdir = mb.config_dir()
    atomic_write_json(cdir / "progress_CharA.json", _full_prof(40))
    atomic_write_json(cdir / "progress_CharB.json", _full_prof(20))
    mb.create_snapshot_if_changed(max_backups=5)

    # Delete CharB and reduce CharA
    (cdir / "progress_CharB.json").unlink()
    atomic_write_json(cdir / "progress_CharA.json", _full_prof(5))
    mb.guard_profile_write("CharA", _full_prof(5))

    loss = mb.check_for_data_loss()
    assert loss is not None

    # User accepts current state (Skip / Keep Current)
    mb.accept_current_moddata_state()
    loss_after = mb.check_for_data_loss()
    assert loss_after is None

