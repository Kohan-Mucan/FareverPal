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
        try:
            QtGui.QPixmapCache.setCacheLimit(8192)  # Cap Qt global pixmap cache at 8 MB
        except Exception:
            pass
    return _QtGui, _QtCore


_MINIMAP_SHEET = None


@lru_cache(maxsize=1)
def _get_cached_atlas_sheet(path: str):
    """LRU cache for browsing heavy category atlas sheets (units, items, skills)."""
    QtGui, _ = _qt()
    pm = QtGui.QPixmap(path)
    return pm if not pm.isNull() else None


def _get_atlas_sheet(path: str):
    """Load atlas sheet: minimap atlas is kept permanently resident in RAM for
    zero-latency 60 FPS overlay rendering; other sheets use LRU caching."""
    global _MINIMAP_SHEET
    if "minimap" in path.lower():
        if _MINIMAP_SHEET is None or _MINIMAP_SHEET.isNull():
            QtGui, _ = _qt()
            _MINIMAP_SHEET = QtGui.QPixmap(path)
        return _MINIMAP_SHEET
    return _get_cached_atlas_sheet(path)


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


@lru_cache(maxsize=None)
def _icon_path(sheet: str, id_: str):
    # Cached: called once per icon in has_icon() AND again in _raw(), and
    # each call does several Path.exists() disk stats. The bundled asset
    # tree never changes at runtime, so the result is stable.
    # Normalize to the canonical plural name (e.g., "unit" -> "units")
    s_low = sheet.lower()
    canonical = _SHEET_MAP.get(s_low, s_low)
    
    # Build list of folders to try: plural lowercase, TitleCase, and original
    sheets_to_try = [canonical, canonical.capitalize(), sheet]
    seen = set()
    unique_sheets = [x for x in sheets_to_try if x and not (x in seen or seen.add(x))]
    
    base_dirs = [
        paths.fallback_icons_dir(),
    ]
            
    for base in base_dirs:
        if not base.exists():
            continue
        for ext in ("svg", "webp", "png"):
            # Try direct file in base (e.g. assets/svgs/<id>.svg)
            for cand_id in (id_, id_.lower()):
                p = base / f"{cand_id}.{ext}"
                if p.exists():
                    return p
            # Try category subfolder (e.g. assets/images/units/<id>.webp)
            for s in unique_sheets:
                for cand_id in (id_, id_.lower()):
                    p = base / s / f"{cand_id}.{ext}"
                    if p.exists():
                        return p
    return None


@lru_cache(maxsize=1024)
def _raw(sheet: str, id_: str):
    """The icon's source pixmap at its native resolution — the atlas cell crop
    when the id has atlas data, else the loose file on disk. None when the
    icon is missing. Atlas-first."""
    QtGui, _ = _qt()
    if sheet and id_:
        entry = atlas.find_entry(sheet, id_)
        if entry and isinstance(entry, dict):
            a_path = atlas.resolve_path(entry.get("file", ""))
            if a_path:
                sheet_pm = _get_atlas_sheet(str(a_path))
                if sheet_pm is not None and not sheet_pm.isNull():
                    n = entry.get("size", 64)
                    return sheet_pm.copy(entry.get("x", 0), entry.get("y", 0), n, n)
        # Fallback to loose file on disk
        path = _icon_path(sheet, id_)
        if path is not None:
            if path.suffix.lower() == ".svg":
                try:
                    from PySide6.QtSvg import QSvgRenderer
                    txt = path.read_text(encoding="utf-8")
                    if "currentColor" in txt:
                        txt = txt.replace("currentColor", "#ffffff")
                    pm = QtGui.QPixmap(96, 96)
                    pm.fill(QtGui.QColor(0, 0, 0, 0))
                    r = QSvgRenderer(QtCore.QByteArray(txt.encode("utf-8")))
                    p = QtGui.QPainter(pm)
                    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                    r.render(p, QtCore.QRectF(0, 0, 96, 96))
                    p.end()
                    if not pm.isNull():
                        return pm
                except Exception:
                    pass
            pm = QtGui.QPixmap(str(path))
            if not pm.isNull():
                return pm
    return None



