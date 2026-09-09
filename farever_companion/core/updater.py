"""In-app updater, checks GitHub Releases (via the website's /api/release.php
oracle) and self-replaces the one-file exe.

Flow: the desktop app asks farever-pals.com what the latest release is (the site
already knows the GitHub repo from its Download config), compares versions, and -
if newer, downloads the GitHub asset, verifies it, and swaps itself on disk.

Windows wrinkle: a running .exe is locked and can't be overwritten, but it *can*
be renamed. So we rename the live exe to `*.old`, move the new one into place,
relaunch, and delete the leftover `*.old` on the next start.

Everything here degrades cleanly: a missing network, an old Python (running from
source rather than a frozen exe), or a checksum mismatch all abort without
touching the installed app. The pure helpers (version parsing/compare) are
unit-tested; the file-swap needs a real frozen exe and is exercised manually.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .. import __version__


@dataclass(frozen=True)
class UpdateInfo:
    version: str            # "0.1.2"
    url: str                # GitHub asset (zip) download URL - see
                            # `trusted_asset_url`; the download path refuses
                            # anything whose HOST is not on the trust list    notes: str = ""         # release body (markdown)
    html_url: str                    # release page (browser fallback)
    sha256: str                           # release binary hash — required so the
                                             # installed exe can be trusted; an
                                             # accepted-host asset is never installed
                                             # unverified.
    size: int | None = None             # file size for the download progress bar

#: Where to send the browser when no release URL is known (dev runs, or a
#: release payload without html_url).
DOWNLOAD_PAGE = "https://kohan-mucan.github.io/FareverPal/"


#: The website's release oracle: GET /api/release.php returns the newest release
#: the site's Download config points at (the site knows the GitHub repo, so the
#: app need not). Public and unauthenticated - the one endpoint the updater still
#: uses now the account feature is gone.
RELEASE_API = "https://farever-pals.com"


#: The startup update-check modes. The DISPLAY TEXT is the stored token, the
#: same convention `ui/pages/settings/rift.py` uses for `show_rift_timer`: a
#: settings.json stays readable and the reader only has to coerce what it cannot
#: recognise. Order is the order the picker draws.
UPDATE_CHECK_MODES = ("Always", "Packaged only", "Never")

#: What an unset, unreadable or future value means. Conservative on purpose: a
#: SOURCE run does not poll, so no settings file - and no test run - can reach
#: the network by accident.
DEFAULT_UPDATE_CHECK = "Packaged only"


def normalize_update_check(value) -> str:
    """Coerce a stored `check_updates` value to one of `UPDATE_CHECK_MODES`.

    Tolerant because the field USED to be a boolean: `True` meant "poll this
    source run too" (now `Always`) and `False` meant "only where a release can
    be installed" (now `Packaged only`), so a settings.json written before this
    change still loads into the mode it MEANT. Anything unrecognised - a
    hand-edited file, a typo, a mode a later version adds - falls back to
    `DEFAULT_UPDATE_CHECK` rather than guessing.
    """
    if value is True:
        return "Always"
    if value is False:
        return "Packaged only"
    text = str(value or "").strip().lower()
    if text == "always":
        return "Always"
    if text in ("packaged only", "packaged-only", "packaged", "frozen"):
        return "Packaged only"
    if text in ("never", "off"):
        return "Never"
    return DEFAULT_UPDATE_CHECK


def should_check(value, frozen: bool) -> bool:
    """Whether THIS run polls the release oracle at startup.

    `Always` polls even in a source run (how a dev sees the update pill without
    a build); `Packaged only` polls only where a downloaded release can actually
    be installed; `Never` is a real opt-out - including in a packaged build,
    which is the half that had NO switch at all before this became a mode.
    Junk reads as the default, which is what keeps an unset setting offline.
    """
    mode = normalize_update_check(value)
    if mode == "Always":
        return True
    if mode == "Never":
        return False
    return bool(frozen)


# --- pure version helpers (unit-tested) ------------------------------------
def parse_version(s: str) -> tuple[int, ...]:
    """"v0.1.2" / "0.1.2-beta" -> (0, 1, 2). Non-numeric junk is dropped; an
    empty/garbage string parses to (0,)."""
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    """True if `latest` is a strictly higher version than `current`."""
    return parse_version(latest) > parse_version(current)


def current_version() -> str:
    return __version__


#: A version the app is willing to PRINT. The sidebar pill draws this string
#: verbatim, so it has to look like a version and nothing else: digits with dots,
#: plus the `-rc1` / `+build` suffix a release tag may carry. Deliberately strict
#: - refusing a weird-but-real tag costs a missing pill, while accepting one
#: costs a button that reads "Update to vlatest" and asks the player to install
#: it.
_VERSION_RE = re.compile(r"^[vV]?\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.\-]+)?$")


def clean_version(raw) -> str:
    """The version to SHOW for a release payload, or "" when it is not one.

    The oracle is a web endpoint, so this is untrusted input, and "" is the
    caller's signal to show nothing at all - the only label that cannot mislead.
    A leading `v` is dropped because every surface that draws a version adds its
    own ("Update to v..."), so keeping the payload's would double it.
    """
    text = str(raw or "").strip()
    if not _VERSION_RE.match(text):
        return ""
    return text[1:] if text[:1] in ("v", "V") else text


def http_url(raw) -> str:
    """The URL to OPEN/FETCH for a payload field, or "" when it is not one.

    http/https, with a host, and nothing else: `webbrowser.open` on a
    `javascript:` or `file:` string is a local-file launch dressed as an update
    button, and a scheme-less "not a url" is a browser error page. Both are
    refused here, once, for every field that can reach a browser or urllib.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return text


