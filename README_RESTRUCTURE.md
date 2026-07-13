# FareverPal Restructure Plan

This plan separates **Game Engine (Memory)** from **Global App Settings**, and cleans up the **UI** folder.

## Phase 1: Constants & Offsets (The Foundation)
Goal: Stop mixing memory addresses with UI/Map settings.

| Action | Source File | Destination | Purpose |
| :--- | :--- | :--- | :--- |
| **New File** | - | `farever_companion/constants.py` | **App Globals:** Map calibration, Version, Feature flags. |
| **Rename** | `core/constants.py` | `core/offsets.py` | **Engine Offsets:** Memory addresses ONLY. |
| **Move Data** | `geo/nav.py` | `constants.py` | Move `CAM_YAW_SIGN` and `CAM_YAW_OFFSET` here. |
| **Move Data** | `core/offsets.py` | `constants.py` | Move `X_OFFSET`, `MAP_SCALE`, `MAP_BOUNDS` here. |

---

## Phase 2: UI Organization (The Tidy-up)
Goal: Separate full-screen "Pages" from HUD "Overlays".

### 1. New Folder: `farever_companion/ui/overlays/`
Move these HUD elements from `ui/` into the new folder:
*   `entity_overlay.py`
*   `dps_overlay.py`
*   `skill_overlay.py`
*   `speedrun_overlay.py`
*   `minimap.py`

### 2. Organize Pages
Move these config pages into the existing `ui/pages/` folder:
*   `friends_page.py`
*   `speedrun_page.py`

---

## Phase 3: What gets Updated (Imports)
Almost every file will need a minor import fix. Here are the big ones:

| File | Change |
| :--- | :--- |
| **`ui/overlay_manager.py`** | Update paths for the 6 overlays (e.g. `from .overlays.minimap import ...`). |
| **`ui/control_panel.py`** | Update paths for the new pages in `ui/pages/`. |
| **`ui/minimap.py`** | Change `from ..core import constants` to `from .. import constants` (for MAP_SCALE). |
| **`core/*.py`** | Change `from .constants import ...` to `from .offsets import ...`. |
| **`tests/*.py`** | Update all test imports to match the new structure. |

---

## Why do this?
1.  **3D Ready:** Having `constants.py` at the root makes it easy to add 3D projection math later without creating "Circular Import" crashes.
2.  **Stability:** You won't accidentally break the UI by changing a memory address, or vice-versa.
3.  **Readability:** It becomes obvious where code lives. If it's a floating HUD, it's in `overlays/`. If it's a settings screen, it's in `pages/`.

---
**Status:** Ready to execute once `ui/pages/entity.py` (Companions/Pets) is stable.