@lru_cache(maxsize=1024)
def _content_bbox(sheet: str, id_: str):
    """(x, y, w, h) of the sprite's visible content inside its native cell, or
    None when the icon is missing or fully transparent. Pixels with alpha
    <= 8 count as background, so faint anti-aliasing never survives but soft
    edges don't get shaved either."""
    QtGui, _ = _qt()
    raw = _raw(sheet, id_)
    if raw is None:
        return None
    img = raw.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        row = memoryview(img.constScanLine(y)).cast("B")
        for x in range(w):
            if row[4 * x + 3] > 8:
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:
        return None
    return (minx, miny, maxx - minx + 1, maxy - miny + 1)


@lru_cache(maxsize=1024)
def pixmap(sheet: str, id_: str, size: int):
    """A QPixmap for an icon id, scaled to `size`. Never None (placeholder).

    Atlas-first: if the id has gfx/atlas data in raw_data, crop from the
    atlas sheet. Otherwise fall back to the individual file on disk.
    """
    QtGui, QtCore = _qt()
    raw = _raw(sheet, id_)
    if raw is not None:
        return raw.scaled(
            size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
    return _placeholder(size)


@lru_cache(maxsize=1024)
def pixmap_cropped(sheet: str, id_: str, size: int):
    """Like pixmap(), but the sprite's transparent margins are trimmed first and
    the visible content is scaled to fill `size` (aspect preserved, centred on a
    transparent canvas). Icons whose content already fills their cell are
    returned unchanged, so only sprites floating small in empty space (e.g.
    some boss sprites) are enlarged to fill the frame. Missing icons fall back
    to the placeholder."""
    QtGui, QtCore = _qt()
    raw = _raw(sheet, id_)
    if raw is None:
        return _placeholder(size)
    bb = _content_bbox(sheet, id_)
    if bb is None:
        return pixmap(sheet, id_, size)
    x, y, w, h = bb
    # Content already fills the cell -> plain scaling is identical and cheaper.
    if w >= raw.width() * 0.97 and h >= raw.height() * 0.97:
        return pixmap(sheet, id_, size)
    content = raw.copy(x, y, w, h).scaled(
        size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
    )
    out = QtGui.QPixmap(size, size)
    out.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(out)
    p.drawPixmap((size - content.width()) // 2, (size - content.height()) // 2, content)
    p.end()
    return out


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
    if not sheet or not id_:
        return False
    if atlas.find_entry(sheet, id_) is not None:
        return True
    return _icon_path(sheet, id_) is not None


def atlas_sprite_resolvable(sheet: str, id_: str) -> bool:
    """True only when the atlas entry exists AND its image file is actually
    present on disk (a renamed/missing sheet makes the entry unusable)."""
    if not sheet or not id_:
        return False
    entry = atlas.find_entry(sheet, id_)
    if not entry or not isinstance(entry, dict):
        return False
    return atlas.resolve_path(entry.get("file", "")) is not None


def trim_caches() -> None:
    """Drop every cached rendered pixmap (idle-memory sweep).

    Everything here repopulates lazily on the next request, so the only cost
    of a sweep is a redraw plus disk reload for icons that come back into
    view. Deliberately keeps `_icon_path` (tiny strings that save disk stats)
    and `_placeholder` (a handful of tiny squares)."""
    _raw.cache_clear()
    _content_bbox.cache_clear()
    pixmap.cache_clear()
    pixmap_cropped.cache_clear()
    tile.cache_clear()
    tile_marker.cache_clear()
    outlined.cache_clear()
    marker.cache_clear()
    asset_icon.cache_clear()
    tinted_asset.cache_clear()
    achievement_marker.cache_clear()
    ui_icon.cache_clear()
    brand_icon.cache_clear()
    tile_ui.cache_clear()
    _get_cached_atlas_sheet.cache_clear()  # heavy browse sheets (units/items/skills)
    # Note: _MINIMAP_SHEET is deliberately preserved so minimap overlay rendering never hitches.
    try:
        from PySide6 import QtGui
        QtGui.QPixmapCache.clear()      # Qt's own global pixmap cache
    except Exception:
        pass


def item_tile(item_id: str, item_name: str, size: int, accent: str) -> QtGui.QPixmap:
    """Smart item icon resolver: tries ID first, then display Name (since some
    assets use spaces), then falls back to the 'unit' sheet for scanned
    boss gear. Returns the standard tinted tile."""
    # 1. Try 'item' sheet with the technical ID
    if has_icon("item", item_id):
        return tile("item", item_id, size, accent)

    # 2. Try 'item' sheet with the display Name (handles names with spaces)
    if item_name and has_icon("item", item_name):
        return tile("item", item_name, size, accent)

    # 3. Fallback to 'unit' sheet (scanned mob/boss gear)
    if has_icon("unit", item_id):
        return tile("unit", item_id, size, accent)

    # 4. Final placeholder tile (the tinted square with ID if possible)
    return tile("item", item_id, size, accent)


@lru_cache(maxsize=1024)
def tile(sheet: str | None, id_: str | None, size: int, accent: str,
         trim: bool = False):
    """Game icon on a rounded, accent-tinted square, the standard row leading
    element (Loot table + both HUDs). `accent` is a hex color (rarity / faction
    / gold / cyan). Background = accent @18%, 1px border = accent @55%, the PNG
    centred with ~2px padding. Missing PNG -> the tinted square still shows.
    With `trim=True` the sprite's transparent margins are cut first so small
    sprites fill the tile instead of floating tiny in empty space (see
    pixmap_cropped)."""
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
        if trim:
            g = pixmap_cropped(sheet, id_, size - 4)
        else:
            g = pixmap(sheet, id_, size - 4)
        p.drawPixmap((size - g.width()) // 2, (size - g.height()) // 2, g)
    p.end()
    return pm


@lru_cache(maxsize=512)
def tile_marker(name: str, size: int, accent: str, outlined: bool = False,
                border: int = 2):
    """Map-marker PNG (assets/map_icons/<name>.png) on the standard accent-
    tinted square tile - the entity-HUD row icon for chests / orbs / world
    activities, matching the minimap's marker art.
    If `outlined` is True, uses the organic glow style instead of a square tile."""
    if outlined:
        return marker(name, size, accent, border=border)
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


@lru_cache(maxsize=512)
def outlined(sheet: str | None, id_: str | None, size: int, accent: str,
             border: int = 2, trim: bool = False):
    """Game icon drawn as itself with an accent border. With `trim=True`
    the sprite's transparent margins are cut first so small sprites fill
    the box instead of floating tiny in empty space."""
    QtGui, QtCore = _qt()
    out = QtGui.QPixmap(size, size)
    out.fill(QtGui.QColor(0, 0, 0, 0))
    if not (sheet and id_ and has_icon(sheet, id_)):
        p = QtGui.QPainter(out); p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(accent))
        r = size / 2 - border
        p.drawEllipse(QtCore.QPointF(size / 2, size / 2), r, r); p.end()
        return out

    b = 1 if size <= 28 else border
    g = (pixmap_cropped(sheet, id_, size - 2 * (b + 1)) if trim
         else pixmap(sheet, id_, size - 2 * (b + 1)))
    keyline, ring = _silhouette(g, "#0b0e14"), _silhouette(g, accent)
    ox, oy = (size - g.width()) // 2, (size - g.height()) // 2
    p = QtGui.QPainter(out); p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    
    for dx, dy in _disk_offsets(b + 1): p.drawPixmap(ox + dx, oy + dy, keyline)

    for dx, dy in _disk_offsets(b): p.drawPixmap(ox + dx, oy + dy, ring)
    p.drawPixmap(ox, oy, g); p.end()
    return out


@lru_cache(maxsize=512)
def marker(name: str, size: int, accent: str | None = None, border: int = 2,
           keyline: bool = True):
    """A map-marker with optional accent outline.

    `keyline=False` skips the dark 1px halo around the colored ring, so the
    outline reads as a single thin colored line instead of a ~2px edge.

    Missing/unknown marker assets degrade to an empty transparent pixmap (an
    accent dot when `accent` is given) instead of returning None, so callers
    like the Entity HUD's QLabel.setPixmap never crash on a missing asset."""
    QtGui, QtCore = _qt()
    b = 1 if size <= 28 else border
    g = asset_icon(name, max(1, size - 2 * (b + 1)))
    if g is None or g.isNull():
        out = QtGui.QPixmap(size, size)
        out.fill(QtGui.QColor(0, 0, 0, 0))
        p = QtGui.QPainter(out)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        if accent:
            p.setBrush(QtGui.QColor(accent))
            p.drawEllipse(QtCore.QRectF(b, b, size - 2 * b, size - 2 * b))
        p.end()
        return out

    keyline_px, ox, oy = _silhouette(g, "#0b0e14"), (size - g.width()) // 2, (size - g.height()) // 2
    out = QtGui.QPixmap(size, size)
    out.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(out)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)

    if accent:
        ring = _silhouette(g, accent)
        if keyline:
            for dx, dy in _disk_offsets(b + 1):
                p.drawPixmap(ox + dx, oy + dy, keyline_px)
        for dx, dy in _disk_offsets(b):
            p.drawPixmap(ox + dx, oy + dy, ring)
    else:
        for dx, dy in _disk_offsets(1):
            p.drawPixmap(ox + dx, oy + dy, keyline_px)

    p.drawPixmap(ox, oy, g)
    p.end()
    return out


def marker_qicon(name: str, size: int = 20, accent: str | None = None, border: int = 2):
    """`marker` wrapped as a QIcon (for QPushButton.setIcon)."""
    QtGui, _ = _qt()
    return QtGui.QIcon(marker(name, size, accent=accent, border=border))


@lru_cache(maxsize=64)
def achievement_marker(size: int, accent: str = "#fbbf24"):
    """A trophy glyph for achievement-sourced items.

    Renders the trophy asset tinted to `accent`. Falls back to a painted
    silhouette if the asset is missing. Cached by (size, accent)."""
    QtGui, QtCore = _qt()
    g = asset_icon("trophy", size)
    if g is not None:
        return _silhouette(g, accent)
    # Fallback (missing asset): painted silhouette — handles + cup bowl + stem + base
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(accent))
    w = float(size)
    p.drawEllipse(QtCore.QRectF(0.10 * w, 0.30 * w, 0.26 * w, 0.22 * w))
    p.drawEllipse(QtCore.QRectF(0.64 * w, 0.30 * w, 0.26 * w, 0.22 * w))
    p.drawRoundedRect(QtCore.QRectF(0.30 * w, 0.12 * w, 0.40 * w, 0.42 * w),
                      0.06 * w, 0.06 * w)
    p.drawRect(QtCore.QRectF(0.46 * w, 0.52 * w, 0.08 * w, 0.12 * w))
    p.drawRect(QtCore.QRectF(0.34 * w, 0.64 * w, 0.32 * w, 0.10 * w))
    p.end()
    return pm


