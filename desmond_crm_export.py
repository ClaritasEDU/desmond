#!/usr/bin/env python3
"""
Desmond → PersonalCRM bridge.

ONE command that turns your text messages into a file PersonalCRM can import.
It reads your messages from whatever source is handy — this Mac's Messages, a
plugged-in iPhone's local backup, a plugged-in Android phone, or an export you
already made — and writes a single `personalcrm_import.json`.

    cd ~/desmond
    python3 desmond_crm_export.py                  # auto-detect the best source
    python3 desmond_crm_export.py --mac            # this Mac's Messages (chat.db)
    python3 desmond_crm_export.py --iphone         # newest plugged-in iPhone backup
    python3 desmond_crm_export.py --iphone DIR     # a specific iPhone backup folder
    python3 desmond_crm_export.py --android        # plugged-in Android over USB
    python3 desmond_crm_export.py --from PATH       # an existing messages.json / export folder
    python3 desmond_crm_export.py --out PATH        # where to write (default ~/Downloads/personalcrm_import.json)

Then, in PersonalCRM:
    Settings → "Text Message Import (Desmond)" → upload the file.

Cell numbers and emails ride along automatically: each message carries the
counterpart's phone/email (`address`), so PersonalCRM assigns numbers to people
without you typing anything — the "pull it from the phone" path. Anyone the
messages only gave a name for can still be given a number by hand in the CRM.

Privacy: this script only READS your messages and WRITES one local JSON file.
Nothing is uploaded anywhere. The output is the standard Desmond export shape
(the same messages.json every Desmond reader emits), validated before writing.
"""

import argparse
import json
import os
import sys

# parse_export is pure (no device dependencies) — safe to import up front. The
# device readers in desmond_sources are imported lazily, only when a live source
# is actually requested, so `--from` and the unit tests never need them.
from desmond_federate import parse_export

DEFAULT_OUT = os.path.expanduser("~/Downloads/personalcrm_import.json")

# The exact per-message fields PersonalCRM's importer reads. We keep the export
# lean by dropping the fields the CRM ignores (reactions, per-message word
# counts, effects, …) while preserving everything the adapter maps.
CRM_MESSAGE_FIELDS = (
    "timestamp",
    "conversation",
    "conversation_type",
    "address",
    "sender",
    "is_from_me",
    "message_type",
    "text",
    "has_attachment",
)


def build_crm_export(export):
    """Turn a Desmond export (dict, JSON text, or bytes) into the lean,
    self-identifying payload PersonalCRM imports. Pure — no I/O — so it is
    trivially testable and reusable from other apps.
    """
    export = parse_export(export)  # validates: must be a dict with a messages list
    messages = []
    for m in export.get("messages", []):
        if not isinstance(m, dict):
            continue
        row = {
            "timestamp": m.get("timestamp"),
            "conversation": m.get("conversation") or "Unknown",
            "conversation_type": m.get("conversation_type") or "direct",
            "address": m.get("address") or "",
            "sender": m.get("sender"),
            "is_from_me": bool(m.get("is_from_me")),
            "message_type": m.get("message_type") or "text",
            "text": m.get("text") or "",
            "has_attachment": bool(m.get("has_attachment")),
        }
        messages.append(row)

    return {
        "app": "personalcrm",
        "schema": "desmond-crm-export/v1",
        "export_date": export.get("export_date"),
        "source": export.get("source", "Desmond"),
        "total_messages": len(messages),
        "total_conversations": export.get("total_conversations"),
        "conversations": export.get("conversations", []),
        "messages": messages,
    }


def load_export_from_path(path):
    """Read a Desmond export from a `messages.json` file OR an export folder
    that contains one. Returns the validated export dict."""
    path = os.path.expanduser(path)
    json_path = os.path.join(path, "messages.json") if os.path.isdir(path) else path
    if not os.path.exists(json_path):
        raise SystemExit(
            f"No messages.json found at {path!r}. Point --from at a Desmond "
            "export folder or its messages.json, or run an exporter first."
        )
    with open(json_path, encoding="utf-8") as f:
        return parse_export(f.read(), source=json_path)


def read_live_source(args):
    """Read from a plugged-in / local live source using desmond_sources.
    Imported lazily so `--from` and tests don't pull in the device readers."""
    import desmond_sources as sources

    if args.mac:
        _exit_if_full_disk_access_missing(sources)
        return sources.read_mac_messages()
    if args.iphone is not None:
        if args.iphone:  # explicit folder given
            return sources.read_iphone_backup(os.path.expanduser(args.iphone))
        backups = sources.find_iphone_backups()
        if not backups:
            raise SystemExit(
                "No iPhone backup found. Plug the iPhone in and make a LOCAL "
                "backup (Finder on Mac; iTunes/Apple Devices on Windows — untick "
                "'Encrypt local backup'), then try again."
            )
        return sources.read_iphone_backup(backups[0]["path"])
    if args.android:
        return sources.read_android_usb()
    return _auto_source(sources)


