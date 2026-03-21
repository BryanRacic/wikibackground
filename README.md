# wikibackground

A single-file Python script that fetches a random image from [Wikimedia Commons](https://commons.wikimedia.org/) and sets it as your GNOME desktop wallpaper. No dependencies beyond the Python standard library.

> **Please be kind to Wikimedia Commons.** Their servers and bandwidth are funded by donations, and their API is offered freely without requiring an API key.

## Features

- Pulls from curated Wikimedia Commons categories (Featured, Quality, etc.)
- Filters by minimum resolution so images always look sharp on your display
- Supports multiple categories — pick from a random pool each time
- Works from cron/systemd with no extra configuration
- Zero external dependencies — stdlib only (Python 3.6+)
- Cleans up old downloads automatically (or keep a history)

## Requirements

- Python 3.6+
- GNOME desktop environment (`gsettings` must be on PATH)
- Internet connection

## Installation

```bash
git clone https://github.com/YOUR_USER/wikibackground.git
cd wikibackground
chmod +x wikibackground.py
```

That's it. No `pip install`, no virtualenv.

## Usage

```
wikibackground.py [-c CATEGORY [CATEGORY ...]] [--min-width N] [--min-height N]
                  [-p PICTURE_OPTION] [-d DIR] [--keep-history] [--dry-run] [-v]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `-c, --category` | `featured` | One or more category aliases or full Wikimedia category names (a random one is chosen each run) |
| `--min-width` | `1920` | Minimum image width in pixels |
| `--min-height` | `1080` | Minimum image height in pixels |
| `-p, --picture-option` | `zoom` | How GNOME displays the image: `none`, `wallpaper`, `centered`, `scaled`, `stretched`, `zoom`, `spanned` |
| `-d, --directory` | `~/.cache/wikibackground` | Where downloaded images are saved |
| `--keep-history` | off | Keep previously downloaded images instead of deleting them |
| `--dry-run` | off | Download the image but don't change the wallpaper |
| `-v, --verbose` | off | Print progress to stderr |

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
```

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
# Every 6 hours
0 */6 * * * /usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py

# Every day at 8am, from nature or landscapes, verbose log to a file
0 8 * * * /usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py -c nature landscapes -v 2>> /tmp/wikibackground.log

# Every 30 minutes, 4K images only
*/30 * * * * /usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py --min-width 3840 --min-height 2160
```

> **Note:** Use full absolute paths for both `python3` and the script. Find your Python path with `which python3`.

### Shell Alias

For quick manual use, add an alias to your `~/.bashrc` or `~/.zshrc`:

```bash
# Simple alias
alias wallpaper='/usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py'

# Alias with your preferred defaults
alias wallpaper='/usr/bin/python3 /home/YOUR_USER/wikibackground/wikibackground.py -c featured nature landscapes -v'
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
4. Filters out non-image files (SVG, TIFF, video, etc.) and shuffles the results
5. Queries image dimensions for up to 20 candidates per batch, looking for one that meets the minimum resolution
6. If no match is found, fetches the next batch (up to 3 batches total)
7. Downloads the image to the cache directory
8. Sets the wallpaper via `gsettings` (both `picture-uri` and `picture-uri-dark`)
9. Removes old images from the cache directory (unless `--keep-history` is set)

## AI Disclosure

Generative AI was used in the creation of this program.

## License

Public domain. Do whatever you want with it.
