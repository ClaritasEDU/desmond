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
  effects.
- **Browser message picker** (`imessage_picker.py`) — pick/search people, choose a
  range, preview, trim, redact. **NEW:** copies the **real photos/videos/files**
  and renders them **inline** in a `conversation.html` transcript with a live
  **newest/oldest** order toggle; saves to **Google Drive** (auto-detected) or
  Downloads; attachment filenames lead with date/time + the people in the chat.
- **NEW: Attachment archiver** (`imessage_attachments.py` + `desmond_attachments.sh`)
  — copies the **real** photos/videos/audio/files out of Messages into an
  organized, browsable archive (per-contact folders, `YYYY-MM-DD_HHMM_name`
  filenames) with JSON/CSV/Markdown manifests so you can find & retrieve any file.
  Auto-detects a **Google Drive for desktop** folder (or use `--dest`). Reads the
  DB read-only; incremental re-runs; `--dry-run` size preview; `--photos-videos`
  filter; reports MISSING (iCloud-offloaded) files so nothing is lost before
  freeing phone space.
- **Windows (iPhone backup)** and **Android (SMS XML)** text exporters.
- **Web setup guide** (`index.html`).

## What's Broken
- Nothing known broken.

## What's In Progress / Needs Real-Mac Verification
- **Attachment archiver** — logic validated by a synthetic-DB test
  (`test_imessage_attachments.py`, 14 checks pass) but **not yet run against a
  real `~/Library/Messages/chat.db`** (no Messages DB in the Linux build
  container). First real run must happen on Chris's Mac.
- **Picker inline media** — validated by `test_imessage_picker.py` (17 checks
  pass); still needs a real-Mac/browser run to confirm HEIC→JPG via `sips`,
  video playback, and the order toggle against a live DB.

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
  → Google Drive-ready archive with manifests, incremental re-runs, dry-run sizing,
  offloaded-file reporting), then upgraded the **picker** to copy real attachments
  and render them **inline** in a `conversation.html` with a **newest/oldest** order
  toggle, save to Google Drive (or Downloads), and name files by date/time + people.
  Added launcher + two synthetic tests (31 checks total, all passing); updated docs.
