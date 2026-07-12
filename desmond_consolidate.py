#!/usr/bin/env python3
"""
Desmond Consolidate — an OPTIONAL mode that builds ONE Markdown file out of
your personal data: messages, calendar, contacts, and call logs.

This is NOT the default Desmond flow. Desmond's headline job is still text
messages (desmond_export.py). Consolidate is for when you want everything in
one AI-ready `PERSONAL_ARCHIVE.md` — upload it to Claude and ask questions
across your whole life ("what did I have going on the week Mom visited?").

Data sources (pick any combination)
-----------------------------------
    messages   your existing Desmond export (messages.json) — run an exporter
               first if you don't have one
    calendar   .ics files. Most people don't use Apple Calendar, so this is
               built for the big three:
                 • Google Calendar:  calendar.google.com → Settings →
                   Import & export → Export (downloads a .zip of .ics files —
                   point this tool at the .zip, it reads it directly)
                 • Microsoft Outlook: Calendar → Settings → Export calendar
                   (.ics), or File → Save Calendar in desktop Outlook
                 • Apple Calendar:   File → Export → Export… (.ics)
    contacts   .vcf vCard files (iCloud contacts export, Google Contacts →
               Export → vCard, or Android Contacts → Share)
    calls      call-log XML from the "SMS Backup & Restore" Android app

Everything runs locally. Nothing is uploaded anywhere.

Usage
-----
    cd ~/desmond
    python3 desmond_consolidate.py                  # interactive: pick sources
    python3 desmond_consolidate.py --sources all
    python3 desmond_consolidate.py --sources calendar
    python3 desmond_consolidate.py --sources calendar,contacts \\
        --calendar ~/Downloads/mycalendar.ics.zip \\
        --contacts ~/Downloads/contacts.vcf

    # optional:
    #   --messages DIR     Desmond export folder (auto-detected otherwise)
    #   --calls FILE       calls .xml from SMS Backup & Restore
    #   --out FILE         output path (default:
    #                      ~/Downloads/Desmond_Personal_Archive/PERSONAL_ARCHIVE.md)
    #   --messages-full    inline EVERY message in the .md (can be huge);
    #                      default is a per-conversation digest
    #   --json             also write personal_archive.json

The output file consolidates your personal information in one place — treat
it like a house key, not a flyer. Store it somewhere you trust.
"""

import argparse
import glob
import json
import os
import quopri
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import datetime

DEFAULT_OUT = os.path.expanduser(
    "~/Downloads/Desmond_Personal_Archive/PERSONAL_ARCHIVE.md")

SEARCH_DIRS = [os.path.expanduser(p) for p in
               ("~/Downloads", "~/Documents", "~/Desktop")]

MESSAGE_EXPORT_DIRS = [os.path.expanduser(p) for p in (
    "~/Downloads/iMessages_Export",
    "~/Documents/iMessages_Export",
    "~/Downloads/Android_SMS_Export",
    "~/Documents/Android_SMS_Export",
)]

ALL_SOURCES = ["messages", "calendar", "contacts", "calls"]

CALL_TYPES = {1: "incoming", 2: "outgoing", 3: "missed",
              4: "voicemail", 5: "rejected", 6: "blocked"}


# ---------------------------------------------------------------------------
# Calendar (.ics) — Google Calendar, Microsoft Outlook, Apple Calendar
# ---------------------------------------------------------------------------

