#!/usr/bin/env python3
"""
Android SMS Exporter
Exports your SMS/MMS messages from Android backup files to AI-ready formats.

This script works with XML backup files created by "SMS Backup & Restore" app:
https://play.google.com/store/apps/details?id=com.riteshsahu.SMSBackupRestore

Works on both Windows and macOS.
"""

import xml.etree.ElementTree as ET
import os
import json
import re
import sys
import base64
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Configuration
OUTPUT_DIR = None  # Set dynamically based on platform
STATE_FILE = None

# Detect platform and set output directory
if sys.platform == "win32":
    OUTPUT_DIR = os.path.expanduser("~/Documents/Android_SMS_Export")
else:
    OUTPUT_DIR = os.path.expanduser("~/Downloads/Android_SMS_Export")

STATE_FILE = os.path.join(OUTPUT_DIR, ".export_state.json")

# Message type mapping (from Android SMS database)
MESSAGE_TYPES = {
    1: "received",
    2: "sent",
    3: "draft",
    4: "outbox",
    5: "failed",
    6: "queued"
}

# MMS box types
MMS_BOX_TYPES = {
    1: "received",
    2: "sent",
    3: "draft",
    4: "outbox"
}

# Call log types
CALL_TYPES = {
    1: "incoming",
    2: "outgoing",
    3: "missed",
    4: "voicemail",
    5: "rejected",
    6: "refused"
}


def find_backup_files(search_paths=None):
    """Find SMS Backup & Restore XML files."""
    print("Searching for Android SMS backup files...")

    if search_paths is None:
        # Common locations for backup files
        search_paths = [
            os.path.expanduser("~/Downloads"),
            os.path.expanduser("~/Documents"),
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~"),
        ]

        # Windows-specific paths
        if sys.platform == "win32":
            search_paths.extend([
                os.path.expandvars(r"%USERPROFILE%\Downloads"),
                os.path.expandvars(r"%USERPROFILE%\Documents"),
            ])

    backup_files = []

    for search_path in search_paths:
        if not os.path.exists(search_path):
            continue

        print(f"  Checking: {search_path}")

        # Look for XML files that match SMS Backup & Restore naming patterns
        for root, dirs, files in os.walk(search_path):
            # Don't recurse too deep
            depth = root.replace(search_path, '').count(os.sep)
            if depth > 2:
                continue

            for filename in files:
                if filename.endswith('.xml'):
                    filepath = os.path.join(root, filename)

                    # Check if it looks like an SMS backup
                    kind = classify_backup(filepath)
                    if kind:
                        stat = os.stat(filepath)
                        backup_files.append({
                            "path": filepath,
                            "filename": filename,
                            "kind": kind,
                            "size": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime)
                        })
                        print(f"    Found: {filename} ({kind})")

    # Sort by modification time, most recent first
    backup_files.sort(key=lambda x: x["modified"], reverse=True)

    return backup_files


def classify_backup(filepath):
    """Identify an SMS Backup & Restore XML file.

    The app writes MESSAGES and CALL LOGS to SEPARATE files (sms-*.xml and
    calls-*.xml) — returns "messages", "calls", or None so the caller can use
    both instead of blindly grabbing whichever was modified last."""
    try:
        # Read just the beginning of the file to check
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            header = f.read(1000)

        if '<smses' in header or '<sms protocol=' in header:
            return "messages"
        if '<mms ' in header and 'msg_box=' in header:
            return "messages"
        if '<calls' in header and '<call number=' in header:
            return "calls"

    except Exception:
        pass

    return None


def is_sms_backup(filepath):
    """Back-compat wrapper: True for any SMS Backup & Restore file."""
    return classify_backup(filepath) is not None


def clean_contact_name(contact_name):
    """SMS Backup & Restore emits literal placeholder strings for missing
    names — treat them as no name so distinct unknown numbers don't all merge
    into one '(Unknown)' conversation."""
    if not contact_name:
        return None
    if contact_name.strip().lower() in ("(unknown)", "unknown", "null"):
        return None
    return contact_name


