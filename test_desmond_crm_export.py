#!/usr/bin/env python3
"""
Synthetic test for desmond_crm_export.py.

Builds a small fake Desmond export (direct + group messages), runs it through
the PersonalCRM bridge, and checks: the payload is self-identifying, only the
fields PersonalCRM reads are kept, values (including cell numbers in `address`)
survive, JSON text/bytes are accepted, malformed input is rejected, and reading
from a messages.json file or an export folder both work.

Run:  python3 test_desmond_crm_export.py
"""

import json
import os
import tempfile

import desmond_crm_export as bridge
from desmond_federate import parse_export

failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def sample_export():
    return {
        "export_date": "2026-07-12T10:00:00",
        "source": "Messages on this Mac",
        "total_messages": 3,
        "total_conversations": 2,
        "conversations": [
            {"name": "Alex Rivera", "type": "direct", "message_count": 2},
            {"name": "Weekend Trip", "type": "group", "message_count": 1},
        ],
        "messages": [
            {
                "timestamp": "2026-01-02T09:00:00", "date": "2026-01-02",
                "time": "09:00:00", "conversation": "Alex Rivera",
                "conversation_type": "direct", "address": "+1 555-0101",
                "sender": "Alex Rivera", "is_from_me": False,
                "message_type": "text", "text": "lunch?",
                "has_attachment": False, "attachment_types": [], "reaction": None,
                "word_count": 1,  # a field the CRM ignores — must be dropped
            },
            {
                "timestamp": "2026-01-02T09:01:00", "conversation": "Alex Rivera",
                "conversation_type": "direct", "address": "+1 555-0101",
                "sender": "Me", "is_from_me": True, "message_type": "text",
                "text": "yes", "has_attachment": False,
            },
            {
                "timestamp": "2026-03-01T08:00:00", "conversation": "Weekend Trip",
                "conversation_type": "group", "address": "+1 555-0202",
                "sender": "Jordan Lee", "is_from_me": False,
                "message_type": "text", "text": "who's driving",
                "has_attachment": False,
            },
        ],
    }


class FakeSources:
    """Stand-in for desmond_sources so the auto-detect path can be driven
    without a Mac: `state` is what messages_db_state() reports."""
    FDA_FIX_MESSAGE = "FAKE FDA FIX: System Settings → Privacy & Security → Full Disk Access"

    def __init__(self, state):
        self.state = state
        self.read_calls = 0

    def messages_db_state(self, path=None):
        return self.state

    def detect_available(self):
        return {"platform": "darwin", "mac_messages": self.state == "ok",
                "mac_messages_needs_full_disk_access": self.state == "no_access",
                "iphone_backups": [], "adb_installed": False, "android_devices": []}

    def read_mac_messages(self):
        self.read_calls += 1
        return sample_export()


