#!/usr/bin/env python3
"""
Synthetic test for desmond_export.py — the single unified exporter.

Builds a fake chat.db with two conversations (one with a real photo, one with an
offloaded attachment), runs the full export, and checks: an index.html linking
per-conversation transcripts, inline media in a transcript, the attachment copied
in, a manifest, and a clean three-way verify after mirroring to a fake Drive.
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime

import desmond_export as dx
import imessage_attachments as att

APPLE = 978307200


def ns(dt):
    return int((dt.timestamp() - APPLE) * 1_000_000_000)


def build_db(db_path, specs):
    """specs: list of (chat_identifier, handle_id_str, filename, mime, transfer)."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text, date, is_from_me,
            handle_id, associated_message_type, balloon_bundle_id, attributedBody);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier, display_name);
        CREATE TABLE chat_message_join (chat_id, message_id);
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename, mime_type,
            transfer_name, total_bytes);
        CREATE TABLE message_attachment_join (message_id, attachment_id);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id);
        CREATE TABLE chat_handle_join (chat_id, handle_id);
    """)
    when = ns(datetime(2024, 7, 4, 18, 30))
    for i, (chat_id, handle, fn, mime, transfer) in enumerate(specs, start=1):
        c.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (i, handle))
        c.execute("INSERT INTO chat (ROWID, chat_identifier, display_name) VALUES (?, ?, NULL)",
                  (i, chat_id))
        c.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (i, i))
        c.execute("INSERT INTO message (ROWID, text, date, is_from_me, handle_id) "
                  "VALUES (?, ?, ?, 0, ?)", (i, f"msg {i}", when, i))
        c.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (i, i))
        c.execute("INSERT INTO attachment (ROWID, filename, mime_type, transfer_name, "
                  "total_bytes) VALUES (?, ?, ?, ?, ?)", (i, fn, mime, transfer, 1500))
        c.execute("INSERT INTO message_attachment_join (message_id, attachment_id) "
                  "VALUES (?, ?)", (i, i))
    conn.commit()
    conn.close()


def main():
    failures = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        adir = os.path.join(tmp, "Att")
        os.makedirs(adir)
        photo = os.path.join(adir, "beach.jpg")
        with open(photo, "wb") as f:
            f.write(b"\xff\xd8\xff" + b"\x00" * 400)

        db = os.path.join(tmp, "chat.db")
        build_db(db, [
            ("+15550000001", "+15550000001", photo, "image/jpeg", "beach.jpg"),
            ("+15550000002", "+15550000002", "/gone/clip.mov", "video/quicktime", "clip.mov"),
        ])

        out = os.path.join(tmp, "Archive")
        res = dx.build_archive(db, out, order="oldest", verbose=False)
        check(res["conversations"] == 2, f"two conversations exported (got {res['conversations']})")
        check(res["attachments"] == 1, f"one real attachment copied (got {res['attachments']})")
        check(res["offloaded"] == 1, f"one offloaded flagged (got {res['offloaded']})")

        check(os.path.exists(os.path.join(out, "index.html")), "wrote index.html (single entry point)")
        index = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        check("Message Archive" in index and "conversations/" in index,
              "index lists conversations with links")
        check("const PAGE = 100" in index and 'id="more"' in index,
              "index paginates (default 100)")

        convs = os.path.join(out, "conversations")
        conv_htmls = [os.path.join(r, "conversation.html")
                      for r, _d, files in os.walk(convs) for f in files
                      if f == "conversation.html"]
        check(len(conv_htmls) == 2, f"a transcript per conversation (got {len(conv_htmls)})")
        joined = "".join(open(h, encoding="utf-8").read() for h in conv_htmls)
        check('<img class="att"' in joined, "media shown inline in a transcript")
        check('id="toggle"' in joined, "transcripts have the date order toggle")
        check("PAGE_SIZE = 100" in joined, "transcripts paginate (default 100)")

        media_files = [f for r, _d, files in os.walk(convs) for f in files
                       if "/attachments/" in os.path.join(r, f).replace(os.sep, "/")]
        check(len(media_files) == 1, f"the real attachment was copied in (got {len(media_files)})")

        with open(os.path.join(out, "attachments.json")) as f:
            man = json.load(f)
        rec = man["attachments"][0]
        check(rec.get("attachment_id") is not None and rec.get("saved_path"),
              "manifest record has attachment_id + saved_path (powers verify)")

        # Mirror to a fake Google Drive and run the three-way verify.
        drive = os.path.join(tmp, "GDrive", dx.ARCHIVE_NAME)
        att.mirror_tree(out, drive)
        v = att.verify_archive(db_path=db, output_dir=out, drive_mirror=drive,
                               expect_drive=True, verbose=False)
        check(v["complete"] is True, "three-way verify complete (device+local+drive)")
        check(v["in_local"] == 1 and v["in_drive"] == 1,
              f"verify: 1 local + 1 drive (got {v['in_local']}/{v['in_drive']})")
        check(v["offloaded"] == 1, "verify reports the 1 offloaded item")
        check(os.path.exists(os.path.join(out, "VERIFY_REPORT.md")), "wrote VERIFY_REPORT.md")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
