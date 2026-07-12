#!/usr/bin/env python3
"""
Synthetic test for desmond_family.py — the ParentPoint scenario.

Two parents (Chris and Kate). The dentist texts only Chris about Emma's
appointment; the school event is only on Kate's calendar; a coach only ever
texts Kate. Family federation must merge both views and report exactly those
gaps — and nothing that ISN'T a gap (their own couple thread, events on both
calendars, texts both received).
"""

import json
import os
import tempfile

import desmond_family as fam
from desmond_federate import ConsentError, parse_export


def msg(ts, conv, sender, from_me, text, conv_type="direct"):
    return {
        "timestamp": ts, "date": ts[:10], "time": ts[11:19],
        "conversation": conv, "conversation_type": conv_type,
        "sender": sender, "is_from_me": from_me,
        "message_type": "text", "text": text,
        "has_attachment": False, "reaction": None,
    }


def export(conversations, messages):
    return parse_export({
        "export_date": "2026-07-12T10:00:00",
        "total_messages": len(messages),
        "conversations": conversations,
        "messages": messages,
    })


ICS_TEMPLATE = """BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:{name}
{events}END:VCALENDAR
"""


def ics_event(title, start, end=None, location=None, all_day=False):
    lines = ["BEGIN:VEVENT"]
    if all_day:
        lines.append(f"DTSTART:{start}")
    else:
        lines.append(f"DTSTART:{start}")
        if end:
            lines.append(f"DTEND:{end}")
    lines.append(f"SUMMARY:{title}")
    if location:
        lines.append(f"LOCATION:{location}")
    lines.append("END:VEVENT")
    return "\n".join(lines) + "\n"


