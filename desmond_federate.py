#!/usr/bin/env python3
"""
Desmond Federation — merge two people's Desmond exports into ONE shared archive.

The use case: two people who already share their lives (say a husband and wife)
each run Desmond on their own phone/computer, then combine the two exports into
a single family archive. Their mutual conversation is stitched together from
both sides (deduplicated), and every message is tagged with whose phone it came
from.

Consent comes first
-------------------
Federation only merges exports that each person made of their OWN data and
CHOSE to share. The tool refuses to run until you confirm that every
participant has agreed. It never reads anyone's Messages database directly —
it only combines export folders that were handed to it.

Command line
------------
    cd ~/desmond
    python3 desmond_federate.py "Chris=~/Downloads/iMessages_Export" \\
                                "Kate=/path/to/kates_export"

    # optional flags:
    #   --out DIR          where the federated archive goes
    #                      (default ~/Downloads/Desmond_Federated_Archive)
    #   --consented        skip the interactive consent prompt (scripts/apps
    #                      that already collected consent themselves)
    #   --shared "A=B"     name the shared thread explicitly when auto-detection
    #                      can't (A = conversation name in person 1's export,
    #                      B = conversation name in person 2's export)

Using Desmond federation from another app (including online apps)
------------------------------------------------------------------
Everything is importable — no external dependencies — and the whole merge is
available as a PURE, in-memory function, so a web service can run it on
uploaded exports without touching the filesystem:

    from desmond_federate import parse_export, federate_data

    # e.g. in an upload handler: each spouse uploaded their messages.json
    exports = [("Chris", parse_export(chris_upload_bytes)),
               ("Kate",  parse_export(kate_upload_bytes))]
    result = federate_data(
        exports, consented=True,
        consent_records=[            # whatever consent trail your app collected
            {"participant": "Chris", "agreed_at": "2026-07-12T10:00:00Z"},
            {"participant": "Kate",  "agreed_at": "2026-07-12T10:03:00Z"},
        ])
    result["federated"]             # merged export (JSON-serializable dict)
    result["summary_md"]            # FEDERATED_SUMMARY.md as a string
    result["shared_transcripts"]    # {thread title: transcript .md string}

For local/scripted use, federate() wraps federate_data() and writes the
archive to disk (that's what the CLI calls). Either way, `consented=True` is
a required, explicit assertion that every participant agreed to the merge;
without it a ConsentError is raised. An online app should only set it after
EACH participant has affirmatively opted in (and should pass its consent
trail via consent_records so the archive carries the proof).

What you get
------------
    Desmond_Federated_Archive/
    ├── federated.json          # every message from both exports, tagged with
    │                           #   "owner" (whose export it came from); the
    │                           #   shared thread deduplicated, "seen_by" both
    ├── federated.csv           # same, for spreadsheets
    ├── FEDERATED_SUMMARY.md    # stats per person + the shared thread
    └── shared/
        └── Chris_and_Kate.md   # the couple's own conversation, rebuilt from
                                #   BOTH phones, in date order

Everything runs locally. Nothing is uploaded anywhere.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Downloads/Desmond_Federated_Archive")

# Two phones rarely log the same SMS at the same second — carriers deliver
# with a lag (iMessage stamps match; SMS between an iPhone and an Android
# can differ by tens of seconds). Identical text from the same sender within
# this window is treated as the same message.
DEDUP_WINDOW_SECONDS = 120


class ConsentError(Exception):
    """Raised when federate() is called without explicit participant consent."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_export(path):
    """Load a Desmond export (a folder containing messages.json, or the
    messages.json file itself). Returns the parsed export dict."""
    path = os.path.expanduser(path)
    if os.path.isdir(path):
        json_path = os.path.join(path, "messages.json")
    else:
        json_path = path
    if not os.path.exists(json_path):
        raise FileNotFoundError(
            f"No messages.json found at {path!r}. Run one of the Desmond "
            "exporters first (imessage_exporter.py / desmond_export.py / "
            "android_sms_exporter.py) — federation combines finished exports."
        )
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return parse_export(data, source=json_path)


