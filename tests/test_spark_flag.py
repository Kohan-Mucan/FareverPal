"""One Spark predicate per question, and every reader asks through it.

The app used to answer "is this a Spark unit" in five places with five private
spelling tests — the card, the Codex grid's filter, the entity HUD row, the
minimap companion dot, the Codex legend row — plus the two overlay/tracker
readers that went through `units.drops_spark`. The spellings happened to agree on
today's data ("spark" is a substring of both "Sparkling" and "Sparkle"), which is
exactly why nothing caught the asymmetry: the id-only tests recognised
`R1KoboldBoss_Sparkling` (name "Snack of Ratsar") while the name-only ones did
not.

Two questions, deliberately separate, one implementation each in `data/units.py`:

* `is_spark_variant` — "is this a sparkling one": the flag OR the game's spelling
  in the id or the displayed name. Critters included; this is what marks a card,
  a HUD row, a legend row, a companion dot.
* `drops_spark` — "does this drop Spark Dust": the flag the build bakes, which is
  the unique-FOE bit minus the compiler's five-unit EXCLUDE_DUST list. Critters
  and mounts/gliders are excluded because they are caught, not killed — the game
  attaches Spark Dust's `FoeUniqueDrops` table to foes, and the app's own item
  index attributes Spark Dust to activity chests/crates and one vendor and to no
  unit at all.

So a sparkling companion is a Spark VARIANT everywhere and a dust source nowhere,
consistently, instead of being one or the other depending on which widget asked.
"""
from __future__ import annotations

import ast
from pathlib import Path

from farever_companion.data import units

ROOT = Path(__file__).resolve().parents[1]

#: Every surface that must ask through the shared predicate. The five that used
#: to carry a private spelling test, plus the three that always delegated.
READERS = (
    "farever_companion/ui/pages/codex/card.py",
    "farever_companion/ui/pages/codex/grid.py",
    "farever_companion/ui/pages/codex/settings.py",
    "farever_companion/ui/overlays/entity_render.py",
    "farever_companion/ui/overlays/minimap_render.py",
    "farever_companion/ui/overlays/entity_gather.py",
    "farever_companion/ui/overlays/minimap.py",
    "farever_companion/ui/tracker.py",
)


def _private_spelling_tests(src: str) -> list[str]:
    """The lines that re-spell the Spark rule: a membership test whose LEFT side
    is a string literal containing "spark" — `"spark" in name.lower()`.

    Read off the AST, not the text, on purpose: a text scan for the exact
    expression each file used to carry would pass the moment someone wrote the
    same rule slightly differently (and would trip over a comment saying so).
    Right-hand tuples are exempt (`kind in ("enemy", "spark_enemy")` tags a POI
    row, it does not classify a unit), as is `"sparkdust"` as an icon name.

    Known limit: a test hidden inside a helper call (`re.search("spark", x)`)
    is not a Compare and would slip past. This catches the shape every one of
    these files actually used.
    """
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        left = node.left
        if isinstance(left, ast.Constant) and isinstance(left.value, str) \
                and "spark" in left.value.lower():
            out.append(f"line {node.lineno}: {left.value!r} in …")
    return out


def test_every_reader_asks_the_shared_predicate():
    for rel in READERS:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "udata.is_spark_variant" in src or "udata.drops_spark" in src, (
            f"{rel} no longer consults units.is_spark_variant / units.drops_spark")
        offenders = _private_spelling_tests(src)
        assert not offenders, (
            f"{rel} re-spells the Spark rule instead of asking the shared "
            f"predicate: {offenders}")


#: The readers talking about a LIVE entity you could farm — they must ask the
#: dust question, never the variant one, or a catchable companion would start
#: showing up in the Spark Dust toggles.
DUST_READERS = (
    "farever_companion/ui/overlays/entity_gather.py",
    "farever_companion/ui/overlays/minimap.py",
    "farever_companion/ui/tracker.py",
)


def test_the_live_readers_ask_the_dust_question_not_the_variant_one():
    for rel in DUST_READERS:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "udata.drops_spark(" in src, (
            f"{rel} stopped asking the shared dust predicate")
        assert "is_spark_variant" not in src, (
            f"{rel} asks the VARIANT question about a live entity — the Spark "
            "Dust toggles must stay on the dust predicate, or a sparkling "
            "companion would appear as dust to farm (see units.drops_spark)")


