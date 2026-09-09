"""The Top DPS Meter's small parts: chips, mode pill, capture badge, target line.

Everything here is meter chrome small enough to share one file - the overlay
window itself (`overlay.py`) is what sits at the ui/ line budget, and its
satellites were one file each. They are the meter's own widgets, and two of them
are shared with the Test Dummy HUD (which reuses `_StatChip` and the capture
badge), so this is the library both HUDs are assembled from.

- `_StatChip` / `_ModePill` - the group-metric chips and the DMG/HEAL/BOTH pill
- `_CaptureBadge` / `insert_into_titlebar` - the title-bar capture-state badge
- `upcoming_boss_name` / `render` - the target line: one label with three states
  (the live target and its HP bar, a boss an instance has declared but not yet
  engaged, or nothing)

`_StatChip` and `_ModePill` are re-imported by `overlay.py`, so existing call
sites (and tests) are unchanged.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ... import theme
from ....core import game_state
from ....core.dps_tracker import DpsTracker
from ....data import names as _names


def install_dense_chrome(win) -> None:
    """The dense HUD body: tight margins, tight spacing.

    Both the meter and the dummy board are read as numbers rather than as
    chrome, so the content gets the pixels instead of the padding."""
    win.content.setContentsMargins(4, 2, 4, 2)
    win.content.setSpacing(4)


def install_dps_sources(win, model, settings) -> None:
    """Record the model/settings and aim BOTH log sources at `win.log`.

    The tracker's roster join/leave lines and the damage reader's capture
    diagnostics land in the same Activity Log sink, so a board reading zero
    can be explained from one place. `getattr(model, "dps", None) or
    DpsTracker(model)` is what makes the two HUDs share the model's tracker
    when it already built one."""
    win.model = model
    win.s = settings
    win.tracker = getattr(model, "dps", None) or DpsTracker(model)
    aim_log_sinks(win, model, win.tracker)


def aim_log_sinks(win, model, tracker) -> None:
    """Aim `tracker`'s and `model.damage`'s `log_line` at `win.log`.

    Both sources report into the window's own Activity Log sink, so a board
    reading zero can be explained from one place. Each half is skipped when its
    source is absent - the normal case for a model with no capture on it yet.
    """
    if tracker is not None:
        tracker.log_line = win.log.emit
    damage = getattr(model, "damage", None) if model is not None else None
    if damage is not None:
        damage.log_line = win.log.emit


def adopt_live_tracker(win) -> None:
    """Re-adopt the model's live tracker when `win` has drifted onto an old one.

    A HUD outlives reattaches and mode churn, and an orphaned tracker shows
    frozen zeros while hits are actually flowing, so every tick re-checks the
    binding."""
    live = getattr(win.model, "dps", None)
    if live is not None and win.tracker is not live:
        win.tracker = live
        win.tracker.log_line = win.log.emit


def install_titlebar_chrome(win) -> None:
    """The shared HUD title bar: flat panel, rich text, the heavy title font.

    Set once, up front, so a window's own title `setText` renders as rich text
    from its first paint rather than being re-parsed by a later format call."""
    win.titlebar.setStyleSheet(
        f"QFrame#TitleBar {{ background: {theme.PANEL}; border: 0; }}")
    win.titlebar.title.setTextFormat(QtCore.Qt.RichText)
    win.titlebar.title.setStyleSheet(
        f"color: {theme.TEXT}; font-weight: 800; font-size: 13px; "
        f"letter-spacing: 1px;")


class _StatChip(QtWidgets.QFrame):
    """Compact group-metric card: dim label, a big focus value, a small sub.

    Which number is the FOCUS is the caller's choice, and there are two shapes:

    - `set_rate(rate, total)` puts the per-second RATE in the big bold line and
      the running total underneath. This is the Top DPS HUD's shape (live
      2026-10-03: "the dp/s h/s damage taken /s should be the main focus
      numbers") - the /s number is what a player watches while fighting, and
      the total is the receipt.
    - `set_value(total, rate)` keeps the total on top, the Test Dummy HUD's
      shape: a test is read as "what did I do", and the run is re-measured
      rather than watched.

    Both write the same two labels, so a chip can switch shapes without being
    rebuilt - the stylesheets are re-applied on every set.
    """

    # The big line is the same either way: white, heavy, monospaced tabs.
    _FOCUS_QSS = ("font-size: 15px; font-weight: 900; color: #ffffff; "
                  "font-family: 'Consolas', monospace;")
    # A rate under a total: the accent cyan, so it still reads as the live
    # number even when it is the small one.
    _SUB_RATE_QSS = ("font-size: 9px; font-weight: 700; color: #00e5ff; "
                     "font-family: 'Consolas', monospace;")
    # A total under a rate: deliberately plain. Two accented numbers in one
    # chip would compete for the eye, which is the thing this layout is for.
    _SUB_TOTAL_QSS = ("font-size: 9px; font-weight: 700; color: #8b9bb4; "
                      "font-family: 'Consolas', monospace;")

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "_StatChip { background-color: rgba(22, 27, 34, 0.85); "
            "border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 6px; padding: 4px 6px; }"
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 5)
        lay.setSpacing(0)
        self.title_lbl = QtWidgets.QLabel(title.upper())
        self.title_lbl.setStyleSheet(
            "font-size: 8px; font-weight: 800; color: #8b9bb4; "
            "letter-spacing: 1.2px;")
        lay.addWidget(self.title_lbl)
        self.val_lbl = QtWidgets.QLabel("0")
        self.val_lbl.setStyleSheet(self._FOCUS_QSS)
        lay.addWidget(self.val_lbl)
        self.sub_lbl = QtWidgets.QLabel("0/s")
        self.sub_lbl.setStyleSheet(self._SUB_RATE_QSS)
        lay.addWidget(self.sub_lbl)

    def set_value(self, val: str, sub: str = ""):
        """Total as the focus, with the /s rate beneath it (dummy HUD)."""
        self.val_lbl.setText(val)
        self.val_lbl.setStyleSheet(self._FOCUS_QSS)
        if sub:
            self.sub_lbl.setText(sub)
            self.sub_lbl.setStyleSheet(self._SUB_RATE_QSS)

    def set_rate(self, rate: str, total: str = ""):
        """Rate as the focus, with the running total beneath it (Top DPS).

        The label the player actually watches mid-fight is the per-second
        number; the total stays one line down so the receipt is still there.
        """
        self.val_lbl.setText(rate)
        self.val_lbl.setStyleSheet(self._FOCUS_QSS)
        if total:
            self.sub_lbl.setText(total)
            self.sub_lbl.setStyleSheet(self._SUB_TOTAL_QSS)


class _ModePill(QtWidgets.QFrame):
    """Compact DMG / HEAL / BOTH segmented pill."""
    currentChanged = QtCore.Signal(str)

    def __init__(self, current: str = "DMG", parent=None):
        super().__init__(parent)
        self._accent = theme.ACCENT
        # `.QFrame` (exact class): a bare `QFrame` rule cascades to every
        # QLabel inside (QLabel IS-A QFrame); this pill has none today, but
        # the DMG/HEAL/BOTH buttons' own styles are self-contained and the
        # scoping keeps it that way under future edits
        self.setStyleSheet(
            f".QFrame {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 9px; }}")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(2)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns: dict[str, QtWidgets.QPushButton] = {}
        for opt in ("DMG", "HEAL", "BOTH"):
            b = QtWidgets.QPushButton(opt)
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(16)
            b.setMinimumWidth(34)
            self._group.addButton(b)
            self._btns[opt] = b
            lay.addWidget(b)
        self._group.buttonClicked.connect(self._on_clicked)
        self.setCurrentText(current)

    def _on_clicked(self, b: QtWidgets.QPushButton) -> None:
        """Restyle on click: QButtonGroup flips the checked state but the
        pill's visual highlight comes from setCurrentText — without this the
        clicked chip would stay in its unchecked (transparent) style."""
        self.setCurrentText(b.text())
        self.currentChanged.emit(b.text())

    def _accent_color(self) -> str:
        return self._accent or theme.ACCENT

    def _style_btn(self, b: QtWidgets.QPushButton, checked: bool) -> None:
        if checked:
            b.setStyleSheet(
                f"QPushButton {{ border: 0; border-radius: 7px; padding: 0 8px; "
                f"font-size: 9px; font-weight: 800; letter-spacing: 0.5px; "
                f"background: {self._accent_color()}; color: {theme.ON_ACCENT}; }}")
        else:
            b.setStyleSheet(
                f"QPushButton {{ border: 0; border-radius: 7px; padding: 0 8px; "
                f"font-size: 9px; font-weight: 800; letter-spacing: 0.5px; "
                f"background: transparent; color: {theme.DIM}; }}"
                f"QPushButton:hover {{ color: {theme.TEXT}; }}")

    def setCurrentText(self, text: str) -> None:
        for opt, b in self._btns.items():
            on = (opt.upper() == str(text or "").upper())
            b.setChecked(on)
            self._style_btn(b, on)

    def currentText(self) -> str:
        b = self._group.checkedButton()
        return b.text() if b else ""

    def retint(self, accent: str) -> None:
        self._accent = accent or theme.ACCENT
        for b in self._btns.values():
            self._style_btn(b, b.isChecked())


class _CaptureBadge(QtWidgets.QLabel):
    """One-word capture-state pill; hidden while there is nothing to say.

    Clickable while stuck (stale pool / calibrating): the overlay wires
    on_click to a real rescan (forced re-hunt + re-derive), so the badge is
    the "unstick capture" button instead of a dead warning.
    """

    _SS = ("font-size: 9px; font-weight: 800; letter-spacing: 1px; "
           "color: {color}; border: 1px solid {color}; "
           "border-radius: 7px; padding: 1px 6px; background: transparent;")

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CapBadge")
        self.on_click = None
        self.setStyleSheet(self._SS.format(color=theme.MUTED))
        self.hide()

    def set_click(self, cb) -> None:
        """Arm (or clear) the click-to-rescan action + pointer affordance."""
        self.on_click = cb
        self.setCursor(QtCore.Qt.PointingHandCursor if callable(cb)
                       else QtCore.Qt.ArrowCursor)

    def mousePressEvent(self, e) -> None:
        cb = getattr(self, "on_click", None)
        if callable(cb) and self.isVisible():
            try:
                cb()
            except Exception:
                pass
        super().mousePressEvent(e)

    def apply(self, text: str, color: str, tooltip: str) -> None:
        """Show ``text`` tinted ``color`` with ``tooltip`` as the diagnosis."""
        if self.text() != text:
            self.setText(text)
        self.setStyleSheet(self._SS.format(color=color))
        self.setToolTip(tooltip)
        self.show()

    def clear(self) -> None:
        """Nothing to say (no source / capture off): hide the badge."""
        self.setToolTip("")
        self.hide()


def insert_into_titlebar(badge: QtWidgets.QWidget, extra_layout) -> None:
    """Seat the badge right after the window title, before the nav buttons."""
    extra_layout.insertWidget(1, badge, 0, QtCore.Qt.AlignVCenter)


def upcoming_boss_name(model, tracker, session) -> str:
    """The instance's DECLARED boss, named while its pre-boss trash runs.

    Only for the trash fight itself (never for a boss, a dummy test or the
    open world), and only while the boss is not already engaged - once the
    real fight starts the target line is the live one with its HP bar.
    """
    boss = getattr(tracker, "_declared_boss", None) if tracker is not None else None
    if (tracker is None or not boss
            or getattr(tracker, "in_boss_fight", False)
            or getattr(tracker, "in_dummy_fight", False)
            or not game_state.in_instance(model)
            or (getattr(session, "kind", "") or "").lower() != "trash"):
        return ""
    return f"👑 {_names.unit_name(boss) or boss} · next"


def _adds_suffix(overlay, session) -> str:
    """"+ trash" when a boss fight is also being fought on its adds.

    The boss board wins the session choice while a boss is engaged (see
    `DpsTracker.session_for`), so the line would name the boss even while the
    player is hitting adds — which reads as the meter tracking the wrong thing
    (reported live 2026-10-03: "it is showing the boss as the target").

    Naming it honestly instead of flipping the whole board: the boss is still
    the session, so its HP bar still tracks the boss, but the label says the
    boss AND its trash are both being hit. Flashing between names on every
    damage tick would be worse than either answer.
    """
    tracker = getattr(overlay, "tracker", None)
    if tracker is None or not getattr(tracker, "in_boss_fight", False):
        return ""
    if session is not getattr(tracker, "boss_session", None):
        return ""
    adds = getattr(tracker, "boss_adds_session", None)
    if adds is not None and float(getattr(adds, "group_damage", 0.0) or 0.0) > 0.0:
        return " + trash"
    return ""


def render(overlay, session) -> None:
    """Paint the target label / HP bar for `session` (or the upcoming boss).

    `overlay` is the meter: this reads and writes its `target_lbl`,
    `target_hp_lbl` and `target_bar`, and asks it for the model + tracker.
    """
    upcoming = upcoming_boss_name(overlay.model, getattr(overlay, "tracker", None),
                                  session)
    if session.target_name != "None" and session.target_max_hp > 0:
        pct = max(0.0, min(100.0, (session.target_hp / session.target_max_hp) * 100.0))
        clean_name = session.target_name.replace("👑 ", "").replace("🎯 ", "")
        prefix = "👑 " if "👑" in session.target_name else "🎯 "
        overlay.target_lbl.setText(f"{prefix}{clean_name}{_adds_suffix(overlay, session)}")
        overlay.target_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.TEXT};")
        overlay.target_hp_lbl.setText(
            f"{session.target_hp:,.0f} / {session.target_max_hp:,.0f} · {pct:.1f}%")
        overlay.target_bar.setValue(int(pct))
    elif upcoming:
        # Pre-boss trash in an instance that already NAMED its boss: the meter
        # holds the engagement back until the trash is done (see
        # DpsTrackerTick._scan_foes), so without this the target line sat at
        # "Target: None" through every pull even though the instance had told
        # us exactly which boss was coming.
        overlay.target_lbl.setText(upcoming)
        overlay.target_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.GOLD};")
        overlay.target_hp_lbl.setText("")
        overlay.target_bar.setValue(0)
    else:
        overlay.target_lbl.setText("Target: None")
        overlay.target_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {theme.MUTED};")
        overlay.target_hp_lbl.setText("")
        overlay.target_bar.setValue(0)
