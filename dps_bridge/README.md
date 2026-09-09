# DPS Bridge (TL;DR)

High-performance native combat bridge for FareverPal. Intercepts combat events directly from the HashLink runtime and streams them live to the companion meter.

---

### Native build outputs and tools

This directory contains the active bridge binaries plus build/diagnostic
helpers. The native build keeps outputs beside their C sources under
`GameFiles/Farever/hooks/hook/` and publishes the active set **here, and only
here** — this folder is the single destination:

- `farever_dps.dll` and `farever_dps.exe` — `hook/farever_dps/`
- `dinput8.dll` — `hook/proxy dinput8/`
- `userenv.dll` — `hook/proxy versions/`

`version.dll` is retired. Its source remains in the hooks tree for reference,
but it is not built, copied, packaged, or offered by the app. The
`monitor_bridge.bat` / `monitor_bridge.py` helpers remain available for live UDP
monitoring.

### Running the app and the monitor at the same time

The DLL sends to one unicast destination (`127.0.0.1:49152`), so exactly one
process can receive it — a second listener on that port wins or loses by bind
order, and the loser reads as a dead DLL. So the app keeps the port and pushes a
verbatim copy of every datagram to a loopback **TCP relay on 49153**, where any
number of subscribers can read it. The Top DPS meter never notices.

| Situation | How the monitor behaves |
|---|---|
| App running (relay on) | attaches to `127.0.0.1:49153`; meter stays live |
| App running, relay off | says so, then reports who holds 49152 |
| App not running | falls back to binding 49152 directly |

The relay is on automatically for source-tree runs and **off in a frozen build**
unless `FAREVER_BRIDGE_RELAY=1`, so the shipped app never opens a listening
socket nobody asked for. Force a transport with `--relay` / `--direct`.

The relay socket is bound exclusively, for the same reason as the capture
socket: on Windows `SO_REUSEADDR` would let a second FareverPal instance bind
49153 too, and subscribers would silently attach to whichever won. A dropped
subscription is routine — the app restarts capture whenever the capture mode
changes — so the monitor reconnects instead of exiting.

The capture socket itself deliberately never sets `SO_REUSEADDR` and never
falls back to a wildcard bind: a lost port must be a loud bind failure naming the
owner, not a silent zero-packet wait.

A second destination (`dist/dps_bridge/`) used to receive a copy of the same four
binaries on every build. Nothing ever selected it: a frozen app reads the copy
`FareverPal.spec` bundles into `_MEIPASS/dps_bridge/`, and a source-tree run finds
**this** folder first in the launch list
(`ui/game_attach._hook_launch_candidates`). The build no longer writes it, so a
`dist/dps_bridge/` left over from an older build is stale — do not ship it.

