#!/usr/bin/env python3
"""
Synthetic test for desmond_sources.py — builds a fake chat.db (the Mac/
iPhone Messages schema), a fake iPhone backup folder (iOS-10+ sharded hash
layout, Info.plist and all), and Android XML bytes, then checks every
reader lands on the same standard export shape. No real Mac, phone, or adb.
"""

import json
import os
import plistlib
import sqlite3
import tempfile

import desmond_sources as src
from imessage_exporter_windows import MESSAGES_DB_HASH

APPLE_EPOCH = 978307200


def apple_ns(unix_ts):
    return int((unix_ts - APPLE_EPOCH) * 1_000_000_000)


def make_chat_db(path, with_body_column=True):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    body_col = ", attributedBody BLOB" if with_body_column else ""
    c.executescript(f"""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT,
                           display_name TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT{body_col},
                              date INTEGER, is_from_me INTEGER,
                              handle_id INTEGER,
                              associated_message_type INTEGER DEFAULT 0,
                              cache_has_attachments INTEGER DEFAULT 0);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
    """)
    c.execute("INSERT INTO handle VALUES (1, '+15125550100')")   # dentist
    c.execute("INSERT INTO handle VALUES (2, 'kate@example.com')")
    c.execute("INSERT INTO chat VALUES (10, '+15125550100', NULL)")
    c.execute("INSERT INTO chat VALUES (11, 'chat0001', 'Room 12 Parents')")
    c.execute("INSERT INTO chat_handle_join VALUES (10, 1)")
    c.execute("INSERT INTO chat_handle_join VALUES (11, 1)")
    c.execute("INSERT INTO chat_handle_join VALUES (11, 2)")

    base = 1783504800   # 2026-07-08
    ins = ("INSERT INTO message (ROWID, text, date, is_from_me, handle_id, "
           "associated_message_type, cache_has_attachments) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)")
    c.execute(ins, (1, "Reminder: Emma's cleaning Jul 20",
                    apple_ns(base), 0, 1, 0, 0))
    c.execute(ins, (2, "Thanks!", apple_ns(base + 60), 1, 1, 0, 0))
    c.execute(ins, (3, "Field trip forms due Friday",
                    apple_ns(base + 120), 0, 2, 0, 0))
    c.execute(ins, (4, "Loved a message", apple_ns(base + 180), 0, 1, 2000, 0))
    c.execute(ins, (5, None, apple_ns(base + 240), 0, 1, 0, 0))  # empty, no att
    c.execute("INSERT INTO chat_message_join VALUES (10, 1)")
    c.execute("INSERT INTO chat_message_join VALUES (10, 2)")
    c.execute("INSERT INTO chat_message_join VALUES (11, 3)")
    c.execute("INSERT INTO chat_message_join VALUES (10, 4)")
    c.execute("INSERT INTO chat_message_join VALUES (10, 5)")
    conn.commit()
    conn.close()


ANDROID_XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<smses count="2">
  <sms address="+15125550142" date="1751968800000" type="1"
       body="Practice moved to 5pm" contact_name="Coach Dan" />
  <sms address="+15125550142" date="1751968900000" type="2"
       body="Got it" contact_name="Coach Dan" />
