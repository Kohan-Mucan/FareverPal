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

from . import cdb, tokens


@lru_cache(maxsize=1)
def _items() -> dict[str, str]:
    return {r["id"]: (r.get("name") or r["id"]) for r in cdb.display_data("items")}


@lru_cache(maxsize=1)
def _units() -> dict[str, str]:
    return {r["id"]: (r.get("name") or r["id"]) for r in cdb.display_data("enemies")}


@lru_cache(maxsize=1)
def _skills() -> dict[str, str]:
    return {r["id"]: (r.get("name") or r["id"]) for r in cdb.display_data("skills")}


def skill_name(skill_id: str | None) -> str | None:
    """Internal skill id (BaseSkill.kind, e.g. 'Priest_Prayer_Smite') -> the
    game's readable name ('Prayer: Smite'), via htdocs/assets/data/skills.json.
    Falls back to a humanized id ('Mace_Base_Attack' -> 'Mace Base Attack')."""
    if not skill_id:
        return skill_id
    nm = _skills().get(skill_id)
    if nm and nm != skill_id:
        return nm
    return humanize(skill_id) or skill_id


def item_name(item_id: str | None) -> str | None:
    if not item_id:
        return item_id
    name = _items().get(item_id)
    if name and name != item_id:
        return name
    return tokens.label(item_id) or name or item_id


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


def loot_table_label(tid: str | None) -> str:
    """Readable name for a loot-table id, for the predictor dropdown. Named
    bosses use their display name ('Munster Chuck (Boss)'); zone codes resolve to
    the region name ('Vault_Z2_1' -> 'Vault Valley of Eternal Autumn 1');
    everything else is humanized ('WorldCrate' -> 'World Crate')."""
    if not tid:
        return ""
    from . import units
    if tid in units.named_bosses():
        return f"{unit_name(tid)} (Boss)"
    label = humanize(tid)
    regions = _zone_region_names()
    if regions:                              # Z1/Z2/Z3 -> region name
        label = re.sub(r"\bZ ?([0-9]+)\b",
                       lambda m: regions.get(m.group(1), m.group(0)), label)
    return label


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