`farever_dps.exe` is likewise the only helper binary the app runs, and the only
one shipped. The build used to publish a second copy as `farever_uninject.exe`,
because `injector.c` chose inject-vs-uninject from its own filename — so the
same bytes under a different name were a different program, and a file named for
uninjecting, sitting beside the injector, was only ever a way to hand the launch
list the wrong binary. Both halves of that are gone: `injector.c` no longer
reads its own filename (a mode needs an explicit `--uninject` argument and
nothing else), and the copy is no longer built, copied, or packaged. Removal
runs through the crash-safe Python path (`core.proc.safe_uninject_hook`, calling
the DLL's `SafeUnload` export). The dev hot-uninject scripts are unaffected —
`uninject_hot.bat` invokes `farever_dps.exe --uninject` from the hooks source
folder.


### Embedded identity

Every native proxy/injector exports one marker with three independent values:

- `FAREVERMOD_PROXY_VERSION` — semantic FareverMod release, generated from `farever_companion.__version__` at build time
- `FAREVERMOD_PROXY_ABI` — native bridge interface version
- `FAREVERMOD_PROXY_HLBOOT` — SHA-256 of the `hlboot.dat` the binary targets

The first two can change without a game patch; the third changes with the game
build. Option 1 shows the installed values and treats any difference as an update,
not just an HLBOOT difference. The native `build_all.bat` reads
`farever_companion\__init__.py` directly and writes the generated C header; there
is no separate version source or generator to keep in sync.

---

### Proxy name table (measured on the installed game, not theory)

A proxy only ever loads if some module in the game folder imports that name.
What each candidate name costs and when it lands:

| name | fns to forward | imported by | load timing | status |
|---|---|---|---|---|
| **dinput8.dll** | 1 | `directx.hdll` | just after boot (runtime class) | **recommended — verified streaming** |
| **userenv.dll** | 1 | `uv.hdll` | just after boot (runtime class) | native build available — verified to load; streaming not yet re-verified |
| version.dll | 6 | `sdl3.dll`, `nvngx_dlss*.dll`, `sl.*.dll` | optional importers, may never load; a System32 copy of the name loaded first wins for the session | **retired** — not built, copied, or shipped (2026-09-24) |
| ~~iphlpapi.dll~~ | 3 | `uv.hdll` | just after boot (runtime class) | **removed** — duplicate of the userenv slot; one alt is enough |
| ~~winmm.dll~~ | 20 | `libhl.dll`, `sdl3.dll`, `fmod*.dll`, `openal32.dll`, `sdl.hdll`, `sl.dlss_g.dll` | **t=0 — during import resolution** | **retired** — killed the game even with its worker asleep |
| opengl32.dll / vulkan-1.dll | 32 / 26 | `sdl.hdll` / `sl.nvperf` | runtime | avoid — heavy loaders |
| d3d11/d3d12/dxgi | 1–3 | `directx.hdll`, `dx12.hdll`, `sl.common` | runtime | never — forwarding a graphics API corrupts rendering |
| dbghelp.dll | 3 | `sl.interposer`, `uv.hdll` | runtime | avoid — stateful API |
| d3dcompiler_47.dll | 2 | `directx.hdll` | runtime | avoid — shader compilation |
| vcruntime140 / msvcp140 / vcruntime140_1 | 28 / 143 / 1 | `Farever.exe` + everything | first | never — the CRT itself |

The boot closure (`Farever.exe`'s static imports, followed transitively through
game-folder modules) is exactly `{farever.exe, libhl.dll, vcruntime140.dll}` —
so winmm was the only t=0 name this game loads, and it is gone. Every remaining
shipped name attaches after the runtime is up, which is the condition the
verified dinput8 build proved safe.

---

### Proxy boot behaviour (Option 1, `dinput8.dll` / `userenv.dll`; `version.dll` parked)

A proxy is loaded by Windows **while the loader lock is held**, at the very start
of the process. Two rules follow, and both are load-bearing:

1. **`DllMain` does almost nothing.** It records the module handle, pre-resolves
the real system DLL it forwards to (`LoadLibraryW` on the loader thread, where
Windows would have loaded it itself), and starts the worker. No log file, no
winsock, no hunt in `DllMain`.
2. **The worker waits for the loader to be idle** before it opens its socket,
writes the log, or hunts — a thread created inside `DllMain` doing loader work
while the creating thread still holds the loader lock is what crashed the game
inside `libhl.dll` (identical access violation across three builds). The probe
is `LdrLockLoaderLock(TRY_ONLY)`: never blocking, and it must read *free* on
consecutive polls (`~50 ms`) before the wait ends, so a sample taken between two
module loads cannot pass for idle. Bounded by
`FAREVERMOD_PROXY_LOADER_WAIT_MS` (default 10 s); after that it proceeds and the
log says `probe=TIMED OUT`.

**Arming needs no wait at all.** The hooks cannot be armed before the HashLink
runtime has published `functions_ptrs`, and the hunt already retries until it
does (30 s budget, 250 ms cycle) — so that *is* the readiness signal, and it is
evidence rather than a guess: a slow or cold boot is covered, which a fixed
sleep never was. The hunt re-checks the loader every pass and drops a pass that
overlaps a module load, so the heap is never walked while the loader is mutating
it.

`PROXY_BOOT_GRACE_MS` is still honoured, now as a **floor** on the total wait and
`0` by default. Two ways to set it, both good for a bisect with no rebuild:

| | |
|---|---|
| `dinput8_grace_ms.txt` next to the DLL | contains one integer (ms). Read first — a file is the only lever that works when the companion launches the game through Steam, since a shell's environment never reaches it. Empty file = `0`. |
| `FAREVERMOD_PROXY_BOOT_GRACE_MS` | same value from the game's environment |

Setting it to `4000` reproduces the old fixed behaviour exactly.

**Logging is dev-only (2026-09-24).** No bridge binary writes a log file unless
a flag file named `FAREVERMOD_DEBUG_LOG` sits next to the DLL, and its first
line names the destination. A normal user therefore never gets a log anywhere —
not in the Steam folder, not in `%APPDATA%`.

```
<dps_bridge>\FAREVERMOD_DEBUG_LOG      # beside the DLL the binary loaded from
  -> first line: D:\...\FareverPal\dps_bridge\bridge_dev.log
```

The flag is re-read on every log call, so it can appear or disappear
mid-session. Only `run-log.bat` makes the app write it: that launcher sets
`FAREVER_DEV_CRASH_LOG`, and the attach / injector-launch / proxy-install
paths write the flag in a dev run and *delete* a leftover one otherwise — so
a normal `run.bat` session never creates one anywhere. (A hand-written flag
with any path in it works the same; the Live Diagnostics reader reads the
same flag, so it follows.)

Older builds wrote `<stem>_proxy.log` / `farever_dps.log` straight into the game
folder. That path is still *read* (and its tail snapshotted) so an un-updated
DLL keeps explaining itself, but nothing creates it any more.

**The build log is a different file.** `build_all.bat` in this folder writes
`<dps_bridge>\bridge_build.log`: the full native build transcript — every step
and every gcc diagnostic, for a good build and a failed one alike — written on
every run and named in the `[FAIL]` banner when a build does not finish. It
belongs to whoever ran the build. The name is deliberately NOT
`bridge_dev.log`: `run-log.bat` points the dev logging above at this folder,
so `bridge_dev.log` is the live log the bridge binaries append to while the
game runs, and a build must never truncate it. The native builder also exits
non-zero when a compile fails, which is what lets this script tell a green run
from a red one; it used to print `[FAIL]` and still exit 0.

**A rebuild cannot republish a DLL the game has loaded.** Windows lets a build
overwrite a binary only while no process has it mapped, and an injected
`farever_dps.dll` stays mapped for as long as `Farever.exe` runs — closing
FareverPal is a detach, not an unload. So a rebuild during play used to compile
the new DLL, fail the `dps_bridge` copy five times, and leave the PREVIOUS build
in place — and because the app injects from this folder first, it kept injecting
that old binary while the log's `manifest build` line quietly named it. Nothing
said "you are testing code from an hour ago"; the damage just never showed up.

The build now runs a pre-flight (`:release_dest_dll` in the native
`build_all.bat`) before it compiles anything. `tasklist /m farever_dps.dll` asks
the loader whether the destination is mapped, and if it is, the
`farever_dps.exe` sitting beside it is asked to have the loaded DLL unload
itself through the safe-unload export — never a bare remote `FreeLibrary`, which
unmapped a live-hooked DLL and crashed the game. The game keeps running and the
companion re-injects on its next attach. When the safe unload is refused the
build stops before compiling binaries it could not publish, instead of ending
red after five retries over stale output.

**Publish is inline, and judged by its outcome.** Publishing each binary used to
be a `call :copy_one`, and cmd.exe intermittently reports `cannot find the batch
label specified - copy_one` for one of those four call sites — which *skips* the
copy without setting `BUILD_FAILED`, so the run ends green over a destination
still holding the previous binary. That is a second, independent way to get a
stale binary, with the game closed. The native script therefore has no
`call :label` and no `goto` left at all: each publish is an inline block that
copies, checks the bytes with `fc /b`, retries five times and sets
`BUILD_FAILED` when it cannot land. `[5/5]` then re-checks the outcome — every
destination file compared against the source it came from — so a skipped or
half-done publish ends the run red instead of quietly shipping the old binary.

**Cleanup.** Live Diagnostics quotes the active mode's own log, so a file left
by a mode we have left is worse than no file — it explains the wrong session.
The companion's game-folder cleanup therefore treats our byproducts by what they
do to the *next* session:

| file | removed when |
|---|---|
| `<stem>_proxy.log` | leaving proxy mode, or when that proxy is no longer installed (the leftover case) |
| `<stem>_forward_only.flag`, `<stem>_grace_ms.txt`, `<stem>_go.flag` | only when the proxies are removed — they are deliberate diagnostics, so a detach leaves them for the next launch (the go flag is additionally cleared on every attach, so a stale one cannot arm a later launch early) |
| `FAREVERMOD_DEBUG_LOG` | only when the proxies are removed — same reason, and it is what turns file logging on at all |
| `farever_dps.log` | on every cleanup (the user's rule: no logs of ours belong in the game folder) |

A proxy that stays installed keeps its own log across a detach, so the tail of
the session that just ended is still there to read — while a leftover log from
the mode you switched away from is gone.

**Nothing is lost when a log is deleted.** Every log removed that way has its
tail kept in memory first (`core/bridge_log.snapshot_tail`), and Live Diagnostics
still shows it — labelled `kept after cleanup`, with the file's real age, so it
is never mistaken for a live file. Without that, a crash would prompt exactly the
cleanup that destroys the evidence for it: the reason a loaded-but-silent bridge
was silent lives only in that log. The kept tail lives in the running app
(memory-only), so an app restart clears it.

**Forward-only diagnostics.** Set `FAREVERMOD_PROXY_FORWARD_ONLY=1`, or drop an
empty file named `<stem>_forward_only.flag` (e.g. `dinput8_forward_only.flag`)
next to the DLL, and the proxy becomes a pure forwarder: no sockets, no hunt, no
hooks. That answers "did our worker thread break the game, or the forwarded
calls?" in one launch, with no rebuild. The first line of the proxy log says
which mode it is in.

**The GO handshake (retired with winmm).** The t=0 proxy used to hold its bridge
work until the companion wrote `<stem>_go.flag` at the player-located moment —
the mechanism, its bounds and its stale-flag clearing were winmm-only
(`#ifdef WINMM_PROXY`), so retiring winmm retired the gate. The C machinery and
the app's `signal_proxy_go` remain in place, inert (`PROXY_GO_ENABLED` is
empty), so a future t=0 name can be gated the same way without re-inventing it.
The 18:02 crash that motivated it — a game thread faulting inside `libhl.dll`
while winmm's worker walked module statics 1.5 s into boot — is documented here
because it is the reason no boot-position proxy is shipped.

---

### Proxy export lists are generated, never hand-written

Windows fails an **entire** import on the first name the proxy does not export,
and the module that asked for it dies with the game — that is exactly how the
first shipped `winmm.dll` killed Farever at `fmod.$Api.set_DEBUG_FLAGS`
(`fmod.dll` imports `WINMM.timeGetTime`). So the export lists are not
maintained by hand: `build_all.bat` runs `hooks/gen_proxy_exports.py` first,
which reads the **installed game's** import tables (`Farever.exe` + every
dependency next to it, including the `.hdll` HashLink libraries) and writes
`version.def` / `dinput8.def` / `userenv.def` plus a generated
forwarder header for anything the C sources do not implement yet.