def main():
    failures = []

    def check(cond, label):
        print(("PASS" if cond else "FAIL") + ": " + label)
        if not cond:
            failures.append(label)

    # ---- Chris's phone -----------------------------------------------------
    chris = export(
        [{"name": "Kate ❤️", "type": "direct"},
         {"name": "Smile Dental", "type": "direct"},
         {"name": "Room 12 Parents", "type": "group"}],
        [
            # Couple's own thread — must NOT show up as a gap.
            msg("2026-07-01T09:00:00", "Kate ❤️", "Me", True, "On my way"),
            msg("2026-07-01T09:05:00", "Kate ❤️", "Kate ❤️", False, "Grab milk"),
            # Dentist texts only Chris — the classic ParentPoint gap.
            msg("2026-07-08T10:00:00", "Smile Dental", "Smile Dental", False,
                "Reminder: Emma's cleaning Jul 20 at 2pm"),
            # School group text BOTH parents got (40s carrier lag on Kate's).
            msg("2026-07-09T15:00:00", "Room 12 Parents", "Ms. Alvarez", False,
                "Field trip forms due Friday", conv_type="group"),
        ])

    # ---- Kate's phone ------------------------------------------------------
    kate = export(
        [{"name": "Chris", "type": "direct"},
         {"name": "Coach Dan", "type": "direct"},
         {"name": "Room 12 Parents", "type": "group"}],
        [
            msg("2026-07-01T09:00:00", "Chris", "Chris", False, "On my way"),
            msg("2026-07-01T09:05:00", "Chris", "Me", True, "Grab milk"),
            # Coach only ever texts Kate — a thread gap for Chris.
            msg("2026-07-05T18:00:00", "Coach Dan", "Coach Dan", False,
                "Practice moved to 5pm Tuesday"),
            msg("2026-07-09T15:00:40", "Room 12 Parents", "Ms. Alvarez", False,
                "Field trip forms due Friday", conv_type="group"),
        ])

    # ---- Calendars ---------------------------------------------------------
    # Shared: parent-teacher conference on both (Kate's entered in a
    # different minute style -> loose same-day match, not a gap).
    # Gap: school assembly only on Kate's; soccer photos only on Chris's.
    chris_ics = ICS_TEMPLATE.format(name="Chris", events=(
        ics_event("Parent Teacher Conference", "20260722T140000",
                  location="Room 12") +
        ics_event("Soccer Photos", "20260725T090000")))
    kate_ics = ICS_TEMPLATE.format(name="Kate", events=(
        ics_event("Parent-Teacher Conference", "20260722T190000") +
        ics_event("School Assembly", "20260724", all_day=True)))

    # ---- parse_calendar accepts every transport -----------------------------
    ev_bytes = fam.parse_calendar(chris_ics.encode())
    check(len(ev_bytes) == 2 and ev_bytes[0]["start"].startswith("2026-07-22"),
          "parse_calendar reads raw ICS bytes")
    ev_json = fam.parse_calendar(json.dumps(
        [{"title": "X", "start": "2026-07-01T10:00:00"}]))
    check(len(ev_json) == 1, "parse_calendar reads a JSON event list")
    ev_dict = fam.parse_calendar({"events": ev_json})
    check(len(ev_dict) == 1, "parse_calendar reads a dict with 'events'")
    try:
        fam.parse_calendar("definitely not a calendar")
        check(False, "parse_calendar rejects garbage")
    except ValueError:
        check(True, "parse_calendar rejects garbage")

    # ---- consent is enforced -------------------------------------------------
    try:
        fam.federate_family_data(
            message_exports=[("Chris", chris), ("Kate", kate)])
        check(False, "federation without consent raises ConsentError")
    except ConsentError:
        check(True, "federation without consent raises ConsentError")

    # One-sided inputs are rejected with a clear error.
    try:
        fam.federate_family_data(message_exports=[("Chris", chris)],
                                 consented=True)
        check(False, "a single message export is rejected")
    except ValueError:
        check(True, "a single message export is rejected")

    # ---- the full federation --------------------------------------------------
    res = fam.federate_family_data(
        message_exports=[("Chris", chris), ("Kate", kate)],
        calendar_exports=[("Chris", fam.parse_calendar(chris_ics)),
                          ("Kate", fam.parse_calendar(kate_ics))],
        consented=True,
        consent_records=[
            {"participant": "Chris", "agreed_at": "2026-07-12T10:00:00Z"},
            {"participant": "Kate", "agreed_at": "2026-07-12T10:03:00Z"},
        ])
    family = res["family"]
    gaps = family["gaps"]

    check(family["format"] == "desmond-family/1", "payload carries a format version")
    check(len(family["consent"].get("records", [])) == 2,
          "consent trail stored in the payload")
    json.dumps(family)
    check(True, "payload is JSON-serializable end to end")

    # Calendar: conference matched loosely (no gap); two real gaps.
    cal_titles = sorted(g["title"] for g in gaps["calendar"])
    check(cal_titles == ["School Assembly", "Soccer Photos"],
          f"calendar gaps are exactly the one-sided events (got {cal_titles})")
    assembly = next(g for g in gaps["calendar"] if g["title"] == "School Assembly")
    check(assembly["missing_for"] == ["Chris"] and assembly["all_day"],
          "all-day assembly flagged as missing for Chris")
    conf = next(e for e in family["calendar"]["events"]
                if "Conference" in e["title"])
    check(conf["shared"] and conf.get("loose_match"),
          "same-day conference merged as a loose match, not a gap")
    check(family["calendar"]["shared_events"] == 1, "one truly shared event")

    # Messages: dentist thread = thread gap; school text matched despite
    # 40s lag; couple thread never appears.
    check(len(gaps["messages"]) == 0,
          f"no per-message gaps (carrier lag tolerated) (got {gaps['messages']})")
    thread_convs = sorted(g["conversation"] for g in gaps["threads"])
    check(thread_convs == ["Coach Dan", "Smile Dental"],
          f"thread gaps are the dentist and the coach (got {thread_convs})")
    dental = next(g for g in gaps["threads"] if g["conversation"] == "Smile Dental")
    check(dental["owner"] == "Chris" and dental["missing_for"] == ["Kate"],
          "dentist texts only Chris; Kate flagged as missing")
    check(not any("Kate" in g["conversation"] or "Chris" in g["conversation"]
                  for g in gaps["threads"]),
          "the couple's own thread is not a gap")

    # A message one parent didn't get inside a MATCHED thread is a gap.
    kate2 = json.loads(json.dumps(kate))
    kate2["messages"] = [m for m in kate2["messages"]
                         if m["conversation"] != "Room 12 Parents"] + [
        msg("2026-07-09T15:00:40", "Room 12 Parents", "Ms. Alvarez", False,
            "Field trip forms due Friday", conv_type="group"),
        msg("2026-07-10T08:00:00", "Room 12 Parents", "Ms. Alvarez", False,
            "Bus leaves 7:45 sharp", conv_type="group"),
    ]
    res2 = fam.federate_family_data(
        message_exports=[("Chris", chris), ("Kate", kate2)], consented=True)
    mg = res2["family"]["gaps"]["messages"]
    check(len(mg) == 1 and mg[0]["text"] == "Bus leaves 7:45 sharp"
          and mg[0]["owner"] == "Kate" and mg[0]["missing_for"] == ["Chris"],
          "a text only Kate received in a shared thread is a message gap")

    # ---- since + keyword filters ----------------------------------------------
    res3 = fam.federate_family_data(
        message_exports=[("Chris", chris), ("Kate", kate)],
        consented=True, since="2026-07-06")
    t3 = [g["conversation"] for g in res3["family"]["gaps"]["threads"]]
    check(t3 == ["Smile Dental"],
          f"--since drops the coach's older thread gap (got {t3})")
    res4 = fam.federate_family_data(
        message_exports=[("Chris", chris), ("Kate", kate)],
        calendar_exports=[("Chris", fam.parse_calendar(chris_ics)),
                          ("Kate", fam.parse_calendar(kate_ics))],
        consented=True, keywords=["dental"])
    g4 = res4["family"]["gaps"]
    check(len(g4["threads"]) == 1 and len(g4["calendar"]) == 0,
          "--keyword narrows the report to matching gaps")

    # ---- explicit --same-thread mapping ----------------------------------------
    kate3 = json.loads(json.dumps(kate))
    for m in kate3["messages"]:
        if m["conversation"] == "Coach Dan":
            m["conversation"] = "+15125550142"
    for c in kate3["conversations"]:
        if c["name"] == "Coach Dan":
            c["name"] = "+15125550142"
    chris3 = json.loads(json.dumps(chris))
    chris3["messages"].append(
        msg("2026-07-05T18:00:10", "Dan (Soccer)", "Dan (Soccer)", False,
            "Practice moved to 5pm Tuesday"))
    chris3["conversations"].append({"name": "Dan (Soccer)", "type": "direct"})
    res5 = fam.federate_family_data(
        message_exports=[("Chris", chris3), ("Kate", kate3)], consented=True,
        explicit_same=[("Dan (Soccer)", "+15125550142")])
    t5 = [g["conversation"] for g in res5["family"]["gaps"]["threads"]]
    check("Dan (Soccer)" not in t5 and "+15125550142" not in t5,
          f"--same-thread merges differently-named threads (got {t5})")

    # ---- rendering ---------------------------------------------------------------
    md = res["gaps_md"]
    check(md.startswith("# Family Coverage Gaps"), "gaps report renders")
    check("Only on Kate's calendar" in md and "School Assembly" in md,
          "calendar gap named in the report")
    check("Smile Dental" in md and "never hears from them" in md,
          "thread gap named in the report")
    summary = res["summary_md"]
    check("Chris" in summary and "Kate" in summary and "Gaps found" in summary,
          "summary names both parents and the gap counts")

    # No gaps at all -> a clean ✅ report.
    res6 = fam.federate_family_data(
        calendar_exports=[("Chris", fam.parse_calendar(chris_ics)),
                          ("Kate", fam.parse_calendar(chris_ics))],
        consented=True)
    check("No gaps found" in res6["gaps_md"],
          "identical calendars produce the no-gaps report")

    # ---- disk wrapper ---------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "Family")
        disk = fam.federate_family(
            message_exports=[("Chris", chris), ("Kate", kate)],
            calendar_exports=[("Chris", fam.parse_calendar(chris_ics)),
                              ("Kate", fam.parse_calendar(kate_ics))],
            output_dir=out, consented=True, verbose=False)
        check(os.path.exists(disk["json_path"]), "family.json written")
        check(os.path.exists(disk["gaps_path"]), "FAMILY_GAPS.md written")
        check(os.path.exists(disk["summary_path"]), "FAMILY_SUMMARY.md written")
        check(disk["shared_transcripts"]
              and os.path.exists(disk["shared_transcripts"][0]),
              "couple's shared transcript written alongside")
        data = json.load(open(disk["json_path"]))
        check(data["gap_counts"]["threads"] == 2, "gap counts persisted")

        # An offline .ics file loads through the same path as a feed.
        ics_path = os.path.join(tmp, "kate.ics")
        with open(ics_path, "w") as f:
            f.write(kate_ics)
        events = fam.load_calendar_source(ics_path, verbose=False)
        check(len(events) == 2, "load_calendar_source reads an .ics file")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
