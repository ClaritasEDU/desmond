#!/bin/bash
# Desmond - Save every conversation as a PDF (photos inline), using Chrome /
# Edge / Chromium already on this Mac. Double-click, or:
#   ./desmond_pdf.command            # all conversations
#   ./desmond_pdf.command "Mom"      # only folders whose name contains Mom
# No browser installed? Open any conversation.html and click "Save as PDF".

cd "$(dirname "$0")" || exit 1
. "./desmond_find_python.sh" || exit 1

"$PY" desmond_pdf.py "$@"
STATUS=$?
echo ""
read -n 1 -s -r -p "Press any key to close this window..."
echo ""
exit $STATUS