A patch that starts importing one more symbol therefore only needs a rebuild —
no crash, no code change:

| | |
|---|---|
| game folder missing | prints a warning, keeps the lists already on disk |
| name already exported | unchanged (the list is the game's imports **unioned** with the existing exports) |
| name the sources implement | keeps its hand-written, precisely typed forwarder |
| name nothing implements | gets a generated forwarder, exported through a `.def` alias |

**One bridge core, thin per-name sources.** Each of `dinput8.dll`, `version.dll`
and `userenv.dll` still needs its own translation unit - its identity defines
(`PROXY_DLL_NAME` / `PROXY_STEM` / `PROXY_LOG_NAME` / `PROXY_REAL_DLL`), its
hand-written forwarders and its `.def` - because that is what Windows matches an
import against. Everything behind the forwarders is one file,
`hook/proxy_bridge.h`, included by each of them: the dev-opt-in logging, the UDP
event socket, the bounded findex hunt, the four MinHook detours and the worker
thread that arms them. It is a header of `static` functions for the same reason
`hlboot_verify.h` is - no build script has to change, and each DLL is still one
`gcc` invocation.

That is a fix, not a tidy-up. While the bridge was copy-pasted into two sources
it drifted, and the drift shipped:

| what diverged | what it cost |
|---|---|
| the real-DLL path literal | `version.dll` resolved `C:\Windows\system32version.dll` (no separator), so all 17 forwarded Version API calls returned their failure value and never reached the system DLL |
| how the four hooks are armed | `dinput8.dll` armed once with no retry, so a HashLink target the JIT had not compiled yet stayed unhooked for the session; the other copy retried for 30 s |
| the HashLink type-name read window | the copies required 32 vs 128 `wchar_t` of readable memory before matching `Hero` |
| incoming-hit attribution | the copies disagreed on whether a hit counts as a hero when only the skill's owner chain says so |

So put a bridge-layer fix in `hook/proxy_bridge.h`, never in a per-name source.
A per-name source that grows bridge code again is that bug coming back.

Two details worth knowing, both of which were bugs first:

- **Casing is copied from the game.** PE export lookup is case-sensitive, so a
generated `timegettime` would not satisfy an import of `timeGetTime` — the same
crash, produced by our own build.
- **Generated forwarders are defined under an internal name**
(`farevermod_fwd_<name>`) and attached to the public name by the `.def` alias,
because the SDK headers already declare prototypes for many of these names
(`mmsystem.h` has `timeGetSystemTime(LPMMTIME, UINT)`); a loose 12-slot
forwarder cannot redeclare those.

`gen_proxy_exports.py --check` exits non-zero if the files are stale (for CI),
and `--print` shows what would be written without touching anything.

---

### Hook-install status telemetry
At load, the bridge also sends one-shot JSON status lines over the same UDP port
(`{"t":"status","s":<stage>,"ok":0|1,"m":<text>}`) covering `boot`, `net`,
`table` (functions_ptrs locate), `mh_init`, one `hook` per intercepted method,
`mh_enable`, and `canary` (the per-frame self-test below). The companion logs
every line to the Activity Log and surfaces any failure in the DPS footer/status
line — so a loaded-but-silent bridge says WHY (e.g. table not found, MinHook
error) instead of just showing no damage.

The companion also *classifies* the capture from those same lines: it remembers
the method and table the `hook` lines name, and when the heartbeat's hit counter
first moves it writes one line naming the damage pipeline that produced the hit
— `ent.Unit.onInflictDamage (findex 4932, table 0)`. The game-side name comes
from `constants.HL_FINDEX_NAMES`; a findex the DLL arms that is unknown there is
never guessed at. This exists because classifying the pipeline by reading
`bridge_dev.log` by hand gave the wrong answer once (2026-09-30: a hook that
fired three times was recorded as silent).

The `canary` verdict survives the usual "a healthy `mh_enable` closes the
window" rule, because it is emitted *after* arming: hooks all saying OK is what
the canary exists to double-check.

### The per-frame canary (are the armed slots live code?)
`mh_enable` only reports that addresses were patched, and a wrong-but-plausible
table patches valid-looking functions the game never calls — which is exactly how
the 2026-09-30 stale-anchor session armed with full OKs and captured nothing. So
after the combat hooks, the bridge arms one more detour per candidate table on
`hxd.App.mainLoop` (`FINDEX_CANARY_MAIN_LOOP`), the function the runtime's own
frame loop (`hxd.$System.setLoop`) calls once per pass. It needs no combat, no
input and no game state: if that table is live code, the counter moves within one
frame while the player stands still.

The heartbeat loop speaks the verdict once, as a `canary` status line: ok=1 with
the first frame count (and again if a table that was silent starts firing — the
companion clears the earlier alarm, so a long load self-heals), or ok=0 when
**no** candidate table ever fired in ~20 s. That second message is the explicit
"armed but silent" verdict: the bridge is patched onto code the game does not
execute. A decoy copy staying silent while another table fires is expected and
is only logged, never reported.

Every heartbeat also carries the running frame count
(`{"t":"heartbeat","tick":N,"hooks":4,"canary":M,"hits":H}`), so the monitor
can watch the number grow without waiting for combat.

### The hit counter (did the damage capture ever see combat?)
The canary proves the armed table is live code; it cannot say whether the damage
hook ever produced an event, and an idle player looks from the outside exactly
like a dead hook. The 2026-09-30 session is why that gap is closed: the armed
`ent.Unit.onInflictDamage` hook fired three times in it (190 outgoing, then 12
and 25 incoming on the player), but its diagnostic line was named after the
method - `[diag] onInflictDamage #1 dealer=... dr=...` - while the watch probe's
lines were named `[diag] WATCH <type>.<method>`. A reader grepping the watch form
found nothing and recorded a working hook as silent, which is a reading this
counter makes impossible.

The count is incremented by the one hit-payload builder, so it counts hits
exactly - never a heal, never a kill - and it is permanent:

- the FIRST hit of a session writes one `[hits] first damage event sent (...)`
  line to the dev log, naming the hooked method that produced it;
- every heartbeat carries the running total as `"hits":H`.

A heartbeat with `canary` growing and `hits` at zero therefore means no damage
event reached the capture hook - a fact about the session, not an inference
about arming. A session with `hits` growing but no meter rows is a
companion-side problem instead.

---

### Event payload contract
One JSON object per line, sent over the same UDP port. The companion parses
every line in `core/dps_bridge.py::parse_hook_event`, which accepts both the
short keys the DLL emits and long aliases for hand-written payloads.

| key | source | meaning |
|---|---|---|
| `t` | all | `hit` \| `heal` \| `kill` (long alias `kind`) |
| `amt` | hit, heal | raw amount (`amount`, `landed`) |
| `landed` | heal | amount that actually landed; heals are credited on this |
| `sk` | hit, heal | skill id; a pet's skill arrives as `"<skill> (pet)"` |
| `src` / `tgt` | hit, heal | caster / target name (`player`/`caster`, `target`) |
| `taddr` | hit | **target unit's address** (`target_addr`), the raw pointer — the same address the companion's own scene scan holds. A name cannot tell one training dummy from another, so without it every hit on a three-dummy yard folds onto one `Dummy` split row (live 2026-10-03). Read as decimal; a `0x` prefix is accepted. Optional like `aff`: a bridge without the field degrades to name-keyed bucketing, never a dropped hit |
| `c` / `k` | hit | crit / kill (`crit`, `kill`) |
| `me` | hit, heal | this is the local player (`is_me`) |
| `inc` | hit | incoming (damage taken) rather than outgoing (`incoming`) |
| `p` | hit | pet tag; the parser folds it into `sk` when not already present |
| `aff` | hit | **per-hit damage type** (`affinity`), e.g. `Physical`, `Magic`, `Raw`, `Chaos`. Absent or empty is normal and means *untagged* — the companion never drops a hit over it |
| `uid` | kill | the killed unit's raw id — exactly what the game holds, so a prefab spawn's path and a pooled instance's `(Clone)` suffix are on the wire; the companion canonicalizes it (and the stored foe id) with `data/units.canonical_unit_id` before confirming a boss kill |
| `hits` | heartbeat | running count of hit events the damage capture has sent since load (`ent.Unit.onInflictDamage`); a growing number is continuous proof the capture is on the live path |

`aff` comes from `st.skill.DamageResult.affinity` (`OFF_DR_AFFINITY`, `0x38`),
the same field the Option 3 memory reader resolves by name. It is emitted only
when the string reads as ASCII letters, because a recycled floaty slot can hold
junk and a stray quote would make the whole line unparseable — which would lose
the event, not just the tag. The companion applies the same rule a second time
(`core/damage_events.py::clean_affinity`), so the vocabulary stays open: an
unrecognised school is a breakdown row, never a dropped hit.

Both native implementations emit `aff`: the injector DLL
(`hook/farever_dps/farever_dps.c`) and the drop-in proxies
(`hook/proxy_bridge.h`, shared by `dinput8.dll` and `userenv.dll`). They share
no code — nothing includes the other — so a field added to one is silently
missing from the other. Any payload change means rebuilding and checking all
three binaries; `tests/test_dps_capture.py` asserts every shipped binary still
carries it on both hit branches.

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
  (including `safe_unload.c` and `safe_uninject_client.h`: they are build
  inputs, so they belong with the source, not next to the native outputs they
  used to sit beside. Every build script reads them from there and **fails the
  build** if either is missing — a DLL built without `safe_unload.c` cannot be
  removed from a running game, and `injector.c` will not compile without the
  header. Native outputs stay in those source folders; nothing is copied into
  this app helper directory.)
- Method IDs are tracked in: `farever_companion/constants.py`
- The proxies' `.def` export lists are generated from the installed game's
  import tables at build time (`hooks/gen_proxy_exports.py`, run by
  `build_all.bat`) — see above.
- The **function-table anchors** (`ANCHOR_JPG_DECODE`, `ANCHOR_PNG_DECODE`,
  `ANCHOR_INFLATE_INIT`, `ANCHOR_INFLATE_BUF`) are generated too — the same
  script and the same run that resolves the hooked findexes
  (`hook/gen_findex_manifest.py` → `findex_manifest.h`, every build). They are
  the four `fmt.hdll` native indexes whose slots identify `functions_ptrs`, so
  they are positional exactly like the hooked findexes and drift the same way.
  `hlboot_verify.h` refuses to compile without them and `--check` fails on a
  stale set. They were hardcoded until 2026-09-30, when a game patch moved
  `fmt.jpg_decode` 34203 → 34743 and `fmt.inflate_init` 36225 → 36702: the
  statics walk stopped recognising the real table, the heap sweep derived its
  candidate from the stale index and "found" the table 540 entries too far
  along, and because `jpg`/`png` are adjacent — and moved by the same amount —
  two of four anchors still lined up, so every hook armed on the wrong
  functions.

  That failure mode is now impossible by construction rather than merely
  guarded: an anchor is a **predicate and nothing else**. The sweep does not
  compute a base at all — it measures one. `functions_ptrs` is a single
  allocation of `nfunctions + nnatives` slots in which *every* slot is a code
  pointer (`module.c` fills the function slots from the runtime's JIT block and
  the native slots from the hdll images), so its extent is a maximal run of
  executable pointers: from any slot inside such a run, walking out to both
  boundaries reads the base off the data. The generated array *length*
  (`FARVER_DPS_NFUNCTIONS + FARVER_DPS_NNATIVES`) is the only number involved,
  and a wrong length can only mean "not found". The slots are noticed in the
  first place by pointing into the runtime's own JIT block (or by equalling a
  live `fmt.hdll` export address — a `GetProcAddress` result, which cannot go
  stale); the anchors then answer exactly one question about the base: is this
  `functions_ptrs`? `is_candidate_table` requires **every** anchor to agree, and
  logs the best near-miss, so a stale set can only mean "table not found"
  (loud) instead of "armed" (silent). A session log tells the two routes apart:
  `Found functions_ptrs via heap scan (measured code-pointer run)` is the
  measured base, and `executable ranges: N (M runtime-owned, cached)` is the
  structural test being available at all. `hook_probe/anchor_audit.py` (launcher:
  `run_anchor_audit.bat`, mirrored in `dps_bridge/`) re-derives the four
  indexes from the `hlboot.dat` the game actually runs and reports
  header-vs-reality — a stale anchor is checkable on demand, not only when a
  build happens to run. It is written independently of the generator on
  purpose: a native rename or a generator bug then shows up as a disagreement
  instead of being confirmed by its own logic.
