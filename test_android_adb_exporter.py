#!/usr/bin/env python3
"""
Synthetic test for android_adb_exporter.py — a fake phone answers every adb
call, so this verifies device listing, the content-query row parser (commas
and newlines in message bodies), contact-name resolution, MMS text parts,
and the final export shape. No adb, no phone, no network.
"""

import json

import android_adb_exporter as adb


DEVICES_OUT = """List of devices attached
R58M12ABCDE\tdevice usb:1-1 product:a54xeea model:SM_A546B device:a54x
emulator-5554\toffline
ZY22FAKE01\tunauthorized usb:1-2
"""

SMS_OUT = (
    "Row: 0 _id=1, thread_id=3, address=+15125550100, date=1751968800000, "
    "type=1, body=Reminder: Emma's cleaning Jul 20, bring insurance card\n"
    "Row: 1 _id=2, thread_id=3, address=+15125550100, date=1751969000000, "
    "type=2, body=Thanks, see you then!\n"
    "Row: 2 _id=3, thread_id=4, address=+15125550188, date=1751969100000, "
    "type=1, body=Line one\nline two of the same text\n"
    "Row: 3 _id=4, thread_id=4, address=+15125550188, date=1751969200000, "
    "type=3, body=a draft never sent\n"
    "Row: 4 _id=5, thread_id=5, address=NULL, date=NULL, type=1, body=NULL\n"
)

CONTACTS_OUT = (
    "Row: 0 data1=+1 (512) 555-0100, display_name=Smile Dental\n"
    "Row: 1 data1=512-555-0188, display_name=Coach Dan\n"
    "Row: 2 data1=NULL, display_name=Nobody\n"
)

MMS_OUT = ("Row: 0 _id=90, thread_id=3, date=1751970000, msg_box=1\n"
           "Row: 1 _id=91, thread_id=99, date=1751970100, msg_box=2\n")

PART_OUT = (
    "Row: 0 mid=90, ct=application/smil, text=NULL\n"
    "Row: 1 mid=90, ct=text/plain, text=Photo of the new schedule attached\n"
    "Row: 2 mid=90, ct=image/jpeg, text=NULL\n"
    "Row: 3 mid=91, ct=text/plain, text=Group reply from me\n"
)


def fake_run(args, adb_path=None, timeout=None, **kw):
    joined = " ".join(args)
    if args[0] == "devices" or (len(args) > 1 and args[1] == "devices"):
        return DEVICES_OUT
    if "content://sms" in joined:
        return SMS_OUT
    if "contacts/data/phones" in joined:
        return CONTACTS_OUT
    if "content://mms/part" in joined:
        return PART_OUT
    if "content://mms" in joined:
        return MMS_OUT
    return ""


def main():
    failures = []

    def check(cond, label):
        print(("PASS" if cond else "FAIL") + ": " + label)
        if not cond:
            failures.append(label)

    # ---- device listing ----
    devices = adb.list_devices(run=fake_run)
    check([d["state"] for d in devices] == ["device", "offline", "unauthorized"],
          f"device states parsed (got {[d['state'] for d in devices]})")
    check(devices[0]["model"] == "SM A546B", "model name parsed")

    # ---- row parser: commas and newlines inside body ----
    rows = adb.parse_content_rows(SMS_OUT, ["_id", "thread_id", "address",
                                            "date", "type", "body"])
    check(len(rows) == 5, f"5 SMS rows parsed (got {len(rows)})")
    check(rows[0]["body"] == "Reminder: Emma's cleaning Jul 20, bring "
                             "insurance card",
          "commas inside the body survive parsing")
    check(rows[2]["body"] == "Line one\nline two of the same text",
          "newlines inside the body survive parsing")
    check(rows[4]["address"] is None and rows[4]["body"] is None,
          "NULL becomes None")
    trap = ("Row: 0 _id=1, thread_id=3, address=+15125550100, "
            "date=1783504800000, type=1, body=see the log line:\n"
            "Row: 7 of the spreadsheet is wrong\n"
            "Row: 1 _id=2, thread_id=3, address=+15125550100, "
            "date=1783504900000, type=1, body=next\n")
    trapped = adb.parse_content_rows(trap, ["_id", "thread_id", "address",
                                            "date", "type", "body"])
    check(len(trapped) == 2 and "spreadsheet" in trapped[0]["body"],
          "a body line that LOOKS like a new row stays in the body")

    # ---- the full read ----
    export = adb.read_android_phone(run=fake_run, quiet=True)
    check(export["source"] == "Android phone via USB (adb)",
          "export labeled as USB read")
    msgs = export["messages"]
    # 5 SMS rows: 1 draft skipped, 1 dateless skipped -> 3; MMS: 2.
    check(export["total_messages"] == 5,
          f"drafts and dateless rows skipped (got {export['total_messages']})")
    dental = [m for m in msgs if m["conversation"] == "Smile Dental"]
    check(len(dental) == 3, "contact name resolved from the phone's address "
          f"book incl. MMS thread (got {len(dental)})")
    check(dental[0]["is_from_me"] is False and dental[1]["is_from_me"] is True,
          "sent/received direction preserved")
    coach = [m for m in msgs if m["conversation"] == "Coach Dan"]
    check(len(coach) == 1 and "line two" in coach[0]["text"],
          "multi-line SMS kept whole under the resolved contact")
    mms_in = next(m for m in msgs if m["message_type"] == "text_with_attachment")
    check(mms_in["text"] == "Photo of the new schedule attached"
          and mms_in["attachment_types"] == ["photo"],
          "MMS text part + attachment type extracted")
    check(any(m["conversation"].startswith("MMS conversation")
              for m in msgs), "MMS-only thread gets a placeholder name")
    json.dumps(export)
    check(True, "export is JSON-serializable")

    # ---- unauthorized / missing phones give human errors ----
    def only_unauthorized(args, **kw):
        if "devices" in args:
            return "List of devices attached\nZY22\tunauthorized\n"
        return ""
    try:
        adb.read_android_phone(run=only_unauthorized, quiet=True)
        check(False, "unauthorized phone raises a fix-it error")
    except adb.AdbError as e:
        check("Allow" in str(e), "unauthorized phone raises a fix-it error")
    try:
        adb.read_android_phone(run=lambda a, **k: "List of devices attached\n",
                               quiet=True)
        check(False, "no phone raises a fix-it error")
    except adb.AdbError as e:
        check("USB debugging" in str(e), "no phone raises a fix-it error")

    # Permission denial surfaces the USB-mode hint.
    def denied(args, **kw):
        if "devices" in args:
            return DEVICES_OUT
        return "Error: Permission Denial: opening provider"
    try:
        adb.read_android_phone(run=denied, quiet=True)
        check(False, "permission denial raises a fix-it error")
    except adb.AdbError as e:
        check("USB debugging" in str(e), "permission denial raises a fix-it error")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
