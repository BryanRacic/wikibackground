# wikibackground — Code Audit Results

**Date:** 2026-06-09
**Scope:** `wikibackground.py` (790 lines), `README.md`, `user_agent.txt.example`, `.gitignore`
**Method:** Full manual read of all source and docs, plus empirical verification of suspected issues (compile check, CSV parsing behavior, import analysis).

Overall: the code is in good shape — clear docstrings, sensible retry/backoff, careful `/proc` parsing, and thoughtful edge-case handling (GVariant quoting, ancestor-PID self-match avoidance, log schema migration). The findings below are ordered by severity.

---

## High severity

### H1. Normal runs delete favorited images from disk

`clean_old_images()` (`wikibackground.py:376-380`) removes **every** image except the one just set:

```python
def clean_old_images(directory, current_file):
    for p in directory.iterdir():
        if p != current_file and p.suffix.lower() in VALID_EXTENSIONS:
            p.unlink()
```

It is called on every non-`--keep-history` run (`wikibackground.py:780-782`) and does not consult the download log. Meanwhile `clear_cache()` (`wikibackground.py:453-483`) carefully preserves favorite files on disk, and the README/aliases advertise favorites as "kept across `--clear-cache`". The net effect: a user favorites a wallpaper, the next scheduled run deletes the file. The tag survives in the log but the image is gone, which defeats the stated future plan of biasing selection toward favorites (the title is in `seen_titles`, so it can never be re-downloaded either).

**Fix:** pass the favorite filename set into `clean_old_images` and skip those files, mirroring `clear_cache`'s behavior. (Or document loudly that favorites only survive with `--keep-history` — but preserving them is almost certainly the intended semantics.)

### H2. README claims Python 3.6+, but the code requires 3.7+

`README.md` says "Python 3.6+" (lines 13, 21), but the script uses `subprocess.run(capture_output=True, text=True)` throughout (e.g. `wikibackground.py:203, 230, 246`). Both `capture_output` and `text` were added in Python 3.7. On 3.6 the script crashes with `TypeError: __init__() got an unexpected keyword argument 'capture_output'` the moment desktop detection runs.

**Fix:** update the README to "Python 3.7+" (3.8+ would be a safe, honest floor given 3.6/3.7 are long EOL).

---

## Medium severity

### M1. `urllib.parse` is used but never imported

`wikibackground.py` imports `urllib.request` and `urllib.error` but calls `urllib.parse.urlencode` (`:102`) and `urllib.parse.unquote` (`:325`). This only works because `urllib.request` happens to import `urllib.parse` internally — a CPython implementation detail, not a guarantee. Verified: `import urllib.parse` appears nowhere in the file.

**Fix:** add `import urllib.parse` to the imports.

### M2. Skip patterns are parsed as CSV, so commas and quotes are mangled

`load_skip_if_running_patterns()` (`wikibackground.py:498-503`) reads `run_skip.csv` with `csv.DictReader`, but the format is documented as "one substring per line; no quoting needed." Verified empirically:

- Pattern `foo,bar` is silently truncated to `foo` (the rest lands in a `None` overflow key).
- A line `"quoted pattern"` has its quotes stripped, so the literal quoted string can never be matched.

Process command lines routinely contain commas, so this is a realistic trap.

**Fix:** read plain lines (skip blanks/`#`, strip whitespace) instead of using the `csv` module — the file format already behaves like a plain line-per-pattern file everywhere else (template, docs). Keep the `pattern` header line for backward compatibility by skipping it.

### M3. Download log rewrite is non-atomic and unsafe under concurrent runs

`record_download()` (`wikibackground.py:407-418`) reads the **entire** log and rewrites the whole file just to append one row, and `save_download_log()` (`:398-404`) writes in place with `open(..., "w")`:

- If the process dies mid-write (power loss, OOM-kill during a cron run), the entire download history is truncated or lost — which silently un-blocklists everything.
- Two overlapping runs (cron firing while a manual run is in flight, or `--favorite` during a scheduled run) read-modify-write the same file with no locking; the loser's changes are silently discarded.
- Rewriting the full file per append is O(n²) over the log's lifetime.

**Fix:** write to a temp file in the same directory and `os.replace()` it (atomic on POSIX). For `record_download`, append a single row when the header already matches `DOWNLOAD_LOG_FIELDS` and only fall back to a full rewrite for schema migration. A simple `fcntl.flock` around read-modify-write operations would close the concurrency hole cheaply.

