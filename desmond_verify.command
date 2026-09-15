#!/bin/bash
# Desmond Verify - three-way check that ALL your message attachments made it
# from the device into the local archive AND the Google Drive mirror.
# Copies nothing.
#
# Double-click this file in Finder, or run:  ./desmond_verify.command
# Point at a specific Drive folder:  ./desmond_verify.command --drive "/path/to/My Drive/Desmond_Message_Attachments"
# Point at a specific local archive: ./desmond_verify.command --dest "/path/to/local/archive"

cd "$(dirname "$0")" || exit 1
. "./desmond_find_python.sh" || exit 1

echo "Desmond - Verifying your attachment backup…"
echo ""

"$PY" imessage_attachments.py --verify "$@"
