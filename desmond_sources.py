#!/usr/bin/env python3
"""
Desmond Sources — read a person's messages from WHEREVER they are, entirely
in memory. No export files, no Downloads folder: this is the module that
lets the family web flow work by just plugging a phone into the computer.

One call, five ways in:

    read_mac_messages()          this Mac's own Messages history (chat.db)
    read_iphone_backup(dir)      an iPhone plugged into this computer —
                                 reads the Finder/iTunes/Apple Devices
                                 backup (Mac AND Windows locations)
    read_android_usb()           an Android phone plugged in with USB
                                 debugging ON (via android_adb_exporter)
    parse_upload(data, name)     bytes dragged into the browser: a Desmond
                                 messages.json OR an SMS Backup & Restore
                                 XML read straight off the phone's storage
    detect_available()           what can this computer read right now?
                                 (drives the web wizard's source buttons)

Every reader returns the SAME standard Desmond export dict (the
messages.json shape), so any two of them federate — one parent on iPhone
and one on Android is exactly as easy as two iPhones.

Platform truth table
--------------------
- Mac + your own iPhone: nothing to plug in — Messages already syncs to
  chat.db on the Mac.
- Any computer + someone else's iPhone: plug it in, make a LOCAL backup
  (Finder on Mac; iTunes/Apple Devices on Windows — untick "encrypt"),
  and this reads it in place. The backup is Apple's own mechanism; nothing
  is installed on the phone.
- Any computer + an Android phone: USB debugging (60-second one-time
  setup) and it's read live over the cable — see android_adb_exporter.
- RCS chats on Android can't be read by ANY of these paths (Google keeps
  them private); SMS — where dentist/school reminders live — is captured.

Reactions/tapbacks are skipped here on purpose: these reads feed the
family gap differ, where a "Loved" tapback only one phone recorded would
show up as a fake missed message.
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime

from imessage_exporter_windows import (BACKUP_LOCATIONS, CONTACTS_DB_HASH,
                                       MESSAGES_DB_HASH, backup_file_path,
                                       convert_apple_time)

MAC_CHAT_DB = os.path.expanduser("~/Library/Messages/chat.db")

MAC_BACKUP_LOCATIONS = [
    os.path.expanduser("~/Library/Application Support/MobileSync/Backup"),
]


class SourceError(Exception):
    """A message source exists but can't be read; .args[0] says why in
    plain language (shown verbatim in the web UI)."""


# ---------------------------------------------------------------------------
# Shared iMessage database reader (Mac chat.db and backup sms.db are the
# same schema)
# ---------------------------------------------------------------------------

_QUERY = """
    SELECT
        message.ROWID, message.text, {body} message.date,
        message.is_from_me, message.handle_id,
        message.associated_message_type, message.cache_has_attachments,
        chat.chat_identifier, chat.display_name, chat.ROWID
    FROM message
    LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
    LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
    ORDER BY message.date ASC
