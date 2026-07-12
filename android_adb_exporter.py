#!/usr/bin/env python3
"""
Android ADB Exporter — read text messages STRAIGHT OFF a USB-connected
Android phone. No backup app, no XML file, no downloads: plug the phone in,
approve the connection on its screen, done.

How it works
------------
Android (unlike iOS) lets a computer read the phone's SMS/MMS store over USB
through `adb` (Android Debug Bridge) — the same official Google tool Android
developers use — via the public SMS content provider. Nothing is installed
on the phone and nothing is modified; this is a read-only query.

One-time phone setup (about 60 seconds)
---------------------------------------
1. Settings → About phone → tap "Build number" 7 times ("You are now a
   developer!")
2. Settings → System → Developer options → turn ON "USB debugging"
3. Plug the phone into the computer with a USB cable
4. On the phone, tap **Allow** when "Allow USB debugging?" pops up

adb itself ships with Android "platform tools" (installed by Android Studio,
or the standalone platform-tools zip from Google). This module finds it
automatically in the usual places.

Command line
------------
    cd ~/desmond
    python3 android_adb_exporter.py            # reads the connected phone,
                                               # writes the standard export
                                               # (~/Downloads/Android_SMS_Export
                                               #  or ~/Documents on Windows)

Using it from another app (the Desmond family web flow does this)
-----------------------------------------------------------------
    from android_adb_exporter import find_adb, list_devices, read_android_phone

    export = read_android_phone()      # standard Desmond export dict,
                                       # entirely in memory — messages.json
                                       # shape, ready for federation

What it reads (and honest limits)
---------------------------------
- **SMS** — full send/receive history, with contact names resolved from the
  phone's own address book.
- **MMS** — text parts of group/photo messages (attachment types noted;
  media itself is not pulled).
- **RCS ("chat features") is NOT readable** — Google Messages stores RCS
  chats in its private database, which no computer can read without rooting
  the phone. Appointment reminders from dentists/schools/pharmacies arrive
  as plain SMS, so they ARE captured; person-to-person chats between two
  modern Android phones may be RCS and missed. (SMS Backup & Restore XML
  has the same limitation.)
"""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

from android_sms_exporter import build_export_data, clean_contact_name, format_phone

ADB_TIMEOUT = 120   # seconds; big SMS stores take a while over USB

# Where adb usually lives when it isn't already on PATH.
ADB_CANDIDATES = [
    "~/Library/Android/sdk/platform-tools/adb",           # macOS, Android Studio
    "~/Android/Sdk/platform-tools/adb",                   # Linux, Android Studio
    "~/platform-tools/adb",                               # standalone unzip
    "~/Downloads/platform-tools/adb",
    "/usr/lib/android-sdk/platform-tools/adb",
    "/opt/homebrew/bin/adb",
    "/usr/local/bin/adb",
]
if sys.platform == "win32":
    ADB_CANDIDATES = [
        r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe",
        r"%USERPROFILE%\platform-tools\adb.exe",
        r"%USERPROFILE%\Downloads\platform-tools\adb.exe",
        r"C:\platform-tools\adb.exe",
    ]


class AdbError(Exception):
    """adb missing, no device, unauthorized device, or a failed query."""


def find_adb():
    """Locate the adb binary. Returns its path, or None if not installed."""
    path = shutil.which("adb")
    if path:
        return path
    for candidate in ADB_CANDIDATES:
        expanded = os.path.expandvars(os.path.expanduser(candidate))
        if os.path.isfile(expanded):
            return expanded
    return None


