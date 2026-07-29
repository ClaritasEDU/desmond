# Desmond - Project Status

> **Repository:** `github.com/christreadaway/desmond`
> **Category:** Infrastructure
> **Local Path:** `~/desmond/`

## Overall Progress: ~96%

v1 shipped (cross-platform message **text** exporters) plus attachment
archiving with three-way verify, federation, consolidate mode, family
federation, and the no-files web wizard. This session (2026-07-29): a
**one-command bridge to PersonalCRM** (`desmond_crm_export.py`) — read texts
from any source and write a `personalcrm_import.json` the companion CRM app
imports, so mined messages (with the cell numbers already attached) become
real, analyzable conversations there. Additive and self-contained; the
ParentPoint/federation work is untouched. Text messages remain the default
use case.

## What's Working
- **⭐ PersonalCRM bridge (NEW)** (`desmond_crm_export.py`) — one command reads
  your texts from any source (this Mac's Messages, a plugged-in iPhone's local
  backup, a plugged-in Android over USB, or an existing export) and writes a
  single `personalcrm_import.json`. Then in PersonalCRM: Settings → Text Message
  Import → upload. Cell numbers/emails ride along in each message's `address`,
  so the CRM assigns numbers to people automatically. Reuses `desmond_sources` +
  `desmond_federate.parse_export`; device readers imported lazily; pure
  `build_crm_export()` is fully unit-tested. Stdlib only; nothing uploaded.
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
  ParentPoint engine. Federates two parents' **messages + calendars** and
  diffs the two views into `FAMILY_GAPS.md` + `family.json`
  (`desmond-family/1`): calendar events only on one calendar, incoming
  texts only one parent received, and threads (dentist/coach/school) that
  only ever text one parent. Noise-controlled (5-min SMS lag tolerance,
  same-day loose event matching, 30-day + upcoming default window,
  `--keyword`, `--same-thread`). Pure in-memory API for apps:
  `federate_family_data()` / `parse_calendar()`. Consent enforced.
- **NEW: The no-files family web wizard** (`desmond_family_web.py`) —
  `python3 desmond_family_web.py` → private local page (127.0.0.1):
  consent on screen → attach messages by plugging phones in (this Mac's
  Messages direct; iPhone local backup read in place — Mac + Windows;
  Android live over USB; or drop a messages.json / SMS Backup & Restore
  XML) → calendars by **Google/Microsoft sign-in** (no iCal links) → gap
  report rendered in the browser. All in memory; disk only on explicit
  Save. Mixed iPhone+Android households verified in tests.
- **NEW: Android over USB** (`android_adb_exporter.py`) — reads SMS/MMS
  text straight off a plugged-in Android via adb content queries
  (read-only; contact names from the phone's own address book; human
  fix-it errors; RCS documented as unreadable-without-root everywhere).
- **NEW: In-memory source readers** (`desmond_sources.py`) — Mac chat.db,
  iPhone backups (incl. encrypted detection + fix hint), Android USB,
  uploads; all land in the standard export shape; `detect_available()`
  powers the wizard.
- **NEW: Calendar sign-in** (`desmond_calendar_auth.py`) — Google OAuth
  (loopback+PKCE) and Microsoft device-code flows, stdlib only; tokens
  cached chmod-600 for one-click reuse; needs a one-time free app
  registration (click-by-click steps in the module docstring); iCal link
  demoted to fallback.
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
- All fix and feature work is validated by synthetic tests (13 test
  scripts, all passing; wizard driven end-to-end over real HTTP with
  stubbed phones/providers) but **not yet run against real devices** —
  a real Mac, a real iPhone backup, a real Android over adb, and real
  Google/Microsoft sign-ins all need Chris's machine.
- Calendar sign-in buttons stay hidden until the one-time Google/Microsoft
  app registrations are done (steps at the top of
  `desmond_calendar_auth.py`; IDs go in `~/.desmond/oauth_clients.json`).

## Tech Stack
- Python 3 (stdlib only — sqlite3, shutil, http.server, xml, zipfile).
  No external dependencies.

## Next Steps
1. **PersonalCRM bridge real run:** `cd ~/desmond && python3 desmond_crm_export.py`
   on Chris's Mac/phone, then import the `personalcrm_import.json` into
   PersonalCRM and sanity-check that contacts, cell numbers, and threads land
   correctly.
2. One-time calendar app registrations (Google Cloud Console + Azure —
   click-by-click steps at the top of `desmond_calendar_auth.py`), put the
   IDs in `~/.desmond/oauth_clients.json`, then run the wizard for real:
   `cd ~/desmond && python3 desmond_family_web.py`.
3. Real-device pass on Chris's machine: this Mac's Messages, a plugged-in
   iPhone's backup, an Android over USB — sanity-check FAMILY_GAPS.md
   noise and tune matching if needed.
4. When the parentpoint branch is resolved (separate session, parentpoint
   repo): wire ParentPoint to desmond_sources + desmond_calendar_auth +
   `federate_family_data()`.
5. Merge `claude/desmond-parentpoint-federation-qsqif6` to main; delete
   branch.

## Blockers
- Real-device verification (container has no Messages DB, phones, or GUI).
- Calendar sign-in needs the one-time app registrations (free, ~10 min).

## Last Session
- **Date:** 2026-07-29
- **Branch:** `claude/project-status-assessment-pi7y12`
- **Summary:** Added `desmond_crm_export.py`, a one-command bridge that reads
  texts from any source (Mac Messages / iPhone backup / Android USB / an
  existing export) and writes a `personalcrm_import.json` the companion
  PersonalCRM app imports — turning mined messages (cell numbers already
  attached via each message's `address`) into real conversations there. Reuses
  `desmond_sources` + `desmond_federate.parse_export`; pure `build_crm_export()`
  unit-tested (`test_desmond_crm_export.py`). Additive, self-contained, stdlib
  only — no existing module touched, so the parentpoint/federation work is
  unaffected. All 13 test suites pass.
- **Previous session (2026-07-12, parts 3–5):** family federation
  (`desmond_family.py`), the no-files web wizard (`desmond_family_web.py`),
  Android-over-USB, in-memory source readers, calendar sign-in, and a
  comprehensive audit (19 bugs fixed with regression tests). See SESSION_NOTES.
