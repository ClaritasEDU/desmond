#!/usr/bin/env python3
"""
iMessage Exporter for Windows
Exports your iMessages from iPhone backups to AI-ready formats.

This script works with unencrypted iPhone backups created via:
- iTunes (Windows 10 and earlier)
- Apple Devices app (Windows 11)

Your iPhone backup contains your complete iMessage/SMS history.
"""

import sqlite3
import os
import json
import re
import shutil
import plistlib
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Configuration - Default Windows backup locations
BACKUP_LOCATIONS = [
    # iTunes backup location (most common)
    os.path.expandvars(r"%APPDATA%\Apple Computer\MobileSync\Backup"),
    # Apple Devices app (Windows 11)
    os.path.expandvars(r"%USERPROFILE%\Apple\MobileSync\Backup"),
]

# The Messages database in iPhone backups has this hashed filename
# This is the SHA1 hash of "HomeDomain-Library/SMS/sms.db"
MESSAGES_DB_HASH = "3d0d7e5fb2ce288813306e4d4636395e047a3d28"

# Contacts database hash (HomeDomain-Library/AddressBook/AddressBook.sqlitedb)
CONTACTS_DB_HASH = "31bb7ba8914766d4ba40d6dfb6113c8b614be442"

# Output configuration
OUTPUT_DIR = os.path.expanduser("~/Documents/iMessages_Export")
STATE_FILE = os.path.join(OUTPUT_DIR, ".export_state.json")

# Global contact lookup cache
CONTACTS_CACHE = {}


def find_backup_directory():
    """Find the most recent iPhone backup directory."""
    print("Searching for iPhone backups...")

    backups_found = []

    for base_location in BACKUP_LOCATIONS:
        if os.path.exists(base_location):
            print(f"  Checking: {base_location}")
            for item in os.listdir(base_location):
                backup_path = os.path.join(base_location, item)
                if os.path.isdir(backup_path):
                    # Check if this looks like an iPhone backup
                    manifest_path = os.path.join(backup_path, "Manifest.plist")
                    info_path = os.path.join(backup_path, "Info.plist")

                    if os.path.exists(manifest_path) or os.path.exists(info_path):
                        # Get backup info
                        backup_info = {"path": backup_path, "name": "Unknown Device"}

                        try:
                            if os.path.exists(info_path):
                                with open(info_path, 'rb') as f:
                                    info = plistlib.load(f)
                                    backup_info["name"] = info.get("Device Name", "Unknown Device")
                                    backup_info["date"] = info.get("Last Backup Date")
                                    backup_info["product"] = info.get("Product Type", "")
                        except Exception as e:
                            print(f"    Warning: Could not read backup info: {e}")

                        # Check for messages database (flat = iOS ≤9,
                        # sharded 3d/3d0d… = iOS 10+)
                        msg_db = backup_file_path(backup_path, MESSAGES_DB_HASH)
                        if msg_db:
                            backup_info["has_messages"] = True
                            backups_found.append(backup_info)
                            print(f"    Found: {backup_info['name']}")
                        else:
                            print(f"    Skipped: {backup_info['name']} (no messages database - may be encrypted)")

    if not backups_found:
        return None

    # Sort by date if available, return most recent
    backups_with_dates = [b for b in backups_found if b.get("date")]
    if backups_with_dates:
        backups_with_dates.sort(key=lambda x: x["date"], reverse=True)
        return backups_with_dates[0]["path"]

    return backups_found[0]["path"]


