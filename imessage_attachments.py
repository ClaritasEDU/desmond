#!/usr/bin/env python3
"""
Desmond — iMessage Attachment Archiver (macOS)

Copies the ACTUAL photos, videos, audio, and files out of Messages into a
browsable archive you can keep forever — and, ideally, drop straight onto
Google Drive. The main exporter only records that an attachment existed
("[photo]"); this tool saves the real bytes so you can go back, find, and
retrieve them later.

What it does
------------
- Reads ~/Library/Messages/chat.db **read-only** (never modifies Messages).
- For every message attachment, copies the real file from
  ~/Library/Messages/Attachments/... into an organized archive:

      <archive>/
      ├── ATTACHMENTS_INDEX.md     # human summary: counts, sizes, what's missing
      ├── attachments.json         # full manifest (every file -> message)
      ├── attachments.csv          # same, for spreadsheets
      ├── .attachments_state.json  # incremental state (re-runs only copy new)
      ├── Mom/
      │   ├── 2024-01-15_0932_IMG_1234.HEIC
      │   └── 2024-03-02_1810_video.MOV
      └── ...

- Files are named "<date>_<time>_<original-name>" so they sort chronologically
  and stay recognizable. The manifest links each file back to the conversation,
  sender, timestamp, and the message text it came with.

Google Drive
------------
This runs on your Mac (that's where Messages lives). To land the archive on
Google Drive, install "Google Drive for desktop" and point --dest at your Drive
folder; the Drive app uploads everything automatically. Desmond auto-detects a
Drive folder if one exists. Nothing is uploaded by this script itself.

iCloud note
-----------
If "Messages in iCloud" with "Optimize Mac Storage" has offloaded originals,
some attachment files won't be on disk yet. Those are reported as MISSING so
you know exactly what still needs to come down BEFORE you delete anything from
your phone.

Usage
-----
    python3 imessage_attachments.py --dry-run        # size preview, copies nothing
    python3 imessage_attachments.py                  # copy new attachments
    python3 imessage_attachments.py --full           # copy everything
    python3 imessage_attachments.py --photos-videos  # images + videos only
    python3 imessage_attachments.py --dest "/path/to/Google Drive/My Drive/Messages"
"""

import argparse
import csv
import glob
import json
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

# Reuse the battle-tested contact + timestamp helpers from the main exporter.
import imessage_exporter as core

MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")
ARCHIVE_FOLDER_NAME = "Desmond_Message_Attachments"

_contacts_loaded = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def human_size(num_bytes):
    """Render a byte count as a friendly string (KB / MB / GB)."""
    num = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024


def find_google_drive_dir():
    """Best-effort detection of a 'Google Drive for desktop' folder on macOS."""
    candidates = []
    # Modern client mounts under ~/Library/CloudStorage/GoogleDrive-<account>/
    candidates += glob.glob(
        os.path.expanduser("~/Library/CloudStorage/GoogleDrive-*/My Drive")
    )
    candidates += glob.glob(
        os.path.expanduser("~/Library/CloudStorage/GoogleDrive-*")
    )
    # Older client used ~/Google Drive/
    candidates.append(os.path.expanduser("~/Google Drive/My Drive"))
    candidates.append(os.path.expanduser("~/Google Drive"))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def default_local_dir():
    """Primary archive lives locally — safe to mirror UP to Drive from here
    (mirroring up from a real local copy avoids copying iCloud-offloaded stubs)."""
    return os.path.expanduser(f"~/Downloads/{ARCHIVE_FOLDER_NAME}")


def default_output_dir():
    return default_local_dir()


def drive_archive_dir(drive_dir=None):
    """The Google Drive mirror location, or None if no Drive folder is found."""
    drive = drive_dir or find_google_drive_dir()
    return os.path.join(drive, ARCHIVE_FOLDER_NAME) if drive else None


