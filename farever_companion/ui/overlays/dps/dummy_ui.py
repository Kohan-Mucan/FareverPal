"""Everything the training-dummy test RENDERS: its control, its split, its baseline.

A dummy is the one encounter the game never concludes for us — a boss dies or
despawns and a trash pull lulls out, but a dummy just stands there
regenerating — so a dummy test needs surfaces the other fights do not. All
three live here, in the order the board stacks them:

* `DummyTestControlMixin` — the Stop / Start Test button, the section hooks and
  the empty-state line. Mixed into the standalone Test Dummy HUD, the one
  window that renders a dummy test: the Top DPS meter carries none of this on
  purpose, so a dummy feature cannot exist twice and drift between the two.
* `TargetSplit` + `_TargetRow` — one row per dummy in the group, each with its
  own health bar, and the board's TARGET PICKER: clicking a row pins that dummy
  as the header target (the pin rule itself is core — `CombatSession.
  select_target` / `DpsTracker.select_dummy_target`). A cluster of dummies is
  deliberately ONE session, so this split is the only place that can say which
  of them took the damage.
* `BaselineDelta` + `_DeltaRow` — the per-skill delta against the remembered
  test, which is what makes a training yard worth having: the same rotation
  measured twice against something that never moves.

One module, not three, because they are one concern: all three are imported by
the same mixin, and all three are the dummy test's own surface. They were only
ever separate because each had to come OUT of `dps/overlay.py` to keep that
file under the 840-line budget — which says nothing about them belonging apart,
and says less now that the meter renders none of them.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ...components import ElideLabel as _ElideLabel
from ....core.dps_data import TargetParse
from ..entity_rows import _Section
from .dummy_replay import DummyReplay


# --- the per-target split ----------------------------------------------#

# The `~` on an estimated row's number: the same "approximately" the rest of the
# app uses, small enough to sit in a four-column row that is meant to be dense.
ESTIMATE_MARK = "~"
# What the marked row says on hover. Wording pins "this ROW", not "this split":
# the rows beside it are measurements.
ESTIMATE_ROW_TIP = ("  ·  part of this row's damage arrived with no target "
                    "address, so the number is an estimate")
# The section header's tooltip, shown only while some row is marked.
ESTIMATE_SPLIT_TIP = (
    f"A row marked {ESTIMATE_MARK} absorbed damage that arrived with no target "
    "address, so which dummy it landed on could not be measured and it was "
    "pinned to the dummy the tracker believes is the target. Only that row is "
    "an estimate; the rest of the split was measured.")


class _TargetRow(QtWidgets.QFrame):
    """One target's slice of the group total: label, hits, damage, share.

    Clickable: a click makes this row the board's pinned target (see        `TargetParse.key` and `CombatSession.select_target`), which is how a player
    says "watch THIS dummy" while they go on hitting a different one. Rows are
    built lazily and reused, so a row's identity is `set_target`'s business, not
    the constructor's.

    A row can also be an ESTIMATE (`set_estimated`), which is a property of one
    row and not of the split: it absorbed hits that arrived with no target
    address, so its damage is the tracker's best guess while the rows beside it
    are measurements.
    """

    def __init__(self, parent=None, on_select=None):
        super().__init__(parent)
        self._on_select = on_select
        self._key: int | str | None = None
        self._pinned = False
        self._estimated = False
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 2, 6, 3)
        outer.setSpacing(2)
        lay = QtWidgets.QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.name_lbl = _ElideLabel("", 150)
        self.name_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
        self.name_lbl.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                    QtWidgets.QSizePolicy.Preferred)
        lay.addWidget(self.name_lbl, 1)

        self.hits_lbl = QtWidgets.QLabel("")
        self.hits_lbl.setStyleSheet(f"font-size: 9px; color: {theme.DIM};")
        lay.addWidget(self.hits_lbl, 0)

        self.total_lbl = QtWidgets.QLabel("0")
        self.total_lbl.setObjectName("TargetTotal")
        self.total_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 800; color: {theme.ACCENT_LIGHT}; "
            f"font-family: 'Consolas', monospace;")
        self.total_lbl.setMinimumWidth(52)
        self.total_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(self.total_lbl, 0)

        self.share_lbl = QtWidgets.QLabel("0.0%")
        self.share_lbl.setObjectName("TargetShare")
        self.share_lbl.setFixedWidth(46)
        self.share_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {theme.DIM}; "
            f"font-family: 'Consolas', monospace;")
        self.share_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(self.share_lbl, 0)
        outer.addLayout(lay)

        # This dummy's own health, under its name. Stays hidden until the scan
        # has read a max: an empty bar would claim "0 HP" for a target nobody
        # has measured yet.
        self.bar = QtWidgets.QProgressBar()
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setStyleSheet(
            "QProgressBar { background: rgba(0, 0, 0, 0.4); border: 0; "
            "border-radius: 2px; }"
            f"QProgressBar::chunk {{ background: {theme.ACCENT_LIGHT}; "
            "border-radius: 2px; }")
        outer.addWidget(self.bar)
        self._sync_affordance()

    # --- the pin ----------------------------------------------------------#

    def _sync_affordance(self) -> None:
        """Clickable rows say so; the pinned one says which one it is.

        The accent left edge is the same "this is the header target" signal the
        meter's own target bar gives, so the section and the bar above it can
        never disagree about who is pinned. Two whole stylesheets rather than a
        property selector + repolish: this runs on a 10 Hz tick, and a state that
        only changes when the pin does should not cost a repolish to read.
        """
        if self._on_select is not None and self._key is not None:
            self.setCursor(QtCore.Qt.PointingHandCursor)
        if self._pinned:
            self.setStyleSheet(
                "_TargetRow { background-color: rgba(255, 255, 255, 0.07); "
                f"border-left: 2px solid {theme.ACCENT_LIGHT}; "
                "border-radius: 4px; }")
            tip = ("Pinned as the test's target — click again to "
                   "follow the damage leader")
        else:
            self.setStyleSheet(
                "_TargetRow { background-color: rgba(22, 24, 29, 0.35); "
                "border-radius: 4px; }")
            tip = "Click to pin this dummy as the test's target"
        # Built from scratch every time: both the pin and the estimate change
        # this text, and appending to whatever is already on the row would
        # stack the same sentence once per state change.
        self.setToolTip((tip + ESTIMATE_ROW_TIP) if self._estimated else tip)

    def _sync_estimate(self) -> None:
        """The colour the row's number carries: a measurement is accent, an
        estimate is warning amber, so the one guessed row cannot be read as a
        number that compares with the two beside it."""
        col = theme.ORANGE if self._estimated else theme.ACCENT_LIGHT
        self.total_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 800; color: {col}; "
            f"font-family: 'Consolas', monospace;")

    def set_key(self, key: int | str) -> None:
        """Point this row at its bucket. Rows are reused, so a row's identity
        changes; the click affordance is re-synced only when it actually does."""
        if self._key == key:
            return
        self._key = key
        self._sync_affordance()

    def set_pinned(self, pinned: bool) -> None:
        if bool(pinned) == self._pinned:
            return
        self._pinned = bool(pinned)
        self._sync_affordance()

    def set_estimated(self, estimated: bool) -> None:
        """Mark this row's damage as a guess rather than a measurement.

        Only the row that ABSORBED hits carrying no target address is a guess
        (see `core.dps_dummy.dummy_split_targets`); the other yard rows are
        measured. Tagging the whole split instead made a yard of three measured
        dummies look uncertain because one hit lost its pointer.
        """
        if bool(estimated) == self._estimated:
            return
        self._estimated = bool(estimated)
        self._sync_estimate()
        self._sync_affordance()

    def mousePressEvent(self, ev) -> None:      # noqa: N802 (Qt naming)
        if self._on_select is not None and self._key is not None:
            self._on_select(self._key)
        super().mousePressEvent(ev)

    def update_stats(self, label: str, tgt: TargetParse, total: float,
                     duration: float) -> None:
        self.name_lbl.set_full(label)
        self.hits_lbl.setText(f"{tgt.hits} hit" + ("s" if tgt.hits != 1 else ""))
        self.total_lbl.setText(
            (ESTIMATE_MARK if self._estimated else "") + f"{tgt.damage:,.0f}")
        # The rate rides the tooltip: the row is about the SPLIT, and a fourth
        # number column would crowd a meter that is meant to be dense.
        self.total_lbl.setToolTip(f"{tgt.dps(duration):,.1f}/s")
        self.share_lbl.setText(f"{tgt.share_pct(total):.1f}%")
        if tgt.has_hp:
            self.bar.setValue(int(tgt.hp_pct))
            self.bar.setToolTip(f"{tgt.hp:,.0f} / {tgt.max_hp:,.0f} hp")
            self.bar.show()
        else:
            self.bar.setToolTip("")
            self.bar.hide()


def _labels(targets: list[TargetParse]) -> list[str]:
    """Row labels, numbering same-name targets so three identical dummies do
    not render as three identical rows. The number is the target's damage rank,
    which is also the order the rows are shown in."""
    counts: dict[str, int] = {}
    for t in targets:
        counts[t.name] = counts.get(t.name, 0) + 1
    seen: dict[str, int] = {}
    out: list[str] = []
    for t in targets:
        if counts.get(t.name, 1) > 1:
            seen[t.name] = seen.get(t.name, 0) + 1
            out.append(f"{t.name} #{seen[t.name]}")
        else:
            out.append(t.name)
    return out


class TargetSplit(QtWidgets.QWidget):
    """The group total plus one row per target; visible only for two or more.

    `on_select` is the row-click handler — a callable taking the row's
    `TargetParse.key` — and the pinned row is highlighted from `update_targets`
    so the click and the highlight cannot be two different truths.
    """

    def __init__(self, parent=None, on_select=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.box = _Section("SPLIT BY TARGET", theme.ACCENT_LIGHT)
        lay.addWidget(self.box)
        self._rows: list[_TargetRow] = []
        self._on_select = on_select
        self.hide()

    def update_targets(self, targets: list[TargetParse], total: float,
                       duration: float, pinned_key: int | str | None = None,
                       estimated_keys: set | frozenset | None = None) -> None:
        """Render `targets` (already damage-ordered) against their `total`.

        Zero targets is not a split, and one target is already the meter's own
        target bar — so both cases hide the section rather than showing a row
        that says the same thing twice.

        `estimated_keys` are the `TargetParse.key`s of the rows the tracker had
        to build without a per-dummy address (see `core.dps_dummy.
        dummy_split_targets`) — the row that ABSORBED the address-less hits,
        and only that row. It is marked on the row itself (a `~` on its number,
        in warning amber, with its own tooltip), so a partly-addressed yard says
        WHICH row is a guess; the header keeps the plain total and carries the
        explanation on hover.
        """
        if len(targets) < 2:
            self.hide()
            return
        est = set(estimated_keys or ())
        while len(self._rows) < len(targets):
            row = _TargetRow(self.box, on_select=self._on_select)
            self._rows.append(row)
            self.box.rows.addWidget(row)
        labels = _labels(targets)
        for i, tgt in enumerate(targets):
            row = self._rows[i]
            row.set_key(tgt.key)
            row.set_pinned(tgt.key == pinned_key)
            row.set_estimated(tgt.key in est)
            row.update_stats(labels[i], tgt, total, duration)
            row.show()
        for row in self._rows[len(targets):]:
            row.hide()
        self.box.header.set_text(f"SPLIT BY TARGET · {len(targets)}")
        self.box.header.set_tag(f"{total:,.0f} total")
        self.box.header.setToolTip(ESTIMATE_SPLIT_TIP if est else "")
        self.show()


# --- the vs-last-test baseline -----------------------------------------#

# A HUD has room for a handful of skills, not a whole rotation. The rest are
# counted in the "+N more skills" line rather than silently dropped, so a long
# comparison reads as "there is more here" instead of "that is all of it".
MAX_ROWS = 5

def _fmt_delta(row) -> str:
    """The delta as a signed number plus a percent when one is meaningful."""
    text = f"{row.delta:+,.0f}/s"
    pct = row.delta_pct
    if pct is not None:
        text += f"  {pct:+.1f}%"
    return text


def _age_text(seconds: float) -> str:
    """How long ago the reference test ran, in words a player would use."""
    if seconds < 90.0:
        return "just now"
    minutes = seconds / 60.0
    if minutes < 90.0:
        return f"{int(minutes)}m ago"
    hours = minutes / 60.0
    if hours < 36.0:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24.0)}d ago"


class _DeltaRow(QtWidgets.QFrame):
    """Skill name, its live DPS, and the change against the reference."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "_DeltaRow { background-color: rgba(22, 24, 29, 0.35); "
            "border-radius: 4px; }")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 1, 6, 1)
        lay.setSpacing(6)

        self.name_lbl = _ElideLabel("", 132)
        self.name_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
        self.name_lbl.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                    QtWidgets.QSizePolicy.Preferred)
        lay.addWidget(self.name_lbl, 1)

        self.live_lbl = QtWidgets.QLabel("0")
        self.live_lbl.setObjectName("DeltaLive")
        self.live_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.DIM}; "
            f"font-family: 'Consolas', monospace;")
        self.live_lbl.setMinimumWidth(56)
        self.live_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(self.live_lbl, 0)

        self.delta_lbl = QtWidgets.QLabel("+0")
        self.delta_lbl.setObjectName("DeltaValue")
        self.delta_lbl.setMinimumWidth(96)
        self.delta_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(self.delta_lbl, 0)

    def update_row(self, row) -> None:
        self.name_lbl.set_full(row.name or row.skill_id)
        self.live_lbl.setText(f"{row.live_dps:,.0f}/s")
        up = row.delta > 0.0
        flat = abs(row.delta) < 0.5
        color = theme.DIM if flat else (theme.GOOD if up else theme.DANGER)
        self.delta_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 800; color: {color}; "
            f"font-family: 'Consolas', monospace;")
        self.delta_lbl.setText("—" if flat else _fmt_delta(row))
        pct = row.delta_pct
        self.setToolTip(
            f"{row.name or row.skill_id}: {row.live_dps:,.1f}/s now, "
            f"{row.base_dps:,.1f}/s in the reference test"
            + ("" if pct is None else f"  ({pct:+.1f}%)"))