- The manifest describes **only what the bridge detours**: the hooked findexes,
  the canary and the four anchors. The **diagnostic watch candidates**
  (`FINDEX_WATCH_*`) lived here for a while and are gone again — the probe
  sessions of 2026-09-30 answered both questions (the damage path and the kill
  witnesses), no build ever armed one, and the header is what the DLL compiles,
  so a candidate in it was a function the shipping binary named and never
  hooked. The probe scripts that would visit those functions again live in
  `hook_probe/` with their own candidate list. What the generator still *fails
  the build* on is two names resolving to one findex (MinHook keeps one detour
  per target, so the second arm is refused), and
  `hook_probe/findex_audit.py` (launcher: `run_findex_audit.bat`, mirrored in
  `dps_bridge/`) re-derives every generated number from the live `hlboot.dat` —
  reporting a stale value, a duplicate proto, a drifted `_NAME` label or a
  number that no generator declared.
- **The build refuses a manifest that is not about the installed game.**
  `build_all.bat` regenerates the manifest and then, before the first `gcc`, runs
  `gen_findex_manifest.py --gate`: the header on disk must carry the
  `FARVER_DPS_HLBOOT_SHA256` of the *installed* game's `hlboot.dat`, or the
  build stops with nothing compiled and nothing published. Regeneration cannot
  be its own check — a build that skips it, renames it, or points it elsewhere
  compiles the committed header, and every findex in that header is positional,
  so a manifest from another patch is a bridge that arms the wrong functions.
  The gate is digest-only and never falls back to an archived copy: an archive
  resolves every findex perfectly well, so a manifest built from one passes
  every per-value audit above and still describes a build nobody is playing.
  With no game installed it warns and continues — the DLL re-hashes the running
  game at load and refuses to arm on a mismatch either way; the gate just means
  the build never produces a binary that would only find that out later. It is
  deliberately plain to re-run on its own: `gen_findex_manifest.py --gate`
  (exit 1 stale, 2 no game installed, 0 matches).
