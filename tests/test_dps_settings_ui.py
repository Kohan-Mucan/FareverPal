"""Offscreen UI tests for the Combat DPS Meter settings card.

The Top DPS overlay's cycle button was removed in favor of a proper settings
card (Settings > DPS > Combat DPS Meter): default view and row limits persist
through ``dps_view`` / ``dps_top_count`` / ``heals_top_count``, and the open
overlay adopts a view change live each tick while its own view chips still
write back to settings. There is no tracking-range control: capture is
zone-wide everywhere, so ``dps_max_dist`` is gone.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion.ui.layout import StretchFlow  # noqa: E402
from farever_companion.ui.pages.settings import SettingsPageMixin  # noqa: E402
from farever_companion.ui.overlays.dps import DpsOverlay  # noqa: E402

# An unwrapped label makes its own text the card's MINIMUM width. A line up to
# this many characters stays inside every flow card's existing minimum (the
# option cards bottom out at 412px, the meter/diagnostics cards around 600px),
# so section titles and field captions are the only labels allowed to skip wrap.
UNWRAPPED_LABEL_MAX_CHARS = 24


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _S:
    dps_view = "damage"
    dps_top_count = 8
    heals_top_count = 2
    dps_solo_only = True
    dps_mode = "proxy"
    dps_hook_enabled = False
    speedrun_bare = False
    speedrun_transparent = False

    def save(self):
        pass


class _Panel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.s = _S()
        self._settings_steppers = []
        self._calls = []

    def _set(self, attr, value):
        setattr(self.s, attr, value)
        self._calls.append((attr, value))


class Panel(_Panel, SettingsPageMixin):
    pass


def _card():
    p = Panel()
    p._dps_card = p._build_dps_card()   # keep the widget tree alive (no parent)
    # The Live Diagnostics card is built too: it owns the status / PID / source
    # labels the DPS tests assert on.
    p._dps_diag_card = p._build_dps_diagnostics_card()
    return p, dict(p._settings_steppers)


def _drain_diag_worker(p, timeout_s: float = 10.0) -> None:
    """Pump the event loop until p's background diagnostics scan lands.

    Never calls worker.wait() (that would block the loop the done signal
    needs); polls isRunning and lets the queued done deliver via pumps.
    """
    import time
    app = QtWidgets.QApplication.instance()
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        app.processEvents()
        w = getattr(p, "_dps_diag_worker", None)
        if w is None or not w.isRunning():
            break
        time.sleep(0.005)
    app.processEvents()
    app.processEvents()


def test_dps_card_builds_from_settings_and_persists_changes():
    p, st = _card()
    # No tracking-range control: capture is zone-wide, not a preference.
    assert not hasattr(p, "_dps_range_seg")
    assert p._dps_view_seg.currentText().lower() == "damage"
    assert set(st) == {"dps_top_count", "heals_top_count", "dps_log_keep_days",
                       "dps_fights_recent_days"}
    assert st["dps_top_count"].value() == 8
    assert st["heals_top_count"].value() == 2
    # 0 = keep every archived fight; the Combat page's Clear Old Logs button
    # reads this and deletes nothing while it is 0.
    assert st["dps_log_keep_days"].value() == 0
    st["dps_log_keep_days"].setValue(30)
    assert p.s.dps_log_keep_days == 30
    # The Fights list's default day window sits beside Keep logs; the list
    # reads it on every render (0 = show every day).
    assert st["dps_fights_recent_days"].value() == 7
    st["dps_fights_recent_days"].setValue(14)
    assert p.s.dps_fights_recent_days == 14

    p._set_dps_view("Both")
    assert p.s.dps_view == "both"

    st["dps_top_count"].setValue(12)
    assert p.s.dps_top_count == 12
    st["heals_top_count"].setValue(5)
    assert p.s.heals_top_count == 5


def test_dps_card_resync_surfaces_external_changes():
    """Overlay-side changes (view chips write settings) appear on the card
    again when the DPS tab is re-shown, without a restart."""
    p, st = _card()
    p.s.dps_view = "healing"
    p.s.dps_top_count = 3
    p._resync_dps_settings_widgets()
    assert p._dps_view_seg.currentText().lower() == "healing"
    assert st["dps_top_count"].value() == 3


def test_solo_toggle_moved_to_hud_card_but_setter_persists():
    """The solo-only toggle moved to Settings > HUD's > Top DPS, so the DPS
    engine card no longer carries it; the shared setter (used by that card
    and the overlay's Solo button) still persists flips through _set."""
    p, _ = _card()
    assert not hasattr(p, "_dps_solo_only_toggle")
    assert p.s.dps_solo_only is True
    p._set_dps_solo_only(False)
    assert p.s.dps_solo_only is False
    assert ("dps_solo_only", False) in p._calls
    p._set_dps_solo_only(True)
    assert p.s.dps_solo_only is True


def test_inject_feedback_never_reflows_the_dps_cards(qapp):
    """A long inject/uninject message must not push the meter card down.

    The Live Diagnostics card and the Combat DPS Meter sit in one `StretchFlow`
    row, which wraps when a card's MINIMUM width passes its share of the row.
    The feedback line was the only label in that card without word wrap, so its
    own sentence became the card's minimum width: un-injecting (Option 2 ->
    Option 3) set a message long enough to wrap the row, and COMBAT DPS METER
    jumped under LIVE DIAGNOSTICS. Wrapping it keeps the card's minimum at its
    content's width, so the row no longer moves — this pins both halves.
    """
    p = Panel()
    tab = p._tab_dps()
    host = QtWidgets.QScrollArea()
    host.setWidgetResizable(True)
    host.setWidget(tab)          # the page's own hosting (scrolls, never squeezes)
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(host)
    win.resize(1280, 900)
    win.show()

    def settle() -> None:
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

    def same_row() -> bool:
        return abs(p._dps_diag_card.geometry().y()
                   - p._dps_card.geometry().y()) <= 2

    try:
        settle()
        assert same_row(), "the meter and diagnostics cards are not side by side"
        clean_min = p._dps_diag_card.minimumSizeHint().width()

        long_msg = ("\u2715 Uninject failed: SafeUnload export missing in the "
                    "loaded module \u00b7 bridge still loaded (Option 3 active) "
                    "\u2014 see Activity Log")
        p._set_dps_feedback(long_msg, p._DPS_FEEDBACK_ERR)
        settle()

        fb = p._dps_inject_feedback
        assert fb.parentWidget() is p._dps_diag_card, \
            "the feedback line belongs to the diagnostics card (one copy only)"
        assert fb.isVisible() and fb.text() == long_msg
        assert p._dps_diag_card.minimumSizeHint().width() == clean_min, \
            "a status sentence must not become the card's minimum width"
        assert same_row(), "the feedback message reflowed the row"
        assert fb.height() >= fb.heightForWidth(fb.width()), \
            "the message is clipped instead of wrapped"

        p._set_dps_feedback("")
        settle()
        assert not fb.isVisible(), "an empty line should collapse"
        assert same_row()
    finally:
        win.hide()
        win.deleteLater()
        settle()


def _flow_rows(tab):
    """Every `StretchFlow` row on the tab, with the cards it lays out.

    The tab has no other kind of wrapping row, so this sweeps whatever exists
    rather than naming today's cards.
    """
    flows = [o for o in tab.findChildren(QtCore.QObject) if isinstance(o, StretchFlow)]
    rows = []
    for flow in flows:
        cards = [flow.itemAt(i).widget() for i in range(flow.count())]
        rows.append((flow, [c for c in cards if c is not None]))
    return rows


def _settle(times: int = 3) -> None:
    app = QtWidgets.QApplication.instance()
    for _ in range(times):
        app.processEvents()
def _settle_until_feedback(p, needle: str, timeout_s: float = 10.0) -> None:
    """Pump the event loop until `p._dps_inject_feedback` carries `needle`.

    The injector verdict arrives on a QUEUED signal from a worker thread (see
    `_connect_dps_inject_result` in dps_modes.py), so the delivery needs both
    the thread's own slice of the CPU and the queued slot to pass through the
    loop - neither of which the blind purges of `_settle` guarantee under
    full-suite load. `_drain_diag_worker` is this module's existing idiom for
    that: pump with a deadline, poll the observable, and never fail-fast - so
    a scheduling hiccup costs a retry, not a red test.
    """
    import time
    app = QtWidgets.QApplication.instance()
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        app.processEvents()
        if needle in p._dps_inject_feedback.text():
            return
        time.sleep(0.005)
    app.processEvents()



def _label_owner(p, lbl) -> str:
    return next((n for n, v in vars(p).items() if v is lbl), "<unattributed>")


def _show_dps_tab(p, width: int = 1600):
    tab = p._tab_dps()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(tab)
    win.resize(width, 900)
    win.show()
    _settle()
    return win, tab


def test_flow_card_labels_wrap_unless_they_are_short_literals(qapp):
    """No label in a flow row may hold long text unwrapped.

    `StretchFlow` wraps a card to the next row once the card's minimum width
    passes its equal share of the row, and an unwrapped `QLabel`'s minimum IS
    its whole text (481px for the port line, against a 613px card). Wrapping
    drops that to the longest word, which is what actually stopped the meter
    card jumping under Live Diagnostics. The port line was the last label
    exempt, on the strength of its text being fixed at construction.
    """
    p = Panel()
    win, tab = _show_dps_tab(p)
    try:
        rows = _flow_rows(tab)
        assert len(rows) >= 2, "the DPS tab's flow rows are gone; nothing was swept"
        assert sum(len(cards) for _, cards in rows) >= 5, \
            "the flow rows hold no cards; nothing was swept"

        offenders = []
        for _flow, cards in rows:
            for card in cards:
                for lbl in card.findChildren(QtWidgets.QLabel):
                    if lbl.wordWrap() or len(lbl.text()) <= UNWRAPPED_LABEL_MAX_CHARS:
                        continue
                    offenders.append(f"{_label_owner(p, lbl)} "
                                     f"({len(lbl.text())}ch): {lbl.text()[:44]!r}")
        assert not offenders, (
            "unwrapped labels inside a flow card: " + "; ".join(offenders)
            + " — a non-wrapping label's minimum is its full text, so its length"
              " becomes the card's minimum width and can reflow the row")
    finally:
        win.hide()
        win.deleteLater()
        _settle()


class _DiagModel:
    """Just enough model for the diagnostics painter's PID / source lines."""

    class _Proc:
        pid = 13116

    class _Damage:
        def live_source_name(self):
            return "Option 2: Injector (farever_dps.dll)"

    proc = _Proc()
    damage = _Damage()


_DIAG_BINFOS = (
    {"dll_name": "farever_dps.dll", "game_dir": "C:\\Farever"},
    {"dll_name": "version.dll", "game_dir": "C:\\Farever",
     "third_party_dinput8": True, "third_party_version": True},
    {"dll_name": "farever_dps.dll", "game_dir": "C:\\Farever",
     "third_party_version": True},
)


class _BridgeStatusModel:
    """Just enough model for the painter's status line (see _DiagModel).

    ``_Damage.status()`` is the one report the inject-failure retraction keys
    off: ``core/dps_bridge.status`` answers ``inject_failed`` while it still
    holds a problem, and anything else once the module or its heartbeat has
    been seen (which is also when it drops the stale reason).
    """

    class _Proc:
        pid = 4242

    class _Damage:
        def __init__(self, status):
            self._status = status

        def status(self):
            return self._status

        def set_mode(self, mode):
            self.mode = mode

        def live_source_name(self):
            return "Option 2: Injector (farever_dps.dll)"

    def __init__(self, status):
        self.proc = self._Proc()
        self.damage = self._Damage(status)


_INJECT_REASON = "LoadLibraryW failed in target process"


def _inject_panel(status: str = "live"):
    """Diagnostics card + model, wired the way the page wires them."""
    p, _ = _card()
    p.s.dps_mode = "injector"
    p._connect_dps_inject_result()
    p.model = _BridgeStatusModel(status)
    p._dps_diag_binfo = {"detected": True, "option": 2,
                         "dll_name": "farever_dps.dll", "game_dir": "C:/Farever"}
    return p


def test_inject_refusal_revises_the_line_it_was_sent_with():
    """A refused inject must not leave "✓ Inject command sent" standing.

    The line is written optimistically, before the injector has run, and the
    verdict arrives on the launcher's worker thread — so this drives the real
    entry point from a real thread, which also proves the write lands on the
    UI thread instead of reaching into a widget from the injector's.
    """
    import threading

    p = _inject_panel()
    p._set_dps_feedback("✓ Inject command sent to PID 4242", p._DPS_FEEDBACK_OK)

    launcher = threading.Thread(target=p._on_dps_inject_result,
                               args=(False, _INJECT_REASON))
    launcher.start()
    launcher.join()
    # The verdict arrives on a queued signal from that thread, so blind
    # purges cannot guarantee it has landed: wait for the line the same
    # way _drain_diag_worker waits for the scan (see the helper).
    _settle_until_feedback(p, "Inject failed")
    fb = p._dps_inject_feedback
    assert "Inject failed" in fb.text()
    assert _INJECT_REASON in fb.text(), \
        "the reason is the actionable half of the message"
    # isHidden, not isVisible: this panel is never shown, and a widget whose
    # ancestors are hidden reports isVisible() False however setVisible went.
    assert not fb.isHidden(), "the line must be shown, not merely filled"
    assert p._DPS_FEEDBACK_ERR in fb.styleSheet(), "a refusal must not stay green"


def test_switching_to_option_2_arms_the_verdict_before_launching(monkeypatch):
    """The connect must happen on the real path, not only in these tests.

    A refusal can only revise the card if the launch site armed the signal
    first; skip that and every other test here still passes while production
    keeps showing the stale green line. The fake injector answers from its
    own thread, so this also pins the ordering: the queued verdict lands
    AFTER the optimistic line, and cannot be overwritten by it.
    """
    import threading

    p, _ = _card()
    p.proc = _BridgeStatusModel._Proc()
    p.s.dps_mode = "proxy"
    p.log = lambda msg: None
    p._dps_diag_binfo = {"detected": False, "option": 0, "dll_name": "",
                         "game_dir": "C:/Farever"}
    monkeypatch.setattr(p, "_refresh_dps_diagnostics", lambda: None)
    monkeypatch.setattr(p, "_update_dps_card_highlights", lambda: None)

    launched = []

    def fake_launch(pid, force=False, log_cb=None, on_result=None,
                    proc=None, model=None):
        # The real signature, mirrored: the Settings switch passes its proc and
        # model so the launch carries its own context (see _try_launch_hook).
        # Deliberately explicit rather than **kwargs - a strict stub is what
        # caught this call site passing an argument the launcher did not take.
        launched.append(on_result)
        threading.Thread(target=lambda: on_result(False, _INJECT_REASON)).start()

    monkeypatch.setattr("farever_companion.ui.game_attach._try_launch_hook",
                        fake_launch)
    p._set_dps_mode_key("injector")

    assert launched, "Option 2 must actually launch the injector"
    # The verdict arrives on a queued signal from the fake injector's own
    # thread, so purges alone cannot guarantee it has landed: wait for the
    # line like _drain_diag_worker waits for the scan (see the helper).
    _settle_until_feedback(p, "Inject failed")
    assert "Inject failed" in p._dps_inject_feedback.text(), \
        "the launched injector's verdict never reached the card"


def test_inject_failure_line_retires_once_the_bridge_reports_alive():
    """The mirror case: a retry attaches the DLL the first attempt missed.

    Option 2 auto-retries (ui/inject_watchdog.py), and the model drops
    ``inject_problem`` as soon as the module or its heartbeat shows up — the
    status line already follows that, so the feedback line must too, or the
    card keeps shouting a failure that is already over.
    """
    p = _inject_panel("inject_failed")
    p._on_dps_inject_result(False, "injector exited 8")
    assert "Inject failed" in p._dps_inject_feedback.text()

    p.model.damage = _BridgeStatusModel._Damage("bridge_ready")
    p._paint_dps_diagnostics()

    assert p._dps_inject_feedback.text() == ""
    assert p._dps_inject_feedback.isHidden(), "an empty line should collapse"


def test_the_painter_keeps_the_failure_line_while_the_model_still_fails():
    """Retraction follows the model, not the fact that a repaint happened."""
    p = _inject_panel("inject_failed")
    p._on_dps_inject_result(False, "injector exited 8")
    for _ in range(3):
        p._paint_dps_diagnostics()
    assert "Inject failed" in p._dps_inject_feedback.text()


def test_a_refusal_after_the_user_left_option_2_is_not_shown():
    """The injector takes seconds, and the verdict must not outlive its mode."""
    p = _inject_panel()
    p._set_dps_feedback("✓ Inject command sent to PID 4242", p._DPS_FEEDBACK_OK)
    p.s.dps_mode = "memory"
    p._on_dps_inject_result(False, _INJECT_REASON)
    assert "Inject failed" not in p._dps_inject_feedback.text(), \
        "a failure line about a mode the user already left is noise"


def test_every_long_line_in_a_flow_card_wraps_and_cannot_set_its_width(qapp):
    """Long text must wrap whether it arrived at runtime or at construction.

    A label ends up holding a long line two ways: a writer sets it (status,
    DLL, PID, source, inject/uninject feedback — driven here through the real
    painters, because they use local aliases and a source scan misses them), or
    it was written that way at construction (the port line, the last one
    exempt). Either way it must wrap: an unwrapped label's minimum is the whole
    line, and the port line alone was 481px of the diagnostics card's 613px
    floor for a fact that was fixed only by convention.

    Then each long line is replaced with a word-breakable sentence to show the
    card's minimum does not follow it — a wrapped label's minimum is its
    longest word, so a sentence of short words leaves the card alone.
    """
    p = Panel()
    p.model = _DiagModel()
    win, tab = _show_dps_tab(p)
    try:
        rows = _flow_rows(tab)
        assert rows and sum(len(c) for _, c in rows) >= 5

        built = []
        for _flow, cards in rows:
            for card in cards:
                for lbl in card.findChildren(QtWidgets.QLabel):
                    built.append((card, lbl, lbl.text()))

        p._set_dps_feedback("\u2715 Uninject failed: SafeUnload export missing in the "
                            "loaded module \u00b7 bridge still loaded (Option 3 active) "
                            "\u2014 see Activity Log")
        for binfo in _DIAG_BINFOS:
            p._dps_diag_binfo = dict(binfo)
            p._paint_dps_diagnostics()
            _settle(1)

        offenders, probes = [], []
        for card, lbl, was in built:
            now = lbl.text()
            if not (now != was or len(now) > UNWRAPPED_LABEL_MAX_CHARS
                    or len(was) > UNWRAPPED_LABEL_MAX_CHARS):
                continue
            probes.append((card, lbl))
            if not lbl.wordWrap():
                offenders.append(f"{_label_owner(p, lbl)} (runtime={now != was}, "
                                 f"{len(now)}ch): {now[:44]!r}")
        assert not offenders, (
            "long or runtime-written labels inside a flow card that do not "
            "wrap: " + "; ".join(offenders) + " — their whole line becomes the "
            "card's minimum width, which is what reflows the row")
        assert len(probes) >= 5, f"only {len(probes)} long lines were found"

        sentence = "the quick brown fox jumps over the lazy dog " * 5
        for card, lbl in probes:
            owner = _label_owner(p, lbl)
            before = card.minimumSizeHint().width()
            saved = lbl.text()
            lbl.setText(sentence.strip())
            _settle()
            after = card.minimumSizeHint().width()
            lbl.setText(saved)
            _settle()
            assert after <= before + 2, (
                f"{owner} set its card's minimum width to {after}px (was "
                f"{before}px): a wrapped label's text must not become the "
                f"card's minimum width")
    finally:
        win.hide()
        win.deleteLater()
        _settle()


class _Model:
    is_dungeon = False
    is_rift = False
    damage = None
    player_addr = 0x1111

    def player_xyz(self):
        return (0.0, 0.0, 0.0)

    def units(self):
        return []

    def player_name(self, a):
        return None

    def _hero_player_ptr(self, a):
        return None

    def _player_name_at(self, p):
        return None


class _Settings:
    opacity = 1.0
    geometry = {}
    dps_view = "damage"
    dps_top_count = 8
    heals_top_count = 2

    def save(self):
        pass


def test_overlay_adopts_settings_live_and_chips_write_back():
    ov = DpsOverlay(_Model(), _Settings())
    try:
        ov._tick()
        assert ov._view_mode == "damage"
        # No distance cap on the shared tracker any more (zone-wide capture).
        assert not hasattr(ov.tracker, "max_dist")

        ov.s.dps_view = "healing"
        ov._tick()
        assert ov._view_mode == "healing"

        # the mode pill still writes the setting on user interaction
        ov.mode_pill._btns["BOTH"].click()
        assert ov.s.dps_view == "both" and ov._view_mode == "both"
    finally:
        ov.close()


def test_overlay_header_is_mockup_pill():
    """The header is the mockup control now: a DMG / HEAL / BOTH segmented
    pill on the top body row (full overlay width, so the chips never overlap
    like they did squeezed into the title bar); the METER / VIEW dropdowns
    and the range cycle button are gone."""
    ov = DpsOverlay(_Model(), _Settings())
    try:
        assert not hasattr(ov, "range_btn"), "cycle button removed in favor of settings"
        assert not hasattr(ov, "_btn_dmg"), "mode tabs replaced by the pill"
        assert not hasattr(ov, "_btn_seg_auto"), "segment buttons replaced by tracker"
        assert not hasattr(ov, "mode_combo") and not hasattr(ov, "seg_combo")
        assert set(ov.mode_pill._btns) == {"DMG", "HEAL", "BOTH"}

        # the pill drives the tracker view + settings
        ov.mode_pill._btns["HEAL"].click()
        assert ov._view_mode == "healing" and ov.s.dps_view == "healing"
    finally:
        ov.close()


# (the dead duplicate key this guards against was a real one: version.dll was
# written twice in PROXY_DLL_INFO, so one description was unreachable)
def test_proxy_picker_describes_only_the_selected_dll():
    """One line under the dropdown, describing the current pick.

    The Option 1 card used to stack a row per shipped DLL; three cards share
    the window width, so prose about names nobody picked was the tallest thing
    on the card. The text still comes from pe_imports.PROXY_DLL_INFO, so the
    picker and the description cannot disagree.
    """
    from farever_companion.core.pe_imports import (PROXY_DLL_INFO,
                                                  PROXY_DLL_NAMES,
                                                  PROXY_RECOMMENDED)
    p = Panel()
    p._refresh_dps_diagnostics = lambda: None   # no background scan in a test
    p._dps_opt = p._tab_dps()                  # builds the three option cards
    combo = p._dps_proxy_combo
    desc = p._dps_proxy_desc
    assert combo.currentData() == PROXY_RECOMMENDED
    when, why = PROXY_DLL_INFO[PROXY_RECOMMENDED]
    assert when in desc.text() and why in desc.text()

    card = p._dps_mode_cards["proxy"]
    for name in [n for n in PROXY_DLL_NAMES if n != PROXY_RECOMMENDED]:
        other_why = PROXY_DLL_INFO[name][1]
        texts = [w.text() for w in card.findChildren(QtWidgets.QLabel)]
        assert not any(other_why in t for t in texts), f"{name} prose still shown"

    # version.dll stopped shipping (2026-09-24): it is not offered, so the
    # picker cannot reach it and its prose cannot leak into the card.
    assert "version.dll" not in PROXY_DLL_NAMES
    offered = [combo.itemData(i) for i in range(combo.count())]
    assert offered and "version.dll" not in offered

    # Switching the pick re-describes it in place (no second label appears).
    combo.setCurrentIndex(PROXY_DLL_NAMES.index("userenv.dll"))
    when_w, why_w = PROXY_DLL_INFO["userenv.dll"]
    assert when_w in desc.text() and why_w in desc.text()
    assert why not in desc.text()
    assert p.s.dps_proxy_dll == "userenv.dll"


def test_no_shipped_proxy_is_a_gamble_so_the_caveat_row_is_idle():
    """The caveat machinery (the red sentence under the picker) has nothing to
    say since version.dll stopped shipping (2026-09-24): every name we ship
    now loads on every launch, so no shipped choice is a gamble. The map is
    kept EMPTY rather than deleted - the next name that needs a caveat gets it
    by adding one entry, and `_paint_proxy_desc` renders it without further
    changes. This test is the tripwire for that day: it passes while no caveat
    exists, and if one is added the shipped registry it guards is still
    consistent (the caveat must name a shipped DLL)."""
    from farever_companion.core.pe_imports import (PROXY_DLL_CAVEATS,
                                                  PROXY_DLL_NAMES)
    p = Panel()
    p._refresh_dps_diagnostics = lambda: None
    p._dps_opt = p._tab_dps()
    desc = p._dps_proxy_desc

    # Every shipped pick renders without a caveat (the machinery is idle).
    for i in range(p._dps_proxy_combo.count()):
        p._dps_proxy_combo.setCurrentIndex(i)
        for caveat in PROXY_DLL_CAVEATS.values():
            assert caveat not in desc.text()

    # Any caveat that exists names a shipped DLL - a caveat about a name the
    # picker cannot reach would be prose nobody can ever see.
    assert set(PROXY_DLL_CAVEATS) <= set(PROXY_DLL_NAMES)
    # ...and the picker offers exactly the shipped registry, in its order.
    offered = [p._dps_proxy_combo.itemData(i)
               for i in range(p._dps_proxy_combo.count())]
    assert offered == list(PROXY_DLL_NAMES)
    # The parked name's reason lives on in the C tree, not in this picker.
    assert "version.dll" not in offered


def test_proxy_description_has_no_dead_duplicate_name():
    """PROXY_DLL_INFO is a plain dict, so a repeated key silently wins: the
    version.dll entry used to be written twice, leaving one description that no
    code path could ever reach. One entry per shipped name, no strays."""
    from farever_companion.core.pe_imports import (PROXY_DLL_CAVEATS,
                                                  PROXY_DLL_INFO,
                                                  PROXY_DLL_NAMES)
    assert set(PROXY_DLL_INFO) == set(PROXY_DLL_NAMES)
    assert set(PROXY_DLL_CAVEATS) <= set(PROXY_DLL_NAMES)
    for when, why in PROXY_DLL_INFO.values():
        assert when and why


def test_install_conflict_warns_and_does_not_move_the_preference(tmp_path, monkeypatch):
    """Another mod occupies the chosen name: the plain remove-it warning, and
    nothing else. No auto-pick: the other mod is probably doing the same kind
    of thing (hooks/overlays/its own bridge), so the user must choose which
    one to keep - the app must not add a second hook DLL under another name.
    """
    p = Panel()
    p._refresh_dps_diagnostics = lambda: None
    logs = []
    p.log = logs.append
    p.s.dps_proxy_dll = "dinput8.dll"
    p._dps_opt = p._tab_dps()

    # The game folder holds the other mod's dinput8.dll; the bridge dir
    # (same tmp_path here) holds our dinput8 source binary.
    monkeypatch.setattr(p, "_find_farever_game_dir", lambda: str(tmp_path))
    (tmp_path / "dinput8.dll").write_bytes(b"MZ" + b"\x00" * 64)  # other mod
    from farever_companion.core import proc as _proc
    monkeypatch.setattr(_proc, "get_bridge_dir", lambda: str(tmp_path))
    monkeypatch.setattr(_proc, "get_reference_checksums", lambda: {})
    monkeypatch.setattr(_proc, "_REF_CHECKSUM_CACHE", {})

    p._install_proxy_dll()

    # The preference did not move, the occupant is untouched, and the
    # feedback says to remove the other mod.
    assert p.s.dps_proxy_dll == "dinput8.dll"
    assert (tmp_path / "dinput8.dll").read_bytes() == b"MZ" + b"\x00" * 64
    feedback = p._dps_proxy_feedback.text()
    assert "Another mod is using dinput8.dll" in feedback
    assert "Remove that mod" in feedback
    assert not any("proxy preference moved" in c for c in logs)


def _install_harness(monkeypatch, tmp_path, game_name="dinput8.dll"):
    """A Panel whose bridge dir is tmp_path/bridge and game folder tmp_path.

    They must be DIFFERENT folders: the update case needs the reference
    binary and the installed copy to be two files with different bytes.
    Writes no DLLs: the caller shapes both per case. Returns
    (panel, source_path, installed_path) - source is the reference binary
    the installer copies FROM, installed is the game-folder copy it would
    overwrite. Replacement confirmation is inline in the feature row.
    """
    p = Panel()
    p._refresh_dps_diagnostics = lambda: None
    p.log = (lambda *a, **k: None)
    p.s.dps_proxy_dll = game_name
    p._dps_opt = p._tab_dps()
    monkeypatch.setattr(p, "_find_farever_game_dir", lambda: str(tmp_path))
    from farever_companion.core import proc as _proc
    monkeypatch.setattr(_proc, "get_bridge_dir", lambda: str(tmp_path / "bridge"))
    (tmp_path / "bridge").mkdir(exist_ok=True)   # the installer reads src from here
    monkeypatch.setattr(_proc, "get_reference_checksums", lambda: {})
    monkeypatch.setattr(_proc, "_REF_CHECKSUM_CACHE", {})
    monkeypatch.setattr(_proc, "find_pid", lambda name="Farever.exe": None)

    return p, tmp_path / "bridge" / game_name, tmp_path / game_name


def test_installing_over_a_live_same_build_proxy_warns_first(monkeypatch, tmp_path):
    """Clicking Install when the SAME build is already in the game folder is a
    warning + explicit confirm, not a silent re-copy.

    This was the hole: the old dialog only fired when the installed hash
    DIFFERED (the update case), so the everyday "DLL is already there" click
    sailed through every check, re-copied, and printed the same green
    'Installed - Restart Farever.exe to activate' as a fresh install - with no
    warning at all.
    """
    p, src, dst = _install_harness(monkeypatch, tmp_path)
    # The reference binary and the installed copy are byte-identical: the
    # same-build case the installer used to warn about never.
    body = b"MZ" + b"\x00" * 200 + b"127.0.0.1:49152" + b"\x00" * 64
    src.write_bytes(body)
    dst.write_bytes(body)

    p._install_proxy_dll()

    assert "Installed: dinput8.dll legacy" in p._dps_proxy_features_lbl.text()
    assert p._dps_inline_update_btn.text() == "Reinstall it"
    assert not p._dps_inline_update_btn.isHidden()
    assert dst.read_bytes() == body
    assert "Restart Farever.exe to activate" not in p._dps_proxy_feedback.text()


def test_a_pending_inline_confirmation_copies_nothing(monkeypatch, tmp_path):
    """Leaving the inline confirmation untouched must leave the game folder byte-identical."""
    p, src, dst = _install_harness(monkeypatch, tmp_path)
    body = b"MZ" + b"\x00" * 200 + b"127.0.0.1:49152" + b"\x00" * 64
    src.write_bytes(body)
    dst.write_bytes(body + b"(installed mtime differs but bytes equal here)")
    dst_mtime = dst.stat().st_mtime

    p._install_proxy_dll()

    assert p._dps_inline_update_btn.text() == "Replace it"
    assert "⚠ Old DLL: dinput8.dll legacy" in p._dps_proxy_features_lbl.text()
    assert "Install new: dinput8.dll (version unavailable)" in p._dps_proxy_features_lbl.text()
    assert dst.read_bytes() == body + b"(installed mtime differs but bytes equal here)"
    assert dst.stat().st_mtime == dst_mtime


def test_installing_over_a_different_build_keeps_the_inline_update_prompt(monkeypatch, tmp_path):
    """The old-build case shows an inline Replace action without a modal dialog.
    """

    p, src, dst = _install_harness(monkeypatch, tmp_path)
    src.write_bytes(
        b"MZ" + b"\x00" * 100
        + b"FAREVERMOD_PROXY_ID=FareverMod;FAREVERMOD_PROXY_VERSION=0.3.6;HLBOOT=old;"
        + b"NEWBUILD")
    dst.write_bytes(
        b"MZ" + b"\x00" * 100
        + b"FAREVERMOD_PROXY_ID=FareverMod;FAREVERMOD_PROXY_VERSION=0.3.5;HLBOOT=old;"
        + b"OLDBUILD")

    p._install_proxy_dll()

    assert "⚠ Old DLL: dinput8.dll v0.3.5" in p._dps_proxy_features_lbl.text()
    assert "  Install new: dinput8.dll v0.3.6" in p._dps_proxy_features_lbl.text()
    assert p._dps_inline_update_btn.text() == "Replace it"
    assert dst.read_bytes().endswith(b"OLDBUILD")
    assert "Installed build:" not in p._dps_proxy_features_lbl.text()
    assert "Current build:" not in p._dps_proxy_features_lbl.text()

    p._dps_inline_update_btn.click()
    assert dst.read_bytes().endswith(b"NEWBUILD")
    assert p._dps_inline_update_btn.isHidden()


def test_option1_mode_switch_does_not_probe_game_on_ui_thread():
    """Selecting Option 1 must use the async diagnostics snapshot instead of
    synchronously enumerating/hash-checking the live game modules."""
    p = Panel()
    p._refresh_dps_diagnostics = lambda: None
    calls = []
    p.log = calls.append

    class _Proc:
        def detect_combat_bridge(self):
            raise AssertionError("Option 1 selection must not probe synchronously")

    p.proc = _Proc()
    p._set_dps_mode_key("proxy")
    assert p.s.dps_mode == "proxy"
    assert any("background" in line for line in calls)


def test_dps_engine_settings_tab_options():
    p = Panel()
    w = p._tab_dps()
    p._dps_diag_binfo = {"proxy_in_game_dir": False, "detected": False,
                         "option": 0, "dll_name": "", "game_dir": ""}
    p._update_dps_card_highlights()
    assert hasattr(p, "_dps_mode_cards")
    assert "proxy" in p._dps_mode_cards
    assert "injector" in p._dps_mode_cards
    assert "memory" in p._dps_mode_cards

    # Option 1: Proxy
    p._set_dps_mode_key("proxy")
    assert p.s.dps_mode == "proxy"
    assert p.s.dps_hook_enabled is False

    # Option 2: Injector
    p._set_dps_mode_key("injector")
    assert p.s.dps_mode == "injector"
    assert p.s.dps_hook_enabled is True

    # Option 3: Memory
    p._set_dps_mode_key("memory")
    assert p.s.dps_mode == "memory"
    assert p.s.dps_hook_enabled is False


@pytest.mark.parametrize("mode", ["injector", "memory"])
def test_options_2_and_3_are_locked_until_option1_proxy_is_removed(mode):
    p = Panel()
    w = p._tab_dps()   # keep the widget tree alive
    p._dps_diag_binfo = {
        "proxy_in_game_dir": True,
        "detected": True,
        "option": 1,
        "dll_name": "userenv.dll",
        "game_dir": r"C:\\Steam\\steamapps\\common\\Farever",
    }
    p._update_dps_card_highlights()

    btn = p._dps_mode_select_btns[mode]
    assert not btn.isEnabled()
    assert btn.text() == "Remove Option 1 DLL first"
    p._set_dps_mode_key(mode)
    assert p.s.dps_mode == "proxy"
    assert p._dps_inject_feedback.text() == ""
    assert p._dps_inject_feedback.isHidden()

    p._dps_diag_binfo = {
        "proxy_in_game_dir": False,
        "detected": False,
        "option": 0,
        "dll_name": "",
        "game_dir": r"C:\\Steam\\steamapps\\common\\Farever",
    }
    p._update_dps_card_highlights()
    assert btn.isEnabled()
    p._set_dps_mode_key(mode)
    assert p.s.dps_mode == mode
    assert p._dps_mode_select_btns[mode].text() == "✓ ACTIVE MODE"


def test_option1_active_requires_proxy_dll_installed():
    """Selecting Option 1 only reads '✓ ACTIVE MODE' once a hash-verified
    FareverPal proxy DLL is actually in the game folder (or live in the
    running game). With none installed the select button says
    '✗ No DLL Installed' instead of claiming to be active — and a 3rd-party
    DLL that merely shares the proxy filename never counts."""
    p = Panel()
    w = p._tab_dps()   # keep the widget tree alive
    p.s.dps_mode = "proxy"
    btn = p._dps_mode_select_btns["proxy"]

    # No FareverPal DLL anywhere -> selected but NOT active.
    p._dps_diag_binfo = {"proxy_in_game_dir": False, "detected": False,
                         "option": 0, "dll_name": "", "game_dir": ""}
    p._update_dps_card_highlights()
    assert btn.text() == "✗ No DLL Installed"

    # A 3rd-party DLL occupying the proxy name must not count as installed.
    p._dps_diag_binfo = {"proxy_in_game_dir": False, "detected": False,
                         "option": 0, "third_party_version": True,
                         "dll_name": "", "game_dir": ""}
    p._update_dps_card_highlights()
    assert btn.text() == "✗ No DLL Installed"

    # FareverPal proxy verified in the game folder -> active.
    p._dps_diag_binfo = {"proxy_in_game_dir": True, "detected": False,
                         "option": 0, "dll_name": "version.dll",
                         "game_dir": ""}
    p._update_dps_card_highlights()
    assert btn.text() == "✓ ACTIVE MODE"

    # A verified proxy already loaded by the running game also counts.
    p._dps_diag_binfo = {"proxy_in_game_dir": False, "detected": True,
                         "option": 1, "dll_name": "version.dll",
                         "game_dir": ""}
    p._update_dps_card_highlights()
    assert btn.text() == "✓ ACTIVE MODE"

    # When another option is selected the proxy card reads normally.
    p.s.dps_mode = "memory"
    p._dps_diag_binfo = {"proxy_in_game_dir": False, "detected": False,
                         "option": 0, "dll_name": "", "game_dir": ""}
    p._update_dps_card_highlights()
    assert btn.text() == "Select Option 1"


def test_option2_uninject_button_shows_only_while_dll_is_loaded():
    """Option 2's ⚡ Uninject button exists to remove a DLL that is IN the
    running game. It used to be always visible - on Option 1 with the game
    closed it offered to uninject nothing. Now it appears only while the
    diagnostics report the injector module detected (option == 2)."""
    p = Panel()
    w = p._tab_dps()   # keep the widget tree alive
    btn = p._dps_uninject_btn
    # The tab is not inside a shown window here, so assert the explicit
    # visibility flag (relative to the parent) rather than isVisible().
    shown = lambda: btn.isVisibleTo(btn.parentWidget())

    # Game closed, no DLL detected -> hidden (any selected mode).
    for mode in ("proxy", "injector", "memory"):
        p.s.dps_mode = mode
        p._dps_diag_binfo = {"detected": False, "option": 0,
                             "dll_name": "", "game_dir": ""}
        p._update_dps_card_highlights()
        assert not shown(), f"visible on mode={mode} with nothing loaded"

    # farever_dps.dll detected in the game -> shown.
    p._dps_diag_binfo = {"detected": True, "option": 2,
                         "dll_name": "farever_dps.dll", "game_dir": ""}
    p._update_dps_card_highlights()
    assert shown()

    # Hidden again as soon as the DLL is gone (game closed / ununjected).
    p._dps_diag_binfo = {"detected": False, "option": 0,
                         "dll_name": "", "game_dir": ""}
    p._update_dps_card_highlights()
    assert not shown()


def test_option1_card_keeps_proxy_identity_in_the_inline_update_blob():
    p = Panel()
    p._refresh_dps_diagnostics = lambda: None
    p._dps_opt = p._tab_dps()
    assert not hasattr(p, "_dps_proxy_build_lbl")

    p._show_inline_proxy_update(False, "userenv.dll", "unknown", "0.3.6")
    text = p._dps_proxy_features_lbl.text()
    assert text == "⚠ Old DLL: userenv.dll legacy\n  Install new: userenv.dll v0.3.6"
    assert p._dps_inline_update_btn.text() == "Replace it"


def test_proxy_scan_reports_installed_identity_without_current_reference(
        tmp_path, monkeypatch):
    """An installed proxy is identifiable even if this app has no matching
    bundled reference to compare it against. This keeps the card honest rather
    than falling back to a blank or guessed build ID.
    """
    from farever_companion.core import proc
    from farever_companion.ui.pages.settings.dps_facets import (
        _scan_dps_combat_bridge_job,
    )

    (tmp_path / "dinput8.dll").write_bytes(
        b"MZFAREVERMOD_PROXY_ID=FareverMod;FAREVERMOD_PROXY_VERSION=3.4.5;"
        b"FAREVERMOD_PROXY_ABI=2;FAREVERMOD_PROXY_HLBOOT=installed-42;payload")
    monkeypatch.setattr(proc, "detect_combat_bridge", lambda **kwargs: {
        "proxy_in_game_dir": True,
        "dll_name": "dinput8.dll",
        "game_dir": str(tmp_path),
    })
    monkeypatch.setattr(proc, "get_bridge_dir", lambda: "")

    info = _scan_dps_combat_bridge_job(0, str(tmp_path))
    assert info["farevermod_installed_dll"] == "dinput8.dll"
    assert info["farevermod_installed_version"] == "3.4.5"
    assert info["farevermod_installed_abi"] == "2"
    assert info["farevermod_installed_build"] == "installed-42"
    assert "farevermod_update_available" not in info


def test_proxy_scan_detects_new_release_on_the_same_game_build(
        tmp_path, monkeypatch):
    """Semantic release and ABI changes trigger Update independently of HLBOOT."""
    from farever_companion.core import proc
    from farever_companion.ui.pages.settings.dps_facets import (
        _scan_dps_combat_bridge_job,
    )

    installed = (
        b"MZFAREVERMOD_PROXY_ID=FareverMod;FAREVERMOD_PROXY_VERSION=1.9.0;"
        b"FAREVERMOD_PROXY_ABI=1;FAREVERMOD_PROXY_HLBOOT=same-game;old")
    current = (
        b"MZFAREVERMOD_PROXY_ID=FareverMod;FAREVERMOD_PROXY_VERSION=1.10.0;"
        b"FAREVERMOD_PROXY_ABI=1;FAREVERMOD_PROXY_HLBOOT=same-game;new")
    (tmp_path / "dinput8.dll").write_bytes(installed)
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "dinput8.dll").write_bytes(current)

    monkeypatch.setattr(proc, "detect_combat_bridge", lambda **kwargs: {
        "proxy_in_game_dir": True,
        "dll_name": "dinput8.dll",
        "game_dir": str(tmp_path),
    })
    monkeypatch.setattr(proc, "get_bridge_dir", lambda: str(bridge))

    info = _scan_dps_combat_bridge_job(0, str(tmp_path))
    assert info["farevermod_installed_version"] == "1.9.0"
    assert info["farevermod_current_version"] == "1.10.0"
    assert info["farevermod_installed_build"] == "same-game"
    assert info["farevermod_current_build"] == "same-game"
    assert info["farevermod_update_available"] is True


