"""PyInstaller / direct entry point."""
import sys

try:
    from farever_companion import paths
    icons_dir = paths.icons_dir()
    atlas_dir = paths.atlas_dir()
    
    has_icons = icons_dir.exists() and (icons_dir / "Items").exists() and (icons_dir / "Units").exists() and (icons_dir / "Skills").exists()
    has_atlas = atlas_dir.exists() and (atlas_dir / "atlas_map.json").exists()
    
    if not (has_icons or has_atlas):
        msg = (
            f"ERROR: missing atlas/icon folder\n\n"
            f"Icons folder: {icons_dir} ({'Found' if has_icons else 'Missing/Incomplete Items, Units, or Skills subfolders'})\n"
            f"Atlas folder: {atlas_dir} ({'Found' if has_atlas else 'Missing atlas_map.json'})"
        )
        raise RuntimeError(msg)

    from farever_companion.app import main
except Exception as e:
    import traceback
    tb = traceback.format_exc()
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        QtWidgets.QMessageBox.critical(
            None,
            "Startup Error",
            f"An error occurred during startup:\n\n{e}\n\nTraceback:\n{tb}"
        )
    except Exception:
        traceback.print_exc()
    sys.exit(1)

if __name__ == "__main__":
    sys.exit(main())