def mirror_tree(src_dir, dest_dir):
    """Incrementally copy src_dir → dest_dir (skip files already present with the
    same size). Returns the number of files newly copied/updated."""
    copied = 0
    for root, _dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        target_root = dest_dir if rel == "." else os.path.join(dest_dir, rel)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            s = os.path.join(root, name)
            d = os.path.join(target_root, name)
            try:
                if os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s):
                    continue
                shutil.copy2(s, d)
                copied += 1
            except Exception:
                pass
    return copied


def mirror_to_drive(src_dir, drive_dir=None):
    """Copy the local archive into Google Drive so attachments live in BOTH
    places. Returns the Drive destination, or None if there's no Drive folder."""
    if not os.path.isdir(src_dir):
        return None
    dest = drive_archive_dir(drive_dir)
    if not dest:
        print("\nNo Google Drive folder detected — attachments are saved locally:")
        print(f"  {src_dir}")
        print("Install 'Google Drive for desktop' (or pass --drive PATH) to also "
              "copy them to Drive.")
        return None
    n = mirror_tree(src_dir, dest)
    print(f"\nAttachments now live in BOTH places ({n:,} new/updated on Drive):")
    print(f"  Local:        {src_dir}")
    print(f"  Google Drive: {dest}")
    return dest


def decode_attributed_body(data):
    """Recover message text Apple stashes in the binary `attributedBody` field
    when `message.text` is NULL. (Mirrors the picker's proven decoder.)"""
    if not data:
        return None
    try:
        if isinstance(data, str):
            return data or None
        chunk = data.split(b"NSString")[1][5:]
        if chunk[0] == 0x81:
            length = int.from_bytes(chunk[1:3], "little")
            chunk = chunk[3:]
        else:
            length = chunk[0]
            chunk = chunk[1:]
        return chunk[:length].decode("utf-8", errors="ignore").strip() or None
    except Exception:
        return None


def categorize(mime_type):
    if not mime_type:
        return "file"
    if mime_type.startswith("image"):
        return "photo"
    if mime_type.startswith("video"):
        return "video"
    if mime_type.startswith("audio"):
        return "audio"
    return "file"


def safe_name(name):
    cleaned = "".join(
        c if c.isalnum() or c in (" ", "-", "_", "(", ")") else "_" for c in str(name)
    ).strip()
    return (cleaned or "Unknown")[:60]


def ensure_contacts():
    global _contacts_loaded
    if not _contacts_loaded:
        core.load_contacts()
        _contacts_loaded = True


def open_db(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def conversation_name(handle_id, chat_id, display_name, cursor):
    """Human-readable conversation name (matches the main exporter/picker)."""
    if display_name:
        return display_name
    if chat_id:
        name = core.lookup_contact_name(chat_id)
        if name == chat_id or str(name).startswith("chat"):
            participants = core.get_chat_participants(chat_id, cursor)
            return participants or chat_id
        return name
    if handle_id:
        return core.get_contact_name(handle_id, cursor)
    return "Unknown"


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
def load_state(state_file):
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_attachment_rowid": 0}


def save_state(state_file, state):
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f)


def iter_attachment_rows(cursor, since_rowid):
    cursor.execute(
        """
        SELECT attachment.ROWID, attachment.filename, attachment.mime_type,
               attachment.transfer_name, attachment.total_bytes,
               message.ROWID, message.date, message.is_from_me,
               message.handle_id, message.text, message.attributedBody,
               chat.chat_identifier, chat.display_name
        FROM attachment
        JOIN message_attachment_join
          ON attachment.ROWID = message_attachment_join.attachment_id
        JOIN message
          ON message_attachment_join.message_id = message.ROWID
        LEFT JOIN chat_message_join
          ON message.ROWID = chat_message_join.message_id
        LEFT JOIN chat
          ON chat_message_join.chat_id = chat.ROWID
        WHERE attachment.ROWID > ?
        ORDER BY attachment.ROWID ASC
        """,
        (since_rowid,),
    )
    return cursor.fetchall()


