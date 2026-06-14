#!/usr/bin/env python3
"""
Test the picker's new export path: real attachments copied + inline-media HTML
transcript + datetime ordering + filename convention (date/time + people first).

Runs on any platform — constructs records directly and calls export_records
(no real Messages DB needed).
"""

import json
import os
import re
import tempfile

import imessage_picker as picker


def main():
    failures = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            failures.append(msg)

    # apply_order: records arrive oldest->newest.
    a = [{"timestamp": "2024-01-01T00:00:00"}, {"timestamp": "2024-02-01T00:00:00"}]
    check(picker.apply_order(a, "oldest")[0]["timestamp"].startswith("2024-01"),
          "apply_order oldest keeps oldest first")
    check(picker.apply_order(a, "newest")[0]["timestamp"].startswith("2024-02"),
          "apply_order newest puts newest first")

    with tempfile.TemporaryDirectory() as tmp:
        photo = os.path.join(tmp, "IMG_1.jpg")
        with open(photo, "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"\x00" * 500)  # tiny fake jpeg

        records = [
            {
                "id": 1, "person": "Mom", "timestamp": "2024-01-15T09:32:00",
                "date": "2024-01-15", "time": "09:32:00", "sender": "Mom",
                "is_from_me": False, "message_type": "text_with_attachment",
                "text": "look at this [photo]", "text_plain": "look at this",
                "attachment_types": ["photo"],
                "attachments": [{"category": "photo", "mime": "image/jpeg",
                                 "filename": photo, "transfer_name": "IMG_1.jpg"}],
                "reaction": None,
            },
            {
                "id": 2, "person": "Mom", "timestamp": "2024-01-16T10:00:00",
                "date": "2024-01-16", "time": "10:00:00", "sender": "Me",
                "is_from_me": True, "message_type": "text", "text": "nice!",
                "text_plain": "nice!", "attachment_types": [], "attachments": [],
                "reaction": None,
            },
            {
                "id": 3, "person": "Mom", "timestamp": "2024-01-17T11:00:00",
                "date": "2024-01-17", "time": "11:00:00", "sender": "Mom",
                "is_from_me": False, "message_type": "attachment",
                "text": "[photo]", "text_plain": "",
                "attachment_types": ["photo"],
                "attachments": [{"category": "photo", "mime": "image/heic",
                                 "filename": "/no/such/offloaded.heic",
                                 "transfer_name": "offloaded.heic"}],
                "reaction": None,
            },
        ]

        dest = os.path.join(tmp, "drive")
        f = {"types": ["text", "attachments", "reactions"], "range": "all",
             "order": "newest", "direction": "both", "dest": dest}

        res = picker.export_records(records, ["Mom"], f)
        check(res["ok"], "export succeeded")
        check(res["attachments_saved"] == 1, f"saved 1 real attachment (got {res['attachments_saved']})")
        check(res["attachments_missing"] == 1, f"flagged 1 missing (got {res['attachments_missing']})")

        folder = res["folder"]
        adir = os.path.join(folder, "attachments")
        files = os.listdir(adir) if os.path.isdir(adir) else []
        check(len(files) == 1, f"one file copied into attachments/ (got {files})")
        # Filename: date/time stamp FIRST, then the people in the chat.
        check(bool(files) and re.match(r"^2024-01-15_0932_Mom_", files[0]),
              f"filename leads with datetime + people (got {files[0] if files else None})")

        html_path = os.path.join(folder, "conversation.html")
        check(os.path.exists(html_path), "wrote conversation.html")
        html = open(html_path, encoding="utf-8").read()
        check("<img class=\"att\"" in html, "HTML embeds the photo inline (<img>)")
        check('id="toggle"' in html and 'let order = "newest"' in html,
              "HTML has order toggle defaulting to newest")
        check("not downloaded from iCloud" in html, "HTML notes the missing/offloaded file")

        with open(os.path.join(folder, "messages.json"), encoding="utf-8") as jf:
            man = json.load(jf)
        check(man["order"] == "newest", "messages.json records the order")
        check(man["attachments_saved"] == 1, "messages.json counts saved attachments")
        media1 = next(m for m in man["messages"] if m["id"] == 1)["media"]
        check(media1 and media1[0]["path"].startswith("attachments/"),
              "manifest links message to its saved attachment path")

        check(os.path.exists(os.path.join(folder, "conversation.md")), "wrote conversation.md")
        check(os.path.exists(os.path.join(folder, "messages.csv")), "wrote messages.csv")

        # Attachments OFF → no files copied.
        f_off = dict(f, types=["text"], dest=os.path.join(tmp, "drive2"))
        res2 = picker.export_records(records, ["Mom"], f_off)
        check(res2["attachments_saved"] == 0,
              "attachments toggle off copies nothing")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
