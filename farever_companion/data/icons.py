"""Real game icons -> QPixmap, cached.

Resolves item/unit/skill ids to the extracted PNGs under
htdocs/assets/icons/<sheet>/<id>.png (the same set the web wiki uses). Scaled
smoothly and cached by (sheet, id, size). Missing icons fall back to a small
flat placeholder so the UI never breaks on a gap.

LAYERING NOTE: this is a deliberately UI-adjacent data module, it renders
QPixmaps, so it is the one `data/` module that touches Qt. To keep the headless
layering contract intact (data/ must import without a GUI toolkit installed), Qt
is imported LAZILY inside `_qt()`, never at module scope. The layering guard in
tests/test_readonly_default.py allows exactly this (it forbids module-level Qt
imports, not in-function ones). Keep all Qt use inside functions here.
"""
from __future__ import annotations

from functools import lru_cache

from .. import paths

# Qt is imported lazily so the data layer stays importable headless.
_QtGui = None
_QtCore = None


def _qt():
    global _QtGui, _QtCore
    if _QtGui is None:
        from PySide6 import QtGui, QtCore
        _QtGui, _QtCore = QtGui, QtCore
    return _QtGui, _QtCore


def _icon_path(sheet: str, id_: str):
    p = paths.icons_dir() / sheet / f"{id_}.png"
    return p if p.exists() else None


@lru_cache(maxsize=4096)
def pixmap(sheet: str, id_: str, size: int):
    """A QPixmap for an icon id, scaled to `size`. Never None (placeholder)."""
    QtGui, QtCore = _qt()
    path = _icon_path(sheet, id_) if (sheet and id_) else None
    if path is not None:
        pm = QtGui.QPixmap(str(path))
        if not pm.isNull():
            return pm.scaled(size, size, QtCore.Qt.KeepAspectRatio,
                             QtCore.Qt.SmoothTransformation)
    return _placeholder(size)


@lru_cache(maxsize=64)
def _placeholder(size: int):
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pm)
    painter.setPen(QtGui.QColor("#2b2f3a"))
    painter.drawRect(1, 1, size - 3, size - 3)
    painter.end()
    return pm


def has_icon(sheet: str, id_: str) -> bool:
    return _icon_path(sheet, id_) is not None


