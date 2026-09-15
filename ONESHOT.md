# Desmond — The One-Shot (Mac vs PC)

Two double-clickable launchers that run the whole export in one shot. Pick the
file for your machine. Both keep a log so problems are easy to diagnose.

## Mac

1. Open the `~/desmond` folder in Finder.
2. Double-click **`desmond_oneshot_mac.command`**.
   - It finds a working Python 3 for you, checks that Terminal has Full Disk
     Access (and opens that Settings pane if not), keeps the Mac awake while it
     runs, and shows progress live.
3. When it finishes, it opens the archive; it lives in
   `~/Downloads/Desmond_Message_Archive/index.html`.

### If it won't open (first time only)

- **"Apple could not verify…" / "cannot be opened"** (a downloaded copy is
  quarantined): System Settings > Privacy & Security > scroll down > **Open
  Anyway**. On older macOS, right-click the file > Open > Open.
- **Terminal opens and says "permission denied"** (a downloaded copy lost its
  run bit). In Terminal, paste:

  ```bash
  cd ~/desmond && chmod +x desmond_oneshot_mac.command && xattr -d com.apple.quarantine desmond_oneshot_mac.command
  ```

- **"Terminal needs Full Disk Access"**: turn on Terminal in the Settings pane
  it opened, then **quit Terminal completely (Cmd+Q)** and double-click again.
- **"a working Python 3 was not found"**: in Terminal run
  `xcode-select --install`, wait for it to finish, then double-click again.

### What the end of the run means

- **DONE** — complete: everything on the device is in the local archive and on
  Google Drive.
- **Archive BUILT, but not yet complete** — the archive is usable, but some
  items are still offloaded in iCloud or Google Drive hasn't finished
  uploading. `VERIFY_REPORT.md` in the archive folder lists exactly what.
  Download the offloaded items in Messages, then run again (add `--retry` to
  loop up to 3 passes).
- **Stopped by you (Control+C)** — nothing is damaged; run again to rebuild
  (already-copied attachments are reused).

What you get: your WHOLE history, text plus the real photos and videos shown
inline in date order, saved locally AND mirrored to Google Drive, then verified
(device vs local vs Drive). Reads Messages read-only. Conversations with numbers
that aren't in your Contacts are named by the full number (never merged).

PDF of a conversation: open it in the archive and click **🖨 Save as PDF**
(then choose Save as PDF in the print window). All conversations at once:
double-click `desmond_pdf.command` (needs Chrome, Edge or Chromium installed).

Logs: the app writes a PII-safe, shareable log to `~/Downloads/Desmond_Logs/`
(counts, timings, errors — no message text or names). The launcher also writes a
raw console log there (`oneshot_mac_launcher_*.log`) that stays on your Mac only.

## PC (Windows)

1. First make an UNENCRYPTED iPhone backup on the PC: connect iPhone > iTunes
   (Win 10) or Apple Devices (Win 11) > select device > "Back Up Now" > make sure
   "Encrypt local backup" is OFF.
2. Open the `desmond` folder in File Explorer.
3. Double-click **`desmond_oneshot_pc.bat`**.
4. When it finishes, open `Documents\iMessages_Export\SUMMARY.md`.

What you get: your full message history exported to browsable Markdown plus
AI-ready JSON/CSV and a summary. No inline media archive, no Google Drive mirror,
no three-way verify (those are Mac-only for now).

Logs: written to `Documents\Desmond_Logs\oneshot_pc_*.log`.

## Recommendation: run it on the Mac

The Mac one-shot is the better path, and it isn't close:

1. **It reads your messages live.** The Mac pulls straight from the Messages
   database. The PC can only read an iPhone backup you made first — an extra
   step, and it silently fails if the backup is encrypted (the default when
   you've ever set a backup password).
2. **You get the real photos and videos, inline.** The Mac archive shows media
   in each thread in date order and copies the actual files. The PC export is
   text plus a summary only.
3. **It's backed up and verified for you.** The Mac mirrors everything to Google
   Drive and runs a three-way check (device vs local vs Drive). The PC does not.
4. **Fewer ways to fail.** The PC path depends on iTunes/Apple Devices, backup
   location, and encryption settings all being right. The Mac path is one
   double-click; the only common snag is granting Terminal Full Disk Access.

Use the PC only when a Mac isn't available. If you have both, run the Mac
one-shot and treat the PC export as a fallback.
