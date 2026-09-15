#!/usr/bin/env python3
"""
Synthetic test for desmond_sources.py — builds a fake chat.db (the Mac/
iPhone Messages schema), a fake iPhone backup folder (iOS-10+ sharded hash
layout, Info.plist and all), and Android XML bytes, then checks every
reader lands on the same standard export shape. No real Mac, phone, or adb.
"""

import builtins
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import tempfile
import textwrap

import desmond_sources as src
from imessage_exporter_windows import MESSAGES_DB_HASH

APPLE_EPOCH = 978307200


def apple_ns(unix_ts):
    return int((unix_ts - APPLE_EPOCH) * 1_000_000_000)


def typedstream(text):
    """Minimal NSAttributedString typedstream (see test_imessage_picker)."""
    n = len(text)
    if n < 128:
        ln = bytes([n])
    elif n < 32768:
        ln = b"\x81" + n.to_bytes(2, "little")
    else:
        ln = b"\x82" + n.to_bytes(4, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString"
            b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x0fNSMutableString\x01"
            b"\x84\x84\x08NSString\x01\x94\x84\x01+" + ln + text
            + b"\x86\x84\x02iI\x01\x01\x92\x84\x84\x84\x0cNSDictionary\x00\x86\x86\x86")


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


def unprivileged_runner_available():
    """root ignores chmod 000, so the real-permissions test needs a second
    user: `runuser -u nobody` (Linux). Returns False when that's not here."""
    if os.name != "posix" or os.geteuid() != 0 or not shutil.which("runuser"):
        return False
    try:
        import pwd
        pwd.getpwnam("nobody")
        return True
    except KeyError:
        return False


