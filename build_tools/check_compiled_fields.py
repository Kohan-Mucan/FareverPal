"""Build check: a compiled shim must not have dropped a field the data layer
reads. Exits 1 listing every violation.

    python build_tools/check_compiled_fields.py            # the build's mode
    python build_tools/check_compiled_fields.py --verbose  # + the measurement
    python build_tools/check_compiled_fields.py --json     # machine-readable

Importable too: `from build_tools.check_compiled_fields import run` ->
(count, violations). `build_tools/verify_assets.py` calls it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from build_tools import compiled_fields  # noqa: E402

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def run() -> tuple[int, list]:
    """(number of violations, the violation rows). Never raises: a shim the
    check cannot import is reported as a violation of its own kind."""
    try:
        result = compiled_fields.sweep()
    except Exception as exc:  # noqa: BLE001 — a broken shim must fail the build
        return 1, [("<sweep>", type(exc).__name__, str(exc), 0, "?", False)]
    return len(result["violations"]), result["violations"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true",
                    help="also print the payload field sets and excused reads")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        result = compiled_fields.sweep()
    except Exception as exc:  # noqa: BLE001
        print(f"{RED}[!] compiled-field check could not run: "
              f"{type(exc).__name__}: {exc}{RESET}")
        return 1

    viol, excused = result["violations"], result["excused"]
    notes = result["notes"]

    if args.json:
        print(json.dumps({
            "violations": [{"shim": s, "payload": p, "field": f, "reads": n,
                            "caller": c, "in_source": src}
                           for s, p, f, n, c, src in viol],
            "notes": [{"shim": s, "payload": p, "field": f, "reads": n,
                       "caller": c} for s, p, f, n, c, _ in notes],
            "excused": [{"shim": s, "payload": p, "field": f, "reads": n,
                         "caller": c, "reason": compiled_fields.EXCEPTIONS[(s, p, f)]}
                        for s, p, f, n, c, _ in excused],
        }, indent=2))
        return 1 if viol else 0

    print(f"\n--- {YELLOW}Compiled Field Check{RESET} ---")
    if args.verbose:
        for shim, payloads in sorted(result["before"].items()):
            for key, fields in sorted(payloads.items()):
                print(f"  {shim}.{key}: {len(fields)} fields "
                      f"-> {sorted(fields) if len(fields) < 12 else '(many)'}")
        for shim, payload, field, reads, caller, in_source in excused:
            print(f"  {YELLOW}excused{RESET} {shim}.{payload}['{field}'] "
                  f"— {compiled_fields.EXCEPTIONS[(shim, payload, field)]}")
        for shim, payload, field, reads, caller, _ in notes:
            print(f"  {YELLOW}note{RESET} {shim}.{payload}['{field}'] ({reads}x) "
                  f"<- {caller}  [no compile input names it either: the reader "
                  f"falls back by design]")

    if not viol:
        print(f"Compiled fields: {GREEN}OK{RESET} (no field the data layer reads "
              f"was dropped in favour of the source sheet; "
              f"{len(excused)} documented exception(s), {len(notes)} soft note(s))")
        return 0

    print(f"Compiled fields: {RED}{len(viol)} FIELD(S) THE COMPILER DROPS THAT "
          f"THE DATA LAYER READS{RESET}")
    for shim, payload, field, reads, caller, in_source in viol:
        print(f"  {RED}*{RESET} {shim}.{payload}['{field}'] ({reads} read(s)) "
              f"<- {caller}\n      the compile input has it — keep it in compiler.py's "
              f"`sheets` field list (or re-read it from the source sheet, the "
              f"way item `props` is)")
    print(f"\n{RED}[!] A dev checkout hides this behind cdb.sheet()'s loose-JSON "
          f"fallback; the frozen build has only the shims. Add the field to "
          f"compiler.py's `sheets` table, or add it to "
          f"compiled_fields.EXCEPTIONS with a reason.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
