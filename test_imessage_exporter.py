#!/usr/bin/env python3
"""
Tests for imessage_exporter — runs on any platform against a synthetic chat.db
built with the real Messages column set. Covers mirror_to_drive plus the
regression fixes: direct vs group chat naming, invalid UTF-8, long CJK folder
names, legacy state files, U+FFFC stripping, double chat joins, emoji tapbacks,
corrupt state files, 0x81/0x82 attributedBody lengths, markdown escaping and
the Full Disk Access exit code.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime

import imessage_exporter as ie

NS = 1_000_000_000


def apple_ns(y, m, d, hh=12, mm=0):
    t = time.mktime(datetime(y, m, d, hh, mm).timetuple())
    return int((t - 978307200) * NS)


def typedstream(text):
    """Minimal NSAttributedString typedstream blob, as Messages stores it in
    message.attributedBody (length byte, 0x81+2 bytes, or 0x82+4 bytes)."""
    b = text.encode()
    n = len(b)
    if n < 128:
        ln = bytes([n])
    elif n < 65536:
        ln = b"\x81" + n.to_bytes(2, "little")
    else:
        ln = b"\x82" + n.to_bytes(4, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString"
            b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x0fNSMutableString\x01"
            b"\x84\x84\x08NSString\x01\x95\x84\x01+" + ln + b
            + b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00\x84\x84"
            b"\x08NSObject\x00\x85\x84\x01i\x01\x92\x84\x84\x84\x08NSString\x01\x94"
            b"\x84\x01+\x12__kIMMessagePartAttributeName\x86\x86\x86")


SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT NOT NULL, service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT,
    display_name TEXT, service_name TEXT);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
    handle_id INTEGER DEFAULT 0, date INTEGER, is_from_me INTEGER DEFAULT 0,
    associated_message_type INTEGER DEFAULT 0, associated_message_guid TEXT,
    associated_message_emoji TEXT, balloon_bundle_id TEXT,
    expressive_send_style_id TEXT, attributedBody BLOB, service TEXT,
    cache_has_attachments INTEGER DEFAULT 0, thread_originator_guid TEXT,
    date_retracted INTEGER DEFAULT 0);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, guid TEXT, filename TEXT,
    mime_type TEXT, transfer_name TEXT, uti TEXT);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
"""


