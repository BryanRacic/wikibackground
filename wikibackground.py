#!/usr/bin/env python3
"""Fetch a random image from Wikimedia Commons and set it as the desktop wallpaper (GNOME/KDE/Xfce)."""

import argparse
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://commons.wikimedia.org/w/api.php"

# Wikimedia's API policy requires a descriptive User-Agent with contact info;
# generic or missing ones get rate-limited (HTTP 429) or blocked. Users supply
# their own by copying user_agent.txt.example -> user_agent.txt (see below).
DEFAULT_USER_AGENT = "wikibackground/1.0 (https://github.com/BryanRacic/wikibackground)"
USER_AGENT_FILE = Path(__file__).resolve().parent / "user_agent.txt"


def _load_user_agent():
    """Return the User-Agent from user_agent.txt, else DEFAULT_USER_AGENT.

    The first non-comment, non-blank line of the file is used verbatim. Missing
    or unreadable file falls back to the default (which is more likely to be
    rate-limited, so users are encouraged to fill in their own contact info).
    """
    try:
        for line in USER_AGENT_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except OSError:
        pass
    return DEFAULT_USER_AGENT


USER_AGENT = _load_user_agent()

# Network retry tuning for transient/rate-limit responses.
MAX_API_RETRIES = 4
DOWNLOAD_LOG = "downloads.csv"
DOWNLOAD_LOG_FIELDS = ("timestamp", "title", "url", "filename", "favorite", "blocklist")
SKIP_IF_RUNNING_LOG = "run_skip.csv"
SKIP_IF_RUNNING_TEMPLATE = (
    "pattern\n"
    "# One substring per line; matched case-insensitively against /proc/*/cmdline.\n"
    "# Blank lines and lines starting with '#' are ignored.\n"
    "# Uncomment or add your own to skip the wallpaper change while they're running.\n"
    "# Examples:\n"
    "# deadlock.exe\n"
    "# linuxsteamrt64/cs2\n"
)
TRUE_VALUES = {"1", "true", "yes", "y", "t"}

CATEGORY_ALIASES = {
    "featured": "Featured_pictures_on_Wikimedia_Commons",
    "quality": "Quality_images",
    "nature": "Quality_images_of_nature",
    "valued": "Valued_images",
    "astronomy": "Featured_pictures_of_astronomy",
    "landscapes": "Featured_pictures_of_landscapes",
    "wildlife": "Featured_pictures_of_animals",
}

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png"}
VALID_PICTURE_OPTIONS = ("none", "wallpaper", "centered", "scaled", "stretched", "zoom", "spanned")
MAX_CANDIDATES_PER_BATCH = 20
MAX_BATCHES = 3


def log(msg, verbose=True):
    if verbose:
        print(msg, file=sys.stderr)


def _retry_after_seconds(err, attempt):
    """Seconds to wait before retrying: honour Retry-After, else exp. backoff."""
    header = err.headers.get("Retry-After") if err.headers else None
    if header:
        try:
            return max(1, int(header))  # delta-seconds form
        except ValueError:
            pass  # HTTP-date form (rare here) — fall back to backoff
    return 2 ** attempt  # 1, 2, 4, 8s


def api_request(params):
    """Make a GET request to the Wikimedia Commons API.

    Retries on rate-limiting (HTTP 429) and transient server errors (5xx),
    honouring the Retry-After header when present, then re-raises if still
    failing after MAX_API_RETRIES attempts.
    """
    params["format"] = "json"
    query = urllib.parse.urlencode(params)
    url = f"{API_URL}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_API_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_API_RETRIES - 1:
                wait = _retry_after_seconds(e, attempt)
                log(f"  API HTTP {e.code}; retrying in {wait}s "
                    f"(attempt {attempt + 1}/{MAX_API_RETRIES})...")
                time.sleep(wait)
                continue
            raise


def fetch_category_members(category, cmcontinue=None):
    """Fetch a batch of file members from a category."""
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtype": "file",
        "cmtitle": f"Category:{category}",
        "cmlimit": "500",
    }
    if cmcontinue:
        params["cmcontinue"] = cmcontinue
    data = api_request(params)
    members = data.get("query", {}).get("categorymembers", [])
    cont = data.get("continue", {}).get("cmcontinue")
    return members, cont


