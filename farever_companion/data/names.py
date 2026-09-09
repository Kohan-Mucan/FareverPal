"""Display-name resolver: internal CDB id -> the game's readable name.

Reuses the display data layer (assets/data/{items,enemies}.json), the same
id -> name mapping the game renders, e.g.
    DM_Multispin   -> "Twin Pillars of Justice"
    MunsterChuck   -> "Munster Chuck"
    UpgradeRare    -> "Spark Shard"
Falls back to a friendly token label, then to the id itself.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import cdb


@lru_cache(maxsize=1)
def _items() -> dict[str, str]:
    return {r["id"]: (r.get("name") or r["id"]) for r in cdb.display_data("items")}


@lru_cache(maxsize=1)
def _units() -> dict[str, str]:
    return {r["id"]: (r.get("name") or r["id"]) for r in cdb.display_data("enemies")}


@lru_cache(maxsize=1)
def _skills() -> dict[str, str]:
    return {r["id"]: (r.get("name") or r["id"]) for r in cdb.display_data("skills")}


@lru_cache(maxsize=1)
def _status_item_counts() -> dict[str, int]:
    """status-id -> HOW MANY items declare it. The compiler stamps the
    consumables' buff refs as row['statusRefs'] (props.effects[].status[].ref
    from the raw item sheet — the buff SKILL rows list no source, so the item
    sheet is the only authority)."""
    out: dict[str, int] = {}
    try:
        for r in cdb.lines("item"):
            for ref in (r.get("statusRefs") or []):
                if ref:
                    out[ref] = out.get(ref, 0) + 1
    except Exception:
        pass
    return out


def status_is_shared(status_id: str | None) -> bool:
    """True when MORE THAN ONE item declares this status, so the id alone
    cannot say which item's buff is active.

    In the current data exactly one status is shared: 'ItemStatus', declared by
    48 different items (every elixir, food and potion uses it). Naming a buff
    after 'the item that grants ItemStatus' therefore picks an arbitrary one —
    the live read shows an ElixirOfDexterity_Z2 buff labelled "Minor Alchemist
    Cauldron", the first cauldron in the sheet that happens to declare it.
    """
    if not status_id:
        return False
    return _status_item_counts().get(status_id, 0) > 1


@lru_cache(maxsize=1)
def _items_by_status() -> dict[str, str]:
    """status-id -> the item that grants it ('Whetstone_Status' -> 'Whetstone').

    Only UNAMBIGUOUS statuses are mapped: an id several items declare (see
    status_is_shared) is dropped rather than resolved to whichever item the
    sheet listed first, which would name the buff after the wrong item."""
    out: dict[str, str] = {}
    counts = _status_item_counts()
    try:
        for r in cdb.lines("item"):
            refs = r.get("statusRefs") or []
            if r.get("id"):
                for ref in refs:
                    if ref and counts.get(ref, 0) == 1:
                        out.setdefault(ref, r["id"])
    except Exception:
        pass
    return out


@lru_cache(maxsize=1)
def _status_to_item() -> dict[str, str]:
    """status-id -> the granting item id, falling back to the sheet's own
    '_Status'-suffix convention ('Whetstone_Status' -> 'Whetstone'). Handles
    the sheet's one typo: the Weightstone's status is authored
    'Weighstone_Status' (missing the 't')."""
    out = dict(_items_by_status())
    for item_id in (r.get("id") or "" for r in cdb.lines("item")):
        if not item_id:
            continue
        out.setdefault(f"{item_id}_Status", item_id)
        out.setdefault(f"{item_id.replace('t', '')}_Status", item_id)
    return out


def status_source_item(status_id: str | None) -> str | None:
    """The item id granting a buff status ('Weighstone_Status' ->
    'Weightstone'), or None when the status isn't item-sourced. Used both for
    naming (status_name) and icon resolution (the item's atlas sprite)."""
    if not status_id:
        return None
    return _status_to_item().get(status_id)


def status_name(status_id: str | None,
                source_item: str | None = None) -> str | None:
    """A live buff id (st.skill.Status.kind) -> readable text.

    Real status rows (skills.json) carry their id as the name ('Whetstone
    Status'), which reads as noise. When the buff comes from an item — a food
    buff like Whetstone_Status / Weighstone_Status — show the GRANTING item's
    name instead ('Whetstone' / 'Weightstone', per the item sheet); other
    statuses keep the skill sheet's name, then humanize.

    `source_item` is the item the LIVE status says granted it (st.skill.Status
    .originItem), and it is what names a SHARED status: 'ItemStatus' is used by
    every elixir/food/potion, so the id can't identify the buff but the live
    item can ('ElixirOfDexterity_Z2' -> "Elixir of Dexterity"). Such a status is
    never named from the sheet map — that is how an elixir buff came out as
    "Minor Alchemist Cauldron".

    A status row the sheet does not name falls back to its parent skill's name
    (`_parent_skill_name`), exactly as the damage log does: a passive's buff is
    itself a derived row, so `Halos_Totem_Passive_Aoe_Status` reads as the
    passive ("Power of the Tides") rather than a humanized id.
    """
    if not status_id:
        return status_id
    if source_item and status_is_shared(status_id):
        nm = item_name(source_item)
        if nm:
            return nm
    item_id = status_source_item(status_id)
    if item_id:
        nm = item_name(item_id)
        if nm:
            return nm
    nm = _skills().get(status_id)
    if nm and nm != status_id:
        return nm
    # A derived status row the sheet never named inherits its parent skill's
    # name, the same rule `skill_name` applies to the damage log: a passive's
    # buff is a status row (`..._Passive_Status`), so without this walk the
    # party page listed "Halos Totem Passive Aoe Status" where the skill the
    # player knows is "Power of the Tides".
    parent = _parent_skill_name(status_id)
    if parent:
        return parent
    # An id that is an ITEM'S OWN id. Some buffs are reported as the granting
    # item rather than as its `<Item>_Status` row (`Trinket_Demon_Status` is the
    # sheet's convention, but a weapon/trinket passive is named after the piece
    # and carries no `_Status` row of its own). The item sheet names it and
    # humanizing does not: 'DM_Multispin' reads 'DM Multispin' where the sheet
    # says 'Twin Pillars of Justice', and 'ElixirOfDexterity_Z2' reads 'Elixir
    # Of Dexterity Z 2' where the sheet says 'Elixir of Dexterity'. Checked
    # AFTER the item-status and parent-skill routes, so every id that already
    # resolved keeps its answer; only the humanize fallback is replaced.
    nm = _items().get(status_id)
    if nm and nm != status_id:
        return nm
    return humanize(status_id) or status_id


@lru_cache(maxsize=8192)
def _ancestor_ids(skill_id: str) -> list[str]:
    """Every ancestor id of a derived row, nearest first.

    Two shapes of derived row exist in the shipped sheet, and only one of them
    is reachable by splitting on `_`:

    - **Segment suffix** - the marker is its own `_`-separated segment
      (`DS_Bladeleaf_Skill2` + `_Status`). Splitting reaches the parent.
    - **Concatenated suffix** - the marker is GLUED onto the last segment
      (`Daggers_DuplicatePoison_Passive` + `Status` ->
      `..._PassiveStatus`). No split can ever produce the parent, so every
      lookup that walks by `_` alone dead-ends on these rows.

    22 rows in the shipped sheet use the concatenated form, and the fix has to
    be shape-agnostic rather than a list of known markers: the parent is
    confirmed to EXIST in the sheet (a real id with a real display name), which
    is what separates a derived row from an id that merely ends in a similar
    string (`SparkSurge_Mark` must not become `SparkSurge`).

    The result is a PREFIX, so a nested chain is reachable by iterating: each
    candidate is fed back through the walk, and `skills.py`/`names.py` dedupe.
    """
    if not skill_id:
        return []
    out: list[str] = []
    parts = skill_id.split("_")
    # 1. Segment suffixes: drop trailing `_`-separated segments, nearest first.
    for cut in range(len(parts) - 1, 0, -1):
        cand = "_".join(parts[:cut])
        nm = _skills().get(cand)
        if nm and nm != cand:
            out.append(cand)
    # 2. Concatenated suffix: peel an ALPHABETIC marker off the LAST segment,
    #    and accept only a candidate the sheet actually names.
    #
    #    Alphabetic-only is load-bearing, not a style choice. A marker that
    #    STARTS WITH A DIGIT is a numbered VARIANT, not an effect: the sheet
    #    carries `Rogue_KnivesTempest_Status` ("Silent Storm") beside
    #    `..._Status2` ("Shred"), and `Halos_Totem_Skill2` ("Riptide") beside
    #    `Halos_Totem_Skill` ("Tidal Totem") - distinct skills that share a
    #    prefix, NOT a skill and its derived half. Peeling the digit would fold
    #    them together: `Halos_Totem_Skill1` would read "Tidal Totem" instead
    #    of its own humanized id (the case test_party_inspect guards). Every
    #    real effect marker here is a word - `Status`, `Area`, `Fire`, `Bis`,
    #    `BlockBuffed`, `MoveStatus`, `FailState` - and the digit forms are
    #    already reachable by the segment walk above, which stops at
    #    `Mage_ShieldOfSpark` for `Mage_ShieldOfSpark_Status2`.
    #
    #    A nested chain is reachable because each accepted base is fed back
    #    through the walk by the caller (`Ratsar_GreatSlamAreaFire` ->
    #    `...Area` -> `...GreatSlam`).
    if parts:
        head = parts[:-1]
        last = parts[-1]
        for n in range(1, len(last)):
            marker = last[-n:]
            if marker[0].isdigit():
                continue
            cand = "_".join(head + [last[: len(last) - n]])
            nm = _skills().get(cand)
            if nm and nm != cand and cand not in out:
                out.append(cand)
    return out


@lru_cache(maxsize=8192)
def parent_skill_id(skill_id: str) -> str | None:
    """The nearest ANCESTOR skill ID of a derived row, or None.

    The same walk `_parent_skill_name` does, but returning the ID rather than
    the display name, because the damage log needs to know WHICH bucket a hit
    belongs to, not what to call it.

    It exists because a derived row carries the SAME name as its parent, so the
    meter showed the player two identical rows for one skill: `Axe_Boomerang_
    Skill1` (Bonethrow, the throw) and `Axe_Boomerang_Skill1_Status`
    (Bonethrow, the bleed it applies) are both named "Bonethrow" in the
    sheet. The bleed's damage IS the skill's damage, so it belongs in that
    bucket rather than beside it (see `dps_data`'s sub-skill fold).

    Both derived-row shapes are walked (see `_ancestor_ids`). The fold itself
    is unaffected by the wider walk: it only fires when the parent is ALREADY a
    row of the same parse; a parent the player never used cannot be created
    from a name (verified over this player's archives - zero rows change
    bucket), so the concatenated rows keep their own rows and only gain the
    identity lookups they were missing.
    """
    if not skill_id:
        return None
    anc = _ancestor_ids(skill_id)
    return anc[0] if anc else None


@lru_cache(maxsize=8192)
def _parent_skill_name(skill_id: str) -> str | None:
    """The nearest ANCESTOR skill that has a real display name, or None.

    The game's skill sheet names a skill but leaves its derived rows blank
    (the display name comes through as the id itself). `Halos_Totem_Passive_Aoe`
    is the damage-dealing half of the "Power of the Tides" passive and has no
    name of its own, so the log showed the humanized id ('Halos Totem Passive
    Aoe') instead of the passive the player recognizes.

    Walk the id's `_`-segments from the end and return the first ancestor that
    resolves to a real name; the trailing segments are exactly the effect
    markers a derived row carries (`_Aoe`, `_Status`, `_Proc`, `_Slow`,
    `_Shield`, `_Buff`, `_Recast`, a rank/stack suffix). A parent that is also
    unnamed returns None and the walk continues.
    """
    for cand in _ancestor_ids(skill_id):
        nm = _skills().get(cand)
        if nm and nm != cand:
            return nm
    return None


def skill_name(skill_id: str | None) -> str | None:
    """Internal skill id (BaseSkill.kind, e.g. 'Priest_Prayer_Smite') -> the
    game's readable name ('Prayer: Smite'), via assets/data/skills.json.

    A derived row the sheet never named inherits its parent skill's name
    (`Halos_Totem_Passive_Aoe` -> 'Power of the Tides'), so a passive's proc
    reads as the passive rather than a humanized id. Falls back to a humanized
    id ('Mace_Base_Attack' -> 'Mace Base Attack')."""
    if not skill_id:
        return skill_id
    nm = _skills().get(skill_id)
    if nm and nm != skill_id:
        return nm
    parent = _parent_skill_name(skill_id)
    if parent:
        return parent
    return humanize(skill_id) or skill_id


def subskill_label(skill_id: str | None, parent_id: str | None = None) -> str | None:
    """The label for a derived row shown NESTED under the skill it folded into.

    A folded row shares its parent's display name by construction - that is
    exactly why it was folded (`Axe_Boomerang_Skill1` and its `_Status` are
    both "Bonethrow") - so printing the bare name on the nested row would show
    the same two words twice and say nothing. The id's own tail is the part
    that is actually different: `..._Skill1_Status` -> "Status". Append it
    after the name so the nested row is self-describing without needing the
    tooltip.
    """
    label = skill_name(skill_id)
    if not label:
        return skill_id
    tail = ""
    if parent_id and skill_id and skill_id.startswith(parent_id + "_"):
        tail = humanize(skill_id[len(parent_id) + 1:])
    if tail and tail.lower() != label.lower():
        return f"{label} · {tail}"
    return label


def item_name(item_id: str | None) -> str | None:
    if not item_id:
        return item_id
    name = _items().get(item_id)
    return name or item_id


def item_label(item_id: str | None) -> str | None:
    """Item name when the sheet names it, else the HUMANIZED id — never a raw
    id. This is the answer `unit_name` already gives for an unknown unit, and
    the one a row wants: `item_name` hands back the id unchanged for a piece
    the sheets do not carry, so a party slot rendering that shows the player
    `CursedBlade_Z2`. Falls through the same way — name, else words, else the
    id — so a label site can never print an id it could have spelled out."""
    if not item_id:
        return item_id
    nm = _items().get(item_id)
    if nm and nm != item_id:
        return nm
    return humanize(item_id) or item_id


def unit_name(unit_id: str | None) -> str | None:
    """Internal unit id -> the game's readable name.
    Handles generic fallbacks and makes variants (Spark, Raminiature) distinct."""
    if not unit_id:
        return unit_id
    
    # Handle path-like IDs
    clean_id = unit_id
    if "/" in clean_id or "\\" in clean_id:
        import os
        clean_id = os.path.splitext(os.path.basename(clean_id))[0]
    
    # 1. Try display manifest (enemies.json)
    name = _units().get(clean_id)
    
    # 2. Try raw unit sheet if manifest failed or gave a generic name
    # We ignore generic plural names ("Companions", "Enemies") to ensure we can 
    # fall back to humanizing the specific ID (e.g. DemonDog_Beige).
    if not name or name == clean_id or name in ("Companions", "Enemies", "Critter", "Critters"):
        from . import units
        raw_row = units._units_by_id().get(clean_id)
        if raw_row:
            raw_name = raw_row.get("name") or (raw_row.get("texts") or {}).get("name")
            if raw_name and raw_name not in ("Companions", "Enemies", "Critter", "Critters"):
                name = raw_name
            else:
                # Try the unit type's name (e.g. "Mount" or "Boss")
                utype = raw_row.get("type")
                if utype:
                    tname = units.type_name(utype)
                    if tname and tname != utype:
                        name = tname

    # 3. If we still have a generic name, or nothing at all, humanize the ID
    if not name or name == clean_id or name in ("Companions", "Enemies", "Critter", "Critters"):
        name = humanize(clean_id)

    # 4. Post-processing: Ensure 'Spark' is tagged
    if name:
        # Append (Spark) if missing from the name but present in the ID
        if "spark" in (clean_id or "").lower() and "spark" not in name.lower():
            name = f"{name} (Spark)"

    return name or clean_id


def any_name(some_id: str | None) -> str | None:
    """Unit name, else item name, else the id unchanged."""
    if not some_id:
        return some_id
    return _units().get(some_id) or _items().get(some_id) or some_id


def humanize(raw: str | None) -> str:
    """Turn a CamelCase / snake id into spaced words: 'WorldCrate' -> 'World
    Crate', 'UpgradeItems_Activity' -> 'Upgrade Items Activity'."""
    if not raw:
        return ""
    s = raw.replace("_", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)       # camelCase boundary
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)     # ACRONYMWord boundary
    s = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", s)          # letter|digit boundary
    return re.sub(r"\s+", " ", s).strip()


