"""The "Tactical Overlay" design system, one palette + one stylesheet (QSS).

Tokens are taken verbatim from the user's exported Stitch design system
(`stitch_farever_companion_gaming_utility/tactical_overlay_design_system/
DESIGN.md` + the per-screen HTML): **strictly geometric / sharp (0px corners)**,
layered charcoal surfaces, cyan accent, Inter for UI / JetBrains Mono for data.
`apply(app)` loads the bundled fonts and installs the QSS once; the control
panel and every overlay inherit the same look.
"""
from __future__ import annotations

# --- palette (exact, from DESIGN.md) --------------------------------------
# surfaces, darkest -> lightest
BG = "#0e0f13"          # base canvas + input backgrounds
SURFACE = "#0f1418"     # main content area
LOWEST = "#0a0f12"      # footer / lowest container
PANEL_LOW = "#171c20"   # cells (toggles, steppers, inputs containers)
PANEL = "#1b2024"       # cards, top bar (surface-container)
PANEL_HI = "#252b2e"    # hover / high container
HIGHEST = "#303539"     # active nav bg, icon boxes (surface-container-highest)
BORDER = "#3e484f"      # outline-variant - the structural border everywhere

# text
TEXT = "#dee3e8"        # on-surface
MUTED = "#bdc8d1"       # on-surface-variant (labels, descriptions)
DIM = "#87929a"         # outline (very muted: coord baselines, etc.)

# accents
ACCENT = "#38bdf8"      # primary-container - buttons, toggles, ticks, active borders
ACCENT_LIGHT = "#8ed5ff"  # primary - active nav text/icon, sparkline
ON_ACCENT = "#004965"   # on-primary-container - text on cyan buttons
TOGGLE_THUMB_ON = "#00354a"  # on-primary - dark thumb on an active (cyan) toggle
ACCENT_DIM = "#13384a"  # cyan tint for selection backgrounds
# the hand-tuned default accent shades; set_accent restores them exactly when the
# user picks the design cyan rather than re-deriving. Order matches the globals
# reassigned in set_accent: (ACCENT, LIGHT, DIM, ON_ACCENT, TOGGLE_THUMB_ON).
_DESIGN_ACCENT = (ACCENT, ACCENT_LIGHT, ACCENT_DIM, ON_ACCENT, TOGGLE_THUMB_ON)

GOLD = "#eac331"        # secondary
SILVER = "#c0c0c0"      # elite
ORANGE = "#f1a02b"      # tertiary-container
NEUTRAL = DIM
DANGER = "#ffb4ab"      # error - enemy / big DPS number
GOOD = "#4ade80"        # success
LIME = "#facc15"        # tracked large gatherables
CHEST = GOLD

HERO = {
    "warrior": ORANGE,
    "rogue": GOOD,
    "mage": ACCENT,
    "priest": GOLD,
}

# fonts
UI_FONT = "Inter"
MONO_FONT = "JetBrains Mono"

RARITY = {
    "Common": "#c2c6d0",
    "Uncommon": "#56d364",
    "Rare": "#539bf5",
    "Epic": "#c297ff",
    "Legendary": "#f0a836",
}
KIND_COLOR = {
    "enemy": DANGER,
    "companion": "#7aa2f7",
    "hero": DIM,
    "chest": CHEST,
    "flower": GOOD,
    "ore": "#c2c6d0",
    "obelisk": ACCENT,
    "orb": "#3b82f6",        # matches the blue orb marker art
    "chest_orb": GOLD,
    "activity": "#8fa395",   # world-activity loot drops (grey-green marker)
    "dungeon": "#7c3aed",    # dungeon entrances / teleporters
    "pos": ACCENT,
}


def rarity_color(rarity: str | None) -> str:
    return RARITY.get(rarity or "", TEXT)


def with_alpha(hex_color: str, alpha: int) -> str:
    """`#rrggbb` -> `rgba(r,g,b,a/255)` for QSS backgrounds/tints."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha / 255:.3f})"


# --- fonts ----------------------------------------------------------------
_fonts_loaded = False


def load_fonts() -> None:
    """Register the bundled variable TTFs (Inter, JetBrains Mono). Idempotent;
    silently no-ops if the files are missing (QSS falls back to Segoe/Consolas)."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    try:
        from PySide6 import QtGui
        from .. import paths
        fdir = paths.assets_dir() / "fonts"
        for ttf in ("Inter-Variable.ttf", "JetBrainsMono-Variable.ttf"):
            p = fdir / ttf
            if p.exists():
                QtGui.QFontDatabase.addApplicationFont(str(p))
    except Exception:
        pass