def test_option1_button_tracks_real_dll_in_game_folder(tmp_path):
    """A Live Diagnostics refresh drives the Option 1 button from the real
    hash-verified DLL check: '✗ No DLL Installed' on an empty game folder,
    '✓ ACTIVE MODE' once a FareverPal reference DLL is copied in."""
    import shutil
    # version.dll stopped shipping (2026-09-24), so the reference binary this
    # test needs is dinput8.dll now - any shipped proxy proves the same thing
    # (hash-verified DLL in folder -> ACTIVE MODE).
    ref = os.path.join(os.path.dirname(__file__), "..", "dps_bridge", "dinput8.dll")
    if not os.path.isfile(ref):
        pytest.skip("dps_bridge reference dinput8.dll not present")

    p = Panel()
    w = p._tab_dps()   # keep the widget tree alive
    p.s.dps_mode = "proxy"
    game_dir = os.path.normpath(str(tmp_path))
    p._cached_farever_game_dir = game_dir
    p.s.farever_game_dir = game_dir

    p._refresh_dps_diagnostics()
    _drain_diag_worker(p)
    assert p._dps_mode_select_btns["proxy"].text() == "✗ No DLL Installed"

    shutil.copy(ref, os.path.join(game_dir, "dinput8.dll"))
    p._refresh_dps_diagnostics()
    _drain_diag_worker(p)
    assert p._dps_mode_select_btns["proxy"].text() == "✓ ACTIVE MODE"


