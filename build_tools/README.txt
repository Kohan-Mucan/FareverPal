Farever Companion
=================

A lightweight, read-only companion overlay for the Steam game Farever:
a minimap, loot/drop predictions, an entity overlay, and a DPS meter.

This is a fan-made tool. It is NOT affiliated with or endorsed by the
developers. It only READS game memory (it never writes to or modifies the
game) and ships no game assets beyond small UI icons.


How to run
----------
1. Start Farever.
2. Double-click  FareverCompanion.exe
   (Windows may show a SmartScreen warning because the app is unsigned —
    choose "More info" > "Run anyway".)
3. In the Companion window, click "Attach" to connect to the running game.
4. Open the overlays you want (Minimap, Entity, Loot, Combat) from the panel.

No installation, no Python, no dependencies — everything is bundled in the
single .exe. First launch may take a few extra seconds while it unpacks.


Notes
-----
- If "Attach" fails, make sure Farever is actually running, and try launching
  the Companion as the same user (no need for administrator in most cases).
- The minimap needs your live player position, so markers only populate once
  attached and in-game.
- Settings and "marked done" POIs are saved between runs.

Found a bug or want a feature? Let us know.