def archive_attachments(
    db_path=MESSAGES_DB,
    output_dir=None,
    state_file=None,
    full=False,
    types=None,
    dry_run=False,
    verbose=True,
):
    """Copy real attachment files into an organized archive.

    Returns a result dict with counts, total bytes, and the list of missing
    (offloaded) files. `types` optionally restricts categories, e.g.
    {"photo", "video"}.
    """
    output_dir = output_dir or default_output_dir()
    state_file = state_file or os.path.join(output_dir, ".attachments_state.json")

    ensure_contacts()
    state = load_state(state_file)
    since = 0 if full else state.get("last_attachment_rowid", 0)

    conn = open_db(db_path)
    cursor = conn.cursor()
    rows = iter_attachment_rows(cursor, since)

    records = []
    missing = []
    by_conversation = defaultdict(lambda: {"count": 0, "bytes": 0})
    by_category = defaultdict(lambda: {"count": 0, "bytes": 0})
    copied_count = 0
    copied_bytes = 0
    skipped_existing = 0
    max_rowid = since

    if not dry_run:
        os.makedirs(output_dir, exist_ok=True)

    for row in rows:
        (att_id, filename, mime_type, transfer_name, total_bytes,
         msg_id, date, is_from_me, handle_id, text, attributed,
         chat_id, display_name) = row

        max_rowid = max(max_rowid, att_id)

        category = categorize(mime_type)
        if types and category not in types:
            continue

        if not filename:
            # No on-disk file reference at all (rare) — record as missing.
            missing.append({"attachment_id": att_id, "name": transfer_name or "(unknown)",
                            "reason": "no file path in database"})
            continue

        src = os.path.expanduser(filename)
        conv = str(conversation_name(handle_id, chat_id, display_name, cursor))
        conv_clean = safe_name(conv)

        dt = core.convert_apple_time(date)
        when = dt.strftime("%Y-%m-%d_%H%M") if dt else "0000-00-00_0000"
        date_str = dt.strftime("%Y-%m-%d") if dt else ""
        time_str = dt.strftime("%H:%M:%S") if dt else ""

        original = transfer_name or os.path.basename(src) or f"attachment_{att_id}"
        # Date/time stamp FIRST, then the people in the chat, then the original name.
        target_name = safe_name_keep_ext(f"{when}_{conv_clean[:40]}_{original}")

        # Resolve message text for context.
        msg_text = text or decode_attributed_body(attributed) or ""

        exists = os.path.exists(src)
        size = None
        if exists:
            try:
                size = os.path.getsize(src)
            except OSError:
                size = total_bytes
        if size is None:
            size = total_bytes or 0

        record = {
            "attachment_id": att_id,
            "conversation": conv,
            "sender": "Me" if is_from_me else conv,
            "is_from_me": bool(is_from_me),
            "timestamp": dt.isoformat() if dt else None,
            "date": date_str,
            "time": time_str,
            "category": category,
            "mime_type": mime_type,
            "original_name": original,
            "size_bytes": size,
            "message_text": (msg_text[:280] if msg_text else ""),
        }

        if not exists:
            record["status"] = "missing"
            record["saved_path"] = None
            missing.append({
                "attachment_id": att_id,
                "conversation": conv,
                "name": original,
                "expected_path": src,
                "reason": "not downloaded (likely offloaded to iCloud)",
            })
            records.append(record)
            continue

        rel_path = os.path.join(conv_clean, target_name)
        dest = os.path.join(output_dir, rel_path)
        record["saved_path"] = rel_path

        if dry_run:
            record["status"] = "would_copy"
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            # Avoid collisions and avoid re-copying identical files.
            if os.path.exists(dest) and os.path.getsize(dest) == size:
                record["status"] = "already_archived"
                skipped_existing += 1
            else:
                if os.path.exists(dest):
                    base, ext = os.path.splitext(dest)
                    dest = f"{base}_{att_id}{ext}"
                    record["saved_path"] = os.path.relpath(dest, output_dir)
                try:
                    shutil.copy2(src, dest)
                    record["status"] = "copied"
                except Exception as e:
                    record["status"] = f"error: {e}"
                    records.append(record)
                    continue

        if record["status"] in ("copied", "would_copy"):
            copied_count += 1
            copied_bytes += size
            by_conversation[conv]["count"] += 1
            by_conversation[conv]["bytes"] += size
            by_category[category]["count"] += 1
            by_category[category]["bytes"] += size

        records.append(record)

    conn.close()

    result = {
        "output_dir": output_dir,
        "dry_run": dry_run,
        "full": full,
        "total_attachments_seen": len(records),
        "copied_count": copied_count,
        "copied_bytes": copied_bytes,
        "copied_human": human_size(copied_bytes),
        "skipped_existing": skipped_existing,
        "missing_count": len(missing),
        "missing": missing,
        "by_conversation": by_conversation,
        "by_category": by_category,
    }

    if not dry_run:
        # Keep the manifest CUMULATIVE: merge this run's records with any
        # existing manifest so incremental runs never lose earlier history.
        merged = {}
        existing = os.path.join(output_dir, "attachments.json")
        if not full and os.path.exists(existing):
            try:
                with open(existing, encoding="utf-8") as ef:
                    for a in json.load(ef).get("attachments", []):
                        if a.get("attachment_id") is not None:
                            merged[a["attachment_id"]] = a
            except Exception:
                pass
        for r in records:
            merged[r["attachment_id"]] = r
        write_manifests(list(merged.values()), output_dir)
        state["last_attachment_rowid"] = max_rowid
        state["last_run"] = datetime.now().isoformat()
        save_state(state_file, state)

    if verbose:
        print_summary(result)

    return result


