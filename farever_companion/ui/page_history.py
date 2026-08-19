"""Back/forward page history stack."""
from __future__ import annotations

from collections.abc import Callable


class PageHistory:
    """Two-stack back/forward navigation history."""

    def __init__(self, select: Callable[[str], None]) -> None:
        self._select = select
        self._back: list[str] = []
        self._forward: list[str] = []
        self._restoring = False

    def record(self, current: str | None, target: str) -> None:
        """Remember fresh navigation away from current."""
        if self._restoring:
            return
        if current is not None and current != target:
            self._back.append(current)
        self._forward.clear()

    def back(self, current: str | None) -> None:
        """Restore previous page."""
        if self._back:
            self._go(current, self._back.pop(), self._forward)

    def forward(self, current: str | None) -> None:
        """Restore forward page."""
        if self._forward:
            self._go(current, self._forward.pop(), self._back)

    def _go(self, current: str | None, target: str, other: list[str]) -> None:
        if current is not None:
            other.append(current)
        self._restoring = True
        try:
            self._select(target)
        finally:
            self._restoring = False