#: The hosts an INSTALLABLE asset may come from. A FareverPal release is a
#: GitHub release download (`github.com/.../releases/download/...`) and GitHub
#: hands the bytes to a CDN host it owns - so one bare host and one domain
#: suffix, and nothing else.
#:
#: Deliberately NOT `RELEASE_API`'s own host: the asset URL arrives INSIDE the
#: oracle's response, so trusting the oracle here would let a bad release ROW
#: serve its own binary, which is the one thing this list exists to stop.
#:
#: What this does and does not buy: it stops a payload pointing anywhere else on
#: the internet (an attacker's own host, a `file:`-adjacent trick, a CDN of their
#: choosing). It does NOT pin the project - `github.com`, `objects.` and the rest
#: of the user-content family host EVERY GitHub user's files, so a payload that
#: can be written can still name someone else's asset. Pinning the repo path
#: (`/kohan-mucan/FareverPal/`) is the next tightening step, left out here
#: because the site owns the repo choice and a rename must not brick updates.
TRUSTED_ASSET_HOSTS = ("github.com",)

#: Subdomains of these count as the same host family. The WHOLE user-content
#: family, not three named CDN hostnames: GitHub has renamed the release-asset
#: host before (`objects.` -> `release-assets.`), and a refusal here means no
#: pill at all, so a rename would quietly stop every install from updating.
TRUSTED_ASSET_DOMAIN_SUFFIXES = (".githubusercontent.com",)


def trusted_asset_url(raw) -> str:
    """The asset URL we are willing to REPLACE THE RUNNING EXE for, or "".

    `http_url` answers "is this a URL"; this answers "is this OURS". The
    download path writes what it fetches over the installed exe and relaunches
    it, so the host is the last thing between a release and a payload that merely
    arrived in the release JSON - `https://evil.example/x.exe` is a perfectly
    good URL. A leading `v`-style trick cannot get past this either: the host is
    read off the parsed URL, lowercased, and matched exactly (or as a real
    subdomain, so `evil-githubusercontent.com` and `github.com.evil.example` are
    both out).
    """
    text = http_url(raw)
    if not text:
        return ""
    host = (urllib.parse.urlsplit(text).hostname or "").lower().rstrip(".")
    if host in TRUSTED_ASSET_HOSTS or host.endswith(
            TRUSTED_ASSET_DOMAIN_SUFFIXES):
        return text
    return ""


def page_url(html_url) -> str:
    """Where a click goes when the app cannot install itself (or the payload
    named no release page): the release page when there is a real one, else the
    project's download page. Never "" - the button always has somewhere to go.
    """
    return http_url(html_url) or DOWNLOAD_PAGE


