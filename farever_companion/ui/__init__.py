"""The PySide6 layer: one design system, and every window built on it.

`theme` plus `components` (and its split-out `toggles` / `cards` / `chips`)
are the shared vocabulary, `control_panel` the sidebar shell assembled from one
mixin per page in `pages/`, and `overlays/` the HUD windows owned by
`overlay_manager`. The only package that imports Qt: pages read the game from
`core.model.LiveModel`, and `game_attach` is the one sanctioned direct edge to
the process, because it owns that lifecycle.
"""
