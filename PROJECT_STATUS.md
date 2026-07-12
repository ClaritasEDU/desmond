# Desmond - Project Status

> **Repository:** `github.com/christreadaway/desmond`
> **Category:** Infrastructure
> **Local Path:** `~/desmond/`

## Overall Progress: ~95%

v1 shipped (cross-platform message **text** exporters) plus attachment
archiving with three-way verify. This session: a **full code review with all
critical/major bugs fixed**, a **federation module** (merge two people's
exports into one shared, consent-based archive), and an **optional
consolidate mode** (one `PERSONAL_ARCHIVE.md` from messages + calendar +
contacts + calls). Text messages remain the default use case.

## What's Working
- **⭐ One-shot full exporter** (`desmond_export.py`) — whole history into one
  browsable archive (inline media, pagination, local + Google Drive,
  three-way verified, PII-safe run logs). Still the headline path.
- **iMessage exporter** (`imessage_exporter.py`) — **fixed this session:**
  default (incremental) runs now actually produce `messages.json` /
  `SUMMARY.md` (state bug), incremental runs merge instead of overwriting
  history, `attributedBody` text is decoded (modern macOS messages no longer
  dropped), DB opened strictly read-only, legacy/corrupt timestamps handled,
  `--full` no longer duplicates markdown day files.
- **Attachment archiver + 3-way verify** (`imessage_attachments.py`) —
  **fixed this session:** verify can no longer report ✅ over truncated Drive
  copies (size-checked; failed mirrors rolled back + reported), incremental
  state can't skip unarchived rows anymore, `--full` keeps manifest history,
  name+size collisions can't swallow attachments, `--photos-videos` verify
  filters by type, manifest/state writes are atomic.
- **Browser picker** (`imessage_picker.py`) — **fixed this session:**
  same-origin check on the API (blocks cross-site export triggering), empty
  selection no longer exports everything, filename-extension XSS closed, CSV
  formula injection neutralized, duplicate attachment copies on re-run fixed,
  big-history load sped up via name caching.
- **Windows (iPhone) exporter** — **fixed this session:** now reads iOS 10+
  sharded backups (was 100% broken on any modern backup), same state/merge
  fixes as the Mac exporter, non-zero exit on failure for Task Scheduler.
- **Android exporter** — **fixed this session:** streams huge MMS backups
  (no more OOM), reads the newest messages file AND the newest calls file,
  survives truncated XML, unknown numbers no longer merge into one
  "(Unknown)" thread, failed/draft messages counted as outgoing.
- **NEW: Federation** (`desmond_federate.py`) — merges two people's exports
  (e.g. husband + wife) into `Desmond_Federated_Archive/` with the mutual
  thread deduplicated and rebuilt from both phones. Consent enforced;
  importable by other apps (`federate(..., consented=True)`); stdlib only.
- **NEW: Optional consolidate mode** (`desmond_consolidate.py`) — ONE
  `PERSONAL_ARCHIVE.md` from selectable sources: messages, calendar (.ics —
  Google Calendar incl. its .zip export, Microsoft Outlook, Apple Calendar),
  contacts (.vcf), call logs (Android). Interactive picker or `--sources`;
  NOT part of any default flow.
- **Web setup guide** (`index.html`) — navigation bug fixed (intro cards were
  permanently hidden), plus `desmond.sh` sync-watcher fixes (real 12-min
  stall window, hard error when chat.db is unreadable instead of a fake
  "SYNC COMPLETE").

## What's Broken
- Nothing known broken. (This session's review fixed 3 critical and ~12 major
  issues — see SESSION_NOTES 2026-07-12 for the full list.)

## What's In Progress / Needs Real-Mac Verification
- All fix and feature work is validated by synthetic-DB tests (9 suites, all
  passing) but **not yet run against a real Mac** (chat.db, Google Drive,
  browser, `sips`). First real run must happen on Chris's Mac.

## Tech Stack
- Python 3 (stdlib only — sqlite3, shutil, http.server, xml, zipfile).
  No external dependencies.

## Next Steps
1. On the Mac: `cd ~/desmond && git pull`, run `python3 desmond_export.py`
   and the picker — confirm the review fixes against the real database.
2. Try `python3 desmond_consolidate.py` with a real Google Calendar export
   (.zip) and an iCloud contacts .vcf.
3. When both of your exports exist, try
   `python3 desmond_federate.py "Chris=..." "Kate=..."`.
4. Merge `claude/app-review-federation-export-bmony0` to main; delete branch.

## Blockers
- Real-Mac verification (build container has no Messages DB, Drive, or GUI).

## Last Session
- **Date:** 2026-07-12
- **Branch:** `claude/app-review-federation-export-bmony0`
- **Summary:** Full-repo code review via 4 parallel review passes; fixed all
  confirmed critical/major findings (never-written messages.json state bug on
  Mac+Windows, iOS 10+ backup layout, verify-lies-about-Drive, incremental
  state skipping attachments, picker CSRF/XSS/CSV-injection, Android OOM +
  calls-file handling, desmond.sh false "complete", and more). Built
  `desmond_federate.py` (consent-based two-person archive merge, importable
  by other apps) and `desmond_consolidate.py` (optional one-file .md personal
  archive with selectable sources incl. Google/Outlook calendar .ics).
  Added 2 new test suites; all 9 suites pass.
