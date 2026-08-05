"""Codex data-flow: compiled raw_codex.py -> data/codex.py accessors (headless)."""
from farever_companion.data import codex


def test_all_regions_compiled():
    d = codex.codex_order()
    assert {"Z1", "Z2", "Z3", "Mounts", "Gliders", "Pets", "Z0", "Bosses"} <= set(d)
    for rid in ("Z1", "Z2", "Z3", "Mounts", "Gliders", "Pets", "Bosses"):
        assert len(d[rid]) > 40, rid
    assert len(d["Z0"]) >= 25


def test_schema_fields_survive_compilation():
    d = codex.codex_order()
    # species families for pets/mounts
    assert any(e.get("group") for e in d["Pets"])
    assert any(e.get("group") for e in d["Mounts"])
    # world source metadata
    assert any(e.get("vendor_npc") for e in d["Gliders"])
    assert any(e.get("chest_loc") for e in d["Mounts"])
    # Z0 misc carries locked kind flags (todo / unreleased)
    assert any(e.get("kind") for e in d["Z0"])


def test_units_by_region_z0_merges_mounts_and_gliders():
    z0 = codex.units_by_region("Z0")
    ids = {e["id"] for e in z0}
    assert any(i.startswith("Mount_") for i in ids)
    assert any(i.startswith("Glider_") for i in ids)
    # every entry has a resolvable display name
    assert all(e.get("name") for e in z0)


def test_others_all_excludes_released_mounts_gliders():
    """The Others tab is the 'no home yet' bucket: cash-shop items,
    unreleased content, and todo placeholders. Released mounts/gliders
    (world drops and achievement rewards) belong on the Collection tab, so
    the Others 'All' sub-view must not include them."""
    d = codex.codex_order()

    # Every released / achievement mount & glider is excluded from Others...
    for zone in ("Mounts", "Gliders"):
        for e in d[zone]:
            k = (e.get("kind") or "").lower()
            if k in ("unreleased", "todo"):
                continue  # not released yet — belongs in Others
            # shop items are buyable NOW, so they stay in Others (Shop view)
            if codex.is_shop_item(e):
                assert codex.belongs_in_others(e), e["id"]
            else:
                assert not codex.belongs_in_others(e), e["id"]

    # ...and the merged Z0 rows the Others tab renders carry only shop /
    # unreleased / todo rows once the released mounts & gliders are removed.
    others_all = [e for e in codex.units_by_region("Z0") if codex.belongs_in_others(e)]
    assert others_all
    for e in others_all:
        assert (codex.is_shop_item(e)
                or (e.get("kind") or "").lower() in ("unreleased", "todo")
                or "todo" in (e.get("id") or "").lower()
                or "todo" in (e.get("name") or "").lower()), e["id"]
    # released mounts/gliders are exactly the rows Others drops
    dropped = [e for e in codex.units_by_region("Z0") if not codex.belongs_in_others(e)]
    assert dropped
    assert all((e.get("kind") or "").lower() not in ("unreleased", "todo")
               for e in dropped)


def test_pets_grouped_by_species():
    pets = codex.units_by_region("Pets")
    species = {p.get("type") for p in pets}
    assert len(species) >= 5, species          # not one flat "Critter" group
    assert "Critter" not in species


def test_units_by_region_no_duplicates():
    for rid in codex.codex_order():
        ids = [e["id"] for e in codex.units_by_region(rid)]
        assert len(ids) == len(set(ids)), rid


def test_bosses_carry_dungeon_metadata():
    bosses = codex.units_by_region("Bosses")
    assert any(e.get("is_dungeon") for e in bosses)
    assert any(e.get("level") for e in bosses)


def test_enemies_data_merges_cdb_and_codex_keys():
    info = codex.enemies_data()
    assert len(info) > 100
    # a raw_codex entry enriched over the CDB row keeps both key styles
    m = next((v for v in info.values() if v.get("vendor_npc")), {})
    assert m and "id" in m and "name" in m