def get_image_infos(titles):
    """Get dimensions and download URLs for many files in a single request.

    The MediaWiki API accepts up to 50 titles per query, so batching here turns
    what used to be one HTTP request per candidate into one per batch — the main
    lever for staying under the rate limit. Returns {requested_title: {"url",
    "width", "height"}}; titles the API can't resolve are simply absent.
    """
    if not titles:
        return {}
    data = api_request({
        "action": "query",
        "titles": "|".join(titles),
        "prop": "imageinfo",
        "iiprop": "url|size",
    })
    query = data.get("query", {})
    # The API may normalize titles (e.g. underscores -> spaces); map the
    # returned page titles back to the exact strings we asked for.
    norm = {n["to"]: n["from"] for n in query.get("normalized", [])}
    infos = {}
    for page in query.get("pages", {}).values():
        imageinfo = page.get("imageinfo")
        if not imageinfo:
            continue
        info = imageinfo[0]
        key = norm.get(page.get("title"), page.get("title"))
        infos[key] = {
            "url": info.get("url"),
            "width": info.get("width", 0),
            "height": info.get("height", 0),
        }
    return infos


def download_image(url, dest_path):
    """Download an image to the given path."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(resp, f)


XFCE_STYLE_MAP = {
    "none": 0, "centered": 1, "wallpaper": 2, "stretched": 3,
    "scaled": 4, "zoom": 5, "spanned": 6,
}

# Map the GNOME-style picture-options onto the fill-mode names understood by
# `plasma-apply-wallpaperimage --fill-mode` (KDE's org.kde.image plugin).
KDE_FILL_MAP = {
    "none": "center", "centered": "center", "wallpaper": "tile",
    "stretched": "stretch", "scaled": "preserveAspectFit",
    "zoom": "preserveAspectCrop", "spanned": "preserveAspectCrop",
}


def _session_env():
    """Build an env that lets gsettings/xfconf talk to the user's session bus from cron."""
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    env.setdefault("DISPLAY", ":0")
    return env


def detect_desktop():
    """Return 'kde', 'xfce', or 'gnome' based on the running session / available tools."""
    current = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "KDE" in current or "PLASMA" in current:
        return "kde"
    if "XFCE" in current:
        return "xfce"
    if "GNOME" in current:
        return "gnome"
    # Cron has no XDG_CURRENT_DESKTOP — fall back to process detection.
    # Check KDE/Xfce before the gsettings fallback: Kubuntu ships gsettings,
    # so the bare gsettings check would otherwise misidentify KDE as GNOME.
    if shutil.which("plasma-apply-wallpaperimage") and subprocess.run(
        ["pgrep", "-x", "plasmashell"], capture_output=True
    ).returncode == 0:
        return "kde"
    if shutil.which("xfconf-query") and subprocess.run(
        ["pgrep", "-x", "xfdesktop"], capture_output=True
    ).returncode == 0:
        return "xfce"
    if shutil.which("gsettings"):
        return "gnome"
    return "unknown"


def set_wallpaper_gnome(image_path, picture_option):
    """Set the GNOME desktop wallpaper via gsettings."""
    uri = f"file://{image_path}"
    env = _session_env()

    for key in ("picture-uri", "picture-uri-dark"):
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.background", key, uri],
            env=env, check=True,
        )
    subprocess.run(
        ["gsettings", "set", "org.gnome.desktop.background", "picture-options", picture_option],
        env=env, check=True,
    )


def _xrandr_monitors(env):
    """Return connected xrandr output names, e.g. ['DP-2', 'DP-3']."""
    result = subprocess.run(
        ["xrandr", "--listmonitors"], env=env, capture_output=True, text=True, check=True,
    )
    names = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].rstrip(":").isdigit():
            names.append(parts[-1])
    return names