def parse_sms_backup(filepath):
    """Parse an SMS Backup & Restore XML file.

    Streams with iterparse instead of loading the whole file as a DOM —
    backups with inline MMS media are routinely hundreds of MB to multiple
    GB, which a full parse would turn into 5-10× that much RAM. Elements are
    processed and freed as they complete, and everything parsed before a
    corrupt byte is still returned instead of throwing the run away."""
    print(f"\nParsing: {filepath}")
    return _parse_stream(filepath)


def parse_backup_bytes(data):
    """Parse SMS Backup & Restore XML that arrived in memory — a browser
    upload, a file read straight off the phone's mounted storage, an API
    payload. Same streaming parser, no filesystem. Returns
    (messages, call_logs)."""
    import io
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _parse_stream(io.BytesIO(data), quiet=True)


def _parse_stream(source, quiet=False):
    messages = []
    call_logs = []

    try:
        for _event, elem in ET.iterparse(source, events=("end",)):
            tag = elem.tag
            if tag == "sms":
                msg = parse_sms_element(elem)
                if msg:
                    messages.append(msg)
            elif tag == "mms":
                msg = parse_mms_element(elem)
                if msg:
                    messages.append(msg)
            elif tag == "call":
                log = parse_call_element(elem)
                if log:
                    call_logs.append(log)
            else:
                continue
            elem.clear()   # free the element (and its MMS media) immediately

        if not quiet:
            print(f"  Parsed {len(messages)} messages and {len(call_logs)} call logs")

    except ET.ParseError as e:
        if not quiet:
            print(f"  Warning: the file is damaged after "
                  f"{len(messages)} messages / {len(call_logs)} calls — "
                  f"exporting what was readable. ({e})")
    except Exception as e:
        if not quiet:
            print(f"  Error: {e}")
        return messages, call_logs

    return messages, call_logs


def parse_sms_element(sms):
    """Parse a single SMS element."""
    try:
        # Get attributes
        address = sms.get('address', 'Unknown')
        body = sms.get('body', '')
        date_ms = sms.get('date')
        msg_type = int(sms.get('type', 1))
        contact_name = clean_contact_name(sms.get('contact_name'))
        readable_date = sms.get('readable_date')

        # Convert timestamp
        if date_ms:
            # Android uses milliseconds since epoch
            timestamp = datetime.fromtimestamp(int(date_ms) / 1000)
        else:
            timestamp = None

        # Determine sender. Everything except type 1 (received) is outgoing:
        # sent, draft, outbox, failed, queued are all authored by me.
        if msg_type != 1:
            is_from_me = True
            sender = "Me"
        else:
            is_from_me = False
            sender = contact_name if contact_name else format_phone(address)

        # Conversation name (use contact name or phone number)
        conversation = contact_name if contact_name else format_phone(address)

        return {
            "source": "sms",
            "timestamp": timestamp,
            "conversation": conversation,
            "address": address,
            "sender": sender,
            "is_from_me": is_from_me,
            "text": body,
            "message_type": "text",
            "has_attachment": False,
            "attachment_types": [],
            "status": MESSAGE_TYPES.get(msg_type, "unknown")
        }

    except Exception as e:
        print(f"    Warning: Could not parse SMS: {e}")
        return None


