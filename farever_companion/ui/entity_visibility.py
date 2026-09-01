"""Entity and companion visibility synchronization across Codex, Settings, and Minimap."""
from __future__ import annotations

from ..data import names


class EntityVisibilityMixin:
    """Mixin synchronizing entity and companion hide/show states between UI tabs and tracker."""

    def _set_unit_hidden(self, uid_or_uids, hidden: bool) -> None:
        profile = self.model.player_profile() if getattr(self, "model", None) else None
        if not profile:
            if hasattr(self, "_refresh_codex_sync"):
                self._refresh_codex_sync()
            return
        uids = [uid_or_uids] if isinstance(uid_or_uids, str) else uid_or_uids
        for u in uids:
            self.s.toggle_unit_hidden(u, hidden, profile)

        if hidden:
            tr = getattr(self, "tracker", None) or getattr(self, "_tracker", None)
            if tr and self.s.track_kind == "unit":
                tid = self.s.track_id
                if any(u == tid for u in uids):
                    tr.clear()

        if hidden and len(uids) == 1:
            self._last_hidden_unit = (uids[0], names.unit_name(uids[0]) or uids[0])
        elif not hidden and self._last_hidden_unit and self._last_hidden_unit[0] in uids:
            self._last_hidden_unit = None

        if hasattr(self, "_unit_chips") and self._unit_chips:
            for us, _n, chip in self._unit_chips:
                if not self._widget_alive(chip):
                    continue
                if (isinstance(us, list) and uids == us) or (not isinstance(us, list) and us in uids):
                    chip.blockSignals(True)
                    chip.setChecked(not hidden)
                    chip.blockSignals(False)
                    chip.restyle()
                    break

        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()

    def _set_all_units_hidden(self, hidden: bool) -> None:
        profile = self.model.player_profile() if getattr(self, "model", None) else None
        if hidden:
            tr = getattr(self, "tracker", None) or getattr(self, "_tracker", None)
            if tr and self.s.track_kind == "unit":
                tr.clear()

        if hasattr(self, "_unit_chips") and self._unit_chips:
            all_uids = []
            for item in self._unit_chips:
                uids = item[0]
                if isinstance(uids, list):
                    all_uids.extend(uids)
                else:
                    all_uids.append(uids)

            self.s.set_all_units_hidden(all_uids, hidden, profile)

            for _, _, chip in self._unit_chips:
                chip.blockSignals(True)
                chip.setChecked(not hidden)
                chip.blockSignals(False)
                chip.restyle()

        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

        if hasattr(self, "_update_unit_tag"):
            self._update_unit_tag()

    def _set_companion_hidden(self, uid: str, hidden: bool) -> None:
        profile = self.model.player_profile() if getattr(self, "model", None) else None
        self.s.toggle_companion_hidden(uid, hidden, profile)

        if hidden:
            tr = getattr(self, "tracker", None) or getattr(self, "_tracker", None)
            if tr and self.s.track_kind == "unit":
                tid = self.s.track_id
                if uid == tid:
                    tr.clear()

            nm = names.unit_name(uid)
            if not nm or nm == uid:
                from ..data import codex
                info = codex.enemies_data().get(uid, {})
                nm = info.get("name") or uid

            self._last_hidden_comp = (uid, nm or uid)
        elif not hidden and self._last_hidden_comp and self._last_hidden_comp[0] == uid:
            self._last_hidden_comp = None

        if hasattr(self, "_companion_chips") and self._companion_chips:
            for u, _n, chip in self._companion_chips:
                if u == uid:
                    chip.blockSignals(True)
                    chip.setChecked(not hidden)
                    chip.blockSignals(False)
                    chip.restyle()
                    break

        if hasattr(self, "_refresh_codex_sync"):
            self._refresh_codex_sync()

        if hasattr(self, "_update_companion_tag"):
            self._update_companion_tag()

    def _unhide_last_unit(self) -> None:
        if not getattr(self, "_last_hidden_unit", None):
            return
        uid, _name = self._last_hidden_unit
        self._set_unit_hidden(uid, False)
        self._last_hidden_unit = None

    def _unhide_last_comp(self) -> None:
        if not getattr(self, "_last_hidden_comp", None):
            return
        uid, _name = self._last_hidden_comp
        self._set_companion_hidden(uid, False)
        self._last_hidden_comp = None
