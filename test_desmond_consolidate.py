#!/usr/bin/env python3
"""
Synthetic test for desmond_consolidate.py — the optional consolidate mode.

Builds a fake Google Calendar .ics (inside a .zip, the way Google exports it),
an Outlook-style .ics, a vCard file (v3 + a quoted-printable v2.1 entry), an
Android calls.xml, and a mini Desmond message export — then consolidates and
checks the resulting PERSONAL_ARCHIVE.md section by section, plus source
selection (calendar only) and the --messages-full toggle.
"""

import json
import os
import tempfile
import zipfile

import desmond_consolidate as dc

GOOGLE_ICS = "\r\n".join([
    "BEGIN:VCALENDAR",
    "X-WR-CALNAME:Family Calendar",
    "BEGIN:VEVENT",
    "SUMMARY:Dentist appointment\\, kids",
    "DTSTART;TZID=America/Chicago:20260115T140000",
    "DTEND;TZID=America/Chicago:20260115T150000",
    "LOCATION:123 Main St\\, Austin",
    "DESCRIPTION:Bring insurance card",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "SUMMARY:Anniversary — this line is long enough that a calendar app wou",
    " ld fold it onto a continuation line per RFC 5545",
    "DTSTART;VALUE=DATE:20260220",
    "RRULE:FREQ=YEARLY",
    "END:VEVENT",
    "END:VCALENDAR",
])

OUTLOOK_ICS = "\r\n".join([
    "BEGIN:VCALENDAR",
    "BEGIN:VEVENT",
    "SUMMARY:Quarterly review",
    "DTSTART:20260310T090000Z",
    "DTEND:20260310T100000Z",
    "STATUS:CONFIRMED",
    "END:VEVENT",
    "END:VCALENDAR",
])

VCF = "\r\n".join([
    "BEGIN:VCARD",
    "VERSION:3.0",
    "FN:Kate Example",
    "TEL;TYPE=CELL:+1 555 000 0001",
    "EMAIL:kate@example.com",
    "BDAY:1985-06-01",
    "ORG:Example Corp;",
    "END:VCARD",
    "BEGIN:VCARD",
    "VERSION:2.1",
    "N:Pérez;José;;;",
    "TEL;CELL;ENCODING=QUOTED-PRINTABLE:+1555=20000=200002",
    "END:VCARD",
    "BEGIN:VCARD",
    "VERSION:3.0",
    "N:;;;;",
    "END:VCARD",  # nameless, empty card should be dropped
])

CALLS_XML = """<?xml version='1.0' encoding='UTF-8'?>
<calls count="3">
  <call number="+15550000001" duration="125" date="1767290400000"
        type="2" contact_name="Kate Example" />
  <call number="+15550000009" duration="0" date="1767294000000"
        type="3" contact_name="(Unknown)" />
  <call number="+15550000001" duration="3700" date="1767297600000"
        type="1" contact_name="Kate Example" />
</calls>
"""