def parse_mms_element(mms):
    """Parse a single MMS element."""
    try:
        # Get attributes
        date_ms = mms.get('date')
        msg_box = int(mms.get('msg_box', 1))
        contact_name = clean_contact_name(mms.get('contact_name'))
        address = mms.get('address', 'Unknown')

        # Convert timestamp (MMS uses seconds, not milliseconds)
        if date_ms:
            # MMS timestamps are in seconds
            ts_value = int(date_ms)
            # Check if it's milliseconds (very large number) or seconds
            if ts_value > 10000000000:
                timestamp = datetime.fromtimestamp(ts_value / 1000)
            else:
                timestamp = datetime.fromtimestamp(ts_value)
        else:
            timestamp = None

        # Determine sender. Boxes 2-4 (sent, draft, outbox) are all mine.
        if msg_box != 1:
            is_from_me = True
            sender = "Me"
        else:
            is_from_me = False
            # Try to get the From address
            from_addr = None
            for addr_elem in mms.findall('.//addr'):
                if addr_elem.get('type') == '137':  # From
                    from_addr = addr_elem.get('address')
                    break
            sender = contact_name if contact_name else (format_phone(from_addr) if from_addr else format_phone(address))

        # Conversation name
        conversation = contact_name if contact_name else format_phone(address)

        # Get message content from parts
        text_content = []
        attachments = []

        for part in mms.findall('.//part'):
            ct = part.get('ct', '')  # Content type
            text = part.get('text', '')

            if 'text/plain' in ct and text:
                text_content.append(text)
            elif 'image/' in ct:
                attachments.append('photo')
            elif 'video/' in ct:
                attachments.append('video')
            elif 'audio/' in ct:
                attachments.append('audio')
            elif ct and 'text/' not in ct and 'application/smil' not in ct:
                attachments.append('file')

        # Combine text content
        body = ' '.join(text_content)

        # Determine message type
        if body and attachments:
            msg_type = "text_with_attachment"
        elif attachments:
            msg_type = "attachment"
            body = f"[{', '.join(attachments)}]"
        else:
            msg_type = "text"

        return {
            "source": "mms",
            "timestamp": timestamp,
            "conversation": conversation,
            "address": address,
            "sender": sender,
            "is_from_me": is_from_me,
            "text": body if body else "[MMS]",
            "message_type": msg_type,
            "has_attachment": len(attachments) > 0,
            "attachment_types": list(set(attachments)),
            "status": MMS_BOX_TYPES.get(msg_box, "unknown")
        }

    except Exception as e:
        print(f"    Warning: Could not parse MMS: {e}")
        return None


def parse_call_element(call):
    """Parse a single call log element."""
    try:
        number = call.get('number', 'Unknown')
        date_ms = call.get('date')
        duration = int(call.get('duration', 0))
        call_type = int(call.get('type', 1))
        contact_name = clean_contact_name(call.get('contact_name'))

        if date_ms:
            timestamp = datetime.fromtimestamp(int(date_ms) / 1000)
        else:
            timestamp = None

        return {
            "timestamp": timestamp,
            "number": number,
            "contact": contact_name if contact_name else format_phone(number),
            "duration_seconds": duration,
            "type": CALL_TYPES.get(call_type, "unknown")
        }

    except Exception:
        return None


def format_phone(number):
    """Format a phone number for display."""
    if not number:
        return "Unknown"

    # Remove non-digit characters for normalization
    digits = re.sub(r'\D', '', number)

    # Format US numbers nicely
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"

    return number


def load_state():
    """Load the last export state."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"last_export": None, "processed_files": []}


def save_state(state):
    """Save the export state."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def export_messages(messages, full_export=False):
    """Export messages to markdown files organized by conversation and date."""

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Sort messages by timestamp
    messages = [m for m in messages if m.get('timestamp')]
    messages.sort(key=lambda x: x['timestamp'])

    # Organize by conversation and date
    conversations = defaultdict(lambda: defaultdict(list))

    for msg in messages:
        # Clean conversation name for filename
        conv_name = msg['conversation']
        invalid_chars = '<>:"/\\|?*' if sys.platform == "win32" else '/'
        conv_name_clean = "".join(c if c not in invalid_chars and (c.isalnum() or c in (' ', '-', '_', '(', ')')) else '_' for c in str(conv_name))
        conv_name_clean = conv_name_clean.strip()[:50]  # Limit length

        date_str = msg['timestamp'].strftime("%Y-%m-%d")
        time_str = msg['timestamp'].strftime("%H:%M")

        conversations[conv_name_clean][date_str].append({
            "time": time_str,
            "sender": msg['sender'],
            "text": msg['text']
        })

    # Write files
    messages_written = 0

    for conv_name, dates in conversations.items():
        conv_dir = os.path.join(OUTPUT_DIR, conv_name)
        os.makedirs(conv_dir, exist_ok=True)

        for date_str, msgs in dates.items():
            filename = os.path.join(conv_dir, f"{date_str}.md")

            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"# Messages with {conv_name} - {date_str}\n\n")

                for msg in msgs:
                    f.write(f"**{msg['time']} - {msg['sender']}:** {msg['text']}\n\n")
                    messages_written += 1

    print(f"Exported {messages_written} messages to {len(conversations)} conversation folders")

    return conversations