def _unfold_ics(text):
    """RFC 5545 line unfolding: a line starting with space/tab continues the
    previous line."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_unescape(value):
    return (value.replace("\\n", "\n").replace("\\N", "\n")
                 .replace("\\,", ",").replace("\\;", ";")
                 .replace("\\\\", "\\"))


def _parse_ics_datetime(value):
    """Parse ICS DTSTART/DTEND values. Returns (datetime, all_day)."""
    value = value.strip()
    is_utc = value.endswith("Z")
    if is_utc:
        value = value[:-1]
    try:
        if "T" in value:
            return datetime.strptime(value, "%Y%m%dT%H%M%S"), False
        return datetime.strptime(value, "%Y%m%d"), True
    except ValueError:
        return None, False


def parse_ics(text, source_name=""):
    """Parse the events out of one .ics document. Returns a list of event
    dicts sorted by start time."""
    events = []
    current = None
    calendar_name = None
    for line in _unfold_ics(text):
        if ":" not in line:
            continue
        prop, value = line.split(":", 1)
        name = prop.split(";", 1)[0].upper()
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            current = {}
            continue
        if name == "END" and value.strip().upper() == "VEVENT":
            if current is not None and current.get("start"):
                events.append(current)
            current = None
            continue
        if current is None:
            if name == "X-WR-CALNAME":
                calendar_name = _ics_unescape(value.strip())
            continue
        if name == "SUMMARY":
            current["title"] = _ics_unescape(value.strip()) or "(untitled)"
        elif name == "DTSTART":
            dt, all_day = _parse_ics_datetime(value)
            if dt:
                current["start"] = dt
                current["all_day"] = all_day
        elif name == "DTEND":
            dt, _ = _parse_ics_datetime(value)
            if dt:
                current["end"] = dt
        elif name == "LOCATION":
            loc = _ics_unescape(value.strip())
            if loc:
                current["location"] = loc
        elif name == "DESCRIPTION":
            desc = _ics_unescape(value.strip())
            if desc:
                current["description"] = desc
        elif name == "RRULE":
            current["repeats"] = True
        elif name == "STATUS":
            current["status"] = value.strip().lower()

    for ev in events:
        ev.setdefault("title", "(untitled)")
        ev["calendar"] = calendar_name or source_name or "calendar"
    events.sort(key=lambda e: e["start"].isoformat())
    return events


def load_calendars(paths, verbose=True):
    """Load events from .ics files/folders/zips (Google Calendar exports are
    zips of .ics — handled directly)."""
    events = []
    for path in paths:
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            for ics in sorted(glob.glob(os.path.join(path, "*.ics"))):
                events.extend(load_calendars([ics], verbose=verbose))
            continue
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as zf:
                    for member in zf.namelist():
                        if member.lower().endswith(".ics"):
                            text = zf.read(member).decode("utf-8", "replace")
                            got = parse_ics(text, os.path.basename(member))
                            events.extend(got)
                            if verbose:
                                print(f"   📅 {member}: {len(got)} events")
            else:
                with open(path, encoding="utf-8", errors="replace") as f:
                    got = parse_ics(f.read(), os.path.basename(path))
                events.extend(got)
                if verbose:
                    print(f"   📅 {os.path.basename(path)}: {len(got)} events")
        except (OSError, zipfile.BadZipFile) as e:
            print(f"   ⚠️  Couldn't read {path}: {e}")
    events.sort(key=lambda e: e["start"].isoformat())
    return events


# ---------------------------------------------------------------------------
# Contacts (.vcf) — iCloud, Google Contacts, Android
# ---------------------------------------------------------------------------

def _vcf_value(prop, value):
    """Decode a vCard property value, handling old-style quoted-printable."""
    if "QUOTED-PRINTABLE" in prop.upper():
        try:
            value = quopri.decodestring(value.encode()).decode("utf-8", "replace")
        except Exception:
            pass
    return _ics_unescape(value.strip())


def parse_vcf(text):
    """Parse vCard 2.1/3.0/4.0 text into a list of contact dicts."""
    contacts = []
    current = None
    for line in _unfold_ics(text):  # same folding rules as ICS
        if ":" not in line:
            continue
        prop, value = line.split(":", 1)
        name = prop.split(";", 1)[0].upper()
        if name == "BEGIN" and value.strip().upper() == "VCARD":
            current = {"phones": [], "emails": []}
            continue
        if name == "END" and value.strip().upper() == "VCARD":
            if current is not None:
                if not current.get("name"):
                    # Fall back to the structured N field: Last;First;...
                    n = current.pop("_n", "")
                    parts = [p for p in n.split(";") if p.strip()]
                    if parts:
                        current["name"] = " ".join(reversed(parts[:2])).strip()
                if current.get("name") or current["phones"] or current["emails"]:
                    current.setdefault("name", "(no name)")
                    contacts.append(current)
            current = None
            continue
        if current is None:
            continue
        val = _vcf_value(prop, value)
        if not val:
            continue
        if name == "FN":
            current["name"] = val
        elif name == "N":
            current["_n"] = val
        elif name == "TEL":
            if val not in current["phones"]:
                current["phones"].append(val)
        elif name == "EMAIL":
            if val not in current["emails"]:
                current["emails"].append(val)
        elif name == "BDAY":
            current["birthday"] = val
        elif name == "ORG":
            current["org"] = val.rstrip(";")
        elif name == "NOTE":
            current["note"] = val
    for c in contacts:
        c.pop("_n", None)
    contacts.sort(key=lambda c: c.get("name", "").lower())
    return contacts


def load_contacts(paths, verbose=True):
    contacts = []
    for path in paths:
        path = os.path.expanduser(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                got = parse_vcf(f.read())
            contacts.extend(got)
            if verbose:
                print(f"   👤 {os.path.basename(path)}: {len(got)} contacts")
        except OSError as e:
            print(f"   ⚠️  Couldn't read {path}: {e}")
    contacts.sort(key=lambda c: c.get("name", "").lower())
    return contacts


# ---------------------------------------------------------------------------
# Call logs (SMS Backup & Restore XML)
# ---------------------------------------------------------------------------

def load_calls(path, verbose=True):
    path = os.path.expanduser(path)
    calls = []
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        print(f"   ⚠️  Couldn't read {path}: {e}")
        return calls
    for call in root.findall(".//call"):
        date_ms = call.get("date")
        try:
            when = datetime.fromtimestamp(int(date_ms) / 1000.0)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        try:
            duration = int(call.get("duration", 0))
        except ValueError:
            duration = 0
        try:
            ctype = int(call.get("type", 0))
        except ValueError:
            ctype = 0
        name = call.get("contact_name")
        if name in (None, "", "(Unknown)"):
            name = call.get("number") or "Unknown"
        calls.append({
            "timestamp": when,
            "who": name,
            "number": call.get("number") or "",
            "type": CALL_TYPES.get(ctype, "call"),
            "duration_seconds": duration,
        })
    calls.sort(key=lambda c: c["timestamp"])
    if verbose:
        print(f"   📞 {os.path.basename(path)}: {len(calls)} calls")
    return calls


# ---------------------------------------------------------------------------
# Messages (an existing Desmond export)
# ---------------------------------------------------------------------------

def load_messages(path, verbose=True):
    path = os.path.expanduser(path)
    json_path = os.path.join(path, "messages.json") if os.path.isdir(path) else path
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    if verbose:
        print(f"   💬 {json_path}: {data.get('total_messages', len(data.get('messages', []))):,} messages")
    return data


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

def discover_sources():
    """Look in Downloads/Documents/Desktop for usable files. Returns
    {source: [paths]}."""
    found = {"messages": [], "calendar": [], "contacts": [], "calls": []}
    for d in MESSAGE_EXPORT_DIRS:
        if os.path.exists(os.path.join(d, "messages.json")):
            found["messages"].append(d)
    for d in SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "*.ics"))):
            found["calendar"].append(p)
        for p in sorted(glob.glob(os.path.join(d, "*.zip"))):
            base = os.path.basename(p).lower()
            if "calendar" in base or "takeout" in base or base.endswith(".ics.zip"):
                found["calendar"].append(p)
        for p in sorted(glob.glob(os.path.join(d, "*.vcf"))):
            found["contacts"].append(p)
        for p in sorted(glob.glob(os.path.join(d, "calls*.xml"))):
            found["calls"].append(p)
    return found


# ---------------------------------------------------------------------------
# The consolidated .md
# ---------------------------------------------------------------------------

def _fmt_duration(seconds):
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def build_archive_md(sections, messages_full=False):
    """sections: dict possibly containing 'messages' (export dict),
    'calendar' (event list), 'contacts' (contact list), 'calls' (call list).
    Returns the consolidated markdown text."""
    now = datetime.now()
    used = [s for s in ALL_SOURCES if sections.get(s) is not None]
    lines = ["# Personal Archive", ""]
    lines.append(f"*Consolidated by Desmond on {now.strftime('%Y-%m-%d %H:%M')}. "
                 f"Sources: {', '.join(used) or 'none'}. Everything below came "
                 "off your own devices and was processed locally.*")
    lines.append("")
    lines.append("> ⚠️ This file consolidates personal information in one "
                 "place. Store it somewhere you trust.")
    lines.append("")

    # ---- Contacts --------------------------------------------------------
    contacts = sections.get("contacts")
    if contacts is not None:
        lines.append(f"## 👤 Contacts ({len(contacts):,})")
        lines.append("")
        for c in contacts:
            bits = []
            if c.get("phones"):
                bits.append(", ".join(c["phones"]))
            if c.get("emails"):
                bits.append(", ".join(c["emails"]))
            if c.get("org"):
                bits.append(c["org"])
            if c.get("birthday"):
                bits.append(f"🎂 {c['birthday']}")
            detail = " — " + " · ".join(bits) if bits else ""
            lines.append(f"- **{c.get('name', '(no name)')}**{detail}")
            if c.get("note"):
                lines.append(f"  - note: {c['note']}")
        lines.append("")

    # ---- Calendar --------------------------------------------------------
    events = sections.get("calendar")
    if events is not None:
        years = sorted({e["start"].year for e in events})
        span = f", {years[0]}–{years[-1]}" if years else ""
        lines.append(f"## 📅 Calendar ({len(events):,} events{span})")
        lines.append("")
        by_month = defaultdict(list)
        for e in events:
            by_month[e["start"].strftime("%Y-%m")].append(e)
        for month in sorted(by_month):
            lines.append(f"### {month}")
            lines.append("")
            for e in by_month[month]:
                when = (e["start"].strftime("%Y-%m-%d (all day)") if e.get("all_day")
                        else e["start"].strftime("%Y-%m-%d %H:%M"))
                extra = []
                if e.get("location"):
                    extra.append(f"@ {e['location']}")
                if e.get("repeats"):
                    extra.append("(repeats)")
                if e.get("status") == "cancelled":
                    extra.append("(cancelled)")
                suffix = " " + " ".join(extra) if extra else ""
                lines.append(f"- **{when}** — {e['title']}{suffix}")
            lines.append("")

    # ---- Calls -----------------------------------------------------------
    calls = sections.get("calls")
    if calls is not None:
        total_secs = sum(c["duration_seconds"] for c in calls)
        lines.append(f"## 📞 Calls ({len(calls):,}, "
                     f"{_fmt_duration(total_secs)} total)")
        lines.append("")
        talk = defaultdict(int)
        count = defaultdict(int)
        for c in calls:
            talk[c["who"]] += c["duration_seconds"]
            count[c["who"]] += 1
        top = sorted(count, key=lambda w: -count[w])[:10]
        if top:
            lines.append("**Most called:** " + ", ".join(
                f"{w} ({count[w]}×, {_fmt_duration(talk[w])})" for w in top))
            lines.append("")
        by_month = defaultdict(list)
        for c in calls:
            by_month[c["timestamp"].strftime("%Y-%m")].append(c)
        for month in sorted(by_month):
            lines.append(f"### {month}")
            lines.append("")
            for c in by_month[month]:
                dur = (f", {_fmt_duration(c['duration_seconds'])}"
                       if c["duration_seconds"] else "")
                lines.append(f"- {c['timestamp'].strftime('%Y-%m-%d %H:%M')} — "
                             f"{c['type']} {c['who']}{dur}")
            lines.append("")

    # ---- Messages --------------------------------------------------------
    export = sections.get("messages")
    if export is not None:
        msgs = export.get("messages", [])
        convs = export.get("conversations", [])
        lines.append(f"## 💬 Messages ({len(msgs):,} across "
                     f"{len(convs):,} conversations)")
        lines.append("")
        if messages_full:
            by_conv = defaultdict(list)
            for m in msgs:
                by_conv[m.get("conversation") or "Unknown"].append(m)
            for conv in sorted(by_conv, key=lambda c: -len(by_conv[c])):
                lines.append(f"### {conv} ({len(by_conv[conv]):,} messages)")
                lines.append("")
                for m in by_conv[conv]:
                    lines.append(f"- **{m.get('date', '')} {m.get('time', '')[:5]} "
                                 f"{m.get('sender', '')}:** {m.get('text') or ''}")
                lines.append("")
        else:
            lines.append("*Digest view (run with `--messages-full` to inline "
                         "every message — can be a very large file).*")
            lines.append("")
            for c in sorted(convs, key=lambda c: -c.get("message_count", 0)):
                first = (c.get("first_message") or "")[:10]
                last = (c.get("last_message") or "")[:10]
                lines.append(f"- **{c.get('name')}** — "
                             f"{c.get('message_count', 0):,} messages, "
                             f"{first} → {last}")
            lines.append("")

    lines.append("---")
    lines.append('*Generated by Desmond consolidate mode. '
                 '"See you in another life, brother."*')
    return "\n".join(lines) + "\n"


def _jsonable(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {type(obj)}")


def consolidate(sources, calendar_paths=None, contacts_paths=None,
                calls_path=None, messages_path=None, out_path=DEFAULT_OUT,
                messages_full=False, write_json=False, verbose=True):
    """Build the consolidated archive. `sources` is a list drawn from
    ALL_SOURCES. Returns {"out_path": ..., "counts": {...}}."""
    sections = {}
    counts = {}
    discovered = discover_sources()

    if "messages" in sources:
        path = messages_path or (discovered["messages"][0]
                                 if discovered["messages"] else None)
        if path:
            sections["messages"] = load_messages(path, verbose=verbose)
            counts["messages"] = len(sections["messages"].get("messages", []))
        else:
            print("   ⚠️  No Desmond message export found — run "
                  "desmond_export.py (Mac) or one of the exporters first, "
                  "or pass --messages DIR. Skipping messages.")

    if "calendar" in sources:
        paths = calendar_paths or discovered["calendar"]
        if paths:
            sections["calendar"] = load_calendars(paths, verbose=verbose)
            counts["calendar"] = len(sections["calendar"])
        else:
            print("   ⚠️  No .ics calendar files found in Downloads/Documents/"
                  "Desktop. Export from Google Calendar (Settings → Import & "
                  "export → Export), Outlook (Settings → Export calendar), or "
                  "Apple Calendar (File → Export), then re-run or pass "
                  "--calendar PATH. Skipping calendar.")

    if "contacts" in sources:
        paths = contacts_paths or discovered["contacts"]
        if paths:
            sections["contacts"] = load_contacts(paths, verbose=verbose)
            counts["contacts"] = len(sections["contacts"])
        else:
            print("   ⚠️  No .vcf contact files found. Export from iCloud "
                  "(icloud.com → Contacts → Export vCard) or Google Contacts "
                  "(Export → vCard), then re-run or pass --contacts PATH. "
                  "Skipping contacts.")

    if "calls" in sources:
        path = calls_path or (discovered["calls"][0]
                              if discovered["calls"] else None)
        if path:
            sections["calls"] = load_calls(path, verbose=verbose)
            counts["calls"] = len(sections["calls"])
        else:
            print("   ⚠️  No calls*.xml found (SMS Backup & Restore with "
                  "'Call Logs' checked). Skipping calls.")

    if not sections:
        print("\n❌ Nothing to consolidate — no usable sources.")
        return None

    out_path = os.path.expanduser(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    md = build_archive_md(sections, messages_full=messages_full)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

    json_path = None
    if write_json:
        json_path = os.path.splitext(out_path)[0] + ".json"
        payload = {"generated": datetime.now().isoformat(), "counts": counts}
        for key, val in sections.items():
            payload[key] = val
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=_jsonable,
                      ensure_ascii=False)

    if verbose:
        print(f"\n✅ Consolidated archive → {out_path}")
        for src in ALL_SOURCES:
            if src in counts:
                print(f"   {src}: {counts[src]:,}")
        if json_path:
            print(f"   JSON copy → {json_path}")
    return {"out_path": out_path, "json_path": json_path, "counts": counts}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _pick_sources_interactive():
    discovered = discover_sources()
    print("\nWhich data should go into the archive?")
    print()
    for i, src in enumerate(ALL_SOURCES, 1):
        found = discovered[src]
        hint = (f"found: {os.path.basename(found[0])}"
                + (f" (+{len(found) - 1} more)" if len(found) > 1 else "")
                if found else "nothing auto-detected yet")
        label = {"messages": "Messages (your Desmond export)",
                 "calendar": "Calendar (.ics — Google / Outlook / Apple)",
                 "contacts": "Contacts (.vcf)",
                 "calls": "Call logs (Android backup XML)"}[src]
        print(f"  {i}. {label} — {hint}")
    print()
    answer = input("Pick numbers (e.g. 1,2), or press Enter for ALL: ").strip()
    if not answer or answer.lower() == "all":
        return list(ALL_SOURCES)
    picked = []
    for part in answer.replace(" ", "").split(","):
        if part.isdigit() and 1 <= int(part) <= len(ALL_SOURCES):
            picked.append(ALL_SOURCES[int(part) - 1])
        elif part in ALL_SOURCES:
            picked.append(part)
    return picked or list(ALL_SOURCES)


def main():
    parser = argparse.ArgumentParser(
        description="OPTIONAL consolidate mode: build one Markdown archive "
                    "from your messages, calendar, contacts, and calls.")
    parser.add_argument("--sources",
                        help="comma-separated: all, or any of "
                             "messages,calendar,contacts,calls "
                             "(omit for the interactive picker)")
    parser.add_argument("--calendar", action="append", default=None,
                        metavar="PATH",
                        help=".ics file, folder of .ics files, or the .zip "
                             "Google Calendar exports (repeatable)")
    parser.add_argument("--contacts", action="append", default=None,
                        metavar="PATH", help=".vcf file (repeatable)")
    parser.add_argument("--calls", default=None, metavar="PATH",
                        help="calls .xml from SMS Backup & Restore")
    parser.add_argument("--messages", default=None, metavar="DIR",
                        help="Desmond export folder (with messages.json)")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"output .md path (default: {DEFAULT_OUT})")
    parser.add_argument("--messages-full", action="store_true",
                        help="inline every message instead of the digest")
    parser.add_argument("--json", action="store_true",
                        help="also write personal_archive.json")
    args = parser.parse_args()

    print("🗂  Desmond Consolidate (optional mode)")
    print("   One Markdown file out of your personal data — processed "
          "locally, uploaded nowhere.")

    if args.sources:
        wanted = args.sources.replace(" ", "").lower()
        sources = (list(ALL_SOURCES) if wanted == "all"
                   else [s for s in wanted.split(",") if s in ALL_SOURCES])
        unknown = [s for s in wanted.split(",")
                   if s and s not in ALL_SOURCES and s != "all"]
        if unknown:
            parser.error(f"unknown source(s): {', '.join(unknown)} "
                         f"(choose from: all, {', '.join(ALL_SOURCES)})")
    else:
        sources = _pick_sources_interactive()

    print(f"\n   Sources: {', '.join(sources)}")
    result = consolidate(
        sources,
        calendar_paths=args.calendar,
        contacts_paths=args.contacts,
        calls_path=args.calls,
        messages_path=args.messages,
        out_path=args.out,
        messages_full=args.messages_full,
        write_json=args.json,
    )
    if result is None:
        sys.exit(1)
    print('\n"See you in another life, brother."')


if __name__ == "__main__":
    main()