FDA_EXIT_CODE = 3   # "Full Disk Access needed" — the launchers key off this


def _exit_if_full_disk_access_missing(sources, avail=None):
    """chat.db is there but macOS won't let Terminal read it: print the one
    fix that works and stop with exit code 3 (a generic "no messages" would
    send the user down the wrong path)."""
    if avail is None:
        needs = sources.messages_db_state() == "no_access"
    else:
        needs = bool(avail.get("mac_messages_needs_full_disk_access"))
    if not needs:
        return
    print(sources.FDA_FIX_MESSAGE)
    if sys.platform == "darwin":
        try:  # open the exact settings pane so nothing needs to be hunted for
            import subprocess
            subprocess.run(["open", "x-apple.systempreferences:com.apple."
                            "preference.security?Privacy_AllFiles"], check=False)
        except Exception:
            pass
    raise SystemExit(FDA_EXIT_CODE)


def _auto_source(sources):
    """No source flag given: use the first thing this computer can read."""
    avail = sources.detect_available()
    _exit_if_full_disk_access_missing(sources, avail)
    if avail.get("mac_messages"):
        return sources.read_mac_messages()
    if avail.get("iphone_backups"):
        return sources.read_iphone_backup(avail["iphone_backups"][0]["path"])
    if avail.get("android_devices"):
        return sources.read_android_usb()
    raise SystemExit(
        "Couldn't find any messages to read on this computer.\n"
        "  • On a Mac, open Messages once so it syncs, then rerun.\n"
        "  • Or plug in an iPhone and make a LOCAL (unencrypted) backup, then --iphone.\n"
        "  • Or plug in an Android with USB debugging on, then --android.\n"
        "  • Or point --from at an export you already made."
    )


def _get_source_export(args):
    """Resolve the requested source into a Desmond export dict, turning the
    'source exists but can't be read' errors into friendly one-liners."""
    if args.from_path:
        return load_export_from_path(args.from_path)
    try:
        return read_live_source(args)
    except SystemExit:
        raise
    except Exception as e:
        # desmond_sources raises SourceError (and friends) with plain-language
        # guidance already; surface it as-is instead of a traceback.
        raise SystemExit(str(e))


OUT_BASENAME = "personalcrm_import.json"


def resolve_out_path(out):
    """`--out` may name a file OR a folder (an existing directory, or a path
    ending in a slash): a folder gets personalcrm_import.json inside it."""
    out = os.path.expanduser(out)
    if os.path.isdir(out) or out.endswith(os.sep) or out.endswith("/"):
        out = os.path.join(out, OUT_BASENAME)
    return out


def write_json_atomic(path, payload):
    """Write the file in one step: a crash or Ctrl-C mid-write leaves the old
    file (or nothing) rather than a half-written JSON PersonalCRM rejects."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Export your text messages to a personalcrm_import.json that "
        "PersonalCRM can import.")
    src = ap.add_argument_group("source (pick one; omit to auto-detect)")
    src.add_argument("--mac", action="store_true",
                     help="This Mac's Messages database (chat.db).")
    src.add_argument("--iphone", nargs="?", const="", metavar="DIR",
                     help="A plugged-in iPhone's local backup (newest, or DIR).")
    src.add_argument("--android", action="store_true",
                     help="A plugged-in Android phone over USB (debugging on).")
    src.add_argument("--from", dest="from_path", metavar="PATH",
                     help="An existing Desmond export folder or messages.json.")
    ap.add_argument("--out", metavar="PATH", default=DEFAULT_OUT,
                    help=f"Where to write the import file (default {DEFAULT_OUT}).")
    args = ap.parse_args(argv)

    export = _get_source_export(args)
    payload = build_crm_export(export)

    out = resolve_out_path(args.out)
    write_json_atomic(out, payload)

    print(f"✅ Wrote {payload['total_messages']:,} messages across "
          f"{len(payload['conversations']):,} conversations to:\n   {out}")
    print("\nNext: open PersonalCRM → Settings → \"Text Message Import (Desmond)\" "
          "and upload that file.")
    print("(Nothing was uploaded anywhere — this is a local file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
