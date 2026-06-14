#!/usr/bin/env python3
"""Test desmond_log: PII-safe redaction + structured run log output."""

import json
import os
import tempfile

import desmond_log as dlog


def main():
    failures = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            failures.append(msg)

    home = os.path.expanduser("~")

    # --- sanitize() strips personal bits ---
    s = dlog.sanitize(f"{home}/Library/CloudStorage/GoogleDrive-me@x.com/My Drive")
    check(home not in s, "sanitize redacts the home path")
    check("GoogleDrive-<account>" in s, "sanitize redacts the Google Drive account")
    check(dlog.sanitize("text me@example.com ok") == "text [email] ok",
          "sanitize redacts email addresses")

    # --- RunLogger writes .log + .json, records metrics, captures errors ---
    with tempfile.TemporaryDirectory() as tmp:
        lg = dlog.RunLogger("unit", log_dir=tmp)
        lg.metric(conversations=3, messages=100, attachments=7)
        with lg.phase("build"):
            pass
        lg.log("info", "note", path=f"{home}/secret/place")
        try:
            raise ValueError("boom")
        except Exception:
            lg.exception("caught it")
        jp = lg.close(status="ok", output_dir=f"{home}/Archive")

        check(os.path.exists(jp) and os.path.exists(lg.txt_path),
              "writes both .json and .log")
        d = json.load(open(jp))
        check(d["metrics"]["conversations"] == 3, "metrics are recorded")
        check(d["status"] == "ok" and "seconds" in d, "status + duration recorded")
        check(d["error_count"] == 1 and d["errors"], "error/exception captured")
        check(home not in json.dumps(d), "no home path leaks into the JSON")
        check(d["summary"]["output_dir"].startswith("~"), "summary paths sanitized")
        check(any(e["msg"].startswith("phase done") for e in d["events"]),
              "phase timing logged")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