def test_bosses_resolve_via_compile_time_dungeon_loc():
    """Dungeon mobs carry the entrance location baked in, so the resolver is a
    direct lookup instead of a per-click POI fuzzy search."""
    bosses = codex.units_by_region("Bosses")
    d_entries = [e for e in bosses if e.get("dungeon_loc")]
    assert d_entries, "compiler should stamp dungeon_loc on dungeon mobs"
    # every dungeon-marked mob resolves to its entrance coords
    for e in d_entries[:25]:
        coords, title, _ = codex.resolve_locations(e["id"])
        assert coords, e["id"]
        assert title, e["id"]


def test_mounts_gliders_resolve_via_drops_from_or_vendor_chest():
    """Mounts/gliders carry their drop-mob links (or vendor/chest sources) so
    the resolver is a dict lookup, not a full enemies_data scan."""
    d = codex.codex_order()
    linked = [e for e in d["Mounts"] + d["Gliders"]
              if e.get("drops_from") or e.get("vendor_npc") or e.get("chest_loc")
              or e.get("dungeon_loc") or e.get("coords")]
    assert len(linked) >= 90, len(linked)
    # every non-todo/unreleased mount/glider resolves to map coords
    # (achievement-only rewards are coords-less by design — they resolve to
    # their achievement title instead)
    for zone in ("Mounts", "Gliders"):
        resolvable = [e for e in d[zone]
                      if (e.get("kind") or "").lower() not in ("todo", "unreleased", "achievement")]
        assert resolvable
        for e in resolvable:
            coords, _t, _r = codex.resolve_locations(e["id"])
            assert coords, (zone, e["id"])
    # drops_from references real enemies (dungeon mobs resolve via dungeon_loc,
    # wild mobs via embedded spawn coords)
    resolved = [e for e in d["Mounts"] + d["Gliders"] if e.get("drops_from")]
    assert resolved
    for e in resolved[:10]:
        for df in e["drops_from"]:
            assert df["id"] in codex.enemies_data(), (e["id"], df["id"])


def test_rift_reward_items_resolve_to_arena():
    """Rift reward tables (Rift_Tier6 / Rift_BonusChest / Rift_Bosschest) are
    anchored at the rift arena entrance, so rift-sourced mounts/gliders get a
    real location instead of being locked as unreleased."""
    d = codex.codex_order()
    rift_items = [e for e in d["Mounts"] + d["Gliders"]
                  if (e.get("chest_id") or "").startswith("Rift_")
                  or (e.get("dungeon_name") or "") in ("Rift Arena", "Rift")]
    assert rift_items, "no rift-sourced mounts/gliders in the codex"
    for e in rift_items:
        # never locked as unreleased once it has a rift source
        assert (e.get("kind") or "").lower() not in ("todo", "unreleased"), e["id"]
        coords, title, _r = codex.resolve_locations(e["id"])
        assert coords, e["id"]
        assert title, e["id"]
    # the tier-6 glider (Niflelian Dragoon) is the canonical regression
    dragoon = next((e for e in d["Gliders"] if e["id"] == "Glider_Dragon_Demon"), None)
    assert dragoon, "Glider_Dragon_Demon (Rift_Tier6) missing from Gliders"
    coords, title, _r = codex.resolve_locations(dragoon["id"])
    assert coords and title, dragoon["id"]


def test_achievement_rewards_stamp_source_info():
    """Mounts/gliders awarded by achievements (ach.json rewards) carry the
    achievement baked into the compiled codex, so the source shows instead of
    a blank 'unreleased' card."""
    d = codex.codex_order()
    by_id = {e["id"]: e for e in d["Mounts"] + d["Gliders"]}
    goat = by_id.get("Mount_Goat_04")
    assert goat, "Crimson Goat (Savior of Skover reward) missing"
    assert goat.get("achievement", {}).get("name") == "Savior of Skover"
    assert goat["achievement"]["category"] == "Combat"
    assert goat["achievement"]["points"] == 10
    # achievement rewards are obtainable in-game — never mislabeled unreleased
    assert goat.get("kind") == "achievement"
    assert (goat.get("kind") or "").lower() not in ("todo", "unreleased")
    # desc resolves the same [Z1_Region] placeholders as the name
    assert goat["achievement"]["desc"] == "Complete all the Dungeons in Skover Island."
    # [Z1_Region] style placeholders resolve to the region name
    bestiary = by_id.get("Mount_Goat_05")
    assert bestiary.get("achievement", {}).get("name") == "Bestiary of Skover Island"
    owl = by_id.get("Glider_Owl_BlackWarm")
    assert owl.get("achievement", {}).get("name") == "Collector of Skover Island"
    # collection-count milestones have no display name in ach.json — the
    # compiler derives one from the id ('CollectMounts_10' -> 'Collect Mounts 10')
    # and resolves the ::targetValue:: template token from the same id
    boar = by_id.get("Mount_Boar_05")
    assert boar.get("achievement", {}).get("name") == "Collect Mounts 10"
    assert boar["achievement"]["desc"] == "Collect 10 Mounts."