def parse_export(data, source="upload"):
    """Validate a Desmond export an app received some other way — an upload,
    an API call, a database blob. Accepts a dict, JSON text, or JSON bytes and
    returns the validated export dict. This is the entry point for ONLINE
    apps: whatever transport delivered the user's messages.json, run it
    through here before federating."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"{source}: not valid JSON ({e})")
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError(f"{source} doesn't look like a Desmond export "
                         "(no 'messages' list).")
    return data


# ---------------------------------------------------------------------------
# Shared-thread detection + dedup
# ---------------------------------------------------------------------------

def _norm_name(name):
    """Loose name normalization for matching conversation names to people:
    lowercase, letters/digits only ('Kate ❤️' -> 'kate')."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def detect_shared_threads(exports, explicit=None):
    """Find the conversation in each person's export that is the thread with
    the *other* participant.

    exports:  list of (name, export_dict) — exactly the federate() input.
    explicit: optional list of (conv_name_in_first, conv_name_in_second).

    Returns a list of dicts:
        {"pair": (name_a, name_b), "conv_a": <conv name in a's export>,
         "conv_b": <conv name in b's export>}
    Only direct (non-group) conversations are matched automatically.
    """
    threads = []
    if explicit:
        for conv_a, conv_b in explicit:
            threads.append({"pair": (exports[0][0], exports[1][0]),
                            "conv_a": conv_a, "conv_b": conv_b})
        return threads

    for i, (name_a, exp_a) in enumerate(exports):
        for name_b, exp_b in exports[i + 1:]:
            conv_a = _find_conv_for(exp_a, name_b)
            conv_b = _find_conv_for(exp_b, name_a)
            if conv_a and conv_b:
                threads.append({"pair": (name_a, name_b),
                                "conv_a": conv_a, "conv_b": conv_b})
    return threads


def _find_conv_for(export, other_name):
    """Inside one export, find the direct conversation whose name matches
    other_name (loose match: 'Kate' matches 'Kate ❤️' and 'Kate Treadaway')."""
    want = _norm_name(other_name)
    if not want:
        return None
    best = None
    for conv in export.get("conversations", []):
        if conv.get("type") == "group":
            continue
        have = _norm_name(conv.get("name"))
        if have == want:
            return conv["name"]  # exact normalized match wins immediately
        if want in have and best is None:
            best = conv["name"]
    return best


def _msg_epoch(ts):
    """ISO timestamp -> epoch seconds, or None when unparseable."""
    try:
        return datetime.fromisoformat(ts[:19]).timestamp()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------