# --- stylesheet -----------------------------------------------------------
# Sharp everywhere: border-radius: 0. Structural depth via tonal layering + 1px
# borders (no shadows, no rounding).
def _build_qss() -> str:
    return f"""
* {{
    font-family: "{UI_FONT}", "Segoe UI", sans-serif;
    font-size: 14px;
    color: {TEXT};
    outline: 0;
    border-radius: 0;
}}
QWidget {{ background: {SURFACE}; }}
QMainWindow, QDialog {{ background: {SURFACE}; }}
/* labels are transparent so they don't paint an opaque rect over cards/bars;
   tag-style labels (RarityTag, LIVE/OFFLINE PREDICTOR) set their own bg and win */
QLabel {{ background: transparent; }}

/* surfaces */
QFrame#Card {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QFrame#Cell {{ background: {PANEL_LOW}; border: 1px solid {BORDER}; }}
QFrame#Panel {{ background: {SURFACE}; border: 0; }}
QFrame#Sidebar {{ background: {PANEL_LOW}; border: 0; border-right: 1px solid {BORDER}; }}
QFrame#TopBar {{ background: {PANEL}; border: 0; border-bottom: 1px solid {BORDER}; }}
QFrame#StatusStrip {{ background: {LOWEST}; border: 0; border-top: 1px solid {BORDER}; }}
QFrame#TitleBar {{ background: {PANEL_HI}; border: 0; border-bottom: 2px solid {ACCENT}; }}
QFrame#Hairline {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: 0; }}

/* text roles */
QLabel#Brand {{ color: {ACCENT}; font-weight: 900; font-size: 24px;
                letter-spacing: -0.5px; }}
QLabel#Title {{ color: {TEXT}; font-weight: 700; font-size: 22px; }}
QLabel#H1 {{ color: {TEXT}; font-weight: 700; font-size: 28px; }}
QLabel#Section {{ color: {ACCENT}; font-weight: 700; font-size: 11px;
                  font-family: "{MONO_FONT}", "Consolas", monospace;
                  letter-spacing: 2px; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Mono {{ font-family: "{MONO_FONT}", "Consolas", monospace; font-size: 11px;
               color: {MUTED}; letter-spacing: 0.5px; }}
QLabel#MonoText {{ font-family: "{MONO_FONT}", "Consolas", monospace; font-size: 13px;
                   color: {TEXT}; }}
QLabel#Big {{ font-size: 38px; font-weight: 900; color: {DANGER}; }}
QLabel#FieldLabel {{ color: {MUTED}; font-weight: 500; font-size: 11px;
                     font-family: "{MONO_FONT}", "Consolas", monospace;
                     letter-spacing: 1px; }}

/* buttons */
QPushButton {{
    background: {PANEL_LOW}; border: 1px solid {BORDER};
    padding: 7px 14px; color: {TEXT}; min-height: 18px;
}}
QPushButton:hover {{ background: {PANEL_HI}; }}
QPushButton:pressed {{ background: {ACCENT_DIM}; }}
QPushButton:disabled {{ color: {DIM}; background: {SURFACE}; }}
QPushButton#Accent {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: {ON_ACCENT};
                      font-weight: 700; }}
QPushButton#Accent:hover {{ background: {ACCENT_LIGHT}; border-color: {ACCENT_LIGHT}; }}
QPushButton#Accent:disabled {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM};
                               color: {DIM}; }}
QPushButton#Outline {{ background: transparent; border: 1px solid {BORDER}; color: {MUTED}; }}
QPushButton#Outline:hover {{ background: {PANEL_HI}; color: {TEXT}; }}
QPushButton#Icon {{ background: transparent; border: 0; color: {MUTED};
                    padding: 2px 6px; min-height: 0; }}
QPushButton#Icon:hover {{ color: {ACCENT}; background: {PANEL_HI}; }}

/* inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER};
    padding: 7px 10px; color: {TEXT}; min-height: 18px;
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QComboBox:on {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{ width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {BG}; border: 1px solid {BORDER}; outline: 0;
    selection-background-color: {ACCENT_DIM}; selection-color: {ACCENT_LIGHT};
    color: {TEXT}; padding: 2px;
}}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 5px 10px; border: 0; }}
QComboBox QAbstractItemView::item:selected {{ background: {ACCENT_DIM}; color: {ACCENT_LIGHT}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 18px;
    background: {PANEL_LOW}; border-left: 1px solid {BORDER}; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 18px;
    background: {PANEL_LOW}; border-left: 1px solid {BORDER}; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {ACCENT_DIM}; }}

/* checkboxes (sharp) */
QCheckBox {{ spacing: 8px; padding: 2px 0; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER};
                        background: {BG}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* item views / tables */
QTreeView, QTableView, QListView {{
    background: {PANEL_LOW}; alternate-background-color: {SURFACE};
    border: 1px solid {BORDER}; gridline-color: {BORDER};
    selection-background-color: {ACCENT_DIM}; selection-color: {TEXT};
}}
QTreeView::item, QTableView::item {{ padding: 6px 2px; }}
QHeaderView::section {{
    background: {PANEL_HI}; color: {MUTED}; border: 0;
    border-bottom: 1px solid {BORDER}; padding: 8px 12px; font-weight: 500;
    font-family: "{MONO_FONT}", "Consolas", monospace; font-size: 11px;
    letter-spacing: 1px;
}}

/* mono log */
QPlainTextEdit {{ background: {PANEL_LOW}; border: 1px solid {BORDER};
                  font-family: "{MONO_FONT}", "Consolas", monospace; font-size: 12px;
                  color: {MUTED}; padding: 8px; selection-background-color: {ACCENT_DIM}; }}

/* scrollbars */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {PANEL_HI}; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {PANEL_HI}; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {DIM}; }}

/* sliders (sharp filled portion) */
QSlider::groove:horizontal {{ background: {HIGHEST}; height: 4px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; height: 12px;
                              margin: -5px 0; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; height: 4px; }}

QToolTip {{ background: {PANEL_HI}; color: {TEXT}; border: 1px solid {BORDER};
            padding: 4px 6px; }}

/* dropdown menus (account button, context menus) — sharp, dark, accent hover */
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ background: transparent; color: {TEXT}; padding: 7px 18px 7px 14px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; color: {ACCENT_LIGHT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 2px; }}
"""


