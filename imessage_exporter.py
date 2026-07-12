#!/usr/bin/env python3
"""
iMessage Exporter for Claude
Exports your iMessages to readable markdown + JSON/CSV. Saves locally and also
copies the export to Google Drive (if installed) so messages live in both places.
"""

import sqlite3
import os
import json
import re
import glob
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Configuration
MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")
OUTPUT_DIR = os.path.expanduser("~/Downloads/iMessages_Export")
STATE_FILE = os.path.expanduser("~/Downloads/iMessages_Export/.export_state.json")

# Global contact lookup cache
CONTACTS_CACHE = {}

# Also copy the export here (inside Google Drive) so messages live local + Drive.
DRIVE_SUBFOLDER = "Desmond_Messages_Export"


def find_google_drive_dir():
    """Best-effort detection of a 'Google Drive for desktop' folder on macOS.
    (Mirrors the helper in imessage_attachments.py; duplicated here to avoid a
    circular import.)"""
    candidates = []
    candidates += glob.glob(os.path.expanduser("~/Library/CloudStorage/GoogleDrive-*/My Drive"))
    candidates += glob.glob(os.path.expanduser("~/Library/CloudStorage/GoogleDrive-*"))
    candidates.append(os.path.expanduser("~/Google Drive/My Drive"))
    candidates.append(os.path.expanduser("~/Google Drive"))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def mirror_to_drive(src_dir=OUTPUT_DIR, drive_dir=None):
    """Copy the local export into Google Drive so the messages live in BOTH
    places. Returns the Drive destination path, or None if there's no Drive."""
    if not os.path.isdir(src_dir):
        return None
    drive = drive_dir or find_google_drive_dir()
    if not drive:
        print("\nNo Google Drive folder detected — your messages are saved locally:")
        print(f"  {src_dir}")
        print("Install 'Google Drive for desktop' (or pass --drive PATH) to also "
              "keep a copy on Drive.")
        return None
    dest = os.path.join(drive, DRIVE_SUBFOLDER)
    try:
        shutil.copytree(src_dir, dest, dirs_exist_ok=True)
        print("\nYour messages now live in BOTH places:")
        print(f"  Local:        {src_dir}")
        print(f"  Google Drive: {dest}")
        return dest
    except Exception as e:
        print(f"\nSaved locally at {src_dir}, but couldn't copy to Google Drive: {e}")
        return None

def load_contacts():
    """Load contacts from the Mac AddressBook database."""
    global CONTACTS_CACHE
    
    print("Loading contacts...")
    
    # Find all possible AddressBook database locations
    ab_sources = os.path.expanduser("~/Library/Application Support/AddressBook/Sources/")
    ab_root = os.path.expanduser("~/Library/Application Support/AddressBook/")
    
    db_files = []
    
    # Check Sources subdirectories
    if os.path.exists(ab_sources):
        db_files.extend(glob.glob(os.path.join(ab_sources, "*", "AddressBook-v22.abcddb")))
    
    # Check root AddressBook folder
    if os.path.exists(ab_root):
        root_db = os.path.join(ab_root, "AddressBook-v22.abcddb")
        if os.path.exists(root_db):
            db_files.append(root_db)
    
    if not db_files:
        print("Note: Could not find Contacts database. Using phone numbers/emails instead.")
        return
    
    for db_file in db_files:
        temp_db = None
        try:
            # Copy database to a private temp location to avoid lock issues
            # (a per-run random name — a fixed /tmp path collides between
            # users/runs on a shared Mac).
            fd, temp_db = tempfile.mkstemp(suffix=".abcddb")
            os.close(fd)
            subprocess.run(['cp', db_file, temp_db], check=True)

            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            
            # Get phone numbers with contact names
            try:
                cursor.execute("""
                    SELECT 
                        ZABCDRECORD.ZFIRSTNAME,
                        ZABCDRECORD.ZLASTNAME,
                        ZABCDPHONENUMBER.ZFULLNUMBER
                    FROM ZABCDRECORD
                    LEFT JOIN ZABCDPHONENUMBER ON ZABCDRECORD.Z_PK = ZABCDPHONENUMBER.ZOWNER
                    WHERE ZABCDPHONENUMBER.ZFULLNUMBER IS NOT NULL
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
                        ZABCDRECORD.ZFIRSTNAME,
                        ZABCDRECORD.ZLASTNAME,
                        ZABCDEMAILADDRESS.ZADDRESS
                    FROM ZABCDRECORD
                    LEFT JOIN ZABCDEMAILADDRESS ON ZABCDRECORD.Z_PK = ZABCDEMAILADDRESS.ZOWNER
                    WHERE ZABCDEMAILADDRESS.ZADDRESS IS NOT NULL
                """)
                
                for row in cursor.fetchall():
                    first_name, last_name, email = row
                    name_parts = [p for p in [first_name, last_name] if p]
                    if name_parts and email:
                        CONTACTS_CACHE[email.lower()] = " ".join(name_parts)
            except Exception as e:
                print(f"  Email lookup error: {e}")
            
            conn.close()

        except Exception as e:
            print(f"  Error reading {db_file}: {e}")
        finally:
            if temp_db:
                try:
                    os.remove(temp_db)
                except OSError:
                    pass
    
    print(f"Loaded {len(CONTACTS_CACHE)} contact mappings.")

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

