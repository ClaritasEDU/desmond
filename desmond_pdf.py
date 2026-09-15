#!/usr/bin/env python3
"""
Desmond — turn conversation transcripts into PDFs (photos kept inline).

Two ways to get a PDF of a conversation:

1. In the browser: open any conversation.html, click "🖨 Save as PDF" — it
   shows every message, waits for the photos to load, then opens the Print
   window where you choose "Save as PDF". No install, works anywhere.

2. In bulk (this script): converts every conversation in the archive (or the
   ones you name) to conversation.pdf next to its conversation.html, using
   the headless mode of Chrome / Chromium / Edge / Brave already on the Mac.

    python3 desmond_pdf.py                 # every conversation in the archive
    python3 desmond_pdf.py "Mom" "Dad"     # only these (substring match)
    python3 desmond_pdf.py --archive PATH  # a different archive folder

Nothing is uploaded; the browser runs locally with no window.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

ARCHIVE_DEFAULT = os.path.expanduser("~/Downloads/Desmond_Message_Archive")

# Headless-capable browsers, in preference order.
BROWSER_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Arc.app/Contents/MacOS/Arc",
]
BROWSER_NAMES = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave"]


def find_browser(explicit=None):
    """Path to a headless-capable browser, or None."""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for path in BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def list_conversations(archive, picks=None):
    """[(name, conversation.html path)] for every conversation folder, filtered
    by case-insensitive substring when `picks` is given."""
    pattern = os.path.join(archive, "conversations", "*", "conversation.html")
    rows = []
    for html in sorted(glob.glob(pattern)):
        name = os.path.basename(os.path.dirname(html))
        if picks and not any(p.lower() in name.lower() for p in picks):
            continue
        rows.append((name, html))
    return rows


def pdf_command(browser, html_path, pdf_path, budget_ms=20000):
    """The headless print command. ?print=1 makes the page render every
    message and preload the photos before the browser snapshots it."""
    url = "file://" + os.path.abspath(html_path) + "?print=1"
    extra = []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        extra.append("--no-sandbox")   # Chromium refuses to run as root otherwise (CI/containers; never a Mac user)
    return [browser, *extra, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            f"--virtual-time-budget={int(budget_ms)}",   # let lazy images load first
            "--no-pdf-header-footer",
            f"--print-to-pdf={os.path.abspath(pdf_path)}", url]


def render_budget(n_messages=0, n_photos=0):
    """(timeout_seconds, virtual_time_ms) scaled to the export: a 4,000-message
    thread with hundreds of photos needs far more than a 5-minute cap."""
    timeout = int(max(300, 120 + n_messages * 0.15 + n_photos * 1.5))
    budget_ms = int(max(20000, 5000 + n_messages * 10 + n_photos * 150))
    return timeout, budget_ms


def convert(browser, html_path, pdf_path, timeout=300, budget_ms=20000):
    cmd = pdf_command(browser, html_path, pdf_path, budget_ms)
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        return f"browser exited {e.returncode}: {(e.stderr or b'')[-300:].decode('utf-8', 'replace').strip()}"
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
        return "no PDF written"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Save conversation transcripts as PDFs (photos inline).")
    ap.add_argument("names", nargs="*", help="Only conversations whose folder name contains these (default: all).")
    ap.add_argument("--archive", default=ARCHIVE_DEFAULT, help="Archive folder (default ~/Downloads/Desmond_Message_Archive).")
    ap.add_argument("--browser", help="Path to a Chrome/Chromium/Edge binary (default: auto-detect).")
    ap.add_argument("--force", action="store_true", help="Rebuild PDFs that already exist.")
    args = ap.parse_args(argv)

    archive = os.path.expanduser(args.archive)
    rows = list_conversations(archive, args.names or None)
    if not rows:
        print(f"No conversations found under {archive}/conversations/"
              + (f" matching {args.names}" if args.names else "") + ".")
        print("Run the one-shot first (desmond_oneshot_mac.command), then try again.")
        return 1

    browser = find_browser(args.browser)
    if not browser:
        print("No Chrome/Chromium/Edge/Brave found for headless PDF printing.")
        print("Two options:")
        print("  1) Install Google Chrome (free), then run this again.")
        print("  2) Open any conversation.html in Safari and click \"🖨 Save as PDF\" —")
        print("     it shows every message with photos inline, then choose Save as PDF.")
        return 2

    print(f"Using: {browser}")
    print(f"Converting {len(rows):,} conversation(s) in {archive}")
    ok = skipped = failed = 0
    started = time.time()
    for i, (name, html) in enumerate(rows, start=1):
        pdf = os.path.join(os.path.dirname(html), "conversation.pdf")
        if os.path.exists(pdf) and not args.force:
            skipped += 1
            continue
        err = convert(browser, html, pdf)
        if err:
            failed += 1
            print(f"  [{i}/{len(rows)}] ✗ {name}: {err}")
        else:
            ok += 1
            print(f"  [{i}/{len(rows)}] ✓ {name} → conversation.pdf "
                  f"({os.path.getsize(pdf) / 1_048_576:.1f} MB)")
    print(f"\nDone in {time.time() - started:.0f}s: {ok} written, {skipped} already existed, {failed} failed.")
    print("Each PDF sits next to its conversation.html in the archive's conversations/ folder.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
