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


def build_db_rows(db_path, rows, chats=None):
    """Flexible variant: rows are dicts (rowid, filename, mime, transfer, total,
    chats=[1], uti=None); chats is a list of (ROWID, chat_identifier,
    display_name). Adds an attachment.uti column like a modern chat.db."""
    if os.path.exists(db_path):
        os.remove(db_path)
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
            mime_type TEXT, transfer_name TEXT, total_bytes INTEGER, uti TEXT);
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """
    )
    c.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
    for cid, ident, disp in (chats or [(1, "+15551234567", None)]):
        c.execute("INSERT INTO chat VALUES (?, ?, ?)", (cid, ident, disp))
        c.execute("INSERT INTO chat_handle_join VALUES (?, 1)", (cid,))
    when = apple_ns(datetime(2024, 1, 15, 9, 32, 0))
    for r in rows:
        rid = r["rowid"]
        c.execute("INSERT OR IGNORE INTO message VALUES (?, ?, ?, 0, 1, NULL)",
                  (rid, f"msg {rid}", when))
        for cid in r.get("chats", [1]):
            c.execute("INSERT INTO chat_message_join VALUES (?, ?)", (cid, rid))
        c.execute("INSERT INTO attachment VALUES (?, ?, ?, ?, ?, ?)",
                  (rid, r["filename"], r.get("mime"), r.get("transfer"),
                   r.get("total", 0), r.get("uti")))
        c.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (rid, rid))
    conn.commit()
    conn.close()


def write_bytes(path, n):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * n)


def load_manifest(out):
    with open(os.path.join(out, "attachments.json"), encoding="utf-8") as f:
        return json.load(f)


def load_state(out):
    with open(os.path.join(out, ".attachments_state.json")) as f:
        return json.load(f)


def regression_tests(check, tmp):
    """Regression tests for reviewer findings (each one reproduced a real bug)."""
    import shutil

    # --- 1. Mirror never nests the archive inside itself ---
    src = os.path.join(tmp, "nest", "My Drive")
    write_bytes(os.path.join(src, "Mom", "a.jpg"), 1000)
    dest = att.drive_archive_dir(src)            # <src>/Desmond_Message_Attachments
    for _ in range(3):
        att.mirror_tree(src, dest)
    files = [os.path.relpath(f, src) for f in _all_files(src)]
    check(len(files) == 2 and max(f.count(os.sep) for f in files) == 2,
          f"mirror_tree into a subfolder of src does not nest repeatedly ({len(files)} files)")
    check(att.mirror_tree(src, src) == 0, "mirror_tree(src, src) is a no-op")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = att.mirror_to_drive(src, drive_dir=src)
    check(r == src and "already lives in Google Drive" in buf.getvalue()
          and len(_all_files(src)) == 2,
          "mirror_to_drive with --dest inside Drive says nothing to mirror")

    # --- 2. Directory-bundle attachments (.rtfd/.pages) archive + verify ---
    db = os.path.join(tmp, "bundle.db")
    out = os.path.join(tmp, "bundle_archive")
    bundle = os.path.join(tmp, "Att", "UUID", "notes.rtfd")
    write_bytes(os.path.join(bundle, "TXT.rtf"), 500)
    write_bytes(os.path.join(bundle, "img", "1.png"), 300)
    photo = os.path.join(tmp, "Att", "IMG_1.HEIC")
    write_bytes(photo, 100)
    build_db_rows(db, [
        dict(rowid=1, filename=bundle, mime="application/rtfd", transfer="notes.rtfd", total=800),
        dict(rowid=2, filename=photo, mime="image/heic", transfer="IMG_1.HEIC", total=100),
    ])
    res = att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
    m = load_manifest(out)
    st = {a["attachment_id"]: a for a in m["attachments"]}
    check(st[1]["status"] == "copied" and st[1]["size_bytes"] == 800,
          f"bundle directory attachment is copied with tree size (got {st[1]['status']})")
    saved_bundle = os.path.join(out, st[1]["saved_path"])
    check(os.path.isdir(saved_bundle)
          and os.path.exists(os.path.join(saved_bundle, "img", "1.png")),
          "bundle contents land in the archive")
    check(res["error_count"] == 0 and res["errors"] == [],
          "result carries error_count/errors (zero here)")
    check(load_state(out)["last_attachment_rowid"] == 2,
          "state advances past the bundle row")
    v = att.verify_archive(db_path=db, output_dir=out, expect_drive=False, verbose=False)
    check(v["complete"] is True and v["in_local"] == 2,
          "verify counts the bundle as present (complete)")
    drive = os.path.join(tmp, "bundle_drive")
    with contextlib.redirect_stdout(io.StringIO()):
        att.mirror_to_drive(out, drive_dir=drive)
    v = att.verify_archive(db_path=db, output_dir=out, drive_dir=drive,
                           expect_drive=True, verbose=False)
    check(v["complete"] is True and v["in_drive"] == 2,
          "bundle mirrors to Drive and three-way verify is complete")

    # --- 2b. A copy error is counted and printed ---
    db_err = os.path.join(tmp, "err.db")
    out_err = os.path.join(tmp, "err_archive")
    good = os.path.join(tmp, "Att", "good.jpg")
    write_bytes(good, 10)
    build_db_rows(db_err, [dict(rowid=1, filename=good, mime="image/jpeg",
                                transfer="good.jpg", total=10)])
    orig_copyfile = shutil.copyfile

    def boom(s, d, *a, **k):
        raise OSError(28, "No space left on device")
    shutil.copyfile = boom
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = att.archive_attachments(db_path=db_err, output_dir=out_err, full=True)
    finally:
        shutil.copyfile = orig_copyfile
    check(res["error_count"] == 1 and "could not be copied" in buf.getvalue(),
          "copy failure is counted in error_count and printed in the summary")

    # --- 3. Long CJK filename (270 bytes) archives without ENAMETOOLONG ---
    db = os.path.join(tmp, "long.db")
    out = os.path.join(tmp, "long_archive")
    long_name = "写真" * 45 + ".jpg"
    p = os.path.join(tmp, "Att", "x.jpg")
    write_bytes(p, 100)
    build_db_rows(db, [dict(rowid=1, filename=p, mime="image/jpeg", transfer=long_name, total=100)])
    res = att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
    m = load_manifest(out)
    check(m["attachments"][0]["status"] == "copied" and res["copied_count"] == 1,
          f"long CJK filename is archived (got {m['attachments'][0]['status'][:60]})")
    tn = att.safe_name_keep_ext(f"2024-01-15_0932_Mom_{long_name}")
    check(len(tn.encode("utf-8")) <= 200 and tn.endswith(".jpg"),
          f"safe_name_keep_ext caps at 200 bytes and keeps .jpg ({len(tn.encode())} bytes)")
    check(len(att.safe_name("😀" * 100).encode("utf-8")) <= 200,
          "safe_name caps folder names at 200 bytes")

    # --- 4. Message in two chats → ONE copy, ONE manifest record ---
    db = os.path.join(tmp, "multi.db")
    out = os.path.join(tmp, "multi_archive")
    p = os.path.join(tmp, "Att", "IMG_2.HEIC")
    write_bytes(p, 100)
    build_db_rows(db, [dict(rowid=1, filename=p, mime="image/heic", transfer="IMG_2.HEIC",
                            total=100, chats=[1, 2])],
                  chats=[(1, "+15551234567", None), (2, "chat9988", "Family")])
    res = att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
    m = load_manifest(out)
    heics = [f for f in _all_files(out) if f.endswith(".HEIC")]
    check(res["copied_count"] == 1 and len(heics) == 1 and len(m["attachments"]) == 1,
          f"attachment joined to two chats is archived once (copied={res['copied_count']}, files={len(heics)})")
    check(m["attachments"][0]["conversation"] == "+15551234567",
          "dedupe is deterministic (lowest chat ROWID wins)")

    # --- 5. copystat failure (cloud/SMB volume) keeps the good copy ---
    orig_copystat = shutil.copystat

    def bad_copystat(s, d, *a, **k):
        orig_copystat(s, d, *a, **k)
        raise PermissionError(1, "Operation not permitted")
    shutil.copystat = bad_copystat
    try:
        src_m = os.path.join(tmp, "cs_local")
        write_bytes(os.path.join(src_m, "Mom", "a.jpg"), 1000)
        errs = []
        n = att.mirror_tree(src_m, os.path.join(tmp, "cs_drive"), errors=errs)
        check(n == 1 and not errs
              and os.path.getsize(os.path.join(tmp, "cs_drive", "Mom", "a.jpg")) == 1000,
              "mirror_tree keeps the file when only copystat fails")
        db = os.path.join(tmp, "cs.db")
        out = os.path.join(tmp, "cs_archive")
        build_db_rows(db, [dict(rowid=1, filename=os.path.join(src_m, "Mom", "a.jpg"),
                                mime="image/jpeg", transfer="a.jpg", total=1000)])
        res = att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
        m = load_manifest(out)
        check(m["attachments"][0]["status"] == "copied"
              and os.path.getsize(os.path.join(out, m["attachments"][0]["saved_path"])) == 1000,
              "archive keeps the file when only copystat fails")
    finally:
        shutil.copystat = orig_copystat

    # --- 5b. A short data copy IS rolled back ---
    orig_copyfile = shutil.copyfile

    def short_copyfile(s, d, *a, **k):
        with open(d, "wb") as f:
            f.write(b"x" * 3)
    shutil.copyfile = short_copyfile
    try:
        errs = []
        n = att.mirror_tree(src_m, os.path.join(tmp, "cs_drive2"), errors=errs)
        check(n == 0 and len(errs) == 1
              and not os.path.exists(os.path.join(tmp, "cs_drive2", "Mom", "a.jpg")),
              "a truncated data copy is rolled back and reported")
    finally:
        shutil.copyfile = orig_copyfile

    # --- 6. Truncated local file → verify INCOMPLETE ---
    db = os.path.join(tmp, "trunc.db")
    out = os.path.join(tmp, "trunc_archive")
    p = os.path.join(tmp, "Att", "IMG_3.HEIC")
    write_bytes(p, 5000)
    build_db_rows(db, [dict(rowid=1, filename=p, mime="image/heic", transfer="IMG_3.HEIC", total=5000)])
    att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
    saved = load_manifest(out)["attachments"][0]["saved_path"]
    write_bytes(os.path.join(out, saved), 10)
    v = att.verify_archive(db_path=db, output_dir=out, expect_drive=False, verbose=False)
    check(v["complete"] is False and v["in_local"] == 0 and v["missing_local"] == 1,
          "verify treats a truncated local file as missing")
    att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
    v = att.verify_archive(db_path=db, output_dir=out, expect_drive=False, verbose=False)
    check(v["complete"] is True, "re-running --full heals the truncated file")

    # --- 7. Drive detect: localized "Mon Drive" → the subfolder, never the root ---
    fake_home = os.path.join(tmp, "home")
    cs = os.path.join(fake_home, "Library", "CloudStorage")
    for d in ("Mon Drive", "Drive partagés", ".shortcut-targets-by-id"):
        os.makedirs(os.path.join(cs, "GoogleDrive-someone@example.com", d))
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = fake_home
    try:
        found = att.find_google_drive_dir()
        check(found == os.path.join(cs, "GoogleDrive-someone@example.com", "Mon Drive"),
              f"find_google_drive_dir picks 'Mon Drive', not the mount root (got {found})")
        os.makedirs(os.path.join(cs, "GoogleDrive-zz@example.com", "My Drive"))
        os.makedirs(os.path.join(cs, "GoogleDrive-aa@example.com", "My Drive"))
        found = att.find_google_drive_dir()
        check(found == os.path.join(cs, "GoogleDrive-aa@example.com", "My Drive"),
              "find_google_drive_dir is deterministic across accounts (sorted)")
        os.makedirs(os.path.join(cs, "GoogleDrive-00@example.com", "Shared drives"))
        found = att.find_google_drive_dir()
        check(found == os.path.join(cs, "GoogleDrive-aa@example.com", "My Drive"),
              "a mount with only 'Shared drives' is skipped, never returned as root")
    finally:
        if old_home is None:
            del os.environ["HOME"]
        else:
            os.environ["HOME"] = old_home

    # --- 8. Archived, then offloaded by iCloud → stays archived, state intact ---
    db = os.path.join(tmp, "offl.db")
    out = os.path.join(tmp, "offl_archive")
    ps = [os.path.join(tmp, "Att", f"OFF_{i}.HEIC") for i in (1, 2, 3)]
    for p in ps:
        write_bytes(p, 100)
    build_db_rows(db, [dict(rowid=i, filename=p, mime="image/heic",
                            transfer=os.path.basename(p), total=100)
                       for i, p in enumerate(ps, 1)])
    att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
    check(load_state(out)["last_attachment_rowid"] == 3, "state settles at 3 after first run")
    os.remove(ps[0])   # iCloud evicts the original
    res = att.archive_attachments(db_path=db, output_dir=out, full=True, verbose=False)
    m = load_manifest(out)
    st = {a["attachment_id"]: a["status"] for a in m["attachments"]}
    check(res["missing_count"] == 0 and st[1] == "already_archived",
          f"archived-then-offloaded original stays already_archived (got {st[1]})")
    check(load_state(out)["last_attachment_rowid"] == 3,
          "state does not regress when an archived original is offloaded")
    v = att.verify_archive(db_path=db, output_dir=out, expect_drive=False, verbose=False)
    check(v["complete"] is True and v["offloaded"] == 0 and v["in_local"] == 3,
          "verify counts the archived copy of an offloaded original as present")
    os.remove(os.path.join(out, m["attachments"][0]["saved_path"]))
    v = att.verify_archive(db_path=db, output_dir=out, expect_drive=False, verbose=False)
    check(v["offloaded"] == 1 and v["in_local"] == 2,
          "verify reports offloaded only when the item is nowhere")

    # --- 9. categorize: Apple extensions + UTI ---
    check(att.categorize(None, "IMG.HEIC") == "photo", "categorize(None, 'IMG.HEIC') == photo")
    check(att.categorize(None, "Audio Message.caf") == "audio", "categorize .caf == audio")
    check(att.categorize(None, "clip.m4v") == "video", "categorize .m4v == video")
    check(att.categorize(None, None, "com.apple.quicktime-movie") == "video",
          "categorize by UTI (quicktime) == video")
    check(att.categorize(None, "x.pluginPayloadAttachment", "public.heic") == "photo",
          "categorize by UTI (public.heic) == photo")
    check(att.categorize("image/jpeg", "x.caf") == "photo", "mime type still wins")
    check(att.categorize(None, "x.pluginPayloadAttachment") == "file",
          "unknown extension without UTI stays file")

    # --- 10. Extension sanitizer ---
    check(att.safe_name_keep_ext("2024-01-15_0932_Mom_sticker.pluginPayloadAttachment")
          == "2024-01-15_0932_Mom_sticker.pluginPayloadAttachment",
          ".pluginPayloadAttachment extension is preserved")
    check(att.safe_name_keep_ext("2024-01-15_0932_Mom_v1.2 release notes")
          == "2024-01-15_0932_Mom_v1.2 release notes",
          "a dotted name with no real extension keeps its whole base")
    check(att.safe_name_keep_ext('x.jpg" onerror="alert(1)') == "x.jpg_ onerror__alert(1)",
          "a hostile extension is sanitized into the base")

    # --- 11. Direct chats are named by full identifier / contact, never last-4 ---
    db = os.path.join(tmp, "name.db")
    build_db_rows(db, [], chats=[(1, "+15551234567", None), (2, "chat9988", None)])
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    check(att.conversation_name(1, "+15551234567", None, cur) == "+15551234567",
          "direct chat with unknown number keeps the full identifier")
    att.core.CONTACTS_CACHE = {"5551234567": "Mom"}
    check(att.conversation_name(1, "+15551234567", None, cur) == "Mom",
          "direct chat resolves to the contact name")
    check(att.conversation_name(1, "chat9988", None, cur) == "Mom",
          "group chat is named by its participants")
    att.core.CONTACTS_CACHE = {}
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

        # --- Regression tests for reviewer findings ---
        regression_tests(check, os.path.join(tmp, "regress"))

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
