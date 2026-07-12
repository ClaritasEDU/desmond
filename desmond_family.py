#!/usr/bin/env python3
"""
Desmond Family Federation — merge two parents' messages AND calendars, then
surface the COVERAGE GAPS: what one parent knows that the other doesn't.

The problem this solves
-----------------------
Family coordination breaks in a very specific way: the dentist texts Mom
about Emma's appointment and Dad never hears about it. The school's calendar
invite lands on Dad's calendar but not Mom's. Neither parent did anything
wrong — the information just never made it to both of them.

This module federates both parents' data (with consent, like
desmond_federate) and then does the part no single-person export can do:
it DIFFS the two views and reports the blind spots —

    • calendar events that exist on only one parent's calendar
    • incoming messages only one parent received (in threads both have)
    • whole threads (a dentist, a coach, a school) that only ever
      talk to one parent

Consent comes first
-------------------
Same rule as desmond_federate: each person exports/connects their OWN data
and explicitly agrees to combine it. The CLI prompts; apps must pass
`consented=True` plus their own consent trail. Nothing is read that wasn't
handed to this tool.

Command line (one command, iPhone-first)
----------------------------------------
    cd ~/desmond
    python3 desmond_family.py \\
        "Chris=~/Downloads/iMessages_Export" \\
        "Kate=/path/to/kates_export" \\
        --calendar "Chris=https://calendar.google.com/calendar/ical/…/basic.ics" \\
        --calendar "Kate=webcal://outlook.office365.com/owa/calendar/…/calendar.ics"

    Messages come from each parent's finished Desmond export
    (imessage_exporter.py / desmond_export.py on Mac,
    imessage_exporter_windows.py from an iPhone backup on Windows).
    Calendars come straight from each parent's private iCal link — Google
    Calendar's "Secret address in iCal format", Outlook/365's published ICS
    link, or an iCloud calendar's public webcal:// link. Exported .ics
    files work too (pass a path instead of a URL).

    Either half is optional: calendars only, messages only, or both.

    Flags:
      --calendar NAME=URL_OR_PATH   a parent's calendar feed/file (repeatable)
      --since YYYY-MM-DD   only report gaps from this date on
                           (default: the last 30 days + everything upcoming)
      --all                report gaps across all history (can be noisy)
      --keyword WORD       only report gaps mentioning WORD (repeatable) —
                           e.g. --keyword dentist --keyword "Ms. Alvarez"
      --same-thread "A=B"  tell the differ that thread A in parent 1's export
                           is thread B in parent 2's (when contact names differ)
      --consented          skip the interactive consent prompt
      --out DIR            output folder
                           (default ~/Downloads/Desmond_Family_Archive)

What you get
------------
    Desmond_Family_Archive/
    ├── family.json          # the federated payload (desmond-family/1)
    ├── FAMILY_GAPS.md       # ⭐ the point: what only one of you has
    ├── FAMILY_SUMMARY.md    # counts per person, shared vs. gapped
    └── shared/              # the couple's own thread, merged (if messages
                             #   were provided — same as desmond_federate)

Using this from another app (e.g. ParentPoint)
----------------------------------------------
Everything is importable and the whole federation+diff is a PURE in-memory
function — no filesystem, no printing — so a web app can run it on uploads
or on calendar feeds it fetched itself:

    from desmond_family import parse_calendar, federate_family_data
    from desmond_federate import parse_export

    result = federate_family_data(
        message_exports=[("Chris", parse_export(chris_upload)),
                         ("Kate",  parse_export(kate_upload))],
        calendar_exports=[("Chris", parse_calendar(chris_ics_text)),
                          ("Kate",  parse_calendar(kate_ics_text))],
        consented=True,
        consent_records=[{"participant": "Chris", "agreed_at": "…"},
                         {"participant": "Kate",  "agreed_at": "…"}],
        since="2026-06-01")

    result["family"]["gaps"]     # structured gap lists (calendar/messages/threads)
    result["gaps_md"]            # FAMILY_GAPS.md as a string, ready to render
    result["summary_md"]         # FAMILY_SUMMARY.md as a string

`parse_calendar()` accepts raw ICS text/bytes (what a feed URL returns), a
JSON list of event dicts, or an already-parsed list — whatever transport the
app used. Events and gaps are plain JSON-serializable dicts.

No files needed: the web wizard
-------------------------------
`python3 desmond_family_web.py` wraps this module in a local browser
wizard: plug each phone into the computer (iPhone backup read in place,
Android read live over USB via android_adb_exporter), connect calendars
with Google/Microsoft sign-in (desmond_calendar_auth), and the gap report
renders on the page — nothing exported, nothing downloaded, nothing
written to disk unless you click Save. Mixed households (one parent on
iPhone, one on Android) federate exactly the same, because every source
lands in the standard export shape (see desmond_sources).

Platform notes
--------------
iPhone:
  • Messages: this Mac's own history reads directly; any other iPhone
    plugs in and its local Finder/iTunes backup is read in place.
    Appointment reminders from dentists/schools almost always arrive as
    SMS, so this captures them.
  • iOS offers NO API to read other apps' push notifications, so texts +
    calendar are the two federable signals on iPhone. That's fine: they're
    where appointment/school traffic actually lives.

Android (built: android_adb_exporter.py):
  • SMS/MMS read live off the plugged-in phone over USB (USB debugging),
    contact names resolved from the phone's own address book; SMS Backup &
    Restore XML still works as the no-cable path.
  • Caveat: RCS chats ("chat features") can't be read without root by ANY
    method — automated reminders are SMS and ARE captured.
  • Android's remaining superpower (future): NotificationListenerService
    can capture OTHER apps' notifications (school apps, pharmacy apps)
    with the user's permission — a signal iPhone can't give us. A future
    android_notification_exporter.py should emit the standard export shape
    ({"messages": [...]}, conversation = app name) so this module
    federates it with zero changes.
  • Call logs are already exported on Android (calls.xml) — a future
    "missed the school's call" gap could reuse detect_message_gaps.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

from desmond_federate import (ConsentError, detect_shared_threads,
                              federate_data, load_export, parse_export,
                              _norm_name)
from desmond_consolidate import (fetch_calendar_feed, normalize_feed_url,
                                 parse_ics, _feed_label)

DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Downloads/Desmond_Family_Archive")

# Two phones rarely log the same SMS at the same second — carriers deliver
# with a lag. Treat identical incoming text within this window as "received".
MESSAGE_MATCH_WINDOW_SECONDS = 300


# ---------------------------------------------------------------------------
# Calendar input — accept whatever transport delivered it
# ---------------------------------------------------------------------------

def parse_calendar(data, source="upload"):
    """Normalize calendar data an app received ANY way — raw ICS text/bytes
    (what fetching a feed URL returns), a JSON string, a dict with an
    "events" list, or a list of event dicts. Returns a JSON-serializable
    list of event dicts sorted by start:

        {"title", "start" (ISO), "end" (ISO), "all_day", "location",
         "description", "calendar", "status", "repeats"}
    """
    if isinstance(data, (bytes, bytearray)):
        # utf-8-sig: Outlook-exported ICS files often start with a BOM
        data = bytes(data).decode("utf-8-sig", "replace")
    if isinstance(data, str):
        stripped = data.lstrip("﻿ \t\r\n")
        if stripped[:15].upper().startswith("BEGIN:VCALENDAR"):
            return _normalize_events(parse_ics(data, source), source)
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"{source}: neither ICS nor valid JSON ({e})")
    if isinstance(data, dict):
        data = data.get("events")
    if not isinstance(data, list):
        raise ValueError(f"{source} doesn't look like calendar data "
                         "(want ICS text or a list of events).")
    return _normalize_events(data, source)


def _normalize_events(events, source):
    """Coerce events to the JSON-safe shape above; drop ones with no start."""
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start = _iso(ev.get("start"))
        if not start:
            continue
        rec = {
            "title": str(ev.get("title") or "(untitled)"),
            "start": start,
            "all_day": bool(ev.get("all_day")),
            "calendar": str(ev.get("calendar") or source or "calendar"),
        }
        end = _iso(ev.get("end"))
        if end:
            rec["end"] = end
        for key in ("location", "description", "status"):
            if ev.get(key):
                rec[key] = str(ev[key])
        if ev.get("repeats"):
            rec["repeats"] = True
        out.append(rec)
    out.sort(key=lambda e: e["start"])
    return out


def _iso(value):
    """datetime -> ISO string; ISO-ish string -> itself; anything else -> None."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and len(value) >= 8:
        return value
    return None