def test_a_spark_critter_is_a_variant_and_never_a_dust_source():
    """The two questions answer differently on purpose, and this is the case the
    distinction exists for: a catchable sparkling companion."""
    critters = [u for u, row in units._units_by_id().items()
                if row.get("isCritter") and units.is_spark_variant(u)]
    assert critters, "no critter is recognised as a Spark variant — did the "\
                     "name/id spelling rule break?"
    for uid in critters:
        assert not units.drops_spark(uid), (
            f"{uid} is a Spark variant but also claims to drop Spark Dust — a "
            "critter is caught, not killed (see units.drops_spark)")
    # the named case, pinned so the docstring above has a witness
    assert units.is_spark_variant("Rabbit_Spark")
    assert not units.drops_spark("Rabbit_Spark")


def test_the_variant_predicate_recognises_every_spelling_the_game_uses():
    assert units.is_spark_variant("Rabbit_Spark")          # `_Spark` companion id
    assert units.is_spark_variant("R1KoboldBoss_Sparkling")  # id only — its
    #   display name ("Snack of Ratsar") contains no spark token at all, which is
    #   the asymmetry the old per-widget tests disagreed about
    assert units.is_spark_variant("Elemental_Z1W_Earth")   # `<creature> Sparkle`
    assert not units.is_spark_variant("Slime_Z1W")
    assert not units.is_spark_variant(None) and not units.is_spark_variant("")


def test_every_dust_source_is_also_a_variant():
    for uid in units.spark_unit_ids():
        assert units.is_spark_variant(uid), (
            f"{uid} drops Spark Dust but is not recognised as a Spark unit")


def test_no_codex_row_contradicts_the_dust_predicate():
    """The grid filter and the card badge render shim rows, and the shim does NOT
    carry `drops_spark` everywhere: the compiler's Bosses loop never sets it, so a
    dungeon mob's Bosses row has no key at all while a zone row for the same unit
    does. Reading the raw key therefore read a MISSING key as "not Spark" — which
    is exactly why both readers now ask the predicate. Pinned here: no row may
    contradict the predicate, and the predicate must cover ids whose rows carry no
    key (the loss the raw read had).
    """
    from farever_companion.data import raw_codex

    rows = [e for region in raw_codex.DATA.values()
            if isinstance(region, list)
            for e in region if isinstance(e, dict) and e.get("id")]
    assert rows, "no codex rows to check"

    flagged = {e["id"] for e in rows if e.get("drops_spark")}
    assert flagged, "the shim flags no Spark unit at all"
    assert flagged <= units.spark_unit_ids()
    for e in rows:
        if "drops_spark" in e:
            assert bool(e["drops_spark"]) == units.drops_spark(e["id"]), (
                f"codex row {e['id']} carries drops_spark={e.get('drops_spark')} "
                "but units.drops_spark says otherwise")

    # the loss, measured: rows that carry NO key whose id the predicate still
    # calls a dust source. Reading `row["drops_spark"]` drops every one of them
    # (the Bosses-tab copies of a dungeon mob among them), which is the bug the
    # two readers were moved onto the predicate for.
    keyless_but_spark = sorted({e["id"] for e in rows
                                if "drops_spark" not in e
                                and units.drops_spark(e["id"])})
    assert keyless_but_spark, (
        "every Spark unit's rows carry the key now — if the shim stopped "
        "omitting it, the raw-key read would be safe again, so revisit this")


def test_no_critter_is_baked_as_a_dust_source():
    """The build's exclusion, pinned where it lands: no companion row in the
    shipped codex may claim Spark Dust."""
    from farever_companion.data import raw_codex

    offenders = [e["id"] for region in raw_codex.DATA.values()
                 if isinstance(region, list)
                 for e in region
                 if isinstance(e, dict) and e.get("drops_spark")
                 and e.get("is_critter")]
    assert not offenders, (
        f"companion row(s) baked as Spark Dust sources: {offenders} — critters "
        "are caught, not killed")