class DirtyRunStrip(QtWidgets.QFrame):
    """"This run is not a clean measurement" — the one warning the test needs.

    A dummy does not fight back, so damage taken during a test is either the
    rotation (a dot, a reflect) or the world walking in, and in both cases the
    DPS on screen is not the DPS of the build. It sits beside the HUD window
    rather than inside it so the rule, the number and the sentence that
    explains them stay in one file with the rest of the test surface.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DirtyRun")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(0)
        self.lbl = QtWidgets.QLabel("")
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 800; color: {theme.ORANGE};")
        lay.addWidget(self.lbl)
        self.setToolTip(
            "A training dummy does not fight back, so damage taken during a "
            "test is your own rotation (a dot, a reflect) or the world walking "
            "in. Either way this run's DPS is not a clean measurement of the "
            "build — the run is still recorded, it is just not one to compare.")
        self.hide()

    def update_share(self, share) -> None:
        """Show the warning when the run is over the limit, else hide.

        The number is always in the taken readout; this line is the claim about
        what it means, so it appears only when the claim changes. The measure is
        a share of the test's own damage rather than an absolute, because the
        two runs being compared can be different lengths.
        """
        if share is None or not getattr(share, "dirty", False):
            self.hide()
            return
        self.lbl.setText(
            f"⚠ {share.pct:,.1f}% of this test's damage came back at you "
            f"(over {share.limit_pct:,.0f}%) — not a clean measurement")
        self.show()

    def clear_share(self) -> None:
        self.hide()


class BaselineDelta(QtWidgets.QWidget):
    """The whole test's per-skill delta against the remembered one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.box = _Section("VS LAST TEST", theme.ACCENT_LIGHT)
        lay.addWidget(self.box)
        self._rows: list[_DeltaRow] = []
        # The personal-best line: "VS LAST TEST" answers whether the last
        # change helped, and this answers whether the gear is actually better
        # than anything this character has ever done. They are separate numbers
        # and conflating them is the mistake — a run worse than your best but
        # better than last time is a normal Tuesday, and it should read as both.
        self.best_lbl = QtWidgets.QLabel("")
        self.best_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {theme.DIM};")
        self.best_lbl.setContentsMargins(6, 0, 6, 1)
        self.best_lbl.hide()
        self.box.rows.addWidget(self.best_lbl)
        self.more_lbl = QtWidgets.QLabel("")
        self.more_lbl.setStyleSheet(
            f"font-size: 9px; color: {theme.DIM};")
        self.more_lbl.setContentsMargins(6, 0, 0, 2)
        self.more_lbl.hide()
        self.box.rows.addWidget(self.more_lbl)
        self.hide()

    def update_baseline(self, cmp, best=None) -> None:
        """Render `cmp` (a `DummyBaseline`) and `best` (a `BestDelta`), or hide
        when there is nothing to say.

        The test's own total delta rides the section header, because that is the
        number a player opens the HUD for; the rows are how they find out which
        skill moved it. The best line sits above the rows because it is the
        coarser question — did this run beat the record — and reading it last
        would mean reading the detail before knowing what it is detail OF.
        """
        rows = list(getattr(cmp, "rows", None) or [])
        has_best = bool(getattr(best, "has_best", False))
        if not rows and not has_best:
            self.hide()
            return
        while len(self._rows) < min(len(rows), MAX_ROWS):
            row = _DeltaRow(self.box)
            self._rows.append(row)
            # Above the best line, which is the summary the detail sits under.
            self.box.rows.insertWidget(self.box.rows.indexOf(self.best_lbl), row)
        for i, row in enumerate(rows[:MAX_ROWS]):
            self._rows[i].update_row(row)
            self._rows[i].show()
        for row in self._rows[MAX_ROWS:]:
            row.hide()
        extra = len(rows) - MAX_ROWS
        if extra > 0:
            self.more_lbl.setText(f"+{extra} more skill" + ("s" if extra > 1 else ""))
            self.more_lbl.show()
        else:
            self.more_lbl.hide()
        self._render_best(best)
        if not rows:
            # A best with no reference to compare against still has something
            # to say, so the section stays up with just that line.
            self.box.header.set_text("VS BEST")
            self.box.header.set_tag("")
            self.show()
            return
        pct = getattr(cmp, "delta_pct", None)
        total = getattr(cmp, "delta", 0.0)
        self.box.header.set_text("VS LAST TEST")
        self.box.header.set_tag(
            f"{total:+,.0f}/s" + ("" if pct is None else f"  {pct:+.1f}%")
            + f"  ·  {_age_text(getattr(cmp, 'age_s', 0.0))}")
        # The comparison is only as good as the run it is against: if the
        # REFERENCE took damage, say so where a player about to trust the
        # number will meet it. The tooltip, not the tag — a HUD this small has
        # no room for a second caveat, and this is the one that explains the
        # delta rather than restating it.
        ref_taken = getattr(cmp, "taken_pct", 0.0) or 0.0
        tip = (f"{getattr(cmp, 'live_dps', 0.0):,.0f}/s now vs "
               f"{getattr(cmp, 'base_dps', 0.0):,.0f}/s in the reference test")
        if ref_taken > 0.0:
            tip += (f"\nThat reference test took {ref_taken:,.1f}% of its own "
                    f"damage — it was not a clean measurement either.")
        self.setToolTip(tip)
        self.show()

    def _render_best(self, best) -> None:
        """The personal-best line, or hidden when there is no best yet.

        A run that has just beaten the record says so in gold and carries no
        percentage: it has nothing left to be measured against, and "+0.0%"
        next to the words NEW BEST is the least useful thing this HUD could
        print. Every other run gets the gap to the ceiling, which is the number
        that makes a regression obvious at a glance.
        """
        if not getattr(best, "has_best", False):
            self.best_lbl.hide()
            return
        self.best_lbl.show()
        if getattr(best, "is_best", False):
            self.best_lbl.setText(f"★ NEW BEST {best.live_dps:,.0f}/s")
            self.best_lbl.setStyleSheet(
                f"font-size: 9px; font-weight: 800; color: {theme.GOLD};")
            self.best_lbl.setToolTip(
                f"This run beat every run before it: {best.live_dps:,.0f}/s.")
            return
        gap = getattr(best, "gap_pct", None)
        up = gap is not None and gap >= 0.0
        color = theme.GOOD if up else theme.DANGER
        self.best_lbl.setText(
            f"BEST {best.best_dps:,.0f}/s · {_age_text(best.age_s)} · "
            + ("—" if gap is None else f"{gap:+.1f}%"))
        self.best_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 800; color: {color};")
        self.best_lbl.setToolTip(
            f"Your best dummy test: {best.best_dps:,.0f}/s over "
            f"{best.best_duration_s:,.0f}s, run {_age_text(best.age_s)}.\n"
            f"This run: {best.live_dps:,.0f}/s."
            + (f"\nThat best test took {best.best_taken_pct:,.1f}% of its own "
               f"damage — not a clean measurement either."
               if (getattr(best, "best_taken_pct", 0.0) or 0.0) > 0.0 else ""))

    def clear_baseline(self) -> None:
        """Hide with the session it measured (meter Reset)."""
        self.best_lbl.hide()
        self.hide()