def convert_apple_time(apple_timestamp):
    """Convert Apple's timestamp format to readable datetime.

    Modern macOS stores message.date as NANOSECONDS since 2001-01-01; databases
    migrated from pre-High Sierra used SECONDS. Detect by magnitude, and never
    let one corrupt row abort an entire export."""
    if apple_timestamp is None:
        return None
    try:
        ts = float(apple_timestamp)
        if abs(ts) > 1e12:          # nanoseconds (seconds would be ~1e9)
            ts /= 1_000_000_000
        return datetime.fromtimestamp(ts + 978307200)
    except (ValueError, OverflowError, OSError):
        return None


def decode_attributed_body(data):
    """Recover message text Apple stashes in the binary `attributedBody` field
    when `message.text` is NULL — common on current macOS. Without this, many
    (often most) recent messages export as empty/unknown."""
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


def open_messages_db(db_path=None):
    """Open chat.db strictly READ-ONLY. A plain connect() would create an empty
    file at a wrong path and could write to the real database — this cannot."""
    return sqlite3.connect(f"file:{db_path or MESSAGES_DB}?mode=ro", uri=True)

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

def export_messages(full_export=False):
    """Export messages to markdown files."""
    
    # Load contacts for name lookup
    load_contacts()
    
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Load state
    state = load_state()
    last_rowid = 0 if full_export else state.get("last_message_rowid", 0)

    # Connect to database (read-only — never touches Messages itself)
    conn = open_messages_db()
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
        message.attributedBody,
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
                attachments_by_msg[msg_id].append("📷 photo")
            elif mime_type.startswith('video'):
                attachments_by_msg[msg_id].append("🎬 video")
            elif mime_type.startswith('audio'):
                attachments_by_msg[msg_id].append("🎵 audio")
            else:
                attachments_by_msg[msg_id].append(f"📎 {transfer_name or 'file'}")
    
    # Reaction type mapping
    reaction_types = {
        2000: "❤️ loved",
        2001: "👍 liked", 
        2002: "👎 disliked",
        2003: "😂 laughed at",
        2004: "‼️ emphasized",
        2005: "❓ questioned",
        3000: "removed ❤️ from",
        3001: "removed 👍 from",
        3002: "removed 👎 from",
        3003: "removed 😂 from",
        3004: "removed ‼️ from",
        3005: "removed ❓ from"
    }
    
    # Organize messages by conversation and date
    conversations = defaultdict(lambda: defaultdict(list))
    max_rowid = last_rowid
    
    for row in messages:
        rowid, text, date, is_from_me, handle_id, assoc_msg_type, attributed, chat_id, display_name = row

        max_rowid = max(max_rowid, rowid)

        # Modern macOS often leaves message.text NULL and stores the real text
        # in the binary attributedBody field.
        if not text:
            text = decode_attributed_body(attributed)
        
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
        
        # Clean up conversation name for filename
        conv_name_clean = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in str(conv_name))
        
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

            with open(filename, mode) as f:
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

