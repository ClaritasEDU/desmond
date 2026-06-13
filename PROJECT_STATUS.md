# Desmond - Project Status

> **Repository:** `github.com/christreadaway/desmond`
> **Category:** Infrastructure
> **Local Path:** `~/desmond/`

## Overall Progress: ~85%

v1 shipped (cross-platform message exporters). Now adding a friendlier,
privacy-first interface layer on top.

## What's Working
- **iMessage exporter** (`imessage_exporter.py`) — full + incremental exports to
  JSON/CSV/markdown, contact name lookup, reactions, attachments, effects.
- **Windows (iPhone backup)** and **Android (SMS XML)** exporters.
- **Web setup guide** (`index.html`) — decision tree by device.
- **NEW: Browser message picker** (`imessage_picker.py` + `desmond_picker.sh`) —
  pick one or more people, choose a time range, preview, trim, then export.
  Privacy controls: preview-before-save with per-message deselect, redaction
  (phones/emails/addresses/IDs), content-type + direction filters, most-recent-N
  cap, keyword include/exclude. Local-only server, nothing uploaded.

## What's Broken
- Nothing known broken.

## What's In Progress
- **Picker needs a real-Mac smoke test** — code compiles and core logic is
  unit-tested, but it has not yet run against an actual `~/Library/Messages/chat.db`
  (no Messages DB available in the build container).

## Tech Stack
- Python 3 (stdlib only — sqlite3, http.server). No external dependencies.
- See CLAUDE.md for details.

## Next Steps
1. Run `cd ~/desmond && git pull && python3 imessage_picker.py` on the Mac and
   verify people list, preview, redaction, and export end-to-end.
2. Merge `claude/kind-turing-t9kcaa` → `main` on github.com.
3. Consider follow-on privacy features: anonymize names, view-only mode,
   group-chat guard, per-export "what's included" receipt.

## Blockers
- Final verification requires Chris's Mac (build container has no Messages DB).

## Last Session
- **Date:** 2026-06-13
- **Branch:** `claude/kind-turing-t9kcaa` (to be merged to main on GitHub by Chris)
- **Summary:** Built the browser-based message picker and added preview-before-save,
  redaction, content/direction/keyword/cap filters, and multi-person selection.
