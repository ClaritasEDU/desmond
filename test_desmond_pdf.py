#!/usr/bin/env python3
"""Synthetic tests for desmond_pdf.py (PDF export of conversation transcripts)."""

import os
import stat
import sys
import tempfile

import desmond_pdf as dp
import imessage_picker as pick


def main():
    failures = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            failures.append(msg)

    # --- the transcript page carries the Save-as-PDF machinery ---------------
    rec = {"person": "Mom", "date": "2024-01-01", "time": "10:00:00",
           "timestamp": "2024-01-01T10:00:00", "sender": "Mom", "is_from_me": False,
           "message_type": "text", "text_plain": "hi",
           "media": [{"category": "video", "path": "v.mov", "name": "v.mov", "missing": False},
                     {"category": "photo", "path": "p.jpg", "name": "p.jpg", "missing": False}]}
    html = pick.render_html([rec], ["Mom"], "summary", "oldest")
    check('id="pdf"' in html, "transcript has a Save as PDF button")
    check("@media print" in html, "transcript has a print stylesheet")
    check("page-break-inside:avoid" in html, "messages are not split across pages")
    check("printcap\">[video: " in html and "printcap\">[audio: " in html, "videos/audio get a printed caption")
    check("overflow-wrap:anywhere" in html, "long unbroken text wraps instead of running off the page")
    check("waitForImages" in html and 'loading="eager"' in html, "photos are force-loaded before print")
    check('get("print")==="1"' in html, "?print=1 auto-renders every message for headless printing")

    with tempfile.TemporaryDirectory() as tmp:
        # --- conversation discovery -------------------------------------------
        for name in ("Mom", "Dad", "Weekend Crew"):
            d = os.path.join(tmp, "conversations", name)
            os.makedirs(d)
            with open(os.path.join(d, "conversation.html"), "w") as f:
                f.write(html)
        rows = dp.list_conversations(tmp)
        check([r[0] for r in rows] == ["Dad", "Mom", "Weekend Crew"], "lists every conversation, sorted")
        rows = dp.list_conversations(tmp, ["mom", "crew"])
        check([r[0] for r in rows] == ["Mom", "Weekend Crew"], "name picks are case-insensitive substrings")
        check(dp.list_conversations(os.path.join(tmp, "nope")) == [], "missing archive → empty list")

        # --- browser detection -------------------------------------------------
        check(dp.find_browser(os.path.join(tmp, "no-such-browser")) is None, "explicit missing browser → None")
        fake = os.path.join(tmp, "fake-chrome")
        with open(fake, "w") as f:
            # Writes a tiny PDF wherever --print-to-pdf points, records its args.
            f.write("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in --print-to-pdf=*) printf '%%PDF-1.4 fake' > \"${a#--print-to-pdf=}\";; esac; done\n"
                    "echo \"$@\" > \"$(dirname \"$0\")/args.txt\"\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        check(dp.find_browser(fake) == fake, "explicit browser path is honoured")

        # --- the print command -------------------------------------------------
        cmd = dp.pdf_command(fake, os.path.join(tmp, "conversations", "Mom", "conversation.html"),
                             os.path.join(tmp, "out.pdf"))
        check("--headless=new" in cmd and any(a.startswith("--print-to-pdf=") for a in cmd), "headless print flags present")
        check(cmd[-1].startswith("file://") and cmd[-1].endswith("?print=1"), "opens the transcript with ?print=1")
        check("--no-pdf-header-footer" in cmd, "no browser header/footer on the pages")

        # --- conversion + CLI ---------------------------------------------------
        err = dp.convert(fake, os.path.join(tmp, "conversations", "Mom", "conversation.html"),
                         os.path.join(tmp, "conversations", "Mom", "conversation.pdf"))
        check(err is None, "convert() succeeds with a browser that writes a PDF")
        rc = dp.main(["--archive", tmp, "--browser", fake])
        check(rc == 0, "main() converts the whole archive")
        for name in ("Mom", "Dad", "Weekend Crew"):
            check(os.path.exists(os.path.join(tmp, "conversations", name, "conversation.pdf")),
                  f"conversation.pdf written for {name}")
        rc = dp.main(["--archive", tmp, "--browser", fake, "Dad"])
        check(rc == 0, "re-run skips PDFs that already exist")

        # --- failure paths ------------------------------------------------------
        broken = os.path.join(tmp, "broken-chrome")
        with open(broken, "w") as f:
            f.write("#!/bin/bash\necho 'boom' >&2; exit 3\n")
        os.chmod(broken, os.stat(broken).st_mode | stat.S_IEXEC)
        err = dp.convert(broken, os.path.join(tmp, "conversations", "Mom", "conversation.html"),
                         os.path.join(tmp, "x.pdf"))
        check(err and "exited 3" in err and "boom" in err, "browser failure is reported with its stderr")
        rc = dp.main(["--archive", tmp, "--browser", broken, "--force"])
        check(rc == 1, "main() exits 1 when conversions fail")
        rc = dp.main(["--archive", os.path.join(tmp, "empty")])
        check(rc == 1, "no conversations → exit 1 with guidance")
        rc = dp.main(["--archive", tmp, "--browser", os.path.join(tmp, "missing")])
        check(rc == 2, "no browser → exit 2 with the in-browser alternative")

    print()
    if failures:
        print(f"{len(failures)} FAILED")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
