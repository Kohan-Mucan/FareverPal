# DPS Bridge (TL;DR)

High-performance native combat bridge for FareverPal. Intercepts combat events directly from the HashLink runtime and streams them live to the companion meter.

---

### Files in this Folder
- **`farever_dps.exe`**: Lightweight helper (< 5 ms). FareverPal runs this automatically in the background when the game starts. You never need to click it manually.
- **`uninject_hot.bat` / `farever_uninject.exe`**: Safely uninjects `farever_dps.dll` from the running game on demand without closing or restarting Farever.
- **`farever_dps.dll`**: The in-game bridge. Streams exact damage, heals, skills, pet hits, and crits over local UDP (`127.0.0.1:49152`).
- **`version.dll`**: Drop-in proxy (Option 1 Default). Windows auto-loads it when the game boots. Recommended default because it coexists safely alongside other mods (like minimaps) using `dinput8.dll`.
- **`dinput8.dll`**: Drop-in proxy alternative. Standard DirectInput proxy if you are not using other mods that occupy `dinput8.dll`.

---

### Hook-install status telemetry
At load, the bridge also sends one-shot JSON status lines over the same UDP port
(`{"t":"status","s":<stage>,"ok":0|1,"m":<text>}`) covering `boot`, `net`,
`table` (functions_ptrs locate), `mh_init`, one `hook` per intercepted method, and
`mh_enable`. The companion logs every line to the Activity Log and surfaces any
failure in the DPS footer/status line — so a loaded-but-silent bridge says WHY
(e.g. table not found, MinHook error) instead of just showing no damage.

---

### How to Use
1. **Start Farever** and launch **FareverPal** (`run.bat`).
2. FareverPal auto-attaches and displays:
   ```
   SOURCE: LIVE — combat bridge
   ```

---

### Source Code & Rebuilding
- C source and build scripts live in: `GameFiles/Farever/hooks/hook/`
- Method IDs are tracked in: `farever_companion/constants.py`
- You only need to run `build.bat` if a game update changes HashLink method IDs.
