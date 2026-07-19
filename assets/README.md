# Companion bundled assets

App-owned assets (not game data). Resolved at runtime by
`farever_companion.paths.assets_dir()` — the dev tree (`companion/assets/`) or,
in a packaged build, `_MEIPASS/assets` (see `package.bat`).

## `fonts/`
UI typography, loaded at startup via `QFontDatabase.addApplicationFont`
(`ui/theme.load_fonts`). The QSS lists Segoe UI / Consolas as fallbacks, so the
app still runs if these are removed.

| File                          | Family         | License | Source |
|-------------------------------|----------------|---------|--------|
| `Inter-Variable.ttf`          | Inter          | OFL 1.1 | github.com/rsms/inter (via google/fonts) |
| `JetBrainsMono-Variable.ttf`  | JetBrains Mono | OFL 1.1 | github.com/JetBrains/JetBrainsMono (via google/fonts) |

Full license texts: `Inter-OFL.txt`, `JetBrainsMono-OFL.txt`. Both are variable
fonts; Qt selects weights from the `wght` axis via QSS `font-weight`.

## `icons_ui/`
Monochrome stroke glyphs for UI chrome (nav rail, buttons, brand). 24×24
viewBox, `stroke="currentColor"`; `data/icons.ui_icon(name, color, size)`
substitutes the color and renders via QtSvg, cached by (name, color, size).
Glyph set follows Lucide (MIT, lucide.dev). Game content icons (items, units) are 
located in `assets/icons/` and `assets/atlas/`.

## Optional
- `app_icon.ico` / `app_icon.png` — a Farever logo mark for the window/taskbar
  (and the `.exe` via `--icon`). If absent, the app falls back to the tinted
  brand glyph. See `notes/UI_OVERHAUL.md §11`.