@lru_cache(maxsize=4096)
def tile(sheet: str | None, id_: str | None, size: int, accent: str):
    """Game icon on a rounded, accent-tinted square, the standard row leading
    element (Loot table + both HUDs). `accent` is a hex color (rarity / faction
    / gold / cyan). Background = accent @18%, 1px border = accent @55%, the PNG
    centred with ~2px padding. Missing PNG -> the tinted square still shows."""
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    bg = QtGui.QColor(accent); bg.setAlpha(46)        # ~18%
    bd = QtGui.QColor(accent); bd.setAlpha(140)       # ~55%
    p.setBrush(bg)
    p.setPen(QtGui.QPen(bd, 1))
    p.drawRect(QtCore.QRectF(0.5, 0.5, size - 1, size - 1))   # sharp (0px) per design
    if sheet and id_ and has_icon(sheet, id_):
        g = pixmap(sheet, id_, size - 4)
        p.drawPixmap((size - g.width()) // 2, (size - g.height()) // 2, g)
    p.end()
    return pm


@lru_cache(maxsize=1024)
def tile_marker(name: str, size: int, accent: str):
    """Map-marker PNG (assets/map_icons/<name>.png) on the standard accent-
    tinted square tile - the entity-HUD row icon for chests / orbs / world
    activities, matching the minimap's marker art."""
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    bg = QtGui.QColor(accent); bg.setAlpha(46)
    bd = QtGui.QColor(accent); bd.setAlpha(140)
    p.setBrush(bg)
    p.setPen(QtGui.QPen(bd, 1))
    p.drawRect(QtCore.QRectF(0.5, 0.5, size - 1, size - 1))
    g = asset_icon(name, size - 4)
    if g is not None:
        p.drawPixmap((size - g.width()) // 2, (size - g.height()) // 2, g)
    p.end()
    return pm


def _silhouette(g, color):
    """A solid-`color` silhouette of pixmap `g`, preserving its alpha, i.e. the
    icon's organic shape filled flat. (SourceIn keeps `g`'s alpha, replaces RGB.)"""
    QtGui, QtCore = _qt()
    sil = QtGui.QPixmap(g.size())
    sil.fill(QtGui.QColor(0, 0, 0, 0))
    sp = QtGui.QPainter(sil)
    sp.drawPixmap(0, 0, g)
    sp.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
    sp.fillRect(sil.rect(), QtGui.QColor(color))
    sp.end()
    return sil


def _disk_offsets(r: int):
    return [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
            if (dx or dy) and dx * dx + dy * dy <= r * r]


@lru_cache(maxsize=4096)
def outlined(sheet: str | None, id_: str | None, size: int, accent: str,
             border: int = 2):
    """Game icon drawn as itself with an accent border hugging the PNG's
    *organic* alpha shape, no square tile. For map markers that should read as
    the item/enemy art. Built by dilating the icon's silhouette (a thin dark
    keyline for legibility over the map, then the accent border) and drawing the
    full-colour icon on top. Missing PNG -> a plain accent dot."""
    QtGui, QtCore = _qt()
    out = QtGui.QPixmap(size, size)
    out.fill(QtGui.QColor(0, 0, 0, 0))
    if not (sheet and id_ and has_icon(sheet, id_)):
        p = QtGui.QPainter(out)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(accent))
        r = size / 2 - border
        p.drawEllipse(QtCore.QPointF(size / 2, size / 2), r, r)
        p.end()
        return out

    g = pixmap(sheet, id_, size - 2 * border)        # room for the border ring
    keyline = _silhouette(g, "#0b0e14")              # dark, for contrast on art
    ring = _silhouette(g, accent)
    ox = (size - g.width()) // 2
    oy = (size - g.height()) // 2

    p = QtGui.QPainter(out)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    for dx, dy in _disk_offsets(border + 1):         # outer dark keyline
        p.drawPixmap(ox + dx, oy + dy, keyline)
    for dx, dy in _disk_offsets(border):             # accent border
        p.drawPixmap(ox + dx, oy + dy, ring)
    p.drawPixmap(ox, oy, g)                           # full-colour icon on top
    p.end()
    return out


@lru_cache(maxsize=64)
def asset_icon(sheet_name: str, size: int):
    """A bundled SVG or PNG map icon from assets/map_icons/<name>.(svg|png), scaled. None if missing."""
    QtGui, QtCore = _qt()
    
    # Check for SVG first
    svg_path = paths.assets_dir() / "map_icons" / f"{sheet_name}.svg"
    if svg_path.exists():
        try:
            from PySide6.QtSvg import QSvgRenderer
            pm = QtGui.QPixmap(size, size)
            pm.fill(QtGui.QColor(0, 0, 0, 0))
            r = QSvgRenderer(str(svg_path))
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            r.render(p, QtCore.QRectF(0, 0, size, size))
            p.end()
            return pm
        except Exception:
            pass

    # Fallback to PNG
    png_path = paths.assets_dir() / "map_icons" / f"{sheet_name}.png"
    if png_path.exists():
        pm = QtGui.QPixmap(str(png_path))
        if not pm.isNull():
            return pm.scaled(size, size, QtCore.Qt.KeepAspectRatio,
                             QtCore.Qt.SmoothTransformation)
    return None


@lru_cache(maxsize=512)
def marker(name: str, size: int, border: int = 2):
    """A colored map-marker PNG (assets/map_icons/<name>.png) drawn with a thin
    dark organic keyline so it stays legible over the map texture. The icon is
    already colored, so it is NOT tinted. None if the asset is missing."""
    QtGui, QtCore = _qt()
    g = asset_icon(name, max(1, size - 2 * border))
    if g is None:
        return None
    keyline = _silhouette(g, "#0b0e14")
    out = QtGui.QPixmap(size, size)
    out.fill(QtGui.QColor(0, 0, 0, 0))
    ox = (size - g.width()) // 2
    oy = (size - g.height()) // 2
    p = QtGui.QPainter(out)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    for dx, dy in _disk_offsets(border):
        p.drawPixmap(ox + dx, oy + dy, keyline)
    p.drawPixmap(ox, oy, g)
    p.end()
    return out


@lru_cache(maxsize=1024)
def ui_icon(name: str, color: str, size: int):
    """A theme-tinted UI-chrome glyph from assets/icons_ui/<name>.svg.

    The SVGs use `currentColor`; we substitute `color` and render via QtSvg to a
    transparent QPixmap. Missing/invalid -> an empty (transparent) pixmap so the
    UI degrades quietly. Cached by (name, color, size)."""
    QtGui, QtCore = _qt()
    from PySide6.QtSvg import QSvgRenderer
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    path = paths.assets_dir() / "icons_ui" / f"{name}.svg"
    if path.exists():
        try:
            txt = path.read_text(encoding="utf-8").replace("currentColor", color)
            r = QSvgRenderer(QtCore.QByteArray(txt.encode("utf-8")))
            p = QtGui.QPainter(pm)
            # inset by ~8% so round caps / edge strokes (crosshair, gear, dice)
            # don't get clipped at the pixmap border
            m = max(1.0, size * 0.08)
            r.render(p, QtCore.QRectF(m, m, size - 2 * m, size - 2 * m))
            p.end()
        except Exception:
            pass
    return pm


def ui_qicon(name: str, color: str, size: int = 18):
    """`ui_icon` wrapped as a QIcon (for QPushButton.setIcon)."""
    QtGui, _ = _qt()
    return QtGui.QIcon(ui_icon(name, color, size))


@lru_cache(maxsize=64)
def brand_icon(name: str, size: int):
    """A multi-color brand glyph from assets/icons_ui/<name>.svg rendered AS-IS
    (no tinting), for logos like the Google "G" that aren't single-color.
    Missing/invalid -> transparent pixmap. Cached by (name, size)."""
    QtGui, QtCore = _qt()
    from PySide6.QtSvg import QSvgRenderer
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    path = paths.assets_dir() / "icons_ui" / f"{name}.svg"
    if path.exists():
        try:
            r = QSvgRenderer(str(path))
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            r.render(p, QtCore.QRectF(0, 0, size, size))
            p.end()
        except Exception:
            pass
    return pm


def brand_qicon(name: str, size: int = 18):
    """`brand_icon` wrapped as a QIcon (for QPushButton.setIcon)."""
    QtGui, _ = _qt()
    return QtGui.QIcon(brand_icon(name, size))


@lru_cache(maxsize=1024)
def tile_ui(name: str, size: int, accent: str):
    """A theme-tinted UI SVG icon (assets/icons_ui) on the standard accent-tinted
    square tile."""
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    bg = QtGui.QColor(accent); bg.setAlpha(46)        # ~18%
    bd = QtGui.QColor(accent); bd.setAlpha(140)       # ~55%
    p.setBrush(bg)
    p.setPen(QtGui.QPen(bd, 1))
    p.drawRect(QtCore.QRectF(0.5, 0.5, size - 1, size - 1))
    g = ui_icon(name, accent, size - 4)
    if g is not None:
        p.drawPixmap((size - g.width()) // 2, (size - g.height()) // 2, g)
    p.end()
    return pm

