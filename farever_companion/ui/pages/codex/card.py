"""Unit card component widget for the Codex page."""
from __future__ import annotations

import os
from PySide6 import QtCore, QtGui, QtWidgets
from ... import components as C
from ... import theme
from ....data import codex


class CodexUnitCard(QtWidgets.QFrame):
    """A visual card for a mob or pet in the bestiary."""
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

        # Check Icon (Absolute positioned in top right)
        self.check_icon = C.IconTile(size=20, parent=self)
        self.check_icon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.check_icon.setVisible(False)
        self.check_icon.move(self.width() - 24, 2)

        # Hide Icon (Absolute positioned in top left when hidden)
        self.hide_icon = C.IconTile(size=20, parent=self)
        self.hide_icon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.hide_icon.setVisible(False)
        self.hide_icon.move(4, 2)

        # Dungeon Badge (Overlay)
        self.dungeon_ico = C.IconTile(size=21, parent=self)
        self.dungeon_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.dungeon_ico.setVisible(False)

        # Chest Badge (Overlay)
        self.chest_ico = C.IconTile(size=21, parent=self)
        self.chest_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.chest_ico.setVisible(False)

        # Vendor Badge (Overlay) — NPC marker for vendor-sourced items
        self.vendor_ico = C.IconTile(size=21, parent=self)
        self.vendor_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.vendor_ico.setVisible(False)

        # Shop Badge (Overlay) — the cash-shop 'shop' marker (the old vendor
        # texture, reused for cash-shop / early-access items)
        self.shop_ico = C.IconTile(size=21, parent=self)
        self.shop_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.shop_ico.setVisible(False)

        # Mob Drop Badge (Overlay) — swords glyph for mounts/gliders that
        # drop from specific mobs
        self.mob_ico = C.IconTile(size=21, parent=self)
        self.mob_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.mob_ico.setVisible(False)

        # Achievement Badge (Overlay) — drawn trophy glyph; marks mounts/
        # gliders awarded by achievements (ach.json rewards)
        self.achievement_ico = C.IconTile(size=21, parent=self)
        self.achievement_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.achievement_ico.setVisible(False)

        # Spark Dust Badge (Overlay)
        self.spark_ico = C.IconTile(size=21, parent=self)
        self.spark_ico.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.spark_ico.setVisible(False)

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

    def set_active(self, active: bool) -> None:
        """Update visual state (dimming and hide icon badge)."""
        self.active = active
        
        ach = self.data.get("achievement")
        ach_line = ""
        if isinstance(ach, dict) and ach.get("name"):
            # Category + points ride along in the payload; surface them so
            # players see the achievement's scope instead of dead weight.
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

        # Mounts/gliders that drop from mobs surface the drop count (unique
        # mobs) instead of the generic 'Plot spawns' hint.
        drops_from = self.data.get("drops_from")
        drop_line = ""
        if isinstance(drops_from, list) and drops_from:
            mob_ids = {d.get("id") for d in drops_from
                       if isinstance(d, dict) and d.get("id")}
            if mob_ids:
                drop_line = f"\nDropped by {len(mob_ids)} Mobs"

        if self.locked:
            self.setToolTip(f"ID: {self.data['id']}\nVisibility locked in Dungeons\nLeft-click: Plot spawns{drop_line}{ach_line}")
        else:
            status_text = "Hidden" if not active else "Visible"
            self.setToolTip(f"ID: {self.data['id']}\nStatus: {status_text}\nLeft-click: Plot spawns\nRight-click: Toggle hide/unhide{drop_line}{ach_line}")

        # Update hide icon badge
        if not active and not self.locked:
            self.hide_icon.set_ui_icon("eye-off", theme.ACCENT)
            self.hide_icon.setVisible(True)
        else:
            self.hide_icon.setVisible(False)

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
            self.check_icon.set_ui_icon("check", theme.GOOD if active else theme.DIM)
            self.check_icon.setVisible(True)
            self.check_icon.move(self.width() - 24, 2)
        else:
            self.check_icon.setVisible(False)

        # Update metadata badges
        center_x = self.width() // 2
        icon_top = 4
        icon_right = center_x + 40
        x_off = icon_right - 12 if self.compact else icon_right + 2

        # Source badges — which of vendor/achievement/dungeon/chest/shop/mob
        # this item carries, via the shared codex.item_sources() helper (same
        # predicates the Pets/Others legend filters use, so they can't drift).
        # Priority: achievement > shop > dungeon > chest > vendor > mob — only
        # the defining source shows, and if one ever coexists the
        # higher-priority badge wins the spot.
        item_sources = codex.item_sources(self.data)
        is_achievement_item = "ach" in item_sources
        if is_achievement_item:
            self.achievement_ico.set_achievement(theme.BLUE if active else theme.DIM)
            self.achievement_ico.setStyleSheet("background: black; border-radius: 4px;")
            self.achievement_ico.move(x_off, icon_top + 4)
            self.achievement_ico.setVisible(True)
        else:
            self.achievement_ico.setVisible(False)

        # Shop badge (cash-shop / early-access premium items, e.g. Sparkling
        # Horsean) — the 'shop' marker from the atlas.
        is_shop_item = "shop" in item_sources
        if is_shop_item and not self.achievement_ico.isVisible():
            self.shop_ico.set_marker("shop", theme.GOOD if active else theme.DIM, outlined=True)
            self.shop_ico.setStyleSheet("background: black; border-radius: 4px;")
            self.shop_ico.move(x_off, icon_top + 4)
            self.shop_ico.setVisible(True)
        else:
            self.shop_ico.setVisible(False)

        # Dungeon badge
        if "dungeon" in item_sources and not self.hide_dungeon_badge and not self.achievement_ico.isVisible() and not self.shop_ico.isVisible():
            self.dungeon_ico.set_marker("dungeon", theme.TEXT if active else theme.DIM, outlined=True)
            self.dungeon_ico.setStyleSheet("background: black; border-radius: 4px;")
            self.dungeon_ico.move(x_off, icon_top + 4)
            self.dungeon_ico.setVisible(True)
        else:
            self.dungeon_ico.setVisible(False)

        # Chest badge
        is_chest_item = "chest" in item_sources
        if is_chest_item and not self.dungeon_ico.isVisible() and not self.achievement_ico.isVisible() and not self.shop_ico.isVisible():
            self.chest_ico.set_marker("chest", theme.GOLD if active else theme.DIM, outlined=True)
            self.chest_ico.setStyleSheet("background: black; border-radius: 4px;")
            self.chest_ico.move(x_off, icon_top + 4)
            self.chest_ico.setVisible(True)
        else:
            self.chest_ico.setVisible(False)

        # Vendor badge (npc marker — person + money bag silhouette; the old
        # 'shop' texture is being reused for the cash shop, so vendors get the
        # new NPC glyph instead). Same treatment as the dungeon/chest badges.
        is_vendor_item = "npc" in item_sources
        if is_vendor_item and not self.dungeon_ico.isVisible() and not self.chest_ico.isVisible() and not self.achievement_ico.isVisible() and not self.shop_ico.isVisible():
            self.vendor_ico.set_marker("npc", theme.GOLD if active else theme.DIM, outlined=True)
            self.vendor_ico.setStyleSheet("background: black; border-radius: 4px;")
            self.vendor_ico.move(x_off, icon_top + 4)
            self.vendor_ico.setVisible(True)
        else:
            self.vendor_ico.setVisible(False)

        # Mob Drop badge (mounts/gliders that drop from specific mobs) — the
        # single-sword marker. Lowest priority: only shows when no defining
        # source badge did.
        is_mob_item = "mob" in item_sources
        if is_mob_item and not self.achievement_ico.isVisible() and not self.shop_ico.isVisible() \
                and not self.dungeon_ico.isVisible() and not self.chest_ico.isVisible() and not self.vendor_ico.isVisible():
            self.mob_ico.set_marker("sword", theme.BROWN if active else theme.DIM, outlined=True)
            self.mob_ico.setStyleSheet("background: black; border-radius: 4px;")
            self.mob_ico.move(x_off, icon_top + 4)
            self.mob_ico.setVisible(True)
        else:
            self.mob_ico.setVisible(False)

        # Spark Dust badge
        if self.data.get("drops_spark"):
            self.spark_ico.set_marker("sparkdust", theme.GOLD if active else theme.DIM, outlined=False)
            self.spark_ico.setStyleSheet("background: transparent; border: 0;")
            self.spark_ico.move(x_off, icon_top + 50)
            self.spark_ico.setVisible(True)
        else:
            self.spark_ico.setVisible(False)