def load_contacts(backup_dir):
    """Load contacts from the iPhone backup."""
    global CONTACTS_CACHE

    print("Loading contacts from backup...")

    contacts_db = backup_file_path(backup_dir, CONTACTS_DB_HASH)

    if not contacts_db:
        print("  Note: Contacts database not found in backup. Using phone numbers/emails.")
        return

    try:
        # Copy database to temp location to avoid lock issues
        temp_db = os.path.join(os.environ.get("TEMP", "/tmp"), "contacts_temp.db")
        shutil.copy2(contacts_db, temp_db)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Get phone numbers with contact names
        try:
            cursor.execute("""
                SELECT
                    ABPerson.First,
                    ABPerson.Last,
                    ABMultiValue.value
                FROM ABPerson
                LEFT JOIN ABMultiValue ON ABPerson.ROWID = ABMultiValue.record_id
                WHERE ABMultiValue.property = 3
            """)

            for row in cursor.fetchall():
                first_name, last_name, phone = row
                name_parts = [p for p in [first_name, last_name] if p]
                if name_parts and phone:
                    name = " ".join(name_parts)
                    # Normalize phone number (remove all non-digits)
                    normalized_phone = re.sub(r'\D', '', phone)
                    # Store with last 10 digits as key (handles country code variations)
                    if len(normalized_phone) >= 10:
                        CONTACTS_CACHE[normalized_phone[-10:]] = name
                    if normalized_phone:
                        CONTACTS_CACHE[normalized_phone] = name
        except Exception as e:
            print(f"  Phone lookup error: {e}")

        # Get email addresses with contact names
        try:
            cursor.execute("""
                SELECT
                    ABPerson.First,
                    ABPerson.Last,
                    ABMultiValue.value
                FROM ABPerson
                LEFT JOIN ABMultiValue ON ABPerson.ROWID = ABMultiValue.record_id
                WHERE ABMultiValue.property = 4
            """)

            for row in cursor.fetchall():
                first_name, last_name, email = row
                name_parts = [p for p in [first_name, last_name] if p]
                if name_parts and email:
                    CONTACTS_CACHE[email.lower()] = " ".join(name_parts)
        except Exception as e:
            print(f"  Email lookup error: {e}")

        conn.close()

        # Clean up temp file
        os.remove(temp_db)

    except Exception as e:
        print(f"  Error reading contacts: {e}")

    print(f"  Loaded {len(CONTACTS_CACHE)} contact mappings.")


def lookup_contact_name(identifier):
    """Look up a contact name from phone number or email."""
    if not identifier:
        return "Unknown"

    # Try direct match (for emails)
    if identifier.lower() in CONTACTS_CACHE:
        return CONTACTS_CACHE[identifier.lower()]

    # Try phone number lookup
    normalized = re.sub(r'\D', '', identifier)
    if normalized in CONTACTS_CACHE:
        return CONTACTS_CACHE[normalized]

    # Try last 10 digits
    if len(normalized) >= 10 and normalized[-10:] in CONTACTS_CACHE:
        return CONTACTS_CACHE[normalized[-10:]]

    # Return original identifier if no match
    return identifier


def get_contact_name(handle_id, cursor):
    """Get the phone number or email for a handle, then look up contact name."""
    cursor.execute("SELECT id FROM handle WHERE ROWID = ?", (handle_id,))
    result = cursor.fetchone()
    if result:
        return lookup_contact_name(result[0])
    return "Unknown"


def get_chat_participants(chat_id, cursor):
    """Get participant names for a group chat."""
    # First get the chat ROWID from the chat_identifier
    cursor.execute("SELECT ROWID FROM chat WHERE chat_identifier = ?", (chat_id,))
    chat_row = cursor.fetchone()
    if not chat_row:
        return None

    chat_rowid = chat_row[0]

    # Get all handles (participants) for this chat
    cursor.execute("""
        SELECT handle.id
        FROM handle
        JOIN chat_handle_join ON handle.ROWID = chat_handle_join.handle_id
        WHERE chat_handle_join.chat_id = ?
    """, (chat_rowid,))

    participants = []
    for row in cursor.fetchall():
        handle_id = row[0]
        name = lookup_contact_name(handle_id)
        # Only add if we got a real name (not the phone number back)
        if name and name != handle_id:
            participants.append(name)
        elif name:
            # Got phone number back, abbreviate it
            participants.append(name[-4:] if len(name) > 4 else name)

    if participants:
        # Limit to first 3 names to keep folder names reasonable
        if len(participants) > 3:
            return ", ".join(participants[:3]) + f" +{len(participants)-3}"
        return ", ".join(participants)

    return None


