"""Network plumbing for the Server diagnostics page.

Hardcoded region/host data, the Windows TCP-table scanner, DNS/GeoIP lookups
and flag pixmaps. Everything here is pure function/data — no widget state.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import socket
import struct
import urllib.request
from pathlib import Path

from PySide6 import QtCore, QtGui

from ....config import config_dir

# --- Master servers (hardcoded) --------------------------------------------
MASTER_HOSTS = {
    "global": [
        ("master.farever.net", 60442),
        ("master.farever.net", 60443),
    ],
    "cn": [
        ("mastercn.farever.net", 60442),
        ("mastercn.farever.net", 60443),
    ],
}

# Login gateways (hardcoded)
LOGIN_GATEWAYS = [
    ("37.187.157.32", 60442),
    ("51.83.66.84", 2003),
    ("51.178.64.48", 60442),
]

# Region game-server IPs (hardcoded)
CAPTURED_GAME_SERVERS = {
    "na": [("148.113.187.10", 6143), ("148.113.190.74", 6296)],
    "eu": [("51.178.197.236", 6262)],
    "as": [("15.235.203.32", 6224), ("51.79.143.102", 6523)],
    "sa": [("43.157.140.147", 6498)],
    "cn": [("82.156.18.93", 6128)],
}


def _default_hosts_for(code: str) -> list:
    """Built-in host list for a region card.

    login_global: masters + login gateways.
    cn:          mastercn + CN IPs.
    na/eu/as/sa: per-region game-server IPs.
    """
    if code == "login_global":
        return [*MASTER_HOSTS["global"], *LOGIN_GATEWAYS]
    if code == "cn":
        return [*MASTER_HOSTS["cn"], *CAPTURED_GAME_SERVERS.get("cn", [])]
    return [*CAPTURED_GAME_SERVERS.get(code, [])]


REGIONS = [
    # --- Main Game Servers ---
    {
        "code": "na",
        "label": "North America",
        "flag": "🇺🇸",
        "iso": "us",
        "icon": "broadcast",
        "hosts": _default_hosts_for("na"),
    },
    {
        "code": "eu",
        "label": "Europe",
        "flag": "🇪🇺",
        "iso": "eu",
        "icon": "broadcast",
        "hosts": _default_hosts_for("eu"),
    },
    {
        "code": "as",
        "label": "Asia",
        "flag": "🇸🇬",
        "iso": "sg",
        "icon": "broadcast",
        "hosts": _default_hosts_for("as"),
    },
    {
        "code": "sa",
        "label": "South America",
        "flag": "🇧🇷",
        "iso": "br",
        "icon": "broadcast",
        "hosts": _default_hosts_for("sa"),
    },
    {
        "code": "cn",
        "label": "China",
        "flag": "🇨🇳",
        "iso": "cn",
        "icon": "broadcast",
        "hosts": _default_hosts_for("cn"),
    },
    # --- Test Servers ---
    {
        "code": "na_test",
        "label": "NA Test",
        "flag": "⚒️ 🇺🇸",
        "iso": "us",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "eu_test",
        "label": "EU Test",
        "flag": "⚒️ 🇪🇺",
        "iso": "eu",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "as_test",
        "label": "Asia Test",
        "flag": "⚒️ 🇸🇬",
        "iso": "sg",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "sa_test",
        "label": "SA Test",
        "flag": "⚒️ 🇧🇷",
        "iso": "br",
        "icon": "broadcast",
        "hosts": [],
    },
    {
        "code": "cn_test",
        "label": "China Test",
        "flag": "⚒️ 🇨🇳",
        "iso": "cn",
        "icon": "broadcast",
        "hosts": [],
    },
    # --- Login Gateways ---
    {
        "code": "login_global",
        "label": "Global Login",
        "flag": "🌐",
        "iso": "fr",
        "icon": "terminal",
        "hosts": [*LOGIN_GATEWAYS],
    },
]

_STATUS_COLORS = {
    "ok": "#4ade80",
    "fail": "#f87171",
    "pending": "#94a3b8",
    "dns_fail": "#fb923c",
    "warn": "#fbbf24",
}

# IPs of the shared login gateways (control plane, not region game servers)
_LOGIN_GATEWAY_IPS = {h for h, _ in LOGIN_GATEWAYS}

# Master hosts are hostnames (the TCP table only shows their resolved IPs), so
# reverse-DNS `hostname` is matched against these. CN masters live under the
# China card in the ping panel; everything else is Global Login control plane.
_MASTER_HOST_LABELS = {
    "master.farever.net": "Global Login",
    "mastercn.farever.net": "China",
}


def _label_for_connection(ip: str, port: int = 0, hostname: str = "", custom_hosts: dict | None = None) -> str:
    """Best-effort region label for a captured connection.

    Matches every known source in priority order so the most specific one wins:
      1. per-region game IPs (CAPTURED_GAME_SERVERS) — exact (ip, port) first,
         then IP-only so new shards on a known host still resolve
      2. login gateways + master servers (masters match by reverse-DNS hostname)
      3. user-added IPs (server_custom_hosts: {code: [[ip, port], ...]})
    Returns "" when the IP is not in any hardcoded/user list.
    """
    # 1. Unique per-region game IPs — exact (ip, port) first, then IP-only
    #    fallback so a new shard on a known host still gets its region.
    ip_label = None
    for r in REGIONS:
        for h, hp in CAPTURED_GAME_SERVERS.get(r["code"], []):
            if h == ip:
                ip_label = r["label"]
                if port == hp:
                    return r["label"]
    if ip_label:
        return ip_label
    # 2. Login gateways and master hosts.
    host = (hostname or "").lower()
    master_label = _MASTER_HOST_LABELS.get(host)
    if not master_label and host.startswith("master") and host.endswith("farever.net"):
        master_label = "Global Login"  # unknown master-shaped control-plane host
    if ip in _LOGIN_GATEWAY_IPS:
        return "Global Login"
    if master_label:
        return master_label
    # 3. User-added IPs -> the region they were assigned to.
    for code, entries in (custom_hosts or {}).items():
        for entry in entries or []:
            if entry and entry[0] == ip:
                r = next((x for x in REGIONS if x["code"] == code), None)
                if r:
                    return r["label"]
    return ""

# --- Windows TCP table scanner ---------------------------------------------
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
PROCESS_NAME = "Farever.exe"

TCP_STATES = {
    1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD",
    5: "ESTABLISHED", 6: "FIN_WAIT_1", 7: "FIN_WAIT_2", 8: "CLOSE_WAIT",
    9: "CLOSING", 10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB",
}


def _get_farever_pid():
    try:
        TH32CS_SNAPPROCESS = 0x2

        class PE32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (0, None, -1):
            return None
        try:
            e = PE32W()
            e.dwSize = ctypes.sizeof(PE32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile.lower() == PROCESS_NAME.lower():
                    return int(e.th32ProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
    except Exception:
        pass
    return None


def _get_tcp_connections(pid: int) -> list:
    results = []
    try:
        iphlp = ctypes.windll.iphlpapi
        size = ctypes.wintypes.DWORD(0)
        iphlp.GetExtendedTcpTable(None, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        if ret != 0:
            return results
        offset = 0
        count = struct.unpack_from("<I", buf.raw, offset)[0]
        offset += 4
        for _ in range(count):
            if offset + 24 > len(buf.raw):
                break
            state, laddr, lport, raddr, rport, owner_pid = struct.unpack_from("<IIIIII", buf.raw, offset)
            offset += 24
            if owner_pid != pid or raddr == 0:
                continue
            rport_h = socket.ntohs(rport & 0xFFFF)
            remote_ip = socket.inet_ntoa(struct.pack("<I", raddr))
            results.append({
                "state": TCP_STATES.get(state, f"STATE{state}"),
                "remote_ip": remote_ip,
                "remote_port": rport_h,
                "remote": remote_ip + ":" + str(rport_h),
            })
    except Exception:
        pass
    return results


def _reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ip


_COUNTRY_CACHE = {}
FLAGS_DIR = Path(config_dir()) / "cache" / "flags"
FLAGS_DIR.mkdir(parents=True, exist_ok=True)


def _download_flag(code: str) -> Path | None:
    if not code or len(code) != 2:
        return None
    code = code.lower()
    path = FLAGS_DIR / f"{code}.png"
    if path.exists():
        return path
    try:
        # Using flagcdn.com for high quality small flags
        url = f"https://flagcdn.com/w40/{code}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "FareverPal/1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as response:
            path.write_bytes(response.read())
        return path
    except Exception:
        return None


def _get_ip_country(ip: str) -> tuple[str, str]:
    """Returns (country_name, iso_code)"""
    if ip in _COUNTRY_CACHE:
        return _COUNTRY_CACHE[ip]

    # Quick check for private/local IPs
    if ip.startswith(("127.", "192.168.", "10.", "172.16.")):
        return "LAN", "lan"

    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,country,countryCode"
        with urllib.request.urlopen(url, timeout=1.5) as response:
            data = json.loads(response.read().decode())
            if data.get("status") == "success":
                code = data.get("countryCode", "").lower()
                name = data.get("country", "Unknown")
                _download_flag(code)
                res = (name, code)
                _COUNTRY_CACHE[ip] = res
                return res
    except Exception:
        pass

    return "Unknown", ""


def _get_flag_pixmap(code: str, size: int = 24):
    if not code:
        return None
    p = FLAGS_DIR / f"{code.lower()}.png"
    if p.exists():
        pm = QtGui.QPixmap(str(p))
        if not pm.isNull():
            return pm.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    return None
