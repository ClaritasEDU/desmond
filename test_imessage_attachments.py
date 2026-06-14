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

        # --- Verify (local only): the full archive in `out` should be COMPLETE ---
        v = att.verify_archive(db_path=db_path, output_dir=out,
                               expect_drive=False, verbose=False)
        check(v["complete"] is True, "local verify reports complete archive")
        check(v["verified"] == 2, f"verify counts 2 archived (got {v['verified']})")
        check(v["offloaded"] == 1, f"verify counts 1 offloaded (got {v['offloaded']})")
        check(v["missing_from_archive"] == 0, "verify finds nothing missing")

        # --- Mirror to a (fake) Google Drive, then THREE-WAY verify ---
        drive_root = os.path.join(tmp, "GoogleDrive")
        dest = att.mirror_to_drive(out, drive_dir=drive_root)
        check(dest == os.path.join(drive_root, att.ARCHIVE_FOLDER_NAME),
              "mirror_to_drive copies into <Drive>/Desmond_Message_Attachments")
        v3 = att.verify_archive(db_path=db_path, output_dir=out,
                                drive_dir=drive_root, expect_drive=True, verbose=False)
        check(v3["complete"] is True, "three-way verify complete (device+local+drive)")
        check(v3["in_local"] == 2 and v3["in_drive"] == 2,
              f"three-way counts 2 local + 2 drive (got {v3['in_local']}/{v3['in_drive']})")

        # --- Report files written, with the diff lists ---
        check(os.path.exists(os.path.join(out, "VERIFY_REPORT.md")), "wrote VERIFY_REPORT.md")
        with open(os.path.join(out, "verify_diff.json")) as f:
            diff = json.load(f)
        check(len(diff["offloaded"]) == 1, "diff report lists the 1 offloaded item")
        check(diff["counts"]["in_drive"] == 2, "diff report records Drive count")

        # --- Delete from the DRIVE mirror → three-way INCOMPLETE ---
        with open(os.path.join(out, "attachments.json")) as f:
            man = json.load(f)
        a_copied = next(a for a in man["attachments"] if a.get("saved_path"))
        os.remove(os.path.join(dest, a_copied["saved_path"]))
        v4 = att.verify_archive(db_path=db_path, output_dir=out,
                                drive_dir=drive_root, expect_drive=True, verbose=False)
        check(v4["complete"] is False and v4["missing_drive"] == 1,
              "three-way verify catches a file missing from Drive")

        # --- Retry idempotence: a deleted LOCAL file is restored by a full re-run ---
        os.remove(os.path.join(out, a_copied["saved_path"]))
        v5 = att.verify_archive(db_path=db_path, output_dir=out,
                                expect_drive=False, verbose=False)
        check(v5["complete"] is False, "verify catches a deleted local file")
        att.archive_attachments(db_path=db_path, output_dir=out, full=True, verbose=False)
        v6 = att.verify_archive(db_path=db_path, output_dir=out,
                                expect_drive=False, verbose=False)
        check(v6["complete"] is True, "re-running --full restores the missing file (retry)")

        # --- mirror_tree is incremental (second pass copies nothing new) ---
        again = att.mirror_tree(out, os.path.join(tmp, "GoogleDrive2"))
        again2 = att.mirror_tree(out, os.path.join(tmp, "GoogleDrive2"))
        check(again > 0 and again2 == 0, "mirror_tree skips already-copied files")

        # --- Verify with no archive present ---
        v7 = att.verify_archive(db_path=db_path,
                                output_dir=os.path.join(tmp, "nope"), verbose=False)
        check(v7["complete"] is False and v7.get("reason") == "no_manifest",
              "verify reports when no archive exists yet")

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