def export_ai_ready(full_export=False):
    """Export messages to AI-ready JSON and CSV formats."""
    
    # Load contacts for name lookup
    load_contacts()
    
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Load state. NOTE: the AI-ready export keeps its OWN rowid cursor —
    # sharing last_message_rowid with the markdown export meant whichever ran
    # second always saw "no new messages" and messages.json was never written.
    state = load_state()
    last_rowid = 0 if full_export else state.get("last_ai_rowid", 0)

    # Connect to database (read-only — never touches Messages itself)
    conn = open_messages_db()
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
        message.attributedBody,
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
        rowid, text, date, is_from_me, handle_id, assoc_msg_type, assoc_msg_guid, balloon_bundle_id, expressive_style, attributed, chat_id, display_name, chat_rowid = row

        max_rowid = max(max_rowid, rowid)

        # Modern macOS often leaves message.text NULL and stores the real text
        # in the binary attributedBody field.
        if not text:
            text = decode_attributed_body(attributed)

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
    print(f"  • Text messages:      {text_count:,}")
    print(f"  • Attachments only:   {attachment_count:,}")
    print(f"  • Text + attachment:  {text_att_count:,}")
    print(f"  • Reactions:          {reaction_count:,}")
    print(f"  • Special/app:        {special_count:,}")
    print(f"  • Total:              {len(all_messages):,}")
    
    # Write JSON
    json_path = os.path.join(OUTPUT_DIR, "messages.json")
    export_data = {
        "export_date": datetime.now().isoformat(),
        "total_messages": len(all_messages),
        "total_conversations": len(conversations_meta),
        "conversations": list(conversations_meta.values()),
        "messages": all_messages
    }
    
    with open(json_path, 'w') as f:
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
    
    with open(summary_path, 'w') as f:
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
        f.write("- `messages.json` — Full structured data for AI analysis\n")
        f.write("- `messages.csv` — Tabular format for spreadsheets or analysis\n")
        f.write("- `SUMMARY.md` — This file\n")
        f.write("- Individual folders — Markdown files organized by contact and date\n")
    
    print(f"Created {summary_path}")

    # Save this export's own cursor (separate from the markdown export's).
    state["last_ai_rowid"] = max_rowid
    save_state(state)

def create_index(output_dir):
    """Create an index file listing all conversations and recent activity."""
    index_path = os.path.join(output_dir, "INDEX.md")
    
    with open(index_path, 'w') as f:
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

    full_export = "--full" in sys.argv

    if not os.path.exists(MESSAGES_DB):
        print(f"Could not find your Messages database at {MESSAGES_DB}")
        print("This tool runs on a Mac with the Messages app set up.")
        print("If Terminal lacks access: System Settings → Privacy & Security → "
              "Full Disk Access → enable Terminal, then restart it.")
        sys.exit(1)

    if full_export:
        print("Running full export of all messages...")
    else:
        print("Exporting new messages since last run...")

    try:
        # Export markdown files (for human browsing)
        export_messages(full_export=full_export)
        
        # Export AI-ready JSON and CSV
        print("\nCreating AI-ready exports...")
        export_ai_ready(full_export=full_export)

        # Also copy everything to Google Drive (messages live local + Drive).
        if "--no-drive" not in sys.argv:
            drive_override = None
            if "--drive" in sys.argv:
                idx = sys.argv.index("--drive")
                if idx + 1 < len(sys.argv):
                    drive_override = os.path.expanduser(sys.argv[idx + 1])
            mirror_to_drive(OUTPUT_DIR, drive_override)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("\nMake sure Terminal has Full Disk Access:")
        print("System Settings > Privacy & Security > Full Disk Access > Enable Terminal")
        sys.exit(1)   # let scheduled runs be observed failing

if __name__ == "__main__":
    main()
