"""The Past Fights panel is lazy: it lists log FILES built from their names and
reads one only when a row is viewed (or its trash action is used), so opening a
season of logs never parses every file.

This is a UI contract, so it lives here rather than beside the tracker tests:
`scan_history_dir` must stay read-free, and the panel must not touch a log until
something the player did asks for it. The write side — deleting one fight or a
batch — is covered here too.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _fight(kind="boss", name="King Ratsar", start=1000.0,
           target="👑 King Ratsar", damage=500.0) -> dict:
    return {
        "name": name, "kind": kind, "boss_label": name,
        "start_time": start, "end_time": start + 60.0, "state": "PAUSED",
        "target_name": target,
        "players": [{"name": "Kartik", "is_me": True,
                     "hero_class": "Warrior", "total_damage": damage}],
        "timeline": {}, "deaths": [], "burst_peak": 0.0,
        "burst_peak_time": 0.0,
    }


def _write_log(folder: Path, filename: str, fights) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    p.write_text(json.dumps({"history": list(fights)}), encoding="utf-8")
    return p


def _log_dir(base: Path, t: float | None = None) -> Path:
    lt = time.localtime(t or time.time())
    return base / time.strftime("%Y-%m", lt) / time.strftime("%d", lt)


def _spy_reads(monkeypatch) -> list[Path]:
    """Record every history-file read the index performs."""
    import farever_companion.ui.combat.history_index as hi

    reads: list[Path] = []
    real = hi.read_history_file

    def spy(path):
        reads.append(Path(path))
        return real(path)

    monkeypatch.setattr(hi, "read_history_file", spy)
    return reads


def test_scan_history_dir_reads_no_log_until_a_row_is_opened(tmp_path, monkeypatch):
    """Listing a folder is a readdir and a stat per file — never a parse."""
    import farever_companion.ui.combat.history_index as hi

    day = _log_dir(tmp_path)
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("boss"), _fight("boss", start=2000.0)])
    _write_log(day, "trash_Kartik_Warrior_a1b2.json", [_fight("trash")])
    reads = _spy_reads(monkeypatch)

    rows = hi.scan_history_dir(tmp_path)
    assert reads == []                      # the scan opened nothing
    assert len(rows) == 2

    by_name = {r["path"].name: r for r in rows}
    boss_row = by_name["King Ratsar_Kartik_Warrior_a1b2.json"]
    trash_row = by_name["trash_Kartik_Warrior_a1b2.json"]
    # the label is read off the NAME, not the contents
    assert boss_row["kind"] == "boss"
    assert boss_row["boss"] == "King Ratsar"
    assert boss_row["char"] == "Kartik Warrior A1b2"
    assert trash_row["kind"] == "trash"

    # asking one row for its fights reads exactly that one file, once
    assert len(boss_row["sessions"]) == 2
    assert reads == [boss_row["path"]]
    _ = list(boss_row["sessions"])
    assert reads == [boss_row["path"]]      # cached, not re-read

    # a different row reads a different file, again only on demand
    assert len(trash_row["sessions"]) == 1
    assert reads == [boss_row["path"], trash_row["path"]]


def test_load_history_sessions_is_the_explicit_on_demand_read(tmp_path):
    import farever_companion.ui.combat.history_index as hi

    day = _log_dir(tmp_path)
    p = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                   [_fight("boss")])
    sessions = hi.load_history_sessions(p)
    assert len(sessions) == 1 and sessions[0].kind == "boss"
    assert hi.load_history_sessions(tmp_path / "missing.json") == []


# --- the Combat page's Fights list: DATES -> LOGS -> FIGHTS -----------------

def _fights_host(tmp_path, monkeypatch):
    """A minimal host carrying only what the list mixin needs.

    The mixin reads `cp_content_lay` / `cp_empty_lbl` (built by
    `_build_past_fights_view` on the real page) and calls back into the page
    for loading, so the double supplies those two widgets and records the
    callbacks instead of running them.
    """
    from PySide6 import QtWidgets
    import farever_companion.ui.pages.combat_fights_list as fl

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(fl, "dps_dir", lambda: tmp_path)

    class _Host(fl.CombatFightsListMixin):
        def __init__(self):
            # The REAL view builder, so every test here runs against the same
            # widget tree the app draws — including the frozen toolbar slot.
            from farever_companion.ui.pages.combat_history import (
                CombatHistoryMixin)
            CombatHistoryMixin._build_past_fights_view(self)
            self._cp_fights_filter = "all"
            self.loaded: list = []
            self.deleted: list = []

        def _load_past_fight(self, char, sess):
            self.loaded.append((char, sess))

        def _delete_past_fight(self, path, sess):
            self.deleted.append((path, sess))

        def _get_active_tracker(self):
            return None

        def _sync_history_combo(self):
            pass

        def _render_combat_breakdown(self):
            pass

        def _clear_old_logs(self):
            pass

    return _Host()


def _visible_log_rows(host):
    from PySide6 import QtWidgets
    return [w for w in host.cp_content_container.findChildren(QtWidgets.QFrame)
            if w.objectName() == "PastFightLogRow"]


def _visible_fight_rows(host):
    from PySide6 import QtWidgets
    return [w for w in host.cp_content_container.findChildren(QtWidgets.QFrame)
            if w.objectName() == "PastFightRow"]


def _fold_rows(host):
    """The `N logs with no <Kind> · show` lines the list draws (Phase 2)."""
    from PySide6 import QtWidgets
    return [w for w in host.cp_content_container.findChildren(
        QtWidgets.QPushButton) if w.objectName() == "PastFightsFoldRow"]


def _reveal_folded(host):
    """Unfold the rows the active filter has nothing for, before a render.

    A test that needs to inspect one of those rows has to ask for it: the list
    does not draw them by default, and that IS the fold (`_add_fold_row`). This
    is the same flag the fold line's click sets.
    """
    host._cp_fights_show_nomatch = True


def _drawn_paths(host):
    """The log paths the list actually drew - the fold removes a row entirely."""
    return {e["row"]["path"] for e in host._cp_log_rows}


#: Row chrome that carries no information (the expand chevron).
_ROW_CHROME = {"\u25b8", "\u25be"}


def _assert_tooltip_covers_row(tip: str, row_widget) -> None:
    """Shared invariant: every label the row DRAWS appears in its hover text.

    Used by each kind of row in the Fights view (log, fight, dialog file,
    records), so a tooltip that drifts from the row it belongs to fails a test
    instead of only being noticed on screen.
    """
    from PySide6 import QtWidgets

    labels = [w.text() for w in row_widget.findChildren(QtWidgets.QLabel)
              if w.text() and w.text() not in _ROW_CHROME]
    assert labels, "the row drew no text at all"
    for text in labels:
        assert text in tip, f"the row shows {text!r} but its tooltip is {tip!r}"


def test_the_difficulty_token_is_read_off_the_log_name(tmp_path):
    """A log written in a named instance carries its difficulty in the NAME —
    the list shows it, and the character label must not absorb it."""
    import farever_companion.ui.combat.history_index as hi

    day = tmp_path / "2026-10" / "02"
    _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2_HARD.json",
               [_fight("trash")])
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])

    by_name = {r["path"].name: r for r in hi.scan_history_dir(tmp_path)}
    hard = by_name["Trash pack King Ratsar_Kartik_Warrior_a1b2_HARD.json"]
    assert hard["mode"] == "hard"
    assert hard["kind"] == "trash"
    assert hard["boss"] == "King Ratsar"          # not the whole tail
    assert hard["char"] == "Kartik Warrior A1b2"  # "Hard" is not a character
    plain = by_name["King Ratsar_Kartik_Warrior_a1b2.json"]
    assert plain["mode"] == ""
    assert plain["boss"] == "King Ratsar"


def test_the_log_row_shows_the_difficulty_it_was_written_at(tmp_path, monkeypatch):
    """The list badges a log from its name alone — no file is opened to say
    which difficulty the run was."""
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2_HEROIC.json",
               [_fight("trash")])
    reads = _spy_reads(monkeypatch)
    host._render_past_fights()
    assert reads == []
    texts = [w.text() for w in host.cp_content_container.findChildren(
        QtWidgets.QLabel)]
    assert "HEROIC" in texts


def test_the_fights_list_groups_logs_by_their_day_folder(tmp_path, monkeypatch):
    """A list of fights is asked "which evening was that?", so the rows are
    grouped by DATE — and the date comes off the archive FOLDER, not the
    mtime, so a file restored or copied later still lands on its own day."""
    host = _fights_host(tmp_path, monkeypatch)
    d1 = tmp_path / "2026-10" / "01"
    d2 = tmp_path / "2026-10" / "02"
    _write_log(d1, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(d2, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("trash")])

    host._render_past_fights()
    labels = _day_labels(host)
    assert any("2026-10-01" in t and "Thu" in t for t in labels)
    assert any("2026-10-02" in t and "Fri" in t for t in labels)
    # newest day first
    assert labels.index(next(t for t in labels if "2026-10-02" in t)) \
        < labels.index(next(t for t in labels if "2026-10-01" in t))
    assert len(_visible_log_rows(host)) == 2


def test_expanding_one_log_reads_only_that_log(tmp_path, monkeypatch):
    """The list is lazy per log: nothing is parsed until a row is expanded,
    and expanding one row reads exactly that file."""
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    a = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                   [_fight("boss"), _fight("boss", start=2000.0)])
    _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("trash")])
    reads = _spy_reads(monkeypatch)

    host._render_past_fights()
    assert reads == []                       # listing read nothing
    assert _visible_fight_rows(host) == []   # and shows no fights yet

    # expand the King Ratsar log: its two fights appear, and only it was read
    entry = next(e for e in host._cp_log_rows if e["row"]["path"] == a)
    host._toggle_log_row(entry)
    assert reads == [a]
    assert len(_visible_fight_rows(host)) == 2

    # collapsing hides them without re-reading; expanding again is cached
    host._toggle_log_row(entry)
    assert not entry["holder"].isVisible()
    host._toggle_log_row(entry)
    assert reads == [a]
    assert len(_visible_fight_rows(host)) == 2


def test_a_rebuild_does_not_re_read_an_expanded_log(tmp_path, monkeypatch):
    """Rebuilding the view must not re-parse a log it already read.

    The list re-creates every row whenever the folder changes, so without a
    parse cache each rebuild threw an expanded log's fights away and read the
    file again to draw the very same rows (duration and damage and all).
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    a = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                   [_fight("boss")])
    reads = _spy_reads(monkeypatch)

    host._render_past_fights()
    entry = next(e for e in host._cp_log_rows if e["row"]["path"] == a)
    host._toggle_log_row(entry)
    assert reads == [a]

    # a full rebuild re-creates the rows; expanding again is a cache hit
    host._render_past_fights(force=True)
    entry2 = next(e for e in host._cp_log_rows if e["row"]["path"] == a)
    host._toggle_log_row(entry2)
    assert reads == [a], "the rebuild re-read the log it had already parsed"
    assert len(_visible_fight_rows(host)) == 1