def poi_label(raw: str | None) -> str:
    """Clean name for a POI (Obelisk, Respawn, etc). Strips Z#/W# prefixes."""
    if not raw:
        return ""
    
    # 1. Strip technical prefixes
    # Pattern: Z1_World_Greenlands_... -> ...
    res = re.sub(r"(?i)^[ZW]\d+_World_[^_]+_", "", raw)
    # Pattern: W1_Siagarta_... -> ...
    res = re.sub(r"(?i)^W\d+_Siagarta_", "", res)
    
    # 2. Humanize
    res = humanize(res)
    
    # 3. Strip remaining Z 1, W 2, etc. (humanize might have space-separated them)
    res = re.sub(r"(?i)\b[ZW]\s?\d+\b", "", res).strip()
    
    return res


@lru_cache(maxsize=1)
def _zone_region_names() -> dict[str, str]:
    """Zone-tier code digit -> region display name, e.g. {'1': 'Skover Island',
    '2': 'Valley of Eternal Autumn'} from the zone sheet (Z#_Region.texts.name)."""
    out: dict[str, str] = {}
    try:
        for r in cdb.lines("zone"):
            zid = r.get("id", "")
            m = re.fullmatch(r"Z(\d+)_Region", zid)
            if m:
                # Handle both raw JSON (texts.name) and baked data (flattened name)
                nm = r.get("name") or (r.get("texts") or {}).get("name")
                if nm:
                    out[m.group(1)] = nm
            elif zid == "CrimsonIsland_Region":
                nm = r.get("name") or (r.get("texts") or {}).get("name")
                if nm:
                    out["3"] = nm
    except Exception:
        pass
    return out


