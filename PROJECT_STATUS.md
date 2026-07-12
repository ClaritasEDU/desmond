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
- **NEW: Federation, online-app-ready** (`desmond_federate.py`) — merges two
  people's exports (e.g. husband + wife) with the mutual thread deduplicated
  and rebuilt from both phones; consent enforced. The whole merge is a pure
  in-memory API for the upcoming online app: `parse_export()` validates
  uploads (bytes/str/dict), `federate_data(..., consented=True,
  consent_records=[...])` returns the merged archive + rendered markdown as
  strings, no filesystem; payload versioned `desmond-federated/1`. CLI
  unchanged (writes `Desmond_Federated_Archive/`). Stdlib only.
- **NEW: Optional consolidate mode with ONLINE calendars**
  (`desmond_consolidate.py`) — ONE `PERSONAL_ARCHIVE.md` from selectable
  sources: messages, calendar, contacts (.vcf), call logs (Android).
  Calendar is online-first: paste your **Google Calendar secret iCal
  address** or **Outlook published ICS link** once (`--calendar-url` +
  `--remember`, or the interactive prompt) and every run fetches it live —
  no .zip export dance. Links stored chmod-600 in
  `~/.desmond/calendar_feeds.json`, never printed in full;
  `--forget-calendar-urls` clears. Exported .ics/.zip files remain the
  offline fallback. NOT part of any default flow.
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
2. Connect a real calendar: `python3 desmond_consolidate.py --sources
   calendar --calendar-url "SECRET_ICAL_LINK" --remember` (Google: Settings →
   Integrate calendar → Secret address; Outlook: Publish a calendar → ICS).
3. Next session: build the online federation app on top of
   `federate_data()` / `parse_export()` (library side is ready).
4. Merge `claude/app-review-federation-export-bmony0` to main; delete branch.

## Blockers
- Real-Mac verification (build container has no Messages DB, Drive, or GUI).

## Last Session
- **Date:** 2026-07-12 (two parts, same branch)
- **Branch:** `claude/app-review-federation-export-bmony0`
- **Summary:** Part 1 — full-repo code review via 4 parallel review passes;
  fixed all confirmed critical/major findings (never-written messages.json
  state bug on Mac+Windows, iOS 10+ backup layout, verify-lies-about-Drive,
  incremental state skipping attachments, picker CSRF/XSS/CSV-injection,
  Android OOM + calls-file handling, desmond.sh false "complete", and more);
  built federation + optional consolidate mode with 2 new test suites.
  Part 2 — refactored federation into a pure in-memory API
  (`federate_data`/`parse_export` + consent trail) ready for the online
  federation app coming in a future session, and made calendar integration
  online-first: Google/Outlook private iCal feed URLs fetched live,
  remembered securely (chmod 600), no .zip exports needed. All 9 suites pass.