def test_a_rewritten_log_is_read_again(tmp_path, monkeypatch):
    """The cache is keyed by (path, mtime, size), so a changed log re-reads."""
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    name = "King Ratsar_Kartik_Warrior_a1b2.json"
    a = _write_log(day, name, [_fight("boss")])
    reads = _spy_reads(monkeypatch)

    host._render_past_fights()
    entry = next(e for e in host._cp_log_rows if e["row"]["path"] == a)
    host._toggle_log_row(entry)
    assert reads == [a]

    # the log gains a fight: a new size, so the cached entry must not be served
    _write_log(day, name, [_fight("boss"), _fight("boss", start=2000.0)])
    host._render_past_fights(force=True)
    entry2 = next(e for e in host._cp_log_rows if e["row"]["path"] == a)
    host._toggle_log_row(entry2)
    assert reads == [a, a], "a changed log must be read again"
    assert len(_visible_fight_rows(host)) == 2


def test_a_fight_rows_tooltip_repeats_what_the_row_shows(tmp_path, monkeypatch):
    """The hover text must agree with the row beside it.

    It used to drop the timestamp and the kind/FAILED badge, so a row showing
    "22:56:30 Boss Fight (Died (Kartik (You))) FAILED 👑 Reblochonk 00:45
    10,636 DMG" hovered as "Boss Fight (Died…) · 👑 Reblochonk · 00:45 ·
    10,636 DMG" — the same fight, two readings. Everything the row draws is
    built from the session it read, so the check is: every label the row shows
    appears in its tooltip.
    """
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    a = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                   [_fight("boss", damage=10636.0)])
    host._render_past_fights()
    entry = next(e for e in host._cp_log_rows if e["row"]["path"] == a)
    host._toggle_log_row(entry)

    row = _visible_fight_rows(host)[0]
    _assert_tooltip_covers_row(row.toolTip(), row)


def test_every_fights_view_row_agrees_with_its_tooltip(tmp_path, monkeypatch):
    """The Fights view's hover text must repeat the row it belongs to.

    The fight row disagreed first (no timestamp, no FAILED badge); auditing the
    rest of the view found the same hole in the collapsed log row — it dropped
    the kind/difficulty badges, the target and the character tag. This one test
    holds both row kinds to the same rule.
    """
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2_HARD.json",
               [_fight("boss", name="Boss Fight (Boss Defeated)",
                       damage=900.0)])
    _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("trash")])

    # 1) the Combat & DPS fights list: collapsed log rows, then fight rows
    host = _fights_host(tmp_path, monkeypatch)
    host._render_past_fights()
    log_rows = _visible_log_rows(host)
    assert log_rows, "no log rows rendered"
    for w in log_rows:
        _assert_tooltip_covers_row(w.toolTip(), w)
    for entry in list(host._cp_log_rows):
        host._toggle_log_row(entry)
    fight_rows = _visible_fight_rows(host)
    assert fight_rows, "no fight rows rendered"
    for w in fight_rows:
        _assert_tooltip_covers_row(w.toolTip(), w)


def test_the_kind_filter_hides_fights_without_touching_other_logs(
        tmp_path, monkeypatch):
    """A filter that hides rows must not pay for the logs it is hiding: only
    the expanded log is read, and only its matching fights are shown."""
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    a = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("trash")])
    reads = _spy_reads(monkeypatch)

    host._cp_fights_filter = "trash"
    _reveal_folded(host)          # the fold line would hide the boss row
    host._render_past_fights()
    entry = next(e for e in host._cp_log_rows if e["row"]["path"] == a)
    host._toggle_log_row(entry)
    assert reads == [a]                      # the boss log was read, once
    assert _visible_fight_rows(host) == []   # ...and contributed no rows


# --- the day-limited scan: the cold-start combo/breakdown path ---------------

def test_the_scanner_can_be_limited_to_one_day_folder(tmp_path):
    """`scan_history_dir(day=...)` returns only that day's files, so a caller
    that wants just today never builds rows for a season of logs."""
    import farever_companion.ui.combat.history_index as hi

    t1 = time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1))
    t2 = time.mktime((2026, 1, 3, 12, 0, 0, 0, 0, -1))
    _write_log(_log_dir(tmp_path, t1),
               "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    wanted = _write_log(_log_dir(tmp_path, t2),
                        "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    day2 = time.strftime("%Y-%m-%d", time.localtime(t2))

    rows = hi.scan_history_dir(tmp_path, day=day2)
    assert [r["path"] for r in rows] == [wanted]
    assert all(r["day"] == day2 for r in rows)


def test_the_day_filter_parses_only_that_days_logs(tmp_path, monkeypatch):
    """The point of the filter: asking for one day must not parse the others.

    This is the 66-file, ~44 ms cold start, in miniature."""
    import farever_companion.ui.combat.history_index as hi

    t1 = time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1))
    t2 = time.mktime((2026, 1, 3, 12, 0, 0, 0, 0, -1))
    old = _write_log(_log_dir(tmp_path, t1),
                     "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    new = _write_log(_log_dir(tmp_path, t2),
                     "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    day2 = time.strftime("%Y-%m-%d", time.localtime(t2))
    reads = _spy_reads(monkeypatch)

    rows = hi.scan_history_dir(tmp_path, day=day2)
    for r in rows:
        list(r["sessions"])          # force whatever the caller would ask for

    assert reads == [new], "a day-limited scan parsed another day's log"
    assert old not in reads


def test_the_encounter_combo_source_is_today_only(tmp_path, monkeypatch):
    """The cold-start combo/breakdown fallback parses TODAY'S logs only.

    Live 2026-10-03: this path walked every archived day and iterated each
    row's `_LazySessions`, parsing all 66 logs on the GUI thread (~44 ms) to
    keep at most three. Older days are the 📜 Fights dialog's job, which lists
    them by name and reads one only when a row is viewed.
    """
    from farever_companion.ui.pages import combat_history as ch

    t_old = time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1))
    old = _write_log(_log_dir(tmp_path, t_old),
                     "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    today_file = _write_log(_log_dir(tmp_path),
                            "King Ratsar_Kartik_Warrior_a1b2.json",
                            [_fight("boss", start=4242.0)])
    monkeypatch.setattr(ch, "dps_dir", lambda: tmp_path)
    reads = _spy_reads(monkeypatch)

    class _Stub:
        _browse_sessions = None

    label, sessions = ch.CombatHistoryMixin._history_source(_Stub(), None)

    assert label == "All Fights"
    assert [s.start_time for s in sessions] == [4242.0]
    assert reads == [today_file], "the cold-start source parsed an older day"
    assert old not in reads


