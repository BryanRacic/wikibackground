#!/usr/bin/env python3
"""Fetch a random image from Wikimedia Commons and set it as the GNOME desktop wallpaper."""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "wikibackground/1.0 (https://github.com/; desktop wallpaper script) Python/urllib"

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


def api_request(params):
    """Make a GET request to the Wikimedia Commons API."""
    params["format"] = "json"
    query = urllib.parse.urlencode(params)
    url = f"{API_URL}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


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


def get_image_info(title):
    """Get image dimensions and download URL for a file."""
    data = api_request({
        "action": "query",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|size",
    })
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        info = page.get("imageinfo", [{}])[0]
        return {
            "url": info.get("url"),
            "width": info.get("width", 0),
            "height": info.get("height", 0),
        }
    return None


def download_image(url, dest_path):
    """Download an image to the given path."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(resp, f)


def set_wallpaper(image_path, picture_option):
    """Set the GNOME desktop wallpaper via gsettings."""
    uri = f"file://{image_path}"
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")

    for key in ("picture-uri", "picture-uri-dark"):
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.background", key, uri],
            env=env, check=True,
        )
    subprocess.run(
        ["gsettings", "set", "org.gnome.desktop.background", "picture-options", picture_option],
        env=env, check=True,
    )


def clean_old_images(directory, current_file):
    """Remove all images in directory except the current one."""
    for p in directory.iterdir():
        if p != current_file and p.suffix.lower() in VALID_EXTENSIONS:
            p.unlink()


def find_suitable_image(category, min_width, min_height, verbose):
    """Find an image meeting resolution requirements. Returns (title, info) or None."""
    cmcontinue = None
    for batch_num in range(1, MAX_BATCHES + 1):
        log(f"Fetching batch {batch_num} from Category:{category}...", verbose)
        members, cmcontinue = fetch_category_members(category, cmcontinue)
        if not members:
            log("No members found in category.", verbose)
            return None

        # Filter to valid image extensions and shuffle
        candidates = [
            m for m in members
            if Path(m["title"]).suffix.lower() in VALID_EXTENSIONS
        ]
        random.shuffle(candidates)
        log(f"  {len(candidates)} image candidates in this batch.", verbose)

        for candidate in candidates[:MAX_CANDIDATES_PER_BATCH]:
            title = candidate["title"]
            log(f"  Checking {title}...", verbose)
            info = get_image_info(title)
            if not info or not info["url"]:
                continue
            if info["width"] >= min_width and info["height"] >= min_height:
                log(f"  Found: {info['width']}x{info['height']}", verbose)
                return title, info
            else:
                log(f"  Too small: {info['width']}x{info['height']}", verbose)

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
    parser.add_argument("--keep-history", action="store_true",
                        help="Don't delete previous downloads")
    parser.add_argument("--dry-run", action="store_true",
                        help="Download but don't set wallpaper")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    args = parser.parse_args()

    # Resolve category aliases and pick one at random
    categories = [CATEGORY_ALIASES.get(c, c) for c in args.category]
    category = random.choice(categories)
    verbose = args.verbose

    # Validate gsettings exists (unless dry-run)
    if not args.dry_run and not shutil.which("gsettings"):
        print("Error: gsettings not found on PATH. Is GNOME installed?", file=sys.stderr)
        sys.exit(1)

    # Ensure cache directory exists
    cache_dir = Path(args.directory).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)

    log(f"Category: {category}", verbose)
    log(f"Min resolution: {args.min_width}x{args.min_height}", verbose)

    # Find a suitable image
    result = find_suitable_image(category, args.min_width, args.min_height, verbose)
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

    if args.dry_run:
        log("Dry run — wallpaper not changed.", verbose)
    else:
        set_wallpaper(dest, args.picture_option)
        log(f"Wallpaper set ({args.picture_option}).", verbose)

    if not args.keep_history:
        clean_old_images(cache_dir, dest)
        log("Old images cleaned up.", verbose)

    # Print the path to stdout
    print(dest)


if __name__ == "__main__":
    main()
