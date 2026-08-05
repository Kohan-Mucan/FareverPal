"""Collection-tracker data layer (catalog, progress math, filters) — headless."""
from __future__ import annotations

from farever_companion import paths
from farever_companion.data import collections as col


def test_catalog_loads_with_expected_categories():
    cat = col.catalog()
    assert cat, "collection catalog missing (compiler derives it from codex.json)"
    keys = [c["key"] for c in col.categories()]
    assert keys == ["mounts", "gliders", "companions"]


def test_known_category_counts():
    # Dynamic counts from game data
    assert len(col.items("mounts")) >= 50
    assert len(col.items("gliders")) >= 50
    assert len(col.items("companions")) >= 60


def test_rows_have_required_fields():
    for r in col.items():
        assert r["id"] and r["name"] and r["category"] and r["subtype"]
        assert "obtainable" in r and "source" in r


def test_icon_sheet_mapping():
    assert col.icon_sheet("companions") == "collection"
    assert col.icon_sheet("mounts") == "collection"
    assert col.icon_sheet("gliders") == "collection"
    assert col.icon_sheet("nonsense") == "item"


def test_icons_exist_for_nearly_all_items():
    sheet_map = {"unit": "Units", "item": "Items", "skill": "Skills"}
    
    # Load atlas keys
    import json
    atlas_keys = set()
    atlas_dir = paths.atlas_dir()
    if atlas_dir.exists():
        for p in atlas_dir.glob("*.json"):
            if p.name == "atlas_index.json":
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    atlas_keys.update(data.keys())
            except Exception:
                pass
                
    base_dirs = [paths.icons_dir()]
            
    missing = []
    for r in col.items():
        if r["id"] in atlas_keys:
            continue
            
        sheet = sheet_map.get(col.icon_sheet(r["category"]), col.icon_sheet(r["category"]))
        found = False
        for base in base_dirs:
            for ext in ("webp", "png"):
                if (base / sheet / f"{r['id']}.{ext}").exists():
                    found = True
                    break
            if found:
                break
        if not found:
            missing.append(r["id"])
    # the known atlas gaps are a couple of unreleased armor pieces
    assert len(missing) <= 5, f"too many missing icons: {missing[:10]}"


def test_obtainable_views_agree_with_codex():
    """Achievement-reward and cash-shop mounts/gliders (e.g. Crimson Goat,
    SparkHorse_01) are obtainable in-game — the catalog must agree with the
    Codex UI, which treats them as obtainable instead of 'unreleased'."""
    from farever_companion.data import raw_codex, codex

    by_id = {r["id"]: r for r in col.items()}

    # every codex row reflagged 'achievement' is obtainable in the catalog
    ach_rows = []
    for zone in ("Mounts", "Gliders"):
        for e in raw_codex.DATA.get(zone, []):
            if (e.get("kind") or "").lower() != "achievement":
                continue
            ach_rows.append(e["id"])
            assert by_id[e["id"]]["obtainable"], e["id"]
    assert len(ach_rows) >= 5, ach_rows
    assert by_id["Mount_Goat_04"]["obtainable"]  # Crimson Goat

    # every cash-shop item the runtime flags is obtainable in the catalog
    shop_rows = [r for r in col.items() if codex.is_shop_item(r)]
    assert len(shop_rows) >= 2, shop_rows
    for r in shop_rows:
        assert r["obtainable"], r["id"]
    assert by_id["SparkHorse_01"]["obtainable"]
    assert by_id["Glider_Butterfly_EA_Spark"]["obtainable"]

    # ...and every catalog row still marked not-obtainable is a genuine
    # unreleased item: the codex agrees (kind unreleased, never achievement
    # or a shop id)
    codex_kinds = {}
    for zone in ("Mounts", "Gliders"):
        for e in raw_codex.DATA.get(zone, []):
            codex_kinds[e["id"]] = (e.get("kind") or "").lower()
    for r in col.items():
        if r.get("obtainable"):
            continue
        assert not codex.is_shop_item(r), r["id"]
        k = codex_kinds.get(r["id"])
        assert k in ("unreleased", "todo"), (r["id"], k)