def backup_file_path(backup_dir, file_hash):
    """Resolve a file inside an iPhone backup. iOS 9 and earlier stored files
    flat; every backup since iOS 10 shards them into two-hex-char subfolders
    (e.g. 3d/3d0d7e5f...). Returns whichever exists, else None."""
    flat = os.path.join(backup_dir, file_hash)
    if os.path.exists(flat):
        return flat
    sharded = os.path.join(backup_dir, file_hash[:2], file_hash)
    if os.path.exists(sharded):
        return sharded
    return None


def convert_apple_time(apple_timestamp):
    """Convert Apple's timestamp format to readable datetime.

    Modern iOS stores message.date as NANOSECONDS since 2001-01-01; older
    backups used SECONDS. Detect by magnitude, and never let one corrupt row
    abort an entire export."""
    if apple_timestamp is None:
        return None
    try:
        ts = float(apple_timestamp)
        if abs(ts) > 1e12:          # nanoseconds (seconds would be ~1e9)
            ts /= 1_000_000_000
        return datetime.fromtimestamp(ts + 978307200)
    except (ValueError, OverflowError, OSError):
        return None


def load_state():
    """Load the last export state."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"last_message_rowid": 0}


def save_state(state):
    """Save the export state."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def export_messages(messages_db_path, full_export=False):
    """Export messages to markdown files."""

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load state
    state = load_state()
    last_rowid = 0 if full_export else state.get("last_message_rowid", 0)

    # Copy database to temp location (iPhone backup files may be locked)
    temp_db = os.path.join(os.environ.get("TEMP", "/tmp"), "messages_temp.db")
    shutil.copy2(messages_db_path, temp_db)

    # Connect to database
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Get messages with attachment and reaction info
    query = """
    SELECT
        message.ROWID,
        message.text,
        message.date,
        message.is_from_me,
        message.handle_id,
        message.associated_message_type,
        chat.chat_identifier,
        chat.display_name
    FROM message
    LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
    LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
    WHERE message.ROWID > ?
    ORDER BY message.date ASC
    """

    cursor.execute(query, (last_rowid,))
    messages = cursor.fetchall()

    if not messages:
        print("No new messages to export.")
        conn.close()
        os.remove(temp_db)
        return

    # Get all attachments
    cursor.execute("""
        SELECT
            message_attachment_join.message_id,
            attachment.mime_type,
            attachment.transfer_name
        FROM attachment
        JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id
    """)

    attachments_by_msg = defaultdict(list)
    for row in cursor.fetchall():
        msg_id, mime_type, transfer_name = row
        if mime_type:
            if mime_type.startswith('image'):
                attachments_by_msg[msg_id].append("photo")
            elif mime_type.startswith('video'):
                attachments_by_msg[msg_id].append("video")
            elif mime_type.startswith('audio'):
                attachments_by_msg[msg_id].append("audio")
            else:
                attachments_by_msg[msg_id].append(f"{transfer_name or 'file'}")

    # Reaction type mapping
    reaction_types = {
        2000: "loved",
        2001: "liked",
        2002: "disliked",
        2003: "laughed at",
        2004: "emphasized",
        2005: "questioned",
        3000: "removed love from",
        3001: "removed like from",
        3002: "removed dislike from",
        3003: "removed laugh from",
        3004: "removed emphasis from",
        3005: "removed question from"
    }

    # Organize messages by conversation and date
    conversations = defaultdict(lambda: defaultdict(list))
    max_rowid = last_rowid

    for row in messages:
        rowid, text, date, is_from_me, handle_id, assoc_msg_type, chat_id, display_name = row

        max_rowid = max(max_rowid, rowid)

        # Get conversation identifier
        if display_name:
            conv_name = display_name
        elif chat_id:
            # First try to look up as a contact (for 1:1 chats)
            conv_name = lookup_contact_name(chat_id)
            # If we got back the same thing (no match), try getting group participants
            if conv_name == chat_id or conv_name.startswith("chat"):
                participants = get_chat_participants(chat_id, cursor)
                if participants:
                    conv_name = participants
                else:
                    conv_name = chat_id
        else:
            conv_name = get_contact_name(handle_id, cursor) if handle_id else "Unknown"

        # Clean up conversation name for filename (Windows-safe)
        invalid_chars = '<>:"/\\|?*'
        conv_name_clean = "".join(c if c not in invalid_chars and (c.isalnum() or c in (' ', '-', '_')) else '_' for c in str(conv_name))
        conv_name_clean = conv_name_clean.strip()

        # Get date
        msg_datetime = convert_apple_time(date)
        if msg_datetime is None:
            continue

        date_str = msg_datetime.strftime("%Y-%m-%d")
        time_str = msg_datetime.strftime("%H:%M")

        # Determine sender - for group chats, get the actual sender's name
        if is_from_me:
            sender = "Me"
        else:
            sender = get_contact_name(handle_id, cursor) if handle_id else conv_name

        # Build message content
        attachments = attachments_by_msg.get(rowid, [])

        # Check for reaction
        if assoc_msg_type and assoc_msg_type in reaction_types:
            content = f"*{reaction_types[assoc_msg_type]} a message*"
        elif text and attachments:
            content = f"{text} [{', '.join(attachments)}]"
        elif text:
            content = text
        elif attachments:
            content = f"[{', '.join(attachments)}]"
        else:
            # Skip empty messages
            continue

        conversations[conv_name_clean][date_str].append({
            "time": time_str,
            "sender": sender,
            "text": content
        })

    # Write to files
    messages_written = 0

    for conv_name, dates in conversations.items():
        conv_dir = os.path.join(OUTPUT_DIR, conv_name)
        os.makedirs(conv_dir, exist_ok=True)

        for date_str, msgs in dates.items():
            filename = os.path.join(conv_dir, f"{date_str}.md")

            # A --full run REWRITES each day file (it holds the complete day,
            # so appending would duplicate the whole history). Incremental
            # runs append only the genuinely new rows.
            mode = 'a' if (not full_export and os.path.exists(filename)) else 'w'

            with open(filename, mode, encoding='utf-8') as f:
                if mode == 'w':
                    f.write(f"# Messages with {conv_name} - {date_str}\n\n")

                for msg in msgs:
                    f.write(f"**{msg['time']} - {msg['sender']}:** {msg['text']}\n\n")
                    messages_written += 1

    # Create a master index file
    create_index(OUTPUT_DIR)

    # Save state
    state["last_message_rowid"] = max_rowid
    state["last_export"] = datetime.now().isoformat()
    save_state(state)

    print(f"Exported {messages_written} messages from {len(conversations)} conversations.")

    conn.close()
    os.remove(temp_db)


