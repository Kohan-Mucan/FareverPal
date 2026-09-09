"""Derive the proxy DLLs' export lists from the installed game.

A drop-in proxy must export **every** name the game imports from it: Windows
fails the whole import on the first missing one and the module that asked for
it dies with the game ("Primitive or library is missing" at
``fmod.$Api.set_DEBUG_FLAGS``, because ``fmod.dll`` imports WINMM.timeGetTime
and the proxy exported only the two timer functions libhl needs).

Hand-maintaining that list means every game patch that starts importing one
more symbol is a crash waiting for a rebuild, so the lists are derived from the
game's own import tables at build time instead. This module holds the pure
part - what to export, which names still need a generated forwarder, and how
the ``.def`` / forwarder text reads - so the build script stays a thin
file-writing shell and the logic stays testable without a game installed.

Rules that keep regeneration safe:

* The generated list is the game's imports **unioned with what is already
  exported**, so a regeneration can only add names, never drop one that some
  other consumer calls.
* Names the C sources already implement keep their hand-written, precisely
  typed forwarders; only the remainder get generic ones.
* Imported names are written down with the game's own casing: PE export lookup
  is case-sensitive, so a generated ``timegettime`` would not satisfy an import
  of ``timeGetTime`` - the same crash, produced by our own build.
* Those generic forwarders are defined under an internal name and exported
  through a ``.def`` alias, because the SDK headers already declare prototypes
  for many of these names and a loose 12-slot signature cannot redeclare them.
"""
from __future__ import annotations

import os
import re

from . import pe_imports

#: The drop-in names the BUILD still produces, in the order the app ranks them
#: when all are shipped. winmm.dll was retired 2026-09-23 (the only t=0 name
#: this game loads, and it killed the game there) and iphlpapi.dll the same day
#: (userenv.dll is the verified uv.hdll alt; one alt is enough).
#: version.dll was PARKED here on 2026-09-24 (kept building, not shipped) and
#: fully retired on 2026-09-29: 17 exports serving importers that only load
#: unreliably (SDL3 / DLSS / Streamline) was upkeep with no payoff, so the
#: build no longer regenerates its .def or its forwarders. version.def is left
#: on disk as a last-resort backup ONLY, and it goes stale the moment the game
#: patches its import table - so putting this name back is not sufficient on
#: its own: gen_proxy_exports.py's TARGETS entry and REAL_PROC_FN below have
#: to come back with it.
PROXY_NAMES: tuple[str, ...] = ("dinput8.dll", "userenv.dll")

#: Function name the generated forwarders call to reach the real system DLL.
#: Each proxy source defines its own. The keys mirror PROXY_NAMES exactly -
#: a name here with no entry there emits forwarders calling a function no
#: proxy defines, which does not compile.
REAL_PROC_FN: dict[str, str] = {
    "dinput8.dll": "real_dinput8_proc",
    "userenv.dll": "real_userenv_proc",
}

#: Value to return when the real system DLL could not be loaded. MMRESULT APIs
#: answer MMSYSERR_NODRIVER (6); everything else here returns 0, which is the
#: "failed" value for BOOL/HRESULT/DWORD-returning APIs. Returning 0 for an
#: MMRESULT API would claim success and hand the caller an uninitialised
#: handle, so those names are pinned.
FAILURE_VALUES: dict[str, int] = {
    "timeBeginPeriod": 6, "timeEndPeriod": 6,
    "waveOutGetDevCapsW": 6, "waveOutOpen": 6, "waveOutClose": 6,
    "waveOutPrepareHeader": 6, "waveOutUnprepareHeader": 6, "waveOutWrite": 6,
    "waveInGetDevCapsW": 6, "waveInOpen": 6, "waveInClose": 6,
    "waveInPrepareHeader": 6, "waveInUnprepareHeader": 6, "waveInAddBuffer": 6,
    "waveInStart": 6, "waveInStop": 6, "waveInReset": 6,
}

_DEF_NAME_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_@?$]*)\s*(?:=|PRIVATE|DATA|NONAME|CONSTANT)?\s*$",
                          re.IGNORECASE)
_DEF_PRIVATE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_@?$]*)\s+PRIVATE\s*$", re.IGNORECASE)