def set_wallpaper_xfce(image_path, picture_option, verbose):
    """Set the Xfce desktop wallpaper via xfconf-query on all monitor backdrops."""
    env = _session_env()
    style = XFCE_STYLE_MAP.get(picture_option, 5)
    path_str = str(image_path)

    result = subprocess.run(
        ["xfconf-query", "-c", "xfce4-desktop", "-l"],
        env=env, capture_output=True, text=True, check=True,
    )
    last_image_props = sorted(
        line.strip() for line in result.stdout.splitlines()
        if line.strip().endswith("/last-image")
    )

    if not last_image_props:
        # Fresh xfdesktop: no backdrop props populated yet. Synthesize them
        # for every connected monitor so xfdesktop picks up the wallpaper.
        monitors = _xrandr_monitors(env)
        if not monitors:
            raise RuntimeError("xfconf has no backdrops and xrandr found no monitors")
        last_image_props = [
            f"/backdrop/screen0/monitor{m}/workspace0/last-image" for m in monitors
        ]
        log(f"Creating xfce4-desktop backdrop properties for monitors: {monitors}", verbose)

    for prop in last_image_props:
        style_prop = prop.replace("/last-image", "/image-style")
        show_prop = prop.replace("/last-image", "/image-show")
        # --create is a no-op if the property already exists with the same type.
        subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-p", prop,
             "--create", "-t", "string", "-s", path_str],
            env=env, check=False,
        )
        subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-p", style_prop,
             "--create", "-t", "int", "-s", str(style)],
            env=env, check=False,
        )
        subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-p", show_prop,
             "--create", "-t", "bool", "-s", "true"],
            env=env, check=False,
        )

    # Force xfdesktop to re-render immediately.
    if shutil.which("xfdesktop"):
        subprocess.run(["xfdesktop", "--reload"], env=env, check=False)


def set_wallpaper_kde(image_path, picture_option, verbose):
    """Set the KDE Plasma wallpaper via plasma-apply-wallpaperimage (all screens)."""
    env = _session_env()
    fill_mode = KDE_FILL_MAP.get(picture_option, "preserveAspectCrop")
    cmd = ["plasma-apply-wallpaperimage", "--fill-mode", fill_mode, str(image_path)]
    log(f"Running: {' '.join(cmd)}", verbose)
    result = subprocess.run(env=env, args=cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "plasma-apply-wallpaperimage failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )


def set_wallpaper(image_path, picture_option, verbose=False):
    desktop = detect_desktop()
    if desktop == "kde":
        set_wallpaper_kde(image_path, picture_option, verbose)
    elif desktop == "xfce":
        set_wallpaper_xfce(image_path, picture_option, verbose)
    elif desktop == "gnome":
        set_wallpaper_gnome(image_path, picture_option)
    else:
        raise RuntimeError(
            "Could not detect desktop environment. None of KDE "
            "(plasma-apply-wallpaperimage), XFCE (xfconf-query + xfdesktop), "
            "or GNOME (gsettings) available."
        )


def _current_wallpaper_filename_gnome():
    """GNOME: read org.gnome.desktop.background/picture-uri via gsettings."""
    if not shutil.which("gsettings"):
        return None
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
            env=_session_env(), capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None

    # gsettings prints GVariant text format, which may quote strings with either
    # single or double quotes depending on the libglib version (older builds use
    # ', newer ones use "). Strip whichever wraps the value before parsing.
    raw = result.stdout.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        quote = raw[0]
        raw = raw[1:-1].replace("\\" + quote, quote).replace("\\\\", "\\")
    if not raw.startswith("file://"):
        return None
    path_str = urllib.parse.unquote(raw[len("file://"):])
    if not path_str:
        return None
    return Path(path_str).name


def _current_wallpaper_filename_xfce():
    """Xfce: read the first /backdrop/.../last-image via xfconf-query.

    set_wallpaper_xfce writes the same path to every monitor's last-image, so
    picking the first one is enough to identify the current wallpaper.
    """
    if not shutil.which("xfconf-query"):
        return None
    env = _session_env()
    try:
        listing = subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-l"],
            env=env, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    props = sorted(
        line.strip() for line in listing.stdout.splitlines()
        if line.strip().endswith("/last-image")
    )
    if not props:
        return None
    try:
        value = subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-p", props[0]],
            env=env, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    path_str = value.stdout.strip()
    if not path_str:
        return None
    return Path(path_str).name


def _current_wallpaper_filename_kde():
    """KDE: read the last Image= from plasma-org.kde.plasma.desktop-appletsrc.

    plasma-apply-wallpaperimage writes the same path to every screen's
    [Containments][N][Wallpaper][org.kde.image][General] Image= key, so the
    last Image= line in the file identifies the current wallpaper.
    """
    config_home = os.environ.get(
        "XDG_CONFIG_HOME", os.path.expanduser("~/.config")
    )
    config = Path(config_home) / "plasma-org.kde.plasma.desktop-appletsrc"
    try:
        text = config.read_text()
    except OSError:
        return None

    raw = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Image="):
            raw = line[len("Image="):].strip()
    if not raw:
        return None
    if raw.startswith("file://"):
        raw = urllib.parse.unquote(raw[len("file://"):])
    return Path(raw).name


def get_current_wallpaper_filename():
    """Return the basename of the currently-set wallpaper, or None on error."""
    desktop = detect_desktop()
    if desktop == "kde":
        return _current_wallpaper_filename_kde()
    if desktop == "xfce":
        return _current_wallpaper_filename_xfce()
    if desktop == "gnome":
        return _current_wallpaper_filename_gnome()
    return None


def clean_old_images(directory, current_file):
    """Remove all images in directory except the current one."""
    for p in directory.iterdir():
        if p != current_file and p.suffix.lower() in VALID_EXTENSIONS:
            p.unlink()


def _truthy(value):
    return str(value or "").strip().lower() in TRUE_VALUES


def load_download_log(log_path):
    """Return normalized rows from the download log (empty list if missing)."""
    if not log_path.exists():
        return []
    rows = []
    with open(log_path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: (row.get(k) or "") for k in DOWNLOAD_LOG_FIELDS})
    return rows


