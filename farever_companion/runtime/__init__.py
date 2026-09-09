"""OS and process plumbing shared by the app.

Atomic JSON writes (`persist`), the crash log / fatal-stamp writer (`crash_log`)
and alert-sound playback (`sound`). None of it knows game state and none of it is
Qt: the UI imports these, never the other way round.
"""
