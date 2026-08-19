"""Right-click copy context menu (dev-mode helper)."""
from __future__ import annotations

import html
import json
import re

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.updater import is_frozen

_TAG = re.compile(r"<[^>]+>")
_EDITABLE = (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit,
             QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)
_VIEWS = (QtWidgets.QGraphicsView,)

_filter: "_CopyTextFilter | None" = None


def install_copy_menu(app) -> None:
    """Install app-wide right-click copy filter (dev-only, idempotent)."""
    global _filter
    if _filter is not None or is_frozen():
        return
    _filter = _CopyTextFilter()
    app.installEventFilter(_filter)


def _plain(text: str) -> str:
    """Strip HTML tags and unescape entities."""
    return html.unescape(_TAG.sub("", text)) if "<" in text else text


def _widget_text(w: QtWidgets.QWidget) -> str | None:
    if isinstance(w, QtWidgets.QLabel):
        t = _plain(w.text()).strip()
        return t or None
    if isinstance(w, QtWidgets.QAbstractButton):
        t = w.text().strip()
        return t or None
    return None


def _skip(w: QtWidgets.QWidget | None) -> bool:
    while w is not None:
        if getattr(w, "_no_copy_menu", False):
            return True
        if w.contextMenuPolicy() in (QtCore.Qt.CustomContextMenu, QtCore.Qt.ActionsContextMenu):
            return True
        if isinstance(w, _EDITABLE + _VIEWS):
            return True
        w = w.parentWidget()
    return False


def _item_data_at(view: QtWidgets.QAbstractItemView, gpos: QtCore.QPoint) -> tuple[str | None, object, str | None]:
    idx = view.indexAt(view.viewport().mapFromGlobal(gpos))
    if not idx.isValid():
        return None, None, None
    t = idx.data(QtCore.Qt.DisplayRole)
    t = str(t).strip() if t is not None else ""
    rid = idx.data(QtCore.Qt.UserRole)
    rtip = idx.data(QtCore.Qt.ToolTipRole)
    rtip = str(rtip).strip() if rtip is not None else ""
    return (t or None), rid, (rtip or None)


def _item_text_at(view: QtWidgets.QAbstractItemView, gpos: QtCore.QPoint) -> str | None:
    return _item_data_at(view, gpos)[0]


def _fmt_id(rid) -> str | None:
    if rid is None:
        return None
    if isinstance(rid, (dict, list, tuple)):
        try:
            s = json.dumps(rid, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            s = repr(rid)
    else:
        s = str(rid)
    if not s.strip():
        return None
    return s if len(s) <= 200 else s[:200] + "…"


def _dev_block(w: QtWidgets.QWidget, gpos: QtCore.QPoint, view: QtWidgets.QAbstractItemView | None,
               text: str | None, tip: str) -> str:
    lines = [f"ELEMENT: {type(w).__name__}"]
    on = w.objectName()
    if on:
        lines.append(f"OBJECT NAME: {on}")
    it = rid = rtip = None
    if view is not None:
        it, rid, rtip = _item_data_at(view, gpos)
    if text:
        lines.append(f"TEXT: {text}")
    elif it:
        lines.append(f"TEXT: {it}")
    fid = _fmt_id(rid)
    if fid:
        lines.append(f"ITEM ID: {fid}")
    if tip:
        lines.append(f"TOOLTIP: {_plain(tip)}")
    if rtip and rtip != tip:
        lines.append(f"ITEM TOOLTIP: {_plain(rtip)}")
    chain = []
    p = w.parentWidget()
    while p is not None and len(chain) < 4:
        on2 = p.objectName()
        chain.append(f"{type(p).__name__}({on2})" if on2 else type(p).__name__)
        p = p.parentWidget()
    if chain:
        lines.append("PARENTS: " + " > ".join(chain))
    return "\n".join(lines)


class _CopyTextFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.ContextMenu:
            return self._offer(QtGui.QCursor.pos())
        return False

    def _offer(self, gpos: QtCore.QPoint) -> bool:
        if is_frozen():
            return False
        w = QtWidgets.QApplication.widgetAt(gpos)
        if w is None or _skip(w):
            return False

        view = None
        anc = w
        while anc is not None:
            if isinstance(anc, QtWidgets.QAbstractItemView):
                view = anc
                break
            anc = anc.parentWidget()

        texts: list[tuple[str, str]] = []
        t = _widget_text(w)
        if t:
            texts.append(("Copy text", t))
        item_text = None
        if view is not None and not texts:
            item_text = _item_text_at(view, gpos)
            if item_text:
                texts.append(("Copy text", item_text))
        tip = w.toolTip().strip()
        if not tip and view is not None:
            tip = view.toolTip().strip()
        if tip:
            texts.append(("Copy tooltip", _plain(tip)))

        if not texts and not (item_text or tip or w.objectName()):
            return False

        dev = _dev_block(w, gpos, view, t or item_text, tip)
        menu = QtWidgets.QMenu()
        for label, text_val in texts:
            act = menu.addAction(label)
            act.triggered.connect(lambda _, tt=text_val: QtWidgets.QApplication.clipboard().setText(tt))
        if not is_frozen():
            menu.addSeparator()
            act = menu.addAction("Copy dev info (element + text)")
            act.triggered.connect(lambda _: QtWidgets.QApplication.clipboard().setText(dev))
        menu.exec(QtGui.QCursor.pos())
        return True