def main():
    failures = []

    def check(cond, label):
        print(("PASS" if cond else "FAIL") + ": " + label)
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        # Google-style zip of .ics
        gzip_path = os.path.join(tmp, "mycalendar.ics.zip")
        with zipfile.ZipFile(gzip_path, "w") as zf:
            zf.writestr("family@group.calendar.google.com.ics", GOOGLE_ICS)
        outlook_path = os.path.join(tmp, "outlook.ics")
        with open(outlook_path, "w") as f:
            f.write(OUTLOOK_ICS)
        vcf_path = os.path.join(tmp, "contacts.vcf")
        with open(vcf_path, "w") as f:
            f.write(VCF)
        calls_path = os.path.join(tmp, "calls-20260101.xml")
        with open(calls_path, "w") as f:
            f.write(CALLS_XML)

        # Mini Desmond export
        export_dir = os.path.join(tmp, "iMessages_Export")
        os.makedirs(export_dir)
        with open(os.path.join(export_dir, "messages.json"), "w") as f:
            json.dump({
                "total_messages": 2, "total_conversations": 1,
                "conversations": [{"name": "Kate Example", "type": "direct",
                                   "message_count": 2,
                                   "first_message": "2026-01-01T09:00:00",
                                   "last_message": "2026-01-01T09:05:00"}],
                "messages": [
                    {"timestamp": "2026-01-01T09:00:00", "date": "2026-01-01",
                     "time": "09:00:00", "conversation": "Kate Example",
                     "sender": "Me", "is_from_me": True,
                     "message_type": "text", "text": "On my way home"},
                    {"timestamp": "2026-01-01T09:05:00", "date": "2026-01-01",
                     "time": "09:05:00", "conversation": "Kate Example",
                     "sender": "Kate Example", "is_from_me": False,
                     "message_type": "text", "text": "Grab milk please"},
                ],
            }, f)

        # --- parser units ---------------------------------------------------
        events = dc.load_calendars([gzip_path, outlook_path], verbose=False)
        check(len(events) == 3, f"3 events parsed from zip + ics (got {len(events)})")
        dentist = next(e for e in events if "Dentist" in e["title"])
        check(dentist["title"] == "Dentist appointment, kids",
              "escaped comma in SUMMARY unescaped")
        check(dentist["location"] == "123 Main St, Austin",
              "LOCATION with TZID param and escapes parsed")
        check(dentist["start"].strftime("%Y-%m-%d %H:%M") == "2026-01-15 14:00",
              "timed event start parsed")
        ann = next(e for e in events if "Anniversary" in e["title"])
        check(ann["all_day"] is True and ann.get("repeats") is True,
              "all-day + RRULE flagged; folded SUMMARY unfolded")
        check("would fold it onto a continuation line" in ann["title"],
              "RFC 5545 folded line joined correctly")
        check(dentist["calendar"] == "Family Calendar",
              "X-WR-CALNAME used as calendar name")

        contacts = dc.load_contacts([vcf_path], verbose=False)
        check(len(contacts) == 2, f"2 real contacts kept, empty card dropped (got {len(contacts)})")
        kate = next(c for c in contacts if c["name"] == "Kate Example")
        check(kate["phones"] == ["+1 555 000 0001"]
              and kate["emails"] == ["kate@example.com"]
              and kate["birthday"] == "1985-06-01",
              "vCard 3.0 fields parsed (TEL/EMAIL/BDAY)")
        jose = next(c for c in contacts if "Pérez" in c["name"])
        check(jose["name"] == "José Pérez",
              f"name built from N field, order flipped (got {jose['name']!r})")
        check(jose["phones"] == ["+1555 000 0002"],
              f"quoted-printable TEL decoded (got {jose['phones']})")

        calls = dc.load_calls(calls_path, verbose=False)
        check(len(calls) == 3, f"3 calls parsed (got {len(calls)})")
        check(calls[0]["type"] == "outgoing" and calls[1]["type"] == "missed",
              "call types mapped")
        check(calls[1]["who"] == "+15550000009",
              "(Unknown) contact falls back to the number")

        # --- full consolidation ----------------------------------------------
        out_md = os.path.join(tmp, "Archive", "PERSONAL_ARCHIVE.md")
        res = dc.consolidate(
            ["messages", "calendar", "contacts", "calls"],
            calendar_paths=[gzip_path, outlook_path],
            contacts_paths=[vcf_path],
            calls_path=calls_path,
            messages_path=export_dir,
            out_path=out_md, write_json=True, verbose=False)
        check(res is not None and os.path.exists(out_md),
              "PERSONAL_ARCHIVE.md written")
        md = open(out_md, encoding="utf-8").read()
        check("## 👤 Contacts (2)" in md, "contacts section with count")
        check("## 📅 Calendar (3 events" in md, "calendar section with count")
        check("### 2026-01" in md and "**2026-01-15 14:00** — Dentist appointment, kids" in md,
              "calendar grouped by month with event lines")
        check("2026-02-20 (all day)" in md and "(repeats)" in md,
              "all-day + repeating events rendered")
        check("## 📞 Calls (3, 1h 3m" in md, "calls section with total talk time")
        check("Most called:" in md and "Kate Example" in md, "most-called stats")
        check("## 💬 Messages (2 across 1 conversations)" in md,
              "messages section present")
        check("Grab milk please" not in md,
              "digest view does NOT inline message text by default")
        check("--messages-full" in md, "digest view explains how to get full text")
        check("Store it somewhere you trust" in md, "privacy warning included")
        check(os.path.exists(res["json_path"]), "personal_archive.json written with --json")
        payload = json.load(open(res["json_path"]))
        check(payload["counts"] == {"messages": 2, "calendar": 3,
                                    "contacts": 2, "calls": 3},
              f"json counts match (got {payload['counts']})")

        # --- source selection -------------------------------------------------
        cal_only = os.path.join(tmp, "cal_only.md")
        dc.consolidate(["calendar"], calendar_paths=[outlook_path],
                       out_path=cal_only, verbose=False)
        cal_md = open(cal_only, encoding="utf-8").read()
        check("## 📅 Calendar" in cal_md and "Contacts" not in cal_md
              and "Messages" not in cal_md,
              "calendar-only run includes only the calendar section")

        # --- --messages-full ---------------------------------------------------
        full_md_path = os.path.join(tmp, "full.md")
        dc.consolidate(["messages"], messages_path=export_dir,
                       out_path=full_md_path, messages_full=True, verbose=False)
        full_md = open(full_md_path, encoding="utf-8").read()
        check("Grab milk please" in full_md,
              "--messages-full inlines the actual messages")

        # --- graceful empty ----------------------------------------------------
        res_none = dc.consolidate(["calls"], calls_path=None,
                                  out_path=os.path.join(tmp, "none.md"),
                                  verbose=False)
        check(res_none is None or res_none.get("counts", {}).get("calls") is None,
              "missing source is skipped without crashing")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
