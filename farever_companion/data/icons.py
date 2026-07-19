"""Real game icons -> QPixmap, cached.

Resolves item/unit/skill ids to the extracted PNGs under
assets/icons/<sheet>/<id>.png (the same set the game uses). Scaled
smoothly and cached by (sheet, id, size). Missing icons fall back to a small
flat placeholder so the UI never breaks on a gap.

LAYERING NOTE: this is a deliberately UI-adjacent data module, it renders
QPixmaps, so it is the one `data/` module that touches Qt. To keep the headless
layering contract intact (data/ must import without a GUI toolkit installed), Qt
is imported LAZILY inside `_qt()`, never at module scope. Keep all Qt use
inside functions here.
"""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from .. import paths
from . import atlas

# Qt is imported lazily so the data layer stays importable headless.
_QtGui = None
_QtCore = None


def _qt():
    global _QtGui, _QtCore
    if _QtGui is None:
        from PySide6 import QtGui, QtCore
        _QtGui, _QtCore = QtGui, QtCore
    return _QtGui, _QtCore


@lru_cache(maxsize=20)
def _get_atlas_sheet(path: str):
    """Cache the large atlas QPixmaps so we don't reload from disk for every icon."""
    QtGui, _ = _qt()
    pm = QtGui.QPixmap(path)
    return pm if not pm.isNull() else None


def _crop_atlas(atlas_path, x: int, y: int, src_size: int, dst_size: int):
    """Load an atlas sheet and crop a single region, scaled to dst_size."""
    QtGui, QtCore = _qt()
    sheet_pm = _get_atlas_sheet(str(atlas_path))
    if sheet_pm is None:
        return None
    rect = QtCore.QRect(x, y, src_size, src_size)
    return sheet_pm.copy(rect).scaled(
        dst_size, dst_size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
    )


_SHEET_MAP = {
    "units": "units",
    "unit": "units",
    "enemies": "units",
    "enemy": "units",
    "items": "items",
    "item": "items",
    "skills": "skills",
    "skill": "skills",
}


def _icon_path(sheet: str, id_: str):
    # Normalize to the canonical plural name (e.g., "unit" -> "units")
    s_low = sheet.lower()
    canonical = _SHEET_MAP.get(s_low, s_low)
    
    # Build list of folders to try: plural lowercase, TitleCase, and original
    sheets_to_try = [canonical, canonical.capitalize(), sheet]
    seen = set()
    unique_sheets = [x for x in sheets_to_try if x and not (x in seen or seen.add(x))]
    
    base_dirs = [paths.icons_dir()]
            
    for base in base_dirs:
        for s in unique_sheets:
            for ext in ("webp", "png"):
                p = base / s / f"{id_}.{ext}"
                if p.exists():
                    return p
    return None


@lru_cache(maxsize=4096)
def pixmap(sheet: str, id_: str, size: int):
    """A QPixmap for an icon id, scaled to `size`. Never None (placeholder).

    Atlas-first: if the id has gfx/atlas data in raw_data, crop from the
    atlas sheet. Otherwise fall back to the individual file on disk.
    """
    QtGui, QtCore = _qt()
    if sheet and id_:
        # 1. Try atlas sprite-sheet
        entry = atlas.find_entry(sheet, id_)
        if entry and isinstance(entry, dict):
            a_path = atlas.resolve_path(entry.get("file", ""))
            if a_path:
                pm = _crop_atlas(
                    a_path,
                    entry.get("x", 0),
                    entry.get("y", 0),
                    entry.get("size", 96),
                    size,
                )
                if pm and not pm.isNull():
                    return pm
        # 2. Fall back to individual file
        path = _icon_path(sheet, id_)
        if path is not None:
            pm = QtGui.QPixmap(str(path))
            if not pm.isNull():
                return pm.scaled(
                    size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                )
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
    if atlas.find_entry(sheet, id_) is not None:
        return True
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


