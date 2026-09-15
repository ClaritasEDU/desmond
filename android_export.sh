#!/bin/bash
# Desmond - Android SMS Exporter for macOS
# Exports your SMS/MMS from Android backup files to AI-ready formats

echo ""
echo "============================================================"
echo "  Desmond - Android SMS Exporter"
echo '  "We have to push the button."'
echo "============================================================"
echo ""

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Find a real Python 3 (skips Apple's "install developer tools?" stub)
. "$SCRIPT_DIR/desmond_find_python.sh" || exit 1

# Run the exporter
"$PY" "$SCRIPT_DIR/android_sms_exporter.py" "$@"