def _placeholder_glyph(size: int) -> QtGui.QPixmap:
    """Fallback 16x16 icon when QtSvg can't load the real SVG.

    Renders a simple item-box icon so the card still has visual weight.
    """
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtGui.QPen(QtGui.QColor("#87929a"), 1.2))
    p.setBrush(QtCore.Qt.NoBrush)
    w = float(size)
    p.drawRoundedRect(QtCore.QRectF(0.30 * w, 0.12 * w, 0.40 * w, 0.42 * w),
                      0.06 * w, 0.06 * w)
    p.drawRect(QtCore.QRectF(0.46 * w, 0.52 * w, 0.08 * w, 0.12 * w))
    p.drawRect(QtCore.QRectF(0.34 * w, 0.64 * w, 0.32 * w, 0.10 * w))
    p.end()
    return pm


_MASTER_SVG_CACHE = {}


def _get_master_symbol(name: str) -> tuple[str, str] | None:
    """Extracts an individual icon (inner_svg, viewBox) from the single master icons.svg."""
    global _MASTER_SVG_CACHE
    candidate_paths = [
        paths.assets_dir() / "atlas" / "icons.svg",
        paths.assets_dir() / "icons.svg",
        paths.atlas_dir() / "icons.svg",
        paths.project_root().parent / "GameFiles" / "Farever" / "atlas" / "icons.svg",
    ]
    master_path = None
    for p in candidate_paths:
        if p.exists():
            master_path = p
            break
    if not master_path:
        return None
    try:
        mtime = master_path.stat().st_mtime
        if _MASTER_SVG_CACHE.get("mtime") != mtime or _MASTER_SVG_CACHE.get("path") != str(master_path):
            import xml.etree.ElementTree as ET
            try:
                ET.register_namespace('', "http://www.w3.org/2000/svg")
                ET.register_namespace('xlink', "http://www.w3.org/1999/xlink")
            except Exception:
                pass
            tree = ET.parse(master_path)
            root = tree.getroot()
            symbols = {}
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag in ("g", "symbol") and "id" in elem.attrib:
                    sym_id = elem.attrib["id"].lower().strip()
                    vb = elem.attrib.get("data-viewbox") or elem.attrib.get("viewBox") or "0 0 24 24"
                    attr_items = []
                    for k, v in elem.attrib.items():
                        if k not in ("data-viewbox", "viewBox", "id"):
                            k_clean = k.split("}")[-1] if "}" in k else k
                            attr_items.append(f'{k_clean}="{v}"')
                    attrs = " ".join(attr_items)
                    inner_parts = [ET.tostring(child, encoding="unicode") for child in elem]
                    inner_xml = "".join(inner_parts).replace("ns0:", "").replace(":ns0", "")
                    full_inner = f'<g {attrs}>{inner_xml}</g>' if attrs else inner_xml
                    symbols[sym_id] = (full_inner, vb)
                    symbols[sym_id.replace("_", "-")] = (full_inner, vb)
                    symbols[sym_id.replace("-", "_")] = (full_inner, vb)
            _MASTER_SVG_CACHE = {"mtime": mtime, "path": str(master_path), "symbols": symbols}
        return _MASTER_SVG_CACHE.get("symbols", {}).get(name.lower().strip())
    except Exception:
        return None


