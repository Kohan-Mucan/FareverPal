"""What happens when the window closes: save the run, save the log, stop.

Split out of `control_panel.py` because SHUTDOWN is its own concern and the
host file has an 840-line budget (`tests/test_ui_utils.py::
test_ui_file_under_line_budget`). Nothing here is window construction or a
page; it is the ordered teardown, and the order is the whole point - see
`ShutdownMixin.closeEvent`.

Every method here is best-effort and swallows its exceptions. Shutdown is the
one place in the app where a raise strands the window and takes the process
with it, so a failure to write a log must never be the reason the app will
not close.
"""
from __future__ import annotations

import datetime
import os

#: The Activity Log is trimmed past this size, and only at session boundaries
#: (see `ShutdownMixin._trim_activity_log`). 512 KB is roughly a hundred runs
#: of the busier logs, so a normal user never sees the trim happen.
ACTIVITY_LOG_MAX_BYTES = 512 * 1024
#: How many runs the file keeps once it IS over the cap.
ACTIVITY_LOG_KEEP_SESSIONS = 10


class ShutdownMixin:
    """`closeEvent` and the two things it has to save before it stops anything.

    Mixed into `ControlPanel`; it reaches `self` for the log buffer, the
    tracker, the drain worker and the detach controller, all of which the host
    owns.
    """

    def _flush_dps_on_close(self) -> None:
        """Persist the fight that was in progress when the app was closed.

        Called from `closeEvent` BEFORE the capture drain stops, because the
        moment the drain stops is the moment the last event is gone.

        Every persist trigger the tracker owns is a fight ENDING (a kill, a
        wipe, the boss engaging, a manual Reset), so a fight still running when
        the user quits had never reached disk: close the app mid-pull and that
        damage was simply gone when it came back up. `archive_current_encounter`
        picks the right session (dummy, then boss, then trash), deep-copies it,
        and skips a session with no damage - so this cannot duplicate a
        finished fight or invent one out of an idle meter.
        """
        try:
            tracker = getattr(getattr(self, "model", None), "dps", None)
            archive = getattr(tracker, "archive_current_encounter", None)
            if callable(archive):
                archive("App Closed")
        except Exception:
            pass

    def _write_activity_log_to_disk(self) -> None:
        """Append this session's Activity Log to ``moddata/activity_log.txt``.

        The log lives in a `deque` in RAM by design - a QWidget may only be
        written on the GUI thread and every capture engine logs from its own -
        so a normal quit used to end with nothing at all. The dev crash log
        already carries the same tail, but only when `FAREVER_DEV_CRASH_LOG` is
        set AND the process actually faults; a CLEAN exit left no trace of what
        the meter had been doing, which is exactly the run you want to look at
        afterwards.

        Appends rather than truncates, so the previous run's lines survive, and
        every session carries a header so one file holding several runs still
        reads as a sequence.
        """
        try:
            lines = list(getattr(self, "_log_buffer", ()) or ())
            if not lines:
                return
            from ..config import config_dir
            path = os.path.join(config_dir(), "activity_log.txt")
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"\n===== session {stamp} pid={os.getpid():08d} "
                         f"({len(lines)} lines) =====\n")
                fh.write("\n".join(lines))
                fh.write("\n")
            self._trim_activity_log(path)
        except Exception:
            pass

    @staticmethod
    def _trim_activity_log(path: str,
                           keep_sessions: int = ACTIVITY_LOG_KEEP_SESSIONS
                           ) -> None:
        """Keep the log bounded: drop whole sessions past `keep_sessions`.

        Trimming at a session boundary rather than by line count is what keeps
        the file readable - a run always begins with its `===== session`
        header, never mid-story. No-op while the file is under the cap, so the
        common case never touches the file twice.
        """
        try:
            if os.path.getsize(path) < ACTIVITY_LOG_MAX_BYTES:
                return
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            parts = text.split("\n===== session ")
            sessions = parts[1:]
            if len(sessions) <= keep_sessions:
                return
            # parts[0] is anything before the first header; it is normally
            # empty, and keeping it preserves a file that predates the format.
            trimmed = parts[0] + "\n===== session " + "===== session ".join(
                sessions[-keep_sessions:])
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(trimmed)
        except Exception:
            pass

    def closeEvent(self, e):
        """Ordered teardown. The ORDER is the contract, not the steps.

        1. A last log line, while the workers are still alive to have written
           the tail the dev crash log flushes.
        2. Archive the fight in progress - before the drain stops, because that
           is when the last event is gone. Labelled "App Closed" rather than a
           boss outcome: the fight did not conclude and the archive must not
           claim it did.
        3. The Activity Log to disk, for the same reason.
        4. Only then stop the drain, the periodic timers, the update workers
           and the server probe, and detach from the game.
        """
        try:
            self.log("shutdown")
        except Exception:
            pass
        self._flush_dps_on_close()
        self._write_activity_log_to_disk()
        try:
            self._stop_dps_drain()
        except Exception:
            pass
        # Close means SHUT DOWN (detach() below stops reading the game), so
        # every periodic driver goes quiet here too — the attach watcher that
        # otherwise keeps looking for the game, plus the memory/pre-warm
        # timers. See MemoryReclaimerMixin.stop_periodic_timers.
        self.stop_periodic_timers()
        for attr in ("_update_check_worker", "_update_dl_worker"):
            worker = getattr(self, attr, None)
            if worker is not None:
                try:
                    if hasattr(worker, "stop"):
                        worker.stop(800)
                    else:
                        worker.requestInterruption()
                        worker.quit()
                        worker.wait(800)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            self._server_cleanup()
        except Exception:
            pass
        self.detach()
        super().closeEvent(e)
