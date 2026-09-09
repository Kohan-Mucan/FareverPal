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
from typing import TYPE_CHECKING

if TYPE_CHECKING:   # annotation-only: Qt binds lazily inside _qt()
    from PySide6 import QtGui

from .. import paths
from . import atlas
from . import units

# Qt is imported lazily so the data layer stays importable headless.
_QtGui = None
_QtCore = None

# The paper-doll tile's stronger tint (2026-09-26), used ONLY when a caller
# asks for it via `item_tile(..., strong=True)`. The standard tile is 46/140
# (18%/55%); these roughly double the fill so a pale `Common` accent is
# actually visible on the near-black paper-doll cell. Every other surface keeps
# the standard values — see `tile`.
_TILE_BG_ALPHA = 88        # ~35%
_TILE_BORDER_ALPHA = 190   # ~75%


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


@lru_cache(maxsize=None)
def _icon_path(sheet: str, id_: str):
    # Cached: called once per icon in has_icon() AND again in _raw(), and
    # each call does several Path.exists() disk stats. The bundled asset
    # tree never changes at runtime, so the result is stable.
    # Normalize to the canonical plural name (e.g., "unit" -> "units")
    s_low = sheet.lower()
    canonical = atlas.SHEET_MAP.get(s_low, s_low)
    
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
    QtGui, QtCore = _qt()
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


def _is_mount_or_glider(item_id: str) -> bool:
    """A mount or glider belongs on the `collection` icon sheet, like the
    codex's companion rows (see `data/codex.py` `Mounts`/`Gliders` groups).

    The raw `item` sheet is for sellable gear; the `collection` sheet is the
    one the game uses for craftable/companion-ish units. Mounts and gliders
    do not appear on `item`, so a loot/companion row that only knows the item
    id would silently fall back to the `units` sheet (or a blank box). Reuse
    the same sheet both HUDs render by."""
    if not item_id:
        return False
    return item_id.startswith("Mount_") or item_id.startswith("Glider_")


def map_icon_sheet(item_id: str) -> str:
    """Return the idiomatically-correct icon sheet for `item_id`.

    Companions use the `collection` sheet already; mounts and gliders share
    that sheet now. Everything else keeps its existing sheet lookup.
    """
    if _is_mount_or_glider(item_id):
        return "collection"
    if units.is_companion(item_id):
        return "collection"
    return "item"


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


def item_tile(item_id: str, item_name: str, size: int, accent: str,
              *, strong: bool = False) -> "QtGui.QPixmap":
    """Smart item icon resolver: tries ID first, then display Name (since some
    assets use spaces), then falls back to the 'unit' sheet for scanned
    boss gear. Returns the standard tinted tile.

    `strong=True` uses the paper-doll tile's stronger tint (see `tile`); it
    defaults off so every other surface keeps the standard 18%/55%.
    """
    bg_a, bd_a = (_TILE_BG_ALPHA, _TILE_BORDER_ALPHA) if strong else (46, 140)

    # 1. Try 'item' sheet with the technical ID
    if has_icon("item", item_id):
        return tile("item", item_id, size, accent, bg_alpha=bg_a,
                    border_alpha=bd_a)

    # 2. Try 'item' sheet with the display Name (handles names with spaces)
    if item_name and has_icon("item", item_name):
        return tile("item", item_name, size, accent, bg_alpha=bg_a,
                    border_alpha=bd_a)

    # 3. Fallback to 'unit' sheet (scanned mob/boss gear)
    if has_icon("unit", item_id):
        return tile("unit", item_id, size, accent, bg_alpha=bg_a,
                    border_alpha=bd_a)

    # 4. Final placeholder tile (the tinted square with ID if possible)
    return tile("item", item_id, size, accent, bg_alpha=bg_a, border_alpha=bd_a)


def item_sprite_resolvable(item_id: str, item_name: str = "") -> bool:
    """True when `item_tile` would draw a REAL sprite for this item (2026-09-26).

    `item_tile` NEVER returns a null pixmap: when it cannot find a sprite it
    draws the tinted placeholder box, which on a filled tile reads as a broken
    image rather than as "this item has no art". So a caller that wants to fall
    back to something else has to ask this first.

    It walks the SAME three attempts `item_tile` makes, in the same order, so
    the two can never disagree about what "has a sprite" means. It uses
    `atlas_sprite_resolvable` rather than `has_icon` on purpose: an atlas entry
    whose sheet file is missing or renamed resolves to nothing at crop time,
    and that is exactly the case this is meant to catch.
    """
    return (atlas_sprite_resolvable("item", item_id)
            or bool(item_name and atlas_sprite_resolvable("item", item_name))
            or atlas_sprite_resolvable("unit", item_id))


@lru_cache(maxsize=1024)
def tile(sheet: str | None, id_: str | None, size: int, accent: str,
         trim: bool = False, bg_alpha: int = 46, border_alpha: int = 140):
    """Game icon on a rounded, accent-tinted square, the standard row leading
    element (Loot table + both HUDs). `accent` is a hex color (rarity / faction
    / gold / cyan). Background = accent @18%, 1px border = accent @55%, the PNG
    centred with ~2px padding. Missing PNG -> the tinted square still shows.
    With `trim=True` the sprite's transparent margins are cut first so small
    sprites fill the tile instead of floating tiny in empty space (see
    pixmap_cropped).

    `bg_alpha`/`border_alpha` override the tint strengths and DEFAULT TO THE
    VALUES ABOVE, so every existing caller is byte-identical. They exist for
    the paper-doll tiles (2026-09-26), which sit on a near-black cell rather
    than in a row: at 18% a pale `Common` accent is invisible there, and the
    dark consumable artwork had nothing to read against — measured glyph
    luminance 0.36-0.42 against the cell's 0.12, next to a Rare sword's 0.61.
    Only that page asks for the stronger tint; raising it globally would
    change the loot table and both HUDs too."""
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    bg = QtGui.QColor(accent); bg.setAlpha(bg_alpha)   # ~18%
    bd = QtGui.QColor(accent); bd.setAlpha(border_alpha)   # ~55%
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