@lru_cache(maxsize=4096)
def tile_marker(name: str, size: int, accent: str, outlined: bool = False):
    """Map-marker PNG (assets/map_icons/<name>.png) on the standard accent-
    tinted square tile - the entity-HUD row icon for chests / orbs / world
    activities, matching the minimap's marker art.
    If `outlined` is True, uses the organic glow style instead of a square tile."""
    if outlined:
        return marker(name, size, accent)
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
    """Game icon drawn as itself with an accent border."""
    QtGui, QtCore = _qt()
    out = QtGui.QPixmap(size, size)
    out.fill(QtGui.QColor(0, 0, 0, 0))
    if not (sheet and id_ and has_icon(sheet, id_)):
        p = QtGui.QPainter(out); p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(accent))
        r = size / 2 - border
        p.drawEllipse(QtCore.QPointF(size / 2, size / 2), r, r); p.end()
        return out

    g = pixmap(sheet, id_, size - 2 * (border + 1))
    keyline, ring = _silhouette(g, "#0b0e14"), _silhouette(g, accent)
    ox, oy = (size - g.width()) // 2, (size - g.height()) // 2
    p = QtGui.QPainter(out); p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    
    for dx, dy in _disk_offsets(border + 1): p.drawPixmap(ox + dx, oy + dy, keyline)

    for dx, dy in _disk_offsets(border): p.drawPixmap(ox + dx, oy + dy, ring)
    p.drawPixmap(ox, oy, g); p.end()
    return out


@lru_cache(maxsize=64)
def asset_icon(sheet_name: str, size: int):
    """A bundled SVG or PNG map icon from assets/map_icons/<name>.(svg|png), scaled. 
    Checks the atlas first, then fallback to disk. None if missing."""
    QtGui, QtCore = _qt()
    
    # 1. Try atlas sprite-sheet first
    entry = atlas.find_entry("minimap", sheet_name)
    if entry and isinstance(entry, dict):
        a_path = atlas.resolve_path(entry.get("file", ""))
        if a_path:
            pm = _crop_atlas(
                a_path,
                entry.get("x", 0),
                entry.get("y", 0),
                entry.get("size", 96),
                size,
            )
            if pm and not pm.isNull():
                return pm

    # 2. Use the new 3-tier path resolver for map markers
    base = paths.map_icons_dir()
    
    # 2a. Check for SVG first
    svg_path = base / f"{sheet_name}.svg"
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

    # 2b. Fallback to PNG
    png_path = base / f"{sheet_name}.png"
    if png_path.exists():
        pm = QtGui.QPixmap(str(png_path))
        if not pm.isNull():
            return pm.scaled(size, size, QtCore.Qt.KeepAspectRatio,
                             QtCore.Qt.SmoothTransformation)

    return None


@lru_cache(maxsize=4096)
def marker(name: str, size: int, accent: str | None = None, border: int = 2):
    """A map-marker with optional accent outline."""
    QtGui, QtCore = _qt()
    g = asset_icon(name, max(1, size - 2 * (border + 1)))
    if g is None:
        out = QtGui.QPixmap(size, size); out.fill(QtGui.QColor(0, 0, 0, 0))
        p = QtGui.QPainter(out); p.setRenderHint(QtGui.QPainter.Antialiasing); p.setPen(QtCore.Qt.NoPen)
        if accent:
            p.setBrush(QtGui.QColor(accent)); p.drawEllipse(QtCore.QRectF(border, border, size-2*border, size-2*border))
        p.end(); return out if accent else None

    keyline, ox, oy = _silhouette(g, "#0b0e14"), (size - g.width()) // 2, (size - g.height()) // 2
    out = QtGui.QPixmap(size, size); out.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(out); p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)

    if accent:
        ring = _silhouette(g, accent)
        for dx, dy in _disk_offsets(border + 1): p.drawPixmap(ox + dx, oy + dy, keyline)
        for dx, dy in _disk_offsets(border): p.drawPixmap(ox + dx, oy + dy, ring)
    else:
        for dx, dy in _disk_offsets(1): p.drawPixmap(ox + dx, oy + dy, keyline)

    p.drawPixmap(ox, oy, g); p.end()
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