def test_live_diag_poll_throttles_scan_and_rescans_on_focus(monkeypatch):
    """The 1 Hz Live Diagnostics poll repaints from cached scan state every
    tick and only re-runs the expensive module/folder scan every
    _DPS_DIAG_SCAN_INTERVAL seconds — or immediately when the DPS tab regains
    focus. While the tab is hidden the poll does nothing at all.

    Scans run on a worker, so the poll under test requests them via
    _request_dps_diag_scan (patched here to apply instantly); the throttle
    gate itself is unchanged."""
    p = Panel()
    w = p._tab_dps()   # keep the widget tree alive
    tab = QtWidgets.QWidget()
    p._settings_tab_pages = {"DPS": tab}
    tab.show()

    calls = {"scans": 0, "paints": 0}

    def fake_request(origin="direct"):
        calls["scans"] += 1
        p._dps_diag_binfo = {"detected": False, "dll_name": "", "game_dir": ""}
        # Mirror _on_dps_diag_scan_done: a landed scan stamps the window.
        import time
        p._dps_diag_last_scan = time.monotonic()

    def fake_paint():
        calls["paints"] += 1

    monkeypatch.setattr(p, "_request_dps_diag_scan", fake_request)
    monkeypatch.setattr(p, "_paint_dps_diagnostics", fake_paint)
    p._dps_diag_last_scan = 0.0
    p._dps_diag_tab_visible = False   # first visible tick = focus regain

    p._on_dps_live_diag_tick()        # tab regained focus -> scan + paint
    assert calls == {"scans": 1, "paints": 1}

    p._on_dps_live_diag_tick()        # within throttle window -> paint only
    assert calls == {"scans": 1, "paints": 2}

    # Leaving the tab silences the poll entirely (no scan, no repaint).
    tab.hide()
    p._on_dps_live_diag_tick()
    assert calls == {"scans": 1, "paints": 2}

    # Coming back forces an immediate fresh scan, not a stale repaint.
    tab.show()
    p._on_dps_live_diag_tick()
    assert calls == {"scans": 2, "paints": 3}