def _loose_candidates(name: str) -> tuple[str, ...]:
    """Filename stems to try for a loose icon, in order: the name as given,
    lowercased, plus dash/underscore-swapped variants (mirrors the master
    sprite sheet, which registers both `target-dummy` and `target_dummy`)."""
    out: list[str] = []
    for cand in (name, name.lower(), name.replace("_", "-"),
                 name.replace("-", "_"),
                 name.replace("_", "-").lower(),
                 name.replace("-", "_").lower()):
        if cand and cand not in out:
            out.append(cand)
    return tuple(out)


@lru_cache(maxsize=64)
def asset_icon(sheet_name: str, size: int):
    """A bundled SVG or PNG map icon from assets/map_icons/<name>.(svg|png), scaled.
    Checks the atlas first, then the master svg, then falls back to loose
    files in assets/icons; None if missing."""
    QtGui, QtCore = _qt()

    # 1. Try atlas sprite-sheet first
    entry = atlas.find_entry("minimap", sheet_name)
    if entry and isinstance(entry, dict):
        a_path = atlas.resolve_path(entry.get("file", ""))
        if a_path:
            src_sz = entry.get("size", 64)
            pm = _crop_atlas(
                a_path,
                entry.get("x", 0),
                entry.get("y", 0),
                src_sz,
                size,
            )
            if pm and not pm.isNull():
                return pm
    # 2. Try master icons.svg single-file sprite sheet next
    res = _get_master_symbol(sheet_name)
    if res:
        try:
            from PySide6.QtSvg import QSvgRenderer
            inner, vb = res
            pm = QtGui.QPixmap(size, size)
            pm.fill(QtGui.QColor(0, 0, 0, 0))
            txt = f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="{vb}">{inner}</svg>'
            if "currentColor" in txt:
                txt = txt.replace("currentColor", "#ffffff")
            r = QSvgRenderer(QtCore.QByteArray(txt.encode("utf-8")))
            if r.isValid():
                p = QtGui.QPainter(pm)
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                r.render(p, QtCore.QRectF(0, 0, size, size))
                p.end()
                if not pm.isNull():
                    return pm
        except Exception:
            pass

    # 3. Fallback to loose files on disk (svg, webp, png) in the single fallback folder
    fb_dir = paths.fallback_icons_dir()
    if fb_dir.exists():
        for cand_name in _loose_candidates(sheet_name):
            # 3a. Check SVG
            svg_file = fb_dir / f"{cand_name}.svg"
            if svg_file.exists():
                try:
                    from PySide6.QtSvg import QSvgRenderer
                    svg_txt = svg_file.read_text(encoding="utf-8")
                    if "currentColor" in svg_txt:
                        svg_txt = svg_txt.replace("currentColor", "#ffffff")
                    pm = QtGui.QPixmap(size, size)
                    pm.fill(QtGui.QColor(0, 0, 0, 0))
                    r = QSvgRenderer(QtCore.QByteArray(svg_txt.encode("utf-8")))
                    if r.isValid():
                        p = QtGui.QPainter(pm)
                        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                        r.render(p, QtCore.QRectF(0, 0, size, size))
                        p.end()
                        if not pm.isNull():
                            return pm
                except Exception:
                    pass

            # 3b. Check WebP / PNG
            for ext in ("webp", "png"):
                img_file = fb_dir / f"{cand_name}.{ext}"
                if img_file.exists():
                    pm = QtGui.QPixmap(str(img_file))
                    if not pm.isNull():
                        return pm.scaled(size, size, QtCore.Qt.KeepAspectRatio,
                                         QtCore.Qt.SmoothTransformation)

    return None