"""


def _decode_body(attributed):
    """Newer macOS/iOS often leaves message.text NULL and stores the text in
    attributedBody. Reuse the exporter's decoder (import deferred: the Mac
    exporter module is always importable, but keep this file's import list
    honest about the dependency being one function)."""
    from imessage_exporter import decode_attributed_body
    return decode_attributed_body(attributed)


def read_imessage_db(db_path, lookup=None, source_label="iMessage"):
    """Read one iMessage/SMS sqlite database (Mac chat.db or a backup's
    sms.db) into a standard Desmond export dict. Read-only; skips
    tapbacks/reactions and empty-text rows (see module docstring).

    lookup: optional callable identifier -> contact name.
    """
    if not os.path.exists(db_path):
        raise SourceError(f"No messages database at {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        try:
            rows = cursor.execute(
                _QUERY.format(body="message.attributedBody,")).fetchall()
            has_body = True
        except sqlite3.OperationalError:
            # very old backups have no attributedBody column
            rows = cursor.execute(_QUERY.format(body="")).fetchall()
            has_body = False
    except sqlite3.DatabaseError as e:
        raise SourceError(
            f"Couldn't open the messages database ({e}). If this is an "
            "iPhone backup, it's probably ENCRYPTED — make a new backup "
            "with 'Encrypt local backup' unticked and try again.")

    handle_cache = {}

    def identifier_for(handle_id):
        if handle_id not in handle_cache:
            row = cursor.execute("SELECT id FROM handle WHERE ROWID = ?",
                                 (handle_id,)).fetchone()
            handle_cache[handle_id] = row[0] if row else None
        return handle_cache[handle_id]

    participants_cache = {}

    def participant_count(chat_rowid):
        if chat_rowid not in participants_cache:
            row = cursor.execute(
                "SELECT COUNT(*) FROM chat_handle_join WHERE chat_id = ?",
                (chat_rowid,)).fetchone()
            participants_cache[chat_rowid] = row[0] if row else 0
        return participants_cache[chat_rowid]

    def name_for(identifier):
        if not identifier:
            return None
        if lookup:
            name = lookup(identifier)
            if name and name != "Unknown" and name != identifier:
                return name
        return identifier

    messages = []
    conversations = {}
    for row in rows:
        if has_body:
            (_rowid, text, attributed, date, is_from_me, handle_id,
             assoc_type, has_att, chat_identifier, display_name,
             chat_rowid) = row
        else:
            (_rowid, text, date, is_from_me, handle_id, assoc_type,
             has_att, chat_identifier, display_name, chat_rowid) = row

        if assoc_type and assoc_type >= 2000:
            continue                       # tapback/reaction — see docstring
        if not text and has_body and attributed:
            text = _decode_body(attributed)
        if not text and not has_att:
            continue
        when = convert_apple_time(date)
        if when is None:
            continue

        is_group = bool(display_name) or (
            chat_rowid is not None and participant_count(chat_rowid) > 1)
        if is_group:
            conversation = display_name or f"Group chat {chat_rowid}"
            conv_type = "group"
            address = (identifier_for(handle_id) if handle_id else None) or ""
        else:
            ident = identifier_for(handle_id) if handle_id else chat_identifier
            conversation = name_for(ident or chat_identifier) or "Unknown"
            conv_type = "direct"
            # the counterpart's number/email — lets the family differ tell
            # "same contact name" apart from "same person"
            address = ident or chat_identifier or ""

        if is_from_me:
            sender = "Me"
        else:
            sender = name_for(identifier_for(handle_id)) if handle_id else conversation
            sender = sender or conversation

        text = text or "[attachment]"
        messages.append({
            "timestamp": when.isoformat(),
            "date": when.strftime("%Y-%m-%d"),
            "time": when.strftime("%H:%M:%S"),
            "conversation": conversation,
            "conversation_type": conv_type,
            "address": address,
            "sender": sender,
            "is_from_me": bool(is_from_me),
            "message_type": "text" if not has_att else "text_with_attachment",
            "text": text,
            "has_attachment": bool(has_att),
            "attachment_types": [],
            "reaction": None,
        })
        meta = conversations.setdefault(conversation, {
            "name": conversation, "type": conv_type, "message_count": 0,
            "first_message": when.isoformat(), "last_message": when.isoformat(),
        })
        meta["message_count"] += 1
        meta["last_message"] = when.isoformat()

    conn.close()
    messages.sort(key=lambda m: m["timestamp"])
    return {
        "export_date": datetime.now().isoformat(),
        "source": source_label,
        "total_messages": len(messages),
        "total_conversations": len(conversations),
        "conversations": list(conversations.values()),
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Source 1: this Mac's Messages
# ---------------------------------------------------------------------------

def read_mac_messages(db_path=None):
    """Messages already on this Mac (chat.db) — the zero-plug path for the
    parent whose computer this is."""
    path = db_path or MAC_CHAT_DB
    if not os.path.exists(path):
        raise SourceError(
            "This computer has no Messages database. Use the iPhone/Android "
            "plug-in options instead.")
    lookup = None
    if db_path is None:
        try:
            import imessage_exporter as mac
            mac.load_contacts()
            lookup = mac.lookup_contact_name
        except Exception:
            lookup = None       # names degrade to numbers, never fail
    return read_imessage_db(path, lookup, source_label="Messages on this Mac")


# ---------------------------------------------------------------------------
# Source 2: an iPhone plugged into this computer (its local backup)
# ---------------------------------------------------------------------------

def find_iphone_backups(locations=None):
    """Every readable iPhone backup on this computer (Mac + Windows backup
    folders). Returns [{path, name, date, has_messages, encrypted}] sorted
    newest first."""
    import plistlib
    found = []
    for base in (locations or (list(BACKUP_LOCATIONS) + MAC_BACKUP_LOCATIONS)):
        if not os.path.isdir(base):
            continue
        for item in sorted(os.listdir(base)):
            backup = os.path.join(base, item)
            if not os.path.isdir(backup):
                continue
            info_path = os.path.join(backup, "Info.plist")
            manifest = os.path.join(backup, "Manifest.plist")
            if not (os.path.exists(info_path) or os.path.exists(manifest)):
                continue
            rec = {"path": backup, "name": "iPhone", "date": None,
                   "has_messages": False, "encrypted": False}
            try:
                with open(info_path, "rb") as f:
                    info = plistlib.load(f)
                rec["name"] = info.get("Device Name") or "iPhone"
                d = info.get("Last Backup Date")
                rec["date"] = d.isoformat() if hasattr(d, "isoformat") else None
            except Exception:
                pass
            try:
                with open(manifest, "rb") as f:
                    rec["encrypted"] = bool(
                        plistlib.load(f).get("IsEncrypted"))
            except Exception:
                pass
            rec["has_messages"] = bool(
                backup_file_path(backup, MESSAGES_DB_HASH))
            found.append(rec)
    found.sort(key=lambda b: b["date"] or "", reverse=True)
    return found


def read_iphone_backup(backup_dir):
    """Read Messages out of one iPhone backup folder, contacts resolved from
    the backup's own address book."""
    msg_db = backup_file_path(backup_dir, MESSAGES_DB_HASH)
    if not msg_db:
        raise SourceError(
            "That backup has no readable messages database — it's most "
            "likely ENCRYPTED. Plug the iPhone in, untick 'Encrypt local "
            "backup' (Finder on Mac, iTunes/Apple Devices on Windows), "
            "back up again, then retry.")
    lookup = None
    try:
        import imessage_exporter_windows as win
        win.load_contacts(backup_dir)
        lookup = win.lookup_contact_name
    except Exception:
        lookup = None
    return read_imessage_db(msg_db, lookup,
                            source_label="iPhone backup (plugged in)")