@lru_cache(maxsize=1)
def _all_zone_names() -> dict[str, str]:
    """Full mapping of zone ID -> display name from the zone sheet."""
    out: dict[str, str] = {}
    try:
        for r in cdb.lines("zone"):
            zid = r.get("id")
            if zid:
                nm = r.get("name") or (r.get("texts") or {}).get("name")
                if nm:
                    out[zid.lower()] = nm
    except Exception:
        pass
    return out


def zone_name(zone_id: str | None) -> str | None:
    """Technical zone ID -> readable name (e.g. 'Z1_Enripit_Falls' -> 'Talitha Falls')."""
    if not zone_id:
        return None
    return _all_zone_names().get(zone_id.lower()) or humanize(zone_id)


def zone_display(zone_id: str | None) -> str | None:
    """The zone's REAL name, or None when the id is not in the zone sheet.

    `zone_name` falls back to `humanize`, so an id the sheet does not know
    still comes back as something — "Z9_Unknownlands" -> "Z 9 Unknownlands",
    with the letter/digit split that `humanize` inserts. Callers that must not
    put a raw technical id in front of the user (the party-member location
    label) cannot pattern-match that output; they need to know whether the
    lookup actually resolved, which is what this answers.
    """
    if not zone_id:
        return None
    return _all_zone_names().get(zone_id.lower())