def test_a_trash_row_reads_the_dungeon_and_boss_apart_from_the_name(tmp_path):
    """The writer names a named pull `Trash pack <Dungeon> - <Boss>_<slug>`.

    Read off the NAME without opening the log, `boss` must be just the boss and
    `dungeon` the instance: it used to glue them together, so the row's target
    line drew the dungeon and boss as one name and `dungeon` was empty.
    """
    import farever_companion.ui.combat.history_index as hi

    day = tmp_path / "2026-10" / "02"
    # the current name, and a pre-2026-10-04 file that must read identically
    _write_log(day,
               "Kobolds Mines - Reblochonk trash_Kartik_Warrior_a1b2.json",
               [_fight("trash")])
    _write_log(day,
               "Trash pack Kobolds Mines - Reblochonk_Kartik_Warrior_a1b2.json",
               [_fight("trash")])

    rows = [r for r in hi.scan_history_dir(tmp_path) if r["kind"] == "trash"]
    assert len(rows) == 2, "both the new and the legacy name must read as trash"
    for row in rows:
        assert row["boss"] == "Reblochonk"
        assert row["dungeon"] == "Kobolds Mines"
        assert row["char"] == "Kartik Warrior A1b2"
        # the display label reads "<Boss> trash", the marker after the boss
        assert row["name"] == "Kobolds Mines - Reblochonk trash"


# --- the per-log sidecar: the collapsed row and the records are read-free ----

def _write_sidecar(path) -> None:
    """Convert one log to a sidecar, the way archive time (or a backfill) does."""
    import farever_companion.ui.combat.history_index as hi
    hi.write_log_summary(path, hi.load_history_sessions(path))


def test_the_collapsed_log_row_shows_aggregates_without_reading_the_log(
        tmp_path, monkeypatch):
    """The collapsed row answers \"how many fights, how big\" from its sidecar.

    The whole point of the Fights list is that a season of logs opens without
    parsing one; drawing an aggregate line must not quietly undo that. Here the
    count / best damage / best time all come from the sidecar — the log is never
    opened (`reads == []`) — and the hover text repeats the line it drew.
    """
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    a = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                   [_fight("boss"), _fight("boss", start=2000.0,
                                           damage=9000.0)])
    _write_sidecar(a)
    reads = _spy_reads(monkeypatch)

    host._render_past_fights()
    assert reads == [], "the collapsed row parsed a log it had a sidecar for"
    texts = [w.text() for w in host.cp_content_container.findChildren(
        QtWidgets.QLabel)]
    agg = next((t for t in texts if t.startswith("2 fights")), None)
    assert agg is not None, texts
    assert "9,000 DMG" in agg and "01:00" in agg, agg

    for w in _visible_log_rows(host):
        _assert_tooltip_covers_row(w.toolTip(), w)


def test_a_log_without_a_sidecar_shows_no_aggregate_and_is_not_read(
        tmp_path, monkeypatch):
    """A missing sidecar is advisory: the row omits the line rather than reads.

    Legacy logs have no sidecar until an explicit action converts them, so the
    collapsed list must treat \"no sidecar\" as \"nothing to say\" — never as a
    reason to parse every legacy log on the GUI thread, which is the freeze the
    lazy index exists to prevent.
    """
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    reads = _spy_reads(monkeypatch)

    host._render_past_fights()
    assert reads == []
    texts = [w.text() for w in host.cp_content_container.findChildren(
        QtWidgets.QLabel)]
    assert not any("fight" in t and "DMG" in t for t in texts), texts


def test_the_batch_bar_is_frozen_above_the_scrolling_list(tmp_path, monkeypatch):
    """The controls must not scroll away with the logs.

    The batch bar used to be a child of `cp_content_lay` — the scroll area's own
    layout — so scrolling down to older files carried Select All, the filters
    and Delete off the top of the page (live 2026-10-04: "the bar goes up the
    page"). It now lives in its own slot ABOVE the scroll area, and the scroll
    body holds only the day headers and log rows.
    """
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    log = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                     [_fight("boss")])
    host._render_past_fights()

    toolbar = host._cp_fights_bar["widget"]
    assert toolbar.parentWidget() is host.cp_fights_toolbar_host
    # it is NOT inside the scrolling body, which is the whole point
    assert not host.cp_content_container.isAncestorOf(toolbar)
    # the scroll body holds just the rows
    rows = _visible_log_rows(host)
    assert rows and all(host.cp_content_container.isAncestorOf(w)
                        for w in rows)
    # the Select All checkbox the report names sits in the frozen bar
    assert [cb for cb in toolbar.findChildren(QtWidgets.QCheckBox)
            if cb.text() == "Select All"], "the bar lost its Select All"

    # a rebuild swaps the bar rather than stacking a second one
    host._render_past_fights(force=True)
    assert host._cp_fights_bar["widget"] is not toolbar
    assert host._cp_fights_toolbar_lay.count() == 1

    # no logs -> no bar, and the slot is emptied (not left holding a stale one)
    log.unlink()
    host._render_past_fights(force=True)
    assert host._cp_fights_toolbar_lay.count() == 0


def test_the_body_stacks_from_the_top_instead_of_floating_apart(
        tmp_path, monkeypatch):
    """The rows must not spread apart to fill a tall viewport.

    The scroll area is `setWidgetResizable(True)`, so the container is stretched
    to the viewport and its layout hands the leftover height to whatever will
    take it. Two logs in a 760px window leave ~200px spare, and with nothing
    claiming it the day header floated ~50px down the pane while the rows sat
    ~100px apart (live 2026-10-05) - the list read as if it had lost its
    spacing. `_pin_rows_to_top` claims that slack: ONE trailing stretch, added
    last, so the rows stack at the layout's own spacing and every spare pixel
    falls to the bottom however tall the window is, and no rebuild leaves a
    second one behind.
    """
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(day, "Queen Vex_Kartik_Warrior_a1b2.json",
               [_fight("boss", name="Queen Vex")])

    def spacers():
        lay = host.cp_content_lay
        return [i for i in range(lay.count())
                if lay.itemAt(i).widget() is None
                and lay.itemAt(i).spacerItem() is not None]

    def assert_one_trailing_stretch():
        from PySide6 import QtWidgets

        lay = host.cp_content_lay
        idx = spacers()
        assert len(idx) == 1, ("the body needs exactly one stretch, "
                               "not %d" % len(idx))
        assert idx[0] == lay.count() - 1, "the stretch is not the last item"
        it = lay.itemAt(idx[0])
        assert it.spacerItem() is not None
        # a stretch that can't grow claims nothing, and the rows float again
        assert lay.stretch(idx[0]) == 1, "the trailing stretch does not grow"
        # and nothing ABOVE it may grow: the rows are what must stay put
        assert [lay.stretch(i) for i in range(lay.count())] == (
            [0] * idx[0] + [1]), "a row can stretch, and will float with it"
        tops = []
        for i in range(idx[0]):
            w = lay.itemAt(i).widget()
            # the empty-state label is a permanent item that is hidden while
            # there are rows; it is not part of the stack
            if w is None or w is host.cp_empty_lbl:
                continue
            tops.append((w.objectName(), lay.itemAt(i).geometry().y(),
                         lay.itemAt(i).geometry().height()))
        return tops

    host._render_past_fights()
    tops = assert_one_trailing_stretch()
    names = [t[0] for t in tops]
    assert names[0] == "PastFightsDayHeader", names
    assert names.count("PastFightLogRow") == 2, names

    # extra room must go BELOW the rows: pack the container taller than it
    # needs and re-run the layout, then measure where the rows landed.
    cont = host.cp_content_container
    cont.resize(700, 520)
    host.cp_content_lay.activate()
    host._render_past_fights(force=True)
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
    cont.resize(700, 520)
    host.cp_content_lay.activate()
    drawn = [t for t in assert_one_trailing_stretch()
             if t[2] > 0 and t[0] != "PastFightHolder"]
    assert [t[0] for t in drawn][:3] == ["PastFightsDayHeader",
                                        "PastFightLogRow",
                                        "PastFightLogRow"], drawn
    if len({t[1] for t in drawn}) > 1:
        assert drawn[0][1] <= 4, ("the day header floated %dpx down a tall "
                                  "viewport" % drawn[0][1])
        gaps = [drawn[i + 1][1] - (drawn[i][1] + drawn[i][2])
                for i in range(len(drawn) - 1)]
        assert all(g <= 12 for g in gaps), ("the rows spread apart: gaps %s"
                                            % gaps)

    # rebuilds must not stack stretch on stretch
    host._render_past_fights(force=True)
    host._render_past_fights(force=True)
    assert_one_trailing_stretch()

    # the same holds for the two message branches: a query matching nothing
    # and an empty folder both end in the one stretch, so the message stays up
    host._on_fights_query_changed("nothing-matches-this")
    assert_one_trailing_stretch()
    host._on_fights_query_changed("")
    for log in day.iterdir():
        log.unlink()
    host._render_past_fights(force=True)
    assert_one_trailing_stretch()


