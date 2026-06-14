#!/usr/bin/env python3
"""
Test imessage_exporter.mirror_to_drive — the message text export should live in
BOTH the local folder and Google Drive. Runs on any platform.
"""

import os
import tempfile

import imessage_exporter as ie


def main():
    failures = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "iMessages_Export")
        os.makedirs(os.path.join(src, "Mom"))
        with open(os.path.join(src, "messages.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(src, "Mom", "2024-01-01.md"), "w") as f:
            f.write("# hi")

        drive = os.path.join(tmp, "GoogleDrive")
        os.makedirs(drive)

        dest = ie.mirror_to_drive(src, drive_dir=drive)
        check(dest == os.path.join(drive, ie.DRIVE_SUBFOLDER),
              "mirrors into <Drive>/Desmond_Messages_Export")
        check(os.path.exists(os.path.join(dest, "messages.json")),
              "top-level file copied to Drive")
        check(os.path.exists(os.path.join(dest, "Mom", "2024-01-01.md")),
              "nested per-conversation file copied to Drive")
        check(os.path.exists(os.path.join(src, "messages.json")),
              "local copy remains (lives in BOTH places)")

        # Re-run after new content → existing Drive copy updates (dirs_exist_ok).
        with open(os.path.join(src, "new.md"), "w") as f:
            f.write("new")
        ie.mirror_to_drive(src, drive_dir=drive)
        check(os.path.exists(os.path.join(dest, "new.md")),
              "re-mirror updates the existing Drive copy")

        # No Drive detected → returns None, local copy untouched.
        nodrive = ie.mirror_to_drive(src, drive_dir=None)
        check(nodrive is None, "no Drive detected → returns None (local only)")
        check(os.path.exists(os.path.join(src, "messages.json")),
              "local export still intact when no Drive")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
