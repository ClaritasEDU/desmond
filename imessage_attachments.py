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


def default_output_dir():
    """Prefer a Google Drive folder; otherwise fall back to ~/Downloads."""
    drive = find_google_drive_dir()
    if drive:
        return os.path.join(drive, ARCHIVE_FOLDER_NAME)
    return os.path.expanduser(f"~/Downloads/{ARCHIVE_FOLDER_NAME}")


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


def verify_archive(db_path=MESSAGES_DB, output_dir=None, verbose=True):
    """Reconcile Messages against the archive folder and report completeness.

    Answers: are all the attachments I *can* copy actually sitting in the
    archive (which should live in Google Drive)? What's still offloaded in
    iCloud? What needs another `--full` run?
    """
    output_dir = output_dir or default_output_dir()
    manifest_path = os.path.join(output_dir, "attachments.json")

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

    verified = 0
    missing_from_archive = 0   # on disk, should be archived, but file not found
    not_in_manifest = 0        # newer than the last archive run
    offloaded = 0              # in iCloud, not downloaded to this Mac
    size_warn = 0              # present but size differs (often "online only")

    for rid, info in expected.items():
        if not info["on_disk"]:
            offloaded += 1
            continue
        rec = man_by_id.get(rid)
        saved = rec.get("saved_path") if rec else None
        if not rec:
            not_in_manifest += 1
            continue
        if not saved:
            missing_from_archive += 1
            continue
        fpath = os.path.join(output_dir, saved)
        if os.path.exists(fpath):
            verified += 1
            try:
                if info["total"] and abs(os.path.getsize(fpath) - info["total"]) > 1024:
                    size_warn += 1
            except OSError:
                pass
        else:
            missing_from_archive += 1

    downloadable = sum(1 for i in expected if expected[i]["on_disk"])
    need_rerun = missing_from_archive + not_in_manifest
    complete = (verified == downloadable) and need_rerun == 0

    drive = find_google_drive_dir()
    in_drive = bool(drive) and _is_inside(output_dir, drive)

    result = {
        "complete": complete,
        "output_dir": output_dir,
        "in_drive": in_drive,
        "expected": len(expected),
        "downloadable": downloadable,
        "verified": verified,
        "offloaded": offloaded,
        "missing_from_archive": need_rerun,
        "size_warnings": size_warn,
    }

    if verbose:
        print("=" * 60)
        print("  Desmond — Verify backup")
        print("=" * 60)
        print(f"  Archive folder: {output_dir}")
        if in_drive:
            print("  ✓ This folder is inside your Google Drive — it syncs to Drive.")
        elif drive:
            print(f"  ⚠️  This folder is NOT inside your Google Drive ({drive}).")
            print("     Re-run the backup with:  --dest \"<your Google Drive folder>\"")
        else:
            print("  ℹ️  No Google Drive for desktop detected — this folder is on")
            print("     your computer. Drag it to drive.google.com to upload.")
        print("-" * 60)
        print(f"  Attachments in Messages:        {len(expected):,}")
        print(f"  Downloaded to this Mac:         {downloadable:,}")
        print(f"  ✓ Verified in the archive:      {verified:,}")
        if offloaded:
            print(f"  ⚠️  Offloaded in iCloud:         {offloaded:,} "
                  "(download these, then re-run --full)")
        if need_rerun:
            print(f"  ⚠️  Missing from archive:        {need_rerun:,} "
                  "(run --full to copy them)")
        if size_warn:
            print(f"  ℹ️  Size differs on {size_warn:,} file(s) — usually fine if "
                  "Google Drive set them to 'online only'.")
        print("-" * 60)
        if complete and not offloaded:
            print(f"  ✅ COMPLETE — all {downloadable:,} downloadable attachments "
                  "are in the archive.")
        elif complete:
            print(f"  ✅ All {downloadable:,} downloaded attachments are archived.")
            print(f"     ⚠️ {offloaded:,} are still offloaded in iCloud — download "
                  "them and re-run --full to include them.")
        else:
            print("  ⚠️  INCOMPLETE — run:  python3 imessage_attachments.py --full")
            print("      then verify again.")
        if in_drive:
            print("  Note: files in the Drive folder upload in the background — "
                  "open the Drive app / drive.google.com and confirm sync finished.")
        print("=" * 60)

    return result


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
                        help="Destination folder (e.g. your Google Drive folder). "
                             "Defaults to a detected Google Drive folder, else "
                             "~/Downloads/.")
    parser.add_argument("--db", metavar="PATH", default=MESSAGES_DB,
                        help="Path to chat.db (default: ~/Library/Messages/chat.db).")
    parser.add_argument("--verify", action="store_true",
                        help="Check that all attachments are in the (Drive) archive; "
                             "copy nothing. Exits non-zero if anything is missing.")
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
    types = {"photo", "video"} if args.photos_videos else None

    # Verify-only mode: just check the existing archive and report.
    if args.verify:
        res = verify_archive(db_path=args.db, output_dir=output_dir)
        sys.exit(0 if res.get("complete") else 1)

    drive = find_google_drive_dir()
    if args.dest:
        print(f"Destination: {output_dir}")
    elif drive:
        print(f"Detected Google Drive — archiving into:\n  {output_dir}")
    else:
        print("No Google Drive folder detected. Archiving locally into:\n"
              f"  {output_dir}\n"
              "(Install 'Google Drive for desktop' and re-run with --dest to "
              "store it on Drive.)")

    try:
        archive_attachments(
            db_path=args.db,
            output_dir=output_dir,
            full=args.full,
            types=types,
            dry_run=args.dry_run,
        )
        # Confirm the files actually landed (unless this was a dry run).
        if not args.dry_run and not args.no_verify:
            print("\nVerifying…")
            verify_archive(db_path=args.db, output_dir=output_dir)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("\nIf this is a permissions error: give Terminal Full Disk Access "
              "(System Settings → Privacy & Security), then restart Terminal.")
        sys.exit(1)


if __name__ == "__main__":
    main()
