# Desmond - Product Specification

## Overview

Desmond is a cross-platform toolkit that exports iMessage/SMS history to AI-ready formats. Named after Desmond Hume from *Lost*, who pushed the button every 108 minutes to save the world.

**Core Value Proposition:** Export your entire text message history for AI analysis, backup, or archival purposes. All processing happens locally - nothing is uploaded anywhere.

---

## Platform Support

| Feature | macOS (iMessage) | Windows (iPhone) | Android |
|---------|------------------|------------------|---------|
| Message export | Yes | Yes | Yes |
| Contact name resolution | Yes | Yes | From backup |
| Automated sync | Yes | No | No |
| Scheduled exports | Yes (launchd) | Yes (Task Scheduler) | No |
| Data source | Messages app | iPhone backup | SMS Backup & Restore XML |
| Call log export | No | No | Yes |

---

## Architecture

### macOS Version (iMessage)

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   desmond.sh    │────▶│  Messages App    │────▶│   chat.db       │
│  (sync helper)  │     │  (iCloud sync)   │     │  (SQLite)       │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Output Files   │◀────│ imessage_exporter│◀────│  AddressBook    │
│  (.json, .csv)  │     │      .py         │     │  (contacts)     │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

**Data Flow:**
1. `desmond.sh` automates iCloud Messages sync via AppleScript
2. `imessage_exporter.py` reads directly from `~/Library/Messages/chat.db`
3. Contact names resolved from macOS AddressBook database
4. Output written to `~/Downloads/iMessages_Export/`

### Windows Version (iPhone)

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│    iTunes /     │────▶│  iPhone Backup   │────▶│   sms.db        │
│  Apple Devices  │     │  (local folder)  │     │  (hashed file)  │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Output Files   │◀────│ imessage_exporter│◀────│ AddressBook.db  │
│  (.json, .csv)  │     │   _windows.py    │     │  (from backup)  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

**Data Flow:**
1. User creates iPhone backup via iTunes/Apple Devices
2. `imessage_exporter_windows.py` locates backup in standard locations
3. Reads messages from hashed database file (`3d0d7e5fb2ce288813306e4d4636395e047a3d28`)
4. Contact names resolved from backup's AddressBook
5. Output written to `~/Documents/iMessages_Export/`

### Android Version

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  SMS Backup &   │────▶│   XML Backup     │────▶│  Transfer to    │
│  Restore App    │     │   File           │     │  Computer       │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Output Files   │◀────│ android_sms_     │◀────│ Contact names   │
│  (.json, .csv)  │     │ exporter.py      │     │ (from XML)      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

**Data Flow:**
1. User creates backup using "SMS Backup & Restore" app on Android
2. XML file transferred to computer (via USB, cloud, etc.)
3. `android_sms_exporter.py` parses the XML backup
4. Contact names extracted from XML (if included in backup)
5. Output written to `~/Downloads/Android_SMS_Export/` (Mac) or `~/Documents/Android_SMS_Export/` (Windows)

---

## Components

### macOS Components (iMessage)

| File | Purpose | Technology |
|------|---------|------------|
| `desmond.sh` | Automates iCloud Messages sync | Bash + AppleScript |
| `imessage_exporter.py` | Exports messages to various formats | Python 3 (stdlib) |
| `setup_imessage_exporter.sh` | Configures automatic hourly exports | Bash + launchd |

### Windows Components (iPhone)

| File | Purpose | Technology |
|------|---------|------------|
| `imessage_exporter_windows.py` | Exports messages from iPhone backup | Python 3 (stdlib) |
| `desmond_windows.bat` | Easy launcher for the exporter | Batch script |
| `setup_windows.bat` | Configures automatic hourly exports | Batch + Task Scheduler |

### Android Components (cross-platform)

| File | Purpose | Technology |
|------|---------|------------|
| `android_sms_exporter.py` | Exports messages from SMS Backup XML | Python 3 (stdlib) |
| `android_export.sh` | macOS launcher script | Bash |
| `android_export_windows.bat` | Windows launcher script | Batch script |

