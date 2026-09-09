"""Pure row/label helpers for the Party Inspect page.

Split out of party_page.py (which the ui/ line budget caps) exactly the way
entity_rows.py and dps_source_text.py split from their pages: the maths and the
wording live here, the widgets live in the page. party_page re-exports every
name, so callers and tests still reach them through the page.

Nothing here reads the process or touches a widget — each function takes what
the readers found and decides only how to say it.
"""
from __future__ import annotations


def _buff_label(b: dict) -> str:
    """What to CALL an active buff: the granting item first when the status id
    can't identify it (st.skill.Status.originItem — the live row's
    `source_item`), then the sheet's status name, skill name, item name, and
    finally the id as words.

    Live 2026-09-15: a character carrying two statuses read one right (the
    axe's `Axe_Boomerang_Skill_Passive_Status` -> "Bloodrage Aura") and one
    wrong — an elixir's generic `ItemStatus` (48 items share it) rendered as
    "Minor Alchemist Cauldron", the first cauldron in the sheet that declares
    it. The live row knew: it said ElixirOfDexterity_Z2.
    """
    from ...data import names as gnames
    kind = b.get("kind") or ""
    return (gnames.status_name(kind, b.get("source_item"))
            or gnames.skill_name(kind)
            or gnames.item_name(kind)
            or gnames.humanize(kind) or kind or "?")


def _buff_tail(b: dict) -> str:
    """The parenthetical after a buff's name — stacks, time left, granting item
    — each only when the game actually states it, as "  (x2 · 90.0s · from X)".

    Two live shapes must not be dressed up as data. A stack count of 1 is the
    norm and says nothing. A duration of EXACTLY 0 is what the game reports for a
    status that does not expire — the local player's only status right now is
    exactly that, the axe's own `Axe_Boomerang_Skill_Passive_Status` ("Bloodrage
    Aura", duration 0) — and rendering it as "0.0s" read like a buff about to
    fall off a buff that never does. Both are dropped; the rest is shown as the
    reader found it, never recomputed."""
    from ...data import names as gnames
    bits = []
    stacks = b.get("stacks")
    if stacks and stacks > 1:
        bits.append(f"x{stacks}")
    # The status's duration field is deliberately NOT shown: live probes
    # (2026-09-16) show it frozen — 2784.43s stayed 2784.43 for 20s+, 8.43s for
    # 40s+ — it is the effect's TOTAL duration, never a countdown, and the game
    # stores no remaining time, so any time figure would be a fabricated one.
    if b.get("source_item") and not gnames.status_is_shared(b.get("kind")):
        # …unless the row is already NAMED after that item (a shared status like
        # 'ItemStatus' is), where "Elixir of Dexterity (from Elixir of
        # Dexterity)" would just say it twice
        src = gnames.item_name(b["source_item"]) or b["source_item"]
        bits.append(f"from {src}")
    return f"  ({' · '.join(bits)})" if bits else ""


def _gear_slot_labels(gear: list[dict]) -> list[str]:
    """One slot label per gear row, so the list reads "Head — Brutality
    Faceshield" instead of "Slot 3 — …".

    The label comes from the piece's own sheet type — armor, jewelry and
    containers all name their slot there (`slot_label` renders GearFinger /
    GearNeck / GearTrinket as Ring / Neck / Trinket) — not from the live array
    index, which is just the game's 30-slot order. Repeats are numbered in the
    order the game hands the pieces over (the two rings read Ring 1 / Ring 2),
    and a piece the sheet can't name falls back to its live slot index so the
    row still says where it came from."""
    from ...data.items import catalog as idata
    from .items.support import slot_label

    labels: list[str] = []
    for g in gear:
        typ = (idata.item(g.get("kind") or "") or {}).get("type")
        labels.append(slot_label(typ) if typ else f"Slot {g.get('slot', '?')}")
    counts: dict[str, int] = {}
    for lbl in labels:
        counts[lbl] = counts.get(lbl, 0) + 1
    if all(n == 1 for n in counts.values()):
        return labels
    nth: dict[str, int] = {}
    out: list[str] = []
    for lbl in labels:
        if counts[lbl] > 1:
            nth[lbl] = nth.get(lbl, 0) + 1
            out.append(f"{lbl} {nth[lbl]}")
        else:
            out.append(lbl)
    return out


def _live_pet(model, addr: int, pet_id: str):
    """The scene entity that IS this hero's pet, or None when the creature
    isn't loaded in the world.

    The join is deliberately strict: a player-owned non-hero unit whose unit_id
    is the loadout's pet AND whose owner is the hero being inspected (live
    2026-09-15: your pet is an ent.Foe whose owner_addr is your own hero
    address). `is_player_owned` alone is not enough — another player's pet of
    the same species is player-owned too — so an owner that doesn't resolve to
    this hero is not shown as theirs.
    """
    if not pet_id:
        return None
    try:
        for u in model.units() or []:
            if (getattr(u, "unit_id", None) == pet_id
                    and not getattr(u, "is_hero", False)
                    and getattr(u, "is_player_owned", False)
                    and getattr(u, "owner_addr", 0) == addr):
                return u
    except Exception:
        return None
    return None


def _pet_labels(pet_id: str, live) -> tuple[str, str]:
    """(creature name, mono sub-line) for the Companion row.

    The name is the game's own unit-sheet name for the id — 'Goat_Demon' reads
    Demonic Raminiature — never the id's spelling, and the sub-line states only
    what is true of the LIVE creature: its own class and level when the entity is
    in the scene, and that it is equipped but not loaded when it isn't.

    hp is printed only above zero. The snapshot does not fill hp for companions
    (the pet entity reads hp=0 live), so a 0 there would be a fabrication dressed
    up as a readout, and a creature at 0 hp is not something a companion is.
    """
    from ...data import names as gnames
    # `unit_name` already ends in humanize for an id no sheet names, so the
    # `or humanize` that used to follow it could never run.
    name = gnames.unit_name(pet_id)
    bits = [pet_id]
    if live is None:
        bits.append("equipped in game, not loaded in this scene")
        return name, "  ·  ".join(bits)
    if getattr(live, "cls", None):
        bits.append(live.cls)
    lvl = getattr(live, "level", 0) or 0
    if lvl:
        bits.append(f"Lv {lvl}")
    hp = getattr(live, "hp", 0.0) or 0.0
    if hp > 0:
        bits.append(f"HP {hp:,.0f}")
    return name, "  ·  ".join(bits)
