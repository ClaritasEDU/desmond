#!/bin/bash
# Desmond - One-shot full export: text + media inline, saved locally AND to
# Google Drive, then verified. Open the index.html it creates.
#
# Double-click this file, or run:  ./desmond_export.sh
# Options:  ./desmond_export.sh --photos-videos | --newest | --no-drive | --retry

cd "$(dirname "$0")" || exit 1

echo "Desmond - exporting your whole message history (text + media, inline)…"
echo ""

python3 desmond_export.py "$@"