# ---------------------------------------------------------------------------
# Source 3: an Android phone plugged in (USB debugging)
# ---------------------------------------------------------------------------

def read_android_usb(serial=None):
    """Read the plugged-in Android phone live over USB. Raises SourceError
    with the fix-it explanation when the phone isn't ready."""
    import android_adb_exporter as adb
    try:
        return adb.read_android_phone(serial=serial, quiet=True)
    except adb.AdbError as e:
        raise SourceError(str(e))


# ---------------------------------------------------------------------------
# Source 4: bytes dragged into the browser
# ---------------------------------------------------------------------------

def parse_upload(data, filename="upload"):
    """Whatever got dropped on the web page: a Desmond messages.json or an
    SMS Backup & Restore XML (read straight off the phone's mounted
    storage). Returns a standard export dict."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    head = data[:4096].lstrip()
    if head.startswith(b"{"):
        from desmond_federate import parse_export
        return parse_export(data, source=filename)
    if head.startswith(b"<"):
        import android_sms_exporter as axe
        messages, _calls = axe.parse_backup_bytes(data)
        if not messages:
            raise SourceError(
                f"{filename} looks like XML but contains no SMS/MMS — is it "
                "a calls-only backup? In SMS Backup & Restore, back up "
                "'Messages' and use that file.")
        export = axe.build_export_data(messages)
        export["source"] = f"SMS Backup & Restore ({filename})"
        return export
    raise SourceError(
        f"Couldn't recognize {filename}. Drop a Desmond messages.json or an "
        "SMS Backup & Restore .xml file.")


# ---------------------------------------------------------------------------
# What can this computer read right now?
# ---------------------------------------------------------------------------

def detect_available():
    """Snapshot of every message source this computer can currently reach —
    the web wizard calls this (and re-calls it when the user taps Rescan
    after plugging a phone in)."""
    out = {"platform": sys.platform,
           "mac_messages": os.path.exists(MAC_CHAT_DB),
           "iphone_backups": find_iphone_backups(),
           "adb_installed": False, "android_devices": []}
    try:
        import android_adb_exporter as adb
        path = adb.find_adb()
        out["adb_installed"] = bool(path)
        if path:
            out["android_devices"] = adb.list_devices()
    except Exception:
        pass
    return out