QSS = _build_qss()


def set_accent(color: str) -> None:
    """Switch the app-wide accent ("Highlight color") to `color`, deriving the
    light/dim shades and rebuilding `QSS`. The caller must then re-apply
    `QSS` (app.setStyleSheet) and refresh painted widgets, see
    ControlPanel._set_accent. Passing the default restores the design cyan."""
    global ACCENT, ACCENT_LIGHT, ACCENT_DIM, ON_ACCENT, TOGGLE_THUMB_ON, QSS
    if not color:
        return
    if color.lower() == _DESIGN_ACCENT[0]:
        ACCENT, ACCENT_LIGHT, ACCENT_DIM, ON_ACCENT, TOGGLE_THUMB_ON = _DESIGN_ACCENT
    else:
        ACCENT = color
        ACCENT_LIGHT = _shade(color, 1.4)
        ACCENT_DIM = _shade(color, 0.34)         # dark accent tint (selection bg, toggle track-off)
        ON_ACCENT = _shade(color, 0.30)          # text on a bright accent button
        TOGGLE_THUMB_ON = _shade(color, 0.26)    # the square thumb on an active (accent) toggle
    QSS = _build_qss()


def _shade(hex_color: str, factor: float) -> str:
    """Lighten (factor>1, toward white) or darken (factor<1, toward black) a
    `#rrggbb`. Used to derive the light/dim accent shades from a custom accent."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if factor >= 1.0:
        t = min(factor - 1.0, 1.0)
        r, g, b = (round(c + (255 - c) * t) for c in (r, g, b))
    else:
        r, g, b = (round(c * factor) for c in (r, g, b))
    clamp = lambda v: max(0, min(255, v))
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def scaled_qss(scale: float) -> str:
    """Per-overlay widget stylesheet: the current `QSS` (already tinted to the
    active accent) with every `Npx` metric multiplied by `scale`, a uniform
    zoom so one HUD can scale without touching the others. Returns "" at scale
    1.0 so the overlay just inherits the (tinted) app QSS."""
    if abs(scale - 1.0) < 0.01:
        return ""
    import re
    return re.sub(r"(\d+)px",
                  lambda m: f"{max(1, round(int(m.group(1)) * scale))}px", QSS)


def apply(app) -> None:
    load_fonts()
    try:
        from PySide6 import QtGui
        app.setFont(QtGui.QFont(UI_FONT, 10))
    except Exception:
        pass
    app.setStyleSheet(QSS)