- **…and then the build proves it.** After the binaries are published and
  byte-verified, step `[6/6]` runs `hook_probe/verify_all.py` against the live
  `hlboot.dat`: one parse, then all three readers — the generator's `--check`
  plus `findex_audit.py` and `anchor_audit.py` — so the manifest is checked by
  something other than the code that
  wrote it. Regeneration can only be self-consistent — the generator writes the
  header, so the header agrees with the generator — and the gate above only
  compares digests; nothing until this point re-derived the *names* from the
  bytecode. A finding (exit 1) fails the build and states that the binaries just
  published came from an unverified manifest; the most likely cause is the audit
  list drifting from the generator rather than a wrong header. A check that
  could not run (exit 2) warns and calls the build UNVERIFIED instead of
  failing, so a machine with no game installed does not push anyone to switch
  the step off. Costs ~12s on every build, and ~35s for the build as a whole.
  The three readers share only the PARSE — each keeps its own name list,
  comparisons and exit code, and each still runs on its own, which is the part
  that makes a disagreement between them meaningful. A reader that raises is a
  finding, not a pass: as three separate processes a crashed audit exited with a
  code matching none of the three cases and the build went green over a check
  that had proved nothing.
- **`build_all.bat /noverify` skips that proof — for C-only builds.** The
  manifest cannot have moved when nothing about it changed, so the ~12 seconds
  buy nothing; a full build with the flag takes ~23s. It is opt-in in the
  strictest sense (one line in the script sets it, in the argument parser — no
  config, no env var), it **cannot** reach the `[0/6]` gate, and it announces
  itself three times: a boxed warning at the step that names what was not
  checked and prints the commands to check it by hand, one `SKIPPED` line per
  reader, and a closing line that is the last thing on screen. It still exits 0 —
  the binaries are fine — because a non-zero code would only teach people to
  discard the build's result, which loses real failures too. An unrecognised
  argument stops the build instead of being ignored, so `/noverifiy` cannot look
  like a flag that did nothing. Arguments are forwarded through this script, so
  `dps_bridge\build_all.bat /noverify` reaches the native builder.