def load_calendar_source(spec, verbose=True):
    """Load one parent's calendar from a URL (Google secret iCal / Outlook
    published ICS / iCloud webcal — fetched live) or a local .ics file or
    folder of .ics files. Returns a normalized event list."""
    url = normalize_feed_url(spec)
    if url:
        label = _feed_label(url)
        text = fetch_calendar_feed(url)
        events = parse_calendar(text, label)
        if verbose:
            print(f"   📅 {label} (online): {len(events)} events")
        return events
    path = os.path.expanduser(spec)
    if os.path.isdir(path):
        events = []
        for name in sorted(os.listdir(path)):
            if name.lower().endswith(".ics"):
                events.extend(load_calendar_source(os.path.join(path, name),
                                                   verbose=verbose))
        events.sort(key=lambda e: e["start"])
        return events
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        events = parse_calendar(f.read(), os.path.basename(path))
    if verbose:
        print(f"   📅 {os.path.basename(path)}: {len(events)} events")
    return events


# ---------------------------------------------------------------------------
# Calendar federation — merge both parents' events, find the gaps
# ---------------------------------------------------------------------------

def _norm_title(title):
    """Loose title normalization: lowercase, alnum+spaces, collapsed."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " "
                      for ch in (title or "").lower())
    return " ".join(cleaned.split())


def _event_key(ev):
    """An event's identity across two calendars: same start (to the minute;
    to the day for all-day events) + same normalized title."""
    start = ev.get("start", "")
    when = start[:10] if ev.get("all_day") else start[:16]
    return (when, _norm_title(ev.get("title")))


def federate_calendars(calendar_exports):
    """Merge each parent's normalized event list into one family calendar.

    calendar_exports: list of (parent_name, events) — events from
    parse_calendar()/load_calendar_source().

    Every merged event carries:
        owner       whose calendar contributed it first
        seen_by     every parent whose calendar has it
        shared      True when every parent has it
        missing_for parents whose calendar does NOT have it (the gap)

    Two passes: exact (minute + title), then a loose same-day + same-title
    pass so the identical appointment entered at 2:00 PM on one calendar and
    14:00 UTC on the other doesn't false-alarm as a gap (loose matches are
    flagged with loose_match: true — worth a glance, the times differ).
    """
    all_names = []
    for name, _ in calendar_exports:
        if name not in all_names:    # a parent may contribute several feeds
            all_names.append(name)
    merged = {}
    order = []
    for owner, events in calendar_exports:
        for ev in events:
            key = _event_key(ev)
            existing = merged.get(key)
            if existing is not None:
                if owner not in existing["seen_by"]:
                    existing["seen_by"].append(owner)
                continue
            rec = dict(ev)
            rec["owner"] = owner
            rec["seen_by"] = [owner]
            merged[key] = rec
            order.append(rec)

    # Loose pass: same day + same title, different minute.
    by_day_title = {}
    for rec in order:
        day_key = (rec["start"][:10], _norm_title(rec["title"]))
        by_day_title.setdefault(day_key, []).append(rec)
    for recs in by_day_title.values():
        if len(recs) < 2:
            continue
        keep = recs[0]
        for other in recs[1:]:
            if set(other["seen_by"]) - set(keep["seen_by"]):
                for name in other["seen_by"]:
                    if name not in keep["seen_by"]:
                        keep["seen_by"].append(name)
                keep["loose_match"] = True
                other["_absorbed"] = True

    order = [r for r in order if not r.pop("_absorbed", False)]
    for rec in order:
        rec["shared"] = len(set(rec["seen_by"])) == len(all_names)
        rec["missing_for"] = [n for n in all_names if n not in rec["seen_by"]]
    order.sort(key=lambda e: e["start"])
    return order


# ---------------------------------------------------------------------------
# Message gaps — notifications only one parent received
# ---------------------------------------------------------------------------

def _epoch(ts):
    try:
        return datetime.fromisoformat(ts[:19]).timestamp()
    except (ValueError, TypeError):
        return None


def _norm_address(value):
    """A phone number/email in comparable form, or None when there's
    nothing identifying. Phones compare on the LAST 7 DIGITS so the same
    number saved as '555-0142' on one phone and '+1 512 555 0142' on the
    other still matches."""
    s = str(value or "").strip().lower()
    if not s:
        return None
    if "@" in s:
        return s
    digits = re.sub(r"\D", "", s)
    return digits[-7:] if len(digits) >= 7 else None


def detect_message_gaps(message_exports, explicit_same=None,
                        explicit_shared=None):
    """Diff each parent's incoming messages against the other's.

    message_exports: list of (parent_name, export_dict) — the same input
    desmond_federate takes. The couple's own thread with each other is
    excluded (that's federation's job, not a gap).

    explicit_same: optional list of (conv_in_first, conv_in_second) pairs
    naming threads that are the same counterpart under different contact
    names (like --shared in desmond_federate).

    explicit_shared: the couple's own thread named explicitly (the same
    pairs passed to federation's --shared) — those threads are excluded
    from gap detection exactly like auto-detected ones.

    Returns (message_gaps, thread_gaps):

    message_gaps — threads BOTH parents have, but a message only one got:
        {"conversation", "owner", "missing_for", "timestamp", "date",
         "time", "sender", "text"}
    thread_gaps — threads only ONE parent has at all (the dentist who only
    ever texts Mom):
        {"conversation", "owner", "missing_for", "type",
         "incoming_count", "first_message", "last_message"}

    Matching is tolerant: identical incoming text within
    MESSAGE_MATCH_WINDOW_SECONDS counts as received on both phones (two
    carriers rarely stamp the same SMS at the same second).
    """
    names = list(dict.fromkeys(name for name, _ in message_exports))

    # The couple's mutual thread(s) are not "gaps" — exclude them,
    # whether auto-detected or named explicitly via --shared.
    couple = set()
    for t in detect_shared_threads(message_exports,
                                   explicit=explicit_shared):
        name_a, name_b = t["pair"]
        couple.add((name_a, _norm_name(t["conv_a"])))
        couple.add((name_b, _norm_name(t["conv_b"])))

    # Explicit same-thread mapping: normalize both sides to a joint key.
    alias = {}
    if explicit_same and len(message_exports) >= 2:
        for conv_a, conv_b in explicit_same:
            joint = _norm_name(conv_a) or _norm_name(conv_b)
            alias[(names[0], _norm_name(conv_a))] = joint
            alias[(names[1], _norm_name(conv_b))] = joint

    # Per parent: joint thread key -> {"name", "type", "incoming": [msgs]}
    threads = {name: {} for name in names}
    for owner, export in message_exports:
        for msg in export.get("messages", []):
            conv = msg.get("conversation") or "Unknown"
            norm = _norm_name(conv)
            if not norm or norm == "unknown":
                continue    # unidentifiable senders can't be diffed honestly
            if (owner, norm) in couple:
                continue
            key = alias.get((owner, norm), norm)
            rec = threads[owner].setdefault(key, {
                "name": conv,
                "type": msg.get("conversation_type", "direct"),
                "incoming": [],
                "addresses": set(),
            })
            addr = _norm_address(msg.get("address"))
            if addr:
                rec["addresses"].add(addr)
            if not msg.get("is_from_me"):
                rec["incoming"].append(msg)

    message_gaps = []
    thread_gaps = []
    seen_thread_keys = set()
    for owner in names:
        others = [n for n in names if n != owner]
        for key, rec in threads[owner].items():
            have = [o for o in others if key in threads[o]]
            lack = [o for o in others if key not in threads[o]]
            if not rec["incoming"]:
                continue    # nobody ever texted in; nothing was missed
            if lack and key not in seen_thread_keys:
                # Some parent doesn't have this thread at all — a thread
                # gap for them, even when other parents DO share it
                # (matters with 3+ participants).
                seen_thread_keys.add(key)
                stamps = sorted((m.get("timestamp") or "")
                                for m in rec["incoming"])
                thread_gaps.append({
                    "conversation": rec["name"],
                    "owner": owner,
                    "missing_for": lack,
                    "type": rec["type"],
                    "incoming_count": len(rec["incoming"]),
                    "first_message": stamps[0],
                    "last_message": stamps[-1],
                })
            if not have:
                continue
            # Same NAME is not the same PERSON: when both sides know the
            # counterpart's number/email and they don't overlap, it's two
            # different people (each parent's own "Mom") — diffing them
            # would fabricate gaps, so those matches are dropped.
            verified = [o for o in have
                        if not (rec["addresses"]
                                and threads[o][key]["addresses"]
                                and rec["addresses"].isdisjoint(
                                    threads[o][key]["addresses"]))]
            if not verified:
                continue
            # Thread matched with at least one other parent: per-message diff.
            for msg in rec["incoming"]:
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                ts = _epoch(msg.get("timestamp", ""))
                missing = [o for o in verified
                           if not _received(threads[o][key]["incoming"], text, ts)]
                if missing:
                    message_gaps.append({
                        "conversation": rec["name"],
                        "owner": owner,
                        "missing_for": missing,
                        "timestamp": msg.get("timestamp") or "",
                        "date": msg.get("date") or "",
                        "time": msg.get("time") or "",
                        "sender": msg.get("sender") or "",
                        "text": text,
                    })

    message_gaps.sort(key=lambda g: g["timestamp"])
    thread_gaps.sort(key=lambda g: g["last_message"], reverse=True)
    return message_gaps, thread_gaps


def _received(incoming, text, ts):
    """Did this parent's thread receive `text` at (roughly) time `ts`?"""
    for msg in incoming:
        if (msg.get("text") or "").strip() != text:
            continue
        other_ts = _epoch(msg.get("timestamp", ""))
        if ts is None or other_ts is None:
            return True
        if abs(other_ts - ts) <= MESSAGE_MATCH_WINDOW_SECONDS:
            return True
    return False


