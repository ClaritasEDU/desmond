#!/usr/bin/env python3
"""
Synthetic test for imessage_attachments.py.

Builds a minimal fake chat.db (matching the columns the archiver queries) plus
real on-disk attachment files in a temp area, then verifies that the archiver:
  - copies files that exist,
  - reports files that are missing (offloaded),
  - writes the JSON/CSV/Markdown manifests,
  - is incremental (a second run copies nothing new),
  - supports --photos-videos filtering and --dry-run.

Runs on any platform (no real Messages DB needed).
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime

import imessage_attachments as att

APPLE_EPOCH = 978307200


def apple_ns(dt):
    return int((dt.timestamp() - APPLE_EPOCH) * 1_000_000_000)


def build_db(db_path, attachments):
    """attachments: list of (filename, mime, transfer_name, total_bytes)."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, date INTEGER,
            is_from_me INTEGER, handle_id INTEGER, attributedBody BLOB);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT,
            display_name TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT,
            mime_type TEXT, transfer_name TEXT, total_bytes INTEGER);
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """
    )
    c.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
    c.execute("INSERT INTO chat (ROWID, chat_identifier, display_name) VALUES "
              "(1, '+15551234567', NULL)")
    c.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1)")
    when = apple_ns(datetime(2024, 1, 15, 9, 32, 0))
    for i, (filename, mime, transfer, total) in enumerate(attachments, start=1):
        c.execute("INSERT INTO message (ROWID, text, date, is_from_me, handle_id) "
                  "VALUES (?, ?, ?, 0, 1)", (i, f"here is file {i}", when))
        c.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (i,))
        c.execute("INSERT INTO attachment (ROWID, filename, mime_type, transfer_name, "
                  "total_bytes) VALUES (?, ?, ?, ?, ?)",
                  (i, filename, mime, transfer, total))
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
        # Make Contacts lookup a no-op so the test is hermetic.
        att.core.CONTACTS_CACHE = {}
        att._contacts_loaded = True

        src_dir = os.path.join(tmp, "Attachments")
        os.makedirs(src_dir)

        photo = os.path.join(src_dir, "IMG_1234.HEIC")
        with open(photo, "wb") as f:
            f.write(b"\x00" * 2048)  # 2 KB fake photo
        video = os.path.join(src_dir, "movie.MOV")
        with open(video, "wb") as f:
            f.write(b"\x00" * 5000)  # ~5 KB fake video
        missing_path = os.path.join(src_dir, "offloaded.HEIC")  # never created

        db_path = os.path.join(tmp, "chat.db")
        build_db(db_path, [
            (photo, "image/heic", "IMG_1234.HEIC", 2048),
            (video, "video/quicktime", "movie.MOV", 5000),
            (missing_path, "image/heic", "offloaded.HEIC", 9999),  # offloaded
        ])

        out = os.path.join(tmp, "archive")

        # --- Full run ---
        res = att.archive_attachments(db_path=db_path, output_dir=out, full=True,
                                      verbose=False)
        check(res["copied_count"] == 2, f"copies 2 existing files (got {res['copied_count']})")
        check(res["missing_count"] == 1, f"reports 1 missing file (got {res['missing_count']})")
        check(res["copied_bytes"] == 7048, f"sums bytes copied (got {res['copied_bytes']})")
        check(os.path.exists(os.path.join(out, "_15551234567",
                                          "2024-01-15_0932_IMG_1234.HEIC"))
              or any("IMG_1234" in p for p in _all_files(out)),
              "photo landed in a per-conversation folder")
        check(os.path.exists(os.path.join(out, "attachments.json")), "wrote attachments.json")
        check(os.path.exists(os.path.join(out, "attachments.csv")), "wrote attachments.csv")
        check(os.path.exists(os.path.join(out, "ATTACHMENTS_INDEX.md")),
              "wrote ATTACHMENTS_INDEX.md")

        with open(os.path.join(out, "attachments.json")) as f:
            manifest = json.load(f)
        statuses = [a["status"] for a in manifest["attachments"]]
        check(statuses.count("copied") == 2, "manifest marks 2 copied")
        check(statuses.count("missing") == 1, "manifest marks 1 missing")
        check(any(a["message_text"] for a in manifest["attachments"]),
              "manifest captures message text context")

        # --- Incremental run: nothing new ---
        res2 = att.archive_attachments(db_path=db_path, output_dir=out, full=False,
                                       verbose=False)
        check(res2["copied_count"] == 0,
              f"incremental re-run copies nothing new (got {res2['copied_count']})")

        # --- Photos/videos filter still picks both here ---
        out2 = os.path.join(tmp, "archive_pv")
        res3 = att.archive_attachments(db_path=db_path, output_dir=out2, full=True,
                                       types={"photo", "video"}, verbose=False)
        check(res3["copied_count"] == 2, "photos-videos filter copies the 2 media files")

        # --- Dry run writes nothing ---
        out3 = os.path.join(tmp, "archive_dry")
        res4 = att.archive_attachments(db_path=db_path, output_dir=out3, full=True,
                                       dry_run=True, verbose=False)
        check(res4["copied_count"] == 2, "dry-run counts 2 would-copy")
        check(not os.path.exists(out3) or not _all_files(out3),
              "dry-run creates no files")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


def _all_files(root):
    found = []
    for dirpath, _, files in os.walk(root):
        for name in files:
            found.append(os.path.join(dirpath, name))
    return found


if __name__ == "__main__":
    main()
