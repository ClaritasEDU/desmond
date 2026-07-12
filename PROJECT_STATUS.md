# Desmond - Project Status

> **Repository:** `github.com/christreadaway/desmond`
> **Category:** Infrastructure
> **Local Path:** `~/desmond/`

## Overall Progress: ~95%

v1 shipped (cross-platform message **text** exporters) plus attachment
archiving with three-way verify, federation, and optional consolidate mode.
This session (2026-07-12 part 3): **family federation**
(`desmond_family.py`) — federate two parents' messages AND calendars and
report the **coverage gaps** (the dentist text only one parent got, the
event on only one calendar). This is the engine ParentPoint will surface in
a later session. Text messages remain the default use case.

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
- **NEW: Family federation / coverage gaps** (`desmond_family.py`) — the
  ParentPoint engine. Federates two parents' **messages + calendars**
  (calendars straight from each parent's private iCal link or .ics) and
  diffs the two views into `FAMILY_GAPS.md` + `family.json`
  (`desmond-family/1`): calendar events only on one calendar, incoming
  texts only one parent received, and threads (dentist/coach/school) that
  only ever text one parent. Noise-controlled (5-min SMS lag tolerance,
  same-day loose event matching, 30-day + upcoming default window,
  `--keyword`, `--same-thread`). Pure in-memory API for apps:
  `federate_family_data()` / `parse_calendar()`. iPhone-first; Android
  notes (RCS gap, NotificationListenerService future exporter) documented
  in the module docstring + README. Consent enforced like federation.
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
- All fix and feature work is validated by synthetic-DB tests (8 test
  scripts, all passing) but **not yet run against a real Mac** (chat.db, Google Drive,
  browser, `sips`). First real run must happen on Chris's Mac.

## Tech Stack
- Python 3 (stdlib only — sqlite3, shutil, http.server, xml, zipfile).
  No external dependencies.

## Next Steps
1. Real-world family-federation run: two actual exports + two real secret
   iCal links → `cd ~/desmond && python3 desmond_family.py "Chris=…"
   "Kate=…" --calendar "Chris=…" --calendar "Kate=…"` — sanity-check
   FAMILY_GAPS.md noise level and tune matching if needed.
2. On the Mac: `cd ~/desmond && git pull`, run `python3 desmond_export.py`
   and the picker — confirm the earlier review fixes against the real
   database.
3. ParentPoint session (parentpoint repo): surface
   `federate_family_data()` output — gap cards/notifications for parents.
4. Merge `claude/desmond-parentpoint-federation-qsqif6` to main; delete
   branch.

## Blockers
- Real-Mac verification (build container has no Messages DB, Drive, or GUI);
  real-data gap-noise tuning needs two genuine exports.

## Last Session
- **Date:** 2026-07-12 (part 3)
- **Branch:** `claude/desmond-parentpoint-federation-qsqif6`
- **Summary:** Built `desmond_family.py` — family federation for the
  ParentPoint use case. Merges two parents' messages (reusing
  `federate_data`) and calendars (reusing the consolidate ICS/feed code),
  then diffs the two views into a coverage-gap report: calendar events only
  one parent has, texts only one parent received, threads that only ever
  text one parent. One CLI command → `Desmond_Family_Archive/` with
  FAMILY_GAPS.md; pure in-memory `federate_family_data()` for ParentPoint.
  iPhone-first with written Android notes (RCS, notification capture).
  New 32-check test suite; all 10 suites pass. Parts 1–2 same day: full
  code review + federation/consolidate (see SESSION_NOTES).