def export_ai_ready(messages_db_path, full_export=False):
    """Export messages to AI-ready JSON and CSV formats."""

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load state. NOTE: the AI-ready export keeps its OWN rowid cursor —
    # sharing last_message_rowid with the markdown export meant whichever ran
    # second always saw "no new messages" and messages.json was never written.
    state = load_state()
    last_rowid = 0 if full_export else state.get("last_ai_rowid", 0)

    # Copy database to temp location
    temp_db = os.path.join(os.environ.get("TEMP", "/tmp"), "messages_temp.db")
    shutil.copy2(messages_db_path, temp_db)

    # Connect to database
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Get messages with more metadata including attachment, reaction, and special message info
    query = """
    SELECT
        message.ROWID,
        message.text,
        message.date,
        message.is_from_me,
        message.handle_id,
        message.associated_message_type,
        message.associated_message_guid,
        message.balloon_bundle_id,
        message.expressive_send_style_id,
        chat.chat_identifier,
        chat.display_name,
        chat.ROWID as chat_rowid
    FROM message
    LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
    LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
    WHERE message.ROWID > ?
    ORDER BY message.date ASC
    """

    cursor.execute(query, (last_rowid,))
    messages = cursor.fetchall()

    if not messages:
        print("No new messages to export.")
        conn.close()
        os.remove(temp_db)
        return

    # Get all attachments
    cursor.execute("""
        SELECT
            message_attachment_join.message_id,
            attachment.filename,
            attachment.mime_type,
            attachment.transfer_name
        FROM attachment
        JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id
    """)

    attachments_by_msg = defaultdict(list)
    for row in cursor.fetchall():
        msg_id, filename, mime_type, transfer_name = row
        att_info = {
            "filename": transfer_name or (filename.split('/')[-1] if filename else None),
            "type": mime_type
        }
        # Categorize attachment
        if mime_type:
            if mime_type.startswith('image'):
                att_info["category"] = "photo"
            elif mime_type.startswith('video'):
                att_info["category"] = "video"
            elif mime_type.startswith('audio'):
                att_info["category"] = "audio"
            else:
                att_info["category"] = "file"
        else:
            att_info["category"] = "file"

        attachments_by_msg[msg_id].append(att_info)

    # Reaction type mapping
    reaction_types = {
        2000: "loved",
        2001: "liked",
        2002: "disliked",
        2003: "laughed",
        2004: "emphasized",
        2005: "questioned",
        3000: "removed love",
        3001: "removed like",
        3002: "removed dislike",
        3003: "removed laugh",
        3004: "removed emphasis",
        3005: "removed question"
    }

    # Special message type mapping (balloon_bundle_id)
    special_types = {
        "com.apple.Handwriting.HandwritingProvider": "handwritten message",
        "com.apple.DigitalTouchBalloonProvider": "Digital Touch",
        "com.apple.messages.MSMessageExtensionBalloonPlugin:0000000000:com.apple.icloud.apps.messages.business.extension": "business chat",
        "com.apple.messages.URLBalloonProvider": "link preview",
        "com.apple.Stickers.UserGenerated.MessagesExtension": "sticker",
        "com.apple.messages.MSMessageExtensionBalloonPlugin": "app message",
    }

    # Expressive send styles
    expressive_styles = {
        "com.apple.MobileSMS.expressivesend.gentle": "sent gently",
        "com.apple.MobileSMS.expressivesend.impact": "sent with slam",
        "com.apple.MobileSMS.expressivesend.loud": "sent loud",
        "com.apple.MobileSMS.expressivesend.invisibleink": "sent with invisible ink",
        "com.apple.messages.effect.CKEchoEffect": "sent with echo",
        "com.apple.messages.effect.CKSpotlightEffect": "sent with spotlight",
        "com.apple.messages.effect.CKHappyBirthdayEffect": "sent with balloons",
        "com.apple.messages.effect.CKHeartEffect": "sent with heart",
        "com.apple.messages.effect.CKLasersEffect": "sent with lasers",
        "com.apple.messages.effect.CKFireworksEffect": "sent with fireworks",
        "com.apple.messages.effect.CKShootingStarEffect": "sent with shooting star",
        "com.apple.messages.effect.CKSparklesEffect": "sent with celebration",
        "com.apple.messages.effect.CKConfettiEffect": "sent with confetti",
    }

    # Build structured data
    all_messages = []
    conversations_meta = {}
    skipped_special = 0
    max_rowid = last_rowid

    for row in messages:
        rowid, text, date, is_from_me, handle_id, assoc_msg_type, assoc_msg_guid, balloon_bundle_id, expressive_style, chat_id, display_name, chat_rowid = row

        max_rowid = max(max_rowid, rowid)

        # Get timestamp
        msg_datetime = convert_apple_time(date)
        if msg_datetime is None:
            continue

        # Get conversation name
        if display_name:
            conv_name = display_name
            conv_type = "group"
        elif chat_id:
            conv_name = lookup_contact_name(chat_id)
            if conv_name == chat_id or conv_name.startswith("chat"):
                participants = get_chat_participants(chat_id, cursor)
                if participants:
                    conv_name = participants
                    conv_type = "group"
                else:
                    conv_name = chat_id
                    conv_type = "unknown"
            else:
                conv_type = "direct"
        else:
            conv_name = get_contact_name(handle_id, cursor) if handle_id else "Unknown"
            conv_type = "direct"

        # Get sender name
        if is_from_me:
            sender = "Me"
        else:
            sender = get_contact_name(handle_id, cursor) if handle_id else conv_name

        # Determine message type and content
        msg_type = "text"
        content = text
        attachments = attachments_by_msg.get(rowid, [])
        reaction = None
        special_content = None
        effect = None

        # Check for expressive send style
        if expressive_style and expressive_style in expressive_styles:
            effect = expressive_styles[expressive_style]

        # Check for reaction
        if assoc_msg_type and assoc_msg_type in reaction_types:
            msg_type = "reaction"
            reaction = reaction_types[assoc_msg_type]
            content = f"{reaction}" if not text else text
        # Check for attachment
        elif attachments:
            if text:
                msg_type = "text_with_attachment"
            else:
                msg_type = "attachment"
                # Describe the attachment
                att_descriptions = []
                for att in attachments:
                    att_descriptions.append(f"[{att['category']}]")
                content = " ".join(att_descriptions)
        # Check for special message types
        elif not text and balloon_bundle_id:
            msg_type = "special"
            # Try to identify the specific type
            for bundle_key, bundle_name in special_types.items():
                if bundle_key in balloon_bundle_id:
                    special_content = bundle_name
                    break
            if not special_content:
                if "gamepigeon" in balloon_bundle_id.lower():
                    special_content = "GamePigeon game"
                elif "pay" in balloon_bundle_id.lower() or "wallet" in balloon_bundle_id.lower():
                    special_content = "Apple Pay"
                elif "fitness" in balloon_bundle_id.lower():
                    special_content = "Fitness sharing"
                elif "music" in balloon_bundle_id.lower():
                    special_content = "Apple Music"
                elif "photo" in balloon_bundle_id.lower():
                    special_content = "shared photo"
                else:
                    special_content = f"app content ({balloon_bundle_id.split('.')[-1] if '.' in balloon_bundle_id else 'unknown'})"
            content = f"[{special_content}]"
        elif not text:
            # No text, no attachment, no balloon - likely system message or empty
            msg_type = "special"
            content = "[unknown message type]"
            skipped_special += 1

        # Build message record
        msg_record = {
            "timestamp": msg_datetime.isoformat(),
            "date": msg_datetime.strftime("%Y-%m-%d"),
            "time": msg_datetime.strftime("%H:%M:%S"),
            "year": msg_datetime.year,
            "month": msg_datetime.month,
            "day": msg_datetime.day,
            "hour": msg_datetime.hour,
            "day_of_week": msg_datetime.strftime("%A"),
            "conversation": conv_name,
            "conversation_type": conv_type,
            "sender": sender,
            "is_from_me": bool(is_from_me),
            "message_type": msg_type,
            "text": content,
            "has_attachment": len(attachments) > 0,
            "attachment_types": [a["category"] for a in attachments] if attachments else [],
            "reaction": reaction,
            "special_content": special_content,
            "effect": effect,
            "char_count": len(content) if content else 0,
            "word_count": len(content.split()) if content else 0
        }

        all_messages.append(msg_record)

        # Track conversation metadata
        if conv_name not in conversations_meta:
            conversations_meta[conv_name] = {
                "name": conv_name,
                "type": conv_type,
                "message_count": 0,
                "first_message": msg_datetime.isoformat(),
                "last_message": msg_datetime.isoformat()
            }
        conversations_meta[conv_name]["message_count"] += 1
        conversations_meta[conv_name]["last_message"] = msg_datetime.isoformat()

    conn.close()
    os.remove(temp_db)

    # Incremental runs MERGE with the existing messages.json — writing only
    # the new delta would silently destroy all prior history in the file.
    if not full_export:
        existing_json = os.path.join(OUTPUT_DIR, "messages.json")
        if os.path.exists(existing_json):
            try:
                with open(existing_json, encoding='utf-8') as f:
                    prior = json.load(f).get("messages", [])
            except Exception:
                prior = []
                print("  Note: existing messages.json was unreadable — "
                      "run --full once to rebuild the complete file.")
            if prior:
                all_messages = prior + all_messages
                conversations_meta = {}
                for m in all_messages:
                    name = m.get("conversation") or "Unknown"
                    meta = conversations_meta.setdefault(name, {
                        "name": name,
                        "type": m.get("conversation_type", "direct"),
                        "message_count": 0,
                        "first_message": m.get("timestamp"),
                        "last_message": m.get("timestamp"),
                    })
                    meta["message_count"] += 1
                    meta["last_message"] = m.get("timestamp")

    # Calculate message type counts for terminal output
    text_count = sum(1 for m in all_messages if m["message_type"] == "text")
    attachment_count = sum(1 for m in all_messages if m["message_type"] == "attachment")
    text_att_count = sum(1 for m in all_messages if m["message_type"] == "text_with_attachment")
    reaction_count = sum(1 for m in all_messages if m["message_type"] == "reaction")
    special_count = sum(1 for m in all_messages if m["message_type"] == "special")

    print(f"\nMessage breakdown:")
    print(f"  - Text messages:      {text_count:,}")
    print(f"  - Attachments only:   {attachment_count:,}")
    print(f"  - Text + attachment:  {text_att_count:,}")
    print(f"  - Reactions:          {reaction_count:,}")
    print(f"  - Special/app:        {special_count:,}")
    print(f"  - Total:              {len(all_messages):,}")

    # Write JSON
    json_path = os.path.join(OUTPUT_DIR, "messages.json")
    export_data = {
        "export_date": datetime.now().isoformat(),
        "total_messages": len(all_messages),
        "total_conversations": len(conversations_meta),
        "conversations": list(conversations_meta.values()),
        "messages": all_messages
    }

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2)

    print(f"\nCreated {json_path}")

    # Write CSV
    csv_path = os.path.join(OUTPUT_DIR, "messages.csv")

    if all_messages:
        fieldnames = ["timestamp", "date", "time", "year", "month", "day", "hour",
                      "day_of_week", "conversation", "conversation_type", "sender",
                      "is_from_me", "message_type", "text", "has_attachment",
                      "attachment_types", "reaction", "special_content", "effect",
                      "char_count", "word_count"]

        import csv
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            # Convert attachment_types list to string for CSV
            for msg in all_messages:
                msg_copy = msg.copy()
                msg_copy["attachment_types"] = ",".join(msg_copy["attachment_types"]) if msg_copy["attachment_types"] else ""
                writer.writerow(msg_copy)

        print(f"Created {csv_path}")

    # Write a summary file for quick context
    summary_path = os.path.join(OUTPUT_DIR, "SUMMARY.md")

    # Calculate stats
    text_msgs = sum(1 for m in all_messages if m["message_type"] == "text")
    attachment_msgs = sum(1 for m in all_messages if m["message_type"] == "attachment")
    text_with_att = sum(1 for m in all_messages if m["message_type"] == "text_with_attachment")
    reactions = sum(1 for m in all_messages if m["message_type"] == "reaction")
    special_msgs = sum(1 for m in all_messages if m["message_type"] == "special")

    photos = sum(1 for m in all_messages if "photo" in m.get("attachment_types", []))
    videos = sum(1 for m in all_messages if "video" in m.get("attachment_types", []))
    audio = sum(1 for m in all_messages if "audio" in m.get("attachment_types", []))

    # Count special content types
    special_content_counts = defaultdict(int)
    for m in all_messages:
        if m.get("special_content"):
            special_content_counts[m["special_content"]] += 1

    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write("# iMessage Export Summary\n\n")
        f.write(f"**Export Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"**Total Messages:** {len(all_messages):,}\n")
        f.write(f"**Total Conversations:** {len(conversations_meta)}\n\n")

        # Date range
        if all_messages:
            first_date = all_messages[0]["date"]
            last_date = all_messages[-1]["date"]
            f.write(f"**Date Range:** {first_date} to {last_date}\n\n")

        # Message type breakdown
        f.write("## Message Types\n\n")
        f.write(f"- **Text messages:** {text_msgs:,}\n")
        f.write(f"- **Attachments only:** {attachment_msgs:,}\n")
        f.write(f"- **Text with attachments:** {text_with_att:,}\n")
        f.write(f"- **Reactions:** {reactions:,}\n")
        f.write(f"- **Special/app content:** {special_msgs:,}\n\n")

        # Attachment breakdown
        f.write("## Attachments\n\n")
        f.write(f"- **Photos:** {photos:,}\n")
        f.write(f"- **Videos:** {videos:,}\n")
        f.write(f"- **Audio messages:** {audio:,}\n\n")

        # Special content breakdown
        if special_content_counts:
            f.write("## Special Content (Apps, Games, etc.)\n\n")
            for content_type, count in sorted(special_content_counts.items(), key=lambda x: -x[1])[:15]:
                f.write(f"- **{content_type}:** {count:,}\n")
            f.write("\n")

        # Top conversations
        f.write("## Top 20 Conversations (by message count)\n\n")
        sorted_convos = sorted(conversations_meta.values(), key=lambda x: x["message_count"], reverse=True)[:20]
        for conv in sorted_convos:
            f.write(f"- **{conv['name']}**: {conv['message_count']:,} messages ({conv['type']})\n")

        f.write("\n## Files\n\n")
        f.write("- `messages.json` - Full structured data for AI analysis\n")
        f.write("- `messages.csv` - Tabular format for spreadsheets or analysis\n")
        f.write("- `SUMMARY.md` - This file\n")
        f.write("- Individual folders - Markdown files organized by contact and date\n")

    print(f"Created {summary_path}")

    # Save this export's own cursor (separate from the markdown export's).
    state["last_ai_rowid"] = max_rowid
    save_state(state)


def create_index(output_dir):
    """Create an index file listing all conversations and recent activity."""
    index_path = os.path.join(output_dir, "INDEX.md")

    with open(index_path, 'w', encoding='utf-8') as f:
        f.write("# iMessage Export Index\n\n")
        f.write(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("## Conversations\n\n")

        for item in sorted(os.listdir(output_dir)):
            item_path = os.path.join(output_dir, item)
            if os.path.isdir(item_path) and not item.startswith('.'):
                # Count messages and get date range
                md_files = [f for f in os.listdir(item_path) if f.endswith('.md')]
                if md_files:
                    dates = sorted([f.replace('.md', '') for f in md_files])
                    f.write(f"- **{item}**: {len(md_files)} days of messages ({dates[0]} to {dates[-1]})\n")


def main():
    import sys

    print("=" * 60)
    print("Desmond - iMessage Exporter for Windows")
    print("=" * 60)
    print()

    full_export = "--full" in sys.argv
    custom_backup = None

    # Check for custom backup path
    for i, arg in enumerate(sys.argv):
        if arg == "--backup" and i + 1 < len(sys.argv):
            custom_backup = sys.argv[i + 1]

    # Find backup directory
    if custom_backup:
        backup_dir = custom_backup
        if not os.path.isdir(backup_dir):
            print(f"Error: Specified backup directory does not exist: {backup_dir}")
            sys.exit(1)
    else:
        backup_dir = find_backup_directory()

    if not backup_dir:
        print("\nNo iPhone backup found!")
        print("\nTo use this tool, you need an unencrypted iPhone backup:")
        print("1. Connect your iPhone to your Windows PC")
        print("2. Open iTunes (Windows 10) or Apple Devices (Windows 11)")
        print("3. Select your device and click 'Back Up Now'")
        print("4. IMPORTANT: Make sure 'Encrypt local backup' is NOT checked")
        print("\nBackup locations checked:")
        for loc in BACKUP_LOCATIONS:
            print(f"  - {loc}")
        print("\nYou can also specify a custom backup path:")
        print("  python imessage_exporter_windows.py --backup \"C:\\path\\to\\backup\"")
        sys.exit(1)

    print(f"Using backup: {backup_dir}")

    # Check for messages database (handles both flat and iOS 10+ sharded layouts)
    messages_db = backup_file_path(backup_dir, MESSAGES_DB_HASH)
    if not messages_db:
        print(f"\nError: Messages database not found in backup.")
        print("This usually means the backup is encrypted.")
        print("\nTo fix this:")
        print("1. Open iTunes/Apple Devices")
        print("2. Select your iPhone")
        print("3. Uncheck 'Encrypt local backup'")
        print("4. Create a new backup")
        sys.exit(1)

    # Load contacts
    load_contacts(backup_dir)

    if full_export:
        print("\nRunning full export of all messages...")
    else:
        print("\nExporting new messages since last run...")

    try:
        # Export markdown files (for human browsing)
        export_messages(messages_db, full_export=full_export)

        # Export AI-ready JSON and CSV
        print("\nCreating AI-ready exports...")
        export_ai_ready(messages_db, full_export=full_export)

        print(f"\nExport complete! Files saved to:")
        print(f"  {OUTPUT_DIR}")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)   # let Task Scheduler runs be observed failing


if __name__ == "__main__":
    main()
