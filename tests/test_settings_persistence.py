"""Settings.save() change-detection: the disk is only written when the
cleaned in-memory state actually differs from the last-written state.

Repeated UI refreshes, overlay timers and drag events call save() constantly;
before the fingerprint guard every one rewrote settings.json (and previously
rewrote collection.json wholesale — the data-loss root cause).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from farever_companion.persist import atomic_write_json


def _mtime(p: Path) -> float:
    return p.stat().st_mtime


def test_unchanged_save_does_not_rewrite_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json",
                      {"ui_scale": 1.0, "opacity": 0.9}, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    before = _mtime(tmp_path / "settings.json")
    time.sleep(0.02)
    s.save()                                # nothing changed
    assert _mtime(tmp_path / "settings.json") == before


def test_changed_save_rewrites_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json",
                      {"ui_scale": 1.0}, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    before = _mtime(tmp_path / "settings.json")
    time.sleep(0.02)
    s.opacity = 0.5
    s.save()
    assert _mtime(tmp_path / "settings.json") != before
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data.get("opacity") == 0.5


def test_in_place_dict_mutation_is_detected(monkeypatch, tmp_path):
    # Overlay geometry is stored as settings.geometry[key] = value (in-place),
    # then save() is called — the fingerprint must catch it (an attribute-set
    # dirty flag would miss this and silently drop overlay positions).
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "settings.json", {}, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    if not s.geometry:
        s.geometry = {}
    s.geometry["overlay"] = "10,20"
    before = _mtime(tmp_path / "settings.json")
    time.sleep(0.02)
    s.save()
    assert _mtime(tmp_path / "settings.json") != before
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data.get("geometry", {}).get("overlay") == "10,20"


def test_fresh_load_unchanged_save_creates_nothing(monkeypatch, tmp_path):
    # No settings.json on disk and no changes -> save() must not create one.
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    from farever_companion.config import Settings
    s = Settings.load()
    s.save()
    assert not (tmp_path / "settings.json").exists()
    s.ui_scale = 1.5                        # real change persists
    s.save()
    assert (tmp_path / "settings.json").exists()


def test_dmg_type_pointers_roundtrip_and_empty_omitted(monkeypatch, tmp_path):
    """The cached DamageDisplay/DamageResult class pointers survive a reload
    (so an app restart skips the type scan) and are omitted from settings.json
    while unset (clean file, no empty-string noise)."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    from farever_companion.config import Settings
    s = Settings.load()
    s.dmg_display_type = "0x7ffc1234"
    s.dmg_result_type = "0x7ffc5678"
    s.save()
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data.get("dmg_display_type") == "0x7ffc1234"
    assert data.get("dmg_result_type") == "0x7ffc5678"

    s2 = Settings.load()
    assert s2.dmg_display_type == "0x7ffc1234"
    assert s2.dmg_result_type == "0x7ffc5678"

    # Clearing them removes the keys from disk on the next save.
    s2.dmg_display_type = ""
    s2.dmg_result_type = ""
    s2.save()
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "dmg_display_type" not in data
    assert "dmg_result_type" not in data


def test_poi_done_authoritative_reads_disk_fresh(monkeypatch, tmp_path):
    """The sync writers base on the DISK file, not on possibly-stale/empty
    memory: a second instance (or fresh Settings) that never loaded the
    profile must not wipe a populated file. Reading authoritative also
    refreshes the whole cached dict so a save keeps orbs/hidden."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(448)],
        "dungeon_orb_done": ["orb_1"],
        "entity_hidden_units": ["u_1"],
    }, compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    # simulate a second instance whose memory never loaded the profile
    s._profile_progress["Mage"] = {"poi_done": []}
    got = s.get_poi_done_authoritative("Mage")
    assert len(got) == 448
    # memory refreshed wholesale -> a save right after keeps the other lists
    assert s._profile_progress["Mage"].get("dungeon_orb_done") == ["orb_1"]
    assert s._profile_progress["Mage"].get("entity_hidden_units") == ["u_1"]


def test_sync_done_list_safe_refuses_empty_base_over_populated_file(
        monkeypatch, tmp_path):
    """The wipe signature: in-memory base EMPTY while the disk file holds
    done data. sync_done_list_safe returns False there (the sync refuses),
    and True for normal writes, missing files and empty files."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    atomic_write_json(tmp_path / "progress_Mage.json",
                      {"poi_done": [f"poi_{i}" for i in range(448)]},
                      compact_lists=True)
    from farever_companion.config import Settings
    s = Settings.load()
    assert s.sync_done_list_safe("Mage", []) is False   # the wipe signature
    assert s.sync_done_list_safe("Mage", ["poi_1"]) is True   # normal pass
    assert s.sync_done_list_safe("Nobody", []) is True  # missing file: ok
    atomic_write_json(tmp_path / "progress_Empty.json", {}, compact_lists=True)
    assert s.sync_done_list_safe("Empty", []) is True   # empty file: ok