def _placeholder_glyph(size: int) -> "QtGui.QPixmap":
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


def _master_svg_text(name: str) -> str | None:
    """The master icons.svg symbol for `name` as a standalone `<svg>` document.

    The sheet registers both `target-dummy` and `target_dummy`, so `name` is
    looked up as given; None when the sheet has no such symbol (or is missing).
    """
    res = _get_master_symbol(name)
    if not res:
        return None
    inner, vb = res
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="{vb}">{inner}</svg>')


def _loose_svg_text(name: str) -> str | None:
    """Text of the first loose `<cand>.svg` under the loose SVG folder.

    Candidates are tried in `_loose_candidates` order; a file that exists but
    will not read is skipped in favour of the next one, and None comes back
    when no candidate resolves (or the folder is absent).
    """
    cand_dir = paths.svgs_dir()
    if not cand_dir.exists():
        return None
    for cand_name in _loose_candidates(name):
        cand = cand_dir / f"{cand_name}.svg"
        if cand.exists():
            try:
                return cand.read_text(encoding="utf-8")
            except Exception:
                continue
    return None


def _render_svg(txt: str, size: int, color: str | None = None,
                margin: float = 0.0):
    """Render an SVG document string onto a `size`x`size` transparent pixmap.

    `color` substitutes every `currentColor` - the master sheet and the loose
    files are authored with it - and None leaves the document's own colors
    alone. `margin` (px) insets the drawing; 0 fills the tile. Returns None
    when the document will not parse, never a blank pixmap, so a caller keeps
    falling through to its next candidate.
    """
    QtGui, QtCore = _qt()
    if color is not None:
        txt = txt.replace("currentColor", color)
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))
    try:
        from PySide6.QtSvg import QSvgRenderer
        r = QSvgRenderer(QtCore.QByteArray(txt.encode("utf-8")))
        if not r.isValid():
            return None
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r.render(p, QtCore.QRectF(margin, margin, size - 2 * margin,
                                  size - 2 * margin))
        p.end()
    except Exception:
        return None
    return pm if not pm.isNull() else None


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
    svg = _master_svg_text(sheet_name)
    if svg:
        pm = _render_svg(svg, size, color="#ffffff")
        if pm is not None:
            return pm

    # 3. Fallback to loose files on disk (svg, webp, png) in the single fallback folder
    fb_dir = paths.fallback_icons_dir()
    if fb_dir.exists():
        for cand_name in _loose_candidates(sheet_name):
            # 3a. Check SVG
            svg_file = fb_dir / f"{cand_name}.svg"
            if svg_file.exists():
                try:
                    svg_txt = svg_file.read_text(encoding="utf-8")
                except Exception:
                    svg_txt = None
                if svg_txt:
                    pm = _render_svg(svg_txt, size, color="#ffffff")
                    if pm is not None:
                        return pm

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
    currentColor substitution.

    Missing/invalid -> a NULL pixmap (2026-09-26). It used to return a
    transparent-but-NON-null one, which every caller's `isNull()` test read as
    a hit: a name with no glyph drew an empty box and the caller's own
    fallback (a letter, a different sprite) never ran. The Player Inspect
    class avatars were blank boxes that way — the classes simply are not
    glyph names. A null pixmap is falsy, so the `ui_icon(...) or fallback`
    call sites keep working too."""
    QtGui, _ = _qt()

    # 1. Minimap atlas (pixel-art raster, recolored to the requested color)
    g = tinted_asset(name, color, size)
    if g is not None and not g.isNull():
        return g

    # 2. Master icons.svg, then 3. the loose SVG on disk (assets/icons).
    # Inset by 8%: this one is drawn as chrome beside text, and a glyph that
    # touches its own tile edge reads heavier than the text it labels.
    txt = _master_svg_text(name) or _loose_svg_text(name)
    if txt:
        pm = _render_svg(txt, size, color=color,
                         margin=max(1.0, size * 0.08))
        if pm is not None:
            return pm
    return QtGui.QPixmap()          # nothing resolved -> NULL, not blank


def ui_qicon(name: str, color: str, size: int = 18):
    """`ui_icon` wrapped as a QIcon (for QPushButton.setIcon)."""
    QtGui, _ = _qt()
    return QtGui.QIcon(ui_icon(name, color, size))


@lru_cache(maxsize=64)
def brand_icon(name: str, size: int):
    """A multi-color brand glyph from master icons.svg or loose SVG/image rendered AS-IS.
    Missing/invalid -> transparent pixmap. Cached by (name, size).

    Its only consumer was the account row's brand button (removed with the
    account feature, 2026-09-27); the cache-clear in trim_caches still names
    it, and the glyph itself stays for any future sidebar branding."""
    QtGui, QtCore = _qt()
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtGui.QColor(0, 0, 0, 0))

    # 1. master icons.svg, then 2. the loose SVG on disk (assets/svgs).
    # Rendered AS-IS: a brand glyph keeps its own colors, so no color is given.
    txt = _master_svg_text(name) or _loose_svg_text(name)
    if txt:
        g = _render_svg(txt, size)
        if g is not None:
            return g

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