def _run_adb(args, adb=None, timeout=ADB_TIMEOUT):
    """Run adb with args, return stdout text. This is the ONLY function that
    touches subprocess — everything else takes a `run` callable so tests
    (and apps) can stub the phone."""
    adb = adb or find_adb()
    if not adb:
        raise AdbError(
            "adb (Android platform tools) isn't installed. Download "
            "'SDK Platform-Tools' from developer.android.com/tools/releases/"
            "platform-tools, unzip it, and re-run.")
    try:
        proc = subprocess.run([adb] + list(args), capture_output=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AdbError(f"adb failed: {e}")
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise AdbError((err or out).strip() or
                       f"adb exited with code {proc.returncode}")
    return out


def list_devices(run=_run_adb):
    """Connected Android devices. Returns [{"serial", "state", "model"}].
    state 'device' = ready; 'unauthorized' = the phone is waiting for the
    user to tap Allow on its screen."""
    out = run(["devices", "-l"])
    devices = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state not in ("device", "unauthorized", "offline"):
            continue
        model = ""
        for p in parts[2:]:
            if p.startswith("model:"):
                model = p[len("model:"):].replace("_", " ")
        devices.append({"serial": serial, "state": state, "model": model})
    return devices


# ---------------------------------------------------------------------------
# content query parsing
# ---------------------------------------------------------------------------

def _shell_args(serial, uri, projection):
    args = ["-s", serial] if serial else []
    return args + ["shell", "content", "query", "--uri", uri,
                   "--projection", ":".join(projection)]


def parse_content_rows(output, projection):
    """Parse `adb shell content query` output into dicts.

    Output looks like:
        Row: 0 _id=12, thread_id=3, address=+15125550100, date=17519..., \\
            type=1, body=Running late, be there soon
    Bodies can contain commas AND newlines, so parsing relies on the LAST
    projected column being the free-text one: every column before it is
    matched non-greedily, and the last column swallows the rest of the row
    (rows are split on the next 'Row: N' line start).
    """
    rows = []
    current = None
    # A new row must name the FIRST projected column right after "Row: N" —
    # otherwise a message body containing a line like "Row: 7 of the
    # spreadsheet is wrong" would be mistaken for a new row and truncate
    # the real one.
    row_start = re.compile(r"^Row: \d+ " + re.escape(projection[0]) + r"=")
    for line in output.splitlines():
        if row_start.match(line):
            if current is not None:
                rows.append(current)
            current = line
        elif current is not None:
            current += "\n" + line     # continuation of a multi-line value
    if current is not None:
        rows.append(current)

    pattern = re.compile(
        r"^Row: \d+ " +
        r", ".join(re.escape(col) + r"=(.*?)" for col in projection[:-1]) +
        (r", " if len(projection) > 1 else r"") +
        re.escape(projection[-1]) + r"=(.*)$",
        re.DOTALL)
    parsed = []
    for row in rows:
        m = pattern.match(row)
        if not m:
            continue
        rec = {col: m.group(i + 1) for i, col in enumerate(projection)}
        for col, val in rec.items():
            if val == "NULL":
                rec[col] = None
        parsed.append(rec)
    return parsed


def query_content(uri, projection, serial=None, run=_run_adb):
    """One content-provider query against the phone. Returns list of dicts."""
    out = run(_shell_args(serial, uri, projection))
    if "Permission Denial" in out or "SecurityException" in out:
        raise AdbError(
            "The phone refused to share messages over USB. Unlock the "
            "phone, make sure USB debugging is ON, and check the USB mode "
            "is not 'Charging only'.")
    return parse_content_rows(out, projection)


# ---------------------------------------------------------------------------
# Reading the phone
# ---------------------------------------------------------------------------

def _contact_map(serial, run):
    """number (last 10 digits) -> display name, from the phone's contacts."""
    try:
        rows = query_content("content://com.android.contacts/data/phones",
                             ["data1", "display_name"], serial, run)
    except AdbError:
        return {}   # contacts permission can be locked down; names optional
    mapping = {}
    for r in rows:
        number, name = r.get("data1"), clean_contact_name(r.get("display_name"))
        if number and name:
            digits = re.sub(r"\D", "", number)[-10:]
            if digits:
                mapping[digits] = name
    return mapping


def _name_for(address, contacts):
    digits = re.sub(r"\D", "", address or "")[-10:]
    return contacts.get(digits) if digits else None


def _ts_ms(value):
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms < 10_000_000_000:      # MMS dates are in seconds
        ms *= 1000
    try:
        return datetime.fromtimestamp(ms / 1000.0)
    except (OSError, OverflowError, ValueError):
        return None


def read_android_messages(serial=None, run=_run_adb, quiet=False):
    """Read SMS + MMS(text) off the phone. Returns the intermediate message
    list (same shape android_sms_exporter's parsers produce)."""
    contacts = _contact_map(serial, run)
    messages = []
    thread_names = {}

    # ---- SMS ----
    sms_rows = query_content("content://sms",
                             ["_id", "thread_id", "address", "date", "type",
                              "body"], serial, run)
    for r in sms_rows:
        when = _ts_ms(r.get("date"))
        if when is None:
            continue
        try:
            msg_type = int(r.get("type") or 1)
        except ValueError:
            msg_type = 1
        if msg_type == 3:        # drafts aren't messages anyone received
            continue
        address = r.get("address") or "Unknown"
        name = _name_for(address, contacts)
        conversation = name or format_phone(address)
        if r.get("thread_id"):
            thread_names.setdefault(r["thread_id"], conversation)
        is_from_me = msg_type != 1
        messages.append({
            "source": "sms",
            "timestamp": when,
            "conversation": conversation,
            "address": address,
            "sender": "Me" if is_from_me else conversation,
            "is_from_me": is_from_me,
            "text": r.get("body") or "",
            "message_type": "text",
            "has_attachment": False,
            "attachment_types": [],
            "status": "sent" if is_from_me else "received",
        })
    if not quiet:
        print(f"   💬 SMS: {len(messages)} messages")

    # ---- MMS (text parts; media noted, not pulled) ----
    try:
        mms_rows = query_content("content://mms",
                                 ["_id", "thread_id", "date", "msg_box"],
                                 serial, run)
        part_rows = query_content("content://mms/part",
                                  ["mid", "ct", "text"], serial, run)
    except AdbError:
        mms_rows, part_rows = [], []   # some OEMs lock MMS down; SMS still counts
    parts_by_mid = {}
    for p in part_rows:
        parts_by_mid.setdefault(p.get("mid"), []).append(p)
    mms_count = 0
    for r in mms_rows:
        when = _ts_ms(r.get("date"))
        if when is None:
            continue
        try:
            box = int(r.get("msg_box") or 1)
        except ValueError:
            box = 1
        if box == 3:
            continue
        conversation = thread_names.get(r.get("thread_id"),
                                        "MMS conversation " +
                                        str(r.get("thread_id") or "?"))
        texts, attachments = [], []
        for p in parts_by_mid.get(r.get("_id"), []):
            ct = p.get("ct") or ""
            if "text/plain" in ct and p.get("text"):
                texts.append(p["text"])
            elif ct.startswith("image/"):
                attachments.append("photo")
            elif ct.startswith("video/"):
                attachments.append("video")
            elif ct.startswith("audio/"):
                attachments.append("audio")
            elif ct and "smil" not in ct and not ct.startswith("text/"):
                attachments.append("file")
        body = " ".join(texts)
        if not body and not attachments:
            continue
        is_from_me = box != 1
        messages.append({
            "source": "mms",
            "timestamp": when,
            "conversation": conversation,
            "address": "",
            # Group-MMS sender attribution needs one extra USB query per
            # message (content://mms/<id>/addr) — skipped for speed, so
            # received MMS show the conversation as the sender.
            "sender": "Me" if is_from_me else conversation,
            "is_from_me": is_from_me,
            "text": body or f"[{', '.join(attachments)}]",
            "message_type": ("text_with_attachment" if body and attachments
                             else "attachment" if attachments else "text"),
            "has_attachment": bool(attachments),
            "attachment_types": attachments,
            "status": "sent" if is_from_me else "received",
        })
        mms_count += 1
    if not quiet:
        print(f"   💬 MMS: {mms_count} messages (text parts)")

    messages.sort(key=lambda m: m["timestamp"])
    return messages


def read_android_phone(serial=None, run=_run_adb, quiet=False):
    """The whole thing, in memory: pick the connected phone, read its
    messages, return a standard Desmond export dict (messages.json shape).
    Raises AdbError with a human explanation when the phone isn't ready."""
    devices = list_devices(run)
    ready = [d for d in devices if d["state"] == "device"]
    waiting = [d for d in devices if d["state"] == "unauthorized"]
    if serial:
        if not any(d["serial"] == serial for d in ready):
            raise AdbError(f"Device {serial} isn't connected and authorized.")
    elif not ready:
        if waiting:
            raise AdbError(
                "The phone is connected but hasn't been authorized — unlock "
                "it and tap **Allow** on the 'Allow USB debugging?' prompt "
                "(check 'Always allow'), then try again.")
        raise AdbError(
            "No Android phone detected. Plug it in with a USB cable and "
            "make sure USB debugging is ON (Settings → About phone → tap "
            "'Build number' 7× → Developer options → USB debugging).")
    else:
        serial = ready[0]["serial"]

    messages = read_android_messages(serial, run, quiet=quiet)
    export = build_export_data(messages)
    export["source"] = "Android phone via USB (adb)"
    return export


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    print("📱 Desmond — Android over USB")
    print("   Reads your texts straight off the plugged-in phone. Read-only.")
    adb = find_adb()
    if not adb:
        print("\n❌ adb isn't installed. One-time setup:")
        print("   1. Download 'SDK Platform-Tools' from")
        print("      developer.android.com/tools/releases/platform-tools")
        print("   2. Unzip it (e.g. into your home folder)")
        print("   3. Re-run this script")
        sys.exit(1)
    try:
        export = read_android_phone()
    except AdbError as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    # Reuse the standard Android export writer for files on disk.
    import android_sms_exporter as axe
    os.makedirs(axe.OUTPUT_DIR, exist_ok=True)
    import json
    json_path = os.path.join(axe.OUTPUT_DIR, "messages.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2)
    print(f"\n✅ {export['total_messages']:,} messages from "
          f"{export['total_conversations']:,} conversations")
    print(f"   → {json_path}")
    print("\n   Note: RCS 'chat features' conversations can't be read over "
          "USB (Google keeps them private on the phone). Appointment and "
          "school reminders arrive as SMS, so they're included.")
    print('\n"See you in another life, brother."')


if __name__ == "__main__":
    main()
