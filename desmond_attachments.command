#!/bin/bash
# Desmond Attachment Archiver - copies your real photos/videos/files out of
# Messages into a browsable folder, mirrored to Google Drive automatically.
#
# Double-click this file in Finder, or run:  ./desmond_attachments.command
#
# Tips:
#   ./desmond_attachments.command --dry-run        # see how much space it'll take first
#   ./desmond_attachments.command --full           # copy everything
#   ./desmond_attachments.command --photos-videos  # images + videos only
#   ./desmond_attachments.command --drive "~/Library/CloudStorage/GoogleDrive-…/My Drive"
#   (Google Drive is auto-detected; --drive only overrides WHICH Drive folder.
#    Don't point --dest inside Drive — the archive would be uploaded twice.)

cd "$(dirname "$0")" || exit 1
. "./desmond_find_python.sh" || exit 1

echo "Desmond - Attachment Archiver"
echo "Copies the actual photos/videos/files from Messages into a folder you can keep."
echo ""

# Default to a full export if no arguments are given (most people want everything).
if [ "$#" -eq 0 ]; then
  "$PY" imessage_attachments.py --full
else
  "$PY" imessage_attachments.py "$@"
fi
