"""Audit the bundled sheets for rows that are still carrying their own id as a
name — run this after a data scan to see what the sheets never filled in.

    .venv\\Scripts\\python.exe build_tools\\check_unnamed_items.py
    .venv\\Scripts\\python.exe build_tools\\check_unnamed_items.py --all
    .venv\\Scripts\\python.exe build_tools\\check_unnamed_items.py --fail-on-gear

A row whose name IS its id (or is blank) is the test-server sheet saying "no
name yet": the UI prints that id verbatim rather than inventing one, so these
are the visible gaps — 'Back_RBee_Wiz' where a real name should be. Most of
them are recipes, materials and internal scan artifacts the Items list hides on
purpose (see `support._listable`); the GEAR ones are the rows a player can
actually run into.

The two cases are not the same, so they are reported apart:

  * an id with a separator, a digit or a camelCase run ('Recipe_SacrificePotion',
    'ActivityLoot', 'LP_Z1_Bee') is an internal id — a placeholder name;
  * a plain word ('Gold', 'Agate', 'Wheat') is how a material legitimately
    reads, so those are only counted unless --all asks for them.

Reports only: exits 0 either way, so it is safe to call from a build step.
--fail-on-gear exits 1 when a piece of gear is unnamed, for whoever wants to
gate on it.
"""
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
# importable both as a script and from the repo root (pytest), same as
# verify_assets.py
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
RESET = "\033[0m"

SHOWN_PER_TYPE = 8

# a separator, a digit or a camelCase run: something no displayed name would
# hold, so the sheet never filled the name column in
_INTERNAL = re.compile(r"[_0-9]|[a-z][A-Z]")


def looks_internal(item_id: str) -> bool:
    """True when an id reads like an internal row name rather than a name."""
    return bool(_INTERNAL.search(item_id or ""))


def collect() -> dict[str, list[str]]:
    """{item type: [ids]} for every row the sheets left unnamed, gear first.

    Reads the same compiled payload the app reads, so what it lists is exactly
    what a player would see printed as an id.
    """
    from farever_companion.data import items as idata
    from farever_companion.data.items import catalog

    raw = catalog._data().get("items", {})
    gear: dict[str, list[str]] = {}
    rest: dict[str, list[str]] = {}
    for iid, row in raw.items():
        name = (row.get("name") or "").strip()
        if name and name != iid:
            continue                       # named properly
        bucket = gear if idata.is_gear(row) else rest
        bucket.setdefault(row.get("type") or "(no type)", []).append(iid)
    for group in (gear, rest):
        for ids in group.values():
            ids.sort()
    return {**dict(sorted(gear.items())), **dict(sorted(rest.items()))}


def gear_ids(groups: dict[str, list[str]]) -> list[str]:
    """The unnamed rows that are actual gear (the actionable ones)."""
    from farever_companion.data import items as idata

    return [iid for ids in groups.values() for iid in ids
            if idata.is_gear(idata.item(iid) or {})]


def _split(groups: dict[str, list[str]],
           keep) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for typ, ids in groups.items():
        kept = [i for i in ids if keep(i)]
        if kept:
            out[typ] = kept
    return out


def report(groups: dict[str, list[str]], show_all: bool = False) -> str:
    """The audit as text: placeholder ids by type, then a word-id tally."""
    from farever_companion import paths

    gear = set(gear_ids(groups))
    placeholders = _split(groups, looks_internal)
    words = _split(groups, lambda i: not looks_internal(i))
    n_ph = sum(len(v) for v in placeholders.values())
    n_words = sum(len(v) for v in words.values())
    out = [f"\n--- {YELLOW}Unnamed rows in the bundled sheets{RESET} ---",
           f"    source: {paths.item_drops_path().name}",
           f"    {n_ph + n_words} row(s) carry their own id (or a blank) as a"
           f" name — {len(gear)} of them gear.",
           f"    {DIM}The UI prints those ids verbatim, so they are the gaps a"
           f" re-scan should fill.{RESET}"]

    if not n_ph:
        out.append(f"  {GREEN}No placeholder id is left in a name column."
                   f"{RESET}")
    else:
        out.append(f"\n  {YELLOW}Placeholder ids{RESET} — {n_ph} row(s), the"
                   f" ones a re-scan should fill:")
        for typ, ids in placeholders.items():
            is_gear = any(i in gear for i in ids)
            mark = f"  {RED}(gear — shows in the Items pages){RESET}" if is_gear \
                else ""
            out.append(f"  {RED if is_gear else DIM}{typ}{RESET} — {len(ids)}"
                       f" row(s){mark}")
            shown = ids if show_all else ids[:SHOWN_PER_TYPE]
            for iid in shown:
                out.append(f"      {'*' if iid in gear else ' '} {iid}")
            if len(ids) > len(shown):
                out.append(f"      {DIM}… and {len(ids) - len(shown)} more"
                           f" (--all){RESET}")

    if n_words:
        out.append(f"\n  {DIM}Single-word ids — {n_words} row(s). A material"
                   f" called 'Gold' or 'Agate' reads like that on purpose, so"
                   f" these are counted, not flagged"
                   f"{'' if not show_all else ':'}{RESET}")
        if show_all:
            for typ, ids in words.items():
                out.append(f"  {DIM}{typ}{RESET} — {len(ids)}: {' '.join(ids)}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    groups = collect()
    print(report(groups, show_all="--all" in args))
    unnamed_gear = gear_ids(groups)
    if unnamed_gear and "--fail-on-gear" in args:
        print(f"\n{RED}[!] {len(unnamed_gear)} gear row(s) have no name in the"
              f" sheets.{RESET}")
        return 1
    if unnamed_gear:
        print(f"\n{YELLOW}[i] {len(unnamed_gear)} gear row(s) unnamed:"
              f" {' '.join(unnamed_gear)}{RESET}"
              f"  (--fail-on-gear to make that an error)")
    else:
        print(f"\n{GREEN}[+] No gear row is missing a name.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