def test_refresh_returns_while_scan_runs_and_paints_on_done(monkeypatch):
    """_refresh_dps_diagnostics never blocks the UI thread: with a wedged
    scan the call returns while the worker is still running, and the labels
    repaint once it finishes."""
    import threading

    p = Panel()
    # Labels only (no _tab_dps: that schedules its own deferred refresh,
    # which would race this test's worker).
    p._dps_diag_card = p._build_dps_diagnostics_card()
    monkeypatch.setattr(p, "_find_farever_game_dir", lambda: "")
    started = threading.Event()
    release = threading.Event()

    def slow_job(pid, g_dir):
        started.set()
        assert threading.current_thread() is not threading.main_thread()
        assert release.wait(timeout=10.0)
        return {"detected": False, "dll_name": "", "game_dir": ""}

    monkeypatch.setattr(
        "farever_companion.ui.pages.settings.dps_facets._scan_dps_combat_bridge_job",
        slow_job)
    assert getattr(p, "_dps_diag_binfo", None) is None
    p._refresh_dps_diagnostics()
    assert started.wait(timeout=10.0)
    assert p._dps_diag_worker.isRunning()   # returned while scan in flight
    assert getattr(p, "_dps_diag_binfo", None) is None  # nothing painted yet
    release.set()
    _drain_diag_worker(p)
    assert p._dps_diag_binfo == {"detected": False, "dll_name": "", "game_dir": ""}
    assert p._dps_diag_status_lbl.text() == "Status: Standing by"