def federate_data(exports, consented=False, explicit_shared=None,
                  consent_records=None):
    """The whole federation, IN MEMORY — nothing is read from or written to
    disk. This is the function an online app calls:

        result = federate_data(
            [("Chris", parse_export(chris_upload)),
             ("Kate",  parse_export(kate_upload))],
            consented=True,
            consent_records=[
                {"participant": "Chris", "agreed_at": "2026-07-12T10:00:00Z"},
                {"participant": "Kate",  "agreed_at": "2026-07-12T10:03:00Z"},
            ])

    Returns:
        {"federated":          the merged export dict (JSON-serializable),
         "summary_md":         FEDERATED_SUMMARY.md as a string,
         "shared_transcripts": {thread title: transcript .md string}}

    exports:         list of (person_name, export_dict) — validate uploads
                     with parse_export() first. Two people is the designed
                     case; more works.
    consented:       must be True — the caller's explicit assertion that every
                     participant agreed. Raises ConsentError otherwise.
    consent_records: optional per-participant acknowledgements (any
                     JSON-serializable shape the app collected — names,
                     timestamps, checkbox ids). Stored verbatim in the result
                     so the archive carries its own consent trail.
    """
    if not consented:
        raise ConsentError(
            "Federation requires every participant's consent. Pass "
            "consented=True only after each person has agreed to share "
            "their export."
        )
    if len(exports) < 2:
        raise ValueError("Federation needs at least two exports.")

    threads = detect_shared_threads(exports, explicit=explicit_shared)
    # (owner_name, conv_name) -> thread record, for O(1) lookup per message
    shared_lookup = {}
    for t in threads:
        name_a, name_b = t["pair"]
        shared_lookup[(name_a, t["conv_a"])] = t
        shared_lookup[(name_b, t["conv_b"])] = t
        t["title"] = f"{name_a} and {name_b}"

    merged = []
    seen_shared = {}   # thread title -> {text: [candidate records]}
    deduplicated = 0

    for owner, export in exports:
        for msg in export.get("messages", []):
            rec = dict(msg)
            rec["owner"] = owner
            thread = shared_lookup.get((owner, msg.get("conversation")))
            # "Me" is ambiguous once two people's exports are combined —
            # rewrite it to the owner's actual name. Inside the shared
            # thread the counterpart's nickname ("Kate ❤️") is normalized
            # to their participant name too.
            if rec.get("is_from_me"):
                rec["sender"] = owner
            elif thread is not None:
                others = [p for p in thread["pair"] if p != owner]
                if len(others) == 1:
                    rec["sender"] = others[0]
            if thread is not None:
                rec["conversation"] = thread["title"]
                rec["shared"] = True
                text = (msg.get("text") or "").strip()
                ts = _msg_epoch(msg.get("timestamp", ""))
                cands = seen_shared.setdefault(thread["title"],
                                               {}).setdefault(text, [])
                # Same message from the other phone: same text AND same
                # sender within DEDUP_WINDOW_SECONDS (nearest wins). The
                # sender check keeps "ok" from Chris and "ok" from Kate in
                # the same minute apart; the window absorbs carrier lag on
                # SMS between an iPhone and an Android.
                best = None
                for cand in cands:
                    seen = cand.get("seen_by") or [cand["owner"]]
                    if owner in seen or cand.get("sender") != rec.get("sender"):
                        continue
                    cts = cand.get("_epoch")
                    if ts is None or cts is None:
                        delta = (0 if (msg.get("timestamp") or "")[:19]
                                 == (cand.get("timestamp") or "")[:19] else None)
                    else:
                        delta = abs(cts - ts)
                    if (delta is not None and delta <= DEDUP_WINDOW_SECONDS
                            and (best is None or delta < best[0])):
                        best = (delta, cand)
                if best is not None:
                    existing = best[1]
                    existing.setdefault("seen_by", [existing["owner"]])
                    existing["seen_by"].append(owner)
                    deduplicated += 1
                    continue
                rec["_epoch"] = ts
                cands.append(rec)
            else:
                rec["shared"] = False
            merged.append(rec)

    merged.sort(key=lambda m: m.get("timestamp") or "")
    for m in merged:
        m.pop("_epoch", None)

    # Rebuild conversation metadata from the merged stream.
    conv_meta = {}
    for m in merged:
        name = m.get("conversation") or "Unknown"
        meta = conv_meta.setdefault(name, {
            "name": name,
            "type": m.get("conversation_type", "direct"),
            "shared": m.get("shared", False),
            "owners": [],
            "message_count": 0,
            "first_message": m.get("timestamp") or "",
            "last_message": m.get("timestamp") or "",
        })
        meta["message_count"] += 1
        meta["last_message"] = m.get("timestamp") or ""
        if m["owner"] not in meta["owners"]:
            meta["owners"].append(m["owner"])

    participants = [name for name, _ in exports]
    consent = {
        "confirmed": True,
        "note": "Each participant exported their own data and agreed to "
                "combine it into this shared archive.",
    }
    if consent_records:
        consent["records"] = consent_records
    federated = {
        "format": "desmond-federated/1",
        "federated_by": "desmond_federate.py",
        "participants": participants,
        "consent": consent,
        "total_messages": len(merged),
        "deduplicated": deduplicated,
        "shared_threads": [t["title"] for t in threads],
        "total_conversations": len(conv_meta),
        "conversations": list(conv_meta.values()),
        "messages": merged,
    }

    per_export = [(name, len(export.get("messages", [])),
                   len(export.get("conversations", [])))
                  for name, export in exports]
    return {
        "federated": federated,
        "summary_md": _render_summary(federated, per_export),
        "shared_transcripts": {t["title"]: _render_shared_transcript(t["title"], merged)
                               for t in threads},
    }