#: How the C sources define an exported function today.
_DECLSPEC_RE = re.compile(
    r"__declspec\s*\(\s*dllexport\s*\)[^;{]*?\bWINAPI\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
#: `FWD<n>(ret, fail, name, ...)`-style macros (winmm's `WINMM_FWD<n>` was the
#: first; dinput8 uses the same shape) - the exported name is always the third
#: argument. It must be matched positionally: a greedy "up to the last comma"
#: match swallows everything and reports the last *type* (mmarg, void *) as the
#: exported name, which silently hides every wave* function.
_WINMM_FWD_RE = re.compile(r"\bWINMM_FWD\d?\s*\(\s*[^,()]+,\s*[^,()]+,\s*"
                           r"([A-Za-z_][A-Za-z0-9_]*)")
_BIND_VERSION_RE = re.compile(r"\bBIND_VERSION\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")
#: Marker each generated forwarder carries (a no-op macro). Generated headers
#: are never fed to defined_exports, so regeneration stays idempotent; the
#: marker is there to be greppable and to make redefinition errors obvious.
GENERATED_MARKER = "PROXY_GENERATED_FORWARDER"

#: Prefix for the *internal* name a generated forwarder is defined under.
#: The public name is attached to it by an alias in the .def
#: (``timeGetTime = farevermod_fwd_timeGetTime``), which is what keeps a
#: generic, deliberately loosely-typed forwarder from colliding with the SDK's
#: own prototype for that name - ``mmsystem.h`` declares
#: ``timeGetSystemTime(LPMMTIME, UINT)``, and redefining it with twelve
#: pointer-sized slots is a compile error in the proxy source.
GENERATED_IMPL_PREFIX = "farevermod_fwd_"


def generated_impl_name(public_name: str) -> str:
    """Internal C name a generated forwarder for `public_name` is defined as."""
    return GENERATED_IMPL_PREFIX + public_name


#: Case-insensitive view of FAILURE_VALUES: imported names now keep the game's
#: own spelling, which may differ from the dictionary's.
_FAILURE_BY_LOWER: dict[str, int] = {k.lower(): v for k, v in FAILURE_VALUES.items()}


def failure_value(public_name: str) -> int:
    """What a generated forwarder returns when the real DLL did not load."""
    return _FAILURE_BY_LOWER.get((public_name or "").lower(), 0)


def imported_function_names(game_dir: str, proxy_name: str) -> set[str]:
    """Every function the game imports from `proxy_name` (ordinals skipped).

    Names keep the case the game imports them with: PE export lookup is
    case-sensitive, so writing ``timegettime`` into the .def would leave
    ``fmod.dll``'s import of ``timeGetTime`` unsatisfied - a missing export and
the crash this module exists to prevent.

    Ordinal imports cannot be expressed by name, and guessing one would be
    worse than leaving it out - the same rule `proxy_export_gaps` uses.
    """
    wanted = (proxy_name or "").lower()
    names: set[str] = set()
    for path in pe_imports.game_modules(game_dir):
        try:
            per_dll = pe_imports.pe_import_functions(path, preserve_case=True)
        except Exception:
            continue
        for dll, funcs in per_dll.items():
            if dll.lower() != wanted:
                continue
            names.update(f for f in funcs if not f.startswith("#"))
    return names


def importer_notes(game_dir: str, proxy_name: str) -> dict[str, list[str]]:
    """{function name: [modules importing it]} - for the .def's comments.

    Keyed by the game's own spelling, so the note sits against the exact .def
    line it explains.
    """
    wanted = (proxy_name or "").lower()
    notes: dict[str, list[str]] = {}
    for path in pe_imports.game_modules(game_dir):
        base = os.path.basename(path).lower()
        try:
            per_dll = pe_imports.pe_import_functions(path, preserve_case=True)
        except Exception:
            continue
        for dll, funcs in per_dll.items():
            if dll.lower() != wanted:
                continue
            for fn in funcs:
                if not fn.startswith("#"):
                    notes.setdefault(fn, [])
                    if base not in notes[fn]:
                        notes[fn].append(base)
    return {fn: sorted(mods) for fn, mods in notes.items()}


def parse_def(text: str) -> tuple[list[str], set[str]]:
    """(names, private names) from an existing .def, order preserved.

    ``PRIVATE`` only affects the import library, not the export table, so it is
    carried across a regeneration rather than dropped.
    """
    names: list[str] = []
    private: set[str] = set()
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.upper().startswith("LIBRARY") or line.upper().startswith("EXPORTS"):
            continue
        m = _DEF_PRIVATE_RE.match(line)
        if m:
            names.append(m.group(1))
            private.add(m.group(1))
            continue
        m = _DEF_NAME_RE.match(line)
        if m and not line.startswith(";"):
            names.append(m.group(1))
    return names, private


def merge_export_names(existing: list[str], imported: set[str]) -> list[str]:
    """Exports to write: what the game imports, plus anything already exported.

    Union, never replacement: a name some other consumer calls must not
    disappear because this game build happens not to use it. Case-insensitive,
    preserving the spelling already on disk.
    """
    out: list[str] = []
    seen: set[str] = set()
    for name in existing:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    for name in sorted(imported, key=str.lower):
        if name.startswith("#"):        # "#12" is an ordinal, not a name
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def defined_exports(*source_paths: str) -> set[str]:
    """Names the C sources already implement (hand-written forwarders)."""
    defined: set[str] = set()
    for path in source_paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        defined.update(_DECLSPEC_RE.findall(text))
        # The macros' own #define lines take (ret, fail, name, ...) too, so they
        # would report "name" as an export - skip them.
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            defined.update(_WINMM_FWD_RE.findall(line))
            defined.update(_BIND_VERSION_RE.findall(line))
    return defined


def missing_forwarders(names: list[str], defined: set[str]) -> list[str]:
    """Exported names with no implementation in the sources, i.e. the ones a
    game patch added - each one would otherwise fail the import at runtime."""
    have = {n.lower() for n in defined}
    return [n for n in names if n.lower() not in have]


def render_def(proxy_name: str, names: list[str], *, build_id: str = "",
               private: set[str] | None = None, notes: dict[str, list[str]] | None = None,
               imported: set[str] | None = None,
               generated: set[str] | None = None) -> str:
    """Text of a generated .def for `proxy_name`.

    `generated` names the exports with no implementation in the sources. Those
    are written as aliases (``name = farevermod_fwd_name``) pointing at the
    generated forwarder, which is defined under that internal name so its loose
    signature cannot clash with the SDK's prototype for the public one - and
    which also means the .def is what decides the exported name. A listed name
    with no implementation fails the link loudly rather than shipping a DLL
    that cannot satisfy the import.
    """
    stem = os.path.splitext(proxy_name)[0]
    private = private or set()
    notes = notes or {}
    # `None` means "assume the names came from the game", which is what earlier
    # callers meant. An explicitly empty set is a real answer: nothing is
    # imported, so every line is a kept export.
    imported = set(names) if imported is None else imported
    # Notes are keyed by the game's spelling while `names` may keep a
    # differently-cased entry from the .def already on disk.
    note_map = {k.lower(): v for k, v in notes.items()}
    gen_lower = {n.lower() for n in (generated or ())}
    out = [
        "; Generated by hooks/gen_proxy_exports.py from the installed game's",
        "; import tables - do not hand-edit. Rebuild (build_all.bat) to refresh.",
    ]
    if build_id:
        out.append(f"; Source build: {build_id}")
    out.append(f"; {len(names)} export(s); {len(imported)} imported by the game.")
    out.extend([f"LIBRARY {stem}", "EXPORTS"])
    for name in names:
        mods = note_map.get(name.lower())
        if mods:
            out.append(f"    ; {name} - needed by {', '.join(mods)}")
        elif name.lower() in gen_lower:
            out.append(f"    ; {name} - generated forwarder (no stub in the source)")
        elif name.lower() not in {n.lower() for n in imported}:
            out.append(f"    ; {name} - kept from the previous export list")
        entry = (f"{name} = {generated_impl_name(name)}"
                 if name.lower() in gen_lower else name)
        out.append(f"    {entry}" + (" PRIVATE" if name in private else ""))
    return "\n".join(out) + "\n"


def render_forwarders(proxy_name: str, names: list[str]) -> str:
    """Generated C forwarders for `names` (empty string when none are needed).

    Each one is deliberately generic: every argument and the return value are
    pointer-sized slots, which is ABI-safe on x64 - the real callee reads the
    registers it needs, and widening an argument never corrupts it while
    narrowing a handle would. WINMM/Version/DirectInput exports are all
    integer/pointer APIs, so no XMM argument is involved. The precise,
    hand-written forwarders stay in the sources; these exist so a symbol added
    by a game patch is forwarded instead of failing the import.

    ``PROXY_GENERATED_FORWARDER(name)`` marks each definition so the generator
    recognises its own output on the next run.
    """
    banner = ["/* Generated file - do not edit. See hooks/gen_proxy_exports.py. */"]
    if not names:
        return "\n".join(banner + [
            "/* No missing exports: every symbol this game imports from this DLL",
            " * already has a hand-written forwarder in the source. */",
        ]) + "\n"
    real_proc = REAL_PROC_FN.get(proxy_name, "")
    if not real_proc:
        raise ValueError(f"no real-proc resolver registered for {proxy_name}")
    lo = ", ".join(f"uintptr_t a{i}" for i in range(1, 13))
    args = ", ".join(f"a{i}" for i in range(1, 13))
    fn_args = ", ".join(["uintptr_t"] * 12)
    out = banner + [
        "#ifndef PROXY_GENERATED_FORWARDER",
        f"#  define {GENERATED_MARKER}(name)",
        "#endif",
        "",
        "/* Generated by hooks/gen_proxy_exports.py - do not hand-edit.",
        f" * {len(names)} symbol(s) the game imports that the sources do not",
        " * implement yet. Each forwards to the real system DLL through",
        f" * {real_proc}(), so a game patch that adds an import is forwarded",
        " * instead of failing the import and taking the game down with it.",
        " *",
        " * Defined under an internal name on purpose: the public name is",
        " * attached to it by an alias in the .def, so this loose signature",
        " * cannot collide with the SDK's own prototype for the same name. */",
        "",
    ]
    for name in names:
        impl = generated_impl_name(name)
        fail = failure_value(name)
        out += [
            f"{GENERATED_MARKER}({name})",
            f"static uintptr_t p_{impl} = 0;",
            f"uintptr_t WINAPI {impl}({lo}) {{",
            f"    if (!p_{impl}) p_{impl} = (uintptr_t){real_proc}(\"{name}\");",
            f"    if (p_{impl}) {{",
            f"        typedef uintptr_t (WINAPI *fn_t)({fn_args});",
            f"        return ((fn_t)p_{impl})({args});",
            "    }",
            f"    return (uintptr_t){fail};",
            "}",
            "",
        ]
    return "\n".join(out)