# --- the test's control, shared by both windows -----------------------#

class DummyTestControlMixin:
    """Stop / Start Test button for the Test Dummy HUD.

    Mixed into `DummyOverlay`, which supplies the `tracker` (a DpsTracker) and
    the `s` settings this reads. Both are read defensively: an overlay can be
    built before a model has attached, and a missing attribute must never raise
    out of a 10 Hz tick.
    """

    def _build_dummy_control(self, row, qss: str) -> None:
        """Add the Stop / Start Test button to the overlay's control row.

        Built hidden: `_sync_dummy_btn` shows it once a dummy is actually in
        range of the last scene scan, so dungeon play never carries it.
        """
        self.dummy_btn = QtWidgets.QPushButton("🎯 Stop Test")
        self.dummy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.dummy_btn.setStyleSheet(qss)
        self.dummy_btn.clicked.connect(self._toggle_dummy_test)
        row.addWidget(self.dummy_btn)
        self.dummy_btn.hide()

    def _toggle_dummy_test(self) -> None:
        """Stop / start the training-dummy test from the HUD itself.

        The dummy is the one fight nothing in the scene ever concludes, so this
        button IS its end signal: Stop archives the finished test and latches the
        engagement off (not even the next scene scan re-opens it — see
        `DpsTracker.stop_dummy_test`), Start re-arms it for the next scan.
        """
        try:
            if bool(getattr(self.tracker, "dummy_test_armed", True)):
                self.tracker.stop_dummy_test()
            else:
                self.tracker.start_dummy_test()
        except Exception:
            pass
        self._tick()

    def _build_dummy_split(self, body) -> None:
        """Add the per-target split section to the HUD's body layout.

        Its top half is the dummy cluster: several dummies standing together are
        ONE session by design (that is what keeps the group total right), so the
        split is the only thing that can say which of them took what. It sits
        right under the TOP DPS section and stays hidden until the session holds
        two or more targets — targets.py owns that rendering.

        The rows are also the target picker: clicking one pins that dummy as the
        board's target (the tracker's rule, `select_dummy_target`), and the
        pinned row is the one highlighted.
        """
        self.split_box = TargetSplit(on_select=self._select_dummy_target)
        body.insertWidget(1, self.split_box)
        # The clean-run warning sits ABOVE the split, because the split and the
        # comparison below it are exactly the things it is warning about.
        self._build_dummy_dirty(body)
        self._build_dummy_baseline(body)
        # The replay strip closes the stack: what the run did (top), which dummy
        # took it (split), how it compares (baseline), and WHEN it went quiet
        # (replay). Appends, so its index does not depend on the inserts above.
        self._build_dummy_replay(body)

    def _build_dummy_dirty(self, body) -> None:
        """Add the "not a clean measurement" strip to a surface's body.

        Built by the mixin rather than inline in the window: the rule is core's,
        the number is the player's, and the one surface that renders a test
        should not be assembling its own copy of either.
        """
        self.dirty_strip = DirtyRunStrip()
        body.insertWidget(1, self.dirty_strip)

    def _update_dummy_dirty(self, session) -> None:
        """Refresh the clean-run flag for the session on screen (10 Hz tick, so
        defensive like every other mixin method)."""
        try:
            self.dirty_strip.update_share(
                self.tracker.dummy_taken_share(session))
        except Exception:
            self.dirty_strip.clear_share()

    def _select_dummy_target(self, key) -> None:
        """A split row was clicked: pin it as the test's target (or unpin).

        Defensive like every other mixin method that a 10 Hz tick and a mouse
        press both reach: a click must never raise out of a Qt event handler,
        and the next tick repaints whatever the tracker decided.
        """
        try:
            self.tracker.select_dummy_target(key)
        except Exception:
            pass

    def _reset_dummy_split(self) -> None:
        """Drop the split (and the baseline) with the session they measured."""
        self.split_box.update_targets([], 0.0, 0.0)
        self.dirty_strip.clear_share()
        self._reset_dummy_baseline()
        self._reset_dummy_replay()

    def _update_dummy_split(self, session, duration: float) -> None:
        """Refresh the per-target split for the session on screen.

        The widget decides whether a split is worth showing (two or more
        targets); this just hands it the session's own numbers, so the rows and
        the total they add up to come from the same place the meter's totals do
        and cannot disagree with them. `pinned_key` is the row the session's
        header is describing, so the highlight follows the rule the tracker
        applies rather than a second guess at it here.

        The rows are the tracker's `dummy_split_targets`: the SCENE's whole
        dummy cluster (every in-range dummy), each with the per-address damage
        when the decoded hits named one — so a yard of three dummies lists all
        three rather than collapsing to the one address the events happened to
        carry. The row that absorbed the hits which carried NO address is the
        one the tracker marks estimated, and only it is marked.
        """
        try:
            targets = self.tracker.dummy_split_targets(session)
        except Exception:
            targets = session.ranked_targets()
        try:
            pinned = self._pinned_key(session)
            try:
                estimated = self.tracker.dummy_split_estimated_keys()
            except Exception:
                estimated = ()
            self.split_box.update_targets(targets, session.target_total, duration,
                                          pinned_key=pinned,
                                          estimated_keys=estimated)
        except Exception:
            self.split_box.hide()
        self._update_dummy_baseline(session)
        self._update_dummy_dirty(session)
        self._update_dummy_replay(session)

    def _build_dummy_baseline(self, body) -> None:
        """Add the "vs last test" section under the split.

        The point of a training yard is that the same rotation can be measured
        twice against something that never moves, so the section that says what
        changed belongs beside the split that says which dummy took it. Built
        hidden and shown only when there is a reference test to compare against
        (see baseline.py).
        """
        self.baseline_box = BaselineDelta()
        body.insertWidget(2, self.baseline_box)

    def _update_dummy_baseline(self, session) -> None:
        """Refresh the per-skill delta against the remembered test.

        The comparison is core's (`dummy_baseline_deltas`), which answers empty
        for anything that is not the dummy test — a boss or trash board has no
        reference run, and a baseline section on one would be a lie. A surface
        cannot raise here either: this runs on a 10 Hz tick.
        """
        try:
            self.baseline_box.update_baseline(
                self.tracker.dummy_baseline_deltas(session),
                self.tracker.dummy_best_deltas(session))
        except Exception:
            self.baseline_box.clear_baseline()

    def _reset_dummy_baseline(self) -> None:
        self.baseline_box.clear_baseline()

    def _build_dummy_replay(self, body) -> None:
        """Add the finished-test replay strip under the split.

        The one thing a total cannot say: WHEN the rotation went quiet. The
        panel reads the finished session's own per-second timeline (see
        dummy_replay.py) and hides itself when there is nothing to play.
        """
        self.replay_box = DummyReplay()
        body.addWidget(self.replay_box)

    def _update_dummy_replay(self, session) -> None:
        """Replay a FINISHED test; a live one has no shape to read yet.

        A running test's timeline grows every second, so a strip of it would
        only be a spikier copy of the DPS two lines above. `in_dummy_fight` is
        the same latch the Stop/Start button reads, so "finished" here matches
        "the button says Start" everywhere else. Defensive: this runs at 10 Hz.
        """
        try:
            if getattr(self.tracker, "in_dummy_fight", False):
                self.replay_box.clear()
            else:
                self.replay_box.update_session(session)
        except Exception:
            self.replay_box.clear()

    def _reset_dummy_replay(self) -> None:
        self.replay_box.clear()

    @staticmethod
    def _pinned_key(session) -> int | str | None:
        """The split row the session's header is currently describing."""
        try:
            bucket = session.pinned_bucket()
        except Exception:
            return None
        return bucket.key if bucket is not None else None

    def _sync_dummy_btn(self) -> None:
        """Label the control with the latch it drives, and show it only where it
        can matter.

        A dummy INSIDE the test's own radius keeps it visible — the scan keeps
        the dummy in `dummy_near` even while stopped, which is what leaves Start
        Test reachable after a Stop, but presence alone is not enough: the same
        radius is what `_engage_dummy` and `_track_dummy_target` compare against,
        so a control offered at a dummy 40 m away would arm a test that can never
        open (and a Stop Test is what the hint beside it says is reachable). A
        test already RUNNING keeps it whatever the radius says, so the button
        that ends one is never taken away mid-run.

        A tracker double with no `dummy_in_range` falls back to bare presence,
        which is the answer this method gave before the radius was consulted.
        """
        live = bool(getattr(self.tracker, "in_dummy_fight", False))
        if live:
            near = True
        else:
            try:
                near = bool(self.tracker.dummy_in_range())
            except Exception:
                near = getattr(self.tracker, "dummy_near", None) is not None
        armed = bool(getattr(self.tracker, "dummy_test_armed", True))
        self.dummy_btn.setText("🎯 Stop Test" if armed else "▶ Start Test")
        self.dummy_btn.setVisible(live or near)

    def _dummy_test_hint(self, default: str) -> str:
        """The empty-state line for standing at a training dummy, else `default`.

        Reads the tracker's own radius predicate — the same one the overlay
        manager's Rule F uses through `_at_training_dummy` — so the HUD can
        never prompt "attack the dummy" while the visibility gate is hiding it.
        The 5 fallback mirrors the `dps_dummy_range` default, so a settings
        double behaves like a fresh install rather than like the 0 = uncapped
        radius. A STOPPED test gets its own line: inviting the player to attack a
        dummy the tracker has been told to ignore is how a HUD looks broken.
        """
        rng = getattr(self.s, "dps_dummy_range", 5) if self.s else 5
        try:
            if self.tracker.dummy_within(rng):
                if not bool(getattr(self.tracker, "dummy_test_armed", True)):
                    return "🎯 Test stopped — press Start Test."
                return "🎯 Attack the dummy to start."
        except Exception:
            pass
        return default