def test_the_kind_filter_chips_act_as_a_radio_group(tmp_path, monkeypatch):
    """Clicking the ACTIVE chip must leave it selected, not blank the row.

    The five chips are checkable, so a click on the chip already in force
    unchecked it while `_cp_fights_filter` kept filtering that kind — the bar
    then showed nothing selected over a list that was still filtered, and only
    a click on some other chip put the row back in sync. The chips are a radio
    group: whichever kind is in force is the one shown checked.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    host._render_past_fights()

    def chips():
        return (host._cp_fights_bar or {}).get("filters", {})

    assert chips()["all"].isChecked()
    chips()["boss"].click()
    assert host._cp_fights_filter == "boss"
    assert chips()["boss"].isChecked() and not chips()["all"].isChecked()

    # the chip that is ALREADY active must stay checked when clicked again
    chips()["boss"].click()
    assert host._cp_fights_filter == "boss"
    assert chips()["boss"].isChecked(), "the active filter chip unchecked itself"
    assert sum(1 for b in chips().values() if b.isChecked()) == 1


def _log_row(host, path):
    """The entry AND the row widget for one log, by its path.

    The widget is looked up by POSITION rather than from the entry: the entries
    and the rendered rows are appended in the same loop, and keeping a second
    Python reference to a widget the list rebuilds and deletes is how a wrapper
    outlives its C++ object.
    """
    entries = host._cp_log_rows
    rows = _visible_log_rows(host)
    assert len(entries) == len(rows), (len(entries), len(rows))
    i = next(k for k, e in enumerate(entries) if e["row"]["path"] == path)
    return entries[i], rows[i]


def _row_labels(w):
    from PySide6 import QtWidgets
    return [l.text() for l in w.findChildren(QtWidgets.QLabel) if l.text()]


def _holder_text(entry):
    """Every line an expanded log's holder draws (fight rows are not labels)."""
    from PySide6 import QtWidgets
    lay = entry["holder"].layout()
    return [lay.itemAt(i).widget().text() for i in range(lay.count())
            if isinstance(lay.itemAt(i).widget(), QtWidgets.QLabel)
            and lay.itemAt(i).widget().text()]


def _write_sidecar(p):
    """Write the log's real sidecar, through the writer the tracker uses."""
    import farever_companion.ui.combat.history_index as hi
    return hi.write_log_summary(p, hi.load_history_sessions(p))


def test_a_filter_tells_the_row_what_it_will_find_without_reading_a_log(
        tmp_path, monkeypatch):
    """The row answers "is there anything in here for me?" BEFORE it is opened.

    The complaint was having to expand a row to find out. A log whose NAME says
    it cannot hold what the filter asks for says so on the row - a muted
    `no Trash` chip and a dimmed chevron - and the verdict costs no read: the
    name and the sidecar are already on the row.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    boss = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                      [_fight("boss")])
    trash = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                       [_fight("trash")])
    reads = _spy_reads(monkeypatch)

    host._cp_fights_filter = "trash"
    _reveal_folded(host)          # keep the ruled-out row on screen to read it
    host._render_past_fights(force=True)
    assert reads == [], "the verdict parsed a log to draw itself"

    b_entry, b_row = _log_row(host, boss)
    t_entry, t_row = _log_row(host, trash)
    assert b_entry["verdict"] == "none"
    assert "no Trash" in _row_labels(b_row)
    assert t_entry["verdict"] == "has"
    assert "no Trash" not in _row_labels(t_row)

    # The dim chevron is a HINT, never a lock: the row still opens, and the
    # holder then says why it is bare instead of going blank.
    host._toggle_log_row(b_entry)
    assert _holder_text(b_entry) == ["(no Trash fights in this log)"]
    assert reads == [boss]          # one log, and only because it was opened


def test_a_boss_log_under_failed_is_never_ruled_out(tmp_path, monkeypatch):
    """Only a name that cannot BE a failure is ruled out.

    `summarize` counts only fights that dealt damage, so a boss log whose sidecar
    reports no failure can still hold a wipe with nothing on the board. Failed
    therefore never says "no Failed" to a boss log; a trash log it does.
    """
    from farever_companion.ui import theme

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    boss = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                      [_fight("boss",
                             name="Boss Fight (Boss Defeated)")])
    trash = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                       [_fight("trash")])
    _write_sidecar(boss)                    # a KILL: fights=1, has_failure=False

    host._cp_fights_filter = "failed"
    _reveal_folded(host)          # the trash row folds; it has to be readable
    host._render_past_fights(force=True)
    b_entry, b_row = _log_row(host, boss)
    t_entry, t_row = _log_row(host, trash)
    assert b_entry["verdict"] == "unknown"
    assert "no Failed" not in _row_labels(b_row)
    assert theme.DIM not in b_entry["chev"].styleSheet()
    assert t_entry["verdict"] == "none"
    assert "no Failed" in _row_labels(t_row)
    assert theme.DIM in t_entry["chev"].styleSheet()


def test_an_empty_log_is_listed_as_empty_rather_than_as_an_ordinary_row(
        tmp_path, monkeypatch):
    """A log its own sidecar counted no fight in says so on the row.

    RC3: an empty log used to be listed like any other, revealed itself only when
    expanded, and silently lost its aggregate line - which read as data loss
    rather than as an empty log.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    empty = _write_log(day, "Boss_Kartik_Warrior_a1b2.json", [])
    _write_sidecar(empty)                   # summarize([]) -> fights 0

    host._render_past_fights()
    entry, row = _log_row(host, empty)
    assert entry["verdict"] == "empty"
    labels = _row_labels(row)
    assert "empty log" in labels
    assert "no fights recorded" in labels
    host._toggle_log_row(entry)             # it still explains itself
    assert _holder_text(entry) == ["(no readable fights in this log)"]





def test_every_collapsed_row_always_draws_its_aggregate_line(
        tmp_path, monkeypatch):
    """The aggregate slot is drawn even when the sidecar has not been written.

    A row that silently DROPPED the line read as a row that lost its data - the
    other half of "this list looks broken" - so a log with no sidecar says
    `no summary yet` in the slot instead of drawing nothing at all.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    bare = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                      [_fight("boss")])
    counted = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                         [_fight("trash")])
    _write_sidecar(counted)

    host._render_past_fights()
    _e, row = _log_row(host, bare)
    assert "no summary yet" in _row_labels(row)
    _e2, row2 = _log_row(host, counted)
    assert any(t.startswith("1 fight") for t in _row_labels(row2))


def test_expanding_any_log_under_any_filter_never_leaves_a_blank_holder(
        tmp_path, monkeypatch):
    """The acceptance line of the fix, as a contract.

    For every filter and every log on disk, an expanded holder ends in fight rows
    or in one line naming why it has none - the report ("empty rows that I need
    to expand to see nothing in them") turned into a test, so the blank holder
    cannot come back through some other path.

    Every row is revealed first, so "every log on disk" really is every log: a
    folded row is still a row a player can reach in one click, and it owes the
    same answer on its holder as one the filter left out.
    """
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("trash")])
    _write_log(day, "Boss_Kartik_Warrior_a1b2.json", [])

    for filt in ("all", "boss", "trash", "dummy", "failed"):
        host._cp_fights_filter = filt
        _reveal_folded(host)
        host._render_past_fights(force=True)
        assert host._cp_log_rows, filt
        for entry in list(host._cp_log_rows):
            host._toggle_log_row(entry)
        for entry in host._cp_log_rows:
            lay = entry["holder"].layout()
            drawn = [lay.itemAt(i).widget() for i in range(lay.count())]
            name = entry["row"]["name"]
            assert drawn, (filt, name, "blank holder")
            for w in drawn:
                assert (w.objectName() == "PastFightRow"
                        or (isinstance(w, QtWidgets.QLabel) and w.text())),                     (filt, name, w)


def test_the_filter_line_is_per_row_and_no_neighbour_can_hide_it(
        tmp_path, monkeypatch):
    """The original bug, as a test: a neighbour's fights hid the empty line.

    `state["shown"]` totalled fights across EVERY log, so the single list-level
    "nothing matched" note was already hidden the moment some other log
    contributed a row - and the log the filter had emptied expanded onto a blank
    holder. The line belongs to the row now, so the order the rows are opened in
    cannot change what any of them shows.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    boss = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                      [_fight("boss")])
    trash = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                       [_fight("trash")])
    host._cp_fights_filter = "trash"
    _reveal_folded(host)          # both rows must be drawable to compare them
    host._render_past_fights(force=True)

    b_entry, _br = _log_row(host, boss)
    t_entry, _tr = _log_row(host, trash)
    # the log that HAS fights is opened first: that is what used to hide it
    host._toggle_log_row(t_entry)
    assert _visible_fight_rows(host), "the Trash filter kept no fight"
    assert _holder_text(b_entry) == []
    host._toggle_log_row(b_entry)
    assert _holder_text(b_entry) == ["(no Trash fights in this log)"]
    # ...and there is no list-level note left to be switched off at all
    assert not hasattr(host, "_cp_fights_filter_note")


