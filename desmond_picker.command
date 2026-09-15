#!/bin/bash
# Desmond Picker - launches the browser interface for exporting messages.
# Double-click this file in Finder, or run:  ./desmond_picker.command

cd "$(dirname "$0")" || exit 1
. "./desmond_find_python.sh" || exit 1

echo "Starting Desmond Picker..."
echo "Your browser will open in a moment."
echo ""

"$PY" imessage_picker.py "$@"
STATUS=$?
if [ $STATUS -eq 3 ]; then
    echo ""
    echo "Terminal needs Full Disk Access to read Messages. Turn it on in"
    echo "System Settings > Privacy & Security > Full Disk Access, QUIT Terminal"
    echo "(Cmd+Q), then double-click this file again."
fi
exit $STATUS
