"""User-data backup: rolling snapshots, loss detection and recovery.

`master` owns master_backup.json (the snapshots, the loss check and the restores)
and `summary` renders the one-line description each snapshot gets in the
recovery dialog. Neither reads the game and neither is Qt, so the UI imports
them, never the other way round.
"""