def test_superseded_scan_result_never_paints(monkeypatch):
    """Overlapping scans resolve in request order: a slow first scan that
    lands after a fast second one is dropped, never overwriting fresher data."""
    import threading

    p = Panel()
    p._dps_diag_card = p._build_dps_diagnostics_card()
    monkeypatch.setattr(p, "_find_farever_game_dir", lambda: "")
    entered = threading.Event()
    gate = threading.Event()
    calls = {"n": 0}

    def gated_job(pid, g_dir):
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            assert gate.wait(timeout=10.0)
            return {"detected": True, "dll_name": "STALE", "game_dir": ""}
        return {"detected": False, "dll_name": "", "game_dir": ""}

    monkeypatch.setattr(
        "farever_companion.ui.pages.settings.dps_facets._scan_dps_combat_bridge_job",
        gated_job)
    p._refresh_dps_diagnostics()            # token 1: wedged in the worker
    assert entered.wait(timeout=10.0)
    p._refresh_dps_diagnostics()            # token 2: fast, supersedes
    _drain_diag_worker(p)
    fresh = {"detected": False, "dll_name": "", "game_dir": ""}
    assert p._dps_diag_binfo == fresh
    gate.set()                              # token 1 finally lands: dropped
    _drain_diag_worker(p)
    assert p._dps_diag_binfo == fresh


