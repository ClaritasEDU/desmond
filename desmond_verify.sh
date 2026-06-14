#!/bin/bash
# Desmond Verify - checks that ALL your message attachments actually made it into
# the archive (which should live in Google Drive). Copies nothing.
#
# Double-click this file, or run:  ./desmond_verify.sh
# Point at a specific folder:      ./desmond_verify.sh --dest "/path/to/Google Drive/Desmond_Message_Attachments"

cd "$(dirname "$0")" || exit 1

echo "Desmond - Verifying your attachment backup…"
echo ""

python3 imessage_attachments.py --verify "$@"
