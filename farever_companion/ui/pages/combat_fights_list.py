"""The Past Fights list on the Combat & DPS page.

The list is a tree of DATES → LOGS → FIGHTS, and it is lazy at every level:

- `scan_history_dir` lists the logs from their names alone, so building the
  whole list costs a readdir and a stat per file;
- a log's fights appear only when its row is EXPANDED, and expanding one reads
  exactly that one file (`load_history_sessions`);
- a log that has never been expanded has never been parsed, so a season of
  archives opens as fast as an empty folder.

It lives here rather than in `combat_history.py` because that mixin also owns
the encounter combo, the history banner and the bulk-log cleanup, and the
AGENTS.md budget measures one file. `CombatHistoryMixin` inherits this, so no
call site moved.

Read-free is not only a performance rule here - it is what a row is allowed to
CLAIM. Every collapsed row states what the current filter will find inside it
(`_row_verdict`), from its file name and its sidecar, and every expanded holder
ends in either fight rows or one line saying why it has none - so no click can
land on a blank body (live 2026-10-06: "empty rows that I need to expand to see
nothing in them").
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .. import theme
from ...data import icons, units as udata
from ...core.dps_records import fight_failed, was_killed
from ...config import dps_dir
from .combat_fights_parts import (
    CombatFightsToolbarMixin,
    _FIGHTS_RECENT_DAYS,
    log_matches_query,
)
from ..combat import (
    history_days,
    scan_history_dir,
    cached_history_sessions,
    summary_line,
    _clean_session_name,
    _KIND_STYLE,
)

# Past-fights kind filter labels ("all" shows every archived fight).
_FIGHTS_FILTER_LABELS = {
    "all": "All",
    "boss": "Boss",
    "trash": "Trash",
    "dummy": "Test Dummy",
    "failed": "Failed",
}

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# The difficulty chip on a log row, matching the badge colours the live
# surfaces use (normal dim, hard orange, heroic gold).
_MODE_STYLE = {"normal": theme.DIM, "hard": theme.ORANGE,
               "heroic": theme.GOLD}

# The default day window AND its "show all days" control live in the toolbar
# mixin (imported above); `_FIGHTS_RECENT_DAYS` is re-exported here so call
# sites and tests can keep reading it from this module. The window is a user
# setting — see `CombatFightsToolbarMixin._fights_recent_days`.


def _day_of(row: dict) -> str:
    """The date a log belongs to, for its day header.

    The archive folder IS the date (`<YYYY-MM>/<DD>/`), so the folder wins; a
    legacy flat file has no folder and falls back to its mtime, which is the
    only date it has.
    """
    day = row.get("day")
    if day:
        return str(day)
    mtime = row.get("mtime") or 0.0
    return (time.strftime("%Y-%m-%d", time.localtime(mtime))
            if mtime else "Unknown date")


def _day_heading(day: str) -> str:
    """A day header that says the weekday too — "which evening was that?" is
    the question a list of fights is actually asked."""
    try:
        t = time.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{day}  ·  {_WEEKDAYS[t.tm_wday]}"


#: The verdict a collapsed log row carries about the ACTIVE kind filter:
#: `has` = the log certainly holds a fight this filter keeps; `none` = certainly
#: none, so opening it shows nothing; `unknown` = the name cannot answer it, and
#: the honest render of "cannot answer it" is a normal, expandable row.
#:
#: It is decided from the file NAME (`_file_descriptor`) plus the sidecar - the
#: two things the scan already has - because this view is read-free by
#: construction: a badge that parsed every log to draw itself would throw away
#: exactly what the lazy scan buys (`scan_history_dir`).
#:
#: `none` and `empty` HINT (a muted chip, a dim chevron); they never lock the row
#: shut. The verdict is a claim about a file NAME, and a naming convention must
#: not be able to hide a real fight - so the row still opens, and the holder then
#: says why it is bare.
#: The per-fight selection box - Variant C's `cb` column. Qt draws a native
#: indicator that ignores the app palette, so the box, its hover and its checked
#: state are spelled out here, sized to the chevron it sits under.
_FIGHT_SELECT_QSS = (
    f"QCheckBox {{ background: transparent; margin: 0px; padding: 0px; }}"
    f"QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid "
    f"{theme.BORDER}; border-radius: 3px; background: {theme.PANEL_LOW}; }}"
    f"QCheckBox::indicator:hover {{ border-color: {theme.ACCENT}; }}"
    f"QCheckBox::indicator:checked {{ background: {theme.ACCENT}; "
    f"border-color: {theme.ACCENT_LIGHT}; }}")

_VERDICT_HAS = "has"
_VERDICT_NONE = "none"
_VERDICT_UNKNOWN = "unknown"
#: The one state that outranks the filter verdict: a sidecar that counted no
#: fight at all makes a log empty whatever the filter is asking for.
_VERDICT_EMPTY = "empty"

#: The kinds a log's NAME can vouch for. `overall` is deliberately absent: it is
#: the legacy day-wide file (`dps_history*.json`), which holds every kind, so its
#: name answers nothing about a filter.
_NAME_SAFE_KINDS = frozenset({"boss", "trash", "dummy"})


def _sidecar_fights(row: dict) -> "int | None":
    """The fight count the log's sidecar reports, or None when there is none.

    None is "not measured", never zero: a log with no sidecar has not been
    counted, and reading it to find out is the read this list exists to avoid.
    """
    summary = row.get("summary")
    if not isinstance(summary, dict):
        return None
    return int(summary.get("fights") or 0)


def _day_count_text(day_rows) -> str:
    """The count a day header carries - read off the rows, never off a log.

    The fight total is drawn only when EVERY log on the day has a sidecar;
    otherwise it would be a partial sum dressed as a total, which is worse than
    the plain log count alone.
    """
    n = len(day_rows)
    text = f"{n} log" + ("" if n == 1 else "s")
    counts = [_sidecar_fights(r) for r in day_rows]
    if counts and all(c is not None for c in counts):
        fights = sum(counts)
        if fights:
            text += f" · {fights} fight" + ("" if fights == 1 else "s")
    return text


def _filter_verdict(row: dict, filter_kind: str) -> str:
    """What the active kind filter will find in this log, from the row alone.

    Conservative by construction: every `none` is a claim the file NAME or the
    sidecar can actually support, and anything they cannot answer is `unknown` -
    which renders exactly like today's row. Two of those are worth naming:

    * a per-boss log under `Failed` is `unknown` even when its sidecar reports no
      failure. `summarize` counts only fights that dealt damage, so a wipe with
      nothing on the board leaves no trace in the sidecar: `has_failure` can
      prove a fail, never its absence.
    * the Test Dummy filter rules out nothing but a `dummy`-named log. Dummy
      engagement has been missed before and the fight routed to the trash
      session (see `_fight_matches_filter`), so only the name is certain.
    """
    if filter_kind == "all":
        return _VERDICT_HAS            # nothing is hidden, so nothing is empty
    kind = (row.get("kind") or "overall").lower()
    if kind not in _NAME_SAFE_KINDS:
        return _VERDICT_UNKNOWN
    if filter_kind == "dummy":
        return _VERDICT_HAS if kind == "dummy" else _VERDICT_UNKNOWN
    if filter_kind == "failed":
        if kind != "boss":
            return _VERDICT_NONE       # only a boss fight can fail
        summary = row.get("summary")
        if isinstance(summary, dict) and summary.get("has_failure"):
            return _VERDICT_HAS
        return _VERDICT_UNKNOWN
    if filter_kind in ("boss", "trash"):
        return _VERDICT_HAS if kind == filter_kind else _VERDICT_NONE
    return _VERDICT_UNKNOWN


def _row_verdict(row: dict, filter_kind: str) -> str:
    """A row's verdict, with an empty log outranking the filter's question."""
    if _sidecar_fights(row) == 0:
        return _VERDICT_EMPTY
    return _filter_verdict(row, filter_kind)


class CombatFightsListMixin(CombatFightsToolbarMixin):
    """Date-grouped, expand-on-demand archive list for the Fights view."""

    # --- the list ----------------------------------------------------------#

    def _render_past_fights(self, force: bool = False) -> None:
        """Render DATES → LOGS, each log expandable into its fights.

        Nothing on disk is parsed to draw this: the rows come from the lazy
        index and a log is read the first time it is expanded. The rebuild is
        skipped when the folder has not changed, and the fingerprint is taken
        from the FILE LIST (path/mtime/size) rather than the fights inside —
        measuring it from parsed sessions is exactly the read this view exists
        to avoid.
        """
        # DAY WINDOW, decided BEFORE the scan: the list opens on the newest
        # `recent` days (a user setting, falling back to `_FIGHTS_RECENT_DAYS`),
        # and a log outside that window is never described — no stat, no
        # sidecar read — so a season costs a folder listing plus the visible
        # days, not every archive ever written. The window is a per-session
        # preference on the instance, so it survives the folder-change rebuilds
        # that recreate the whole list.
        all_days = history_days(dps_dir())          # dated folders, newest first
        recent = self._fights_recent_days()         # user setting (0 = all)
        show_all = bool(getattr(self, "_cp_fights_show_all_days", False))
        window = (None if (show_all or recent <= 0)
                  else set(all_days[:recent]))
        rows = scan_history_dir(dps_dir(), days=window)
        # The search box is visible whenever the FOLDER has logs, not whenever
        # the query matched some — a query matching nothing must not hide the
        # very field needed to clear it.
        search = getattr(self, "cp_fights_search", None)
        if search is not None:
            search.setVisible(bool(rows))
        filter_kind = getattr(self, "_cp_fights_filter", "all")
        # SEARCH: read-free. Matched against the row the scan already built —
        # the character/boss/dungeon off the file NAME and the failure flag off
        # the sidecar — so a keystroke never parses a log (combat_fights_parts).
        query = getattr(self, "_cp_fights_query", "") or ""
        if query:
            rows = [r for r in rows if log_matches_query(r, query)]

        past_fp = (filter_kind, show_all, query,
                   tuple(sorted((str(r["path"]), r.get("mtime", 0.0),
                                 r.get("size", 0)) for r in rows)))
        if not force and getattr(self, "_last_past_fights_fp", None) == past_fp:
            return
        self._last_past_fights_fp = past_fp

        self._clear_past_fights_body()

        if not rows:
            if query:
                msg = (f"No logs match \"{query}\". The search reads only "
                       f"each log's name and its sidecar summary, so a log "
                       f"with no sidecar has no failure state to match.")
                self.cp_empty_lbl.setVisible(False)
            else:
                msg = ("No archived fights found in moddata yet. Finish a "
                       "fight and it is saved here automatically (per "
                       "character).")
                self.cp_empty_lbl.setVisible(True)
            self.cp_content_lay.addWidget(QtWidgets.QLabel(msg))
            self._pin_rows_to_top()
            return
        self.cp_empty_lbl.setVisible(False)

        # The days actually present among the scanned rows (an undated legacy
        # file adds its mtime day), newest first. `day_total` is the DATED
        # folder count from the cheap listing, so the day-window control can
        # name how many days it is holding back without reading them.
        shown_days = sorted({_day_of(r) for r in rows}, reverse=True)
        bar = self._build_fights_toolbar(filter_kind, day_total=len(all_days),
                                         show_all=show_all)
        # ABOVE the scroll area, not inside it: the controls have to stay put
        # while the log rows scroll past them (see `_place_fights_toolbar`).
        self._place_fights_toolbar(bar["widget"])
        self._cp_fights_bar = bar

        # One row per log, all sharing this render's filter, and NO shared
        # "shown" counter between them. That counter is what broke the list: it
        # totalled fights across EVERY log, so the single list-level "nothing
        # matched" note was hidden the moment some OTHER log contributed a row -
        # and every non-matching log after it expanded onto a blank holder. A
        # fact about one row is now stated on that row (`_fill_log_children`),
        # which needs no state to share and cannot be switched off by a
        # neighbour. The day header names the day's own count, off the rows.
        # PHASE 2 FOLD: with a kind filter on, a log its NAME rules out (`none`)
        # is not drawn at all - one quiet line per DAY stands in for it, so the
        # list stops showing rows the filter has nothing for. Three verdicts are
        # deliberately left alone: `all` folds nothing (nothing is filtered),
        # `unknown` folds nothing (the verdict's whole point is that a name which
        # cannot answer stays visible), and `empty` folds nothing (that is a fact
        # about the LOG, said on its row under every filter, and it is what
        # Phase 4's cleanup would act on).
        #
        # Per DAY rather than one line for the whole list: the fold replaces rows
        # inside their own group, so a day whose logs all fold still shows its
        # header and count instead of a heading over nothing.
        show_none = bool(getattr(self, "_cp_fights_show_nomatch", False))
        for day in shown_days:
            day_rows = [r for r in rows if _day_of(r) == day]
            self._add_day_header(_day_heading(day), _day_count_text(day_rows))
            # The count is what the day HOLDS, not what the fold took, so the
            # line is still drawn while those rows are revealed - that is the
            # only way back: the same line, reading `hide`, folds them again.
            none_rows = 0
            for r in day_rows:
                verdict = _row_verdict(r, filter_kind)
                if verdict == _VERDICT_NONE and filter_kind != "all":
                    none_rows += 1
                    if not show_none:
                        continue
                self._add_log_row(r, filter_kind, verdict)
            if none_rows:
                self._add_fold_row(filter_kind, none_rows, show_none)
        self._pin_rows_to_top()
        bar["update"]()
        self.cp_empty_lbl.setVisible(False)

    def _on_fights_query_changed(self, text: str) -> None:
        """A keystroke in the search box re-filters the list.

        No typing debounce: the render is bounded to the visible day window,
        and the search box lives OUTSIDE the re-rendered tree (see
        `_build_past_fights_view`), so the rebuild cannot steal focus from it.
        """
        self._cp_fights_query = text or ""
        self._render_past_fights(force=True)

    def _clear_past_fights_body(self) -> None:
        """Drop everything but the persistent empty-state label.

        Spacers included - see the note inside.
        """
        lay = self.cp_content_lay
        for i in reversed(range(lay.count())):
            item = lay.itemAt(i)
            w = item.widget()
            if w is not None and w is self.cp_empty_lbl:
                continue
            # A SPACER has to go too, not just a widget: the stretch this render
            # adds is what pins the rows to the top (`_pin_rows_to_top`), and one
            # left behind per rebuild would accumulate stretch on stretch.
            lay.takeAt(i)
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        # The bar lives OUTSIDE this layout, so it is not caught above.
        self._place_fights_toolbar(None)
        self._cp_fight_rows: list[dict] = []
        self._cp_log_rows: list[dict] = []
        self._cp_fights_bar = None

    def _pin_rows_to_top(self) -> None:
        """Keep the body's rows at the TOP of the viewport.

        The scroll area is `setWidgetResizable(True)`, so the container is
        stretched to fill the viewport and its layout hands the leftover height
        to whatever will take it. At a 760px window the list holds two logs, so
        there is a lot of leftover: with nothing to claim it the day header
        floated ~50px down the pane and the two rows sat ~100px apart, and every
        rebuild moved them again (2026-10-05, live). The stretch is the
        tie-breaker - it is the only item with no maximum and no fixed height, so
        every spare pixel goes to the bottom and the rows stack tight from the
        top, whatever the window size.
        """
        self.cp_content_lay.addStretch(1)

    # --- rows --------------------------------------------------------------#

    def _add_day_header(self, text: str, count: str = "") -> None:
        """One day's label, a hairline rule, and how much the day holds.

        The header used to be a lone em-dash-flanked date label: decoration that
        said nothing about the day. It now leads with the date and the weekday,
        runs a quiet rule to the right, and names the day's own log count - read
        off the rows this render already has, so no log is opened to draw it.
        The objectName is what the day-window tests look for, so the rule and the
        count can change without a test reading the date out of a string.
        """
        head = QtWidgets.QFrame()
        head.setObjectName("PastFightsDayHeader")
        head.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Fixed)
        head.setStyleSheet(
            "QFrame#PastFightsDayHeader { background: transparent; "
            "border: none; }")
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(2, 10, 2, 3)
        hl.setSpacing(8)

        day_lbl = QtWidgets.QLabel(text)
        day_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 800; letter-spacing: 1px; "
            f"color: {theme.MUTED}; background: transparent;")
        hl.addWidget(day_lbl)

        rule = QtWidgets.QFrame()
        rule.setObjectName("PastFightsDayRule")
        rule.setFixedHeight(1)
        rule.setStyleSheet(
            f"QFrame#PastFightsDayRule {{ background: {theme.BORDER}; "
            f"border: none; }}")
        hl.addWidget(rule, 1)

        if count:
            count_lbl = QtWidgets.QLabel(count)
            count_lbl.setStyleSheet(
                f"font-size: 10.5px; color: {theme.DIM}; background: "
                f"transparent;")
            hl.addWidget(count_lbl)
        self.cp_content_lay.addWidget(head)

    def _add_fold_row(self, filter_kind: str, count: int,
                      shown: bool) -> None:
        """One quiet line standing in for the rows the filter has nothing for.

        `3 logs with no Trash  ·  show`, and once clicked the same line reads
        `hide` and every one of those rows is drawn again. It is a per-INSTANCE
        toggle (`_cp_fights_show_nomatch`), the same pattern as the day-window
        control, because a folder-change rebuild recreates the whole list and the
        choice has to survive it.

        Only `none` rows fold, and the line's wording is exact because of it: a
        row is here when its NAME (boss/trash/dummy, off `_file_descriptor`)
        cannot hold what the filter asks for, and the writer files a log by its
        fight kinds - the same trust the row's own `no <Kind>` chip already asks
        for. That is also what keeps Select All honest: a folded row holds no
        fight the filter keeps, so it contributes nothing to a selection over
        this filter (see `_expand_every_log`).
        """
        label = _FIGHTS_FILTER_LABELS.get(filter_kind, filter_kind)
        text = (f"{count} log" + ("" if count == 1 else "s")
                + f" with no {label}" + ("  ·  hide" if shown else "  ·  show"))
        btn = QtWidgets.QPushButton(text)
        btn.setObjectName("PastFightsFoldRow")
        btn.setCheckable(True)
        btn.setChecked(shown)
        btn.setFlat(True)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton#PastFightsFoldRow {{ background: transparent; "
            f"color: {theme.DIM}; border: none; text-align: left; "
            f"padding: 6px 2px 6px 32px; font-size: 10.5px; font-style: "
            f"italic; }}"
            f"QPushButton#PastFightsFoldRow:hover {{ "
            f"color: {theme.ACCENT_LIGHT}; }}")
        btn.setToolTip(
            f"This {label} filter has nothing in {count} of the logs on this "
            f"day. Click to list them anyway.")
        btn.clicked.connect(self._toggle_nomatch_rows)
        self.cp_content_lay.addWidget(btn)

    def _toggle_nomatch_rows(self, checked: bool) -> None:
        """Reveal (or fold away again) the rows the active filter has nothing for.

        A per-instance preference like `_cp_fights_show_all_days`, so the
        folder-change rebuilds - which recreate the whole list - keep it.
        `force` because only the fold moved: the file fingerprint the render
        compares is identical, so a plain render would return early.
        """
        self._cp_fights_show_nomatch = bool(checked)
        self._render_past_fights(force=True)

    def _add_log_row(self, r: dict, filter_kind: str, verdict: str) -> None:
        """One archived LOG, collapsed - nothing has been read for this row.

        `filter_kind` and `verdict` are this render's, taken once and handed down
        rather than re-read per row: every row of one render belongs to the same
        filter, and the verdict (descriptor + sidecar, via `_row_verdict`) is the
        SAME value the fold decision used - so the chip a row draws and the
        reason it was not folded cannot disagree. The tooltip repeats every label
        the row draws, the chip included.
        """
        kind = (r.get("kind") or "overall").lower()
        k_col, k_txt = _KIND_STYLE.get(kind, (theme.ACCENT, kind.upper()))
        # A row the current filter cannot use, and a log with nothing in it, both
        # get the dim chevron. DIMMED, not disabled: the verdict is read off a
        # file name, so the row still opens - and the holder then says why it is
        # bare instead of being blank.
        dim_chev = verdict in (_VERDICT_NONE, _VERDICT_EMPTY)
        name = r.get("name") or r["path"].stem
        when = (time.strftime("%H:%M", time.localtime(r["mtime"]))
                if r.get("mtime") else "--")
        size = int(r.get("size") or 0)
        size_txt = f"{size / 1024:.0f} KB" if size >= 1024 else f"{size} B"

        log_w = QtWidgets.QFrame()
        log_w.setObjectName("PastFightLogRow")
        log_w.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                            QtWidgets.QSizePolicy.Fixed)
        # One rhythm for the whole list: the same minimum height whether the
        # aggregate line is filled in or says it has no sidecar yet, so the list
        # stops stepping as rows with and without one alternate.
        log_w.setMinimumHeight(40)
        log_w.setCursor(QtCore.Qt.PointingHandCursor)
        log_w.setStyleSheet(
            f"QFrame#PastFightLogRow {{ background: {theme.PANEL_LOW}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
            f"QFrame#PastFightLogRow:hover {{ background: {theme.PANEL_HI}; "
            f"border-color: {theme.ACCENT}; }}")

        h = QtWidgets.QHBoxLayout(log_w)
        h.setContentsMargins(10, 6, 12, 6)
        h.setSpacing(8)

        chev = QtWidgets.QLabel("▸")
        chev.setFixedWidth(14)
        chev.setStyleSheet(
            f"font-size: 12px; font-weight: 900; "
            f"color: {theme.DIM if dim_chev else theme.ACCENT_LIGHT}; "
            f"background: transparent;")
        h.addWidget(chev)

        name_lbl = QtWidgets.QLabel(name)
        name_col = {_VERDICT_EMPTY: theme.DIM,
                    _VERDICT_NONE: theme.MUTED}.get(verdict, theme.TEXT)
        name_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 800; color: {name_col}; "
            f"background: transparent;")
        h.addWidget(name_lbl)

        kind_lbl = QtWidgets.QLabel(k_txt)
        kind_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {k_col}; "
            f"background: {theme.with_alpha(k_col, 28)}; border-radius: 3px; "
            f"padding: 1px 5px;")
        h.addWidget(kind_lbl)
        # The difficulty the log was written at, when the game named it (it is
        # the token in the FILE NAME, shown here so the list reads without
        # opening anything).
        mode = (r.get("mode") or "").upper()
        if mode:
            mode_lbl = QtWidgets.QLabel(mode)
            mode_col = _MODE_STYLE.get(mode.lower(), theme.MUTED)
            mode_lbl.setToolTip(
                f"Archived at {mode} difficulty (from the game's own read)")
            mode_lbl.setStyleSheet(
                f"font-size: 9px; font-weight: 800; color: {mode_col}; "
                f"background: {theme.with_alpha(mode_col, 24)}; "
                f"border-radius: 3px; padding: 1px 5px;")
            h.addWidget(mode_lbl)
        # The verdict chip: what the current filter will find in here, said
        # BEFORE the row is opened. Only a verdict the name or the sidecar can
        # support gets a chip - `unknown` stays bare on purpose, because a chip
        # backed by nothing is exactly the lie this pass exists to remove.
        verdict_txt = ""
        if verdict == _VERDICT_EMPTY:
            verdict_txt = "empty log"
        elif verdict == _VERDICT_NONE:
            verdict_txt = ("no " + _FIGHTS_FILTER_LABELS.get(filter_kind,
                                                            filter_kind))
        if verdict_txt:
            verdict_lbl = QtWidgets.QLabel(verdict_txt)
            v_col = theme.DIM if verdict == _VERDICT_EMPTY else theme.MUTED
            verdict_lbl.setStyleSheet(
                f"font-size: 9px; font-weight: 800; color: {v_col}; "
                f"background: {theme.with_alpha(theme.BORDER, 70)}; "
                f"border-radius: 3px; padding: 1px 5px;")
            h.addWidget(verdict_lbl)

        # The aggregate slot is ALWAYS drawn. Fight count / best damage /
        # duration come from the log's sidecar - the row's whole point is to be
        # drawable without opening the log - but a row that silently DROPS the
        # line reads as a row that lost its data, which is the other half of
        # "this list looks broken". So a sidecar that has not been written says
        # so, and a sidecar that counted nothing says that instead.
        agg = summary_line(r.get("summary"))
        if agg:
            agg_txt, agg_col = agg, theme.MUTED
        elif verdict == _VERDICT_EMPTY:
            agg_txt, agg_col = "no fights recorded", theme.DIM
        else:
            agg_txt, agg_col = "no summary yet", theme.DIM
        agg_lbl = QtWidgets.QLabel(agg_txt)
        agg_lbl.setStyleSheet(
            f"font-size: 10px; color: {agg_col}; "
            f"font-family: monospace; background: transparent;")
        h.addWidget(agg_lbl)
        h.addStretch(1)

        char_lbl = QtWidgets.QLabel(f"[{r.get('char', '')}]")
        char_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 700; "
            f"color: {theme.ACCENT_LIGHT}; background: transparent;")
        h.addWidget(char_lbl)
        meta_lbl = QtWidgets.QLabel(f"{when} · {size_txt}")
        meta_lbl.setStyleSheet(
            f"font-size: 10.5px; color: {theme.DIM}; font-family: monospace; "
            f"background: transparent;")
        h.addWidget(meta_lbl)

        # The expanded fights hang off a left rail in the chevron's own column,
        # so the body reads as belonging to the row above it and the whole tree
        # keeps one left edge. A scoped selector, so the rule cannot leak onto
        # the fight rows inside (QLabel and QFrame both descend from QFrame).
        holder = QtWidgets.QFrame()
        holder.setObjectName("PastFightHolder")
        holder.setStyleSheet(
            f"QFrame#PastFightHolder {{ background: transparent; "
            f"border-left: 2px solid {theme.BORDER}; }}")
        holder_lay = QtWidgets.QVBoxLayout(holder)
        # No left inset: a fight row's own 10px margin plus its 14px checkbox is
        # the chevron's column, so the fights line up under the row above AND the
        # rail reads as one edge. The indent the eye needs comes back from the
        # checkbox itself (see `_add_fight_row`), and a holder with no fight to
        # draw pads its note to the same text edge (`_add_holder_note`).
        holder_lay.setContentsMargins(0, 2, 0, 6)
        holder_lay.setSpacing(4)
        holder.setVisible(False)

        self.cp_content_lay.addWidget(log_w)
        self.cp_content_lay.addWidget(holder)

        # The entry holds the state the row's own widgets need, and NOT the row
        # frame: the rebuild DELETES this row (`_clear_past_fights_body`), and a
        # second Python reference to a widget Qt then destroys is how a wrapper
        # outlives its C++ object - a GC-time access violation (found by running
        # the full suite, 2026-10-07). The tests look the row frame up by
        # position instead.
        entry = {"row": r, "expanded": False, "filled": False,
                 "holder": holder, "chev": chev, "verdict": verdict}

        def toggle(_ev=None) -> None:
            self._toggle_log_row(entry)

        log_w.mousePressEvent = toggle
        # Every label this row draws is repeated here. The kind/difficulty
        # badges and the character tag used to be missing, so the hover text
        # disagreed with the row it belonged to.
        tip_parts = [name, k_txt]
        if mode:
            tip_parts.append(mode)
        if verdict_txt:
            tip_parts.append(verdict_txt)
        tip_parts.append(agg_txt)
        if r.get("char"):
            tip_parts.append(f"[{r['char']}]")
        tip_parts.append(f"{when} · {size_txt}")
        hint = ("Click to look inside anyway." if dim_chev
                else "Click to list this log's fights.")
        log_w.setToolTip(" · ".join(tip_parts) + "\n" + hint)
        self._cp_log_rows = getattr(self, "_cp_log_rows", [])
        self._cp_log_rows.append(entry)

    def _toggle_log_row(self, entry: dict) -> None:
        """Expand or collapse one log, reading its file on first expand.

        A row whose verdict says this filter has nothing in here still OPENS. The
        verdict is read off a file NAME (`_row_verdict`), and a row that could
        refuse the click would let a naming convention hide a real fight. The
        read is one log, and the holder answers with the reason instead of going
        blank - which is the bug this pass fixes.
        """
        if entry["expanded"]:
            entry["expanded"] = False
            entry["holder"].setVisible(False)
            entry["chev"].setText("▸")
            return
        if not entry["filled"]:
            self._fill_log_children(entry)
        entry["expanded"] = True
        entry["holder"].setVisible(True)
        entry["chev"].setText("▾")

    def _fill_log_children(self, entry: dict) -> None:
        """Read ONE log and build a row per fight - the on-demand read.

        Every path out of here leaves the holder showing SOMETHING: fight rows,
        or one line naming why it has none. The old shared `state["shown"]`
        counter and its single list-level note are gone - the note was hidden as
        soon as ANY log contributed a fight, so a log the filter had emptied
        expanded onto a blank body. The fact is about this row, so it is stated
        on this row, and no neighbour's fights can switch it off.
        """
        if entry["filled"]:
            return
        entry["filled"] = True
        row = entry["row"]
        # Served from the parse cache: this view rebuilds its whole list whenever
        # the folder changes, and re-reading every expanded log after each rebuild
        # was pure repetition — the rows need only the fight's own duration and
        # damage. Keyed by the row's own (mtime, size), so a rewritten log is
        # read afresh.
        sessions = cached_history_sessions(row["path"], row.get("mtime"),
                                           row.get("size"))
        holder_lay = entry["holder"].layout()
        if not sessions:
            self._add_holder_note(
                holder_lay, "(no readable fights in this log)",
                "This log is on disk but holds nothing the analyzer can read - "
                "an aborted or empty write.")
            return

        filter_kind = getattr(self, "_cp_fights_filter", "all")
        newest_first = sorted(range(len(sessions)),
                              key=lambda i: sessions[i].start_time or 0.0,
                              reverse=True)
        shown = 0
        for i in newest_first:
            sess = sessions[i]
            if not self._fight_matches_filter(sess, filter_kind):
                continue
            shown += 1
            self._add_fight_row(row, sess, holder_lay)
        if shown:
            return              # the filter kept something: there is no news
        # The FILTER emptied this one, not the log: the file holds fights, they
        # are just not the ones being asked for. Said on the row that came up
        # bare, which is what makes expanding it worth doing at all.
        label = _FIGHTS_FILTER_LABELS.get(filter_kind, filter_kind)
        self._add_holder_note(
            holder_lay, f"(no {label} fights in this log)",
            f"This log holds {len(sessions)} fight"
            f"{'' if len(sessions) == 1 else 's'}, none of them matching the "
            f"{label} filter.")

    @staticmethod
    def _add_holder_note(lay, text: str, tip: str) -> None:
        """The line an expanded log shows instead of going blank.

        Every expanded holder now ends in either fight rows or one of these, so
        "open a row and see nothing" is not a state this view can reach - under
        ANY filter, for any log on disk.
        """
        note = QtWidgets.QLabel(text)
        # 22px of left padding, not a layout inset: the holder starts at the
        # rail so its FIGHT rows can put their checkbox in the chevron's column,
        # which leaves the note to line its text up with the fight names itself.
        note.setStyleSheet(
            f"font-size: 10.5px; color: {theme.DIM}; background: transparent; "
            f"padding: 2px 0 2px 22px;")
        note.setToolTip(tip)
        lay.addWidget(note)

    @staticmethod
    def _fight_matches_filter(sess, filter_kind: str) -> bool:
        """Whether one archived fight survives the current kind filter."""
        kind = (sess.kind or "overall").lower()
        # A training-dummy fight matches by kind OR target, so older archives
        # (dummy damage routed to the trash session when dummy engagement was
        # missed) still group as Test Dummy — and never under Trash even though
        # kind reads "trash".
        is_dummy = (kind == "dummy"
                    or udata.is_training_dummy(sess.target_name))
        if filter_kind == "all":
            return True
        if filter_kind == "failed":
            # A FAILED fight is a boss fight that ended without a kill (see
            # `fight_failed`): the pulls to review, the attempts to learn from.
            # A trash pull that lulls out is not a failure and a dummy test
            # cannot fail, so neither shows here even when an old archive
            # misfiled it.
            return not is_dummy and bool(fight_failed(sess))
        if filter_kind == "dummy":
            return bool(is_dummy)
        if is_dummy:
            # Dummy fights belong only to the Test Dummy filter.
            return False
        return kind == filter_kind

    def _add_fight_row(self, row: dict, sess, parent_lay) -> None:
        """One fight inside an expanded log, read the way Variant C reads.

        `[box] name  DAMAGE  outcome  ...  duration · players` - the outcome in
        its own slot, and the rest of the fight's facts (timestamp, kind, boss)
        on the tooltip where they cost the row no width. Load and Delete moved to
        a right-click on the row (and a double-click still loads): the checkbox
        and the frozen bar's Load/Delete Selected are the bulk path, and the two
        buttons were taking the space the damage column now has.
        """
        kind = (sess.kind or "overall").lower()
        k_col, k_txt = _KIND_STYLE.get(kind, (theme.ACCENT, kind.upper()))
        if fight_failed(sess):
            # A wipe says so on its row: scanning for the attempts is the whole
            # point of the filter, and a row that needs the filter to be
            # readable has not done its job.
            k_col, k_txt = theme.DANGER, "FAILED"

        raw_boss = ((sess.target_name or "")
                    .replace("👑 ", "").replace("🎯 ", "").strip())
        if not raw_boss or raw_boss == "None":
            boss_txt, boss_col, boss_pre = "no boss recorded", theme.DIM, "🎯 "
        elif "👑" in (sess.target_name or ""):
            boss_txt, boss_col, boss_pre = raw_boss, theme.GOLD, "👑 "
        else:
            boss_txt, boss_col, boss_pre = raw_boss, theme.MUTED, "🎯 "

        when = (time.strftime("%H:%M:%S", time.localtime(sess.start_time))
                if sess.start_time else "--")
        mins = int(sess.duration // 60)
        secs = sess.duration % 60
        nm = _clean_session_name(sess.name)
        # The right-hand slot as Variant C draws it: how long the fight ran and
        # how many names were in it. The count comes off the session's own
        # roster, so saying it costs no read, and it is dropped (rather than
        # printed as `0 players`) for an archive that recorded nobody.
        players = len(getattr(sess, "players", None) or [])
        meta = (f"{mins:02d}:{secs:02.0f}"
                + (f" · {players} players" if players else ""))

        row_w = QtWidgets.QFrame()
        row_w.setObjectName("PastFightRow")
        row_w.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                            QtWidgets.QSizePolicy.Fixed)
        row_w.setCursor(QtCore.Qt.PointingHandCursor)

        state_item = {"path": row["path"], "char": row.get("char", ""),
                      "sess": sess, "selected": False, "widget": row_w}

        def apply_style(st=state_item) -> None:
            """Repaint the row for its selection - AND move its checkbox.

            The one place that knows the selection changed, so every path that
            sets `st["selected"]` (a click on the row, the bar's Select All) is
            mirrored in the box without either of them knowing about the other.
            The checkbox comes after this runs at creation, hence the lookup.
            """
            is_sel = st["selected"]
            bg = theme.ACCENT_DIM if is_sel else theme.PANEL
            brd = theme.ACCENT if is_sel else theme.BORDER
            st["widget"].setStyleSheet(
                f"QFrame#PastFightRow {{ background: {bg}; border: 1px solid "
                f"{brd}; border-radius: 5px; }}"
                f"QFrame#PastFightRow:hover {{ background: {theme.PANEL_HI}; "
                f"border-color: {theme.ACCENT}; }}")
            cb = st.get("checkbox")
            if cb is not None and cb.isChecked() != is_sel:
                cb.blockSignals(True)
                cb.setChecked(is_sel)
                cb.blockSignals(False)

        state_item["update_style"] = apply_style
        apply_style()
        self._cp_fight_rows.append(state_item)

        h = QtWidgets.QHBoxLayout(row_w)
        h.setContentsMargins(10, 5, 8, 5)
        h.setSpacing(8)

        def on_select_toggled(on: bool, st=state_item) -> None:
            """The checkbox half of the selection.

            `st["update_style"]` moves the box itself (that is the one place the
            selection is painted, and it blocks the signal doing it), so what
            this adds is the toolbar refresh the row click already does: the bar
            counts the selection, and Delete Selected (N) must not go stale
            because the box was clicked instead of the row.
            """
            st["selected"] = bool(on)
            st["update_style"]()
            bar = getattr(self, "_cp_fights_bar", None)
            if bar:
                bar["update"]()

        # The selection box, in the LOG row's chevron column (Variant C): the
        # fights under a row start where that row's own text does, and the tree
        # keeps one left edge. The row stays clickable - the box is the visible
        # half of a selection that already worked, not a new gate on it.
        sel_cb = QtWidgets.QCheckBox()
        sel_cb.setObjectName("PastFightSelect")
        sel_cb.setFixedWidth(14)
        sel_cb.setCursor(QtCore.Qt.PointingHandCursor)
        sel_cb.setStyleSheet(_FIGHT_SELECT_QSS)
        sel_cb.setToolTip("Select this fight (the toolbar's Load/Delete use the "
                          "selection; double-click the row to load it)")
        sel_cb.toggled.connect(on_select_toggled)
        state_item["checkbox"] = sel_cb
        h.addWidget(sel_cb, 0, QtCore.Qt.AlignVCenter)

        name_lbl = QtWidgets.QLabel(nm)
        name_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 800; color: {theme.TEXT}; "
            f"background: transparent;")
        h.addWidget(name_lbl)

        # No " DMG" suffix: the reader knows what the number in the gold column
        # is, and the suffix is one more thing between the name and the outcome.
        # The tooltip keeps it spelled out.
        dmg_lbl = QtWidgets.QLabel(f"{sess.group_damage:,.0f}")
        dmg_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 900; color: {theme.GOLD}; "
            f"font-family: monospace; background: transparent;")
        h.addWidget(dmg_lbl)

        # Variant C's fight row carries no kind badge and no second copy of the
        # boss: the log row above already says the kind and the name, and both
        # still reach the tooltip. FAILED is the one badge that stays on the
        # row - a wipe has to be scannable there (that is what the Failed filter
        # is for), and it is the outcome the KILLED chip is the other half of.
        if fight_failed(sess):
            kind_lbl = QtWidgets.QLabel(k_txt)
            kind_lbl.setStyleSheet(
                f"font-size: 9px; font-weight: 800; color: {k_col}; "
                f"background: {theme.with_alpha(k_col, 28)}; border-radius: 3px; "
                f"padding: 1px 5px;")
            h.addWidget(kind_lbl)

        # The OUTCOME, as Variant C draws it: a boss fight that ended in the kill
        # says so (its kind chip already said `FAILED` when it did not). Gated on
        # the kind exactly like `fight_failed`, because `was_killed` is about the
        # BOSS - a trash pull that lulls out killed nothing and must not claim it.
        outcome = ""
        if kind in ("boss", "boss_adds") and was_killed(sess):
            outcome = "✔ KILLED"
            kill_lbl = QtWidgets.QLabel(outcome)
            kill_lbl.setStyleSheet(
                f"font-size: 9px; font-weight: 800; color: {theme.GOOD}; "
                f"background: {theme.with_alpha(theme.GOOD, 28)}; "
                f"border-radius: 3px; padding: 1px 5px;")
            kill_lbl.setToolTip("The boss died in this fight")
            h.addWidget(kill_lbl)

        if udata.is_training_dummy(raw_boss) or udata.is_training_dummy(
                sess.target_name):
            dpm = icons.asset_icon("target-dummy", 16)
            if dpm is None or dpm.isNull():
                dpm = icons.ui_icon("target-dummy", boss_col, 16)
            if dpm is not None and not dpm.isNull():
                dummy_ic = QtWidgets.QLabel()
                dummy_ic.setPixmap(dpm)
                dummy_ic.setToolTip("Training dummy (own boss group)")
                dummy_ic.setStyleSheet("background: transparent;")
                h.addWidget(dummy_ic)

        h.addStretch(1)
        meta_lbl = QtWidgets.QLabel(meta)
        meta_lbl.setStyleSheet(
            f"font-size: 10.5px; color: {theme.MUTED}; font-family: monospace; "
            f"background: transparent;")
        h.addWidget(meta_lbl)

        def _row_menu(pos, ch=row.get("char", ""), s=sess, p=row["path"],
                      widget=row_w) -> None:
            """Load and Delete, off the row's face but still on the row.

            Variant C's fight row carries no buttons: the checkbox makes the
            selection and the frozen bar acts on it (Load Selected / Delete
            Selected). Both per-fight actions stay reachable without the two
            buttons eating the width the damage column now has - a right-click
            on the row, and a double-click still loads the fight.
            """
            menu = QtWidgets.QMenu(widget)
            menu.addAction("📂 Load this fight",
                           lambda: self._load_past_fight(ch, s))
            menu.addAction("🗑 Delete this fight",
                           lambda: self._delete_past_fight(p, s))
            menu.exec(widget.mapToGlobal(pos))

        row_w.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        row_w.customContextMenuRequested.connect(_row_menu)

        def on_press(ev, st=state_item) -> None:
            if ev.button() == QtCore.Qt.LeftButton:
                st["selected"] = not st["selected"]
                st["update_style"]()
                bar = getattr(self, "_cp_fights_bar", None)
                if bar:
                    bar["update"]()
                ev.accept()

        def on_double(ev, ch=row.get("char", ""), s=sess) -> None:
            if ev.button() == QtCore.Qt.LeftButton:
                self._load_past_fight(ch, s)
                ev.accept()

        row_w.mousePressEvent = on_press
        row_w.mouseDoubleClickEvent = on_double
        # Mirror the row itself, and then some: the tooltip carries the fields
        # the row no longer draws (the timestamp, the kind, the boss) so nothing
        # is lost by moving them off its face, and every label the row DOES draw
        # appears here verbatim (`tests/test_past_fights.py`).
        row_w.setToolTip(
            f"{when} · {nm} · {k_txt} · {boss_pre}{boss_txt} · "
            f"{outcome + ' · ' if outcome else ''}{meta} · "
            f"{sess.group_damage:,.0f} DMG\n"
            f"Click to select · double-click to load · right-click for "
            f"Load / Delete")

        parent_lay.addWidget(row_w)

    def _expand_every_log(self) -> None:
        """Read every listed log AND OPEN it, so Select All is WYSIWYG.

        Select All used to read every listed log and select its fights while
        leaving the logs COLLAPSED, so a bulk delete could remove fights hidden
        inside rows the player never opened (live 2026-10-05: "select all ...
        looks like it got rid of them all"). The selection now reveals itself:
        every log that holds a fight matching the current filter is expanded
        first, so every row about to be deleted is on screen. A log with nothing
        matching the filter (or nothing readable) is left closed — none of its
        fights are in the selection. This is Select All's bulk cost, and it is
        the only place this view reads every listed log.

        FOLDED rows are not listed at all, and the selection is still complete
        without them: a row only folds when the filter's own verdict says it has
        nothing here (`_add_fold_row`), so it carries no fight this filter keeps.
        """
        for entry in getattr(self, "_cp_log_rows", []):
            self._fill_log_children(entry)
            if not self._entry_has_fight_rows(entry):
                continue
            entry["expanded"] = True
            entry["holder"].setVisible(True)
            entry["chev"].setText("\u25be")

    @staticmethod
    def _entry_has_fight_rows(entry: dict) -> bool:
        """Whether an expanded log actually holds a fight row for this filter.

        A log whose only fights the filter hides (or that holds no readable
        fight) must not be opened by Select All: it carries no selected row, so
        opening it would show an empty body rather than part of the selection.
        """
        lay = entry["holder"].layout()
        if lay is None:
            return False
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and w.objectName() == "PastFightRow":
                return True
        return False

    # --- filter ------------------------------------------------------------#

    def _set_fights_filter(self, fk: str) -> None:
        """Pick the Past Fights kind filter (all / boss / trash / dummy / failed).

        The chips are a RADIO GROUP, not five independent toggles. Clicking the
        chip that is already active used to UNCHECK it while `_cp_fights_filter`
        kept filtering, so the bar showed no selection for a list that was
        still filtered — and the next click on a different chip was the only
        thing that put the row back in sync. The chosen chip is forced back on
        before anything else, so the row can never disagree with the filter
        actually in force.
        """
        chips = (getattr(self, "_cp_fights_bar", None) or {}).get("filters") or {}
        for k, chip in chips.items():
            want = (k == fk)
            if chip.isChecked() != want:
                chip.blockSignals(True)
                chip.setChecked(want)
                chip.blockSignals(False)
        changed = (fk != getattr(self, "_cp_fights_filter", "all"))
        self._cp_fights_filter = fk
        # Keep the page's SEGMENT row in step: on the Fights view those chips
        # ARE this filter (see `CombatPageMixin._sync_segment_chips`), so a
        # click on either row has to move the other — including the no-op case
        # where the clicked kind is already in force but the SEGMENT highlight
        # still has to follow it.
        if callable(getattr(self, "_sync_segment_chips", None)):
            self._sync_segment_chips()
        if changed:
            # A NEW filter starts folded. The reveal was a decision about the
            # filter that hid those rows ("show me the Trash-less ones"), not a
            # global preference, and carrying it over would leave every later
            # filter unfolded because of one click somewhere else.
            self._cp_fights_show_nomatch = False
            self._render_past_fights(force=True)