@lru_cache(maxsize=512)
def tinted_asset(name: str, color: str, size: int):
    """The pre-colored atlas/map sprite force-recolored to `color`, keeping
    its alpha shape and shading (per-pixel luminance scaled onto the color).
    None when the asset itself is missing."""
    QtGui, _ = _qt()
    g = asset_icon(name, size)
    if g is None or g.isNull():
        return None
    img = g.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
    h = color.lstrip("#")
    cr, cg, cb = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if not c.alpha():
                continue
            lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
            img.setPixelColor(x, y, QtGui.QColor(
                int(cr * lum), int(cg * lum), int(cb * lum), c.alpha()))
    return QtGui.QPixmap.fromImage(img)


@lru_cache(maxsize=1024)
def ui_icon(name: str, color: str, size: int):
    """A theme-tinted UI-chrome glyph.

    Resolution order: minimap atlas (raster) -> master icons.svg -> loose SVG.
    The atlas sprite is recolored to `color` via tinted_asset; SVGs use
    currentColor substitution. Missing/invalid -> an empty transparent pixmap."""
    QtGui, QtCore = _qt()

    # 1. Minimap atlas (pixel-art raster, recolored to the requested color)
    g = tinted_asset(name, color, size)
    if g is not None and not g.isNull():
        return g

    # 2. Master icons.svg single-file sprite sheet
    from PySide6.QtSvg import QSvgRenderer
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    txt = None
    res = _get_master_symbol(name)
    if res:
        inner, vb = res
        txt = f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="{vb}">{inner}</svg>'

    # 3. Fallback to loose SVG on disk (assets/icons)
    if not txt:
        for cand_dir in (paths.svgs_dir(),):
            if not cand_dir.exists():
                continue
            for cand_name in _loose_candidates(name):
                cand = cand_dir / f"{cand_name}.svg"
                if cand.exists():
                    try:
                        txt = cand.read_text(encoding="utf-8")
                        break
                    except Exception:
                        txt = None
            if txt:
                break

    if txt:
        try:
            rendered_txt = txt.replace("currentColor", color)
            r = QSvgRenderer(QtCore.QByteArray(rendered_txt.encode("utf-8")))
            if r.isValid():
                p = QtGui.QPainter(pm)
                m = max(1.0, size * 0.08)
                r.render(p, QtCore.QRectF(m, m, size - 2 * m, size - 2 * m))
                p.end()
                if not pm.isNull():
                    return pm
        except Exception:
            pass
    return pm