def safe_name_keep_ext(name):
    """Sanitize a filename but preserve its extension."""
    base, ext = os.path.splitext(name)
    cleaned = "".join(
        c if c.isalnum() or c in (" ", "-", "_", "(", ")", ".") else "_" for c in base
    ).strip()
    return (cleaned or "attachment")[:120] + ext


def write_manifests(records, output_dir):
    """Write JSON + CSV manifests and a human-readable index from the full
    (cumulative) set of archive records."""
    copied = [r for r in records if r.get("status") in ("copied", "already_archived")]
    missing = [r for r in records if r.get("status") == "missing"]
    total_bytes = sum(r.get("size_bytes") or 0 for r in copied)

    by_category = defaultdict(lambda: {"count": 0, "bytes": 0})
    by_conversation = defaultdict(lambda: {"count": 0, "bytes": 0})
    for r in copied:
        size = r.get("size_bytes") or 0
        cat = r.get("category", "file")
        by_category[cat]["count"] += 1
        by_category[cat]["bytes"] += size
        conv = r.get("conversation", "?")
        by_conversation[conv]["count"] += 1
        by_conversation[conv]["bytes"] += size

    # JSON
    with open(os.path.join(output_dir, "attachments.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated": datetime.now().isoformat(),
                "output_dir": output_dir,
                "copied_count": len(copied),
                "copied_bytes": total_bytes,
                "missing_count": len(missing),
                "attachments": records,
            },
            f,
            indent=2,
        )

    # CSV
    fields = ["attachment_id", "conversation", "sender", "is_from_me", "timestamp",
              "date", "time", "category", "mime_type", "original_name",
              "size_bytes", "saved_path", "status", "message_text"]
    with open(os.path.join(output_dir, "attachments.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)

    # Markdown index
    with open(os.path.join(output_dir, "ATTACHMENTS_INDEX.md"), "w", encoding="utf-8") as f:
        f.write("# Message Attachments Archive\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"**Archived files:** {len(copied):,} ({human_size(total_bytes)})\n")
        f.write(f"**Missing / not downloaded:** {len(missing):,}\n\n")

        f.write("## By type\n\n")
        for cat, stats in sorted(by_category.items(), key=lambda x: -x[1]["bytes"]):
            f.write(f"- **{cat}:** {stats['count']:,} ({human_size(stats['bytes'])})\n")

        f.write("\n## Top conversations (by size)\n\n")
        for conv, stats in sorted(by_conversation.items(),
                                  key=lambda x: -x[1]["bytes"])[:25]:
            f.write(f"- **{conv}**: {stats['count']:,} files "
                    f"({human_size(stats['bytes'])})\n")

        if missing:
            f.write("\n## ⚠️ Missing (likely offloaded to iCloud)\n\n")
            f.write("These attachments are referenced by Messages but the file "
                    "isn't on this Mac yet. Re-download them (open the thread, or "
                    "turn off Settings → Apple ID → iCloud → Messages → "
                    "\"Optimize Mac Storage\", or run `desmond.sh` to sync) and "
                    "run this again **before deleting anything from your phone.**\n\n")
            for m in missing[:200]:
                f.write(f"- {m.get('conversation', '?')}: "
                        f"{m.get('original_name', '?')}\n")
            if len(missing) > 200:
                f.write(f"- … and {len(missing) - 200:,} more "
                        f"(see attachments.csv, status=missing)\n")

        f.write("\n## How to find a file\n\n")
        f.write("- Browse the per-conversation folders (named by contact).\n")
        f.write("- Files are named `YYYY-MM-DD_HHMM_<people>_originalname`.\n")
        f.write("- Or open `attachments.csv` and filter by conversation, date, "
                "or type, then follow `saved_path`.\n")


def print_summary(result):
    print()
    print("=" * 60)
    mode = "DRY RUN (nothing copied)" if result["dry_run"] else "Archive complete"
    print(f"  Desmond — Attachment Archiver — {mode}")
    print("=" * 60)
    verb = "Would copy" if result["dry_run"] else "Copied"
    print(f"  {verb}: {result['copied_count']:,} files "
          f"({result['copied_human']})")
    if result["skipped_existing"]:
        print(f"  Already archived (skipped): {result['skipped_existing']:,}")
    if result["missing_count"]:
        print(f"  ⚠️  Missing / not downloaded: {result['missing_count']:,} "
              f"(see ATTACHMENTS_INDEX.md — re-download before deleting from phone)")
    print(f"  Destination: {result['output_dir']}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Verification — did everything actually make it into the (Drive) archive?
# ---------------------------------------------------------------------------
def _is_inside(child, parent):
    try:
        child = os.path.abspath(child)
        parent = os.path.abspath(parent)
        return os.path.commonpath([child, parent]) == parent
    except (ValueError, TypeError):
        return False


def all_attachment_rows(cursor):
    """Every attachment that belongs to a message (the archiver's scope)."""
    cursor.execute("""
        SELECT attachment.ROWID, attachment.filename, attachment.total_bytes
        FROM attachment
        JOIN message_attachment_join
          ON attachment.ROWID = message_attachment_join.attachment_id
    """)
    return cursor.fetchall()


def verify_archive(db_path=MESSAGES_DB, output_dir=None, drive_dir=None,
                   drive_mirror=None, expect_drive=True, verbose=True,
                   write_report=True):
    """Three-way reconciliation: Messages (the device) vs the LOCAL archive vs
    the GOOGLE DRIVE mirror. Confirms every downloadable attachment is present in
    all three places, and flags what's offloaded in iCloud or missing anywhere.

    `drive_mirror` may be passed explicitly (e.g. by the unified exporter, whose
    Drive folder differs); otherwise it's derived from `drive_dir`.
    """
    output_dir = output_dir or default_output_dir()
    manifest_path = os.path.join(output_dir, "attachments.json")
    if drive_mirror is None:
        drive_mirror = drive_archive_dir(drive_dir) if expect_drive else None

    if not os.path.exists(manifest_path):
        if verbose:
            print("=" * 60)
            print("  Desmond — Verify — no archive found")
            print("=" * 60)
            print(f"  Looked in: {output_dir}")
            print("  No attachments.json manifest here yet.")
            print("  Run the backup first:  python3 imessage_attachments.py --full")
            print("=" * 60)
        return {"complete": False, "reason": "no_manifest", "output_dir": output_dir}

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    man_by_id = {a.get("attachment_id"): a for a in manifest.get("attachments", [])}

    conn = open_db(db_path)
    cursor = conn.cursor()
    rows = all_attachment_rows(cursor)
    conn.close()

    expected = {}
    for rid, filename, total in rows:
        expected[rid] = {
            "total": total,
            "on_disk": bool(filename) and os.path.exists(os.path.expanduser(filename)),
        }

    downloadable = in_local = in_drive = offloaded = size_warn = 0
    offloaded_list, missing_local_list, missing_drive_list = [], [], []

    def detail(rid):
        rec = man_by_id.get(rid) or {}
        return {
            "attachment_id": rid,
            "conversation": rec.get("conversation"),
            "date": rec.get("date"),
            "original_name": rec.get("original_name"),
            "saved_path": rec.get("saved_path"),
        }

    for rid, info in expected.items():
        if not info["on_disk"]:
            offloaded += 1            # referenced on the device, not downloaded
            offloaded_list.append(detail(rid))
            continue
        downloadable += 1
        rec = man_by_id.get(rid)
        saved = rec.get("saved_path") if rec else None
        if not saved:
            missing_local_list.append(detail(rid))
            if drive_mirror is not None:
                missing_drive_list.append(detail(rid))
            continue
        if os.path.exists(os.path.join(output_dir, saved)):
            in_local += 1
            try:
                if info["total"] and abs(
                        os.path.getsize(os.path.join(output_dir, saved))
                        - info["total"]) > 1024:
                    size_warn += 1
            except OSError:
                pass
        else:
            missing_local_list.append(detail(rid))
        if drive_mirror is not None:
            if os.path.exists(os.path.join(drive_mirror, saved)):
                in_drive += 1
            else:
                missing_drive_list.append(detail(rid))

    missing_local = len(missing_local_list)
    missing_drive = len(missing_drive_list)
    local_ok = in_local == downloadable
    drive_present = drive_mirror is not None and os.path.isdir(drive_mirror)
    drive_ok = drive_present and in_drive == downloadable
    if expect_drive:
        complete = local_ok and drive_ok
    else:
        complete = local_ok

    result = {
        "complete": complete,
        "output_dir": output_dir,
        "drive_dir": drive_mirror,
        "expect_drive": expect_drive,
        "expected": len(expected),
        "downloadable": downloadable,
        "in_local": in_local,
        "in_drive": in_drive,
        "offloaded": offloaded,
        "missing_local": missing_local,
        "missing_drive": missing_drive,
        "size_warnings": size_warn,
        "offloaded_list": offloaded_list,
        "missing_local_list": missing_local_list,
        "missing_drive_list": missing_drive_list,
        # legacy aliases
        "verified": in_local,
        "missing_from_archive": missing_local,
    }

    if verbose:
        print("=" * 64)
        print("  Desmond — Verify backup (device vs local vs Google Drive)")
        print("=" * 64)
        print(f"  ON THE DEVICE (Messages):  {len(expected):,} attachments "
              f"— {downloadable:,} downloaded, {offloaded:,} offloaded in iCloud")
        ok = "✅" if local_ok else "⚠️ "
        print(f"  IN LOCAL ARCHIVE:          {in_local:,} / {downloadable:,}  {ok}"
              f"  ({output_dir})")
        if not expect_drive:
            print("  ON GOOGLE DRIVE:           (skipped — --no-drive)")
        elif not drive_present:
            print("  ON GOOGLE DRIVE:           ⚠️  no Drive mirror found "
                  "(install Google Drive for desktop / use --drive PATH)")
        else:
            ok = "✅" if drive_ok else "⚠️ "
            print(f"  ON GOOGLE DRIVE:           {in_drive:,} / {downloadable:,}  {ok}"
                  f"  ({drive_mirror})")
        print("-" * 64)
        if missing_local:
            print(f"  ⚠️  Missing from LOCAL:     {missing_local:,} "
                  "→ run:  python3 imessage_attachments.py --full")
        if expect_drive and drive_present and missing_drive:
            print(f"  ⚠️  Missing from DRIVE:     {missing_drive:,} "
                  "→ re-run the backup to mirror them up")
        if size_warn:
            print(f"  ℹ️  {size_warn:,} file(s) differ in size — usually fine if "
                  "Drive set them to 'online only'.")
        if complete and offloaded == 0:
            print(f"  ✅ ALL {downloadable:,} attachments are present in all three "
                  "places.")
        elif complete:
            print(f"  ✅ All {downloadable:,} downloaded attachments are in local "
                  + ("+ Drive" if expect_drive else "") + ".")
            print(f"  ⚠️  {offloaded:,} are still offloaded in iCloud — download "
                  "them, re-run --full, and verify again.")
        else:
            print("  ⚠️  NOT fully in sync yet — follow the steps above, then "
                  "verify again.")
        if drive_present:
            print("  Note: Drive uploads in the background — confirm sync finished "
                  "in the Drive app before clearing space on your phone.")
        print("=" * 64)

    if write_report and os.path.isdir(output_dir):
        report_path = write_verify_report(result, output_dir)
        result["report_path"] = report_path
        if verbose:
            print(f"  Full diff + counts written to: {report_path}")

    return result


def write_verify_report(result, output_dir):
    """Write VERIFY_REPORT.md + verify_diff.json: per-place counts and the exact
    list of what's missing where, so re-runs can close the gap."""
    json_path = os.path.join(output_dir, "verify_diff.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "complete": result["complete"],
            "counts": {
                "device_total": result["expected"],
                "device_downloaded": result["downloadable"],
                "device_offloaded": result["offloaded"],
                "in_local": result["in_local"],
                "in_drive": result["in_drive"],
                "missing_local": result["missing_local"],
                "missing_drive": result["missing_drive"],
            },
            "missing_local": result["missing_local_list"],
            "missing_drive": result["missing_drive_list"],
            "offloaded": result["offloaded_list"],
        }, f, indent=2)

    def _rows(items):
        out = []
        for m in items[:1000]:
            who = m.get("conversation") or "?"
            when = m.get("date") or "?"
            name = m.get("original_name") or f"id {m.get('attachment_id')}"
            out.append(f"- {when} · {who} · {name} (id {m.get('attachment_id')})")
        if len(items) > 1000:
            out.append(f"- … and {len(items) - 1000:,} more (see verify_diff.json)")
        return "\n".join(out) if out else "- (none)"

    md_path = os.path.join(output_dir, "VERIFY_REPORT.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Backup Verification Report\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("## How many attachments in each place\n\n")
        f.write("| Place | Attachments |\n|---|---|\n")
        f.write(f"| On the device (Messages) | {result['expected']:,} "
                f"({result['downloadable']:,} downloaded, "
                f"{result['offloaded']:,} offloaded in iCloud) |\n")
        f.write(f"| Local archive | {result['in_local']:,} / "
                f"{result['downloadable']:,} |\n")
        drive_cell = (f"{result['in_drive']:,} / {result['downloadable']:,}"
                      if result.get("drive_dir") else "no Drive mirror found")
        f.write(f"| Google Drive | {drive_cell} |\n\n")
        verdict = ("✅ All downloaded attachments are present in every place."
                   if result["complete"] and result["offloaded"] == 0 else
                   "✅ Local + Drive complete; some still offloaded in iCloud."
                   if result["complete"] else
                   "⚠️ Not fully in sync — see the diff below, then re-run.")
        f.write(f"**Verdict:** {verdict}\n\n")
        f.write("## To finish the archive\n\n")
        f.write("1. Re-run the backup to copy/mirror anything missing:\n"
                "   `python3 imessage_attachments.py --full`\n"
                "   (or `--retry` to loop until complete).\n"
                "2. For offloaded items, download them in Messages first "
                "(turn off \"Optimize Mac Storage\" or open the threads), then re-run.\n\n")
        f.write(f"## Missing from LOCAL archive ({result['missing_local']:,})\n\n")
        f.write(_rows(result["missing_local_list"]) + "\n\n")
        f.write(f"## Missing from GOOGLE DRIVE ({result['missing_drive']:,})\n\n")
        f.write(_rows(result["missing_drive_list"]) + "\n\n")
        f.write(f"## Offloaded in iCloud — not downloaded yet "
                f"({result['offloaded']:,})\n\n")
        f.write(_rows(result["offloaded_list"]) + "\n")
    return md_path


def main():
    parser = argparse.ArgumentParser(
        description="Archive iMessage photos/videos/files into a browsable folder "
                    "(ideal for Google Drive).")
    parser.add_argument("--full", action="store_true",
                        help="Copy ALL attachments (ignore incremental state).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report how many files / how much space, copy nothing.")
    parser.add_argument("--photos-videos", action="store_true",
                        help="Only images and videos (skip audio/files).")
    parser.add_argument("--dest", metavar="PATH",
                        help="Local archive folder (the primary copy). "
                             "Default: ~/Downloads/Desmond_Message_Attachments.")
    parser.add_argument("--drive", metavar="PATH",
                        help="Google Drive folder to also mirror into "
                             "(default: auto-detected Google Drive for desktop).")
    parser.add_argument("--no-drive", action="store_true",
                        help="Keep the local copy only; don't mirror to Google Drive.")
    parser.add_argument("--db", metavar="PATH", default=MESSAGES_DB,
                        help="Path to chat.db (default: ~/Library/Messages/chat.db).")
    parser.add_argument("--verify", action="store_true",
                        help="Three-way check (device vs local vs Drive); writes a "
                             "report, copies nothing. Non-zero exit if incomplete.")
    parser.add_argument("--retry", nargs="?", type=int, const=3, default=None,
                        metavar="N",
                        help="Back up + mirror + verify in a loop (default 3 passes) "
                             "until local & Drive are complete.")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the automatic verification pass after archiving.")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print("Could not find your Messages database at "
              f"{args.db}")
        print("This tool runs on a Mac with the Messages app set up.")
        print("If Terminal lacks access: System Settings → Privacy & Security → "
              "Full Disk Access → enable Terminal, then restart it.")
        sys.exit(1)

    output_dir = os.path.expanduser(args.dest) if args.dest else default_output_dir()
    drive_override = os.path.expanduser(args.drive) if args.drive else None
    types = {"photo", "video"} if args.photos_videos else None
    expect_drive = not args.no_drive

    # Verify-only mode: three-way report (device vs local vs Drive), copy nothing.
    if args.verify:
        res = verify_archive(db_path=args.db, output_dir=output_dir,
                             drive_dir=drive_override, expect_drive=expect_drive)
        sys.exit(0 if res.get("complete") else 1)

    def run_once(full):
        print(f"\nArchiving locally to: {output_dir}")
        if expect_drive:
            dm = drive_archive_dir(drive_override)
            print(f"…and mirroring to Google Drive: {dm}" if dm else
                  "(No Google Drive detected — saving locally only. Install Google "
                  "Drive for desktop or pass --drive PATH.)")
        archive_attachments(db_path=args.db, output_dir=output_dir,
                            full=full, types=types, dry_run=args.dry_run)
        if args.dry_run:
            return None
        if expect_drive:
            mirror_to_drive(output_dir, drive_override)
        if args.no_verify:
            return None
        print("\nVerifying (device vs local vs Google Drive)…")
        return verify_archive(db_path=args.db, output_dir=output_dir,
                              drive_dir=drive_override, expect_drive=expect_drive)

    try:
        if args.retry is not None and not args.dry_run:
            attempts = max(1, args.retry)
            res = None
            for i in range(attempts):
                print(f"\n===== Attempt {i + 1} of {attempts} =====")
                res = run_once(full=True)
                if res and res.get("complete"):
                    print(f"\n✅ Archive complete after {i + 1} pass(es).")
                    break
            else:
                print(f"\n⚠️  Still incomplete after {attempts} passes — see "
                      "VERIFY_REPORT.md. Download any offloaded iCloud items, then "
                      "run --retry again.")
            sys.exit(0 if (res and res.get("complete")) else 1)
        else:
            run_once(full=args.full)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("\nIf this is a permissions error: give Terminal Full Disk Access "
              "(System Settings → Privacy & Security), then restart Terminal.")
        sys.exit(1)


if __name__ == "__main__":
    main()
