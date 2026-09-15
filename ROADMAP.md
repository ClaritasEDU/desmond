# Desmond — Roadmap

> Written 2026-09-15 after a full code review + test pass. Mac first (that is
> the recommended path — see `ONESHOT.md`); the PC work is planned here but
> not yet built. Numbers refer to the review findings recorded in
> `SESSION_NOTES.md` (2026-09-15 entry).

## Where we are

- **Mac one-shot** (`desmond_oneshot_mac.command` → `desmond_export.py`) is the
  headline path and got the bulk of this session's fixes: correct naming of
  unknown numbers, Full Disk Access detection with the right fix-it, live
  progress, honest exit codes, Ctrl+C safety, keep-awake, HEIC/MOV with no MIME
  type, `--photos-videos` verify, Drive-folder detection on non-English Macs,
  bundle attachments, long non-ASCII names, invalid UTF-8 rows, corrupt state
  files, redaction covering every output file, and the picker's stored-XSS +
  Host-header holes. All 13 test suites pass with new regression tests.
- **PC one-shot** (`desmond_oneshot_pc.bat` → `imessage_exporter_windows.py`)
  works for text from an unencrypted iPhone backup, but has the known gaps
  below. It has NOT been changed this session (review only).
- Real-device verification (a real Mac, real iPhone backup, real Android over
  USB, real Google/Microsoft sign-in) still needs Chris's machine.

## Next up (Mac) — small, in priority order

| # | Item | Why | Effort |
|---|------|-----|--------|
| M1 | Real run of `desmond_oneshot_mac.command` on the Mac; read `~/Downloads/Desmond_Logs/*.json` | Everything here is proven on synthetic chat.db files only | 30 min |
| M0 | ~~Progress lines during the silent database read and attachment copy~~ done 2026-09-15 | The Terminal window sat silent for minutes on a big history | done |
| M0b | ~~PDF export of a conversation with photos inline~~ done 2026-09-15 (`Save as PDF` button + `desmond_pdf.py` batch) | Requested after the first real run | done |
| M2 | Incremental day-file ordering in `imessage_exporter.py` (late-synced iCloud messages land at the bottom of a day file) | Cosmetic in Markdown; JSON/CSV are sorted | 1 hr |
| M3 | Contacts lookup fallback when AddressBook access is denied — prompt once with the `osascript` Contacts trigger from the launcher | Unknown-number naming is now correct, but names are nicer | 1 hr |
| M4 | Optional: Pillow-free HEIC → JPG only via `sips` (already done) — document that on a non-Mac browser HEIC originals won't render | Docs only | 15 min |
| M5 | `desmond.sh`: replace AppleScript UI clicking (fragile across macOS versions) with a plain "count messages + tell the user" loop, or gate it behind a `--click` flag | UI-scripting breaks with every Settings redesign | 2 hr |
| M6 | Unit-test style: convert the print/check test scripts to `unittest` so a single failing assertion is isolated and `python3 -m unittest` discovers them | Test hygiene | 2 hr |

## PC plan (planned, not built) — ordered

Findings from the PC review, each confirmed by reading the code or a synthetic
backup repro. **Recommendation stands: run it on the Mac.** Build these only if
a PC-only household needs it.

### Phase 0 — make the PC launcher tell the truth (½ day)