def test_diagnostics_scan_never_builds_a_qthread(monkeypatch):
    """The Live Diagnostics scan must not construct a QThread.

    The DPS tab re-runs this scan every few seconds while it is on screen,
    and that recurring Qt-thread construction is exactly where the
    main-thread access violation in run.log landed (workers.py
    CallWorker.__init__, reached from the 1 Hz diagnostics tick). With
    CallWorker poisoned, a scan must still start, run off the GUI thread and
    paint — i.e. the call site no longer reaches for a Qt thread at all.
    """
    import threading
    from PySide6 import QtCore

    p = Panel()
    p._dps_diag_card = p._build_dps_diagnostics_card()
    monkeypatch.setattr(p, "_find_farever_game_dir", lambda: "")

    def _poison(*_a, **_k):
        raise AssertionError("diagnostics scan built a QThread")

    monkeypatch.setattr("farever_companion.ui.workers.CallWorker", _poison)
    seen = []

    def job(pid, g_dir):
        seen.append(threading.current_thread())
        return {"detected": False, "dll_name": "", "game_dir": ""}

    monkeypatch.setattr(
        "farever_companion.ui.pages.settings.dps_facets._scan_dps_combat_bridge_job",
        job)
    p._refresh_dps_diagnostics()
    assert not isinstance(p._dps_diag_worker, QtCore.QThread)
    _drain_diag_worker(p)
    assert seen and seen[0] is not threading.main_thread()
    assert p._dps_diag_binfo == {"detected": False, "dll_name": "",
                                 "game_dir": ""}


