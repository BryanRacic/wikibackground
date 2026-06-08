# wikibackground

A single-file Python script that fetches a random image from [Wikimedia Commons](https://commons.wikimedia.org/) and sets it as your desktop wallpaper on GNOME or Xfce. No dependencies beyond the Python standard library.

> **Please be kind to Wikimedia Commons.** Their servers and bandwidth are funded by donations, and their API is offered freely without requiring an API key.

## Features

- Pulls from curated Wikimedia Commons categories (Featured, Quality, etc.)
- Filters by minimum resolution so images always look sharp on your display
- Supports multiple categories — pick from a random pool each time
- Works from cron/systemd with no extra configuration
- Zero external dependencies — stdlib only (Python 3.6+)
- Cleans up old downloads automatically (or keep a history)
- Tracks every download in a CSV log so the same image is never fetched twice
- Mark the current wallpaper as a `--favorite` or `--blocklist` it so it's never shown again
- Skip the wallpaper change while a fullscreen game (or any named process) is running

## Requirements

- Python 3.6+
- GNOME (`gsettings` on PATH) **or** Xfce (`xfconf-query` + running `xfdesktop`)
- Internet connection

The script auto-detects the desktop environment at runtime, so the same
invocation works on both. On Xfce it sets the wallpaper via `xfconf-query`
on the `xfce4-desktop` channel for every connected monitor and calls
`xfdesktop --reload` to refresh immediately. `--picture-option` values are
translated to the equivalent Xfce `image-style` integers (0–6).

## Installation

```bash
git clone https://github.com/YOUR_USER/wikibackground.git
cd wikibackground
chmod +x wikibackground.py
```

That's it. No `pip install`, no virtualenv.

## User-Agent (recommended)

Wikimedia's [User-Agent policy](https://meta.wikimedia.org/wiki/User-Agent_policy)
asks API clients to send a descriptive `User-Agent` that identifies the tool and
gives a way to contact you. Requests with a generic, default, or missing
User-Agent are rate-limited (you'll see `HTTP Error 429: Too Many Requests`) or
blocked more aggressively.

To set your own, copy the template and fill in your details:

```bash
cp user_agent.txt.example user_agent.txt
# then edit user_agent.txt — replace YOUR_USERNAME / YOUR_EMAIL with your own
```

The script reads the first non-comment, non-blank line of `user_agent.txt` (in
the repo directory) and sends it verbatim. If the file is missing, a generic
default is used instead. `user_agent.txt` is git-ignored, so your contact info
stays out of version control.

> If you do hit a 429 anyway, the script automatically retries with backoff
> (honouring the server's `Retry-After` header), so transient rate-limits
> recover on their own rather than crashing the run.

## Usage

```
wikibackground.py [-c CATEGORY [CATEGORY ...]] [--min-width N] [--min-height N]
                  [-p PICTURE_OPTION] [-d DIR] [--cache-ratio R] [--keep-history]
                  [--dry-run] [-v] [--skip-if-running SUBSTRING [SUBSTRING ...]]
                  [--favorite | --blocklist | --clear-cache]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `-c, --category` | `featured` | One or more category aliases or full Wikimedia category names (a random one is chosen each run) |
| `--min-width` | `1920` | Minimum image width in pixels |
| `--min-height` | `1080` | Minimum image height in pixels |
| `-p, --picture-option` | `zoom` | How GNOME displays the image: `none`, `wallpaper`, `centered`, `scaled`, `stretched`, `zoom`, `spanned` |
| `-d, --directory` | `~/.cache/wikibackground` | Where downloaded images and the download log are saved |
| `--cache-ratio` | `1.0` | Probability (0.0–1.0) of downloading a new image vs. reusing one from the cache directory |
| `--keep-history` | off | Keep previously downloaded images instead of deleting them |
| `--dry-run` | off | Download the image but don't change the wallpaper |
| `-v, --verbose` | off | Print progress to stderr |
| `--skip-if-running` | none | Skip the wallpaper change if any running process's command line contains one of the given substrings (case-insensitive). Reads `/proc/*/cmdline`; no root needed. Useful for not interrupting fullscreen games. Pass with no arguments to load patterns from `run_skip.csv` in the cache directory (auto-created with commented-out example entries on first use; edit it to enable). |
| `--favorite` | off | Tag the currently-set wallpaper as a favorite in the log and exit (does not change the wallpaper). Reads the active wallpaper from the desktop (gsettings on GNOME, xfconf on Xfce) so it works correctly after `--dry-run` or cache-reuse runs where the latest log entry isn't what's actually on screen. |
| `--blocklist` | off | Tag the currently-set wallpaper as blocklisted in the log and exit; blocklisted images are never picked again from cache or remote |
| `--clear-cache` | off | Delete cached images and log entries that aren't favorited or blocklisted, then exit. Favorites are kept on disk and in the log; blocklisted images have their file deleted but their log entry preserved (so they stay blocked) |

### Examples

```bash
# Set a random featured picture as wallpaper
./wikibackground.py

# Verbose output to see what's happening
./wikibackground.py -v

# Pick from nature, astronomy, or landscapes each run
./wikibackground.py -c nature astronomy landscapes

# High-res only (e.g., for a 4K display)
./wikibackground.py --min-width 3840 --min-height 2160

# Download only, don't change wallpaper
./wikibackground.py --dry-run -v

# Use centered mode and keep all downloaded images
./wikibackground.py -p centered --keep-history

# Build a collection and reuse cached images 70% of the time
./wikibackground.py --keep-history --cache-ratio 0.3

# Don't change the wallpaper while a game is running (matches anywhere in the command line)
./wikibackground.py --skip-if-running deadlock.exe linuxsteamrt64/cs2

# Same idea, but read patterns from ~/.cache/wikibackground/run_skip.csv
# (auto-created on first run with sensible defaults; edit it to add your own)
./wikibackground.py --skip-if-running

# Like the current wallpaper? Mark it as a favorite for later.
./wikibackground.py --favorite

# Don't want to ever see the current wallpaper again? Blocklist it.
./wikibackground.py --blocklist

# Reclaim disk space: delete everything except favorites (keeps blocklist entries).
./wikibackground.py --clear-cache
```

## Download Log

Every successful download is appended to `downloads.csv` inside the cache directory (default `~/.cache/wikibackground/downloads.csv`). The log persists across `--keep-history`-less cleanups and has the following columns:

| Column | Notes |
|---|---|
| `timestamp` | UTC ISO-8601 time of download |
| `title` | Wikimedia Commons file title (e.g. `File:Foo.jpg`) |
| `url` | Direct download URL |
| `filename` | Local filename within the cache directory |
| `favorite` | `1` if marked via `--favorite`, otherwise empty |
| `blocklist` | `1` if marked via `--blocklist`, otherwise empty |

The log drives two behaviours:

- **Never re-download the same image.** Before picking a random candidate from Wikimedia Commons, the script consults the log and skips any title/URL it has already pulled — so each run reveals something new.
- **Blocklist is permanent.** A blocklisted entry is filtered out of both the local cache (so cache-reuse won't pick it) and remote candidate lists (so it can never be re-downloaded). The local file itself will be removed on the next normal run unless `--keep-history` is set.

`--favorite` and `--blocklist` act on the wallpaper that is *currently set* (read from `gsettings` on GNOME or `xfconf-query` on Xfce), not necessarily the most recently logged download — the two diverge when the latest run was `--dry-run` or reused a cached image. If the current wallpaper has no matching log entry (e.g. set manually outside the script), the operation errors out and nothing is marked. Favorites currently just record the tag; future versions will use it to bias selection toward favorited images.

## Process Skip List

When `--skip-if-running` is passed with no arguments, the script reads patterns from `run_skip.csv` inside the cache directory (default `~/.cache/wikibackground/run_skip.csv`). The file is auto-created on first use with commented-out example entries, so a fresh install blocks nothing until you edit it:

```csv
pattern
# One substring per line; matched case-insensitively against /proc/*/cmdline.
# Blank lines and lines starting with '#' are ignored.
# Uncomment or add your own to skip the wallpaper change while they're running.
# Examples:
# deadlock.exe
# linuxsteamrt64/cs2
```

Uncomment the examples or add your own — one per line, no quoting needed. Each pattern is matched case-insensitively as a substring against the full command line of every running process under `/proc`. The script's own process and any parent shell are skipped so the flag's own arguments can't self-match.

Passing patterns directly on the command line (e.g. `--skip-if-running linuxsteamrt64/cs2 deadlock.exe`) overrides the file for that run; the file is not consulted.

## Categories

### Built-in Aliases

These short names map to popular Wikimedia Commons categories:

| Alias | Wikimedia Category |
|---|---|
| `featured` | [Featured pictures on Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Featured_pictures_on_Wikimedia_Commons) |
| `quality` | [Quality images](https://commons.wikimedia.org/wiki/Category:Quality_images) |
| `nature` | [Quality images of nature](https://commons.wikimedia.org/wiki/Category:Quality_images_of_nature) |
| `valued` | [Valued images](https://commons.wikimedia.org/wiki/Category:Valued_images) |
| `astronomy` | [Featured pictures of astronomy](https://commons.wikimedia.org/wiki/Category:Featured_pictures_of_astronomy) |
| `landscapes` | [Featured pictures of landscapes](https://commons.wikimedia.org/wiki/Category:Featured_pictures_of_landscapes) |
| `wildlife` | [Featured pictures of animals](https://commons.wikimedia.org/wiki/Category:Featured_pictures_of_animals) |

### Using Any Wikimedia Commons Category

You can pass any Wikimedia Commons category name directly — just use the exact category name as it appears in the URL, with underscores instead of spaces.

To browse all available categories, visit:

**https://commons.wikimedia.org/wiki/Commons:Categories**

Some good starting points for high-quality wallpaper images:

- [Featured pictures by subject](https://commons.wikimedia.org/wiki/Commons:Featured_pictures) — browse all featured picture subcategories
- [Quality images by subject](https://commons.wikimedia.org/wiki/Category:Quality_images_by_subject)
- [Featured pictures of architecture](https://commons.wikimedia.org/wiki/Category:Featured_pictures_of_architecture)
- [Featured pictures of science](https://commons.wikimedia.org/wiki/Category:Featured_pictures_of_science)

To use a custom category, take the category name from the URL. For example, the URL:

```
https://commons.wikimedia.org/wiki/Category:Featured_pictures_of_buildings
```

becomes:

```bash
./wikibackground.py -c Featured_pictures_of_buildings
```

You can mix aliases and full names:

```bash
./wikibackground.py -c featured nature Featured_pictures_of_buildings
```

## Automating Wallpaper Changes

### Option 1: systemd Timer (recommended)

systemd timers are the best approach on modern Ubuntu/GNOME — they handle missed runs (e.g., if your laptop was asleep), provide logging via `journalctl`, and don't require any environment variable hacks.

**Step 1:** Create the service unit:

```bash
mkdir -p ~/.config/systemd/user
```

```ini
# ~/.config/systemd/user/wikibackground.service
[Unit]
Description=Set desktop wallpaper from Wikimedia Commons

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py -c featured nature landscapes astronomy -v
```

**Step 2:** Create the timer unit:

```ini
# ~/.config/systemd/user/wikibackground.timer
[Unit]
Description=Change wallpaper periodically

[Timer]
OnCalendar=*-*-* 06,12,18,00:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

The `Persistent=true` line means if a scheduled run was missed (laptop was off or asleep), it will run as soon as possible after waking up.

**Step 3:** Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now wikibackground.timer
```

**Useful commands:**

```bash
# Check timer status
systemctl --user status wikibackground.timer

# See when it will next fire
systemctl --user list-timers

# View logs
journalctl --user -u wikibackground.service

# Run it manually right now
systemctl --user start wikibackground.service
```

### Option 2: Cron

Edit your crontab:

```bash
crontab -e
```

Add a line to change the wallpaper on a schedule. The script automatically sets `DBUS_SESSION_BUS_ADDRESS` so it works from cron without any wrapper scripts.

```cron
# Every 6 hours, skipping the change while any process listed in run_skip.csv is running
0 */6 * * * /usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py --skip-if-running

# Every day at 8am, from nature or landscapes, verbose log to a file
0 8 * * * /usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py -c nature landscapes --skip-if-running -v 2>> /tmp/wikibackground.log

# Every 30 minutes, 4K images only
*/30 * * * * /usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py --min-width 3840 --min-height 2160 --skip-if-running
```

> **Note:** Use full absolute paths for both `python3` and the script. Find your Python path with `which python3`.
>
> **Tip:** Pass `--skip-if-running` (no arguments) on every scheduled run so the script honours your `run_skip.csv` patterns and won't change the wallpaper while a fullscreen game is up. Without the flag, the skip logic is never consulted.

### Shell Alias

For quick manual use, add an alias to your `~/.bashrc` or `~/.zshrc`:

```bash
# Simple alias
alias wallpaper='/usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py'

# Alias with your preferred defaults
alias wallpaper='/usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py -c featured nature landscapes -v'

# Tag the current wallpaper as a favorite (kept across --clear-cache)
alias favpaper='/usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py --favorite'

# Blocklist the current wallpaper so it's never picked again
alias blockpaper='/usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py --blocklist'
```

Then reload your shell:

```bash
source ~/.bashrc  # or source ~/.zshrc
```

Now you can run:

```bash
wallpaper                          # use your defaults
wallpaper -c astronomy             # override category
wallpaper --min-width 3840         # override resolution
favpaper                           # like the current wallpaper? keep it
blockpaper                         # hate the current wallpaper? never show it again
```

### Symlink to PATH

To make `wikibackground` available as a command without the full path:

```bash
# User-local (make sure ~/.local/bin is on your PATH)
ln -s /home/YOUR_USER/wikibackground/wikibackground.py ~/.local/bin/wikibackground

# Or system-wide
sudo ln -s /home/YOUR_USER/wikibackground/wikibackground.py /usr/local/bin/wikibackground
```

## How It Works

1. Resolves category aliases to full Wikimedia Commons category names
2. If multiple categories are given, picks one at random
3. Fetches up to 500 file members from the category via the [MediaWiki API](https://www.mediawiki.org/wiki/API:Main_page)
4. Filters out non-image files (SVG, TIFF, video, etc.), drops any titles/URLs already in the download log, and shuffles the rest
5. Queries image dimensions for up to 20 candidates per batch, looking for one that meets the minimum resolution
6. If no match is found, fetches the next batch (up to 3 batches total)
7. Downloads the image to the cache directory and appends an entry to `downloads.csv`
8. Sets the wallpaper: on GNOME via `gsettings` (both `picture-uri` and `picture-uri-dark`); on Xfce via `xfconf-query` on `/backdrop/screen0/monitor<name>/workspace0/{last-image,image-style,image-show}` for every connected monitor, followed by `xfdesktop --reload`
9. Removes old images from the cache directory (unless `--keep-history` is set); the log itself is preserved

## AI Disclosure

Generative AI was used in the creation of this program.

## License

Public domain. Do whatever you want with it.