def federate(exports, output_dir=DEFAULT_OUTPUT_DIR, consented=False,
             explicit_shared=None, verbose=True, consent_records=None):
    """Merge multiple people's Desmond exports and write the shared archive to
    disk (the CLI path). All the logic lives in federate_data() — use that
    directly from an online app.

    Returns a dict with counts and the output paths.
    """
    data = federate_data(exports, consented=consented,
                         explicit_shared=explicit_shared,
                         consent_records=consent_records)
    federated = data["federated"]
    participants = federated["participants"]
    merged = federated["messages"]

    output_dir = os.path.expanduser(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, "federated.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(federated, f, indent=2, ensure_ascii=False)

    csv_path = _write_csv(output_dir, merged)

    summary_path = os.path.join(output_dir, "FEDERATED_SUMMARY.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(data["summary_md"])

    shared_dir = os.path.join(output_dir, "shared")
    shared_paths = []
    for title, md in data["shared_transcripts"].items():
        os.makedirs(shared_dir, exist_ok=True)
        path = os.path.join(shared_dir,
                            _safe_filename(title.replace(" ", "_")) + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        shared_paths.append(path)

    if verbose:
        print(f"\n🤝 Federated {len(exports)} exports "
              f"({' + '.join(participants)})")
        print(f"   Messages: {len(merged):,} "
              f"({federated['deduplicated']:,} duplicates merged from the "
              "shared thread)")
        for title in federated["shared_threads"]:
            print(f"   Shared thread: {title}")
        print(f"   → {output_dir}")

    return {
        "output_dir": output_dir,
        "participants": participants,
        "messages": len(merged),
        "deduplicated": federated["deduplicated"],
        "shared_threads": federated["shared_threads"],
        "json_path": json_path,
        "csv_path": csv_path,
        "summary_path": summary_path,
        "shared_transcripts": shared_paths,
    }


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

CSV_FIELDS = ["timestamp", "date", "time", "conversation", "conversation_type",
              "owner", "sender", "is_from_me", "shared", "message_type",
              "text", "has_attachment", "reaction"]


def _write_csv(output_dir, merged):
    csv_path = os.path.join(output_dir, "federated.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for m in merged:
            writer.writerow({k: m.get(k, "") for k in CSV_FIELDS})
    return csv_path


def _render_summary(federated, per_export):
    """FEDERATED_SUMMARY.md as a string. per_export: [(name, n_msgs, n_convs)]."""
    lines = ["# Federated Archive Summary", ""]
    lines.append(f"**Participants:** {', '.join(federated['participants'])}")
    lines.append(f"**Total messages:** {federated['total_messages']:,}")
    lines.append(f"**Duplicates merged:** {federated['deduplicated']:,}")
    lines.append("")
    lines.append("Every participant exported their own data and agreed to "
                 "combine it into this shared archive.")
    lines.append("")
    lines.append("## Per person")
    lines.append("")
    for name, n_msgs, n_convs in per_export:
        lines.append(f"- **{name}** — {n_msgs:,} messages, {n_convs:,} "
                     "conversations contributed")
    if federated["shared_threads"]:
        lines.append("")
        lines.append("## Shared threads (merged from both phones)")
        lines.append("")
        for title in federated["shared_threads"]:
            meta = next((c for c in federated["conversations"]
                         if c["name"] == title), None)
            if meta:
                lines.append(f"- **{title}** — {meta['message_count']:,} "
                             f"messages, {meta['first_message'][:10]} → "
                             f"{meta['last_message'][:10]}")
            else:
                lines.append(f"- **{title}**")
    lines.append("")
    lines.append("## Largest conversations")
    lines.append("")
    top = sorted(federated["conversations"],
                 key=lambda c: -c["message_count"])[:20]
    for c in top:
        tag = " *(shared)*" if c.get("shared") else ""
        lines.append(f"- {c['name']} — {c['message_count']:,} messages "
                     f"(from {', '.join(c['owners'])}){tag}")
    lines.append("")
    lines.append("---")
    lines.append("*This archive contains both participants' message history — "
                 "store it as carefully as you'd store either one alone.*")
    return "\n".join(lines) + "\n"


def _safe_filename(name):
    return "".join(ch if (ch.isalnum() or ch in " _-") else "_"
                   for ch in name).strip() or "shared"


def _render_shared_transcript(title, merged):
    """The couple's own conversation, rebuilt from both phones, date-ordered.
    Returns the transcript .md as a string."""
    msgs = [m for m in merged if m.get("conversation") == title]

    lines = [f"# {title}", ""]
    lines.append(f"*{len(msgs):,} messages, rebuilt from both phones and "
                 "deduplicated. Messages seen on both phones are marked ⇄.*")
    current_date = None
    for m in msgs:
        if m.get("date") != current_date:
            current_date = m.get("date")
            lines.append("")
            lines.append(f"## {current_date}")
            lines.append("")
        both = "⇄ " if len(m.get("seen_by", [])) > 1 else ""
        text = m.get("text") or ""
        lines.append(f"- **{m.get('time', '')[:5]} {m.get('sender')}:** "
                     f"{both}{text}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_person(arg):
    """'Chris=~/Downloads/iMessages_Export' -> ('Chris', path)"""
    if "=" not in arg:
        raise argparse.ArgumentTypeError(
            f"Expected NAME=PATH (e.g. \"Chris=~/Downloads/iMessages_Export\"), "
            f"got {arg!r}")
    name, path = arg.split("=", 1)
    name, path = name.strip(), path.strip()
    if not name or not path:
        raise argparse.ArgumentTypeError(f"Both a name and a path are needed "
                                         f"in {arg!r}")
    return name, path


def main():
    parser = argparse.ArgumentParser(
        description="Merge two people's Desmond exports into one shared, "
                    "consent-based archive.")
    parser.add_argument("people", nargs="+", type=_parse_person,
                        metavar="NAME=PATH",
                        help='e.g. "Chris=~/Downloads/iMessages_Export" '
                             '"Kate=/path/to/kates_export"')
    parser.add_argument("--out", default=DEFAULT_OUTPUT_DIR,
                        help="output folder (default: "
                             "~/Downloads/Desmond_Federated_Archive)")
    parser.add_argument("--consented", action="store_true",
                        help="affirm that every participant has already "
                             "agreed (skips the interactive prompt)")
    parser.add_argument("--shared", action="append", default=None,
                        metavar="A_CONV=B_CONV",
                        help="explicitly name the shared thread: the "
                             "conversation name in person 1's export = the "
                             "name in person 2's export")
    args = parser.parse_args()

    if len(args.people) < 2:
        parser.error("Federation needs at least two NAME=PATH exports.")

    explicit = None
    if args.shared:
        explicit = []
        for pair in args.shared:
            if "=" not in pair:
                parser.error(f"--shared expects A_CONV=B_CONV, got {pair!r}")
            a, b = pair.split("=", 1)
            explicit.append((a.strip(), b.strip()))

    names = [name for name, _ in args.people]
    print("🤝 Desmond Federation")
    print(f"   Combining exports from: {' + '.join(names)}")

    if not args.consented:
        print()
        print("   Federation combines each person's private message history")
        print("   into ONE shared archive that everyone listed can read.")
        answer = input(f"   Has each of them ({', '.join(names)}) agreed to "
                       "this? Type yes to continue: ").strip().lower()
        if answer not in ("y", "yes"):
            print("   Stopped — nothing was merged. Come back once everyone "
                  "is on board.")
            sys.exit(1)

    exports = []
    for name, path in args.people:
        try:
            exports.append((name, load_export(path)))
        except (FileNotFoundError, ValueError) as e:
            print(f"❌ {e}")
            sys.exit(1)

    federate(exports, output_dir=args.out, consented=True,
             explicit_shared=explicit)
    print('\n"See you in another life, brother."')


if __name__ == "__main__":
    main()