### M4. Failed/interrupted downloads leave partial files that get used as wallpapers

`download_image()` (`wikibackground.py:171-176`) streams directly to the final destination. If the connection drops mid-transfer, a truncated image is left in the cache directory. It is never logged (logging happens after download, `:772`), so:

- The cache-reuse path (`:732-750`) happily picks it and sets a corrupt image as the wallpaper.
- `clear_cache` treats it as untracked and deletes it — but only if the user runs that.

Also, unlike API calls, image downloads get no retry on 429/5xx, so a transient rate-limit kills the whole run.

**Fix:** download to `dest.with_suffix(dest.suffix + ".part")` (a non-`VALID_EXTENSIONS` suffix, so it's invisible to cache reuse), then `os.replace()` to the final name on success; unlink the temp on failure. Optionally reuse the same retry/backoff loop as `api_request`.

### M5. Image selection is heavily biased toward the alphabetical start of a category

`fetch_category_members` returns members in sorted order, and `find_suitable_image` (`wikibackground.py:570-613`) only ever looks at the first `MAX_BATCHES × 500 = 1500` members. For large categories ("Featured pictures on Wikimedia Commons" has tens of thousands of files), the script samples only the alphabetical head — users will see disproportionately many images whose titles start with digits/`A`, and crawl forward only as those get marked seen.

**Fix options (cheapest first):**
1. Pass a random `cmstarthexsortkey` (e.g. a random hex prefix) so each run starts at a random point in the category.
2. When a fetched batch of 500 yields no suitable candidate among the sampled 20, sample another 20 from the *same* already-fetched batch before requesting the next page (also saves API calls — see L7).

---

## Low severity

### L1. API retry messages ignore the `--verbose` flag

`api_request` calls `log(...)` without a verbose argument (`wikibackground.py:112-113`), and `log()` defaults to `verbose=True`, so retry chatter always prints to stderr even without `-v`. Arguably intentional for warnings, but it's the only non-`-v` output besides the final skip message — make it explicit (`verbose=True` with a comment) or thread the flag through.

### L2. `api_request` mutates the caller's params dict

`params["format"] = "json"` (`wikibackground.py:101`) modifies the dict passed in. Harmless today (all call sites pass fresh literals) but a classic latent bug. Use `params = {**params, "format": "json"}`.

### L3. Uncapped `Retry-After`

`_retry_after_seconds` (`wikibackground.py:83-91`) honors the server's `Retry-After` verbatim. A pathological response (`Retry-After: 86400`) would hang a cron run for a day. Cap it (e.g. `min(value, 120)`). Also: the comment says backoff is "1, 2, 4, 8s", but since retries only happen while `attempt < MAX_API_RETRIES - 1`, the maximum sleep is `2**2 = 4s` — comment is off by one.

### L4. `"File:"` prefix stripped with `replace()`, filename not sanitized

`title.replace("File:", "")` (`wikibackground.py:766`) removes the substring *anywhere* in the title, not just the prefix. MediaWiki title rules make `/`, `..`, and embedded `File:` essentially impossible, so this isn't exploitable today, but the filename comes from remote data and goes straight into `cache_dir / filename`. Cheap hardening: use prefix-only stripping (`title[5:]` after a `startswith` check, or `removeprefix` on 3.9+) and reject/replace any `/` or leading `.` in the result.

### L5. GNOME `file://` URI is not percent-encoded

`set_wallpaper_gnome` builds `f"file://{image_path}"` (`wikibackground.py:213`). Underscore-normalized Commons filenames rarely contain URI-special characters, but a `%` or `#` in a filename would produce a broken URI. `_current_wallpaper_filename_gnome` already `unquote`s on the read side (`:325`), so the write side should `urllib.parse.quote(str(image_path))` for symmetry.

### L6. `detect_desktop()` runs up to twice per invocation

The main path calls it once to validate (`wikibackground.py:715`) and `set_wallpaper` calls it again (`:292`); each call may spawn `pgrep`. Trivially fixed by passing the detected desktop into `set_wallpaper`.

### L7. A fresh 500-member page is fetched even when the current one has plenty of untried candidates

In `find_suitable_image`, after sampling 20 of up to 500 fresh candidates, the remaining ~480 are discarded and the next loop iteration issues a new `categorymembers` request (`wikibackground.py:578-590`). Re-sampling the in-memory remainder first (one extra `imageinfo` call, zero extra `categorymembers` calls) finds an image with fewer requests — aligned with the project's stated goal of being kind to Wikimedia's servers.

### L8. Favorite + blocklist can both be set on the same entry

The flags are mutually exclusive per run, but running `--favorite` then `--blocklist` on the same wallpaper sets both fields. Result: `clear_cache` keeps the file forever (favorite wins at `:469`) while selection never shows it (blocklist wins at `:726-735`) — a zombie file. Consider having each mark clear the other field.

### L9. `--skip-if-running` with CLI patterns can false-match a concurrent instance of itself

`_ancestor_pids` correctly excludes self and ancestors, but a *different* concurrently running instance that was passed `--skip-if-running deadlock.exe` on its command line will match the pattern `deadlock.exe` and cause this run to skip. The file-based mode (`run_skip.csv`) doesn't have this problem. Worth a note in the README, or skip any process whose cmdline contains the script's own filename.

### L10. `--favorite`/`--blocklist` match by basename only, and GNOME read path checks only `picture-uri`

`mark_current_wallpaper` compares basenames (`wikibackground.py:444-448`), so a manually-set wallpaper from another directory that happens to share a name with a logged download gets tagged. And `_current_wallpaper_filename_gnome` reads only `picture-uri` (`:311`) — fine for script-set wallpapers (the script sets both keys), but if a user manually changed only `picture-uri-dark` while in dark mode, the wrong image is tagged. Both are edge cases; a path-aware comparison (does the URI's parent match the cache dir?) would close the first one.

### L11. Unknown category aliases fail with a misleading error

A typo like `-c naturee` is passed through as a literal category name (`wikibackground.py:710`), yielding "Error: no suitable image found." A friendlier check: if the first batch returns zero members, suggest that the category may not exist and list the known aliases.

---

## Documentation nits

- **`README.md:34`** — clone URL is the `YOUR_USER` placeholder while `DEFAULT_USER_AGENT` (`wikibackground.py:23`) and `user_agent.txt.example` both point at `github.com/BryanRacic/wikibackground`. Use the real URL.
- **`README.md:13,21`** — Python version claim (see H2).
- **`README.md:151`** — states favorites are kept, which is currently contradicted by H1; whichever way H1 is resolved, make the docs match.
- `--picture-option` help text says "GNOME picture option" (`wikibackground.py:625`) but it applies to Xfce too (via `XFCE_STYLE_MAP`); the README table has the same GNOME-only phrasing at line 81.

---

## Things checked that are fine

- Retry/backoff logic re-raises correctly after the final attempt; the loop cannot fall through and return `None`.
- `_ancestor_pids` parses `/proc/<pid>/stat` correctly, including comms containing spaces/parens (splits on last `)`), and terminates at PID 1 (ppid 0).
- Title normalization mapping in `get_image_infos` is built in the correct direction (`to → from`).
- `--cache-ratio` semantics match the documentation (`random.random() >= ratio` → P(download) = ratio; 1.0 always downloads, 0.0 always reuses).
- XFCE style integer mapping matches xfdesktop's `image-style` enum (GNOME "wallpaper" = tiled = 2).
- GVariant quote-stripping handles both single- and double-quoted gsettings output, with escape unwrapping.
- Blocklisted titles remain in `seen_titles` after `--clear-cache` (rows preserved), so they can never be re-downloaded — matches docs.
- `.gitignore` correctly excludes `user_agent.txt` (contact info) and `__pycache__/`.
- `nargs="*"` vs `None` distinction for `--skip-if-running` (flag absent vs. flag with no args) is handled correctly.

---

## Suggested priority order

1. **H1** — stop deleting favorites (small, user-visible data loss).
2. **H2 / M1** — one-line fixes (README version, missing import).
3. **M4** — atomic downloads via temp file + rename (prevents corrupt wallpapers).
4. **M3** — atomic log writes (prevents history/blocklist loss).
5. **M2** — plain-line parsing for `run_skip.csv`.
6. **M5 / L7** — random category start point + in-batch resampling (better variety, fewer API calls).
7. The remaining low-severity items opportunistically.
