"""Drops From rendering (item + craft detail panes): the full-width
boss/vendor rows, the 2-column source grid, and the long-list preview
with the '+N more sources' expander grouped by drop kind.
"""
from __future__ import annotations

import re
from functools import lru_cache

from PySide6 import QtCore, QtGui, QtWidgets

from ... import theme
from ....data import icons
from ....data import names
from ....data.items.sources import (_CHEST_COUNT_RE, _CHEST_GROUP_IDS,
                                    _CHEST_SINGLE_RE, _KIND_GROUP_LABEL,
                                    _KIND_LABEL, _NODE_SIZE_SUFFIXES,
                                    chest_source_label as _chest_source_label,
                                    chest_zone_name as _chest_zone_name)
from . import support
from .support import GLYPH_DOWN, GLYPH_UP, GLYPH_RIFT


def _boss_cell(d: dict, on_codex_click) -> QtWidgets.QWidget:
    """One boss source on its own full-width line: gold-outlined unit icon,
    GOLD name + dungeon sub-line; with on_codex_click the icon and name
    become Codex-jump buttons."""
    cell = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(cell)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    pm = icons.outlined("Units", d.get("source_id"), _ROW_ICON, theme.GOLD,
                        border=2, trim=True)  # trim fills small sprites
    if on_codex_click:
        tile = QtWidgets.QPushButton()
        tile.setIcon(QtGui.QIcon(pm))
        tile.setIconSize(QtCore.QSize(_ROW_ICON, _ROW_ICON))
        tile.setFixedSize(_ROW_ICON, _ROW_ICON)
        tile.setFlat(True)
        tile.setCursor(QtCore.Qt.PointingHandCursor)
        tile.setStyleSheet(
            "QPushButton{background:transparent;border:0;padding:0;}"
            f"QPushButton:hover{{background:"
            f"{theme.with_alpha(theme.ACCENT, 30)};border-radius:4px;}}")
        tile.clicked.connect(
            lambda _=False, sid=d.get("source_id"), nm=d["source"]:
            on_codex_click(sid, nm))
    else:
        tile = QtWidgets.QLabel()
        tile.setPixmap(pm)
    lay.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
    txt = QtWidgets.QVBoxLayout()
    txt.setSpacing(1)
    txt.setContentsMargins(0, 0, 0, 0)
    if on_codex_click:
        # link-style button: same Codex jump as the icon (gold, brightens,
        # explicit font-size — the 10px base font squeezed it to 12px tall)
        src = QtWidgets.QPushButton(d["source"])
        src.setFlat(True)
        src.setCursor(QtCore.Qt.PointingHandCursor)
        src.setStyleSheet(
            f"QPushButton{{color:{theme.GOLD};font-weight:600;font-size:13px;"
            "background:transparent;border:0;padding:0;text-align:left;}"
            f"QPushButton:hover{{color:{theme.TEXT};}}")
        src.clicked.connect(
            lambda _=False, sid=d.get("source_id"), nm=d["source"]:
            on_codex_click(sid, nm))
    else:
        src = support.WrapLabel(d["source"])
        src.setStyleSheet(
            f"color:{theme.GOLD};font-weight:600;font-size:13px;"
            "background:transparent;")
    txt.addWidget(src)
    if d.get("dungeon"):
        # the boss's dungeon, under the name, inside the text column
        sub = support.WrapLabel(d["dungeon"])
        sub.setObjectName("Mono")
        sub.setStyleSheet(
            f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:12px;background:transparent;")
        txt.addWidget(sub)
    lay.addLayout(txt, 1)
    return cell


# non-boss drop-kind icon markers/colors (the Codex card's source keys)
_KIND_ICON = {"chest": ("chest", theme.GOLD), "npc": ("npc", theme.GOLD),
              "unit": ("sword", theme.BROWN),
              "gatherable": ("flower", theme.GOOD),
              "achievement": ("trophy", theme.GOLD),
              "worldloot_affinity": ("sword", theme.BROWN),
              "worldrecipe": ("recipe", theme.GOLD)}
# vendor source id -> the NPC's actual unit sprite (the scan stamps the
# hub-spawn id, not the sprite); unknown vendors keep the npc marker
_NPC_SPRITES = (("WanderingMerchant", "TODO_WanderingMerchant"),
                ("DemonHuntMira", "DemonHunterMira"),
                ("DemonHuntZoey", "DemonHunterZoey"),
                ("MountTamer", "TODO_StableMaster"))


def _plain_icon(d: dict):
    """The 32px leading icon for a non-boss drop cell: the mob's unit sprite
    (brown outline), the vendor's NPC sprite, else the kind's map marker."""
    kind, sid = d["kind"], d.get("source_id") or ""
    if kind == "unit" and icons.has_icon("Units", sid):
        return icons.outlined("Units", sid, _ROW_ICON, theme.BROWN, border=2)
    if kind == "npc":
        for prefix, sprite in _NPC_SPRITES:
            if sid.startswith(prefix):
                return icons.pixmap("Units", sprite, _ROW_ICON)
    name, accent = _KIND_ICON.get(kind, ("sword", theme.BROWN))
    return icons.marker(name, _ROW_ICON, accent)


def _plain_cell(d: dict, on_codex_click=None,
                item_id: str | None = None) -> QtWidgets.QWidget:
    """One non-boss drop source as a 2-column-grid cell: the kind's leading
    icon, source + kind tag, locations beneath. Gathering-node ids render
    their merged node type; with on_codex_click the source name becomes a
    Codex jump when a card exists for it (see _codex_target)."""
    cell = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(cell)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    tile = QtWidgets.QLabel()
    tile.setPixmap(_plain_icon(d))
    tile.setFixedSize(_ROW_ICON, _ROW_ICON)
    lay.addWidget(tile, 0, QtCore.Qt.AlignVCenter)
    txt = QtWidgets.QVBoxLayout()
    txt.setSpacing(1)
    txt.setContentsMargins(0, 0, 0, 0)
    line = QtWidgets.QHBoxLayout()
    line.setSpacing(6)
    label = (f"{GLYPH_RIFT}  " if d["rift"] else "") + _row_label(d)
    target = _codex_target(d, item_id)
    if on_codex_click and target:
        src = QtWidgets.QPushButton(label)
        src.setFlat(True)
        src.setCursor(QtCore.Qt.PointingHandCursor)
        src.setStyleSheet(
            f"QPushButton{{color:{theme.TEXT};font-weight:600;font-size:13px;"
            "background:transparent;border:0;padding:0;text-align:left;}"
            f"QPushButton:hover{{color:{theme.ACCENT};}}")
        src.clicked.connect(
            lambda _=False, uid=target, nm=d["source"]:
            on_codex_click(uid, nm))
    else:
        src = support.WrapLabel(label)
        src.setStyleSheet(
            f"color:{theme.TEXT};font-weight:600;font-size:13px;"
            "background:transparent;")
    if d["kind"] == "achievement":
        # 'Achievement · Collect Gliders 25 (Collection · 5 pts)' — the
        # category/points the codex card shows on the achievement line
        # read inline (no tooltips on the Items page)
        extra = ""
        if d.get("category"):
            extra = f" ({d['category']}"
            if d.get("points") is not None:
                extra += f" · {d['points']} pts"
            extra += ")"
        if extra:
            src.setText(src.text() + extra)
    line.addWidget(src, 1)
    kind = QtWidgets.QLabel(_KIND_LABEL.get(d["kind"],
                                            d["kind"].upper()))
    kind.setStyleSheet(
        f"color:{theme.DIM};font-size:11px;letter-spacing:1px;"
        "background:transparent;")
    line.addWidget(kind, 0, QtCore.Qt.AlignVCenter)
    if d["kind"] == "npc" and not d.get("codex"):
        # the guild vendors sell at Rare quality — show the sale quality
        # and the town's sale level (no price: it's not the point); codex
        # mount/glider vendors (Perina Wann, Zoey) sell for currency, not
        # at Rare quality, so their rows skip the tag
        rare = QtWidgets.QLabel("RARE" + (f" L{d['hub_level']}"
                                          if d.get("hub_level") else ""))
        rare.setStyleSheet(
            f"color:{theme.rarity_color('Rare')};font-size:11px;"
            "letter-spacing:1px;background:transparent;")
        line.addWidget(rare, 0, QtCore.Qt.AlignVCenter)
    txt.addLayout(line)
    if d.get("locs") and not (d["kind"] == "npc" and not d.get("codex")
                               and (d.get("source_id")
                                    or "").startswith(("DemonHuntMira",
                                                       "DemonHuntZoey"))):
        # Guild Merchant rows ride the town's level next to the RARE tag
        # (RARE L20); the hub names ride here, chest rows keep plain names
        # one short preview line: locs flattened into names and capped at
        # two with a '+N' for the rest — the zone clusters 'A · B' would
        # otherwise wall the row ('Munster's Crossing · Gorgon's Hollow ·
        # Abandoned Mines · Aurock Mound +2')
        locs = [n for e in d["locs"] for n in e.split(" · ")]
        locs_txt = " · ".join(locs[:_PREVIEW_LOCS])
        if len(locs) > _PREVIEW_LOCS:
            locs_txt += f" +{len(locs) - _PREVIEW_LOCS}"
        loc = support.WrapLabel(locs_txt)
        loc.setObjectName("Mono")
        loc.setStyleSheet(
            f'color:{theme.MUTED};font-family:"{theme.MONO_FONT}", "Consolas", monospace;'
            "font-size:12px;background:transparent;")
        txt.addWidget(loc)
    lay.addLayout(txt, 1)
    return cell


# Drops From grid rows shown before the '+N more sources' expander — a
# common mat drops from 27 sources, and 14 full rows of that is a wall. The
# preview keeps the detail a card; the expander reveals every remaining row
_PREVIEW_DROPS = 8
# kind groups up to this many distinct names render as one plain line in
# the reveal (head + names, no toggle header — a header line for a handful
# of names is more lines, not fewer); bigger groups start collapsed so a
# long crate list can't wall the pane — one click opens them
_GROUP_PREVIEW_NAMES = 6
# locations shown on one short preview line before the '+N' overflow — the
# zone clusters 'A · B' would otherwise wall the row
_PREVIEW_LOCS = 2
# the leading cell icon size: the pixmap and the tile's fixed size must
# match, so a bump here keeps the sprite and its 32px tile aligned
_ROW_ICON = 32


def _friendly_source_name(d: dict) -> str:
    """Readable source name for a drop row: gathering-node ids get their
    size suffix and leading zone code stripped and are humanized
    ('R2Plant2_Small' -> 'Plant 2'); named sources pass through."""
    nm = d["source"]
    for suf in _NODE_SIZE_SUFFIXES:
        if nm.endswith(suf):
            nm = nm[: -len(suf)]
            break
    nm = re.sub(r"^R\d+(?=[A-Z])", "", nm)   # zone code: 'R2Plant2' -> 'Plant2'
    return names.humanize(nm) if " " not in nm else nm


def _node_type_label(d: dict) -> str:
    """The base node type a gathering source belongs to: size suffixes,
    zone codes and numbered/rare instances all collapse to one label
    ('R2Plant2_Small' + 'R2Plant3_Small' + 'R2PlantRare' -> 'Plant')."""
    nm = d["source"]
    for suf in _NODE_SIZE_SUFFIXES:
        if nm.endswith(suf):
            nm = nm[: -len(suf)]
            break
    nm = re.sub(r"^R\d+(?=[A-Z])", "", nm)   # zone code: 'R2Plant2' -> 'Plant2'
    nm = re.sub(r"\d+$", "", nm)              # numbered: 'Plant2' -> 'Plant'
    nm = re.sub(r"Rare$", "", nm)             # rare: 'PlantRare' -> 'Plant'
    return names.humanize(nm)


def _row_label(d: dict) -> str:
    """The display label for a drop row: gathering nodes read as their
    merged node type, chests drop the '(xN)' roll count, everything else
    keeps its readable name."""
    if d["kind"] == "gatherable":
        return _node_type_label(d)
    if d["kind"] == "chest":
        return _chest_source_label(d)
    return _friendly_source_name(d)


def _kind_group_rows(rows: list[dict],
                     on_codex_click=None,
                     on_expand=None,
                     kind_order: tuple[str, ...] | None = None,
                     collapse_kinds: tuple[str, ...] = ()) -> list[QtWidgets.QWidget]:
    """The expanded rows grouped per kind: short groups render as ONE
    wrapping line (no toggle header), large groups as a collapsible block
    starting collapsed. Kinds in `collapse_kinds` always render as a
    collapsible block starting collapsed, even when short — a MOBS drop
    hint stays hidden until its header is clicked. Names with a codex
    card render as jump buttons. `kind_order` reorders the group headers
    (GATHER first, then MOBS for craft materials)."""
    by: dict[str, list[dict]] = {}
    for d in rows:
        by.setdefault(d["kind"], []).append(d)
    if kind_order is None:
        order = [k for k in _KIND_GROUP_LABEL if k in by] \
            + [k for k in by if k not in _KIND_GROUP_LABEL]
    else:
        order = list(kind_order) + [k for k in by if k not in kind_order]
    out = []
    for kind in order:
        if kind not in by:
            continue      # ordered kinds may be absent after filtering
        grp = by[kind]
        title = f"{_KIND_GROUP_LABEL.get(kind, kind.upper())} · {len(grp)}"
        kind_label = _KIND_GROUP_LABEL.get(kind, kind.upper())
        # build the deduped, dot-separated name widgets first so the group
        # shape (plain line vs collapsible block) can be chosen after
        widgets: list[QtWidgets.QWidget] = []
        seen: set[str] = set()
        first = True
        for d in grp:
            nm = _row_label(d)
            if nm in seen:
                continue
            seen.add(nm)
            if not first:
                dot = QtWidgets.QLabel("·")
                dot.setStyleSheet(
                    f"color:{theme.MUTED};font-size:12px;"
                    "background:transparent;")
                widgets.append(dot)
            first = False
            target = _codex_target(d) if on_codex_click else None
            if target:
                btn = QtWidgets.QPushButton(nm)
                btn.setFlat(True)
                btn.setCursor(QtCore.Qt.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton{{color:{theme.TEXT};font-size:12px;"
                    "background:transparent;border:0;padding:0;text-align:left;}"
                    f"QPushButton:hover{{color:{theme.ACCENT};}}")
                btn.clicked.connect(
                    lambda _=False, uid=target, nm2=d["source"]:
                    on_codex_click(uid, nm2))
                widgets.append(btn)
            else:
                lbl = QtWidgets.QLabel(nm)
                lbl.setStyleSheet(
                    f"color:{theme.TEXT};font-size:12px;background:transparent;")
                widgets.append(lbl)
        if len(seen) <= _GROUP_PREVIEW_NAMES and kind not in collapse_kinds:
            # short groups read at a glance as one line — head + names, no
            # toggle header (a header line for a handful of names is more
            # lines, not fewer); kinds in `collapse_kinds` skip this
            # shortcut so they always start collapsed
            row = QtWidgets.QWidget()
            flow = support.FlowLayout(row)
            flow.setContentsMargins(0, 0, 0, 0)
            flow.setSpacing(6)
            head = QtWidgets.QLabel(f"{title} —")
            head.setStyleSheet(
                f"color:{theme.TEXT};font-weight:600;font-size:12px;"
                "background:transparent;")
            flow.addWidget(head)
            for w in widgets:
                flow.addWidget(w)
            out.append(row)
            continue
        # large group: a collapsible 'CHESTS · 17 ▾' header over the names,
        # starting collapsed so a long crate list can't wall the reveal
        group = QtWidgets.QWidget()
        gl = QtWidgets.QVBoxLayout(group)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(2)
        head = QtWidgets.QPushButton(f"{title}  {GLYPH_DOWN}")
        head.setFlat(True)
        head.setCursor(QtCore.Qt.PointingHandCursor)
        head.setStyleSheet(
            f"QPushButton{{color:{theme.TEXT};font-weight:600;font-size:12px;"
            "background:transparent;border:0;padding:0;text-align:left;}"
            f"QPushButton:hover{{color:{theme.ACCENT};}}")
        flow_row = support.FlowRow()
        for w in widgets:
            flow_row.layout().addWidget(w)
        flow_row.hide()
        gl.addWidget(head)
        gl.addWidget(flow_row)

        def _toggle(_=False, w=flow_row, b=head, t=title):
            showing = w.isVisible()
            w.setVisible(not showing)
            b.setText(f"{t}  {GLYPH_DOWN if showing else GLYPH_UP}")
            if on_expand is not None:
                on_expand()

        head.clicked.connect(_toggle)
        out.append(group)
    return out


def _vendor_codex_unit(d: dict) -> str:
    """The codex unit id behind a vendor drop row (the NPC sprite the icon
    shows), or '' when the vendor has no sprite."""
    sid = d.get("source_id") or ""
    for prefix, sprite in _NPC_SPRITES:
        if sid.startswith(prefix):
            return sprite
    return ""


# the mount/glider vendors' sprite prefix (codex cards name the vendor but
# carry no unit id — the scan's ids did, so the icon lookup needs the same
# prefix to draw the Stable Master / Demon Huntress sprites)
_VENDOR_SPRITE_PREFIX = {"Perina Wann": "MountTamer_NPC",
                         "Zoey, Demon Huntress": "DemonHuntZoey_NPC"}


def _codex_zone_names(coords: list[dict]) -> list[str]:
    """Readable zone names for codex coords ('Navelin', 'Greenhorn Forest'),
    deduped, order kept."""
    from ....geo import zones as geo_zones
    from ....data import dungeons
    out: list[str] = []
    seen: set[str] = set()
    for c in coords:
        zid = geo_zones.resolve_zone(c.get("x", 0), c.get("y", 0),
                                     c.get("z", 0))
        if not zid or not dungeons.zone_tag_from_zone_id(zid):
            continue
        nm = names.zone_name(zid)
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def codex_drop_rows(item_id: str) -> list[dict]:
    """Drop rows for a mount/glider taken from its codex card (the
    authoritative acquisition data): one 'npc' row per vendor with the
    exact sale spots, one 'unit' row per mob that drops it, and an
    'achievement' row when the card carries a reward. Empty when the card
    has no acquisition data or the item has no card."""
    from ....data import codex

    region = codex.find_unit_region(item_id)
    if not region:
        return []
    entry = next((e for e in codex.units_by_region(region)
                  if e.get("id") == item_id), None)
    if not entry:
        return []
    rows: list[dict] = []
    # vendors: group the card's sale spots by vendor name — one row per
    # vendor whose locs are exactly where THIS item is sold
    v_raw = entry.get("vendor_npcs") or entry.get("vendor_npc") or []
    by_name: dict[str, list[dict]] = {}
    for v in v_raw:
        if isinstance(v, dict) and v.get("name"):
            by_name.setdefault(v["name"], []).append(v)
    for vname, spots in by_name.items():
        rows.append({"kind": "npc", "codex": True, "source": vname,
                     "source_id": _VENDOR_SPRITE_PREFIX.get(vname, ""),
                     "locs": _codex_zone_names(spots), "n_locs": len(spots),
                     "boss": False, "rift": False})
    # mob drops: one row per mob the card lists, locs = the mob's spawns
    df = entry.get("drops_from")
    if isinstance(df, list):
        for d in df:
            mob_id = d.get("id") if isinstance(d, dict) else str(d)
            if not mob_id:
                continue
            mob_name = d.get("name") if isinstance(d, dict) else None
            if not mob_name:
                info = codex.enemies_data().get(mob_id, {})
                mob_name = info.get("name") or mob_id
            mcoords, _, _ = codex.resolve_locations(mob_id)
            rows.append({"kind": "unit", "source": mob_name,
                         "source_id": mob_id,
                         "locs": _codex_zone_names(mcoords),
                         "n_locs": len(mcoords),
                         "boss": False, "rift": False})
    # achievement reward: achievement-only mounts get their source here
    # instead of 'No drop sources found.', and achievement+mob mounts show both
    ach = entry.get("achievement")
    if isinstance(ach, dict) and ach.get("name"):
        row = {"kind": "achievement", "source": ach["name"],
               "boss": False, "rift": False}
        if ach.get("category"):
            row["category"] = ach["category"]
        if ach.get("points") is not None:
            row["points"] = ach["points"]
        rows.append(row)
    return rows


def _codex_target(d: dict, item_id: str | None = None) -> str | None:
    """The codex target a drop row can jump to — a card exists for it in
    some region — or None when the row has no codex entry. Vendors resolve
    through their NPC sprite, non-boss mobs through their source id, single
    chests to their row in the Codex Chests list. When the row's item has
    its own codex card (mounts/gliders sold by vendors), `item_id` prefers
    THAT card, which pins the item's exact sale spot(s)."""
    if d.get("boss"):
        return None      # boss rows use their own gold link path
    if d["kind"] == "npc":
        # an item with its own card pins the item's exact sale spot(s);
        # sprite-less vendors without an item card never link
        from ....data import codex
        if item_id and codex.find_unit_region(item_id):
            return item_id
        uid = _vendor_codex_unit(d)
    elif d["kind"] == "unit":
        uid = d.get("source_id") or ""
    elif d["kind"] == "chest":
        # only concrete single chests live in the codex Chests list — the
        # grouped crate/activity tokens have no per-chest row
        sid = d.get("source_id") or ""
        if sid and not _CHEST_GROUP_IDS.search(sid) \
                and _CHEST_SINGLE_RE.search(sid):
            return sid
        return None
    else:
        return None
    if not uid:
        return None
    from ....data import codex
    if codex.find_unit_region(uid):
        return uid
    return None


def _deduped_drops(drops: list[dict]) -> list[dict]:
    """Drop rows with the gathering-node variants merged by base node type
    (the survivor keeps every merged row's locations). Boss/vendor rows
    are never merged; first-seen order is kept."""
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for d in drops:
        if d["kind"] == "npc" or d.get("boss"):
            out.append(d)
            continue
        key = (d["kind"], _row_label(d))
        t = seen.get(key)
        if t is None:
            seen[key] = d
            out.append(d)
            continue
        for loc in d.get("locs") or ():
            if loc not in t["locs"]:
                t["locs"].append(loc)
        t["n_locs"] = (t.get("n_locs") or 0) + (d.get("n_locs") or 0)
    return out


def build_drops_body(drops: list[dict],
                     on_codex_click=None,
                     on_expand=None,
                     item_id: str | None = None) -> QtWidgets.QVBoxLayout:
    """The Drops From body: VENDOR and boss rows stay full-width on their
    own line; the rest flow through one 2-column grid, previewed to
    _PREVIEW_DROPS rows with a '+N more sources' expander grouped by kind.
    With on_codex_click, sources with a codex card render as jump links.
    Returns the pane layout."""
    body = QtWidgets.QVBoxLayout()
    body.setSpacing(0)
    drops = _deduped_drops(drops)
    grid = None      # open 2-column grid — every source becomes a cell
    grid_idx = 0
    preview_left = _PREVIEW_DROPS
    extra: list[dict] = []
    for d in drops:
        if d["kind"] == "npc" or d.get("boss"):
            # vendors and bosses stay full-width — the price/levels and
            # dungeon sub-lines need the room
            if grid is not None:
                body.addLayout(grid)
                grid = None
                grid_idx = 0
            body.addWidget(_boss_cell(d, on_codex_click) if d.get("boss")
                           else _plain_cell(d, on_codex_click,
                                            item_id=item_id))
            continue
        if preview_left:
            if grid is None:
                grid = QtWidgets.QGridLayout()
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setHorizontalSpacing(16)
                grid.setVerticalSpacing(6)
                grid.setColumnStretch(0, 1)
                grid.setColumnStretch(1, 1)
            grid.addWidget(_plain_cell(d, on_codex_click, item_id=item_id),
                           grid_idx // 2, grid_idx % 2,
                           QtCore.Qt.AlignTop)
            grid_idx += 1
            preview_left -= 1
        else:
            extra.append(d)
    if grid is not None:
        body.addLayout(grid)
    if extra:
        more = QtWidgets.QWidget()
        ml = QtWidgets.QVBoxLayout(more)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        # MOBS groups always start collapsed — the mob names stay hidden
        # until their header is clicked, so a long farming list can't wall
        # the reveal (the user asked for this on every Drops From pane)
        for w in _kind_group_rows(extra, on_codex_click, on_expand,
                                  collapse_kinds=("unit",)):
            ml.addWidget(w)
        more.hide()
        body.addWidget(more)
        n = len(extra)
        btn = QtWidgets.QPushButton(f"{GLYPH_DOWN}  +{n} more sources")
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setFlat(True)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.MUTED};"
            "border:0;padding:3px 0;font-size:10px;font-weight:600;"
            "text-align:left;}"
            f"QPushButton:hover{{color:{theme.ACCENT};}}")

        def _toggle(_=False, w=more, b=btn, count=n):
            on = w.isVisible()
            w.setVisible(not on)
            b.setText(f"{GLYPH_UP}  show fewer" if not on
                      else f"{GLYPH_DOWN}  +{count} more sources")
            if on_expand is not None:
                on_expand()

        btn.clicked.connect(_toggle)
        body.addWidget(btn)
    return body