def test_catalog_derivation_marks_achievement_and_shop_obtainable(tmp_path):
    """The compiler derives the catalog from codex.json, which flags
    achievement-only rewards AND cash-shop items as 'unreleased' (its scanner
    doesn't know about either). The derivation must reflag both obtainable,
    mirroring the codex payload and the runtime's is_shop_item."""
    import json
    from compiler import _collection_catalog_from_codex, _load_shop_ids

    codex = {"codex": {
        "Mount_Goat_04": {"id": "Mount_Goat_04", "name": "Crimson Goat",
                          "type": "Mount", "kind": "unreleased"},
        "SparkHorse_01": {"id": "SparkHorse_01", "name": "Sparkling Horsean",
                          "type": "Mount", "kind": "unreleased"},
        "ShopOnly_01": {"id": "ShopOnly_01", "name": "Shop Pet",
                        "type": "Glider", "kind": "unreleased"},
        "Mount_Wolf_01": {"id": "Mount_Wolf_01", "name": "Enripian Wolf",
                          "type": "Mount", "kind": "unreleased"},
        "Mount_Boar_03": {"id": "Mount_Boar_03", "name": "Alandian Hog",
                          "type": "Mount", "kind": "drop",
                          "coords": [{"x": 1.0, "y": 2.0}]},
    }}
    (tmp_path / "codex.json").write_text(json.dumps(codex), encoding="utf-8")
    # shop.json (canonical shop list once the scanner ships it) written in
    # the game's native sheet shape ({"lines": [...]}) — the compiler must
    # accept it alongside the plain {"shop": [...]} wrapper
    (tmp_path / "shop.json").write_text(json.dumps({"lines": [{"id": "ShopOnly_01"}]}),
                                        encoding="utf-8")
    ach = {"Mount_Goat_04": {"name": "Savior of Skover"}}
    shop_ids = _load_shop_ids(tmp_path, tmp_path)
    assert shop_ids == {"ShopOnly_01"}

    m = _collection_catalog_from_codex(tmp_path, tmp_path, [], ach, shop_ids)
    rows = {r["id"]: r for r in m["items"]}
    # achievement reward (no coords) -> obtainable
    assert rows["Mount_Goat_04"]["obtainable"] is True
    # cash-shop items: pattern-matched id and shop.json id -> obtainable
    assert rows["SparkHorse_01"]["obtainable"] is True
    assert rows["ShopOnly_01"]["obtainable"] is True
    # genuinely unreleased (no achievement, no shop) -> not obtainable
    assert rows["Mount_Wolf_01"]["obtainable"] is False
    # world-drop mount keeps its obtainable status
    assert rows["Mount_Boar_03"]["obtainable"] is True


def test_summary_counts_obtainable_only():
    s = col.summary(set())
    coll, total = s["_overall"]
    assert coll == 0
    n_unobtainable = sum(1 for r in col.items() if not r["obtainable"])
    assert total == len(col.items()) - n_unobtainable


def test_summary_progress_math():
    one_mount = next(r["id"] for r in col.items("mounts") if r["obtainable"])
    s = col.summary({one_mount})
    assert s["mounts"][0] == 1
    assert s["_overall"][0] == 1
    # an unknown id counts nowhere
    s2 = col.summary({"NotARealItem"})
    assert s2["_overall"][0] == 0


def test_matches_filters():
    row = next(r for r in col.items("mounts") if r["obtainable"])
    assert col.matches(row)
    assert col.matches(row, query=row["name"][:4].lower())
    assert not col.matches(row, query="zzzzzz_no_such_thing")
    assert col.matches(row, subtype=row["subtype"])
    assert not col.matches(row, subtype="NotASubtype")
    assert col.matches(row, state="missing", owned=set())
    assert not col.matches(row, state="collected", owned=set())
    assert col.matches(row, state="collected", owned={row["id"]})