def test_dps_settings_page_carries_no_hover_tooltips():
    """Settings > DPS shows no mouse-over tooltips: every action button is
    labelled and the long explanations belong on the card, not in a hover
    bubble. Walks the whole tab so a new ``setToolTip`` anywhere in the page
    fails this instead of quietly shipping."""
    p = Panel()
    tab = p._tab_dps()
    widgets = [tab] + tab.findChildren(QtWidgets.QWidget)
    tipped = [
        (type(w).__name__, w.objectName() or "-", w.toolTip())
        for w in widgets if w.toolTip()
    ]
    assert tipped == [], tipped


def test_memory_mode_reports_leftover_dll_as_ignored():
    """Option 3 never loads a DLL: a still-mapped farever_dps.dll (leftover
    from an injector session) must read as ignored — never as the active
    capture, and never as a 'relaunch to unload' warning tied to memory."""
    p, _ = _card()
    p.s.dps_mode = "memory"
    p._dps_diag_binfo = {"detected": True, "option": 2,
                         "dll_name": "farever_dps.dll",
                         "game_dir": "C:\\Farever"}
    p._paint_dps_diagnostics()
    dll_text = p._dps_diag_dll_lbl.text()
    assert "ignored" in dll_text and "Memory Reader" in dll_text
    assert "still loaded" not in dll_text
    assert "ACTIVE IN GAME" not in p._dps_diag_status_lbl.text()