def _write_prof(tmp_path, name="Mage", pois=448, orbs=0, hidden=0):
    atomic_write_json(tmp_path / f"progress_{name}.json", {
        "poi_done": [f"poi_{i}" for i in range(pois)],
        **({"dungeon_orb_done": [f"orb_{i}" for i in range(orbs)]} if orbs else {}),
        **({"entity_hidden_units": [f"u_{i}" for i in range(hidden)]} if hidden else {}),
    }, compact_lists=True)


def test_save_profile_data_seeds_disk_when_memory_empty(monkeypatch, tmp_path):
    """A writer with an EMPTY in-memory reference (failed load / fresh second
    instance) must never clobber a populated file: save_profile_data seeds the
    base from the current file before writing."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=448, hidden=220)
    from farever_companion.config import Settings
    s = Settings.load()
    s._profile_progress["Mage"] = {}          # as if the load failed
    s.save_profile_data("Mage")               # would previously write {}
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert len(d["poi_done"]) == 448          # not wiped
    assert len(d["entity_hidden_units"]) == 220


def test_toggle_done_is_disk_authoritative(monkeypatch, tmp_path):
    """The stale-cache two-instance case: instance A cached 4 POIs, instance B
    adds poi_e to disk. A then toggles poi_b off - the toggle must apply to the
    CURRENT disk list so B's poi_e survives instead of being clobbered."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=4)              # poi_0..poi_3
    from farever_companion.config import Settings
    a = Settings.load()
    assert a.get_poi_done("Mage") == ["poi_0", "poi_1", "poi_2", "poi_3"]
    # instance B adds poi_e on disk after A cached its copy
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(4)] + ["poi_e"]}, compact_lists=True)

    done = a.toggle_done("poi_1", "Mage")    # unmark poi_1 in A's UI
    assert done is False
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert "poi_e" in d["poi_done"]           # B's addition survived
    assert "poi_1" not in d["poi_done"]       # A's toggle applied
    assert len(d["poi_done"]) == 4


def test_hidden_and_best_saves_are_disk_authoritative(monkeypatch, tmp_path):
    """toggle_unit_hidden / save_dps_best must not drag a stale cache over
    newer disk state (hidden list and other keys survive)."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=10, hidden=3)   # u_0..u_2
    from farever_companion.config import Settings
    a = Settings.load()
    assert len(a.get_entity_hidden_units("Mage")) == 3
    # instance B hides u_new and collects poi_9 on disk afterwards
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(10)],
        "entity_hidden_units": ["u_0", "u_1", "u_2", "u_new"]}, compact_lists=True)

    a.toggle_unit_hidden("u_1", False, "Mage")    # A un-hides u_1
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert "u_new" in d["entity_hidden_units"]     # B's hide survived
    assert "u_1" not in d["entity_hidden_units"]   # A's toggle applied

    a.save_dps_best("Mage", {"best": 12345})
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert d.get("dps_best") == {"best": 12345}
    assert "u_new" in d["entity_hidden_units"]     # still not clobbered
    assert len(d["poi_done"]) == 10


def test_save_profile_progress_preserves_other_keys_from_disk(monkeypatch, tmp_path):
    """save_profile_progress writes onto the CURRENT file (not the stale
    cache), so orbs/hidden added by another instance survive a poi save."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    _write_prof(tmp_path, pois=10, orbs=2)     # orb_0..orb_1
    from farever_companion.config import Settings
    a = Settings.load()
    assert len(a.get_poi_done("Mage")) == 10
    # instance B collects orb_9 after A cached its copy
    atomic_write_json(tmp_path / "progress_Mage.json", {
        "poi_done": [f"poi_{i}" for i in range(10)],
        "dungeon_orb_done": ["orb_0", "orb_1", "orb_9"]}, compact_lists=True)

    a.save_profile_progress("Mage", [f"poi_{i}" for i in range(11)])  # A adds poi_10
    d = json.loads((tmp_path / "progress_Mage.json").read_text(encoding="utf-8"))
    assert "orb_9" in d["dungeon_orb_done"]    # B's orb survived
    assert "poi_10" in d["poi_done"]