def ui_qicon(name: str, color: str, size: int = 18):
    """`ui_icon` wrapped as a QIcon (for QPushButton.setIcon)."""
    QtGui, _ = _qt()
    return QtGui.QIcon(ui_icon(name, color, size))


@lru_cache(maxsize=64)
def brand_icon(name: str, size: int):
    """A multi-color brand glyph from master icons.svg or loose SVG/image rendered AS-IS.
    Missing/invalid -> transparent pixmap. Cached by (name, size)."""
    QtGui, QtCore = _qt()
    from PySide6.QtSvg import QSvgRenderer
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    
    txt = None
    # 1. Try master icons.svg first
    res = _get_master_symbol(name)
    if res:
        inner, vb = res
        txt = f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="{vb}">{inner}</svg>'

    # 2. Fallback to loose SVG on disk (assets/svgs)
    if not txt:
        for cand_dir in (paths.svgs_dir(),):
            if not cand_dir.exists():
                continue
            for cand_name in _loose_candidates(name):
                cand = cand_dir / f"{cand_name}.svg"
                if cand.exists():
                    try:
                        txt = cand.read_text(encoding="utf-8")
                        break
                    except Exception:
                        txt = None
            if txt:
                break

    if txt:
        try:
            r = QSvgRenderer(QtCore.QByteArray(txt.encode("utf-8")))
            if r.isValid():
                p = QtGui.QPainter(pm)
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                r.render(p, QtCore.QRectF(0, 0, size, size))
                p.end()
                if not pm.isNull():
                    return pm
        except Exception:
            pass

    # 3. Fallback to loose raster image on disk (assets/svgs)
    cand_dir = paths.fallback_icons_dir()
    if cand_dir.exists():
        for ext in ("png", "webp"):
            for cand_name in _loose_candidates(name):
                cand = cand_dir / f"{cand_name}.{ext}"
                if cand.exists():
                    pm = QtGui.QPixmap(str(cand))
                    if not pm.isNull():
                        return pm.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

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


@lru_cache(maxsize=256)
def emoji_pixmap(ch: str, size: int = 18, color: str = "#ffffff"):
    """Draw a unicode glyph/emoji tinted to the given color onto a transparent QPixmap."""
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    f = QtGui.QFont()
    f.setPixelSize(int(size * 0.82))
    p.setFont(f)
    p.setPen(QtGui.QColor(color))
    p.drawText(pm.rect(), QtCore.Qt.AlignCenter, ch)
    p.end()
    return pm