def test_bridge_mode_mismatch_still_warns():
    """The leftover-DLL warning stays for bridge modes: injected on Option 2
    then switched to Option 1 must still say the DLL is loaded."""
    p, _ = _card()
    p.s.dps_mode = "proxy"
    p._dps_diag_binfo = {"detected": True, "option": 2,
                         "dll_name": "farever_dps.dll",
                         "game_dir": "C:\\Farever"}
    p._paint_dps_diagnostics()
    assert "still loaded" in p._dps_diag_dll_lbl.text()

def test_live_diagnostics_quote_the_bridge_log_tail(tmp_path):
    """A loaded-but-silent bridge used to say only "no event yet this run"
    while the DLL's own log already held the reason ("table not present yet").
    The tail is NOT painted on the card any more - a raw C log blob made the
    card unreadable - but Copy Diagnostics still carries it, so a pasted
    report keeps the explanation."""
    p, _ = _card()
    (tmp_path / "dinput8_proxy.log").write_text(
        "[boot] dinput8.dll proxy bridge init start\n"
        "[hunt] table not present yet; retrying (29 s left) (ok=1)\n"
        "[HEARTBEAT] Bridge alive (tick: 1, hooks: 4)\n",
        encoding="utf-8")
    p.s.dps_mode = "proxy"
    p._dps_diag_binfo = {"game_dir": str(tmp_path), "dll_name": "dinput8.dll"}

    # The card has no log label at all any more - the blob is out of the UI.
    assert not hasattr(p, "_dps_diag_log_lbl")

    snapshot = "\n".join(p._bridge_log_lines())
    assert "[hunt] table not present yet" in snapshot
    assert "+1 heartbeat line(s) omitted" in snapshot


def test_live_diagnostics_label_a_kept_tail_as_such(tmp_path):
    """Game-folder cleanup deletes the logs, so the tail it kept in memory is
    usually the only evidence left of a crash. Copy Diagnostics must carry it
    - and label it, or a pasted report reads as if the file were still on disk."""
    from farever_companion.core import bridge_log

    bridge_log.clear_snapshots()
    try:
        p, _ = _card()
        log = tmp_path / "dinput8_proxy.log"
        log.write_text("[boot] loader idle after 512 ms (probe=idle, floor=0 ms)\n"
                       "[verify] REFUSING to arm 4 hooks (verdict=1)\n",
                       encoding="utf-8")
        bridge_log.snapshot_if_bridge_log(str(log))
        log.unlink()
        p.s.dps_mode = "proxy"
        p._dps_diag_binfo = {"game_dir": str(tmp_path), "dll_name": "dinput8.dll"}

        copied = "\n".join(p._bridge_log_lines())
        assert "kept after cleanup" in copied
        assert "REFUSING to arm 4 hooks" in copied
    finally:
        bridge_log.clear_snapshots()


def test_live_diagnostics_hide_the_log_row_when_there_is_nothing_to_say(tmp_path):
    """Memory mode writes no DLL log, so the copied report must not quote some
    stray file from an earlier session."""
    p, _ = _card()
    (tmp_path / "dinput8_proxy.log").write_text("[boot] x\n", encoding="utf-8")
    p.s.dps_mode = "memory"
    p._dps_diag_binfo = {"game_dir": str(tmp_path), "dll_name": ""}

    assert p._bridge_log_lines() == []


def test_the_dummy_card_carries_the_free_test_toggle():
    """Free Test must be findable ON THE CARD, and it must persist.

    Asked for live 2026-10-03: "where is the button for free mode as in non
    stop" - the control existed but nothing proved it was actually rendered on
    the Test Dummy card, which is the only place a player would look for it.

    Asserted against the built card (not the bare widget) and via the card's own
    children, so a toggle that is constructed and then dropped - or left off
    the layout - fails here rather than being found by hand months later.
    """
    p = Panel()
    p._dummy_card = p._build_dummy_card()

    labels = [w.text() for w in p._dummy_card.findChildren(QtWidgets.QLabel)]
    assert "Free Test" in labels, labels

    free = p._dummy_free
    assert free is not None and not free.isChecked()   # off by default
    free.setChecked(True)
    assert p.s.dps_dummy_free is True

    p.s.dps_dummy_free = False
    p._dummy_free.set_checked_silent(True)
    assert p.s.dps_dummy_free is False, "the card must not rewrite a silent set"
