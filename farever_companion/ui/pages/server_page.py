"""Server diagnostics page (ping tester + live connection scanner).

Split into the `server` package (page chrome, panel mixins, workers and the
network helpers in `server.net`); this module is kept as a compatibility
shim so `from .pages.server_page import ServerPageMixin` keeps working.
"""
from __future__ import annotations

from .server.page import ServerPageMixin

__all__ = ["ServerPageMixin"]
