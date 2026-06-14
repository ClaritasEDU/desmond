# Desmond - Project Status

> **Repository:** `github.com/christreadaway/desmond`
> **Category:** Infrastructure
> **Local Path:** `~/desmond/`

## Overall Progress: ~90%

v1 shipped (cross-platform message **text** exporters). Now: **real attachment
archiving** (the actual photos/videos/files) and an upgraded picker that shows
media **inline**, **date/time ordered** with a newest↔oldest toggle — all
Google Drive-ready.

## What's Working
- **iMessage exporter** (`imessage_exporter.py`) — full + incremental text exports
  to JSON/CSV/markdown, contact name lookup, reactions, attachments (as labels),
  effects. **NEW:** saves locally **and** copies to Google Drive
  (`Desmond_Messages_Export/`) so the text archive lives in both places
  (`--no-drive` / `--drive PATH` to control).
- **Browser message picker** (`imessage_picker.py`) — pick/search people, choose a
  range, preview, trim, redact. Copies the **real photos/videos/files** and renders
  them **inline** in a `conversation.html` transcript with a live **newest/oldest**
  order toggle; filenames lead with date/time + the people in the chat. **NEW:**
  each pick is saved **locally and mirrored to Google Drive**, then **verified**
  (UI shows `local N/N, Drive N/N`) with a per-export `VERIFY_REPORT.md`.
- **NEW: Attachment archiver** (`imessage_attachments.py` + `desmond_attachments.sh`)
  — copies the **real** photos/videos/audio/files out of Messages into an
  organized, browsable archive that lives in **both** places: a **local** primary
  copy **and** a **Google Drive** mirror (incremental `mirror_tree`). Per-contact
  folders, `YYYY-MM-DD_HHMM_people_name` filenames, cumulative JSON/CSV/MD manifest.
  Reads the DB read-only; incremental; `--dry-run` sizing; `--photos-videos`;
  `--no-drive` / `--drive PATH`; `--retry [N]` loops until complete.
- **NEW: Three-way verification + report** (`--verify` / `desmond_verify.sh`) —
  reconciles **device (Messages) vs local vs Google Drive**, prints per-place
  counts, and writes `VERIFY_REPORT.md` + `verify_diff.json` listing exactly
  what's missing where (and what's offloaded in iCloud). Runs automatically after
  a backup; `--retry` drives it to a full archive; exit code is scriptable.
- **Windows (iPhone backup)** and **Android (SMS XML)** text exporters.
- **Web setup guide** (`index.html`).

## What's Broken
- Nothing known broken.

## What's In Progress / Needs Real-Mac Verification
- **Attachment archiver + 3-way verify** — logic validated by a synthetic-DB test
  (`test_imessage_attachments.py`, 29 checks pass, incl. mirror, three-way verify,
  Drive/local tamper, retry-restore, report contents) but **not yet run against a
  real `~/Library/Messages/chat.db`** or a real Google Drive folder (none in the
  Linux container). First real run + verify must happen on Chris's Mac.
- **Picker inline media + Drive mirror/verify** — validated by
  `test_imessage_picker.py` (23 checks pass); still needs a real-Mac/browser run to
  confirm HEIC→JPG via `sips`, video playback, the order toggle, and the Drive
  mirror against a live DB.

## Tech Stack
- Python 3 (stdlib only — sqlite3, shutil, http.server). No external dependencies.

## Next Steps
1. On the Mac: `cd ~/desmond && git pull` then `python3 imessage_attachments.py --dry-run`
   to preview total size, then `--full` to archive (pointed at Google Drive).
2. Run `python3 imessage_picker.py`, pick a person with photos, save, and open
   `conversation.html` — confirm inline images/videos and the order toggle.
3. Decide: add attachment extraction to the **Windows (iPhone backup)** and
   **Android (MMS base64)** paths so non-Mac users get media too.
4. Optional: an inline-media HTML view for the full bulk archive as well.

## Blockers
- Final verification of both the archiver and picker requires Chris's Mac
  (build container has no Messages DB or Attachments folder).

## Last Session
- **Date:** 2026-06-14
- **Branch:** `claude/nifty-cori-60alcq`
- **Summary:** Built the iMessage **attachment archiver** (real photos/videos/files
  → Google Drive-ready archive, incremental, dry-run sizing, offloaded reporting),
  upgraded the **picker** to copy real attachments + render them **inline** with a
  **newest/oldest** toggle and date/time+people filenames, and added **backup
  verification** (`--verify`/`desmond_verify.sh`) that confirms everything reached
  the Drive archive, made the **text export live local + Google Drive**, made
  **attachments live local + Drive** too, and added **three-way verification**
  (device vs local vs Drive) with a **diff report** (`VERIFY_REPORT.md`) and a
  **`--retry`** loop to drive to a full archive, and gave the **picker** the same
  local + Drive + verify treatment. Three test suites (59 checks total, all
  passing); docs updated.
