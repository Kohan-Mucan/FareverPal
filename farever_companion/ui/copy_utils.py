"""Small, reusable clipboard actions for UI features.

Content formatting belongs to the feature that owns it; this module only
handles putting already-formatted text on the clipboard and giving a button a
short success state.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets


def copy_text(text: str) -> bool:
    """Copy *text* to the application clipboard and return whether it succeeded."""
    try:
        clipboard = QtGui.QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(str(text))
        return True
    except Exception:
        return False


def copy_with_feedback(
    text: str,
    button: QtWidgets.QAbstractButton | None = None,
    *,
    copied_text: str = "✓ Copied",
    restore_text: str | None = None,
    restore_ms: int = 1400,
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    """Copy text and briefly update an optional button.

    The button is intentionally optional so non-button actions can use the
    same clipboard path. A destroyed Qt button is ignored when the delayed
    restore runs, which is important for rebuilt page sections.
    """
    try:
        if not copy_text(text):
            return False
        if button is None:
            return True

        previous = button.text() if restore_text is None else restore_text
        button.setText(copied_text)

        def restore() -> None:
            try:
                if button is not None:
                    button.setText(previous)
            except RuntimeError:
                # The page may have rebuilt and deleted the old button.
                pass

        QtCore.QTimer.singleShot(max(0, int(restore_ms)), restore)
        return True
    except Exception as exc:
        if on_error is not None:
            on_error(exc)
        return False


__all__ = ["copy_text", "copy_with_feedback"]