| # | Item | File | Severity |
|---|------|------|----------|
| P1 | `desmond_oneshot_pc.bat` prints DONE even when the export fails: `powershell -Command "python … | Tee-Object"` returns Tee-Object's exit code, not Python's. Drop the PowerShell tee; run Python directly, `set STATUS=%ERRORLEVEL%`, and let Python own logging (P7). | `desmond_oneshot_pc.bat:52-53` | HIGH |
| P2 | Python detection passes on the Microsoft Store stub (`WindowsApps\python.exe`, exit 9009) and fails on `py`-launcher-only installs. Resolver: try `py -3`, `python`, `python3`; accept the first where `-c "import sys,sqlite3; sys.exit(0 if sys.version_info>=(3,8) else 1)"` returns 0. Same block in `desmond_windows.bat`, `setup_windows.bat`, `android_export_windows.bat`. | all `.bat` | HIGH |
| P3 | `set PYTHONUTF8=1` before launching + `sys.stdout.reconfigure(errors="replace")` in `main()` — an emoji device name or any ✅/⚠️ glyph crashes under a pipe or Task Scheduler (cp1252). | `.bat`, `imessage_exporter_windows.py:83` | MEDIUM |
| P4 | Log timestamp: `%date%` token slicing assumes `MM/DD/YYYY`; en-US returns `Tue 09/15/2026`. Use `powershell Get-Date -Format yyyyMMdd_HHmmss` or name the log from Python. | `desmond_oneshot_pc.bat:43-44` | LOW |
| P5 | Forward `%*` so `--backup "C:\path"` works from the one-shot; quote-safe `SCRIPT_DIR` (apostrophes in OneDrive folder names). | `desmond_oneshot_pc.bat:52` | LOW |
| P6 | `setup_windows.bat`: drop the admin gate (schtasks for the current user doesn't need it; UAC under another admin creates the task for the wrong profile); add `setlocal`; check `%ERRORLEVEL%` after the initial run; run the task via `pythonw.exe` / `cmd /c … >> log` so no console pops hourly. | `setup_windows.bat:13-20,36,49,77` | MEDIUM |

### Phase 1 — exporter correctness (1 day)

| # | Item | File | Severity |
|---|------|------|----------|
| P7 | Wire `desmond_log.RunLogger` into `imessage_exporter_windows.main()` exactly as `desmond_export.py` does (PII-safe `.log/.json`), and `os.startfile(OUTPUT_DIR)` behind an `--open` flag the one-shot passes. | `imessage_exporter_windows.py` | HIGH |
| P8 | Decode `attributedBody` (iOS 16+ leaves `text` NULL for most messages → currently exported as "[unknown message type]" / dropped from Markdown). Reuse `imessage_exporter.decode_attributed_body` (move it to a small shared module so Windows doesn't import the Mac exporter). | `imessage_exporter_windows.py:302-317, 494-516` | HIGH |
| P9 | Encrypted-backup detection: read `Manifest.plist` `IsEncrypted` in discovery (skip/flag with the fix-it text), raise early in `desmond_sources.read_iphone_backup`, catch `sqlite3.DatabaseError` on the first query → print the untick-encryption steps and `sys.exit(2)`; one-shot maps 2 → "backup is encrypted". Today it picks the newest backup even if encrypted and dies with a raw traceback. | `imessage_exporter_windows.py:76-93, 326, 943-953` | HIGH |
| P10 | Dedupe messages joined to two chats (`seen_rowids`, as `desmond_sources` already does). | `imessage_exporter_windows.py:302-317, 494-516` | MEDIUM |
| P11 | Temp DB copies: per-run `tempfile.mkdtemp()`, copy `sms.db` + `-wal`/`-shm` (fileIDs `cd47480f…`, `36ce215d…`) so WAL-only rows aren't lost, `try/finally` cleanup (today a failed run leaves a full copy of the message DB in `%TEMP%`, and two overlapping runs collide). | `imessage_exporter_windows.py:112-113, 293-294, 485-486` | MEDIUM |
| P12 | Reactions 2006/3006 (emoji tapbacks) and 1000 (stickers) in both maps. | `imessage_exporter_windows.py:346-359, 545-558` | LOW |
| P13 | `test_imessage_exporter_windows.py` (none exists): sharded layout, attributedBody-only text, double chat join, encrypted backup, WAL present, non-ASCII device name with piped stdout. Reuse `make_chat_db` from `test_desmond_sources.py`. | new | MEDIUM |

### Phase 2 — media parity with the Mac (2–3 days)

| # | Item | Notes |
|---|------|-------|
| P14 | Real attachments from the backup (`MediaDomain`): `attachment.filename` is `~/Library/SMS/Attachments/xx/yy/GUID/name`; backup fileID = `sha1("MediaDomain-Library/SMS/Attachments/…")` at `xx/<hash>`. Prefer `Manifest.db` (`Files` table, plain SQLite when unencrypted; also gives size for verify) with the computed-hash fallback. Implement `backup_attachment_path(backup_dir, filename)` in `desmond_sources`. | HEIC: no `sips` on Windows and browsers can't render HEIC — ship originals, show a link/placeholder; convert only if Pillow + pillow-heif happen to be importable (stdlib-only rule). **Decision for Chris.** |
| P15 | One-shot HTML archive on PC: give `imessage_picker` two seams — an attachment resolver (default `os.path.expanduser`) and an injectable contacts loader (Windows: `imessage_exporter_windows.load_contacts(backup_dir)`) — then `desmond_export.py --backup DIR` reuses `build_archive` unchanged. | |
| P16 | Google Drive on Windows: candidates `[D-Z]:\My Drive`, `%USERPROFILE%\Google Drive\My Drive`, `%USERPROFILE%\My Drive`; `mirror_tree` is already path-agnostic. | |
| P17 | Three-way verify on PC: with P14, "offloaded" simply means "not in the backup" (common with Messages in iCloud). | |

### Phase 3 — polish

- P18 Backup chooser (`--list-backups` / interactive pick) for households with two iPhones.
- P19 OneDrive-aware Documents folder (`SHGetKnownFolderPath(FOLDERID_Documents)` via `ctypes`).
- P20 Docs: README file table + platform table, `ONESHOT.md` PC section once the PC produces `index.html`; note that with "Messages in iCloud" a local backup may hold only a subset of attachments; backup-location comment (`%USERPROFILE%\Apple\MobileSync\Backup` is now the common one on Win 11).

### Decisions for Chris (PC)

1. HEIC on Windows: originals-only (stdlib rule) vs an optional Pillow dependency.
2. Keep the PowerShell tee in the PC one-shot? Recommendation: no — Python owns logging (P7).
3. Keep Task Scheduler support at all? The one-shot may be enough; the scheduler adds hidden-window/log complexity.

## Larger bets (unchanged from before)

- ParentPoint: wire `desmond_sources` + `desmond_calendar_auth` +
  `federate_family_data()` into the online app once that branch is resolved.
- PersonalCRM bridge real run (`desmond_crm_export.py`) and import check.
- One-time Google/Microsoft app registrations so the calendar sign-in buttons
  appear in the family wizard.
