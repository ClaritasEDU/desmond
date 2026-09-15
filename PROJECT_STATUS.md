# Desmond - Project Status

> **Repository:** `github.com/ClaritasEDU/desmond`
> **Category:** Infrastructure
> **Local Path:** `~/desmond/`

## Overall Progress: ~97%

v1 shipped (cross-platform message **text** exporters) plus attachment
archiving with three-way verify, federation, consolidate mode, family
federation, the no-files web wizard, the PersonalCRM bridge, and per-platform
one-shot launchers. **This session (2026-09-15, part 2): a full code review of
the whole app, Mac first** — 55 verified findings, ~50 fixed on the Mac path
with a regression test each (495 passing assertions across 13 suites), the PC
improvements planned in `ROADMAP.md`. Recommendation unchanged: **run it on the
Mac.**

## What's Working
- **⭐ Mac one-shot** — `desmond_oneshot_mac.command` (double-click in Finder)
  → `desmond_export.py`: whole history, text + real photos/videos inline in
  date order, local + Google Drive, three-way verified, PII-safe log. Now:
  finds a real Python 3 (skips Apple's stub, Finder's minimal PATH), checks
  Full Disk Access first and opens the right Settings pane, shows progress
  live, keeps the Mac awake, survives Ctrl+C, and reports honest exit codes
  (0 done · 1 error · 3 needs Full Disk Access · 4 built but incomplete /
  offloaded iCloud items · 130 stopped). `ONESHOT.md` explains each.
- **Conversation naming is correct** — unknown numbers keep their full
  identifier and type "direct" everywhere (exporter, picker, archiver, one-shot);
  they were previously collapsed to their last 4 digits and merged.
- **iMessage exporter** (`imessage_exporter.py`) — lenient UTF-8, dedupes
  messages in two chats, U+FFFC stripped, Sequoia emoji tapbacks + stickers,
  legacy state files don't duplicate `messages.json`, corrupt state recovers,
  atomic state writes, byte-safe folder names, case-insensitive APFS handled,
  markdown-safe multi-line text, 0x82 attributedBody lengths, FDA detection.
- **Attachment archiver + 3-way verify** (`imessage_attachments.py`) — never
  mirrors an archive into itself, archives `.rtfd`/`.pages` bundles, byte-safe
  long names, one copy per attachment even across merged chats, data copied +
  size-checked with best-effort metadata (cloud volumes), verify rejects
  truncated local files, Drive auto-detect never returns the CloudStorage root
  (localized "My Drive" handled), archived-then-offloaded originals stay
  archived, NULL-MIME HEIC/CAF/MOV classified by extension + UTI,
  `.pluginPayloadAttachment` names preserved, copy errors surfaced.
- **Browser picker** (`imessage_picker.py`, `desmond_picker.command`) —
  redaction scrubs all four files; XSS via conversation names closed; `Host`
  validated (DNS rebinding); `<!--<script` safe; port fallback 8765–8785;
  Save exports exactly what was previewed; Drive failure doesn't fail a
  finished local export; empty type selection exports nothing.
- **Launchers** — `desmond_export.command`, `desmond_attachments.command`,
  `desmond_verify.command`, `desmond_picker.command` (renamed from `.sh`;
  Finder only runs `.command`), all sourcing `desmond_find_python.sh`.
  `setup_imessage_exporter.sh` runs the first export before loading the hourly
  agent and tells you the exact Python to grant Full Disk Access.
  `desmond.sh` accepts `346,000`, selects the iMessage pane before Sync Now,
  and stops with the Automation/Accessibility fix instead of fake stalls.
- **PersonalCRM bridge** (`desmond_crm_export.py`) — `--out DIR` works,
  atomic write, Full Disk Access detection in auto mode.
- **In-memory source readers** (`desmond_sources.py`) — `messages_db_state()`
  + `detect_available()["mac_messages_needs_full_disk_access"]` so the wizard
  and CRM CLI can show the right remedy.
- Federation, family federation, the family web wizard, Android over USB,
  calendar sign-in, consolidate mode, PII-safe run logging — unchanged, all
  suites green.

## What's Broken
- Nothing known broken on the Mac path.

## What's In Progress / Needs Real-Mac Verification
- Everything above is proven on synthetic chat.db files and container stubs
  (including the Full Disk Access paths run as a non-root user). A real run on
  Chris's Mac is the next step: `caffeinate`, `sips` HEIC→JPG, the Settings
  deep link, Gatekeeper on the `.command`, real Google Drive for desktop.
- **PC path: planned, not built.** `ROADMAP.md` P1–P20: the PC one-shot still
  prints DONE on failure, accepts the Microsoft Store Python stub, doesn't
  decode `attributedBody` (iOS 16+ text lost), doesn't detect encrypted
  backups, and has no media/Drive/verify parity.
- Calendar sign-in buttons stay hidden until the one-time Google/Microsoft
  app registrations are done (steps at the top of `desmond_calendar_auth.py`).

## Tech Stack
- Python 3 (stdlib only; 3.8+, Xcode CLT 3.9 fine). bash 3.2-compatible shell.

## Next Steps
1. Real run on the Mac: double-click `~/desmond/desmond_oneshot_mac.command`;
   share `~/Downloads/Desmond_Logs/*.json`.
2. Merge `claude/charming-newton-k8s1b8` to `main` (online) and delete it.
3. PC Phase 0 (`ROADMAP.md`, ½ day) if a PC-only household needs it; decide
   HEIC-on-Windows policy before Phase 2.
4. One-time calendar app registrations, then run the family wizard for real.
5. PersonalCRM bridge real run + import check.

## Blockers
- Real-device verification (container has no Messages DB, phones, or GUI).

## Last Session
- **Date:** 2026-09-15 (part 2)
- **Branch:** `claude/charming-newton-k8s1b8`
- **Summary:** Full code review (five parallel reviewers, every finding
  reproduced), then ~50 Mac-path fixes with regression tests, launcher
  hardening, docs updated line by line, and `ROADMAP.md` with the ordered PC
  plan. Details in SESSION_NOTES.
- **Previous session (2026-09-15, part 1):** per-platform one-shot launchers
  (`desmond_oneshot_mac.command`, `desmond_oneshot_pc.bat`, `ONESHOT.md`).
- **Previous session (2026-07-29):** PersonalCRM bridge (`desmond_crm_export.py`).
