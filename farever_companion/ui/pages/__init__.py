"""The sidebar pages, one mixin per page, composed into `ControlPanel`.

`control_panel.py` is the assembly point, not the home of the pages: it mixes
in one class per page from here, and `ui/nav_registry.py` (NAV) names the
sidebar order. This `__init__` re-exports nothing on purpose, for the same
reason `ui/overlays/` does not - every page is reached by an explicit
`from .pages.<name> import <Name>PageMixin` at the top of `control_panel.py`,
and a hub here would only add an import edge PyInstaller could follow, growing
the frozen bundle for no runtime gain.

- combat_page.py   the Combat & DPS page (+ combat_history, combat_roster,
                   combat_rail, combat_compare beside it)
- party_page.py    the Party page (+ party_grid, party_profile, party_roster,
                   party_rows, party_stats)
- overlays.py      the HUD overlay cards
- log.py           the Activity Log
- pb_page.py       the profile/build page
- tile_delegate.py the shared QStyledItemDelegate for the codex/grid tiles
- codex/ items/ craft/ settings/ server/
                   the five page packages, each naming its own mixins
"""