def classify_as_nobody():
    """Build a fake ~/Library/Messages/chat.db that user `nobody` can't read
    (chmod 000 folder) and ask messages_db_state() about it AS nobody."""
    home = tempfile.mkdtemp(prefix="desmond_fda_")
    os.chmod(home, 0o755)
    msgs = os.path.join(home, "Library", "Messages")
    os.makedirs(msgs)
    with open(os.path.join(msgs, "chat.db"), "wb") as f:
        f.write(b"SQLite format 3\x00" + b"\x00" * 100)
    os.chmod(msgs, 0o000)
    runner = os.path.join(home, "runner.py")
    with open(runner, "w") as f:
        f.write(textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {os.path.dirname(os.path.abspath(src.__file__))!r})
            import desmond_sources as s
            db = os.path.expanduser("~/Library/Messages/chat.db")
            print("exists", os.path.exists(db))
            print("state", s.messages_db_state(db))
            a = s.detect_available()
            print("needs", a["mac_messages_needs_full_disk_access"], a["mac_messages"])
            try:
                s.read_mac_messages()
                print("read ok")
            except s.SourceError as e:
                print("read", "Full Disk Access" in str(e))
        """))
    os.chmod(runner, 0o644)
    try:
        r = subprocess.run(["runuser", "-u", "nobody", "--", "env", f"HOME={home}",
                            "python3", runner], capture_output=True, text=True, timeout=60)
        return r.stdout + r.stderr
    finally:
        os.chmod(msgs, 0o700)
        shutil.rmtree(home, ignore_errors=True)


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
        check(all(m["address"] == "+15125550100" for m in dental),
              "direct messages carry the counterpart's address "
              "(feeds the family differ's identity check)")
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

        # A message joined to TWO chats (merged SMS/iMessage rows) exports once.
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO chat VALUES (20, 'chat0002', 'Dup Group')")
        conn.execute("INSERT INTO chat_handle_join VALUES (20, 1)")
        conn.execute("INSERT INTO chat_handle_join VALUES (20, 2)")
        conn.execute("INSERT INTO chat_message_join VALUES (20, 1)")
        conn.commit(); conn.close()
        export_dup = src.read_imessage_db(db, lookup=names.get)
        check(export_dup["total_messages"] == 3,
              "double chat membership doesn't duplicate the message "
              f"(got {export_dup['total_messages']})")

        # Attachment-only messages use the standard 'attachment' type.
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO message (ROWID, text, date, is_from_me, "
                     "handle_id, associated_message_type, "
                     "cache_has_attachments) VALUES (9, NULL, ?, 0, 1, 0, 1)",
                     (apple_ns(1783504800 + 300),))
        conn.execute("INSERT INTO chat_message_join VALUES (10, 9)")
        conn.commit(); conn.close()
        export_att = src.read_imessage_db(db, lookup=names.get)
        att = next(m for m in export_att["messages"]
                   if m["text"] == "[attachment]")
        check(att["message_type"] == "attachment",
              "attachment-only rows typed 'attachment' like the exporters")

        # U+FFFC (inline-attachment marker) is stripped; long attributedBody
        # texts (0x82 + 4-byte length) decode in full.
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO message (ROWID, text, date, is_from_me, handle_id, "
                     "associated_message_type, cache_has_attachments) "
                     "VALUES (12, ?, ?, 0, 1, 0, 1)",
                     ("￼photo caption", apple_ns(1783504800 + 360)))
        conn.execute("INSERT INTO message (ROWID, text, attributedBody, date, is_from_me, "
                     "handle_id, associated_message_type, cache_has_attachments) "
                     "VALUES (13, NULL, ?, ?, 0, 1, 0, 0)",
                     (typedstream(b"Z" * 40000), apple_ns(1783504800 + 420)))
        conn.execute("INSERT INTO chat_message_join VALUES (10, 12)")
        conn.execute("INSERT INTO chat_message_join VALUES (10, 13)")
        conn.commit(); conn.close()
        export_ffc = src.read_imessage_db(db, lookup=names.get)
        by_text = {m["text"][:12]: m for m in export_ffc["messages"]}
        check("photo captio" in by_text and by_text["photo captio"]["text"] == "photo caption",
              "U+FFFC object-replacement character stripped from text")
        long_msg = [m for m in export_ffc["messages"] if m["text"].startswith("ZZZ")]
        check(len(long_msg) == 1 and len(long_msg[0]["text"]) == 40000,
              "attributedBody with 0x82 4-byte length decodes the whole message "
              f"(got {len(long_msg[0]['text']) if long_msg else 0})")

        # ---- Mac path errors are human -----------------------------------
        try:
            src.read_mac_messages(db_path=os.path.join(tmp, "nope.db"))
            check(False, "missing chat.db raises SourceError")
        except src.SourceError as e:
            check("Full Disk Access" not in str(e),
                  "missing chat.db raises SourceError (and not the FDA hint)")

        # ---- Full Disk Access classification ------------------------------
        check(src.messages_db_state(db) == "ok", "readable chat.db → state 'ok'")
        check(src.messages_db_state(os.path.join(tmp, "nope.db")) == "missing",
              "absent chat.db → state 'missing'")

        # (a) monkeypatched open(): EACCES on chat.db is 'no_access', never 'missing'
        real_open = builtins.open

        def denied_open(path, *a, **k):
            if os.fspath(path) == db:
                raise PermissionError(13, "Operation not permitted", os.fspath(path))
            return real_open(path, *a, **k)

        builtins.open = denied_open
        try:
            check(src.messages_db_state(db) == "no_access",
                  "PermissionError on open() → state 'no_access'")
            try:
                src.read_mac_messages(db_path=db)
                check(False, "read_mac_messages without access raises the FDA fix")
            except src.SourceError as e:
                check("Full Disk Access" in str(e) and "Privacy & Security" in str(e),
                      "read_mac_messages without access raises the FDA fix")
            try:
                src.read_imessage_db(db)
                check(False, "read_imessage_db without access raises the FDA fix")
            except src.SourceError as e:
                check("Full Disk Access" in str(e),
                      "read_imessage_db without access raises the FDA fix")
        finally:
            builtins.open = real_open

        # (b) real permissions, as an unprivileged user (root ignores chmod 000)
        if unprivileged_runner_available():
            out = classify_as_nobody()
            check("exists False" in out,
                  "as nobody: os.path.exists() hides the protected chat.db (the old bug)")
            check("state no_access" in out,
                  f"as nobody: chmod-000 Messages folder → 'no_access' (output: {out.strip()!r})")
            check("needs True False" in out,
                  "as nobody: detect_available flags mac_messages_needs_full_disk_access")
            check("read True" in out,
                  "as nobody: read_mac_messages raises the FDA fix")
        else:
            print("SKIP: no `runuser`/`nobody` (or not root) — real-permission FDA "
                  "check covered by the monkeypatched variant above")

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
        up3 = src.parse_upload(b"\xef\xbb\xbf" + ANDROID_XML, "bom.xml")
        check(up3["total_messages"] == 2, "BOM'd XML upload parsed")
        up4 = src.parse_upload(ANDROID_XML.decode().encode("utf-16"),
                               "utf16.xml")
        check(up4["total_messages"] == 2, "UTF-16 XML upload parsed")
        try:
            src.parse_upload(b'{"not json', "messages.json")
            check(False, "bad JSON upload raises SourceError (friendly 400)")
        except src.SourceError:
            check(True, "bad JSON upload raises SourceError (friendly 400)")

        # ---- detect_available never crashes -------------------------------
        avail = src.detect_available()
        check(set(avail) >= {"platform", "mac_messages", "iphone_backups",
                             "adb_installed", "android_devices",
                             "mac_messages_needs_full_disk_access"},
              "detect_available returns the full snapshot (incl. the FDA flag)")
        check(isinstance(avail["mac_messages"], bool)
              and isinstance(avail["mac_messages_needs_full_disk_access"], bool),
              "mac_messages stays a bool; FDA flag is a bool")
        check(not (avail["mac_messages"] and avail["mac_messages_needs_full_disk_access"]),
              "a readable chat.db never also claims to need Full Disk Access")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
