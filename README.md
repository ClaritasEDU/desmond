# Desmond

**"We have to push the button."**

A cross-platform toolkit that exports your text message history to AI-ready formats. Works with iMessage on Mac, iPhone backups on Windows, and Android SMS backups.

Named after Desmond Hume from *Lost*, who pushed the button every 108 minutes to save the world.

---

## Quick Start

**Not sure where to begin?** Use our interactive setup guide:

**[Open the Setup Guide →](https://christreadaway.github.io/desmond/)**

Or open `index.html` locally in your browser.

The guide will ask about your phone and computer, then show you exactly what to do.

---

## Platform Support

| Platform | Data Source | Auto Sync | Script |
|----------|-------------|-----------|--------|
| **macOS (iMessage)** | Messages app (iCloud) | Yes | `imessage_exporter.py` |
| **Windows (iPhone)** | iPhone backup (iTunes) | No | `imessage_exporter_windows.py` |
| **Android** | SMS Backup & Restore app | No | `android_sms_exporter.py` |

**Everything runs locally. Nothing is uploaded anywhere.**

---

## What You Get

```
~/Downloads/iMessages_Export/      (Mac - iMessage)
~/Documents/iMessages_Export/      (Windows - iPhone)
~/Downloads/Android_SMS_Export/    (Mac - Android)
~/Documents/Android_SMS_Export/    (Windows - Android)
├── messages.json               # Full structured data for AI analysis
├── messages.csv                # Tabular format for spreadsheets
├── SUMMARY.md                  # Stats, top conversations, content breakdown
├── INDEX.md                    # List of all conversations
├── John Smith/
│   ├── 2024-01-15.md
│   └── ...
└── ...
```

### JSON/CSV Fields

Every message includes:

| Field | Description |
|-------|-------------|
| `timestamp` | ISO format (2024-01-15T09:32:00) |
| `date`, `time` | Separate date and time |
| `year`, `month`, `day`, `hour` | For time analysis |
| `day_of_week` | Monday, Tuesday, etc. |
| `conversation` | Contact or group name |
| `conversation_type` | "direct" or "group" |
| `sender` | Who sent the message |
| `is_from_me` | true/false |
| `message_type` | "text", "attachment", "reaction", "special" |
| `text` | Message content or description |
| `has_attachment` | true/false |
| `attachment_types` | ["photo", "video", "audio", "file"] |
| `reaction` | "loved", "liked", "laughed", etc. (iMessage only) |
| `special_content` | "GamePigeon game", "Apple Pay", etc. (iMessage only) |
| `effect` | "sent with balloons", "sent gently", etc. (iMessage only) |
| `char_count`, `word_count` | For analysis |

---

## macOS Setup (iMessage)

### Requirements

- macOS with Messages app
- Messages in iCloud enabled (on both iPhone and Mac)
- Python 3 (pre-installed on macOS)

### 1. Grant Terminal Permissions

Open **System Settings > Privacy & Security** and add Terminal to:
- **Full Disk Access** (required)
- **Accessibility** (for sync automation)

For **Contacts** access, run this in Terminal to trigger the permission prompt:

```bash
osascript -e 'tell application "Contacts" to get name of first person'
```

Click **OK** when the popup appears. Restart Terminal after granting permissions.

### 2. Sync Your Messages (if needed)

If your iCloud Messages sync keeps pausing:

```bash
chmod +x desmond.sh
./desmond.sh
```

Desmond will click Sync Now every 15 seconds and show your progress:

```
[15:44:08] ====== STARTING ======
[15:44:08] Messages on Mac: 142,847
[15:44:08] Conversations: 89
[15:44:08] ========================

[15:44:23] Push #2 - +312 new messages (total: 143,159)
[15:44:38] Push #3 - +287 new messages (total: 143,446)
...

[15:52:53] ====== SYNC APPEARS COMPLETE ======
[15:52:53] Final count: 346,476 messages
[15:52:53] "See you in another life, brother."
```

**Optional:** Set a target if you know your message count:

```bash
./desmond.sh 346000
```

### 3. Export Your Messages

```bash
python3 imessage_exporter.py --full
```

### 4. Automatic Exports (optional)

To run exports hourly in the background:

```bash
chmod +x setup_imessage_exporter.sh
./setup_imessage_exporter.sh
```

---

## Archiving Photos & Videos (and Google Drive)

The exporters above save your message **text** and note when a photo/video was
sent. To actually **keep the photos, videos, and files themselves**, use the
attachment archiver. It copies the real media out of Messages into a browsable
folder you can keep forever — ideal for dropping onto Google Drive.

> Runs on your **Mac** (that's where Messages and the files live). To land it on
> Google Drive, install **Google Drive for desktop** and point `--dest` at your
> Drive folder — the Drive app uploads it automatically. Desmond auto-detects a
> Drive folder if you have one.

```bash
# 1. See how much space it will take first (copies nothing):
python3 imessage_attachments.py --dry-run

# 2. Archive everything (auto-detects Google Drive, else ~/Downloads):
python3 imessage_attachments.py --full

# Only images & videos:
python3 imessage_attachments.py --photos-videos

# Or send it straight to a specific Google Drive folder:
python3 imessage_attachments.py --full --dest "/Users/you/Library/CloudStorage/GoogleDrive-…/My Drive/Messages"
```

Or just double-click `desmond_attachments.sh`.

**What you get:**

```
Desmond_Message_Attachments/
├── ATTACHMENTS_INDEX.md     # counts, total size, top conversations, what's missing
├── attachments.json         # manifest: every file → conversation, sender, date, text
├── attachments.csv          # same, for spreadsheets
├── Mom/
│   ├── 2024-01-15_0932_IMG_1234.HEIC
│   └── 2024-03-02_1810_movie.MOV
└── ...
```

Files are named `YYYY-MM-DD_HHMM_originalname` so they sort by date and stay
recognizable. To **find** something later, browse the per-contact folders or
open `attachments.csv` and filter by person/date/type, then follow `saved_path`.

**Reads Messages read-only — it never modifies or deletes anything.** Re-runs are
incremental (only new attachments are copied).

> **⚠️ Before you delete anything from your phone to free up space:** if
> "Messages in iCloud" with "Optimize Mac Storage" is on, some originals may be
> offloaded and not on your Mac yet. The archiver lists these as **MISSING** in
> `ATTACHMENTS_INDEX.md`. Re-download them (open the thread, turn off "Optimize
> Mac Storage", or run `desmond.sh` to sync) and re-run until nothing is missing
> — *then* it's safe to clear space on the phone.

---

## Browse conversations with photos inline (the picker)

Prefer to grab specific people and *read* the thread with media in place? Use the
browser picker:

```bash
python3 imessage_picker.py     # opens in your browser
```

- **Search & pick people** (the controls you already know), choose a date range,
  preview, and trim before saving.
- Turn on **📎 Photos / videos / files** to copy the **real attachments** into the
  export and show them **inline in the conversation** — images render, videos and
  audio play, right where they were sent.
- **Order toggle:** oldest-first or newest-first — set it before saving *and* flip
  it live in the saved `conversation.html`.
- **Where to save:** defaults to **Google Drive** (if Google Drive for desktop is
  installed) or your **Downloads** folder otherwise — editable in the UI.
- Attachment filenames lead with the **date/time and the people in the chat**,
  e.g. `2024-01-15_0932_Mom_IMG_1234.HEIC`. Originals are preserved; HEIC photos
  also get a JPG copy so they display in any browser.

Each saved export folder contains `conversation.html` (read it here),
`conversation.md`, `messages.json`, `messages.csv`, and an `attachments/` folder.

---

## Windows Setup (iPhone)

### Requirements

- Windows 10 or 11
- Python 3 ([download here](https://www.python.org/downloads/))
- iTunes (Windows 10) or Apple Devices app (Windows 11)
- An **unencrypted** iPhone backup

### 1. Install Python

Download and install Python from [python.org](https://www.python.org/downloads/).

**Important:** Check "Add Python to PATH" during installation.

### 2. Create an iPhone Backup

1. Connect your iPhone to your Windows PC
2. Open iTunes (Windows 10) or Apple Devices (Windows 11)
3. Select your device
4. **Uncheck** "Encrypt local backup" (encrypted backups cannot be read)
5. Click "Back Up Now"
6. Wait for backup to complete

### 3. Export Your Messages

**Option A: Double-click**
- Double-click `desmond_windows.bat`

**Option B: Command line**
```cmd
python imessage_exporter_windows.py --full
```

The script will automatically find your most recent iPhone backup.

### 4. Automatic Exports (optional)

To run exports hourly using Windows Task Scheduler:

1. Right-click `setup_windows.bat`
2. Select "Run as administrator"
3. Follow the prompts

---

## Android Setup (SMS/MMS)

Works on both **Windows** and **macOS**.

### Requirements

- Android phone
- "SMS Backup & Restore" app ([Google Play](https://play.google.com/store/apps/details?id=com.riteshsahu.SMSBackupRestore))
- Python 3

### 1. Install SMS Backup & Restore

Download from Google Play Store:
https://play.google.com/store/apps/details?id=com.riteshsahu.SMSBackupRestore

### 2. Create a Backup

1. Open "SMS Backup & Restore" on your Android phone
2. Tap **Backup Now**
3. Select **Messages** (and optionally **Call Logs**)
4. Choose backup location:
   - **Local storage** - then transfer the XML file to your computer
   - **Google Drive** - then download the XML file to your computer
5. Wait for backup to complete
6. Transfer the `.xml` file to your computer (Downloads folder works best)

### 3. Export Your Messages

**On Windows:**
```cmd
# Double-click android_export_windows.bat
# Or run:
python android_sms_exporter.py
```

**On macOS:**
```bash
chmod +x android_export.sh
./android_export.sh
# Or run:
python3 android_sms_exporter.py
```

**With a specific file:**
```bash
python3 android_sms_exporter.py --file "path/to/sms-backup.xml"
```

The script will automatically search common folders (Downloads, Documents, Desktop) for backup files.

### What Gets Exported

- All SMS text messages
- MMS messages (including photo/video descriptions)
- Call logs (if included in backup)
- Contact names (if available in backup)

---

## Platform Comparison

| Feature | macOS (iMessage) | Windows (iPhone) | Android |
|---------|------------------|------------------|---------|
| **Data source** | Live Messages DB | iPhone backup | SMS Backup XML |
| **Sync automation** | Yes | No | No |
| **Data freshness** | Real-time | As of last backup | As of last backup |
| **Reactions** | Yes | Yes | No |
| **Special content** | Yes (GamePigeon, etc.) | Yes | No |
| **Message effects** | Yes | Yes | No |
| **Call logs** | No | No | Yes (optional) |
| **MMS/photos (the actual files)** | **Exported** (`imessage_attachments.py`) | Metadata only | Metadata only |

### Key Differences

**iMessage (Mac/Windows)**
- Rich message types: reactions, effects, games, Apple Pay
- Group chat support with participant tracking
- Requires Apple ecosystem (iPhone + Mac or iTunes)

**Android SMS**
- Standard SMS/MMS only (no special features)
- Call log export included
- Works with any Android phone
- No root or special permissions needed

---

## Using with Claude

**For analysis and insights:**
- Upload `messages.json` — Claude can analyze patterns, relationships, timing, sentiment
- Upload `SUMMARY.md` — Quick overview when you just need context

**Example prompts:**
- "Who do I text the most?"
- "What time of day am I most active?"
- "Show me my messaging patterns by day of week"
- "Find all messages where I discussed [topic]"
- "What's the sentiment trend in my conversations with [person]?"
- "Summarize my conversations with [person] over the last year"

---

## Message Types Explained

### iMessage (Mac/Windows)

| Type | What it captures |
|------|------------------|
| `text` | Regular text messages |
| `attachment` | Photos, videos, audio messages, files (no text) |
| `text_with_attachment` | Text message that also has media |
| `reaction` | Tapback reactions (loved, liked, laughed, etc.) |
| `special` | GamePigeon, Apple Pay, Digital Touch, stickers, handwriting, etc. |

### Android

| Type | What it captures |
|------|------------------|
| `text` | SMS text messages |
| `attachment` | MMS with media (photo/video description) |
| `text_with_attachment` | MMS with text and media |

---

## Troubleshooting

### macOS (iMessage)

**"No new messages to export"**
- Make sure Terminal has Full Disk Access
- Restart Terminal after granting permissions

**Sync keeps pausing**
- Keep your Mac plugged in and awake
- Run `desmond.sh` to automate clicking Sync Now

**Contact names not showing**
- Grant Contacts access to Terminal
- Run: `osascript -e 'tell application "Contacts" to get name of first person'`

### Windows (iPhone)

**"No iPhone backup found"**
- Create a backup using iTunes or Apple Devices
- Make sure backup is not encrypted

**"Messages database not found in backup"**
- Your backup is encrypted
- Uncheck "Encrypt local backup" in iTunes/Apple Devices
- Create a new backup

**Python not found**
- Reinstall Python with "Add Python to PATH" checked

### Android

**"No Android SMS backup files found"**
- Make sure the XML backup file is in Downloads, Documents, or Desktop
- Use `--file` flag to specify the exact path

**"No messages found in backup file"**
- The file might be corrupted or from a different app
- This tool only works with "SMS Backup & Restore" app backups

**Contact names showing as phone numbers**
- The backup may not include contact names
- Enable "Include contact names" in SMS Backup & Restore settings before backing up

---

## Privacy & Security

- All processing happens locally on your computer
- No data is uploaded anywhere
- No network connections are made
- Export files contain your complete message history — secure them appropriately

---

## Files Reference

### macOS (iMessage)
| File | Purpose |
|------|---------|
| `desmond.sh` | Automates iCloud Messages sync |
| `imessage_exporter.py` | Exports message text from Mac |
| `imessage_attachments.py` | Archives the actual photos/videos/files (Google Drive-ready) |
| `desmond_attachments.sh` | Easy launcher for the attachment archiver |
| `imessage_picker.py` | Browser UI to pick/preview/export specific conversations |
| `setup_imessage_exporter.sh` | Sets up hourly automatic exports |

### Windows (iPhone)
| File | Purpose |
|------|---------|
| `imessage_exporter_windows.py` | Exports messages from iPhone backup |
| `desmond_windows.bat` | Easy launcher (double-click to run) |
| `setup_windows.bat` | Sets up hourly automatic exports |

### Android (both platforms)
| File | Purpose |
|------|---------|
| `android_sms_exporter.py` | Exports messages from SMS Backup XML |
| `android_export.sh` | macOS launcher |
| `android_export_windows.bat` | Windows launcher |

### Documentation
| File | Purpose |
|------|---------|
| `README.md` | This file |
| `PRODUCT_SPEC.md` | Detailed technical specification |
| `index.html` | Interactive web-based setup guide |

---

## License

MIT — do whatever you want with it.

---

## Support

If Desmond saved your sanity (and your messages):

[Patreon](https://www.patreon.com/c/christreadaway)

---

*"See you in another life, brother."*