def chest_label(chest_id: str | None, loot_table: str | None = None) -> str:
    """Friendly label for a chest, stripping redundant world/zone prefixes."""
    # Favor the ID for the label if it's a generic world table, as IDs usually 
    # contain more specific location info (e.g. 'Camp 1')
    use_id = not loot_table or loot_table in ("WorldCrate", "WorldChest", "WorldActivity")
    raw = chest_id if (use_id and chest_id) else (loot_table or chest_id)

    if not raw:
        return "Chest"

    # 1. Strip technical prefixes using regex to avoid 'len' unresolved reference
    # Pattern: Z1_World_Greenlands_... -> ...
    clean_raw = re.sub(r"(?i)^[ZW]\d+_World_[^_]+_", "", raw)
    # Pattern: W1_Siagarta_... -> ...
    clean_raw = re.sub(r"(?i)^W\d+_Siagarta_", "", clean_raw)

    # 2. Humanize
    label = humanize(clean_raw)
    
    # 3. Handle Recipes and Chests specially
    if re.search(r"(?i)\bRecipe\b", label):
        # For recipes, strip technical "noise" words
        label = re.sub(r"(?i)\b(Chest|World|Root|Stem|Leaf|Activity)\b", "", label).strip()
        if not label.lower().startswith("recipe"):
            label = f"Recipe {label}"
    elif re.search(r"(?i)\bChest\b", label):
        # For normal chests, strip noise and ensure "Chest" is at the start
        label = re.sub(r"(?i)\b(Chest|World|Root|Stem|Leaf|Activity)\b", "", label).strip()
        label = f"Chest {label}"
    
    # 4. Append numeric suffix from chest_id if missing (e.g. "Chest 53")
    if chest_id:
        m = re.search(r"(\d+)$", chest_id)
        if m and not re.search(rf"\b{m.group(1)}\b", label):
            label = f"{label} {m.group(1)}"

    # 5. Final formatting
    label = re.sub(r"\s+", " ", label).strip()
    
    if not label:
        return "Chest"
    
    # Capitalize properly
    if label.islower():
        label = label.title()
    else:
        label = label[0].upper() + label[1:]

    return label
