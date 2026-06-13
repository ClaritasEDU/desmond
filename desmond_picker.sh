#!/bin/bash
# Desmond Picker - launches the browser interface for exporting messages.
# Double-click this file, or run:  ./desmond_picker.sh

cd "$(dirname "$0")" || exit 1

echo "Starting Desmond Picker..."
echo "Your browser will open in a moment."
echo ""

python3 imessage_picker.py