def test_achievement_sources_resolve_as_titles_not_locations():
    """Achievement-sourced mounts have no map coords (achievements are
    meta-goals, not spawns) but the resolver reports the achievement as the
    source title so the map label shows where the item comes from."""
    coords, title, _r = codex.resolve_locations("Mount_Goat_04")
    assert not coords
    assert title == "Achievement · Savior of Skover"
    coords, title, _r = codex.resolve_locations("Mount_Goat_05")
    assert not coords
    assert title == "Achievement · Bestiary of Skover Island"


def test_achievement_only_items_not_flagged_unreleased():
    """The compiler reflags achievement-only rewards (no world coords) from
    codex.json's 'unreleased' to 'achievement' — an achievement reward IS
    obtainable in-game, so calling it unreleased would be wrong. World-drop
    mounts that also carry an achievement keep their real spawn coords."""
    d = codex.codex_order()
    flagged = [e for e in d["Mounts"] + d["Gliders"]
               if (e.get("kind") or "").lower() == "unreleased" and e.get("achievement")]
    assert not flagged, [e["id"] for e in flagged]
    # the two cohorts stay distinct: achievement-only (no coords) vs drop (coords)
    only = [e for e in d["Mounts"] + d["Gliders"]
            if (e.get("kind") or "").lower() == "achievement"]
    drops = [e for e in d["Mounts"] + d["Gliders"]
             if e.get("achievement") and e.get("coords")]
    assert only, "achievement-only cohort empty"
    assert drops, "drop+achievement cohort empty"
    assert all(not e.get("coords") for e in only)
    assert all((e.get("kind") or "") not in ("todo", "unreleased") for e in drops)


def test_z0_todo_unreleased_short_circuit():
    """todo/unreleased entries can never have locations — the resolver returns
    empty immediately instead of running the fallback chain."""
    z0 = codex.units_by_region("Z0")
    flagged = [e for e in z0 if (e.get("kind") or "").lower() in ("todo", "unreleased")]
    assert flagged
    for e in flagged[:15]:
        coords, _title, _rift = codex.resolve_locations(e["id"])
        assert not coords, e["id"]


def test_resolver_coverage_is_lookup_driven():
    """Almost every resolvable entry gets coords; only genuinely sourceless
    entries (todo/unreleased, one known pet) stay empty — never a fuzzy POI
    guess that pins the wrong dungeon."""
    d = codex.codex_order()
    unresolved = []
    for entries in d.values():
        for e in entries:
            if (e.get("kind") or "").lower() in ("todo", "unreleased", "achievement"):
                continue
            coords, _t, _r = codex.resolve_locations(e["id"])
            if not coords:
                unresolved.append(e["id"])
    # Brawler Benoit used to fuzzy-pin to Crimson Barracks (a dungeon it does
    # not belong to) — now codex.json supplies his spawn next to Brawler Brahim.
    # Zone mobs without recorded spawn coords stay empty too: the species-group
    # fallback must never misread the zone tag in their id ('Z2W' in
    # 'OgreManfish_Z2W_FS_Claws') as a mob family, which used to paint every
    # Z2W mob's spawns as 'Mob Drop: Z2W Mobs (83 Species)'.
    assert unresolved == ["Kobold_Z1W_Caster", "Kobold_Z1W_Daggers",
                          "OgreManfish_Z2W_FS_Claws", "YellowRabbits"], unresolved