def build_export_data(messages):
    """Turn parsed Android messages into a standard Desmond export dict —
    the same shape messages.json carries — WITHOUT touching the filesystem.
    This is what federation/web flows consume; export_ai_ready() wraps it
    to write the files."""

    # Sort messages by timestamp
    messages = [m for m in messages if m.get('timestamp')]
    messages.sort(key=lambda x: x['timestamp'])

    # Build structured records
    all_messages = []
    conversations_meta = {}

    for msg in messages:
        timestamp = msg['timestamp']
        conv_name = msg['conversation']

        record = {
            "timestamp": timestamp.isoformat(),
            "date": timestamp.strftime("%Y-%m-%d"),
            "time": timestamp.strftime("%H:%M:%S"),
            "year": timestamp.year,
            "month": timestamp.month,
            "day": timestamp.day,
            "hour": timestamp.hour,
            "day_of_week": timestamp.strftime("%A"),
            "conversation": conv_name,
            "conversation_type": "direct",  # Android doesn't distinguish easily
            "address": msg.get('address', ''),
            "sender": msg['sender'],
            "is_from_me": msg['is_from_me'],
            "message_type": msg['message_type'],
            "text": msg['text'],
            "has_attachment": msg['has_attachment'],
            "attachment_types": msg['attachment_types'],
            "reaction": None,  # Android SMS doesn't have reactions
            "special_content": None,
            "effect": None,
            "char_count": len(msg['text']) if msg['text'] else 0,
            "word_count": len(msg['text'].split()) if msg['text'] else 0,
            "source": msg.get('source', 'sms')
        }

        all_messages.append(record)

        # Track conversation metadata
        if conv_name not in conversations_meta:
            conversations_meta[conv_name] = {
                "name": conv_name,
                "type": "direct",
                "message_count": 0,
                "first_message": timestamp.isoformat(),
                "last_message": timestamp.isoformat()
            }
        conversations_meta[conv_name]["message_count"] += 1
        conversations_meta[conv_name]["last_message"] = timestamp.isoformat()

    return {
        "export_date": datetime.now().isoformat(),
        "source": "Android SMS Backup & Restore",
        "total_messages": len(all_messages),
        "total_conversations": len(conversations_meta),
        "conversations": list(conversations_meta.values()),
        "messages": all_messages
    }


