# Desmond

**"We have to push the button."**

A cross-platform toolkit that exports your text message history to AI-ready formats. Works with iMessage on Mac, iPhone backups on Windows, and Android SMS backups.

Text messages are the headline act. Three optional extras build on it:
**[Federation](#federation-two-people-one-shared-archive)** merges two people's
exports (say, a husband and wife) into one shared archive,
**[Family federation](#family-federation-find-the-coverage-gaps-messages--calendars)**
diffs two parents' messages **and calendars** to surface what only one of them
knows (the missed dentist text, the invite on one calendar), and
**[Consolidate mode](#optional-consolidate-mode-one-md-of-your-personal-data)**
builds a single Markdown file from your messages, calendar (Google / Outlook /
Apple), contacts, and call logs.

Named after Desmond Hume from *Lost*, who pushed the button every 108 minutes to save the world.

---

## Quick Start

**Not sure where to begin?** Use our interactive setup guide:

**[Open the Setup Guide →](https://christreadaway.github.io/desmond/)**

Or open `index.html` locally in your browser.

The guide will ask about your phone and computer, then show you exactly what to do.

---

## ⭐ Easiest: one command (full archive, text + media inline)

On a Mac, this single command exports your **whole** message history — text **and**
the real photos/videos — into one browsable archive, saved **locally and on Google
Drive**, then **verified**:

```bash
cd ~/desmond
python3 desmond_export.py
```

Open the `index.html` it creates, click a conversation, and read the entire thread
with **photos and videos inline, in date/time order**. (Or double-click
`desmond_export.sh`.)

```
Desmond_Message_Archive/
├── index.html                 # ← open this; searchable list of every conversation
├── conversations/
│   └── <Person>/
│       ├── conversation.html  # full thread, media inline, date-ordered (newest/oldest toggle)
│       └── attachments/       # the real files (named YYYY-MM-DD_HHMM_people_original)
├── attachments.json / .csv    # manifest
└── VERIFY_REPORT.md           # device vs local vs Google Drive
```

Useful flags: `--photos-videos` (images/videos only), `--newest` (newest first),
`--no-drive` (local only), `--drive "PATH"` (choose the Drive folder), `--retry`
(loop until local & Drive match). It reads Messages **read-only** and never
deletes anything; re-runs are incremental.

Each transcript shows **100 messages at a time** ("Show next 100" / "Show all")
and the conversation list pages the same way, so even a huge history opens
instantly instead of crashing the browser.

Every run also writes a **shareable log** to `~/Downloads/Desmond_Logs/` (a
`.log` and a `.json` with counts, timings, environment, and any errors — **no
message text or contact names**, and home path/Google Drive account/email are
redacted). Send me that `.json` to help refine how it works.

> *Why an `index.html` plus per-conversation files instead of one giant file?* A
> full history can be hundreds of thousands of messages — too large for any browser
> to open as a single page. One entry point that links to per-conversation
> transcripts keeps everything fast and openable. (For a single conversation as one
> self-contained file, use the picker below.)

The sections below document the individual building blocks (text-only export, the
attachment archiver, and the per-person picker), which `desmond_export.py` builds on.

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
├── INDEX.md                    # List of all conversations (iMessage exporters)
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

Your messages are saved **locally** *and* automatically copied to **Google Drive**
(into `Desmond_Messages_Export/`) if Google Drive for desktop is installed — so the
text archive lives in both places. Use `--no-drive` to skip the Drive copy, or
`--drive "/path/to/My Drive/Messages"` to choose where it goes.

### 4. Automatic Exports (optional)

To run exports hourly in the background:

```bash
chmod +x setup_imessage_exporter.sh
./setup_imessage_exporter.sh
```

---

## Archiving Photos & Videos (local + Google Drive)

The exporters above save your message **text** and note when a photo/video was
sent. To actually **keep the photos, videos, and files themselves**, use the
attachment archiver. It copies the real media out of Messages into a browsable
folder that lives in **both** places: a **local** copy *and* a **Google Drive**
mirror.

> Runs on your **Mac** (that's where Messages and the files live). It archives
> locally first, then mirrors to **Google Drive** if Google Drive for desktop is
> installed (auto-detected). The local copy is always kept.

```bash
# 1. See how much space it will take first (copies nothing):
python3 imessage_attachments.py --dry-run

# 2. Back up everything: local copy + mirror to Google Drive + auto-verify
python3 imessage_attachments.py --full

# Keep going until local AND Drive are 100% complete (loops a few passes):
python3 imessage_attachments.py --retry

# Other options:
python3 imessage_attachments.py --full --photos-videos   # images + videos only
python3 imessage_attachments.py --full --no-drive         # local copy only
python3 imessage_attachments.py --full --drive "/Users/you/Library/CloudStorage/GoogleDrive-…/My Drive"
```

Or just double-click `desmond_attachments.sh`.

**What you get (in both the local folder and Google Drive):**

```
Desmond_Message_Attachments/
├── ATTACHMENTS_INDEX.md     # counts, total size, top conversations, what's missing
├── attachments.json         # manifest: every file → conversation, sender, date, text
├── attachments.csv          # same, for spreadsheets
├── VERIFY_REPORT.md         # per-place counts + the exact diff (after --verify)
├── Mom/
│   ├── 2024-01-15_0932_Mom_IMG_1234.HEIC
│   └── 2024-03-02_1810_Mom_movie.MOV
└── ...
```

Files are named `YYYY-MM-DD_HHMM_<people>_originalname` so they sort by date, name
the people in the chat, and stay recognizable. To **find** something later, browse
the per-contact folders or open `attachments.csv` and filter by person/date/type.

**Reads Messages read-only — it never modifies or deletes anything.** Re-runs are
incremental, and the manifest is cumulative (history is never lost).

> **⚠️ Before you delete anything from your phone to free up space:** if
> "Messages in iCloud" with "Optimize Mac Storage" is on, some originals may be
> offloaded and not on your Mac yet. Verify lists these as **offloaded**.
> Re-download them (open the thread, turn off "Optimize Mac Storage", or run
> `desmond.sh` to sync) and re-run until nothing is missing — *then* it's safe to
> clear space on the phone.

---

### Verify: device vs local vs Google Drive

Confirm every attachment exists in all **three** places — what Messages knows
about (the device), the local archive, and Google Drive:

```bash
python3 imessage_attachments.py --verify      # or double-click desmond_verify.sh
```

It prints per-place counts and writes a **report** (`VERIFY_REPORT.md` +
`verify_diff.json`) listing exactly what's missing where:

```
ON THE DEVICE (Messages):  12,431 attachments — 12,419 downloaded, 12 offloaded in iCloud
IN LOCAL ARCHIVE:          12,419 / 12,419  ✅
ON GOOGLE DRIVE:           12,419 / 12,419  ✅
✅ ALL 12,419 attachments are present in all three places.
```

To close any gap, re-run the backup (it re-copies what's missing and re-mirrors),
or use `--retry` to loop until complete. Items still **offloaded in iCloud** must
be downloaded in Messages first. Exit code is non-zero until everything matches,
so it's scriptable.

> Drive uploads in the background — after a green verdict, glance at the Google
> Drive app (or drive.google.com) to confirm the upload finished before clearing
> space on your phone.

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
- **Lives in both places:** each pick is saved to a **local** folder and mirrored
  to **Google Drive** (toggle "☁︎ Also copy to Google Drive" in the UI). After
  saving it's **verified** — the UI shows `local N/N, Drive N/N` and writes a
  `VERIFY_REPORT.md` into the export.
- Attachment filenames lead with the **date/time and the people in the chat**,
  e.g. `2024-01-15_0932_Mom_IMG_1234.HEIC`. Originals are preserved; HEIC photos
  also get a JPG copy so they display in any browser.

Each saved export folder contains `conversation.html` (read it here),
`conversation.md`, `messages.json`, `messages.csv`, `VERIFY_REPORT.md`, and an
`attachments/` folder — in both the local copy and the Google Drive mirror.

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

## Federation: two people, one shared archive

For couples (or any two people who share their lives) who each want their
message history in one place — and other apps that need to combine two
people's data with consent.

**How it works:** each person runs Desmond on their *own* phone/computer,
then the two finished exports are merged:

```bash
cd ~/desmond
python3 desmond_federate.py "Chris=~/Downloads/iMessages_Export" \
                            "Kate=/path/to/kates_export"
```

- **Consent first.** The tool asks you to confirm that *both* people agreed
  before it merges anything (`--consented` for scripts that already asked).
  It never reads anyone's Messages database — only export folders that were
  handed to it.
- **The shared thread is stitched together.** Your conversation with each
  other appears in both exports — federation detects it (even with nicknames
  like "Kate ❤️"; use `--shared "Wifey=Hubs"` if the names don't match at
  all), deduplicates it, and rebuilds it as one transcript
  (`shared/Chris_and_Kate.md`).
- **Every message is tagged** with `owner` (whose phone it came from), and
  "Me" is rewritten to real names so a combined archive stays unambiguous.

**What you get:** `Desmond_Federated_Archive/` with `federated.json`,
`federated.csv`, `FEDERATED_SUMMARY.md`, and the merged shared-thread
transcript.

**For other apps — including online apps:** the whole merge is a pure,
in-memory, dependency-free function, so a web service can federate uploaded
exports without touching the filesystem:

```python
from desmond_federate import parse_export, federate_data

result = federate_data(
    [("Chris", parse_export(chris_upload)),    # bytes/str/dict all accepted
     ("Kate",  parse_export(kate_upload))],
    consented=True,                            # required, explicit
    consent_records=[                          # your app's consent trail,
        {"participant": "Chris", "agreed_at": "…"},   # stored in the archive
        {"participant": "Kate",  "agreed_at": "…"},
    ])
result["federated"]            # merged archive (JSON-serializable dict)
result["summary_md"]           # summary markdown, ready to render
result["shared_transcripts"]   # {thread title: transcript markdown}
```

An online app should set `consented=True` only after **each** participant has
affirmatively opted in. For local use, `federate()` wraps this and writes the
files (that's what the CLI does).

---

## Family federation: find the coverage gaps (messages + calendars)

Family coordination breaks in one specific way: the dentist texts one parent
about the kid's appointment and the other parent never hears about it; the
school event lands on one calendar but not the other. Family federation
combines **both parents' messages AND calendars** and then does what no
single-person export can — it **diffs the two views** and reports the blind
spots:

- 📅 **calendar events only on one parent's calendar**
- 💬 **texts only one parent received** (in threads you both have)
- 📥 **whole threads that only ever talk to one of you** (the dentist, the
  coach, the school office)

### ⭐ The easy way: the web wizard (no files, no exports)

```bash
cd ~/desmond
python3 desmond_family_web.py
```

Your browser opens a private page served only on this computer. Four steps:

1. **Names + consent** — both parents agree on screen; the consent trail is
   embedded in the result.
2. **Messages — plug the phone in, that's it:**
   - *Messages on this Mac* — read directly, nothing to plug in
   - *iPhone* — plug it in, make/refresh a local backup when the page asks
     (Finder / iTunes / Apple Devices, encryption unticked); the wizard
     finds and reads it in place
   - *Android* — plug it in with USB debugging on (the page shows the
     60-second setup); messages are read live over the cable
   - or drop a file on the page (a Desmond `messages.json`, or an SMS
     Backup & Restore `.xml` straight off the phone's storage)
   **One parent on iPhone and one on Android works** — every source lands
   in the same shape before the diff.
3. **Calendars — sign in, don't paste links:** each parent clicks
   **Connect Google** or **Connect Microsoft/Outlook** and signs in. A
   paste-a-link fallback hides under "Advanced" for iCloud published
   calendars.
4. **The gap report renders on the page.** Nothing is written to disk
   unless you click **Save archive**; quitting the terminal forgets
   everything.

**One-time developer setup for calendar sign-in** (you, once — not each
parent): register the app with Google and Microsoft and drop the client IDs
into `~/.desmond/oauth_clients.json`. The exact click-by-click steps are at
the top of `desmond_calendar_auth.py`; until it's done, the wizard shows
those steps and the link fallback still works.

### The scripted way (CLI, uses export files)

```bash
cd ~/desmond
python3 desmond_family.py \
    "Chris=~/Downloads/iMessages_Export" \
    "Kate=/path/to/kates_export" \
    --calendar "Chris=https://calendar.google.com/calendar/ical/…/basic.ics" \
    --calendar "Kate=webcal://…"
```

Writes `Desmond_Family_Archive/` with **`FAMILY_GAPS.md`**, `family.json`
(format `desmond-family/1`), `FAMILY_SUMMARY.md`, and the couple's merged
thread under `shared/`. `--since` / `--all` / `--keyword` /
`--same-thread "Dan (Soccer)=+1512555…"` control the report.

### How it stays low-noise

Identical texts within 5 minutes count as "both got it" (carriers lag);
same-day/same-title events match even when the two calendars disagree on
the minute; the default window is the last 30 days + everything upcoming.
Consent is enforced at the library level — nothing merges until every
participant has agreed.

### For apps (this is the ParentPoint hook)

The whole pipeline is pure, in-memory functions — no filesystem, no
printing; the web wizard is just a thin local UI over them:

```python
from desmond_sources import parse_upload            # or read_* readers
from desmond_calendar_auth import fetch_calendar_events
from desmond_family import parse_calendar, federate_family_data

result = federate_family_data(
    message_exports=[("Chris", parse_upload(chris_bytes, "messages.json")),
                     ("Kate",  parse_upload(kate_xml, "sms.xml"))],
    calendar_exports=[("Chris", parse_calendar(google_events, "google")),
                      ("Kate",  parse_calendar(outlook_events, "microsoft"))],
    consented=True, consent_records=[…], since="2026-06-01")

result["family"]["gaps"]   # structured gap lists: calendar/messages/threads
result["gaps_md"]          # FAMILY_GAPS.md as a string, ready to render
```

### Platform truth table

| Signal | iPhone | Android |
|--------|--------|---------|
| Texts | This Mac's Messages read directly; any other iPhone plugs in and its local backup is read in place | **Read live over USB** (`android_adb_exporter.py`, USB debugging) or SMS Backup & Restore XML dropped on the page. **RCS chats can't be read by any tool without root** — automated reminders are SMS and ARE captured |
| Calendar | Google/Microsoft **sign-in** (`desmond_calendar_auth.py`); iCloud published-link fallback | Identical — calendars are account-based, not phone-based |
| App notifications | **Not possible** — iOS has no API to read other apps' notifications; texts + calendar are the federable signals | `NotificationListenerService` can capture school/pharmacy/team app notifications with user permission — a future `android_notification_exporter.py` emitting the standard export shape would federate with zero changes here |
| Calls | — | `calls.xml` already exported; a "missed the school's call" gap could reuse the same differ |

---

## Optional: Consolidate mode (one .md of your personal data)

**Not the default** — Desmond's day job is still text messages. When you want
one AI-ready file of *you* to upload to Claude, consolidate mode builds a
single `PERSONAL_ARCHIVE.md` from the sources you pick:

```bash
cd ~/desmond
python3 desmond_consolidate.py                  # interactive: pick sources
python3 desmond_consolidate.py --sources all
python3 desmond_consolidate.py --sources calendar   # calendar only
```

| Source | Where it comes from |
|--------|---------------------|
| `messages` | Your existing Desmond export (auto-detected) |
| `calendar` | **Online (recommended):** your calendar's private iCal link, fetched fresh on every run — no exporting, no .zip. Works with **Google Calendar** and **Microsoft Outlook / 365** (see below). Exported `.ics` files (or Google's export `.zip`) still work as an offline fallback |
| `contacts` | `.vcf` vCard files (iCloud, Google Contacts, Android share) |
| `calls` | Call-log XML from "SMS Backup & Restore" (Android) |

**Connecting your calendar online (one-time, ~30 seconds):**

1. Copy your calendar's private address:
   - **Google Calendar:** calendar.google.com → ⚙ Settings → click your
     calendar in the left sidebar → *Integrate calendar* → copy **"Secret
     address in iCal format"**
   - **Outlook / Microsoft 365:** outlook.com → ⚙ Settings → Calendar →
     *Shared calendars* → *Publish a calendar* → copy the **ICS** link
     (`webcal://` links work too)
2. ```bash
   cd ~/desmond
   python3 desmond_consolidate.py --sources calendar \
       --calendar-url "PASTE_LINK_HERE" --remember
   ```

`--remember` saves the link (privately, chmod 600 in
`~/.desmond/calendar_feeds.json`) so every future run — including
`--sources all` and the interactive picker — fetches your calendar
automatically. The interactive mode will also offer to set this up the first
time you pick calendar. `--forget-calendar-urls` clears saved links. These
are secret URLs — anyone holding one can read that calendar — so Desmond
never prints them in full.

Other options: files in Downloads/Documents/Desktop are auto-detected;
point at specific files with `--calendar`, `--contacts`, `--calls`,
`--messages`. Messages appear as a per-conversation digest by default — add
`--messages-full` to inline every message (can be huge). `--json` also
writes a structured `personal_archive.json`.

Everything is processed locally. The only network use is fetching calendar
links **you** provide; the archive itself never leaves your machine. It
consolidates your personal information in one file — store it somewhere you
trust.

---

## Send your texts to PersonalCRM (one command)

[PersonalCRM](https://github.com/christreadaway/personalcrm) — the companion
relationship-analytics app — imports your real text messages so they drive its
timeline, word cloud, search, and intent mining. `desmond_crm_export.py` is the
one-command bridge:

```bash
cd ~/desmond
python3 desmond_crm_export.py                 # auto-detect the best source
python3 desmond_crm_export.py --mac           # this Mac's Messages (chat.db)
python3 desmond_crm_export.py --iphone        # newest plugged-in iPhone backup
python3 desmond_crm_export.py --iphone DIR    # a specific iPhone backup folder
python3 desmond_crm_export.py --android       # plugged-in Android over USB
python3 desmond_crm_export.py --from PATH      # an export you already made
python3 desmond_crm_export.py --out PATH       # default ~/Downloads/personalcrm_import.json
```

Then in PersonalCRM: **Settings → "Text Message Import (Desmond)"** → upload the
`personalcrm_import.json`.

- **Cell numbers come along automatically.** Each message carries the
  counterpart's phone/email (`address`), so PersonalCRM assigns numbers to
  people without any typing. Anyone the texts only gave a name for can be given
  a number by hand in the CRM.
- **Local only.** The script just reads your messages and writes one JSON file —
  nothing is uploaded. The output is the standard Desmond export shape (the same
  `messages.json` every reader emits), validated before writing.
- Under the hood it reuses `desmond_sources` (the in-memory readers) and
  `desmond_federate.parse_export`; no new dependencies.

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

- All processing happens locally on your computer. The tools upload nothing;
  the only network use is consolidate mode *downloading* calendar feed links
  you explicitly provide
- If you keep the **Google Drive mirror** on, the Google Drive desktop app
  then syncs that copy to your Google account (that's the point — an off-site
  backup). Use `--no-drive` for a purely local export
- Export files contain your complete message history — secure them appropriately
- Federation merges exports only with **both people's consent**, and the
  federated archive contains both histories — protect it accordingly

---

## Files Reference

### macOS (iMessage)
| File | Purpose |
|------|---------|
| `desmond_export.py` | **One-shot full export** — text + media inline, local + Drive, verified |
| `desmond_export.sh` | Easy launcher for the one-shot full export |
| `desmond_log.py` | Writes the PII-safe run log to `~/Downloads/Desmond_Logs/` |
| `desmond.sh` | Automates iCloud Messages sync |
| `imessage_exporter.py` | Exports message text from Mac |
| `imessage_attachments.py` | Archives the actual photos/videos/files (Google Drive-ready); `--verify` checks completeness |
| `desmond_attachments.sh` | Easy launcher for the attachment archiver |
| `desmond_verify.sh` | Verifies all attachments are in the Drive archive |
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

### Federation, Consolidate & Integrations (both platforms)
| File | Purpose |
|------|---------|
| `desmond_federate.py` | Merge two people's exports into one shared, consent-based archive (also an importable module for other apps) |
| `desmond_consolidate.py` | **Optional mode:** one `PERSONAL_ARCHIVE.md` from messages + calendar (.ics) + contacts (.vcf) + call logs |
| `desmond_crm_export.py` | **PersonalCRM bridge:** read texts from any source → write `personalcrm_import.json` for import into PersonalCRM (cell numbers included) |

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
