#!/bin/bash
# Desmond Attachment Archiver - copies your real photos/videos/files out of
# Messages into a browsable folder (ideal for Google Drive).
#
# Double-click this file, or run:  ./desmond_attachments.sh
#
# Tips:
#   ./desmond_attachments.sh --dry-run        # see how much space it'll take first
#   ./desmond_attachments.sh --full           # copy everything
#   ./desmond_attachments.sh --photos-videos  # images + videos only
#   ./desmond_attachments.sh --dest "/Users/you/Library/CloudStorage/GoogleDrive-…/My Drive/Messages"

cd "$(dirname "$0")" || exit 1

echo "Desmond - Attachment Archiver"
echo "Copies the actual photos/videos/files from Messages into a folder you can keep."
echo ""

# Default to a full export if no arguments are given (most people want everything).
if [ "$#" -eq 0 ]; then
  python3 imessage_attachments.py --full
else
  python3 imessage_attachments.py "$@"
fi
