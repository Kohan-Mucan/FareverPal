"""Crash-safe persistence helpers shared by every data writer.

All user data is written through `atomic_write_json` — settings.json,
collection.json, every progress_<profile>.json, and the Settings fields they
carry (dps bests, server pings, geometry, hidden lists, ...) — so a crash
mid-save can never leave a truncated file. Files that fail to parse on load
are preserved as *.bak by `backup_corrupt` instead of being silently
clobbered, and `find_backup_files` / `restore_backup_file` power the startup
recovery scan.

Pure stdlib (no Qt, no Settings import) so any layer — config, UI, workers —
can import the same path without pulling in the rest of the app.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def _compact_simple_lists(text: str) -> str:
    """Collapse containers whose members are all scalars (no nested []/{}) onto
    a single line — e.g. `"rift_ding": [1, 5, 10, 15]` or
    `"server_pings": ["na", "na_test", "login_global"]`. Improves readability of
    short lists without touching structured objects. Safe no-op if none.
    """
    import re
    list_pat = re.compile(r"\[\s*((?:[^\n\[\]{}]+\n)+?)\s*\]", re.MULTILINE)
    obj_pat = re.compile(r"\{\s*((?:\s*\"[^\"\n]*\"\s*:\s*[^\n\[\]{}]+\n)+?)\s*\}", re.MULTILINE)

    def list_repl(m: "re.Match") -> str:
        items = [ln.strip().rstrip(",") for ln in m.group(1).splitlines() if ln.strip()]
        return "[" + ", ".join(items) + "]"

    def obj_repl(m: "re.Match") -> str:
        items = [ln.strip().rstrip(",") for ln in m.group(1).splitlines() if ln.strip()]
        return "{" + ", ".join(items) + "}"

    out = text
    for _ in range(5):  # a few passes to catch nested-in-a-container cases
        out, n1 = list_pat.subn(list_repl, out)
        out, n2 = obj_pat.subn(obj_repl, out)
        if n1 == 0 and n2 == 0:
            break
    return out


def atomic_write_json(path: Path, data: dict, compact_lists: bool = False) -> None:
    """Write JSON `data` to `path` atomically: serialize to a temp file in
    the same directory, then os.replace() over the target.

    A crash mid-write can never leave a truncated/corrupt file — which
    previously fell back to defaults on the next launch and was then
    overwritten, silently wiping the user's settings. Raises OSError on
    failure; callers decide whether to swallow it.
    """
    text = json.dumps(data, indent=1)
    if compact_lists:
        text = _compact_simple_lists(text)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def backup_corrupt(path: Path) -> None:
    """If `path` exists but failed to parse, move it aside as *.bak so the
    next save() can't clobber the only copy of the user's data. Returns
    nothing; a failed backup is swallowed (the caller still falls back to
    defaults).
    """
    try:
        if path.exists():
            path.replace(path.with_suffix(path.suffix + ".bak"))
    except OSError:
        pass


def find_backup_files(directory: Path) -> list[Path]:
    """Every *.bak file preserved in `directory` — settings.json.bak,
    collection.json.bak, progress_*.json.bak — oldest first. Empty when none
    were preserved (fresh install or nothing ever corrupted).
    """
    try:
        return sorted(directory.glob("*.bak"), key=lambda p: (p.stat().st_mtime, p.name))
    except OSError:
        return []


def restore_backup_file(bak: Path) -> Path | None:
    """Copy a preserved *.bak back over its original file (strip the trailing
    .bak: settings.json.bak -> settings.json). Returns the restored path, or
    None on failure. The .bak is deleted on success so the startup scan stops
    listing it; its content now lives in the restored file.
    """
    if bak.suffix != ".bak":
        return None
    target = bak.with_suffix("")
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(bak.read_bytes())
        os.replace(tmp, target)
        bak.unlink(missing_ok=True)
        return target
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def preserve_file_aside(target: Path, dest_dir: Path) -> Path | None:
    """Move `target` aside to `dest_dir` under a timestamped name (e.g.
    settings.json.20260810-015612.bak) before a restore overwrites it, so
    restoring never destroys the current version of the user's data.

    Only the NEWEST preserved copy of each file is kept — older ones are
    pruned automatically, so cache/restore never grows beyond one copy per
    file. Returns the preserved path, or None when there is nothing to
    preserve (target missing) or the move fails. On success the original path
    no longer exists — the caller then restores the *.bak over it.
    """
    if not target.exists() or not target.is_file():
        return None
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{target.name}.{ts}.bak"
    n = 2
    while dest.exists():
        dest = dest_dir / f"{target.name}.{ts}-{n}.bak"
        n += 1
    try:
        os.replace(target, dest)
    except OSError:
        return None
    _prune_preserved(target.name, dest_dir, keep=dest)
    return dest


def _prune_preserved(name: str, dest_dir: Path, keep: Path) -> None:
    """Delete every preserved copy of `name` (e.g. settings.json.*.bak) in
    `dest_dir` except the one just created (`keep`), so the folder never grows
    beyond one copy per file — and that copy is always the latest restore.

    Deterministic: no mtime/name ordering (timestamps can tie at coarse
    filesystem granularity). Best-effort: a file that can't be removed is left
    in place. The corrupt-file *.bak itself (settings.json.bak) is never
    matched by this pattern.
    """
    for p in dest_dir.glob(f"{name}.*.bak"):
        if p == keep:
            continue
        try:
            p.unlink()
        except OSError:
            pass


def delete_backup_file(bak: Path) -> bool:
    """Permanently delete a preserved *.bak. Returns True on success, False
    when the file is missing or can't be removed. Only the *.bak is touched —
    the live file it was backed up from is never modified.

    This is destructive: the .bak is often the ONLY remaining copy of the
    user's settings/profile data (the original failed to parse), so callers
    should confirm with the user first.
    """
    if bak.suffix != ".bak":
        return False
    try:
        bak.unlink()
        return True
    except OSError:
        return False