---

## Output Formats

All platforms produce identical output formats:

### 1. `messages.json` - Structured Data
```json
{
  "export_date": "2024-01-15T10:30:00",
  "total_messages": 346476,
  "total_conversations": 3178,
  "conversations": [...],
  "messages": [
    {
      "timestamp": "2024-01-15T09:32:00",
      "conversation": "John Smith",
      "sender": "Me",
      "message_type": "text",
      "text": "Hello!",
      ...
    }
  ]
}
```

### 2. `messages.csv` - Tabular Format
All message fields in spreadsheet-compatible format.

### 3. `SUMMARY.md` - Statistics
- Total messages and conversations
- Date range
- Message type breakdown
- Top conversations

### 4. Per-Contact Folders
```
John Smith/
├── 2024-01-15.md
├── 2024-01-16.md
└── ...
```

### 5. `call_logs.json` / `call_logs.csv` (Android only)
- Incoming, outgoing, missed calls
- Duration and timestamps
- Contact information

---

## Message Data Schema

| Field | Type | Description | iMessage | Android |
|-------|------|-------------|----------|---------|
| `timestamp` | ISO 8601 | Full timestamp | Yes | Yes |
| `date` | YYYY-MM-DD | Date only | Yes | Yes |
| `time` | HH:MM:SS | Time only | Yes | Yes |
| `year`, `month`, `day`, `hour` | Integer | For time analysis | Yes | Yes |
| `day_of_week` | String | Monday, Tuesday, etc. | Yes | Yes |
| `conversation` | String | Contact/group name | Yes | Yes |
| `conversation_type` | String | "direct" or "group" | Yes | Always "direct" |
| `sender` | String | Who sent the message | Yes | Yes |
| `is_from_me` | Boolean | true if sent by user | Yes | Yes |
| `message_type` | String | text, attachment, reaction, special | Yes | text, attachment |
| `text` | String | Message content | Yes | Yes |
| `has_attachment` | Boolean | Has media attached | Yes | Yes |
| `attachment_types` | Array | photo, video, audio, file | Yes | Yes |
| `reaction` | String | loved, liked, laughed, etc. | Yes | No |
| `special_content` | String | GamePigeon, Apple Pay, etc. | Yes | No |
| `effect` | String | sent with balloons, etc. | Yes | No |
| `char_count` | Integer | Character count | Yes | Yes |
| `word_count` | Integer | Word count | Yes | Yes |
| `source` | String | sms, mms | No | Yes |

---

## Platform Differences

### Sync Automation

| Aspect | macOS | Windows | Android |
|--------|-------|---------|---------|
| Automatic sync | Yes - `desmond.sh` clicks "Sync Now" | No - requires manual backup | No - requires manual backup |
| How it works | AppleScript UI automation | N/A | N/A |
| Frequency | Every 15 seconds until complete | User-initiated | User-initiated |

**Why Windows/Android can't auto-sync:**
- Windows doesn't have access to iCloud Messages directly
- Android doesn't expose SMS database to external apps without root
- Both require user-initiated backups

### Database/File Location

| Platform | Data Source |
|----------|-------------|
| macOS | `~/Library/Messages/chat.db` (SQLite) |
| Windows | `%APPDATA%\Apple Computer\MobileSync\Backup\[device-id]\3d0d7e...` (SQLite) |
| Android | User-provided XML file from SMS Backup & Restore app |

### Contacts Resolution

| Platform | Contact Source |
|----------|----------------|
| macOS | `~/Library/Application Support/AddressBook/` |
| Windows | iPhone backup's AddressBook (hashed file in backup) |
| Android | Embedded in XML backup (if "Include contact names" enabled in app) |

### Scheduled Exports

| Platform | Scheduler | Configuration |
|----------|-----------|---------------|
| macOS | launchd | `~/Library/LaunchAgents/com.user.imessage-exporter.plist` |
| Windows | Task Scheduler | "Desmond iMessage Exporter" task |
| Android | N/A | Manual runs only |

### Permissions Required