# --- Phase 2: the fold -------------------------------------------------------

def test_a_filtered_list_folds_the_rows_it_has_nothing_for(tmp_path, monkeypatch):
    """The filter stops drawing rows it has nothing for, behind one line.

    The row verdict already knows a named log cannot hold what the filter asks
    for; drawing it anyway left the list padded with rows whose only content is
    "nothing here". Those rows are now one line for the day they were in.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    boss = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                      [_fight("boss")])
    trash = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                       [_fight("trash")])
    empty = _write_log(day, "Boss_Kartik_Warrior_a1b2.json", [])
    _write_sidecar(empty)                   # an empty log, not a chopped one

    host._cp_fights_filter = "trash"
    host._render_past_fights(force=True)

    drawn = _drawn_paths(host)
    assert boss not in drawn, "a row the filter has nothing for was drawn"
    assert {trash, empty} <= drawn, drawn
    folds = _fold_rows(host)
    assert len(folds) == 1, [f.text() for f in folds]
    assert folds[0].text() == "1 log with no Trash  ·  show"
    assert folds[0].isChecked() is False
    # ...and it sits UNDER the rows of its day, not above them
    lay = host.cp_content_lay
    assert lay.indexOf(folds[0]) > max(lay.indexOf(r)
                                       for r in _visible_log_rows(host))


def test_the_fold_reveals_on_click_and_outlives_the_rebuilds(
        tmp_path, monkeypatch):
    """One click lists the folded rows, and the choice is remembered.

    The reveal is a per-instance toggle, the pattern the day-window control
    already uses: a folder change, a rescan or the token's refresh recreates
    every widget, and a choice that dies with them would have to be re-made on
    every tick.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    boss = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                      [_fight("boss")])
    trash = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                       [_fight("trash")])

    host._cp_fights_filter = "trash"
    host._render_past_fights(force=True)
    assert boss not in _drawn_paths(host)

    _fold_rows(host)[0].click()             # the real button, the real slot
    assert boss in _drawn_paths(host), "clicking the line did not reveal it"
    assert host._cp_fights_show_nomatch is True
    folds = _fold_rows(host)
    assert len(folds) == 1, "the rebuild left the old fold line behind"
    assert folds[0].text().endswith("hide")
    assert folds[0].isChecked() is True

    # a rebuild - the fingerprint is unchanged, so this is the force path
    host._render_past_fights(force=True)
    assert boss in _drawn_paths(host), "a rebuild folded the rows back"
    assert _fold_rows(host)[0].text().endswith("hide")

    # ...and it folds back, through the fresh line, not a remembered widget
    _fold_rows(host)[0].click()
    assert boss not in _drawn_paths(host)
    assert host._cp_fights_show_nomatch is False
    assert _fold_rows(host)[0].text().endswith("show")


def test_the_fold_never_hides_a_row_the_name_cannot_answer(
        tmp_path, monkeypatch):
    """Only a row the filter's own question RULES OUT is folded.

    `unknown` exists because some names cannot answer - a boss log can hold a
    wipe with nothing on the board - so those rows stay visible, and `all`
    filters nothing at all, so it folds nothing either.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("trash")])

    for filt in ("all", "dummy"):
        host._cp_fights_filter = filt
        host._render_past_fights(force=True)
        assert _fold_rows(host) == [], filt
        assert len(_visible_log_rows(host)) == 2, filt

    # Failed is the mixed case: the trash log cannot BE a failure, so it folds,
    # while the boss log stays put for the verdict that cannot answer.
    host._cp_fights_filter = "failed"
    host._render_past_fights(force=True)
    assert len(_visible_log_rows(host)) == 1
    assert _fold_rows(host)[0].text() == "1 log with no Failed  ·  show"


def test_an_empty_log_is_never_folded_by_a_kind_filter(tmp_path, monkeypatch):
    """An empty log is a fact about the LOG, not about the filter.

    It says so on its row under every filter, and it is what a cleanup would act
    on - folding it into a kind filter's "no Trash" line would bury it.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    empty = _write_log(day, "Boss_Kartik_Warrior_a1b2.json", [])
    _write_sidecar(empty)

    host._cp_fights_filter = "trash"
    host._render_past_fights(force=True)
    assert _drawn_paths(host) == {empty}
    assert _fold_rows(host) == []


def test_a_day_whose_logs_all_fold_keeps_its_header(tmp_path, monkeypatch):
    """The fold is per DAY: a heading over nothing is what it must not leave.

    One line for the whole list would have to either drop the empty day's header
    or strand it, so each day folds its own rows and keeps its date and count.
    """
    host = _fights_host(tmp_path, monkeypatch)
    quiet = tmp_path / "2026-10" / "02"          # nothing for the Trash filter
    busy = tmp_path / "2026-10" / "03"           # one matching log
    _write_log(quiet, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(busy, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
               [_fight("trash")])

    host._cp_fights_filter = "trash"
    host._render_past_fights(force=True)
    assert len(_day_labels(host)) > 2, _day_labels(host)   # both days, named
    assert len(_visible_log_rows(host)) == 1
    assert [f.text() for f in _fold_rows(host)] ==         ["1 log with no Trash  ·  show"]


def test_a_new_filter_starts_folded_again(tmp_path, monkeypatch):
    """The reveal was a decision about the filter that hid those rows."""
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    boss = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                      [_fight("boss")])
    trash = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                       [_fight("trash")])

    host._cp_fights_filter = "trash"
    host._render_past_fights(force=True)
    _fold_rows(host)[0].click()
    assert host._cp_fights_show_nomatch is True

    host._set_fights_filter("boss")
    assert host._cp_fights_show_nomatch is False, "a new filter kept the reveal"
    assert boss in _drawn_paths(host)
    assert trash not in _drawn_paths(host), "the other kind folded instead"
    assert [f.text() for f in _fold_rows(host)] ==         ["1 log with no Boss  ·  show"]



