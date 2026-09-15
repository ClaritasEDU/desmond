#!/usr/bin/env python3
"""
Test the picker's new export path: real attachments copied + inline-media HTML
transcript + datetime ordering + filename convention (date/time + people first).

Runs on any platform — constructs records directly and calls export_records
(no real Messages DB needed). The regression section below builds a synthetic
chat.db (the real column set) to drive gather()/list_people()/the HTTP handler.
"""

import json
import os
import re
import socket
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request

import imessage_picker as picker

APPLE_EPOCH = 978307200


def apple_ns(unix_ts):
    return int((unix_ts - APPLE_EPOCH) * 1_000_000_000)


def typedstream(text):
    """Minimal NSAttributedString typedstream as found in chat.db attributedBody:
    1-byte length under 128, 0x81 + 2-byte, or 0x82 + 4-byte for long texts."""
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
            + b"\x86\x84\x02iI\x01\x01\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i"
            b"\x01\x92\x84\x96\x96\x1d__kIMMessagePartAttributeName\x86\x86\x86")


XSS_GROUP = '<img src=x onerror="alert(1)">'
PII_TEXT = "call me at 512-555-0199 or bob@example.com, SSN 123-45-6789"


def make_chat_db(path, base=1783504800):
    """Synthetic chat.db with the real column set the picker queries."""
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
            date INTEGER, is_from_me INTEGER, handle_id INTEGER,
            associated_message_type INTEGER DEFAULT 0, balloon_bundle_id TEXT,
            cache_has_attachments INTEGER DEFAULT 0);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, mime_type TEXT,
            filename TEXT, transfer_name TEXT);
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
    """)
    c.executemany("INSERT INTO handle VALUES (?,?,?)", [
        (1, "+15125550100", "iMessage"), (2, "+15125559100", "SMS"),
        (3, "kate@example.com", "iMessage")])
    # Two unknown numbers sharing the same last four digits (…0100 / …9100)
    # plus a group whose display name is hostile, plus a SECOND chat row for
    # the first number (merged SMS/iMessage threads do this).
    c.executemany("INSERT INTO chat VALUES (?,?,?)", [
        (10, "+15125550100", None), (11, "+15125559100", None),
        (12, "chat0001", XSS_GROUP), (13, "+15125550100", None)])
    c.executemany("INSERT INTO chat_handle_join VALUES (?,?)",
                  [(10, 1), (11, 2), (12, 1), (12, 3), (13, 1)])
    ins = ("INSERT INTO message (ROWID,text,attributedBody,date,is_from_me,handle_id,"
           "associated_message_type) VALUES (?,?,?,?,?,?,?)")
    c.executemany(ins, [
        (1, PII_TEXT, None, apple_ns(base), 0, 1, 0),
        (2, "ok", None, apple_ns(base + 60), 1, 0, 0),
        (3, "unknown number two", None, apple_ns(base + 120), 0, 2, 0),
        (4, None, typedstream(b"from attributedBody"), apple_ns(base + 180), 1, 0, 0),
        (5, "group hello", None, apple_ns(base + 240), 0, 3, 0),
        (6, "merged sms/imessage", None, apple_ns(base + 300), 0, 1, 0),
        (7, "￼photo caption", None, apple_ns(base + 360), 0, 1, 0),
    ])
    c.executemany("INSERT INTO chat_message_join VALUES (?,?)",
                  [(10, 1), (10, 2), (11, 3), (10, 4), (12, 5), (10, 6), (13, 6), (10, 7)])
    conn.commit()
    conn.close()


def http(port, path, host, method="GET", body=None, origin=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=body.encode() if body else None)
    req.add_header("Host", host)
    if origin:
        req.add_header("Origin", origin)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    failures = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            failures.append(msg)

    # apply_order: records arrive oldest->newest.
    a = [{"timestamp": "2024-01-01T00:00:00"}, {"timestamp": "2024-02-01T00:00:00"}]
    check(picker.apply_order(a, "oldest")[0]["timestamp"].startswith("2024-01"),
          "apply_order oldest keeps oldest first")
    check(picker.apply_order(a, "newest")[0]["timestamp"].startswith("2024-02"),
          "apply_order newest puts newest first")

    with tempfile.TemporaryDirectory() as tmp:
        photo = os.path.join(tmp, "IMG_1.jpg")
        with open(photo, "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"\x00" * 500)  # tiny fake jpeg

        records = [
            {
                "id": 1, "person": "Mom", "timestamp": "2024-01-15T09:32:00",
                "date": "2024-01-15", "time": "09:32:00", "sender": "Mom",
                "is_from_me": False, "message_type": "text_with_attachment",
                "text": "look at this [photo]", "text_plain": "look at this",
                "attachment_types": ["photo"],
                "attachments": [{"category": "photo", "mime": "image/jpeg",
                                 "filename": photo, "transfer_name": "IMG_1.jpg"}],
                "reaction": None,
            },
            {
                "id": 2, "person": "Mom", "timestamp": "2024-01-16T10:00:00",
                "date": "2024-01-16", "time": "10:00:00", "sender": "Me",
                "is_from_me": True, "message_type": "text", "text": "nice!",
                "text_plain": "nice!", "attachment_types": [], "attachments": [],
                "reaction": None,
            },
            {
                "id": 3, "person": "Mom", "timestamp": "2024-01-17T11:00:00",
                "date": "2024-01-17", "time": "11:00:00", "sender": "Mom",
                "is_from_me": False, "message_type": "attachment",
                "text": "[photo]", "text_plain": "",
                "attachment_types": ["photo"],
                "attachments": [{"category": "photo", "mime": "image/heic",
                                 "filename": "/no/such/offloaded.heic",
                                 "transfer_name": "offloaded.heic"}],
                "reaction": None,
            },
        ]

        dest = os.path.join(tmp, "drive")
        f = {"types": ["text", "attachments", "reactions"], "range": "all",
             "order": "newest", "direction": "both", "dest": dest}

        res = picker.export_records(records, ["Mom"], f)
        check(res["ok"], "export succeeded")
        check(res["attachments_saved"] == 1, f"saved 1 real attachment (got {res['attachments_saved']})")
        check(res["attachments_missing"] == 1, f"flagged 1 missing (got {res['attachments_missing']})")

        folder = res["folder"]
        adir = os.path.join(folder, "attachments")
        files = os.listdir(adir) if os.path.isdir(adir) else []
        check(len(files) == 1, f"one file copied into attachments/ (got {files})")
        # Filename: date/time stamp FIRST, then the people in the chat.
        check(bool(files) and re.match(r"^2024-01-15_0932_Mom_", files[0]),
              f"filename leads with datetime + people (got {files[0] if files else None})")

        html_path = os.path.join(folder, "conversation.html")
        check(os.path.exists(html_path), "wrote conversation.html")
        html = open(html_path, encoding="utf-8").read()
        check("<img class=\"att\"" in html, "HTML embeds the photo inline (<img>)")
        check('id="toggle"' in html and 'let order = "newest"' in html,
              "HTML has order toggle defaulting to newest")
        check("PAGE_SIZE = 100" in html and 'id="more"' in html,
              "transcript paginates (default 100 per page)")
        check("not downloaded from iCloud" in html, "HTML notes the missing/offloaded file")

        with open(os.path.join(folder, "messages.json"), encoding="utf-8") as jf:
            man = json.load(jf)
        check(man["order"] == "newest", "messages.json records the order")
        check(man["attachments_saved"] == 1, "messages.json counts saved attachments")
        media1 = next(m for m in man["messages"] if m["id"] == 1)["media"]
        check(media1 and media1[0]["path"].startswith("attachments/"),
              "manifest links message to its saved attachment path")

        check(os.path.exists(os.path.join(folder, "conversation.md")), "wrote conversation.md")
        check(os.path.exists(os.path.join(folder, "messages.csv")), "wrote messages.csv")

        # Local + Google Drive mirror, then per-export verify.
        f_drive = dict(f, dest=os.path.join(tmp, "local2"),
                       drive=os.path.join(tmp, "GDrive"), mirror_drive=True)
        res3 = picker.export_records(records, ["Mom"], f_drive)
        check(res3["drive_folder"] is not None, "picker mirrors the export to Google Drive")
        check(res3["in_local"] == 1 and res3["in_drive"] == 1,
              f"picker verify: 1 local + 1 drive (got {res3['in_local']}/{res3['in_drive']})")
        check(res3["missing_drive"] == 0, "nothing missing from Drive")
        dfolder = res3["drive_folder"]
        check(os.path.isdir(os.path.join(dfolder, "attachments")) and
              len(os.listdir(os.path.join(dfolder, "attachments"))) == 1,
              "attachment mirrored into Drive export/attachments")
        check(os.path.exists(os.path.join(res3["folder"], "VERIFY_REPORT.md")) and
              os.path.exists(os.path.join(dfolder, "VERIFY_REPORT.md")),
              "VERIFY_REPORT.md written to both local and Drive")
        check(os.path.exists(os.path.join(res3["folder"], "verify_diff.json")),
              "verify_diff.json written")
        check(res3.get("drive_error") is None, "clean mirror reports no drive_error")

        # Attachments OFF → no files copied.
        f_off = dict(f, types=["text"], dest=os.path.join(tmp, "drive2"))
        res2 = picker.export_records(records, ["Mom"], f_off)
        check(res2["attachments_saved"] == 0,
              "attachments toggle off copies nothing")

        # ---- Drive mirror failure must NOT fail an export already on disk ----
        blocker = os.path.join(tmp, "GoogleDriveFile")
        open(blocker, "w").close()          # a FILE where the Drive folder should be
        f_bad = dict(f, types=["text", "attachments", "reactions"],
                     dest=os.path.join(tmp, "local3"), drive=blocker, mirror_drive=True)
        res4 = picker.export_records(records, ["Mom"], f_bad)
        check(res4["ok"] is True, "Drive mirror OSError: local export still reports ok")
        check(bool(res4.get("drive_error")) and "Google Drive" in res4["drive_error"],
              f"Drive mirror OSError surfaced as drive_error (got {res4.get('drive_error')!r})")
        check(res4["drive_folder"] is None, "failed mirror leaves drive_folder unset")
        check(os.path.exists(os.path.join(res4["folder"], "VERIFY_REPORT.md")),
              "VERIFY_REPORT.md still written after a Drive failure")
        vr = open(os.path.join(res4["folder"], "VERIFY_REPORT.md"), encoding="utf-8").read()
        check("Google Drive warning" in vr, "VERIFY_REPORT.md mentions the Drive warning")
        check("d.drive_error" in picker.PAGE and "esc(d.drive_error)" in picker.PAGE,
              "UI shows drive_error as a warning beside the local path")

        # ---- inline JSON can't break out of the <script> block --------------
        rec = {"person": "Mom", "date": "2024-01-15", "time": "09:32:00",
               "timestamp": "2024-01-15T09:32:00", "sender": "Mom", "is_from_me": False,
               "message_type": "text",
               "text_plain": "lol <!--<script> check </script> this   line", "media": []}
        page = picker.render_html([rec], ["Mom"], "summary", "oldest")
        block = page.split("const RECORDS = ", 1)[1].split(";\nconst PAGE_SIZE", 1)[0]
        check("<" not in block, "no raw '<' inside the inline RECORDS JSON")
        check(" " not in block and "\\u2028" in block,
              "U+2028 line terminator escaped in inline JSON")
        check(json.loads(block)[0]["text_plain"] == rec["text_plain"],
              "escaped payload still decodes to the original text")
        check(page.count("<script>") == 1 and page.count("</script>") == 1,
              "message text can't inject extra script tags")

        # ---- types=[] means NOTHING, not everything ---------------------------
        err = picker.export_records([], ["Mom"], dict(f, types=[]))
        check(err["ok"] is False and "Nothing to export" in err["error"],
              "empty selection returns the 'Nothing to export' error")

        # ---- attributedBody decoder: 0x82 4-byte length + U+FFFC ---------------
        long_text = picker.decode_attributed_body(typedstream(b"X" * 40000))
        check(long_text is not None and len(long_text) == 40000 and long_text[:3] == "XXX",
              f"0x82 (4-byte) length prefix decodes the full text (got {len(long_text or '')})")
        mid = picker.decode_attributed_body(typedstream(b"Y" * 300))
        check(mid is not None and len(mid) == 300, "0x81 (2-byte) length prefix still decodes")
        short = picker.decode_attributed_body(typedstream("￼photo caption".encode("utf-8")))
        check(short == "photo caption", f"U+FFFC object-replacement stripped (got {short!r})")
        check(picker.decode_attributed_body(None) is None, "empty body decodes to None")

        # =====================================================================
        # Synthetic chat.db: gather / list_people / HTTP handler regressions
        # =====================================================================
        db = os.path.join(tmp, "chat.db")
        make_chat_db(db)
        # Two photo attachments: one real file (the IMG_1.jpg written above)
        # and one offloaded in iCloud (path doesn't exist).
        _c = sqlite3.connect(db)
        _c.executemany("INSERT INTO attachment VALUES (?,?,?,?)", [
            (1, "image/jpeg", photo, "IMG_1.jpg"),
            (2, "image/heic", "/no/such/offloaded.heic", "IMG_2.HEIC")])
        _c.executemany("INSERT INTO message_attachment_join VALUES (?,?)", [(7, 1), (7, 2)])
        _c.commit(); _c.close()
        saved_db, saved_port = picker.MESSAGES_DB, picker.PORT
        picker.MESSAGES_DB = db
        picker._contacts_loaded = True      # no AddressBook: identifiers stay raw
        picker._conv_name_cache.clear()
        try:
            # -- naming rule: direct chats keep the FULL identifier, typed direct
            people = {p["name"]: p for p in picker.list_people()}
            check("+15125550100" in people and people["+15125550100"]["type"] == "direct",
                  f"unknown direct number keeps its full identifier, type direct (got {list(people)})")
            check("+15125559100" in people and people["+15125559100"]["type"] == "direct",
                  "second unknown number (same last four digits) is its own direct chat")
            check("0100" not in people and "9100" not in people,
                  "numbers are never abbreviated to their last four digits")
            check(XSS_GROUP in people and people[XSS_GROUP]["type"] == "group",
                  "chat… identifier with a display name is a group")
            check(people["+15125550100"]["count"] == 5,
                  f"list_people counts a message joined to two chats once (got {people['+15125550100']['count']})")

            # -- dedupe: message 6 sits in chat 10 AND chat 13 (same person)
            recs = picker.gather({"people": ["+15125550100"], "range": "all"})
            ids = [r["id"] for r in recs]
            check(sorted(ids) == [1, 2, 4, 6, 7],
                  f"gather exports a double-joined message once (got {ids})")
            r7 = next(r for r in recs if r["id"] == 7)
            check(r7["text_plain"] == "photo caption",
                  f"U+FFFC stripped from message.text too (got {r7['text_plain']!r})")
            r4 = next(r for r in recs if r["id"] == 4)
            check(r4["text_plain"] == "from attributedBody",
                  "text recovered from attributedBody")

            # -- types=[] via gather → no records
            check(picker.gather({"people": ["+15125550100"], "range": "all", "types": []}) == [],
                  "gather with an explicit empty types list returns nothing")
            check(len(picker.gather({"people": ["+15125550100"], "range": "all"})) == 5,
                  "gather with NO types key still defaults to everything")

            # -- redaction scrubs EVERY saved file, not just messages.csv
            recs = picker.gather({"people": ["+15125550100"], "range": "all", "redact": True,
                                  "types": ["text", "attachments", "reactions"]})
            r1 = next(r for r in recs if r["id"] == 1)
            check("123-45-6789" not in r1["text"] and "123-45-6789" not in r1["text_plain"],
                  "redact scrubs both text and text_plain in gather()")
            resr = picker.export_records(recs, ["+15125550100"],
                                         {"range": "all", "types": ["text"], "redact": True,
                                          "dest": os.path.join(tmp, "redacted"),
                                          "mirror_drive": False})
            for fn in ("conversation.html", "conversation.md", "messages.json", "messages.csv"):
                body = open(os.path.join(resr["folder"], fn), encoding="utf-8").read()
                check("123-45-6789" not in body and "bob@example.com" not in body
                      and "555-0199" not in body,
                      f"redacted export: {fn} contains no SSN / email / phone")

            # -- stored XSS: conversation names never reach innerHTML unescaped
            check("${p.name}" not in picker.PAGE and "${esc(p.name)}" in picker.PAGE,
                  "renderPeople escapes the conversation name")
            check("${esc(p.type)}" in picker.PAGE, "renderPeople escapes the type too")
            esc_def = re.search(r"function esc\(s\)\{[^\n]*\}", picker.PAGE)
            check(esc_def is not None and "&#39;" in esc_def.group(0) and "&quot;" in esc_def.group(0),
                  "PAGE esc() escapes single AND double quotes")
            check(picker.PAGE.index("function esc(") < picker.PAGE.index("function renderPeople("),
                  "esc() is defined before renderPeople uses it")
            check("${r.date}" not in picker.PAGE and "${esc(r.date)}" in picker.PAGE,
                  "renderPreview escapes date/time fields")
            check("${d.first}" not in picker.PAGE and "${esc(d.first)}" in picker.PAGE,
                  "save result escapes first/last dates")

            # -- Save exports the PREVIEWED filters, not the live form
            check("state.previewed = filters" in picker.PAGE
                  and "{ ...state.previewed, deselected }" in picker.PAGE,
                  "Save sends the filter snapshot captured at preview time")
            check("...collect(), deselected" not in picker.PAGE,
                  "Save no longer re-collects the live form")
            check(picker.PAGE.count("invalidatePreview") >= 10,
                  "every control change invalidates the preview")
            check('$("preview").style.display = "none"' in picker.PAGE.split("function invalidatePreview")[1][:200],
                  "invalidatePreview hides the preview")

            # -- Host header validation over a real HTTP connection
            server = picker.bind_server(range(18800, 18810))
            check(server is not None, "bind_server found a free test port")
            port = picker.PORT
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                code, _ = http(port, "/api/people", f"evil.example:{port}")
                check(code == 403, f"GET with a rebinding Host is refused (got {code})")
                code, body = http(port, "/api/people", f"127.0.0.1:{port}")
                check(code == 200 and "+15125550100" in body,
                      f"GET with Host 127.0.0.1:PORT is served (got {code})")
                code, _ = http(port, "/api/people", f"localhost:{port}")
                check(code == 200, f"GET with Host localhost:PORT is served (got {code})")
                code, _ = http(port, "/", f"evil.example:{port}")
                check(code == 403, "the page itself is refused for a bad Host")

                # -- /api/media serves the REAL photo bytes for the preview
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/media/1")
                req.add_header("Host", f"127.0.0.1:{port}")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    check(resp.status == 200 and resp.headers["Content-Type"] == "image/jpeg",
                          "preview photo served with an image content type")
                    check(resp.read() == open(photo, "rb").read(), "preview photo is the original bytes")
                code, _ = http(port, "/api/media/1", f"evil.example:{port}")
                check(code == 403, "preview photo refused for a rebinding Host")
                code, _ = http(port, "/api/media/2", f"127.0.0.1:{port}")
                check(code == 404, "an offloaded (missing) photo → 404, not a crash")
                code, _ = http(port, "/api/media/abc", f"127.0.0.1:{port}")
                check(code == 404, "a non-numeric id → 404")
                check('src="/api/media/${Number(a.id)}"' in picker.PAGE, "preview renders real photo thumbnails")
                code, _ = http(port, "/api/preview", f"evil.example:{port}", "POST",
                               '{"people":["+15125550100"],"range":"all"}')
                check(code == 403, f"POST with a rebinding Host is refused (got {code})")
                code, body = http(port, "/api/preview", f"127.0.0.1:{port}", "POST",
                                  '{"people":["+15125550100"],"range":"all"}',
                                  origin=f"http://127.0.0.1:{port}")
                check(code == 200 and json.loads(body)["total"] == 5,
                      f"POST from our own page still works (got {code})")
                code, _ = http(port, "/api/preview", f"127.0.0.1:{port}", "POST", "{}",
                               origin="http://evil.example")
                check(code == 403, "cross-origin POST still refused")
            finally:
                server.shutdown()
                server.server_close()

            # -- port fallback: 8765 busy → next port; all busy → None
            blockers = []
            try:
                s = socket.socket(); s.bind(("127.0.0.1", 18820)); s.listen(1); blockers.append(s)
                srv = picker.bind_server(range(18820, 18823))
                check(srv is not None and picker.PORT == 18821,
                      f"busy first port falls through to the next one (PORT={picker.PORT})")
                if srv:
                    srv.server_close()
                for p in (18821, 18822):
                    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", p)); s.listen(1); blockers.append(s)
                check(picker.bind_server(range(18820, 18823)) is None,
                      "all ports busy → bind_server returns None (friendly message, no traceback)")
            finally:
                for s in blockers:
                    s.close()
            check(picker.PORT_RANGE[0] == 8765 and picker.PORT_RANGE[-1] == 8785,
                  "production range is 8765..8785")
            check("server.server_close()" in open(picker.__file__, encoding="utf-8").read(),
                  "main() closes the server socket on exit")

            # -- Full Disk Access: main() exits 3 (no_access) / 1 (missing)
            import desmond_sources
            real_state = desmond_sources.messages_db_state
            try:
                desmond_sources.messages_db_state = lambda p=None: "no_access"
                try:
                    picker.main()
                    check(False, "no_access → main() exits 3")
                except SystemExit as e:
                    check(e.code == 3, f"no_access → main() exits 3 (got {e.code})")
                desmond_sources.messages_db_state = lambda p=None: "missing"
                try:
                    picker.main()
                    check(False, "missing → main() exits 1")
                except SystemExit as e:
                    check(e.code == 1, f"missing → main() exits 1 (got {e.code})")
            finally:
                desmond_sources.messages_db_state = real_state
        finally:
            picker.MESSAGES_DB, picker.PORT = saved_db, saved_port
            picker._conv_name_cache.clear()

    # Save produces ONE PDF of the whole export (photos inline) when a headless
    # browser is available, and says why not otherwise.
    import stat as _stat
    import desmond_pdf
    with tempfile.TemporaryDirectory() as tmp:
        fake = os.path.join(tmp, "fake-chrome")
        with open(fake, "w") as f:
            f.write("#!/bin/bash\nfor a in \"$@\"; do case \"$a\" in --print-to-pdf=*) printf '%%PDF-1.4 fake' > \"${a#--print-to-pdf=}\";; esac; done\n")
        os.chmod(fake, os.stat(fake).st_mode | _stat.S_IEXEC)
        folder = os.path.join(tmp, "Mom_all_x"); os.makedirs(folder)
        with open(os.path.join(folder, "conversation.html"), "w") as f:
            f.write("<html></html>")
        orig = desmond_pdf.find_browser
        try:
            desmond_pdf.find_browser = lambda explicit=None: fake
            pdf, err = picker.make_pdf(folder, "Mom", "all")
            check(pdf == os.path.join(folder, "Mom_all.pdf") and os.path.exists(pdf) and err is None,
                  "Save renders one PDF named after the person and range")
            desmond_pdf.find_browser = lambda explicit=None: None
            pdf, err = picker.make_pdf(folder, "Mom", "all")
            check(pdf is None and "Save as PDF" in err, "no browser → clear fallback instruction, export still ok")
        finally:
            desmond_pdf.find_browser = orig
    check('d.pdf_path' in picker.PAGE and "Your PDF" in picker.PAGE, "result panel leads with the PDF")

    # A full-page gate covers the controls until /api/people has answered.
    check('id="loading"' in picker.PAGE and "position:fixed; inset:0" in picker.PAGE,
          "page is gated by a full-screen loading overlay")
    check('$("loading").style.display = "none"' in picker.PAGE, "overlay is removed only after the list loads")
    check('id="retry"' in picker.PAGE and "Full Disk Access" in picker.PAGE, "load failure shows the fix and a retry button")
    check(picker.PAGE.index('id="loading"') < picker.PAGE.index('id="search"'), "overlay is the first thing in the body")

    # The picker defaults to the whole history, not the last 7 days.
    check('class="opt on" data-r="all"' in picker.PAGE, "date range defaults to All time in the UI")
    check('range: "all"' in picker.PAGE, "page state defaults to All time")
    check(picker.filter_summary({}).startswith("All time"), "server-side default range is All time")
    for key in ("7d", "30d", "90d", "180d", "365d"):
        check(f'data-r="{key}"' in picker.PAGE, f"range chooser {key} present in the UI")
        since, until = picker.resolve_range(key, None, None)
        check(since is not None, f"range {key} resolves to a start time")
    s180, _ = picker.resolve_range("180d", None, None)
    s90, _ = picker.resolve_range("90d", None, None)
    check(s180 < s90, "6 months starts earlier than 3 months")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
