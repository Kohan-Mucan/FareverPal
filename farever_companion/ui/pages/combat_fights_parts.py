"""The Past Fights view's satellites: the frozen action bar and the search.

One shard, not two little modules beside `combat_fights_list.py`: both are parts
of the same view, and the split exists only because the AGENTS.md line budget
measures ONE file. That is a reason to split, not a reason to scatter - a family
that needs a second file gets a shard of real size, not a module per idea.

* `CombatFightsToolbarMixin` - the batch action bar above the list (select /
  filter / delete / day window). It is the one part of the view that survives a
  list rebuild: the list swaps it into a host slot OUTSIDE the scroll area,
  because a bar inside that layout scrolls off the top of the page and a bulk
  action you have to scroll back up to reach is useless (live 2026-10-04: "the
  bar goes up the page").
* `row_search_text` / `log_matches_query` / `build_fights_search_box` - the
  search, answered from the index and the sidecars only, so a keystroke never
  makes the list parse a season of logs to find a match.

A log with no sidecar has no failure state to match - it is never parsed to find
one. That is the honest reading of a missing sidecar (the failure state is
unknown, not False), and it is what keeps a keystroke from reading a season of
fights. Multi-term: every whitespace-separated term must appear somewhere in the
row's haystack, so "kartik reblochonk" narrows rather than widening.

`CombatFightsListMixin` inherits the bar's mixin, so no call site moved.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import theme
from ..combat import delete_past_fights_batch, forget_cached_sessions

# The Fights list's DEFAULT day window, used when the settings field is absent
# or unreadable. How many most-recent DAYS the list draws on first open: the
# scan is already read-free (a directory walk + a stat and a tiny sidecar per
# log), but building a Qt widget per log row — and a day header per day — is
# what makes a long season crawl, so the rest sit behind this bar's "Show all
# days". Counted in DAYS rather than logs because a player thinks in sessions
# ("this week"), and one busy evening can hold a dozen logs. The window itself
# is a USER SETTING (Settings > DPS > Combat DPS Meter > Fights list window);
# this is only the fallback. 0 in the setting = show every day.
_FIGHTS_RECENT_DAYS = 7


class CombatFightsToolbarMixin:
    """The batch action bar: selection, kind filter, day window, cleanup."""

    def _fights_recent_days(self) -> int:
        """The default Fights day-window, from Settings > DPS > Combat DPS Meter.

        Falls back to `_FIGHTS_RECENT_DAYS` when the host has no settings (the
        list is also exercised as a bare mixin) or the stored value is not a
        number. A value <= 0 means "show every day", the same convention the
        Keep-logs setting uses for "keep everything".
        """
        ctx = getattr(self, "s", None)
        raw = getattr(ctx, "dps_fights_recent_days", _FIGHTS_RECENT_DAYS)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return _FIGHTS_RECENT_DAYS

    def _place_fights_toolbar(self, widget) -> None:
        """Swap the frozen action bar above the scroll area (`None` clears it).

        The batch bar must sit OUTSIDE `cp_content_lay` — the scroll area's
        layout — because a bar inside it scrolls off the top of the page and a
        bulk action you have to scroll back up to reach is useless (live
        2026-10-04: "the bar goes up the page"). `_build_past_fights_view`
        creates the host slot; a bare host without one (a test double) keeps
        the old behaviour of putting the bar at the top of the scroll body.
        """
        lay = getattr(self, "_cp_fights_toolbar_lay", None)
        if lay is None:
            if widget is not None:
                self.cp_content_lay.addWidget(widget)
            return
        for i in reversed(range(lay.count())):
            w = lay.itemAt(i).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if widget is not None:
            lay.addWidget(widget)

    def _build_fights_toolbar(self, filter_kind: str, day_total: int = 0,
                              show_all: bool = False) -> dict:
        """The batch action bar above the list (select / filter / delete).

        Returned as a dict of its live widgets plus an `update` callback, so
        the row code can refresh the selection counts without reaching into
        closures it cannot see. `day_total` is the number of DAYS on disk, so
        the "Show all days" control only appears when the window is actually
        hiding some. The window size is read here from settings, so the label
        and the threshold follow whatever the user chose.
        """
        recent = self._fights_recent_days()
        batch_bar = QtWidgets.QFrame()
        # Fixed height so the toolbar never stretches when few rows render.
        batch_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                QtWidgets.QSizePolicy.Fixed)
        # `.QFrame` (exact class): a bare `QFrame` rule cascades to every
        # QLabel inside (QLabel IS-A QFrame) and boxed each text element.
        batch_bar.setStyleSheet(
            f".QFrame {{ background: {theme.PANEL}; border: 1px solid "
            f"{theme.BORDER}; border-radius: 6px; }}")
        bl = QtWidgets.QHBoxLayout(batch_bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(10)

        select_all_cb = QtWidgets.QCheckBox("Select All")
        select_all_cb.setStyleSheet(
            f"font-size: 11.5px; font-weight: 700; color: {theme.TEXT};")
        bl.addWidget(select_all_cb)

        # Kind filter: a row of checkable chips (All / Boss / Trash / Test
        # Dummy / Failed). FAILED is its own chip rather than a kind because a
        # wipe is not a kind of fight — it is an outcome, and the player asking
        # for it is asking "show me the attempts", not "show me a kind".
        filter_chips: dict[str, QtWidgets.QPushButton] = {}
        for fk, lbl, col in (("all", "All Fights", theme.ACCENT),
                             ("boss", "⚔ Boss", theme.GOLD),
                             ("trash", "🗑 Trash", theme.DIM),
                             ("dummy", "🎯 Test Dummy", theme.ACCENT_LIGHT),
                             ("failed", "☠ Failed", theme.DANGER)):
            b = QtWidgets.QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(fk == filter_kind)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background: {theme.PANEL_LOW}; color: "
                f"{theme.TEXT}; border: 1px solid {theme.BORDER}; "
                f"border-radius: 4px; padding: 2px 10px; font-weight: 700; "
                f"font-size: 11px; }}"
                f"QPushButton:checked {{ background: "
                f"{theme.with_alpha(col, 40)}; color: {col}; border: 1px "
                f"solid {col}; }}"
                f"QPushButton:hover {{ border-color: {col}; }}")
            b.clicked.connect(lambda _=False, k=fk: self._set_fights_filter(k))
            bl.addWidget(b)
            filter_chips[fk] = b

        # Day-window control: the newest `recent` days draw by default, and
        # this reveals the rest (or folds them back). A toggle rather than a
        # one-way "show all", so a player who opened a season can put it away
        # again; it renders checked whenever the window is. A window of 0 means
        # the whole list is already shown, so there is nothing to toggle.
        if recent > 0 and day_total > recent:
            hidden = day_total - recent
            day_btn = QtWidgets.QPushButton(
                f"Showing all {day_total} days" if show_all
                else f"Show all days (+{hidden})")
            day_btn.setCheckable(True)
            day_btn.setChecked(show_all)
            day_btn.setCursor(QtCore.Qt.PointingHandCursor)
            day_btn.setToolTip(
                f"Only the last {recent} days are listed by default so a long "
                f"season opens instantly. Change this in Settings > DPS > "
                f"Combat DPS Meter > Fights list window.")
            day_btn.setStyleSheet(
                f"QPushButton {{ background: {theme.PANEL_LOW}; color: "
                f"{theme.MUTED}; border: 1px solid {theme.BORDER}; "
                f"border-radius: 4px; padding: 5px 12px; font-weight: 800; "
                f"font-size: 11.5px; }}"
                f"QPushButton:checked {{ background: "
                f"{theme.with_alpha(theme.ACCENT, 40)}; color: "
                f"{theme.ACCENT_LIGHT}; border: 1px solid {theme.ACCENT}; }}"
                f"QPushButton:hover {{ color: {theme.TEXT}; border-color: "
                f"{theme.ACCENT}; }}")
            day_btn.clicked.connect(self._toggle_all_fight_days)
            bl.addWidget(day_btn)

        bl.addStretch(1)

        # Clear Old Logs: the only thing in the app that deletes archived
        # history in bulk, so it says out loud what it will remove and asks
        # first. The window itself is a setting (Settings > DPS > Combat DPS
        # Meter > Keep logs) and 0 there means "keep everything" — a button
        # that quietly wiped a year of logs because a number was zero would be
        # the worst possible reading of that setting.
        clear_logs_btn = QtWidgets.QPushButton("🧹 Clear Old Logs")
        clear_logs_btn.setCursor(QtCore.Qt.PointingHandCursor)
        clear_logs_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_LOW}; color: "
            f"{theme.DIM}; border: 1px solid {theme.BORDER}; border-radius: "
            f"4px; padding: 5px 12px; font-weight: 800; font-size: 11.5px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT}; border-color: "
            f"{theme.GOLD}; }}")
        clear_logs_btn.clicked.connect(self._clear_old_logs)
        bl.addWidget(clear_logs_btn)

        load_sel_btn = QtWidgets.QPushButton("📂 Load Selected")
        load_sel_btn.setEnabled(False)
        load_sel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        load_sel_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_LOW}; color: "
            f"{theme.DIM}; border: 1px solid {theme.BORDER}; border-radius: "
            f"4px; padding: 5px 12px; font-weight: 800; font-size: 11.5px; }}"
            f"QPushButton:enabled {{ background: {theme.ACCENT_DIM}; color: "
            f"{theme.ACCENT_LIGHT}; border: 1px solid {theme.ACCENT}; }}"
            f"QPushButton:enabled:hover {{ background: {theme.ACCENT}; color: "
            f"#00354a; }}")
        bl.addWidget(load_sel_btn)

        delete_batch_btn = QtWidgets.QPushButton("🗑 Delete Selected (0)")
        delete_batch_btn.setEnabled(False)
        delete_batch_btn.setCursor(QtCore.Qt.PointingHandCursor)
        delete_batch_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_LOW}; color: "
            f"{theme.DIM}; border: 1px solid {theme.BORDER}; border-radius: "
            f"4px; padding: 5px 12px; font-weight: 800; font-size: 11.5px; }}"
            f"QPushButton:enabled {{ background: "
            f"{theme.with_alpha(theme.DANGER, 25)}; color: {theme.DANGER}; "
            f"border: 1px solid {theme.DANGER}; }}"
            f"QPushButton:enabled:hover {{ background: {theme.DANGER}; color: "
            f"#ffffff; }}")
        bl.addWidget(delete_batch_btn)

        bar = {"widget": batch_bar, "select_all": select_all_cb,
               "load": load_sel_btn, "delete": delete_batch_btn,
               "filters": filter_chips, "updating": False}

        def update() -> None:
            sel = [st for st in self._cp_fight_rows if st["selected"]]
            cnt = len(sel)
            delete_batch_btn.setText(f"🗑 Delete Selected ({cnt})")
            delete_batch_btn.setEnabled(cnt > 0)
            load_sel_btn.setEnabled(cnt > 0)
            select_all_cb.blockSignals(True)
            select_all_cb.setChecked(cnt > 0 and cnt == len(self._cp_fight_rows))
            select_all_cb.blockSignals(False)

        def on_select_all(on: bool) -> None:
            if bar["updating"]:
                return
            # Select All is an explicit bulk action, and it is the one place
            # this view is allowed to read every log: a selection that skipped
            # the fights it never expanded would delete less than it says.
            if on:
                self._expand_every_log()
            for st in self._cp_fight_rows:
                st["selected"] = on
                st["update_style"]()
            update()

        select_all_cb.toggled.connect(on_select_all)

        def on_load() -> None:
            sel = [st for st in self._cp_fight_rows if st["selected"]]
            if sel:
                self._load_past_fight(sel[0]["char"], sel[0]["sess"])

        load_sel_btn.clicked.connect(on_load)

        def on_delete() -> None:
            items = [(st["path"], st["sess"])
                     for st in self._cp_fight_rows if st["selected"]]
            if not items:
                return

            def _after_delete() -> None:
                """Drop each touched log's parse cache, then rebuild."""
                for p, _s in items:
                    forget_cached_sessions(p)
                self._cp_past_fights_dirty = True
                self._sync_history_combo()
                self._render_combat_breakdown()

            delete_past_fights_batch(
                self, items,
                active_tracker=self._get_active_tracker(),
                browse_holder=self,
                after_delete_cb=_after_delete)

        delete_batch_btn.clicked.connect(on_delete)

        bar["update"] = update
        return bar

    def _toggle_all_fight_days(self, checked: bool) -> None:
        """Reveal (or re-hide) every day the default day-window was limiting.

        A per-session preference kept on the INSTANCE, not in the toolbar
        closure: the folder-change rebuilds recreate the whole list, and the
        choice has to survive them. `force` because only the window changed —
        the file fingerprint the render compares is identical.
        """
        self._cp_fights_show_all_days = bool(checked)
        self._render_past_fights(force=True)