def test_a_day_header_names_how_many_logs_the_day_holds(tmp_path, monkeypatch):
    """The day header carries the day's count, never a partial fight total.

    The fight total is drawn only when EVERY log on the day has a sidecar;
    otherwise it would be a partial sum dressed as a total, which is worse than
    the plain log count it replaces.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    a = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    b = _write_log(day, "Trash pack King Ratsar_Kartik_Warrior_a1b2.json",
                   [_fight("trash")])

    host._render_past_fights()
    assert "2 logs" in _day_labels(host), _day_labels(host)   # no sidecars yet
    _write_sidecar(a)
    _write_sidecar(b)
    host._render_past_fights(force=True)
    shown = _day_labels(host)
    assert any(t == "2 logs · 2 fights" for t in shown), shown



# --- the day window: a long season draws fresh ------------------------------

def _day_labels(host):
    """Every label a day header draws, found by the header's objectName.

    The header used to be recognised by its `-  date  -` decoration; it now
    carries an objectName, so the rule and the log count beside the date can
    change without a test parsing the header's text.
    """
    from PySide6 import QtWidgets
    heads = [w for w in host.cp_content_container.findChildren(
        QtWidgets.QFrame) if w.objectName() == "PastFightsDayHeader"]
    return [w.text() for h in heads
            for w in h.findChildren(QtWidgets.QLabel) if w.text()]


def _season(tmp_path, days):
    for day in days:
        _write_log(tmp_path / "2026-10" / day[-2:],
                   "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])


def test_the_fights_list_shows_only_the_most_recent_days(tmp_path, monkeypatch):
    """A long season draws a bounded number of days, not every log ever.

    Live 2026-10-04: the list built a widget per log across the whole season, so
    opening it over months of archives crawled. It now draws only the newest
    `_FIGHTS_RECENT_DAYS` days, with the rest behind a control that says how
    many it is holding back.
    """
    from PySide6 import QtWidgets
    import farever_companion.ui.pages.combat_fights_list as fl

    host = _fights_host(tmp_path, monkeypatch)
    days = [f"2026-10-{d:02d}" for d in range(1, 11)]        # 10 days > 7
    _season(tmp_path, days)

    host._render_past_fights()

    n = fl._FIGHTS_RECENT_DAYS
    assert len(_visible_log_rows(host)) == n
    shown = _day_labels(host)
    for day in days[-n:]:                                    # 10-04 .. 10-10
        assert any(day in t for t in shown), (day, shown)
    for day in days[:-n]:                                    # 10-01 .. 10-03
        assert not any(day in t for t in shown), (day, shown)

    # the control names the number of days it is hiding, and starts unlit
    btn = next(b for b in host._cp_fights_bar["widget"].findChildren(
        QtWidgets.QPushButton) if b.text().startswith("Show all days"))
    assert "(+3)" in btn.text(), btn.text()
    assert not btn.isChecked()


def test_show_all_days_reveals_the_whole_season_and_folds_back(
        tmp_path, monkeypatch):
    """The day window is a toggle that survives the rebuilds it triggers.

    The preference lives on the instance, so the folder-change rebuilds that
    recreate the whole list do not silently snap the view back to 7 days — and
    a player who opened a season can put it away again without leaving the page.
    """
    from PySide6 import QtWidgets
    import farever_companion.ui.pages.combat_fights_list as fl

    host = _fights_host(tmp_path, monkeypatch)
    days = [f"2026-10-{d:02d}" for d in range(1, 11)]
    _season(tmp_path, days)
    host._render_past_fights()
    assert len(_visible_log_rows(host)) == fl._FIGHTS_RECENT_DAYS

    def day_btn():
        return next(b for b in host._cp_fights_bar["widget"].findChildren(
            QtWidgets.QPushButton) if b.isCheckable() and "days" in b.text())

    day_btn().click()
    assert host._cp_fights_show_all_days is True
    assert len(_visible_log_rows(host)) == len(days)
    shown = _day_labels(host)
    for day in days:
        assert any(day in t for t in shown), (day, shown)

    # the choice is remembered across a folder-change rebuild
    host._render_past_fights(force=True)
    assert len(_visible_log_rows(host)) == len(days)
    assert day_btn().isChecked(), "the control did not reflect the open window"

    # ...and it folds back, because it is a toggle, not a one-way door
    day_btn().click()
    assert host._cp_fights_show_all_days is False
    assert len(_visible_log_rows(host)) == fl._FIGHTS_RECENT_DAYS


def test_the_day_window_follows_the_user_setting(tmp_path, monkeypatch):
    """The window size is a user setting, not a hard-coded 7.

    Settings > DPS > Combat DPS Meter > Fights list window. A non-default value
    changes both how many days draw and what the "Show all days" control says
    it is holding back; 0 means show every day, with nothing left to toggle.
    """
    from PySide6 import QtWidgets
    import types

    host = _fights_host(tmp_path, monkeypatch)
    host.s = types.SimpleNamespace(dps_fights_recent_days=3)
    days = [f"2026-10-{d:02d}" for d in range(1, 11)]        # 10 days > 3
    _season(tmp_path, days)

    host._render_past_fights()
    assert len(_visible_log_rows(host)) == 3
    btn = next(b for b in host._cp_fights_bar["widget"].findChildren(
        QtWidgets.QPushButton) if b.text().startswith("Show all days"))
    assert "(+7)" in btn.text(), btn.text()

    # 0 = show every day; there is then nothing for the toggle to reveal, so
    # the control is gone rather than offering to hide what is already shown.
    host.s.dps_fights_recent_days = 0
    host._render_past_fights(force=True)
    assert len(_visible_log_rows(host)) == len(days)
    assert not any(b.isCheckable() and "days" in b.text()
                   for b in host._cp_fights_bar["widget"].findChildren(
                       QtWidgets.QPushButton)), \
        "a show-all toggle survived over an already-open window"


def _spy_sidecar_reads(monkeypatch) -> list:
    """Record every SIDECAR read the scan performs (logs are spied separately)."""
    import farever_companion.ui.combat.history_index as hi

    reads: list = []
    real = hi.read_log_summary

    def spy(path, mtime, size):
        reads.append(Path(path))
        return real(path, mtime, size)

    monkeypatch.setattr(hi, "read_log_summary", spy)
    return reads


def test_a_repeat_scan_of_an_unchanged_folder_reads_no_sidecar(
        tmp_path, monkeypatch):
    """Re-opening the Fights list must cost metadata, not reads.

    The first open describes the visible days and reads their sidecars; the
    second finds nothing has moved, so it reads none. This is the cache's whole
    contract — the folder is untouched and a stale aggregate line is worse than
    a slow list.
    """
    import farever_companion.ui.combat.history_index as hi

    day = tmp_path / "2026-10" / "02"
    a = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    c = _write_log(day, "King Ratsar_Koban_Rogue_a1b2.json", [_fight("boss")])
    _write_sidecar(a)

    # The cold scan probes only `c`: `a`'s sidecar was written through
    # `write_log_summary`, which records what it just wrote, so the scan does
    # not re-read a file this process already holds. `c` has no sidecar and
    # must still be probed (and reads as None).
    first = _spy_sidecar_reads(monkeypatch)
    rows = hi.scan_history_dir(tmp_path)
    assert set(first) == {c}, "the cold scan must probe what it does not hold"
    by_path = {r["path"]: r for r in rows}
    assert by_path[a]["summary"], "the sidecar just written was not surfaced"
    assert by_path[c]["summary"] is None, "a log with no sidecar claimed one"

    second = _spy_sidecar_reads(monkeypatch)
    globs = _spy_globs(monkeypatch)
    rows = hi.scan_history_dir(tmp_path)
    assert len(rows) == 2, "the repeat scan dropped a log"
    assert second == [], "an unchanged folder was read again"
    # The day-level cache still removes the expensive work on a repeat open: no
    # glob, no descriptor rebuild, no sidecar read. It does NOT skip the readdir
    # itself — freshness is the folder's entry signature (`os.scandir`), because
    # a directory mtime is updated lazily on Windows and cannot be trusted to
    # move when a log is added (see `history_cache` and
    # test_history_scan_labels_per_boss_files_with_the_character).
    assert day not in globs, "an unchanged day folder was globbed again"

    # ...and the cache still answers a CHANGE. A new log in a cached day is
    # found, and it costs ONE sidecar probe: the two logs already described keep
    # theirs, keyed by their own unchanged fingerprint. `b` deliberately gets no
    # sidecar file, so the probe is the observable cost (it reads as None).
    b = _write_log(day, "King Ratsar_Sw2_Mage_c3d4.json", [_fight("boss")])
    third = _spy_sidecar_reads(monkeypatch)
    rows = hi.scan_history_dir(tmp_path)
    assert len({r["path"] for r in rows}) == 3, "a log added to a cached day "\
        "was not seen"
    assert third == [b], "adding one log re-read the whole day"


def test_a_log_added_to_a_cached_day_is_seen_even_when_the_folder_mtime_never_moves(
        tmp_path, monkeypatch):
    """The day cache must key on the ENTRY LIST, not the folder's mtime.

    Windows updates a directory's mtime lazily — measured on the dev box, 141 of
    200 file creations left it untouched — so a cache that trusts the mtime
    reuses a day's rows after a new log lands in it and the fight never appears
    in the Fights list. The first version of `history_cache` had exactly that
    bug. This freezes every directory mtime (what the lazy filesystem looks
    like) and asserts the added log is still found: it would fail against the
    mtime-keyed cache and passes against the entry-signature one.
    """
    import farever_companion.ui.combat.history_cache as hc
    import farever_companion.ui.combat.history_index as hi

    monkeypatch.setattr(hc, "_dir_mtime", lambda _p: 12345.0)
    hc.forget_day_cache()

    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    assert len(hi.scan_history_dir(tmp_path)) == 1

    _write_log(day, "King Ratsar_Koban_Rogue_a1b2.json", [_fight("boss")])
    rows = hi.scan_history_dir(tmp_path)
    assert len({r["path"] for r in rows}) == 2, (
        "a log added to a cached day was missed because the folder mtime did "
        "not move")


def _spy_globs(monkeypatch) -> list:
    """Record every directory LISTED by the scan (a glob is a readdir)."""
    globs: list = []
    real = Path.glob

    def spy(self, pattern):
        globs.append(self)
        return real(self, pattern)

    monkeypatch.setattr(Path, "glob", spy)
    return globs


def test_history_days_lists_day_folders_newest_first_without_stats(tmp_path):
    """The cheap half of the scan: day folders by name, no file described.

    The Fights list sizes its day window from this before it describes a single
    log, so it must count only folders that actually hold a log — an empty day
    folder left behind by a delete must not fill the window.
    """
    import farever_companion.ui.combat.history_index as hi

    t1 = time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1))
    t2 = time.mktime((2026, 1, 3, 12, 0, 0, 0, 0, -1))
    _write_log(_log_dir(tmp_path, t1),
               "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(_log_dir(tmp_path, t2),
               "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    (tmp_path / "2026-01" / "04").mkdir(parents=True)      # empty day folder

    d1 = time.strftime("%Y-%m-%d", time.localtime(t1))
    d2 = time.strftime("%Y-%m-%d", time.localtime(t2))
    assert hi.history_days(tmp_path) == [d2, d1]


def test_the_day_window_scan_describes_only_the_kept_days(tmp_path, monkeypatch):
    """A log outside the window costs no sidecar read — the scan-level saving.

    The render window is only "instant" if the scan skips the work too: the
    stamp is checked BEFORE `_file_descriptor`, so an out-of-window log is never
    stat-ed and its sidecar is never opened.
    """
    from pathlib import Path
    import farever_companion.ui.combat.history_index as hi

    t1 = time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1))
    t2 = time.mktime((2026, 1, 3, 12, 0, 0, 0, 0, -1))
    old = _write_log(_log_dir(tmp_path, t1),
                     "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    new = _write_log(_log_dir(tmp_path, t2),
                     "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    day2 = time.strftime("%Y-%m-%d", time.localtime(t2))

    seen: list[Path] = []
    real = hi.read_log_summary
    monkeypatch.setattr(
        hi, "read_log_summary",
        lambda p, m, s: (seen.append(Path(p)), real(p, m, s))[1])

    rows = hi.scan_history_dir(tmp_path, days={day2})
    assert [r["path"] for r in rows] == [new]
    assert seen == [new], "the windowed scan paid for a hidden day's log"
    assert old not in seen


def test_an_old_layout_day_folder_is_carried_by_the_window(tmp_path):
    """Pre-month-split `<YYYY-MM-DD>/` folders are DATED, not undated.

    Reading them as undated (always shown) would defeat the day window on an
    install that upgraded mid-season, when those folders are the whole season.
    The folder name is the day, so the window includes and excludes them like
    the current `<YYYY-MM>/<DD>/` layout.
    """
    import farever_companion.ui.combat.history_index as hi

    _write_log(tmp_path / "2026-01-02",
               "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])

    assert [r["day"] for r in hi.scan_history_dir(tmp_path)] == ["2026-01-02"]
    assert [r["day"] for r in hi.scan_history_dir(
        tmp_path, days={"2026-01-02"})] == ["2026-01-02"]
    assert hi.scan_history_dir(tmp_path, days={"2026-01-03"}) == []
    assert hi.history_days(tmp_path) == ["2026-01-02"]


# --- the Fights search box: read-free, name + sidecar only -------------------

def _fight_killed(**kw):
    d = _fight(**kw)
    d["boss_killed"] = True
    return d


def test_the_search_matches_name_fields_and_sidecar_failure_without_parsing(
        tmp_path, monkeypatch):
    """A keystroke must not undo the lazy index.

    The character, boss and dungeon are read off the file NAME by the scan; the
    failure state is read off the per-log SIDECAR. Nothing is parsed — a log
    with no sidecar has no failure state to match, which is why the last boss
    (failed too, but without a sidecar) drops out of a "failed" search.
    """
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    boss = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    pulls = _write_log(
        day, "Kobolds Mines - Reblochonk trash_Kartik_Warrior_a1b2.json",
        [_fight("trash")])
    killed = _write_log(day, "King Ratsar_Alara_Mage_c3d4.json",
                        [_fight_killed(damage=700.0)])
    noside = _write_log(day, "Ice Queen_Kartik_Warrior_a1b2.json", [_fight("boss")])
    for p in (boss, pulls, killed):
        _write_sidecar(p)                 # noside deliberately has none
    reads = _spy_reads(monkeypatch)

    def shown(q):
        host._cp_fights_query = q
        host._render_past_fights(force=True)
        return [e["row"]["path"] for e in host._cp_log_rows]

    assert sorted(shown("kobolds")) == [pulls]           # dungeon
    assert sorted(shown("reblochonk")) == [pulls]        # boss
    assert sorted(shown("kartik")) == sorted([boss, pulls, noside])  # character
    assert sorted(shown("kartik reblochonk")) == [pulls]  # terms narrow (AND)
    # failure comes from the SIDECAR: the killed boss and the sidecar-less boss
    # both drop out, even though the latter is a failed boss too
    assert sorted(shown("failed")) == [boss]
    assert sorted(shown("wipe")) == [boss]
    assert sorted(shown("")) == sorted([boss, pulls, killed, noside])
    assert reads == [], "the search parsed a log"

    # typing in the box drives the same filter (the box lives outside the tree)
    host.cp_fights_search.setText("alara")
    assert host._cp_fights_query == "alara"
    assert [e["row"]["path"] for e in host._cp_log_rows] == [killed]

    # a query that matches nothing says so WITHOUT hiding the box to clear it
    host.cp_fights_search.setText("nothing-matches-this")
    assert host._cp_log_rows == []
    assert not host.cp_fights_search.isHidden()


def test_the_search_box_does_not_read_a_log_to_answer(tmp_path, monkeypatch):
    """The whole point: the index stays lazy while the box filters a season."""
    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    for i in range(5):
        _write_log(day, f"Boss{i}_Kartik_Warrior_a1b2.json", [_fight("boss")])
    reads = _spy_reads(monkeypatch)

    host.cp_fights_search.setText("boss3")
    assert len(host._cp_log_rows) == 1
    host.cp_fights_search.setText("")
    assert len(host._cp_log_rows) == 5
    assert reads == []


def test_the_char_label_uses_the_shared_slug_and_token_sets(tmp_path):
    """The index recovers a per-boss label through `boss_file_slug`, not its own
    re-derivation of the underscore boundary, the outcome token and the class.

    Re-parsing is how the index drifts from the writer: a naming change (a new
    outcome token, the class appended after the hash) would leave the row
    reading a different character than the one that wrote the file. These are
    the shapes the writer emits, and each must reduce to the writer's slug.
    """
    from farever_companion.core import dps_tracker_history as dth
    import farever_companion.ui.combat.history_index as hi

    # the index uses the WRITER's token sets, not copies of them
    assert hi._MODE_TOKENS is dth.MODE_TOKENS
    assert hi._OUTCOME_TOKENS is dth.OUTCOME_TOKENS

    day = tmp_path / "2026-10" / "02"
    cases = {
        # a plain per-boss name
        "Kobolds Mines - Reblochonk_Kartik_Warrior_a1b2_PASS.json":
            "Kartik Warrior A1b2",
        # outcome + difficulty tail
        "Kobolds Mines - Reblochonk_Kartik_Warrior_a1b2_FAILED_HARD.json":
            "Kartik Warrior A1b2",
        # the class `file_for` appends AFTER the hash
        "Kobolds Mines - Reblochonk_Kartik_a1b2_Warrior_PASS.json":
            "Kartik A1b2",
        # the account hash is not part of the name
        "Kobolds Mines - Reblochonk_Kartik_Warrior_S8c4ba8_PASS.json":
            "Kartik Warrior",
        # a bare boss name with a slug, no class/outcome
        "Reblochonk_Kartik_Warrior_a1b2.json":
            "Kartik Warrior A1b2",
    }
    for name, want in cases.items():
        path = day / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"history": []}', encoding="utf-8")
        assert dth.boss_file_slug(path.stem), name      # the shared helper sees it
        assert hi._char_label_from_history_path(path) == want, name


# --- deleting archived fights: the file goes when its last fight does --------

def _yes_to_confirm(monkeypatch):
    """Answer every confirm dialog Yes without opening one."""
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))


def test_deleting_every_fight_removes_the_log_and_its_empty_folders(
        tmp_path, monkeypatch):
    """A log whose LAST fight is deleted is removed, not left as an empty stub.

    Rewriting `{"history": []}` left a valid JSON file every scanner kept
    listing — an empty row, a husk on disk and an empty day folder that survived
    on disk while the app showed nothing (live 2026-10-05: "it renders empty
    folders and the files are still on the pc"). The file, its sidecar and any
    folder the removal empties all go.
    """
    import farever_companion.ui.combat.past_fights as pf
    import farever_companion.ui.combat.history_index as hi
    from farever_companion.ui.combat.history_index import (
        load_history_sessions, write_log_summary)

    _yes_to_confirm(monkeypatch)
    day = tmp_path / "2026-10" / "02"
    log = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                     [_fight("boss", start=1000.0),
                      _fight("boss", start=2000.0)])
    sessions = load_history_sessions(log)
    assert write_log_summary(log, sessions) is not None
    sidecar = Path(str(log) + ".summary")
    assert sidecar.exists()

    assert pf.delete_past_fights_batch(
        None, [(log, s) for s in sessions], active_tracker=None) is True

    assert not log.exists(), "the emptied log file stayed on disk"
    assert not sidecar.exists(), "the log's sidecar outlived its log"
    assert not day.exists(), "the emptied day folder stayed"
    assert not day.parent.exists(), "the emptied month folder stayed"
    assert hi.scan_history_dir(tmp_path) == []


def test_deleting_one_of_several_fights_keeps_the_log_with_the_rest(
        tmp_path, monkeypatch):
    """Only the selected fight goes; the file survives while it still holds one."""
    import farever_companion.ui.combat.past_fights as pf
    from farever_companion.ui.combat.history_index import load_history_sessions

    _yes_to_confirm(monkeypatch)
    day = tmp_path / "2026-10" / "02"
    log = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                     [_fight("boss", start=1000.0),
                      _fight("boss", name="Other", start=2000.0,
                             target="👑 Other")])
    sessions = load_history_sessions(log)

    assert pf.delete_past_fights_batch(
        None, [(log, sessions[0])], active_tracker=None) is True

    assert log.exists(), "the log still held a fight but was deleted"
    assert day.exists(), "the folder still holds the surviving log"
    kept = load_history_sessions(log)
    assert [s.start_time for s in kept] == [2000.0]


def test_deleting_the_only_fight_removes_the_file(tmp_path, monkeypatch):
    """The single-fight trash path follows the same rule as the batch one."""
    import farever_companion.ui.combat.past_fights as pf
    from farever_companion.ui.combat.history_index import load_history_sessions

    _yes_to_confirm(monkeypatch)
    day = tmp_path / "2026-10" / "02"
    log = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    sess = load_history_sessions(log)[0]

    assert pf.delete_past_fight(None, log, sess, active_tracker=None) is True
    assert not log.exists(), "the emptied log file stayed on disk"
    assert not day.exists(), "its empty day folder stayed"


# --- Select All: the selection must be visible -----------------------------

def test_select_all_reveals_every_fight_it_selects(tmp_path, monkeypatch):
    """Select All must SHOW what it will delete, not select hidden fights.

    It used to read every listed log and select their fights while leaving the
    logs collapsed, so a bulk delete could remove fights hidden inside rows the
    player never opened (live 2026-10-05: "select all ... looks like it got rid
    of them all"). Ticking it now expands every log that holds a matching
    fight, so the rows about to be deleted are on screen.
    """
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(day, "Queen Vex_Kartik_Warrior_a1b2.json",
               [_fight("boss", name="Queen Vex", target="👑 Queen Vex")])
    host._render_past_fights()

    cb = next(c for c in host._cp_fights_bar["widget"].findChildren(
        QtWidgets.QCheckBox) if c.text() == "Select All")
    cb.setChecked(True)                     # fires on_select_all

    # both logs are open, and every fight they hold is visible and selected
    assert len(host._cp_log_rows) == 2
    assert all(e["expanded"] and not e["holder"].isHidden()
               for e in host._cp_log_rows), "a selected log stayed collapsed"
    assert len(host._cp_fight_rows) == 2
    assert all(st["selected"] for st in host._cp_fight_rows)
    assert len(_visible_fight_rows(host)) == 2

    # unchecking deselects every fight again
    cb.setChecked(False)
    assert not any(st["selected"] for st in host._cp_fight_rows)


def test_select_all_leaves_a_log_with_nothing_matching_closed(tmp_path, monkeypatch):
    """A log the active filter hides carries no selection, so it stays shut."""
    from PySide6 import QtWidgets

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json", [_fight("boss")])
    _write_log(day, "trash_Kartik_Warrior_a1b2.json", [_fight("trash")])
    host._cp_fights_filter = "boss"
    # The trash row folds under this filter, so ask for it: Select All still has
    # to leave it closed once it IS drawn.
    _reveal_folded(host)
    host._render_past_fights()

    cb = next(c for c in host._cp_fights_bar["widget"].findChildren(
        QtWidgets.QCheckBox) if c.text() == "Select All")
    cb.setChecked(True)

    trash = next(e for e in host._cp_log_rows
                 if e["row"]["kind"] == "trash")
    assert not trash["expanded"], "Select All opened a log with no matching fight"
    assert len(host._cp_fight_rows) == 1
    assert host._cp_fight_rows[0]["selected"]


def test_every_fight_row_carries_its_selection_box(tmp_path, monkeypatch):
    """Variant C's `cb` column: a fight SHOWS whether it is selected.

    Selection used to be invisible - a click tinted the row and nothing else, so
    "what will Delete Selected (N) take?" could only be read off the toolbar
    count. The box is the visible half of that same state: it starts unchecked,
    Select All checks it, ticking it changes the selection, and a click on the
    row moves it back - one state, reached from either side.
    """
    from PySide6 import QtWidgets
    from tests.qt_helpers import click

    host = _fights_host(tmp_path, monkeypatch)
    day = tmp_path / "2026-10" / "02"
    log = _write_log(day, "King Ratsar_Kartik_Warrior_a1b2.json",
                     [_fight("boss"), _fight("boss", name="Queen Vex")])
    host._render_past_fights()
    entry, _row = _log_row(host, log)
    host._toggle_log_row(entry)

    def box_of(row):
        return row.findChild(QtWidgets.QCheckBox, "PastFightSelect")

    rows = _visible_fight_rows(host)
    assert len(rows) == 2, "the log did not expand into its fights"
    for r in rows:
        assert box_of(r) is not None, "a fight row has no selection box"
        assert not box_of(r).isChecked()

    # Select All reaches the boxes (it sets the very state the box draws)
    bar_cb = next(c for c in host._cp_fights_bar["widget"].findChildren(
        QtWidgets.QCheckBox) if c.text() == "Select All")
    bar_cb.setChecked(True)
    assert len(host._cp_fight_rows) == 2
    assert all(box_of(r).isChecked() for r in rows)

    # ...ticking ONE box deselects exactly that fight
    box_of(rows[0]).setChecked(False)
    assert sum(1 for st in host._cp_fight_rows if st["selected"]) == 1

    # ...and a click on the ROW moves the box, because the row's own repaint is
    # what syncs the two
    click(rows[0])
    assert box_of(rows[0]).isChecked()
    assert all(st["selected"] for st in host._cp_fight_rows)


def test_a_killed_boss_fight_says_so_on_its_own_row(tmp_path, monkeypatch):
    """Variant C's outcome chip: KILLED is the other half of FAILED.

    A wipe already said FAILED on its row; the kill had no marker at all, so the
    list could not be scanned for the fights that went right. Gated on the KIND,
    because `was_killed` is a fact about the BOSS: a trash pull that lulls out
    killed nothing, whatever the archive's name claims.
    """
    def labels_of_one_fight(day_name, filename, fight):
        """One log, one fight, on its own day: no list ORDER to assume.

        Fight rows come back in their holder's order, not in the order the logs
        were opened, so expanding three logs in one list and then indexing the
        rows reads whichever one the tree happens to list first (found by this
        test failing). A day per case leaves exactly one row to look at.
        """
        host = _fights_host(tmp_path, monkeypatch)
        day = tmp_path / "2026-10" / day_name
        _write_log(day, filename, [fight])
        host._render_past_fights()
        entry, _row = _log_row(host, day / filename)
        host._toggle_log_row(entry)
        rows = _visible_fight_rows(host)
        assert len(rows) == 1, (filename, len(rows))
        return _row_labels(rows[0])

    assert "✔ KILLED" in labels_of_one_fight(
        "02", "King Ratsar_Kartik_Warrior_a1b2.json",
        _fight("boss", name="Boss Fight (Boss Defeated)"))
    # a wipe keeps the outcome it already had, and gets no kill chip
    wipe = labels_of_one_fight("03", "Queen Vex_Kartik_Warrior_a1b2.json",
                               _fight("boss", name="Boss Fight (Died)"))
    assert "✔ KILLED" not in wipe
    assert "FAILED" in wipe
    # a trash pull whose name claims a kill: the KIND gate is what stops it
    assert "✔ KILLED" not in labels_of_one_fight(
        "04", "Trash pack Queen Vex_Kartik_Warrior_a1b2.json",
        _fight("trash", name="Boss Fight (Boss Defeated)"))