def save_download_log(log_path, rows):
    """Rewrite the full log with the current header (migrates older schemas)."""
    with open(log_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DOWNLOAD_LOG_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in DOWNLOAD_LOG_FIELDS})


def record_download(log_path, title, url, filename):
    """Append a download entry to the log."""
    rows = load_download_log(log_path)
    rows.append({
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": title,
        "url": url,
        "filename": filename,
        "favorite": "",
        "blocklist": "",
    })
    save_download_log(log_path, rows)


def mark_current_wallpaper(log_path, field, current_filename):
    """Set `field` to '1' on the log entry matching the currently-set wallpaper.

    Returns (row, status):
      - ("<row>", "matched"):   tagged the entry for current_filename
      - ("<row>", "unknown"):   current_filename was None (desktop reader failed);
                                tagged the most recent entry as a fallback
      - (None,    "not_found"): current_filename didn't match any log entry
      - (None,    "empty"):     log has no entries
    """
    if field not in ("favorite", "blocklist"):
        raise ValueError(f"Unsupported mark field: {field}")
    rows = load_download_log(log_path)
    if not rows:
        return None, "empty"

    if current_filename is None:
        rows[-1][field] = "1"
        save_download_log(log_path, rows)
        return rows[-1], "unknown"

    # Scan newest-first so the most recent matching entry wins if a filename
    # was ever re-downloaded.
    for i in range(len(rows) - 1, -1, -1):
        if rows[i]["filename"] == current_filename:
            rows[i][field] = "1"
            save_download_log(log_path, rows)
            return rows[i], "matched"

    return None, "not_found"


