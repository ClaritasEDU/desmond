# Desmond — The One-Shot (Mac vs PC)

Two double-clickable launchers that run the whole export in one shot. Pick the
file for your machine. Both keep a log so problems are easy to diagnose.

## Mac

1. Open the `~/desmond` folder in Finder.
2. Double-click **`desmond_oneshot_mac.command`**.
   - First time only: if macOS blocks it, right-click the file > Open > Open.
3. When it finishes, open `~/Downloads/Desmond_Message_Archive/index.html`.

What you get: your WHOLE history, text plus the real photos and videos shown
inline in date order, saved locally AND mirrored to Google Drive, then verified
(device vs local vs Drive). Reads Messages read-only.

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