def export_ai_ready(messages):
    """Export messages to AI-ready JSON and CSV formats."""

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    export_data = build_export_data(messages)
    all_messages = export_data["messages"]
    conversations_meta = {c["name"]: c for c in export_data["conversations"]}

    # Calculate stats
    sms_count = sum(1 for m in all_messages if m.get('source') == 'sms')
    mms_count = sum(1 for m in all_messages if m.get('source') == 'mms')
    text_count = sum(1 for m in all_messages if m["message_type"] == "text")
    attachment_count = sum(1 for m in all_messages if m["message_type"] == "attachment")
    text_att_count = sum(1 for m in all_messages if m["message_type"] == "text_with_attachment")

    print(f"\nMessage breakdown:")
    print(f"  - SMS messages:       {sms_count:,}")
    print(f"  - MMS messages:       {mms_count:,}")
    print(f"  - Text only:          {text_count:,}")
    print(f"  - Attachments only:   {attachment_count:,}")
    print(f"  - Text + attachment:  {text_att_count:,}")
    print(f"  - Total:              {len(all_messages):,}")

    # Write JSON
    json_path = os.path.join(OUTPUT_DIR, "messages.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2)

    print(f"\nCreated {json_path}")

    # Write CSV
    csv_path = os.path.join(OUTPUT_DIR, "messages.csv")

    if all_messages:
        fieldnames = ["timestamp", "date", "time", "year", "month", "day", "hour",
                      "day_of_week", "conversation", "conversation_type", "address",
                      "sender", "is_from_me", "message_type", "text",
                      "has_attachment", "attachment_types", "reaction",
                      "special_content", "effect", "char_count", "word_count",
                      "source"]

        import csv
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for msg in all_messages:
                msg_copy = msg.copy()
                msg_copy["attachment_types"] = ",".join(msg_copy["attachment_types"]) if msg_copy["attachment_types"] else ""
                writer.writerow(msg_copy)

        print(f"Created {csv_path}")

    # Write summary
    summary_path = os.path.join(OUTPUT_DIR, "SUMMARY.md")

    photos = sum(1 for m in all_messages if "photo" in m.get("attachment_types", []))
    videos = sum(1 for m in all_messages if "video" in m.get("attachment_types", []))
    audio = sum(1 for m in all_messages if "audio" in m.get("attachment_types", []))

    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write("# Android SMS Export Summary\n\n")
        f.write(f"**Export Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"**Source:** SMS Backup & Restore app\n")
        f.write(f"**Total Messages:** {len(all_messages):,}\n")
        f.write(f"**Total Conversations:** {len(conversations_meta)}\n\n")

        if all_messages:
            first_date = all_messages[0]["date"]
            last_date = all_messages[-1]["date"]
            f.write(f"**Date Range:** {first_date} to {last_date}\n\n")

        f.write("## Message Types\n\n")
        f.write(f"- **SMS messages:** {sms_count:,}\n")
        f.write(f"- **MMS messages:** {mms_count:,}\n")
        f.write(f"- **Text only:** {text_count:,}\n")
        f.write(f"- **Attachments only:** {attachment_count:,}\n")
        f.write(f"- **Text with attachments:** {text_att_count:,}\n\n")

        f.write("## Attachments\n\n")
        f.write(f"- **Photos:** {photos:,}\n")
        f.write(f"- **Videos:** {videos:,}\n")
        f.write(f"- **Audio:** {audio:,}\n\n")

        f.write("## Top 20 Conversations (by message count)\n\n")
        sorted_convos = sorted(conversations_meta.values(), key=lambda x: x["message_count"], reverse=True)[:20]
        for conv in sorted_convos:
            f.write(f"- **{conv['name']}**: {conv['message_count']:,} messages\n")

        f.write("\n## Files\n\n")
        f.write("- `messages.json` - Full structured data for AI analysis\n")
        f.write("- `messages.csv` - Tabular format for spreadsheets or analysis\n")
        f.write("- `SUMMARY.md` - This file\n")
        f.write("- Individual folders - Markdown files organized by contact and date\n")

    print(f"Created {summary_path}")


def export_call_logs(call_logs):
    """Export call logs to JSON and CSV."""
    if not call_logs:
        return

    # Sort by timestamp
    call_logs = [c for c in call_logs if c.get('timestamp')]
    call_logs.sort(key=lambda x: x['timestamp'])

    # Write JSON
    json_path = os.path.join(OUTPUT_DIR, "call_logs.json")

    export_data = {
        "export_date": datetime.now().isoformat(),
        "total_calls": len(call_logs),
        "calls": [
            {
                "timestamp": c['timestamp'].isoformat(),
                "date": c['timestamp'].strftime("%Y-%m-%d"),
                "time": c['timestamp'].strftime("%H:%M:%S"),
                "contact": c['contact'],
                "number": c['number'],
                "type": c['type'],
                "duration_seconds": c['duration_seconds'],
                "duration_formatted": f"{c['duration_seconds'] // 60}:{c['duration_seconds'] % 60:02d}"
            }
            for c in call_logs
        ]
    }

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2)

    print(f"Created {json_path}")

    # Write CSV
    csv_path = os.path.join(OUTPUT_DIR, "call_logs.csv")

    import csv
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "date", "time", "contact", "number", "type", "duration_seconds"])
        for c in call_logs:
            writer.writerow([
                c['timestamp'].isoformat(),
                c['timestamp'].strftime("%Y-%m-%d"),
                c['timestamp'].strftime("%H:%M:%S"),
                c['contact'],
                c['number'],
                c['type'],
                c['duration_seconds']
            ])

    print(f"Created {csv_path}")