</smses>"""


def main():
    failures = []

    def check(cond, label):
        print(("PASS" if cond else "FAIL") + ": " + label)
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        # ---- shared iMessage reader --------------------------------------
        db = os.path.join(tmp, "chat.db")
        make_chat_db(db)
        names = {"+15125550100": "Smile Dental"}
        export = src.read_imessage_db(db, lookup=names.get)
        msgs = export["messages"]
        check(export["total_messages"] == 3,
              f"tapbacks + empty rows skipped (got {export['total_messages']})")
        dental = [m for m in msgs if m["conversation"] == "Smile Dental"]
        check(len(dental) == 2 and dental[0]["sender"] == "Smile Dental"
              and dental[1]["sender"] == "Me",
              "direct thread named via contact lookup; senders right")
        group = next(m for m in msgs if m["conversation_type"] == "group")
        check(group["conversation"] == "Room 12 Parents",
              "group chat named from display_name")
        check(all(isinstance(m["timestamp"], str)
                  and m["timestamp"][:4] == "2026" for m in msgs),
              "Apple nanosecond dates converted to ISO strings")
        json.dumps(export)
        check(True, "export is JSON-serializable")

        # No contacts -> numbers, never a crash.
        export2 = src.read_imessage_db(db)
        check(any(m["conversation"] == "+15125550100"
                  for m in export2["messages"]),
              "no-lookup fallback uses the raw number")

        # Old backups without attributedBody still read.
        db_old = os.path.join(tmp, "old.db")
        make_chat_db(db_old, with_body_column=False)
        export3 = src.read_imessage_db(db_old)
        check(export3["total_messages"] == 3,
              "pre-attributedBody databases read via fallback query")

        # ---- Mac path errors are human -----------------------------------
        try:
            src.read_mac_messages(db_path=os.path.join(tmp, "nope.db"))
            check(False, "missing chat.db raises SourceError")
        except src.SourceError:
            check(True, "missing chat.db raises SourceError")

        # ---- iPhone backup discovery + read ------------------------------
        backups_root = os.path.join(tmp, "Backup")
        bdir = os.path.join(backups_root, "00008110-000A1B2C3D4E5F")
        shard = os.path.join(bdir, MESSAGES_DB_HASH[:2])
        os.makedirs(shard)
        make_chat_db(os.path.join(shard, MESSAGES_DB_HASH))
        with open(os.path.join(bdir, "Info.plist"), "wb") as f:
            plistlib.dump({"Device Name": "Kate's iPhone",
                           "Product Type": "iPhone15,2"}, f)
        with open(os.path.join(bdir, "Manifest.plist"), "wb") as f:
            plistlib.dump({"IsEncrypted": False}, f)

        found = src.find_iphone_backups(locations=[backups_root])
        check(len(found) == 1 and found[0]["name"] == "Kate's iPhone"
              and found[0]["has_messages"] and not found[0]["encrypted"],
              f"backup discovered with device name (got {found})")
        export4 = src.read_iphone_backup(found[0]["path"])
        check(export4["total_messages"] == 3
              and export4["source"].startswith("iPhone backup"),
              "backup read through the sharded hash layout")

        # Encrypted/incomplete backup -> fix-it error.
        bdir2 = os.path.join(backups_root, "ENCRYPTED0001")
        os.makedirs(bdir2)
        with open(os.path.join(bdir2, "Manifest.plist"), "wb") as f:
            plistlib.dump({"IsEncrypted": True}, f)
        try:
            src.read_iphone_backup(bdir2)
            check(False, "encrypted backup raises the untick-encryption hint")
        except src.SourceError as e:
            check("ENCRYPTED" in str(e),
                  "encrypted backup raises the untick-encryption hint")

        # ---- uploads: json, xml, garbage ----------------------------------
        exp_json = json.dumps(export).encode()
        up1 = src.parse_upload(exp_json, "messages.json")
        check(up1["messages"][0]["conversation"] == "Smile Dental",
              "messages.json upload parsed")
        up2 = src.parse_upload(ANDROID_XML, "sms-backup.xml")
        check(up2["total_messages"] == 2
              and up2["conversations"][0]["name"] == "Coach Dan",
              "SMS Backup & Restore XML upload parsed in memory")
        try:
            src.parse_upload(b"hello there", "note.txt")
            check(False, "garbage upload rejected")
        except src.SourceError:
            check(True, "garbage upload rejected")

        # ---- detect_available never crashes -------------------------------
        avail = src.detect_available()
        check(set(avail) >= {"platform", "mac_messages", "iphone_backups",
                             "adb_installed", "android_devices"},
              "detect_available returns the full snapshot")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