# ---------------------------------------------------------------------------
# Gap filtering
# ---------------------------------------------------------------------------

def _gap_matches(gap, since, keywords):
    when = gap.get("start") or gap.get("timestamp") or gap.get("last_message") or ""
    # A gap with no usable date stays IN scope — dropping it silently would
    # hide real findings whenever `since` is set (which the CLI does by
    # default).
    if since and when and when[:10] < since[:10]:
        return False
    if keywords:
        hay = " ".join(str(gap.get(k, "")) for k in
                       ("title", "text", "conversation", "location",
                        "description", "sender")).lower()
        if not any(kw.lower() in hay for kw in keywords):
            return False
    return True


# ---------------------------------------------------------------------------
# The whole thing, in memory (what an app like ParentPoint calls)
# ---------------------------------------------------------------------------

def federate_family_data(message_exports=None, calendar_exports=None,
                         consented=False, consent_records=None,
                         since=None, keywords=None, explicit_same=None,
                         explicit_shared=None):
    """Federate a family's messages and/or calendars and diff the coverage.
    Pure and in-memory: no filesystem, no printing.

    message_exports:  [(parent_name, export_dict)] — validate uploads with
                      desmond_federate.parse_export() first. Optional.
    calendar_exports: [(parent_name, events)] — normalize with
                      parse_calendar() first. Optional.
    consented:        must be True — every participant agreed. ConsentError
                      otherwise (same contract as desmond_federate).
    consent_records:  the calling app's consent trail, stored verbatim.
    since:            "YYYY-MM-DD" — only report gaps from this date on
                      (None = everything).
    keywords:         only report gaps mentioning one of these strings
                      (None = everything).
    explicit_same:    [(conv_in_first, conv_in_second)] same-thread hints
                      for the message differ.
    explicit_shared:  passed through to desmond_federate for the couple's
                      own thread.

    Returns {"family": payload, "gaps_md": str, "summary_md": str,
             "shared_transcripts": {title: md}}.
    The payload (format desmond-family/1) is JSON-serializable end to end.
    """
    if not consented:
        raise ConsentError(
            "Family federation requires every participant's consent. Pass "
            "consented=True only after each person has agreed to share "
            "their data.")
    message_exports = list(message_exports or [])
    calendar_exports = list(calendar_exports or [])
    if not message_exports and not calendar_exports:
        raise ValueError("Nothing to federate — pass message_exports "
                         "and/or calendar_exports.")
    if len(message_exports) == 1:
        raise ValueError("Message federation needs BOTH parents' exports "
                         "(got one). Add the other export, or leave "
                         "messages out.")
    if len(calendar_exports) == 1:
        raise ValueError("Calendar federation needs BOTH parents' calendars "
                         "(got one). Add the other calendar, or leave "
                         "calendars out.")

    participants = []
    for name, _ in message_exports + calendar_exports:
        if name not in participants:
            participants.append(name)
    if len(participants) < 2:
        raise ValueError("Family federation needs at least two people.")

    # Messages: reuse desmond_federate wholesale, then diff for gaps.
    messages_payload = None
    shared_transcripts = {}
    message_gaps, thread_gaps = [], []
    if message_exports:
        fed = federate_data(message_exports, consented=True,
                            explicit_shared=explicit_shared,
                            consent_records=consent_records)
        messages_payload = fed["federated"]
        shared_transcripts = fed["shared_transcripts"]
        message_gaps, thread_gaps = detect_message_gaps(
            message_exports, explicit_same=explicit_same,
            explicit_shared=explicit_shared)

    # Calendars: federate + gaps are the events with missing_for.
    calendar_events = []
    calendar_gaps = []
    if calendar_exports:
        calendar_events = federate_calendars(calendar_exports)
        calendar_gaps = [dict(ev) for ev in calendar_events
                         if ev["missing_for"]]

    gaps = {
        "calendar": [g for g in calendar_gaps
                     if _gap_matches(g, since, keywords)],
        "messages": [g for g in message_gaps
                     if _gap_matches(g, since, keywords)],
        "threads": [g for g in thread_gaps
                    if _gap_matches(g, since, keywords)],
    }

    consent = {
        "confirmed": True,
        "note": "Each participant connected their own data and agreed to "
                "combine it into this family view.",
    }
    if consent_records:
        consent["records"] = consent_records

    family = {
        "format": "desmond-family/1",
        "federated_by": "desmond_family.py",
        "participants": participants,
        "consent": consent,
        "filters": {"since": since, "keywords": list(keywords or [])},
        "messages": messages_payload,
        "calendar": {
            "total_events": len(calendar_events),
            "shared_events": sum(1 for e in calendar_events if e["shared"]),
            "events": calendar_events,
        } if calendar_exports else None,
        "gap_counts": {k: len(v) for k, v in gaps.items()},
        "gaps": gaps,
    }
    return {
        "family": family,
        "gaps_md": _render_gaps(family),
        "summary_md": _render_summary(family, message_exports,
                                      calendar_exports),
        "shared_transcripts": shared_transcripts,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_when(ev):
    if ev.get("all_day"):
        return f"{ev['start'][:10]} (all day)"
    return f"{ev['start'][:10]} {ev['start'][11:16]}"


def _render_gaps(family):
    """FAMILY_GAPS.md — the deliverable: what only one of you has."""
    gaps = family["gaps"]
    names = family["participants"]
    filters = family.get("filters", {})
    lines = ["# Family Coverage Gaps", ""]
    lines.append("*What one of you has that the other doesn't — the things "
                 "most likely to fall through the cracks.*")
    scope = []
    if filters.get("since"):
        scope.append(f"since {filters['since']}")
    if filters.get("keywords"):
        scope.append("matching: " + ", ".join(filters["keywords"]))
    if scope:
        lines.append("")
        lines.append(f"*Showing gaps {'; '.join(scope)}.*")
    lines.append("")

    total = sum(len(v) for v in gaps.values())
    if total == 0:
        lines.append("✅ **No gaps found.** Everything in scope is on both "
                     "of your phones/calendars.")
        lines.append("")
        return "\n".join(lines) + "\n"

    if family.get("calendar") is not None:
        lines.append(f"## 📅 Calendar — events only on one calendar "
                     f"({len(gaps['calendar'])})")
        lines.append("")
        if not gaps["calendar"]:
            lines.append("✅ None — your calendars agree.")
            lines.append("")
        else:
            for name in names:
                mine = [g for g in gaps["calendar"] if name in g["seen_by"]]
                if not mine:
                    continue
                lines.append(f"### Only on {name}'s calendar")
                lines.append("")
                for g in mine:
                    extra = f" @ {g['location']}" if g.get("location") else ""
                    who = ", ".join(g["missing_for"])
                    lines.append(f"- **{_fmt_when(g)} — {g['title']}**{extra} "
                                 f"→ {who} doesn't have this")
                lines.append("")

    if family.get("messages") is not None:
        lines.append(f"## 💬 Messages — texts only one of you received "
                     f"({len(gaps['messages'])})")
        lines.append("")
        if not gaps["messages"]:
            lines.append("✅ None — shared threads match on both phones.")
            lines.append("")
        else:
            for name in names:
                mine = [g for g in gaps["messages"] if g["owner"] == name]
                if not mine:
                    continue
                lines.append(f"### Only {name} received")
                lines.append("")
                for g in mine:
                    who = ", ".join(g["missing_for"])
                    lines.append(f"- **{g['date']} {g['time'][:5]} · "
                                 f"{g['conversation']}:** {g['text']}  "
                                 f"*({who} has this thread but not this "
                                 "message)*")
                lines.append("")

        lines.append(f"## 📥 Threads only one of you has "
                     f"({len(gaps['threads'])})")
        lines.append("")
        if not gaps["threads"]:
            lines.append("✅ None — every counterpart texts you both.")
            lines.append("")
        else:
            for g in gaps["threads"]:
                who = ", ".join(g["missing_for"])
                lines.append(f"- **{g['conversation']}** only texts "
                             f"{g['owner']} — {g['incoming_count']} incoming "
                             f"message(s), last on {g['last_message'][:10]}. "
                             f"{who} never hears from them.")
            lines.append("")

    lines.append("---")
    lines.append("*A gap isn't an accusation — it's a heads-up. Forward the "
                 "message, share the invite, done.*")
    return "\n".join(lines) + "\n"


def _render_summary(family, message_exports, calendar_exports):
    lines = ["# Family Federation Summary", ""]
    lines.append(f"**Participants:** {', '.join(family['participants'])}")
    counts = family["gap_counts"]
    lines.append(f"**Gaps found:** {counts['calendar']} calendar · "
                 f"{counts['messages']} message · {counts['threads']} thread "
                 "(see FAMILY_GAPS.md)")
    lines.append("")
    if message_exports:
        lines.append("## Messages")
        lines.append("")
        for name, export in message_exports:
            lines.append(f"- **{name}** — "
                         f"{len(export.get('messages', [])):,} messages, "
                         f"{len(export.get('conversations', [])):,} "
                         "conversations contributed")
        merged = family["messages"]
        lines.append(f"- Federated: {merged['total_messages']:,} messages "
                     f"({merged['deduplicated']:,} duplicates merged)")
        lines.append("")
    if calendar_exports:
        cal = family["calendar"]
        lines.append("## Calendar")
        lines.append("")
        for name, events in calendar_exports:
            lines.append(f"- **{name}** — {len(events):,} events contributed")
        lines.append(f"- Federated: {cal['total_events']:,} events, "
                     f"{cal['shared_events']:,} on both calendars")
        lines.append("")
    lines.append("---")
    lines.append("*Each participant connected their own data and agreed to "
                 "combine it. Store this view as carefully as either "
                 "person's data alone.*")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Disk wrapper (what the CLI calls)
# ---------------------------------------------------------------------------

def federate_family(message_exports=None, calendar_exports=None,
                    output_dir=DEFAULT_OUTPUT_DIR, consented=False,
                    consent_records=None, since=None, keywords=None,
                    explicit_same=None, explicit_shared=None, verbose=True):
    """federate_family_data() + write the archive to disk. Returns paths
    and counts."""
    data = federate_family_data(
        message_exports=message_exports, calendar_exports=calendar_exports,
        consented=consented, consent_records=consent_records, since=since,
        keywords=keywords, explicit_same=explicit_same,
        explicit_shared=explicit_shared)
    family = data["family"]

    output_dir = os.path.expanduser(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "family.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(family, f, indent=2, ensure_ascii=False)
    gaps_path = os.path.join(output_dir, "FAMILY_GAPS.md")
    with open(gaps_path, "w", encoding="utf-8") as f:
        f.write(data["gaps_md"])
    summary_path = os.path.join(output_dir, "FAMILY_SUMMARY.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(data["summary_md"])

    shared_paths = []
    if data["shared_transcripts"]:
        shared_dir = os.path.join(output_dir, "shared")
        os.makedirs(shared_dir, exist_ok=True)
        for title, md in data["shared_transcripts"].items():
            safe = "".join(ch if (ch.isalnum() or ch in " _-") else "_"
                           for ch in title.replace(" ", "_")).strip() or "shared"
            path = os.path.join(shared_dir, safe + ".md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            shared_paths.append(path)

    counts = family["gap_counts"]
    if verbose:
        print(f"\n👨‍👩‍👧 Family federation: "
              f"{' + '.join(family['participants'])}")
        print(f"   Gaps: {counts['calendar']} calendar · "
              f"{counts['messages']} message · {counts['threads']} thread")
        print(f"   → {gaps_path}")
    return {
        "output_dir": output_dir,
        "participants": family["participants"],
        "gap_counts": counts,
        "json_path": json_path,
        "gaps_path": gaps_path,
        "summary_path": summary_path,
        "shared_transcripts": shared_paths,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_pair(arg, what):
    if "=" not in arg:
        raise argparse.ArgumentTypeError(
            f"Expected NAME={what} (e.g. \"Chris=…\"), got {arg!r}")
    name, value = arg.split("=", 1)
    name, value = name.strip(), value.strip()
    if not name or not value:
        raise argparse.ArgumentTypeError(
            f"Both a name and a {what.lower()} are needed in {arg!r}")
    return name, value


def main():
    parser = argparse.ArgumentParser(
        description="Federate two parents' messages and calendars, and "
                    "report the coverage gaps (what only one of you has).")
    parser.add_argument("people", nargs="*",
                        type=lambda a: _parse_pair(a, "PATH"),
                        metavar="NAME=EXPORT_PATH",
                        help='each parent\'s Desmond message export, e.g. '
                             '"Chris=~/Downloads/iMessages_Export" '
                             '(optional if you pass --calendar for both)')
    parser.add_argument("--calendar", action="append", default=None,
                        type=lambda a: _parse_pair(a, "URL_OR_PATH"),
                        metavar="NAME=URL_OR_PATH",
                        help="a parent's calendar: private iCal URL (Google "
                             "'Secret address', Outlook published ICS, "
                             "iCloud webcal://) or an exported .ics file "
                             "(repeatable — one per parent)")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                        help="only report gaps from this date on (default: "
                             "last 30 days + everything upcoming)")
    parser.add_argument("--all", action="store_true",
                        help="report gaps across ALL history (can be noisy)")
    parser.add_argument("--keyword", action="append", default=None,
                        metavar="WORD",
                        help="only report gaps mentioning WORD (repeatable)")
    parser.add_argument("--same-thread", action="append", default=None,
                        metavar="A_CONV=B_CONV", dest="same_thread",
                        help="thread A in parent 1's export is thread B in "
                             "parent 2's (when contact names differ)")
    parser.add_argument("--shared", action="append", default=None,
                        metavar="A_CONV=B_CONV",
                        help="explicitly name the couple's own thread "
                             "(same as desmond_federate --shared)")
    parser.add_argument("--consented", action="store_true",
                        help="affirm that every participant has already "
                             "agreed (skips the interactive prompt)")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_DIR,
                        help="output folder (default: "
                             "~/Downloads/Desmond_Family_Archive)")
    args = parser.parse_args()

    if not args.people and not args.calendar:
        parser.error(
            'Nothing to federate. Pass each parent\'s message export '
            '("Chris=~/Downloads/iMessages_Export" "Kate=/path/to/export") '
            'and/or --calendar "NAME=URL_OR_PATH" for each parent.')

    names = []
    for name, _ in list(args.people) + list(args.calendar or []):
        if name not in names:
            names.append(name)
    print("👨‍👩‍👧 Desmond Family Federation")
    print(f"   Combining data from: {' + '.join(names)}")

    if not args.consented:
        print()
        print("   This combines each person's private messages/calendar into")
        print("   ONE shared view that everyone listed can read — including")
        print("   a report of what only one of you has.")
        answer = input(f"   Has each of them ({', '.join(names)}) agreed? "
                       "Type yes to continue: ").strip().lower()
        if answer not in ("y", "yes"):
            print("   Stopped — nothing was combined. Come back once "
                  "everyone is on board.")
            sys.exit(1)

    message_exports = []
    for name, path in args.people:
        try:
            message_exports.append((name, load_export(path)))
        except (FileNotFoundError, ValueError) as e:
            print(f"❌ {e}")
            sys.exit(1)

    calendar_exports = []
    for name, spec in args.calendar or []:
        try:
            calendar_exports.append((name, load_calendar_source(spec)))
        except (OSError, ValueError) as e:
            print(f"❌ Couldn't load {name}'s calendar: {e}")
            print("   (Want the SECRET/published iCal link — see README — "
                  "or an exported .ics file.)")
            sys.exit(1)

    def _split_pairs(items, flag):
        pairs = []
        for pair in items or []:
            if "=" not in pair:
                parser.error(f"{flag} expects A_CONV=B_CONV, got {pair!r}")
            a, b = pair.split("=", 1)
            pairs.append((a.strip(), b.strip()))
        return pairs or None

    since = args.since
    if since is None and not args.all:
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    try:
        federate_family(
            message_exports=message_exports or None,
            calendar_exports=calendar_exports or None,
            output_dir=args.out, consented=True, since=since,
            keywords=args.keyword,
            explicit_same=_split_pairs(args.same_thread, "--same-thread"),
            explicit_shared=_split_pairs(args.shared, "--shared"))
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    print('\n"See you in another life, brother."')


if __name__ == "__main__":
    main()