def build_db(path, variant="base"):
    """Synthetic chat.db. Chats 1 and 2 are DIRECT chats with two unknown
    numbers sharing the same last-4 digits; chat 3 is a named group."""
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    h = c.execute
    h("INSERT INTO handle VALUES (1,'+15551234567','iMessage')")
    h("INSERT INTO handle VALUES (2,'+15559994567','SMS')")
    h("INSERT INTO handle VALUES (3,'bob@example.com','iMessage')")
    h("INSERT INTO chat VALUES (1,'iMessage;-;+15551234567','+15551234567','','iMessage')")
    h("INSERT INTO chat VALUES (2,'SMS;-;+15559994567','+15559994567','','SMS')")
    h("INSERT INTO chat VALUES (3,'iMessage;+;chat123','chat123','Weekend Crew','iMessage')")
    h("INSERT INTO chat_handle_join VALUES (1,1),(2,2),(3,1),(3,3)")
    msgs = [
        # rowid, text, handle, date, from_me, assoc_type, assoc_guid, emoji, balloon, body, chat
        (1, "hi from unknown 1", 1, apple_ns(2024, 3, 1, 9, 0), 0, 0, None, None, None, None, 1),
        (2, "reply to unknown 1", 0, apple_ns(2024, 3, 1, 9, 5), 1, 0, None, None, None, None, 1),
        (3, "hi from unknown 2", 2, apple_ns(2024, 3, 1, 10, 0), 0, 0, None, None, None, None, 2),
        (4, "￼", 1, apple_ns(2024, 3, 1, 11, 0), 0, 0, None, None, None, typedstream("￼"), 1),
        (5, None, 1, apple_ns(2024, 3, 1, 11, 1), 0, 0, None, None, None, typedstream("body only text"), 1),
        (6, None, 3, apple_ns(2024, 3, 2, 8, 0), 0, 2006, "p:0/ABC", "🔥", None,
         typedstream("Reacted 🔥 to “hi”"), 3),
        (7, "line one\n# not a heading\n- not a list\nline four", 3, apple_ns(2024, 3, 2, 8, 1),
         0, 0, None, None, None, None, 3),
        (8, None, 1, apple_ns(2024, 3, 2, 8, 2), 0, 0, None, None, None, typedstream("x" * 70000), 3),
    ]
    for rowid, text, hid, date, fm, assoc, ag, emoji, balloon, ab, chat in msgs:
        h("INSERT INTO message (ROWID,guid,text,handle_id,date,is_from_me,"
          "associated_message_type,associated_message_guid,associated_message_emoji,"
          "balloon_bundle_id,attributedBody,service,cache_has_attachments) "
          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (rowid, f"g{rowid}", text, hid, date, fm, assoc, ag, emoji, balloon, ab,
           "iMessage", 1 if rowid == 4 else 0))
        h("INSERT INTO chat_message_join VALUES (?,?,?)", (chat, rowid, date))
    h("INSERT INTO attachment VALUES (1,'a1','~/Library/Messages/Attachments/ab/IMG_1.jpeg',"
      "'image/jpeg','IMG_1.jpeg','public.jpeg')")
    h("INSERT INTO message_attachment_join VALUES (4,1)")
    if variant == "badutf8":
        h("INSERT INTO message (ROWID,guid,text,handle_id,date,is_from_me) VALUES "
          "(9,'g9',CAST(X'48656C6C6F20FF20776F726C64' AS TEXT),1,?,0)", (apple_ns(2024, 3, 3),))
        h("INSERT INTO chat_message_join VALUES (1,9,0)")
    if variant == "dupjoin":
        h("INSERT INTO chat_message_join VALUES (2,1,0)")
    if variant == "longname":
        h("INSERT INTO chat VALUES (4,'iMessage;+;chat999','chat999',?,'iMessage')", ("家" * 90,))
        h("INSERT INTO message (ROWID,guid,text,handle_id,date,is_from_me) VALUES "
          "(10,'g10','long group',3,?,0)", (apple_ns(2024, 3, 4),))
        h("INSERT INTO chat_message_join VALUES (4,10,0)")
    if variant == "newmsg":
        h("INSERT INTO message (ROWID,guid,text,handle_id,date,is_from_me) VALUES "
          "(11,'g11','later message',1,?,0)", (apple_ns(2024, 3, 1, 12, 0),))
        h("INSERT INTO chat_message_join VALUES (1,11,0)")
    if variant == "casefold":
        h("INSERT INTO chat VALUES (5,'iMessage;+;chatA','chatA','Mom','iMessage')")
        h("INSERT INTO chat VALUES (6,'iMessage;+;chatB','chatB','MOM','iMessage')")
        h("INSERT INTO message (ROWID,guid,text,handle_id,date,is_from_me) VALUES "
          "(12,'g12','hi mom',3,?,0)", (apple_ns(2024, 3, 5),))
        h("INSERT INTO message (ROWID,guid,text,handle_id,date,is_from_me) VALUES "
          "(13,'g13','HI MOM',3,?,0)", (apple_ns(2024, 3, 5, 13),))
        h("INSERT INTO chat_message_join VALUES (5,12,0),(6,13,0)")
    c.commit()
    c.close()


class Quiet:
    """Silence the exporter's progress prints inside a test."""
    def __enter__(self):
        self._out = sys.stdout
        sys.stdout = open(os.devnull, "w")
        return self

    def __exit__(self, *a):
        sys.stdout.close()
        sys.stdout = self._out


