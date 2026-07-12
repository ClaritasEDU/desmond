#!/usr/bin/env python3
"""
Synthetic test for desmond_federate.py.

Builds two fake Desmond exports (Chris's and Kate's), including their mutual
thread seen from both phones, federates them, and checks: consent is enforced,
the shared thread is detected and deduplicated, senders are disambiguated,
owners are tagged, and the summary/transcript/CSV outputs are written.
"""

import json
import os
import tempfile

import desmond_federate as fed


def make_export(tmp, folder, conversations, messages):
    d = os.path.join(tmp, folder)
    os.makedirs(d)
    with open(os.path.join(d, "messages.json"), "w") as f:
        json.dump({
            "export_date": "2026-07-12T10:00:00",
            "total_messages": len(messages),
            "total_conversations": len(conversations),
            "conversations": conversations,
            "messages": messages,
        }, f)
    return d


def msg(ts, conv, sender, from_me, text, conv_type="direct"):
    return {
        "timestamp": ts, "date": ts[:10], "time": ts[11:19],
        "conversation": conv, "conversation_type": conv_type,
        "sender": sender, "is_from_me": from_me,
        "message_type": "text", "text": text,
        "has_attachment": False, "attachment_types": [],
        "reaction": None, "special_content": None, "effect": None,
        "char_count": len(text), "word_count": len(text.split()),
    }


def main():
    failures = []

    def check(cond, label):
        print(("PASS" if cond else "FAIL") + ": " + label)
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        # Chris's phone: thread with "Kate ❤️" + a thread with Mom.
        chris_dir = make_export(tmp, "chris", [
            {"name": "Kate ❤️", "type": "direct", "message_count": 2,
             "first_message": "2026-01-01T09:00:00",
             "last_message": "2026-01-01T09:05:00"},
            {"name": "Mom", "type": "direct", "message_count": 1,
             "first_message": "2026-01-02T12:00:00",
             "last_message": "2026-01-02T12:00:00"},
        ], [
            msg("2026-01-01T09:00:00", "Kate ❤️", "Me", True, "On my way home"),
            msg("2026-01-01T09:05:00", "Kate ❤️", "Kate ❤️", False, "Grab milk please"),
            msg("2026-01-02T12:00:00", "Mom", "Mom", False, "Call me when free"),
        ])

        # Kate's phone: the SAME two messages from her side + her own thread.
        kate_dir = make_export(tmp, "kate", [
            {"name": "Chris", "type": "direct", "message_count": 2,
             "first_message": "2026-01-01T09:00:00",
             "last_message": "2026-01-01T09:05:00"},
            {"name": "Book Club", "type": "group", "message_count": 1,
             "first_message": "2026-01-03T18:00:00",
             "last_message": "2026-01-03T18:00:00"},
        ], [
            msg("2026-01-01T09:00:00", "Chris", "Chris", False, "On my way home"),
            msg("2026-01-01T09:05:00", "Chris", "Me", True, "Grab milk please"),
            msg("2026-01-03T18:00:00", "Book Club", "Ann", False,
                "Meeting moved to Thursday", conv_type="group"),
        ])

        # Consent is enforced at the library level.
        exports = [("Chris", fed.load_export(chris_dir)),
                   ("Kate", fed.load_export(kate_dir))]
        try:
            fed.federate(exports, os.path.join(tmp, "nope"), consented=False,
                         verbose=False)
            check(False, "federate without consent raises ConsentError")
        except fed.ConsentError:
            check(True, "federate without consent raises ConsentError")

        # load_export rejects folders without an export.
        try:
            fed.load_export(os.path.join(tmp, "empty-nonexistent"))
            check(False, "load_export rejects a folder without messages.json")
        except FileNotFoundError:
            check(True, "load_export rejects a folder without messages.json")

        out = os.path.join(tmp, "Federated")
        res = fed.federate(exports, out, consented=True, verbose=False)

        check(res["shared_threads"] == ["Chris and Kate"],
              f"shared thread auto-detected despite emoji name (got {res['shared_threads']})")
        # 6 raw messages, 2 shared ones deduplicated -> 4.
        check(res["deduplicated"] == 2,
              f"both shared messages deduplicated (got {res['deduplicated']})")
        check(res["messages"] == 4,
              f"merged stream has 4 messages (got {res['messages']})")

        data = json.load(open(res["json_path"]))
        shared = [m for m in data["messages"] if m.get("shared")]
        check(len(shared) == 2, f"2 messages in the shared thread (got {len(shared)})")
        check(all(m["conversation"] == "Chris and Kate" for m in shared),
              "shared messages renamed to the joint thread")
        check({m["sender"] for m in shared} == {"Chris", "Kate"},
              "'Me' rewritten to real names on both sides")
        check(all(sorted(m.get("seen_by", [])) == ["Chris", "Kate"] for m in shared),
              "deduped messages record both phones in seen_by")
        check(all("owner" in m for m in data["messages"]),
              "every message tagged with its owner")
        mom = next(m for m in data["messages"] if m["conversation"] == "Mom")
        check(mom["owner"] == "Chris" and mom["shared"] is False,
              "non-shared threads keep their owner and shared=False")
        club = next(m for m in data["messages"] if m["conversation"] == "Book Club")
        check(club["owner"] == "Kate", "Kate's group thread carried over")

        check(os.path.exists(res["csv_path"]), "federated.csv written")
        summary = open(res["summary_path"]).read()
        check("Chris" in summary and "Kate" in summary and "shared" in summary.lower(),
              "summary names both people and the shared thread")
        check(len(res["shared_transcripts"]) == 1
              and os.path.exists(res["shared_transcripts"][0]),
              "shared transcript .md written")
        transcript = open(res["shared_transcripts"][0]).read()
        check("Grab milk please" in transcript and "⇄" in transcript,
              "transcript contains the merged messages, marked as seen by both")

        # Explicit --shared mapping works when names don't match at all.
        chris2 = make_export(tmp, "chris2", [
            {"name": "Wifey", "type": "direct", "message_count": 1,
             "first_message": "2026-02-01T08:00:00",
             "last_message": "2026-02-01T08:00:00"}],
            [msg("2026-02-01T08:00:00", "Wifey", "Me", True, "hey")])
        kate2 = make_export(tmp, "kate2", [
            {"name": "Hubs", "type": "direct", "message_count": 1,
             "first_message": "2026-02-01T08:00:00",
             "last_message": "2026-02-01T08:00:00"}],
            [msg("2026-02-01T08:00:00", "Hubs", "Hubs", False, "hey")])
        res2 = fed.federate(
            [("Chris", fed.load_export(chris2)), ("Kate", fed.load_export(kate2))],
            os.path.join(tmp, "Federated2"), consented=True,
            explicit_shared=[("Wifey", "Hubs")], verbose=False)
        check(res2["deduplicated"] == 1 and res2["messages"] == 1,
              "explicit --shared mapping merges nickname threads")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