def main():
    print("=" * 60)
    print("Desmond - Android SMS Exporter")
    print("=" * 60)
    print()

    full_export = "--full" in sys.argv
    custom_file = None

    # Check for custom file path
    for i, arg in enumerate(sys.argv):
        if arg == "--file" and i + 1 < len(sys.argv):
            custom_file = sys.argv[i + 1]

    # Find or use specified backup file
    if custom_file:
        if not os.path.exists(custom_file):
            print(f"Error: Specified file does not exist: {custom_file}")
            sys.exit(1)
        backup_files = [{"path": custom_file, "filename": os.path.basename(custom_file)}]
    else:
        backup_files = find_backup_files()

    if not backup_files:
        print("\nNo Android SMS backup files found!")
        print("\nTo use this tool, you need a backup from 'SMS Backup & Restore' app:")
        print()
        print("1. Install 'SMS Backup & Restore' from Google Play Store:")
        print("   https://play.google.com/store/apps/details?id=com.riteshsahu.SMSBackupRestore")
        print()
        print("2. Open the app and tap 'Backup Now'")
        print("3. Select 'Messages' (and optionally 'Call Logs')")
        print("4. Choose where to save (Google Drive, local storage, etc.)")
        print("5. Transfer the XML file to your computer")
        print("6. Run this script again")
        print()
        print("You can also specify a file directly:")
        print("  python android_sms_exporter.py --file \"path/to/backup.xml\"")
        sys.exit(1)

    # SMS Backup & Restore writes messages and call logs to SEPARATE files —
    # use the most recent of EACH kind rather than whichever file is newest.
    for b in backup_files:
        b.setdefault("kind", classify_backup(b["path"]) or "messages")
    message_files = [b for b in backup_files if b["kind"] == "messages"]
    call_files = [b for b in backup_files if b["kind"] == "calls"]

    messages, call_logs = [], []
    backup_file = None
    if message_files:
        backup_file = message_files[0]["path"]
        print(f"\nUsing messages backup: {backup_file}")
        messages, calls_inline = parse_sms_backup(backup_file)
        call_logs.extend(calls_inline)
    if call_files:
        print(f"Using call-log backup: {call_files[0]['path']}")
        _more_msgs, more_calls = parse_sms_backup(call_files[0]["path"])
        call_logs.extend(more_calls)

    if not messages and not call_logs:
        print("\nNo messages found in backup file.")
        sys.exit(1)

    # Export
    if messages:
        print("\nExporting messages...")
        export_messages(messages, full_export=full_export)

        print("\nCreating AI-ready exports...")
        export_ai_ready(messages)
    else:
        print("\nNo messages backup found (only call logs) — back up "
              "'Messages' in SMS Backup & Restore to export texts.")

    if call_logs:
        print(f"\nExporting {len(call_logs)} call logs...")
        export_call_logs(call_logs)

    print(f"\nExport complete! Files saved to:")
    print(f"  {OUTPUT_DIR}")

    # Save state
    state = load_state()
    state["last_export"] = datetime.now().isoformat()
    if backup_file:
        state["last_file"] = backup_file
    save_state(state)


if __name__ == "__main__":
    main()
