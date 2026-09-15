#!/bin/bash
#
# DESMOND — ONE-SHOT (Mac)
# Double-click this file in Finder to export your WHOLE message history:
# text + real photos/videos inline, saved locally AND to Google Drive, then
# verified (device vs local vs Drive). Opens the browsable archive when done.
#
# This is a launcher only. The actual work is done by desmond_export.py, which
# reads Messages READ-ONLY and never changes anything.
#
# Options (advanced — normally just double-click):
#   --photos-videos   images & videos only
#   --newest          newest messages first
#   --no-drive        keep the local copy only (skip Google Drive)
#   --retry           re-run up to 3 passes until local & Drive match (--retry N for more)
#
# To stop while it runs: press Control + C, then close the window.
#

# Always run from the folder this file lives in (dummy-proof: no cd needed).
cd "$(dirname "$0")" || {
    echo "Could not find the Desmond folder. Move this file back into ~/desmond and try again."
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
}

# --- Logging: capture everything the launcher sees to a local file ----------
# NOTE: desmond_export.py already writes its own PII-SAFE, shareable log to
# ~/Downloads/Desmond_Logs/ (counts/timings/errors, no message text or names).
# THIS launcher log is a RAW console capture kept only on your Mac — it may
# contain conversation names, so don't share it. To share diagnostics, send
# the app's PII-safe .log/.json from ~/Downloads/Desmond_Logs/ instead.
LOG_DIR="$HOME/Downloads/Desmond_Logs"
mkdir -p "$LOG_DIR" 2>/dev/null
STAMP="$(date +%Y%m%d_%H%M%S)"
LAUNCHER_LOG="$LOG_DIR/oneshot_mac_launcher_${STAMP}.log"

# Send all output (stdout + stderr) to the screen AND to the log file.
# (tee -i ignores Control+C so the last lines still reach the log.)
exec > >(tee -a -i "$LAUNCHER_LOG") 2>&1

# Control+C: say so plainly instead of dying mid-line. Runs are safe to re-run.
trap 'echo ""; echo "  Stopped by you (Control+C). Nothing is damaged — double-click again to re-run."; sleep 0.3; exit 130' INT

echo ""
echo "=============================================================="
echo "  DESMOND — ONE-SHOT (Mac)"
echo "  Exporting your whole message history: text + media, inline."
echo "  Launcher log: $LAUNCHER_LOG"
echo "=============================================================="
echo ""

# --- Find a REAL Python 3 (skips Apple's "install developer tools?" stub) ---
# Shared helper; also turns on live (unbuffered) output so progress shows as it
# happens instead of in 8 KB bursts, and UTF-8 output so emoji can't crash it.
if ! . "./desmond_find_python.sh"; then
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi
echo "Using Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# --- Full Disk Access pre-check ---------------------------------------------
# Without it, macOS hides ~/Library/Messages from Terminal entirely. Catch that
# BEFORE starting so the fix is obvious (and open the exact settings pane).
if [ -e "$HOME/Library/Messages/chat.db" ] && [ ! -r "$HOME/Library/Messages/chat.db" ] \
   || { [ ! -e "$HOME/Library/Messages/chat.db" ] && [ -d "$HOME/Library" ] && [ ! -r "$HOME/Library/Messages" ]; }; then
    echo "Terminal is not allowed to read your Messages database yet"
    echo "(or Messages has never been set up on this Mac)."
    echo ""
    echo "One-time fix:"
    echo "  1) In the System Settings window that just opened (Privacy & Security >"
    echo "     Full Disk Access), turn ON 'Terminal'. If Terminal isn't listed,"
    echo "     click '+' and add /Applications/Utilities/Terminal.app."
    echo "  2) QUIT Terminal completely (Cmd+Q), then double-click this file again."
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null
    read -n 1 -s -r -p "Press any key to close..."
    exit 3
fi

# --- Keep the Mac awake for the whole export (big histories take a while) ---
if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i -w $$ &
fi

# --- Run the one-shot full export -------------------------------------------
"$PY" desmond_export.py "$@"
STATUS=$?

echo ""
if [ $STATUS -eq 0 ]; then
    echo "=============================================================="
    echo "  DONE. Your archive is in the folder printed just above"
    echo "  (~/Downloads/Desmond_Message_Archive unless you passed --dest)."
    echo "  index.html in that folder lists every conversation."
    echo "  \"See you in another life, brother.\""
    echo "=============================================================="
elif [ $STATUS -eq 3 ]; then
    echo "=============================================================="
    echo "  Terminal needs Full Disk Access (see the message above)."
    echo "  Turn it on, QUIT Terminal (Cmd+Q), then double-click this again."
    echo "=============================================================="
elif [ $STATUS -eq 4 ]; then
    echo "=============================================================="
    echo "  Archive BUILT, but not yet complete: some items are still"
    echo "  offloaded in iCloud or Google Drive is still uploading."
    echo "  Open VERIFY_REPORT.md in the archive folder for the list, download"
    echo "  those items in Messages, then double-click this again (add --retry"
    echo "  to loop up to 3 passes)."
    echo "=============================================================="
elif [ $STATUS -eq 2 ]; then
    echo "=============================================================="
    echo "  Unknown option (exit code 2). The options are listed at the top"
    echo "  of this file: --photos-videos --newest --no-drive --retry"
    echo "=============================================================="
else
    echo "=============================================================="
    echo "  Something went wrong (exit code $STATUS)."
    echo "  Most common fix: give Terminal Full Disk Access —"
    echo "  System Settings > Privacy & Security > Full Disk Access,"
    echo "  turn on Terminal, quit Terminal, then double-click this again."
    echo ""
    echo "  For help, share the PII-safe log from ~/Downloads/Desmond_Logs/"
    echo "=============================================================="
fi

echo ""
read -n 1 -s -r -p "Press any key to close this window..."
echo ""
sleep 0.3   # let the log writer (tee) flush the last lines
exit $STATUS
