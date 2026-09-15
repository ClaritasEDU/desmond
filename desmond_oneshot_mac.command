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
#   --retry           loop until local & Drive match
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
exec > >(tee -a "$LAUNCHER_LOG") 2>&1

echo ""
echo "=============================================================="
echo "  DESMOND — ONE-SHOT (Mac)"
echo "  Exporting your whole message history: text + media, inline."
echo "  Launcher log: $LAUNCHER_LOG"
echo "=============================================================="
echo ""

# --- Make sure Python 3 is available ----------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 was not found on this Mac."
    echo ""
    echo "Fix it in one of these ways, then double-click this file again:"
    echo "  1) Open Terminal and run:  xcode-select --install"
    echo "     (installs Apple's Command Line Tools, which include python3)"
    echo "  2) Or install Python from https://www.python.org/downloads/macos/"
    echo ""
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

# --- Run the one-shot full export -------------------------------------------
python3 desmond_export.py "$@"
STATUS=$?

echo ""
if [ $STATUS -eq 0 ]; then
    echo "=============================================================="
    echo "  DONE. Your archive is in:  ~/Downloads/Desmond_Message_Archive"
    echo "  Open index.html in that folder to browse every conversation."
    echo "  \"See you in another life, brother.\""
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
exit $STATUS