**macOS:**
- Full Disk Access (for Messages database)
- Accessibility (for Sync Now automation)
- Contacts (for name lookup)

**Windows:**
- Administrator (only for Task Scheduler setup)
- No special permissions needed otherwise

**Android:**
- No special permissions on computer
- SMS Backup & Restore app needs SMS permission on phone

---

## Message Type Support

| Message Type | iMessage (Mac/Win) | Android |
|--------------|-------------------|---------|
| Text messages | Yes | Yes (SMS) |
| Photos/videos | Files exported (`imessage_attachments.py`) | Metadata only (MMS) |
| Audio messages | Files exported (`imessage_attachments.py`) | Metadata only |
| Reactions (tapbacks) | Yes | No |
| Message effects | Yes | No |
| GamePigeon games | Yes | No |
| Apple Pay | Yes | No |
| Digital Touch | Yes | No |
| Stickers | Yes | No |
| Group chats | Yes | Limited |
| Call logs | No | Yes |

---

## Security & Privacy

### Data Handling
- All processing happens locally
- No network requests
- No data uploaded anywhere
- Temporary files cleaned up after use

### Backup Encryption
- **macOS:** N/A (reads live database)
- **Windows:** Requires UNENCRYPTED iPhone backup
  - Encrypted backups cannot be read
  - User must disable backup encryption in iTunes/Apple Devices
- **Android:** N/A (XML backups are not encrypted)

### Sensitive Data
- Phone numbers may appear if contact not in address book
- Full message content is exported
- Users should secure their export files

---

## Limitations

### macOS (iMessage)
- Requires Messages in iCloud enabled
- Sync can be slow for large message histories
- Accessibility permission needed for automation

### Windows (iPhone)
- Requires manual iPhone backup
- Cannot read encrypted backups
- Data is only as recent as the last backup
- No SMS sync automation

### Android
- Requires "SMS Backup & Restore" app
- No reactions or special message types
- MMS attachments show as metadata only (not exported)
- Contact names depend on backup app settings
- No automated/scheduled exports

### All Platforms
- Attachment files: **macOS exports the real photos/videos/files** via
  `imessage_attachments.py`; Windows (iPhone backup) and Android still capture
  attachment metadata only
- Some special message types appear as "[unknown message type]"
- Very old messages may have missing metadata

---

## Android Backup App Details

The Android exporter works exclusively with **SMS Backup & Restore** by SyncTech:

- **App URL:** https://play.google.com/store/apps/details?id=com.riteshsahu.SMSBackupRestore
- **XML Schema:** Custom format with SMS, MMS, and call log elements
- **Contact names:** Optional - user must enable "Include contact names" in backup settings
- **MMS content:** Text is extracted; attachments are described by type but not exported
- **File naming:** Typically `sms-YYYYMMDDHHMMSS.xml` or user-specified

### Supported XML Elements

| Element | Description |
|---------|-------------|
| `<sms>` | Individual SMS messages with address, body, date, type |
| `<mms>` | MMS messages with multiple `<part>` and `<addr>` children |
| `<call>` | Call log entries with number, duration, type |

---

## Future Considerations

### Potential Enhancements
1. ✅ **Attachment export (macOS)** — `imessage_attachments.py` copies the real
   photo/video/files into a browsable, Google Drive-ready archive. *Still to do:*
   attachment extraction for Windows (iPhone backup) and Android (base64 in MMS XML).
2. **Incremental Windows backups** - Detect and prompt for new backup
3. **GUI version** - Cross-platform desktop app
4. **Export filtering** - By date range, conversation, or content
5. **Multiple backup support** - Merge data from multiple devices
6. **Android ADB export** - Direct export via Android Debug Bridge (requires developer mode)
7. **WhatsApp support** - Export from WhatsApp backup files

### Not Planned
- Cloud sync or upload features
- Real-time message monitoring
- Encrypted iPhone backup support (requires user's password)
- Root-required Android methods

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | - | Initial macOS release |
| 2.0 | - | Added Windows support via iPhone backups |
| 3.0 | - | Added Android support via SMS Backup & Restore |
