#!/bin/bash
# Desmond - One-shot full export: text + media inline, saved locally AND to
# Google Drive, then verified. Open the index.html it creates.
#
# Double-click this file in Finder, or run:  ./desmond_export.command
# Options:  ./desmond_export.command --photos-videos | --newest | --no-drive | --retry
#
# (desmond_oneshot_mac.command is the fuller launcher: it also keeps a log,
#  checks Full Disk Access up front and keeps the Mac awake.)

cd "$(dirname "$0")" || exit 1
. "./desmond_find_python.sh" || exit 1

echo "Desmond - exporting your whole message history (text + media, inline)…"
echo ""

"$PY" desmond_export.py "$@"