# --- frozen-exe helpers ----------------------------------------------------
def is_frozen() -> bool:
    """True when running as the packaged one-file exe (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def exe_path() -> Path:
    """The on-disk exe (for a one-file build this is the real launcher, not the
    _MEIPASS temp dir)."""
    return Path(sys.executable)


def _old_path(cur: Path) -> Path:
    return cur.with_name(cur.name + ".old")


def cleanup_old() -> None:
    """Delete the `*.old` left by a previous self-update. Best-effort; called on
    startup once the new exe is the running one (so the old file is unlocked)."""
    if not is_frozen():
        return
    old = _old_path(exe_path())
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass            # still locked / in use - next launch will get it


# --- check -----------------------------------------------------------------
def fetch_latest_release(base_url: str = RELEASE_API) -> dict:
    """GET the release oracle and return its JSON dict.

    Blocking (stdlib urllib), so callers run it off the Qt thread. Never raises:
    no network, a non-JSON body or an HTTP error all come back as
    ``{"ok": False, "error": ...}`` - the shape `check` already handles.
    """
    url = base_url.rstrip("/") + "/api/release.php"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "FareverPal-Companion"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        return {"ok": False, "error": "network"}


def check(api=None) -> UpdateInfo | None:
    """Ask the release oracle for the latest version; return UpdateInfo if it's
    newer than us, else None. `api` is any object with a `latest_release()`
    returning the release dict; omit it to ask the real oracle
    (`fetch_latest_release`). Never raises.
    """
    try:
        res = api.latest_release() if api is not None else fetch_latest_release()
    except Exception:
        return None
    if not isinstance(res, dict) or not res.get("ok"):
        return None
    # Every field below is UNTRUSTED input: `version` is printed verbatim on the
    # sidebar pill and `url` is fetched, so a payload that is not a version or a
    # URL is refused here rather than rendered. (`parse_version` drops
    # non-digits by design, so "latest" and "9.9.9; DROP TABLE" both used to
    # compare as newer and reach the pill.)
    ver = clean_version(res.get("version"))
    # NOT `http_url`: a fetchable URL from a host we do not publish on is not an
    # update, it is a download we would refuse a moment later - so it reports
    # "nothing newer" here, and the pill never advertises an install that would
    # be turned down (see `trusted_asset_url`).
    url = trusted_asset_url(res.get("url"))
    if not ver or not url or not is_newer(ver):
        return None
    sha256 = res.get("sha256")
    if not isinstance(sha256, str) or not sha256.strip():
        return None
    size = res.get("size")
    return UpdateInfo(
        version=ver,
        url=url,
        notes=str(res.get("notes") or ""),
        sha256=sha256.strip(),
        size=(int(size) if isinstance(size, int) else None),
        # Left EMPTY when the payload named no real page, so `page_url` stays
        # the one place that decides where a click goes.
        html_url=http_url(res.get("html_url")),
    )


# --- download + stage ------------------------------------------------------
def download_and_stage(info: UpdateInfo, progress=None) -> Path:
    """Download the release asset, verify it, and produce the staged exe next to
    the running one as `<name>.new`; return that path. Raises on any failure
    (caller keeps the current install untouched).

    Handles both kinds of asset we publish: a **bare .exe** (the current
    convention) and a **.zip** containing the exe, detected by content, not by
    URL, so naming never matters. Streams to a temp file rather than buffering
    the whole ~300 MB payload in RAM. `progress(done, total)` is called during
    download (total may be 0 if unknown)."""
    if not is_frozen():
        raise RuntimeError("self-update only works from the packaged exe")
    # The gate is repeated HERE, not trusted from `check`: this is the function
    # that downloads a binary and stages it over the installed exe, so whatever
    # built this `UpdateInfo` (a future caller, a hand-rolled one, a payload that
    # got past an older build) it still cannot make us fetch from an arbitrary
    # host. Raised before the temp file is opened, so a refusal leaves the
    # install and the disk untouched.
    if not trusted_asset_url(info.url):
        host = (urllib.parse.urlsplit(str(info.url or "")).hostname
                or "(no url)")
        raise RuntimeError(
            f"release asset is not on a trusted host ({host}) - refusing to "
            f"download it")

    cur = exe_path()
    tmp = cur.with_name(cur.name + ".download")
    new = cur.with_name(cur.name + ".new")
    req = urllib.request.Request(info.url, headers={"User-Agent": "FareverPal-Companion"})
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
            total = info.size or int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if tmp.stat().st_size == 0:
            raise RuntimeError("downloaded an empty file")
        if h.hexdigest().lower() != info.sha256.lower():
            raise RuntimeError("checksum mismatch — refusing to install")
        if info.size and tmp.stat().st_size != info.size:
            raise RuntimeError("size mismatch — refusing to install")
        _stage_payload(tmp, cur.name, new)
        return new
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _stage_payload(downloaded: Path, exe_name: str, dest: Path) -> None:
    """Turn the downloaded asset into the staged exe at `dest`, handling either a
    bare PE executable or a zip that contains it (sniffed by magic bytes)."""
    with open(downloaded, "rb") as f:
        magic = f.read(4)
    if dest.exists():
        dest.unlink()
    if magic.startswith(b"PK\x03\x04"):            # zip archive
        with zipfile.ZipFile(downloaded) as zf:
            member = _pick_exe(zf.namelist(), exe_name)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 256 * 1024)
    elif magic.startswith(b"MZ"):                  # bare Windows executable
        os.replace(downloaded, dest)               # move (no 300 MB copy)
    else:
        raise RuntimeError("downloaded asset is neither an .exe nor a .zip")


def _pick_exe(members: list[str], exe_name: str) -> str:
    """Choose the exe member of a release zip: exact name first, else any .exe."""
    target = next((m for m in members if m.rsplit("/", 1)[-1].lower() == exe_name.lower()), None)
    if target is None:
        target = next((m for m in members if m.lower().endswith(".exe")), None)
    if target is None:
        raise RuntimeError("no .exe inside the release archive")
    return target


# --- apply (swap + relaunch) ----------------------------------------------
def apply_and_relaunch(new: Path) -> None:
    """Atomically move the staged exe over the running one and relaunch. The
    caller must quit the app immediately after this returns so the old process
    exits. Rolls back if the swap fails partway."""
    if not is_frozen():
        raise RuntimeError("self-update only works from the packaged exe")
    cur = exe_path()
    old = _old_path(cur)
    # Clear any stale .old so the rename below can't collide.
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass
    # Rename the live exe out of the way (allowed for a running image), then move
    # the new one into its place. If the second step fails, put the original back.
    os.replace(cur, old)
    try:
        os.replace(new, cur)
    except OSError:
        os.replace(old, cur)        # roll back - install stays on the old version
        raise

    flags = 0x00000008 | 0x00000200    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(cur)], cwd=str(cur.parent), close_fds=True, creationflags=flags)
