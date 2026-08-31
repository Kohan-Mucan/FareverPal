"""Unit card component widget for the Codex page."""
from __future__ import annotations

import os
from PySide6 import QtCore, QtGui, QtWidgets
from ... import components as C
from ... import theme
from ....data import codex


class CodexUnitCard(QtWidgets.QFrame):
    """A visual card for a mob or pet in the bestiary."""
    # right-click toggles the card; keep the app-wide copy menu off it
    _no_copy_menu = True
    toggled = QtCore.Signal(bool)
    selected = QtCore.Signal(str)

    def __init__(self, uid: str, data: dict, kills: int, total: int, active: bool = True, compact: bool = False, hide_dungeon_badge: bool = False, parent=None):
        super().__init__(parent)
        self.uid = uid
        self.data = data
        self.active = active
        self.compact = compact
        self.hide_dungeon_badge = hide_dungeon_badge
        self.locked = False
        self.progress = data.get("progress", 0)
        self.setObjectName("Cell")
        
        if self.compact:
            self.setFixedWidth(110)
            self.setMinimumHeight(128)
        else:
            self.setFixedSize(130, 155)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        # Badges are lazily created on demand in set_active() to avoid allocating
        # 10+ child QWidgets for every card when 90% of cards show no badges.
        self._check_icon = None
        self._hide_icon = None
        self._dungeon_ico = None
        self._chest_ico = None
        self._vendor_ico = None
        self._shop_ico = None
        self._mob_ico = None
        self._soulstone_ico = None
        self._achievement_ico = None
        self._spark_ico = None

        # Picture (Icon)
        self.icon_tile = C.IconTile(size=80)
        lay.addWidget(self.icon_tile, 0, QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)

        # Name row
        self.name_container = QtWidgets.QWidget()
        self.name_lay = QtWidgets.QHBoxLayout(self.name_container)
        self.name_lay.setContentsMargins(0, 4, 0, 0)
        self.name_lay.setSpacing(4)
        self.name_lay.setAlignment(QtCore.Qt.AlignCenter)

        self.name_lbl = QtWidgets.QLabel(data.get("name", uid))
        self.name_lbl.setObjectName("FieldLabel")
        self.name_lbl.setStyleSheet("font-size: 11px; font-weight: bold;") 
        self.name_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.name_lbl.setWordWrap(True)
        if self.compact:
            self.name_lbl.setMinimumHeight(36)
        else:
            self.name_lbl.setMinimumHeight(50)
        
        self.name_lay.addWidget(self.name_lbl)

        lay.addWidget(self.name_container, 0, QtCore.Qt.AlignTop)
        lay.addStretch(1)

        # Use the icon from data if available, otherwise fallback to UID
        icon_val = data.get("icon") or uid
        if not isinstance(icon_val, str):
            icon_val = str(uid)
        if "." in icon_val:
            icon_val = icon_val.rsplit(".", 1)[0]
            if icon_val.endswith(".prefab"):
                icon_val = icon_val.rsplit(".", 1)[0]
        if "/" in icon_val or "\\" in icon_val:
            icon_val = os.path.splitext(os.path.basename(icon_val))[0]

        self.icon_id = icon_val
        self.sheet = "collection" if data.get("is_critter") else "units"

        # Tooltip with raw ID
        self.setToolTip(f"ID: {data['id']}\nClick to toggle visibility")
        
        self.set_active(active)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        # Sticker (compact) view: the cards sit close together, so a press on
        # the name strip must not count as a card press — accidentally
        # clicking a name shouldn't plot spawns or toggle visibility. Only the
        # icon area of a compact card responds.
        if self.compact and self.name_container.geometry().contains(event.position().toPoint()):
            event.ignore()
            return
        if event.button() == QtCore.Qt.RightButton:
            if not self.locked:
                self.active = not self.active
                self.set_active(self.active)
                self.toggled.emit(self.active)
        elif event.button() == QtCore.Qt.LeftButton:
            if hasattr(self, "selected"):
                self.selected.emit(self.uid)
        super().mousePressEvent(event)

    def _badge(self, attr: str, size: int = 21) -> C.IconTile:
        w = getattr(self, attr, None)
        if w is None:
            w = C.IconTile(size=size, parent=self)
            w.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            setattr(self, attr, w)
        return w

    def _hide_badge(self, attr: str) -> None:
        w = getattr(self, attr, None)
        if w is not None and w.isVisible():
            w.setVisible(False)

    @property
    def check_icon(self): return self._badge("_check_icon", 20)
    @property
    def hide_icon(self): return self._badge("_hide_icon", 20)
    @property
    def dungeon_ico(self): return self._badge("_dungeon_ico", 21)
    @property
    def chest_ico(self): return self._badge("_chest_ico", 21)
    @property
    def vendor_ico(self): return self._badge("_vendor_ico", 21)
    @property
    def shop_ico(self): return self._badge("_shop_ico", 21)
    @property
    def mob_ico(self): return self._badge("_mob_ico", 21)
    @property
    def soulstone_ico(self): return self._badge("_soulstone_ico", 21)
    @property
    def achievement_ico(self): return self._badge("_achievement_ico", 21)
    @property
    def spark_ico(self): return self._badge("_spark_ico", 21)

    def set_active(self, active: bool) -> None:
        """Update visual state (dimming and hide icon badge)."""
        self.active = active
        
        ach = self.data.get("achievement")
        ach_line = ""
        if isinstance(ach, dict) and ach.get("name"):
            meta = []
            if ach.get("category"):
                meta.append(str(ach["category"]))
            if ach.get("points") is not None:
                meta.append(f"{ach['points']} pts")
            ach_line = f"\nAchievement: {ach['name']}"
            if meta:
                ach_line += f"  ({' · '.join(meta)})"
            if ach.get("desc"):
                ach_line += f"\n   {ach['desc']}"

        drops_from = self.data.get("drops_from")
        is_soulstone_drop = codex.is_soulstone_drop(self.data)
        drop_line = ""
        if isinstance(drops_from, list) and drops_from:
            mob_ids = {d.get("id") for d in drops_from
                       if isinstance(d, dict) and d.get("id")}
            if mob_ids:
                drop_line = (f"\nDropped by {len(mob_ids)} Mobs (Soulstones)"
                             if is_soulstone_drop
                             else f"\nDropped by {len(mob_ids)} Mobs")

        if self.locked:
            self.setToolTip(f"ID: {self.data['id']}\nVisibility locked in Dungeons{drop_line}{ach_line}")
        else:
            self.setToolTip(f"ID: {self.data['id']}{drop_line}{ach_line}")

        # Update hide icon badge
        if not active and not self.locked:
            h_ico = self._badge("_hide_icon", 20)
            h_ico.set_ui_icon("eye-off", theme.ACCENT)
            h_ico.move(4, 2)
            h_ico.setVisible(True)
        else:
            self._hide_badge("_hide_icon")

        is_boss = self.data.get("is_boss", False)
        is_elite = self.data.get("is_elite", False)
        is_critter = self.data.get("is_critter", False) or self.sheet == "collection"
        is_spark_critter = is_critter and (self.data.get("drops_spark", False) or "spark" in (self.data.get("name") or "").lower() or "spark" in (self.data.get("id") or "").lower())
        
        # Apply icon style
        if self.compact:
            self.icon_tile.set(self.sheet, self.icon_id, accent="#00000000")
            
            if is_boss or is_spark_critter:
                bcol = theme.GOLD if active else "#a16207"
                if is_critter:
                    self.icon_tile.set_outlined(self.sheet, self.icon_id, accent=bcol, border=4)
                    self.icon_tile.setStyleSheet("border: 0; background: transparent;")
                else:
                    self.icon_tile.setStyleSheet(f"border: 4px solid {bcol}; border-radius: 40px; background: transparent;")
            elif is_elite:
                bcol = theme.SILVER if active else "#717171"
                self.icon_tile.setStyleSheet(f"border: 4px solid {bcol}; border-radius: 40px; background: transparent;")
            else:
                self.icon_tile.setStyleSheet("border: 0; background: transparent;")
            
            self.setStyleSheet("QFrame#Cell { background-color: transparent; border: 0; }")
        else:
            accent = theme.ACCENT if active else theme.ACCENT_DIM
            if is_spark_critter:
                bcol = theme.GOLD if active else "#a16207"
                self.icon_tile.set_outlined(self.sheet, self.icon_id, accent=bcol, border=4)
                self.icon_tile.setStyleSheet("border: 0;")
            else:
                self.icon_tile.set(self.sheet, self.icon_id, accent=accent)
                if is_boss:
                    bcol = theme.GOLD if active else "#a16207"
                    self.icon_tile.setStyleSheet(f"border: 2px solid {bcol};")
                elif is_elite:
                    bcol = theme.SILVER if active else "#717171"
                    self.icon_tile.setStyleSheet(f"border: 2px solid {bcol};")
                else:
                    self.icon_tile.setStyleSheet("border: 0;")

            if active:
                self.setStyleSheet("QFrame#Cell { background-color: rgba(56, 189, 248, 0.04); }")
            else:
                self.setStyleSheet("QFrame#Cell { background-color: transparent; border-color: #334155; }")
        
        # Update text colors
        color = theme.TEXT if active else theme.DIM
        self.name_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {color};")

        # Update check icon
        is_complete = self.progress >= 3
        if is_complete:
            c_ico = self._badge("_check_icon", 20)
            c_ico.set_ui_icon("check", theme.GOOD if active else theme.DIM)
            c_ico.move(self.width() - 24, 2)
            c_ico.setVisible(True)
        else:
            self._hide_badge("_check_icon")

        # Update metadata badges
        center_x = self.width() // 2
        icon_top = 4
        icon_right = center_x + 40
        x_off = icon_right - 12 if self.compact else icon_right + 2

        item_sources = codex.item_sources(self.data)
        is_achievement_item = "ach" in item_sources
        if is_achievement_item:
            b = self._badge("_achievement_ico", 21)
            b.set_achievement(theme.BLUE if active else theme.DIM)
            b.setStyleSheet("background: black; border-radius: 4px;")
            b.move(x_off, icon_top + 4)
            b.setVisible(True)
        else:
            self._hide_badge("_achievement_ico")

        is_shop_item = "shop" in item_sources
        if is_shop_item and not is_achievement_item:
            b = self._badge("_shop_ico", 21)
            b.set_marker("shop", theme.GOLD if active else theme.DIM, outlined=True)
            b.setStyleSheet("background: black; border-radius: 4px;")
            b.move(x_off, icon_top + 4)
            b.setVisible(True)
        else:
            self._hide_badge("_shop_ico")

        # Dungeon badge
        if "dungeon" in item_sources and not self.hide_dungeon_badge and not is_achievement_item and not is_shop_item:
            b = self._badge("_dungeon_ico", 21)
            d_col = theme.KIND_COLOR.get("dungeon", "#7c3aed")
            b.set_marker("dungeon", d_col if active else theme.DIM, outlined=True)
            b.setStyleSheet("background: black; border-radius: 4px;")
            b.move(x_off, icon_top + 4)
            b.setVisible(True)
        else:
            self._hide_badge("_dungeon_ico")

        # Chest badge
        is_chest_item = "chest" in item_sources
        if is_chest_item and "dungeon" not in item_sources and not is_achievement_item and not is_shop_item:
            b = self._badge("_chest_ico", 21)
            b.set_marker("chest", theme.GOLD if active else theme.DIM, outlined=True)
            b.setStyleSheet("background: black; border-radius: 4px;")
            b.move(x_off, icon_top + 4)
            b.setVisible(True)
        else:
            self._hide_badge("_chest_ico")

        # Vendor badge
        is_vendor_item = "npc" in item_sources
        if is_vendor_item and not is_chest_item and "dungeon" not in item_sources and not is_achievement_item and not is_shop_item:
            b = self._badge("_vendor_ico", 21)
            b.set_marker("npc", theme.GOLD if active else theme.DIM, outlined=True)
            b.setStyleSheet("background: black; border-radius: 4px;")
            b.move(x_off, icon_top + 4)
            b.setVisible(True)
        else:
            self._hide_badge("_vendor_ico")

        # Soulstone badge
        is_soulstone_item = "mob" in item_sources and codex.is_soulstone_drop(self.data)
        if is_soulstone_item and not is_achievement_item and not is_shop_item \
                and "dungeon" not in item_sources and not is_chest_item and not is_vendor_item:
            b = self._badge("_soulstone_ico", 21)
            s_col = theme.KIND_COLOR.get("soulstone", "#e879f9")
            b.set_marker("soulstone", s_col if active else theme.DIM, outlined=True)
            b.setStyleSheet("background: black; border-radius: 4px;")
            b.move(x_off, icon_top + 4)
            b.setVisible(True)
        else:
            self._hide_badge("_soulstone_ico")

        # Mob Drop badge
        is_mob_item = "mob" in item_sources and not is_soulstone_item
        if is_mob_item and not is_achievement_item and not is_shop_item \
                and "dungeon" not in item_sources and not is_chest_item and not is_vendor_item:
            b = self._badge("_mob_ico", 21)
            b.set_marker("sword", theme.BROWN if active else theme.DIM, outlined=True)
            b.setStyleSheet("background: black; border-radius: 4px;")
            b.move(x_off, icon_top + 4)
            b.setVisible(True)
        else:
            self._hide_badge("_mob_ico")

        # Spark Dust badge
        if self.data.get("drops_spark"):
            b = self._badge("_spark_ico", 21)
            b.set_marker("sparkdust", theme.GOLD if active else theme.DIM, outlined=True)
            b.setStyleSheet("background: transparent; border: 0;")
            b.move(x_off, icon_top + 50)
            b.setVisible(True)
        else:
            self._hide_badge("_spark_ico")
