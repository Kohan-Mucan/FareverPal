"""Reads and parses in-game announcements (Rifts)."""
from __future__ import annotations
import re
import time
import logging
from .hl import Hl, is_ptr
from .proc import Proc
from ..constants import (
    OFF_HERO_OWNERPLAYER, OFF_PLAYER_SYSTEM_CLIENT, OFF_SYSTEM_CLIENT_HISTORY, OFF_SYSTEM_MESSAGE_BRUTE_SCAN
)


log = logging.getLogger(__name__)

class Announcement:
    def __init__(self, zone_id: str, time_str: str, is_open: bool = False):
        self.zone_id = zone_id
        self.time_str = time_str
        self.is_open = is_open
        self.received_at = time.time()

class AnnouncementReader:
    def __init__(self, proc: Proc, hl: Hl, locator):
        self.proc = proc
        self.hl = hl
        self.locator = locator
        self.seen_ptrs = set()
        self.last_announcement: Announcement | None = None
        self._last_poll = 0.0

    def clear(self):
        """Explicitly clear the cached announcement (called when rift is entered or closed)."""
        self.last_announcement = None

    def _brute_read_string(self, addr: int, depth: int = 0) -> str | None:
        if not is_ptr(addr) or depth > 2:
            return None
        try:
            cls = self.hl.class_of(addr)
            if cls == "String":
                return self.hl.hl_string(addr)
            if cls and ("Player" in cls or "User" in cls or "Hero" in cls):
                return f"PLAYER:{cls}"
        except: pass
        
        if depth == 0:
            try:
                val = self.proc.u64(addr)
                return self._brute_read_string(val, depth + 1)
            except: pass
        return None

    def update(self) -> Announcement | None:
        """Poll for new system announcements (throttled to 2Hz)."""
        now = time.time()
        if now - self._last_poll < 0.5:
            return self.last_announcement
        self._last_poll = now

        try:
            me = self.locator.locate()
            if not me:
                print("[DEBUG AnnouncementReader] locate() returned None - player not located")
                return self.last_announcement

            # `me` is an ent.Hero pointer. ownerPlayer points to the st.Player object
            me_tp = self.hl.ptr(me)
            off_owner = self.hl.field_offset(me_tp, "ownerPlayer")
            if off_owner is None: off_owner = OFF_HERO_OWNERPLAYER
            
            player_ptr = self.hl.ptr(me + off_owner)
            if not player_ptr:
                print(f"[DEBUG AnnouncementReader] player_ptr (st.Player) is NULL at me(0x{me:X}) + off_owner(0x{off_owner:X})")
                return self.last_announcement

            player_tp = self.hl.ptr(player_ptr)
            off_client = self.hl.field_offset(player_tp, "chatClient")
            if off_client is None: off_client = OFF_PLAYER_SYSTEM_CLIENT
            
            client_ptr = self.hl.ptr(player_ptr + off_client)
            if not client_ptr:
                print(f"[DEBUG AnnouncementReader] client_ptr is NULL at st.Player(0x{player_ptr:X}) + off_client(0x{off_client:X})")
                return self.last_announcement
            
            client_tp = self.hl.ptr(client_ptr)
            off_history = self.hl.field_offset(client_tp, "history")
            if off_history is None: off_history = OFF_SYSTEM_CLIENT_HISTORY
            
            history_arr_ptr = self.hl.ptr(client_ptr + off_history)

            if not history_arr_ptr:
                print(f"[DEBUG AnnouncementReader] history_arr_ptr is NULL at client(0x{client_ptr:X}) + off_history(0x{off_history:X})")
                return self.last_announcement
            
            msg_ptrs = self.hl.array(history_arr_ptr, max_elems=100)
            if not msg_ptrs:
                return self.last_announcement
            
            # Use same brute-force approach as the successful script
            for ptr in msg_ptrs:
                if not ptr or ptr in self.seen_ptrs:
                    continue
                
                found_strings = {}
                has_player_ptr = False
                for off in OFF_SYSTEM_MESSAGE_BRUTE_SCAN:
                    try:
                        val = self.proc.u64(ptr + off)
                        if is_ptr(val):
                            s = self._brute_read_string(val)
                            if s:
                                found_strings[off] = s
                                if s.startswith("PLAYER:"):
                                    has_player_ptr = True
                    except: pass
                
                if not has_player_ptr:
                    for off, logic_str in found_strings.items():
                        if "rift" in logic_str.lower():
                            self._parse_announcement(logic_str)
                            log.info(f"System Announcement Parsed: {logic_str}")
                            break

                self.seen_ptrs.add(ptr)

            if len(self.seen_ptrs) > 500:
                current = list(self.seen_ptrs)
                self.seen_ptrs = set(current[-250:])
                
        except Exception as e:
            pass

        if self.last_announcement and (time.time() - self.last_announcement.received_at > 1200):
            self.last_announcement = None
            
        return self.last_announcement

    def _parse_announcement(self, s: str):
        s_low = s.lower()
        if "closed" in s_low:
            self.last_announcement = None
            return

        # 1. Find the Zone ID in brackets (e.g., [Z2_Krisomal_North] or [z1_enripit_falls])
        match = re.search(r'\[([^\]]+)\]', s)
        if not match:
            return
        
        zid = match.group(1).strip()
        
        # 2. Try to find a time if it exists (e.g., 15:00 or 15m)
        time_match = re.search(r'(\d{1,2}:\d{2})', s)
        time_str = time_match.group(1) if time_match else ""
        
        # 3. Detect if it's already open vs opening soon
        is_open = "has opened" in s_low or "opened" in s_low or "active" in s_low
        if is_open:
            time_str = "LIVE"
            
        self.last_announcement = Announcement(zid, time_str, is_open)