def run_export(tmp, variant="base", full=True, keep=False, name=None):
    """Build a DB for `variant`, point the exporter at it, run both exports.
    Returns (output_dir, exception_or_None)."""
    name = name or variant
    db = os.path.join(tmp, f"{name}.db")
    out = os.path.join(tmp, f"out_{name}")
    build_db(db, variant)
    ie.MESSAGES_DB = db
    ie.OUTPUT_DIR = out
    ie.STATE_FILE = os.path.join(out, ".export_state.json")
    if not keep:
        shutil.rmtree(out, ignore_errors=True)
    err = None
    try:
        with Quiet():
            ie.export_messages(full_export=full)
            ie.export_ai_ready(full_export=full)
    except Exception as e:  # noqa: BLE001 — tests report the failure
        err = e
    return out, err


def read_json(out):
    with open(os.path.join(out, "messages.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    failures = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            failures.append(msg)

    # No real Contacts database in the test environment.
    ie.load_contacts = lambda: None
    ie.CONTACTS_CACHE.clear()

    # ---------------------------------------------------------------- Drive
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "iMessages_Export")
        os.makedirs(os.path.join(src, "Mom"))
        with open(os.path.join(src, "messages.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(src, "Mom", "2024-01-01.md"), "w") as f:
            f.write("# hi")

        drive = os.path.join(tmp, "GoogleDrive")
        os.makedirs(drive)

        with Quiet():
            dest = ie.mirror_to_drive(src, drive_dir=drive)
        check(dest == os.path.join(drive, ie.DRIVE_SUBFOLDER),
              "mirrors into <Drive>/Desmond_Messages_Export")
        check(os.path.exists(os.path.join(dest, "messages.json")),
              "top-level file copied to Drive")
        check(os.path.exists(os.path.join(dest, "Mom", "2024-01-01.md")),
              "nested per-conversation file copied to Drive")
        check(os.path.exists(os.path.join(src, "messages.json")),
              "local copy remains (lives in BOTH places)")

        # Re-run after new content → existing Drive copy updates (dirs_exist_ok).
        with open(os.path.join(src, "new.md"), "w") as f:
            f.write("new")
        with Quiet():
            ie.mirror_to_drive(src, drive_dir=drive)
        check(os.path.exists(os.path.join(dest, "new.md")),
              "re-mirror updates the existing Drive copy")

        # No Drive detected → returns None, local copy untouched.
        with Quiet():
            nodrive = ie.mirror_to_drive(src, drive_dir=None)
        check(nodrive is None, "no Drive detected → returns None (local only)")
        check(os.path.exists(os.path.join(src, "messages.json")),
              "local export still intact when no Drive")

    # ------------------------------------------- pure helpers (no DB needed)
    # 0x81 / 0x82 attributedBody length forms
    check(ie.decode_attributed_body(typedstream("short")) == "short",
          "attributedBody 1-byte length decodes")
    check(ie.decode_attributed_body(typedstream("y" * 300)) == "y" * 300,
          "attributedBody 0x81 (2-byte) length decodes")
    check(ie.decode_attributed_body(typedstream("z" * 70000)) == "z" * 70000,
          "attributedBody 0x82 (4-byte) length decodes in full")
    check(ie.decode_attributed_body(typedstream("￼")) is None,
          "attributedBody holding only U+FFFC decodes to None")
    check(ie.decode_attributed_body(b"garbage") is None,
          "attributedBody garbage → None, no exception")

    # markdown escaping
    esc = ie.escape_markdown_lines("first\n# heading\n- item\n* star\n> quote\n+ plus\nplain")
    check(esc == "first\n\\# heading\n\\- item\n\\* star\n\\> quote\n\\+ plus\nplain",
          "continuation lines starting with #,-,*,>,+ are backslash-escaped")
    check(ie.escape_markdown_lines("# first line") == "# first line",
          "first line (after the sender prefix) is left alone")
    check(ie.escape_markdown_lines("a\n  - indented") == "a\n  \\- indented",
          "indented continuation markers are escaped too")

    # safe_dir_name
    cjk = "家" * 90
    sd = ie.safe_dir_name(cjk)
    check(len(sd.encode("utf-8")) <= 120 and sd.startswith("家家") and "_" in sd,
          "safe_dir_name byte-truncates a long CJK name and adds a hash suffix")
    check(ie.safe_dir_name(cjk) != ie.safe_dir_name("家" * 89 + "宅"),
          "two different long names truncate to different folder names")
    check(ie.safe_dir_name("Weekend Crew") == "Weekend Crew", "short names unchanged")
    check(ie.safe_dir_name("a/b:c") == "a_b_c", "path separators replaced")
    check(ie.safe_dir_name("") == "Unknown" and ie.safe_dir_name(None) == "Unknown",
          "empty/None name → Unknown")

    # reaction classification
    check(ie.classify_reaction(2000)[0:2] == ("reaction", "loved"), "2000 → loved")
    check(ie.classify_reaction(2006, "🔥") == ("reaction", "reacted 🔥", "reacted 🔥 to"),
          "2006 custom emoji tapback carries the emoji")
    check(ie.classify_reaction(3006, "🔥")[0] == "removal", "3006 → removal")
    check(ie.classify_reaction(1000)[0:2] == ("reaction", "sticker"), "1000 → sticker")
    check(ie.classify_reaction(2999)[0] == "reaction" and ie.classify_reaction(3999)[0] == "removal",
          "any 2xxx is a reaction, any 3xxx a removal")
    check(ie.classify_reaction(0) is None and ie.classify_reaction(None) is None
          and ie.classify_reaction(4000) is None, "non-reaction types → None")

    # shared direct/group rule
    check(ie.is_group_chat_identifier("chat123") and not ie.is_group_chat_identifier("+15551234567")
          and not ie.is_group_chat_identifier("bob@example.com"),
          "chat_identifier starting with 'chat' is a group, anything else is direct")

    # ------------------------------------------------------ Full Disk Access
    with tempfile.TemporaryDirectory() as tmp:
        ie.MESSAGES_DB = os.path.join(tmp, "nope", "chat.db")
        check(ie.check_messages_db_access(ie.MESSAGES_DB) == "missing",
              "missing chat.db classified as 'missing'")
        def denied(*a, **k):
            raise PermissionError(1, "Operation not permitted")
        ie.open = denied   # shadows the builtin inside the module
        try:
            check(ie.check_messages_db_access(ie.MESSAGES_DB) == "no_access",
                  "PermissionError on chat.db classified as 'no_access'")
            code = None
            try:
                with Quiet():
                    ie.main()
            except SystemExit as e:
                code = e.code
            check(code == 3, "main() exits 3 (Full Disk Access) on PermissionError")
        finally:
            del ie.open
        code = None
        try:
            with Quiet():
                ie.main()
        except SystemExit as e:
            code = e.code
        check(code == 1, "main() exits 1 when chat.db is genuinely missing")

    # ------------------------------------------------------------ DB exports
    with tempfile.TemporaryDirectory() as tmp:
        # 1. unknown direct numbers: full identifier, "direct", NOT merged
        out, err = run_export(tmp, "base")
        check(err is None, f"base export runs without error ({err})")
        data = read_json(out)
        convs = {(m["conversation"], m["conversation_type"]) for m in data["messages"]}
        check(("+15551234567", "direct") in convs,
              "unknown 1:1 number keeps its FULL identifier and type 'direct'")
        check(("+15559994567", "direct") in convs,
              "second unknown number (same last-4) is a separate 'direct' conversation")
        check(not any(c[0] == "4567" for c in convs),
              "no conversation is named by last-4 digits")
        check(("Weekend Crew", "group") in convs, "named group stays a 'group'")
        check(os.path.isdir(os.path.join(out, "_15551234567"))
              and os.path.isdir(os.path.join(out, "_15559994567")),
              "markdown export writes one folder per unknown number")
        check(len(data["messages"]) == 8 and sorted(m["rowid"] for m in data["messages"]) == list(range(1, 9)),
              "every message exported exactly once, rowid stored in each record")

        # 5. U+FFFC stripped; attachment-only message typed correctly
        by_rowid = {m["rowid"]: m for m in data["messages"]}
        check(by_rowid[4]["message_type"] == "attachment" and by_rowid[4]["text"] == "[photo]",
              "message whose text is only U+FFFC + photo is typed 'attachment'")
        check(not any("￼" in (m["text"] or "") for m in data["messages"]),
              "no U+FFFC survives in messages.json")
        md1 = open(os.path.join(out, "_15551234567", "2024-03-01.md"), encoding="utf-8").read()
        check("￼" not in md1 and "[📷 photo]" in md1,
              "markdown: U+FFFC stripped, photo placeholder written")
        check(by_rowid[5]["text"] == "body only text", "attributedBody-only text recovered")

        # 7. 2006 emoji tapback typed as reaction
        check(by_rowid[6]["message_type"] == "reaction" and by_rowid[6]["reaction"] == "reacted 🔥",
              "associated_message_type 2006 typed 'reaction' with its emoji")
        md_group = open(os.path.join(out, "Weekend Crew", "2024-03-02.md"), encoding="utf-8").read()
        check("*reacted 🔥 to a message*" in md_group, "markdown renders the emoji tapback")

        # 10. 0x82 long body survives the DB round-trip
        check(by_rowid[8]["text"] == "x" * 70000, "70,000-char attributedBody exported in full")

        # 9. multi-line markdown escaping in the day file
        check("\n\\# not a heading\n\\- not a list\nline four" in md_group,
              "multi-line message continuation lines escaped in markdown")
        check(by_rowid[7]["text"] == "line one\n# not a heading\n- not a list\nline four",
              "JSON keeps the raw (unescaped) text")

        # get_chat_participants: backward-compatible 2-arg call + rowid preference
        conn = sqlite3.connect(os.path.join(tmp, "base.db"))
        cur = conn.cursor()
        p = ie.get_chat_participants("chat123", cur)
        check(p == "+15551234567, bob@example.com",
              "get_chat_participants(chat_id, cursor) returns FULL identifiers")
        # chat_identifier 'chat123' duplicated under another ROWID (SMS copy)
        cur.execute("INSERT INTO chat VALUES (9,'SMS;+;chat123','chat123','','SMS')")
        cur.execute("INSERT INTO chat_handle_join VALUES (9,2)")
        check(ie.get_chat_participants("chat123", cur, chat_rowid=9) == "+15559994567",
              "chat_rowid kwarg wins over the (non-unique) chat_identifier")
        conn.close()

        # 2. invalid UTF-8 row does not abort
        out, err = run_export(tmp, "badutf8")
        check(err is None, f"invalid UTF-8 text row does not abort the export ({err})")
        if err is None:
            data = read_json(out)
            check(len(data["messages"]) == 9, "bad-UTF-8 message still exported (replaced)")
            check(any("Hello" in (m["text"] or "") and "world" in (m["text"] or "")
                      for m in data["messages"]), "bad byte replaced, rest of text kept")

        # 3. long CJK conversation name exports
        out, err = run_export(tmp, "longname")
        check(err is None, f"90-char CJK group name does not crash the run ({err})")
        if err is None:
            dirs = [d for d in os.listdir(out) if d.startswith("家")]
            check(len(dirs) == 1 and len(dirs[0].encode("utf-8")) <= 120,
                  "CJK conversation folder created with byte-safe truncated name")
            check(os.path.exists(os.path.join(out, dirs[0], "2024-03-04.md")),
                  "long-named conversation's day file written")
            data = read_json(out)
            check(any(m["conversation"] == "家" * 90 for m in data["messages"]),
                  "JSON keeps the full (untruncated) conversation name")

        # 6. double chat join deduped
        out, err = run_export(tmp, "dupjoin")
        check(err is None, f"dupjoin export runs ({err})")
        if err is None:
            data = read_json(out)
            check(len(data["messages"]) == 8 and sum(1 for m in data["messages"] if m["rowid"] == 1) == 1,
                  "message joined to two chats exported once in JSON")
            md_all = ""
            for d in ("_15551234567", "_15559994567"):
                p = os.path.join(out, d, "2024-03-01.md")
                if os.path.exists(p):
                    md_all += open(p, encoding="utf-8").read()
            check(md_all.count("hi from unknown 1") == 1,
                  "message joined to two chats written once in markdown")

        # 13. case-insensitive APFS: "Mom" and "MOM" share one folder
        out, err = run_export(tmp, "casefold")
        check(err is None, f"casefold export runs ({err})")
        if err is None:
            mom_dirs = [d for d in os.listdir(out) if d.casefold() == "mom"]
            check(len(mom_dirs) == 1, "conversations differing only by case share one folder")
            md = open(os.path.join(out, mom_dirs[0], "2024-03-05.md"), encoding="utf-8").read()
            check("hi mom" in md and "HI MOM" in md, "both conversations' messages in that folder")
            check(md.count("# Messages with") == 1, "single header (no rewrite mid-run)")

        # 4. legacy state file without last_ai_rowid → no duplicates
        out, err = run_export(tmp, "base", name="inc")
        check(err is None, f"initial run for incremental test ({err})")
        state_path = os.path.join(out, ".export_state.json")
        with open(state_path) as f:
            st = json.load(f)
        st.pop("last_ai_rowid", None)            # simulate a pre-cursor state file
        # ...and a messages.json written by an old version (no rowid field)
        data = read_json(out)
        for m in data["messages"]:
            m.pop("rowid", None)
        with open(os.path.join(out, "messages.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        with open(state_path, "w") as f:
            json.dump(st, f)
        out, err = run_export(tmp, "newmsg", full=False, keep=True, name="inc")
        check(err is None, f"incremental run on legacy state ({err})")
        if err is None:
            data = read_json(out)
            rowids = sorted(m.get("rowid") for m in data["messages"] if m.get("rowid") is not None)
            check(len(data["messages"]) == 9,
                  "legacy state without last_ai_rowid: no duplicated history (9, not 17)")
            check(rowids == [11], "only the genuinely new message was appended")
            with open(state_path) as f:
                st = json.load(f)
            check(st.get("last_ai_rowid") == 11 and st.get("last_message_rowid") == 11,
                  "both cursors advanced to the new rowid")
            md = open(os.path.join(out, "_15551234567", "2024-03-01.md"), encoding="utf-8").read()
            check(md.count("hi from unknown 1") == 1 and "later message" in md,
                  "markdown day file appended, not duplicated")

        # merge helper: rowid-based dedupe with legacy fallback
        prior = [{"rowid": 1, "timestamp": "t1", "sender": "A", "text": "x"},
                 {"timestamp": "t2", "sender": "B", "text": "y"}]
        new = [{"rowid": 1, "timestamp": "t1", "sender": "A", "text": "x"},
               {"rowid": 2, "timestamp": "t2", "sender": "B", "text": "y"},
               {"rowid": 3, "timestamp": "t3", "sender": "C", "text": "z"}]
        merged = ie.merge_message_records(prior, new)
        check([m.get("rowid") for m in merged] == [1, None, 3],
              "merge dedupes on rowid, and on (timestamp, sender, text) for legacy records")

        # 8. corrupt state file recovers
        ie.STATE_FILE = os.path.join(tmp, "corrupt", ".export_state.json")
        os.makedirs(os.path.dirname(ie.STATE_FILE))
        with open(ie.STATE_FILE, "w") as f:
            f.write('{"last_message_rowid": 5, "last_ai')   # truncated mid-write
        with Quiet():
            st = ie.load_state()
        check(st == {"last_message_rowid": 0}, "truncated state file → fresh state, no crash")
        with open(ie.STATE_FILE, "w") as f:
            f.write('[1, 2, 3]')
        with Quiet():
            st = ie.load_state()
        check(st == {"last_message_rowid": 0}, "non-object state file → fresh state")
        ie.save_state({"last_message_rowid": 7, "last_ai_rowid": 7})
        check(json.load(open(ie.STATE_FILE)) == {"last_message_rowid": 7, "last_ai_rowid": 7}
              and not os.path.exists(ie.STATE_FILE + ".tmp"),
              "save_state writes atomically and leaves no tmp file")
        out, err = run_export(tmp, "base", name="corruptrun")
        with open(ie.STATE_FILE, "w") as f:
            f.write("not json at all")
        out, err = run_export(tmp, "newmsg", full=False, keep=True, name="corruptrun")
        check(err is None, f"a full run after a corrupt state file completes ({err})")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