def clear_cache(cache_dir, log_path, verbose):
    """Delete unmarked cached images and their log entries.

    Favorites: file and log entry both kept.
    Blocklisted: file deleted, log entry kept (so the image stays blocked).
    Unmarked: file deleted, log entry removed.
    Untracked files on disk (no log entry) are treated as unmarked and deleted.
    """
    rows = load_download_log(log_path)
    favorite_files = {r["filename"] for r in rows if _truthy(r["favorite"]) and r["filename"]}
    blocked_files = {r["filename"] for r in rows if _truthy(r["blocklist"]) and r["filename"]}

    files_deleted = 0
    for p in cache_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in VALID_EXTENSIONS:
            continue
        if p.name in favorite_files:
            log(f"  Keeping favorite: {p.name}", verbose)
            continue
        p.unlink()
        files_deleted += 1
        log(f"  Deleted: {p.name}", verbose)

    kept_rows = [
        r for r in rows
        if _truthy(r["favorite"]) or _truthy(r["blocklist"])
    ]
    rows_removed = len(rows) - len(kept_rows)
    save_download_log(log_path, kept_rows)

    return files_deleted, rows_removed, len(favorite_files), len(blocked_files)


def load_skip_if_running_patterns(path):
    """Read patterns from run_skip.csv, creating an empty template if missing.

    The auto-created file contains only commented-out example entries, so a
    fresh install blocks nothing until the user edits it. Blank lines and
    lines starting with '#' are ignored. Returns a list of non-empty pattern
    strings in file order.
    """
    if not path.exists():
        path.write_text(SKIP_IF_RUNNING_TEMPLATE)

    patterns = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            pat = (row.get("pattern") or "").strip()
            if pat and not pat.startswith("#"):
                patterns.append(pat)
    return patterns


def _ancestor_pids():
    """Return {self pid + every ancestor pid} by walking /proc/<pid>/stat ppids.

    Used to skip the script's own process and any shell that invoked it, since
    their command lines contain the --skip-if-running argument as a literal
    substring and would otherwise self-match.
    """
    pids = set()
    pid = os.getpid()
    while pid and pid not in pids:
        pids.add(pid)
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text()
        except (OSError, PermissionError):
            break
        # Field layout: pid (comm) state ppid ... — comm may contain spaces or
        # parens, so split on the last ')' rather than naive whitespace.
        rparen = stat.rfind(")")
        if rparen < 0:
            break
        fields = stat[rparen + 1 :].split()
        if len(fields) < 2:
            break
        try:
            pid = int(fields[1])
        except ValueError:
            break
    return pids


def find_blocking_process(patterns):
    """Scan /proc for a running process whose cmdline contains any pattern.

    Returns (pid, cmdline, matched_pattern) on first match, else None.
    Matching is case-insensitive substring on the full argv joined by spaces.
    Skips self and ancestor processes so the script doesn't self-match on its
    own --skip-if-running arguments. Unreadable entries (permissions, kernel
    threads, race with exit) are also skipped.
    """
    lowered = [(p, p.lower()) for p in patterns if p]
    if not lowered:
        return None
    skip_pids = _ancestor_pids()
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in skip_pids:
            continue
        try:
            data = (entry / "cmdline").read_bytes()
        except (OSError, PermissionError):
            continue
        if not data:
            continue
        cmdline = data.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        cl_lower = cmdline.lower()
        for orig, lower in lowered:
            if lower in cl_lower:
                return pid, cmdline, orig
    return None


def find_suitable_image(category, min_width, min_height, seen_titles, seen_urls, verbose):
    """Find an image meeting resolution requirements that hasn't been downloaded.

    Returns (title, info) or None.
    """
    cmcontinue = None
    for batch_num in range(1, MAX_BATCHES + 1):
        log(f"Fetching batch {batch_num} from Category:{category}...", verbose)
        members, cmcontinue = fetch_category_members(category, cmcontinue)
        if not members:
            log("No members found in category.", verbose)
            return None

        # Filter to valid image extensions, skip already-downloaded titles, shuffle
        candidates = [
            m for m in members
            if Path(m["title"]).suffix.lower() in VALID_EXTENSIONS
            and m["title"] not in seen_titles
        ]
        random.shuffle(candidates)
        candidates = candidates[:MAX_CANDIDATES_PER_BATCH]
        log(f"  {len(candidates)} fresh image candidates in this batch.", verbose)

        # One imageinfo request for the whole batch instead of one per title.
        infos = get_image_infos([c["title"] for c in candidates])
        for candidate in candidates:
            title = candidate["title"]
            info = infos.get(title)
            if not info or not info["url"]:
                continue
            if info["url"] in seen_urls:
                log(f"  {title}: already downloaded (url match), skipping.", verbose)
                continue
            if info["width"] >= min_width and info["height"] >= min_height:
                log(f"  Found: {title} {info['width']}x{info['height']}", verbose)
                return title, info
            else:
                log(f"  Too small: {title} {info['width']}x{info['height']}", verbose)

        if not cmcontinue:
            log("No more batches available.", verbose)
            break

    return None