- **Two discovery routes have to agree before anything is armed.** The hunt
  reaches `functions_ptrs` two independent ways — by following pointers out of
  module static data (the statics walk) and by measuring the shape of the heap
  (the sweep) — and neither can see the other's evidence, so a base both agree on
  exists for two unrelated reasons. A base only one of them can produce is *not*
  armed: a pointer-graph base is confirmed by measuring the heap at it, and a
  heap base is confirmed by asking the statics walk to reach that exact address.
  Refusals are loud and named (`REFUSING to arm on 0x…` / `NOT ARMING: … was not
  confirmed twice`) and reach the DPS footer, because "armed but silent" is
  precisely the failure this removes. Two consequences: a decoy duplicate table
  that passes the anchors but that the runtime's own pointer graph does not reach
  is no longer armed in collect mode, and a session where only one route can see
  the table now refuses instead of arming — `FARVERPAL_DPS_ALLOW_UNCORROBORATED=1`
  on the game process restores the one-route behaviour for a live A/B (logged
  when used). Both witnesses are the routes' own tests aimed at a known address,
  so the second costs milliseconds — a second full sweep on every launch would
  spend 25–30 s of the arming budget to learn the same thing.
- `FINDEX_CANARY_MAIN_LOOP` (`hxd.App.mainLoop`) comes from the same generated
  manifest. It is a findex like any other, so it drifts like any other — and it
  is what lets a session say "armed but silent" instead of only "no combat".
- You only need to run `build.bat` if a game update changes HashLink method IDs.