#: Virtual tokens a row exposes to the search when its sidecar reports a failed
#: pull. Not a field on the row — added for the sidecar's `has_failure` only, so
#: "failed"/"wipe" match logs a sidecar actually flagged and nothing else.
_FAILURE_TOKENS = ("failed", "wipe")


def row_search_text(row: dict) -> str:
    """The lower-cased haystack a row is matched against — no log is read.

    Built from the descriptor (character, boss, dungeon, display name) plus the
    sidecar's boss rollup keys and its failure flag. `summary` is absent for a
    log without a sidecar, which is "failure state unknown", not False.
    """
    parts = [
        row.get("char") or "",
        row.get("boss") or "",
        row.get("dungeon") or "",
        row.get("name") or "",
    ]
    summary = row.get("summary")
    if isinstance(summary, dict):
        bosses = summary.get("bosses")
        if isinstance(bosses, dict):
            parts.extend(str(b) for b in bosses)
        if summary.get("has_failure"):
            parts.extend(_FAILURE_TOKENS)
    return " ".join(parts).lower()


def log_matches_query(row: dict, query: str) -> bool:
    """Whether a log row survives `query`, using the row alone (no parsing).

    An empty/whitespace query matches everything, so clearing the box restores
    the unfiltered list without a special case at the call site.
    """
    terms = (query or "").lower().split()
    if not terms:
        return True
    hay = row_search_text(row)
    return all(term in hay for term in terms)


def build_fights_search_box(text: str, on_change) -> QtWidgets.QLineEdit:
    """The styled search field for the Fights view.

    Connected to `on_change(str)` on every keystroke. The CALLER must keep it
    out of the re-rendered tree: the list recreates its body (and the batch
    bar) on each change, and rebuilding the box mid-typing would steal focus.
    """
    box = QtWidgets.QLineEdit()
    box.setPlaceholderText("Search character, boss, dungeon, failed…")
    box.setText(text or "")
    box.setClearButtonEnabled(True)
    box.setFixedWidth(240)
    box.setCursor(QtCore.Qt.IBeamCursor)
    box.setToolTip(
        "Matches the character, boss, dungeon and failure state already read "
        "off each log's name and its sidecar summary — it never parses a log. "
        "Separate terms narrow the result (all must match).")
    box.setStyleSheet(
        f"QLineEdit {{ background: {theme.PANEL_LOW}; color: {theme.TEXT}; "
        f"border: 1px solid {theme.BORDER}; border-radius: 4px; "
        f"padding: 4px 8px; font-size: 11.5px; }}"
        f"QLineEdit:focus {{ border-color: {theme.ACCENT}; }}")
    box.textChanged.connect(on_change)
    return box