def main():
    parser = argparse.ArgumentParser(description="Set desktop wallpaper from Wikimedia Commons.")
    parser.add_argument("-c", "--category", nargs="+", default=["featured"],
                        help="One or more category aliases or full Wikimedia category names; a random one is chosen (default: featured)")
    parser.add_argument("--min-width", type=int, default=1920,
                        help="Minimum image width in pixels (default: 1920)")
    parser.add_argument("--min-height", type=int, default=1080,
                        help="Minimum image height in pixels (default: 1080)")
    parser.add_argument("-p", "--picture-option", default="zoom", choices=VALID_PICTURE_OPTIONS,
                        help="GNOME picture option (default: zoom)")
    parser.add_argument("-d", "--directory", default="~/.cache/wikibackground",
                        help="Directory to save downloaded images (default: ~/.cache/wikibackground)")
    parser.add_argument("--cache-ratio", type=float, default=1.0, metavar="RATIO",
                        help="Probability (0.0–1.0) of downloading a new image vs. reusing one from the cache directory (default: 1.0 = always download new)")
    parser.add_argument("--keep-history", action="store_true",
                        help="Don't delete previous downloads")
    parser.add_argument("--dry-run", action="store_true",
                        help="Download but don't set wallpaper")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("--skip-if-running", nargs="*", metavar="SUBSTRING",
                        help="Skip the wallpaper change if any running process's command line contains one of the given substrings (case-insensitive). If passed with no arguments, patterns are read from run_skip.csv in the cache directory (auto-created with sane defaults on first use).")
    mark_group = parser.add_mutually_exclusive_group()
    mark_group.add_argument("--favorite", action="store_true",
                            help="Mark the currently-set wallpaper as a favorite and exit.")
    mark_group.add_argument("--blocklist", action="store_true",
                            help="Mark the currently-set wallpaper as blocklisted (never shown again) and exit.")
    mark_group.add_argument("--clear-cache", action="store_true",
                            help="Delete cached images and log entries that aren't favorited or blocklisted, then exit. Favorites are kept; blocklisted images have their file deleted but their log entry preserved.")
    args = parser.parse_args()

    # Validate cache-ratio
    if not 0.0 <= args.cache_ratio <= 1.0:
        print("Error: --cache-ratio must be between 0.0 and 1.0.", file=sys.stderr)
        sys.exit(1)

    verbose = args.verbose

    # Ensure cache directory exists (needed by all paths, including tagging)
    cache_dir = Path(args.directory).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_path = cache_dir / DOWNLOAD_LOG

    # Tag-only fast path: --favorite / --blocklist mark the wallpaper currently
    # set on the desktop (not just the latest download — the two diverge after
    # --dry-run or cache-reuse runs).
    if args.favorite or args.blocklist:
        field = "favorite" if args.favorite else "blocklist"
        current = get_current_wallpaper_filename()
        row, status = mark_current_wallpaper(log_path, field, current)
        if status == "empty":
            print("Error: no downloads recorded; nothing to mark.", file=sys.stderr)
            sys.exit(1)
        if status == "not_found":
            print(
                f"Error: current wallpaper ({current}) has no matching entry in the "
                f"download log; nothing marked. Was it set by this script?",
                file=sys.stderr,
            )
            sys.exit(1)
        if status == "unknown":
            print(
                "Warning: couldn't read current wallpaper from the desktop environment; "
                "marked the most recent download instead.",
                file=sys.stderr,
            )
        print(f"Marked {row['filename']} as {field}.")
        return

    if args.clear_cache:
        files_deleted, rows_removed, kept_favorites, kept_blocked = clear_cache(
            cache_dir, log_path, verbose,
        )
        print(
            f"Cleared cache: deleted {files_deleted} file(s) and {rows_removed} log entry(s). "
            f"Kept {kept_favorites} favorite(s); preserved {kept_blocked} blocklist entry(s)."
        )
        return

    # Bail out early if a blocklisted process (e.g., a fullscreen game) is running.
    # nargs="*" gives None when the flag is absent, [] when passed with no args.
    if args.skip_if_running is not None:
        if args.skip_if_running:
            patterns = args.skip_if_running
        else:
            patterns = load_skip_if_running_patterns(cache_dir / SKIP_IF_RUNNING_LOG)
            log(f"Loaded {len(patterns)} skip pattern(s) from {SKIP_IF_RUNNING_LOG}.", verbose)
        match = find_blocking_process(patterns)
        if match:
            pid, cmdline, pattern = match
            log(f"Skipping: '{pattern}' matched pid {pid} ({cmdline[:120]}).", verbose=True)
            return

    # Resolve category aliases and pick one at random
    categories = [CATEGORY_ALIASES.get(c, c) for c in args.category]
    category = random.choice(categories)

    # Validate a supported desktop is available (unless dry-run)
    if not args.dry_run:
        desktop = detect_desktop()
        if desktop == "unknown":
            print("Error: no supported desktop detected. Need gsettings (GNOME) or "
                  "xfconf-query + xfdesktop (Xfce).", file=sys.stderr)
            sys.exit(1)
        log(f"Detected desktop: {desktop}", verbose)

    # Load the download log once; used for cache filtering and remote dedupe.
    log_rows = load_download_log(log_path)
    seen_titles = {r["title"] for r in log_rows if r["title"]}
    seen_urls = {r["url"] for r in log_rows if r["url"]}
    blocked_filenames = {
        r["filename"] for r in log_rows
        if _truthy(r["blocklist"]) and r["filename"]
    }

    # Check for cached images and decide whether to reuse one (excluding blocklisted)
    cached_images = [
        p for p in cache_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS
        and p.name not in blocked_filenames
    ]

    if cached_images and random.random() >= args.cache_ratio:
        # Reuse a random cached image
        dest = random.choice(cached_images)
        log(f"Reusing cached image: {dest}", verbose)

        if args.dry_run:
            log("Dry run — wallpaper not changed.", verbose)
        else:
            set_wallpaper(dest, args.picture_option, verbose)
            log(f"Wallpaper set ({args.picture_option}).", verbose)

        print(dest)
        return

    # Download a new image
    log(f"Category: {category}", verbose)
    log(f"Min resolution: {args.min_width}x{args.min_height}", verbose)
    log(f"{len(seen_titles)} prior downloads on record ({len(blocked_filenames)} blocklisted).", verbose)

    result = find_suitable_image(
        category, args.min_width, args.min_height, seen_titles, seen_urls, verbose,
    )
    if not result:
        print("Error: no suitable image found.", file=sys.stderr)
        sys.exit(1)

    title, info = result
    # Derive filename from title (strip "File:" prefix)
    filename = title.replace("File:", "").replace(" ", "_")
    dest = cache_dir / filename

    log(f"Downloading {info['url']}...", verbose)
    download_image(info["url"], dest)
    log(f"Saved to {dest}", verbose)
    record_download(log_path, title, info["url"], filename)

    if args.dry_run:
        log("Dry run — wallpaper not changed.", verbose)
    else:
        set_wallpaper(dest, args.picture_option, verbose)
        log(f"Wallpaper set ({args.picture_option}).", verbose)

    if not args.keep_history:
        clean_old_images(cache_dir, dest)
        log("Old images cleaned up.", verbose)

    # Print the path to stdout
    print(dest)


if __name__ == "__main__":
    main()