def main():
    print("desmond_crm_export tests\n")

    payload = bridge.build_crm_export(sample_export())

    check(payload["app"] == "personalcrm", "payload identifies the target app")
    check(payload["schema"] == "desmond-crm-export/v1", "payload carries a schema tag")
    check(payload["total_messages"] == 3, "message count is correct")
    check(len(payload["conversations"]) == 2, "conversation metadata preserved")
    check(payload["source"] == "Messages on this Mac", "source label preserved")

    first = payload["messages"][0]
    check(set(first.keys()) == set(bridge.CRM_MESSAGE_FIELDS),
          "only the CRM-relevant fields are kept (dropped word_count etc.)")
    check(first["address"] == "+1 555-0101",
          "cell number rides along in `address` (pull-from-phone path)")
    check(first["conversation_type"] == "direct", "direct type preserved")
    check(payload["messages"][2]["conversation_type"] == "group",
          "group type preserved")
    check(payload["messages"][1]["is_from_me"] is True, "is_from_me preserved")

    # Accepts JSON text and bytes, not just a dict.
    as_text = bridge.build_crm_export(json.dumps(sample_export()))
    as_bytes = bridge.build_crm_export(json.dumps(sample_export()).encode("utf-8"))
    check(as_text["total_messages"] == 3, "accepts JSON text input")
    check(as_bytes["total_messages"] == 3, "accepts JSON bytes input")

    # The output is itself a valid Desmond export (round-trips through parse).
    check(parse_export(payload)["total_messages"] == 3,
          "output re-validates as a Desmond export")

    # Malformed input is rejected.
    try:
        bridge.build_crm_export('{"nope": true}')
        check(False, "rejects JSON with no messages list")
    except ValueError:
        check(True, "rejects JSON with no messages list")

    # Reading from a messages.json file and from an export folder.
    with tempfile.TemporaryDirectory() as tmp:
        folder = os.path.join(tmp, "MyExport")
        os.makedirs(folder)
        json_path = os.path.join(folder, "messages.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(sample_export(), f)

        from_file = bridge.load_export_from_path(json_path)
        from_folder = bridge.load_export_from_path(folder)
        check(from_file["total_messages"] == 3, "load_export_from_path reads a file")
        check(from_folder["total_messages"] == 3, "load_export_from_path reads a folder")

        # End-to-end via main(): reads --from and writes --out.
        out_path = os.path.join(tmp, "personalcrm_import.json")
        rc = bridge.main(["--from", folder, "--out", out_path])
        check(rc == 0, "main() returns 0 on success")
        check(os.path.exists(out_path), "main() writes the --out file")
        with open(out_path, encoding="utf-8") as f:
            written = json.load(f)
        check(written["app"] == "personalcrm", "written file is CRM-ready")

        # --out pointing at a DIRECTORY writes personalcrm_import.json inside it.
        out_dir = os.path.join(tmp, "Downloads")
        os.makedirs(out_dir)
        rc = bridge.main(["--from", folder, "--out", out_dir])
        inside = os.path.join(out_dir, "personalcrm_import.json")
        check(rc == 0 and os.path.isfile(inside),
              "--out <existing directory> writes personalcrm_import.json inside it")
        check(os.path.isdir(out_dir), "the directory is not clobbered by a file")
        leftovers = [n for n in os.listdir(out_dir) if n.endswith(".tmp")]
        check(not leftovers, f"atomic write leaves no temp file behind (got {leftovers})")
        with open(inside, encoding="utf-8") as f:
            check(json.load(f)["total_messages"] == 3, "directory-mode file is complete")

        # A trailing slash means "folder", even one that doesn't exist yet.
        new_dir = os.path.join(tmp, "NewFolder") + os.sep
        rc = bridge.main(["--from", folder, "--out", new_dir])
        check(rc == 0 and os.path.isfile(os.path.join(new_dir, "personalcrm_import.json")),
              "--out with a trailing slash creates the folder and writes inside it")
        check(bridge.resolve_out_path(out_path) == out_path,
              "a plain file path is used as-is")

        # Overwrite goes through os.replace: the old file is swapped, not truncated.
        with open(inside, "w", encoding="utf-8") as f:
            f.write("stale")
        bridge.write_json_atomic(inside, {"fresh": True})
        with open(inside, encoding="utf-8") as f:
            check(json.load(f) == {"fresh": True}, "write_json_atomic replaces an existing file")

    # Auto-detect on a Mac without Full Disk Access: print the fix, exit 3.
    fake = FakeSources("no_access")
    try:
        bridge._auto_source(fake)
        check(False, "auto-detect without Full Disk Access exits with code 3")
    except SystemExit as e:
        check(e.code == 3, f"auto-detect without Full Disk Access exits with code 3 (got {e.code})")
    check(fake.read_calls == 0, "no read is attempted when access is missing")

    # Readable Mac: no FDA exit, chat.db is read.
    ok = FakeSources("ok")
    bridge._exit_if_full_disk_access_missing(ok)
    check(bridge._auto_source(ok)["total_messages"] == 3 and ok.read_calls == 1,
          "auto-detect with a readable chat.db reads it normally")

    # No Mac database at all: the generic 'nothing to read' exit (not code 3).
    try:
        bridge._auto_source(FakeSources("missing"))
        check(False, "auto-detect with nothing readable exits with a message")
    except SystemExit as e:
        check(e.code != 3 and "Couldn't find any messages" in str(e.code),
              "auto-detect with nothing readable exits with the generic message")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
