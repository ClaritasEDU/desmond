#!/usr/bin/env python3
"""
Desmond — One-shot full export (text + media, inline, in date order).

A SINGLE command that exports your WHOLE iMessage history into one browsable
archive. Open `index.html`, click a conversation, and read the entire thread with
photos and videos shown **inline, in date/time order** — the real attachment
files are copied in too. The whole archive is saved **locally and mirrored to
Google Drive**, then **verified** (device vs local vs Drive).

    python3 desmond_export.py                  # everything → local + Google Drive
    python3 desmond_export.py --photos-videos  # images & videos only
    python3 desmond_export.py --newest         # newest messages first
    python3 desmond_export.py --no-drive       # keep the local copy only
    python3 desmond_export.py --retry          # loop until local & Drive match

Output (one folder, one entry point):

    Desmond_Message_Archive/
    ├── index.html                       # ← open this; lists every conversation
    ├── conversations/
    │   └── <Person>/
    │       ├── conversation.html        # full thread, media inline, date-ordered
    │       └── attachments/             # the real files
    ├── attachments.json / .csv          # manifest (also powers verify)
    └── VERIFY_REPORT.md                 # device vs local vs Drive

Runs on a Mac with Messages. Reads the database read-only — never modifies it.
"""

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import quote

import imessage_picker as pick
import imessage_attachments as attach
import desmond_log as dlog

ARCHIVE_NAME = "Desmond_Message_Archive"


def default_local_dir():
    return os.path.expanduser(f"~/Downloads/{ARCHIVE_NAME}")


INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Desmond — Message Archive</title>
<style>
 body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e7eaf0;}
 .wrap{max-width:820px;margin:0 auto;padding:24px 18px 80px;}
 h1{font-size:22px;margin:0 0 2px;} .sub{color:#9aa3b2;font-size:13px;margin:0 0 16px;}
 input{width:100%;padding:10px 12px;background:#0f1115;color:#e7eaf0;border:1px solid #2a2f3a;border-radius:9px;font-size:15px;margin-bottom:12px;}
 a.row{display:flex;gap:12px;align-items:baseline;padding:10px 12px;border:1px solid #2a2f3a;border-radius:10px;margin-bottom:8px;text-decoration:none;color:#e7eaf0;}
 a.row:hover{border-color:#4f8cff;background:#161a22;}
 .nm{flex:1;font-weight:600;} .ct{color:#9aa3b2;font-size:12.5px;white-space:nowrap;text-align:right;}
 .miss{color:#d6a;}
 button{background:#22304a;color:#fff;border:1px solid #4f8cff;border-radius:8px;padding:8px 12px;font-size:13.5px;cursor:pointer;margin:6px 0;}
</style></head><body><div class="wrap">
<h1>📲 Message Archive</h1>
<p class="sub">__TOTALS__</p>
<input id="q" placeholder="Search conversations…" autocomplete="off">
<div id="list"></div>
<button id="more">Show more</button>
<p class="sub" id="count"></p>
</div>
<script>
const ROWS = __ROWS__;
const PAGE = 100;                 // default to 100 so a huge list won't choke
let filtered = [], shown = 0;
function esc(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));}
function rowEl(r){
  const a=document.createElement("a"); a.className="row"; a.href=r.href;
  const miss=r.missing?` · <span class="miss">${r.missing.toLocaleString()} offloaded</span>`:"";
  a.innerHTML=`<span class="nm">${esc(r.name)}</span>`
    +`<span class="ct">${r.count.toLocaleString()} msgs · ${r.attachments.toLocaleString()} media${miss}<br>${r.first} → ${r.last}</span>`;
  return a;
}
function appendPage(){
  const L=document.getElementById("list");
  const end=Math.min(shown+PAGE, filtered.length);
  for(let i=shown;i<end;i++) L.appendChild(rowEl(filtered[i]));
  shown=end; update();
}
function update(){
  const rem=filtered.length-shown, more=document.getElementById("more");
  more.style.display = rem>0 ? "" : "none";
  if(rem>0) more.textContent="Show next "+Math.min(PAGE,rem);
  document.getElementById("count").textContent =
    filtered.length ? ("showing "+shown.toLocaleString()+" of "+filtered.length.toLocaleString()+" conversations") : "";
}
function apply(q){
  q=(q||"").toLowerCase();
  filtered=ROWS.filter(r=>r.name.toLowerCase().includes(q));
  document.getElementById("list").innerHTML=""; shown=0; appendPage();
  if(!filtered.length){ document.getElementById("list").innerHTML='<p class="sub">No matches.</p>'; }
}
document.getElementById("q").oninput=e=>apply(e.target.value);
document.getElementById("more").onclick=()=>appendPage();
apply("");
</script></body></html>"""


def render_index(output_dir, rows, totals):
    payload = json.dumps(rows).replace("</", "<\\/")
    page = (INDEX_TEMPLATE
            .replace("__TOTALS__", pick._h(totals))
            .replace("__ROWS__", payload))
    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def build_archive(db_path, output_dir, order="oldest", photos_videos=False, verbose=True):
    """Single pass over Messages → per-conversation inline transcripts + copied
    attachments + an index + a manifest (which powers verify)."""
    pick.MESSAGES_DB = db_path
    pick._contacts_loaded = False
    pick.ensure_contacts()

    records = pick.gather({"range": "all", "direction": "both", "order": order,
                           "types": ["text", "attachments", "reactions"]})

    by_person = {}
    for r in records:
        by_person.setdefault(r["person"], []).append(r)

    os.makedirs(output_dir, exist_ok=True)
    manifest = []
    rows = []
    tot_msg = tot_att = tot_missing = 0

    for name, recs in by_person.items():
        safe = pick.safe_name(name) or "Unknown"
        cdir = os.path.join(output_dir, "conversations", safe)
        os.makedirs(cdir, exist_ok=True)

        media_by_id = {}
        att_saved = att_missing = 0
        for r in recs:
            media = []
            for a in (r.get("attachments") or []):
                if photos_videos and (a or {}).get("category") not in ("photo", "video"):
                    continue
                info = pick.copy_attachment(a, r, cdir)
                media.append(info)
                if info.get("missing"):
                    att_missing += 1
                else:
                    att_saved += 1
                    manifest.append({
                        "attachment_id": (a or {}).get("id"),
                        "conversation": name,
                        "date": r["date"], "time": r["time"],
                        "category": info.get("category"),
                        "original_name": info.get("name"),
                        "saved_path": os.path.join("conversations", safe, info["path"]),
                        "status": "copied",
                        "size_bytes": None,
                    })
            media_by_id[r["id"]] = media

        html_records = [{
            "person": r["person"], "date": r["date"], "time": r["time"],
            "timestamp": r["timestamp"], "sender": r["sender"],
            "is_from_me": r["is_from_me"], "message_type": r["message_type"],
            "text_plain": (r.get("text_plain")
                           or (r.get("text", "") if r["message_type"] == "reaction" else "")),
            "media": media_by_id[r["id"]],
        } for r in recs]

        first = min(r["date"] for r in recs)
        last = max(r["date"] for r in recs)
        summary = (f"{len(recs):,} messages · {first} → {last} · "
                   f"{att_saved:,} attachments"
                   + (f" · {att_missing:,} offloaded in iCloud" if att_missing else ""))
        with open(os.path.join(cdir, "conversation.html"), "w", encoding="utf-8") as f:
            f.write(pick.render_html(html_records, [name], summary, order))

        rows.append({"name": name, "count": len(recs), "first": first, "last": last,
                     "attachments": att_saved, "missing": att_missing,
                     "href": "conversations/" + quote(safe) + "/conversation.html"})
        tot_msg += len(recs)
        tot_att += att_saved
        tot_missing += att_missing

    rows.sort(key=lambda x: -x["count"])
    totals = (f"{len(rows):,} conversations · {tot_msg:,} messages · "
              f"{tot_att:,} attachments saved"
              + (f" · {tot_missing:,} offloaded in iCloud" if tot_missing else ""))
    render_index(output_dir, rows, totals)
    attach.write_manifests(manifest, output_dir)  # attachments.json/.csv/INDEX → verify

    if verbose:
        print(f"Built archive: {len(rows):,} conversations · {tot_msg:,} messages · "
              f"{tot_att:,} attachments"
              + (f" · {tot_missing:,} offloaded" if tot_missing else ""))
        print(f"  Open: {os.path.join(output_dir, 'index.html')}")
    return {"output_dir": output_dir, "conversations": len(rows),
            "messages": tot_msg, "attachments": tot_att, "offloaded": tot_missing}


def run_once(db_path, output_dir, order, photos_videos, expect_drive, drive_override,
             do_verify=True, logger=None):
    stats = build_archive(db_path, output_dir, order=order, photos_videos=photos_videos)
    if logger:
        logger.metric(conversations=stats["conversations"], messages=stats["messages"],
                      attachments=stats["attachments"], offloaded=stats["offloaded"])

    drive_folder = None
    if expect_drive:
        base = drive_override or attach.find_google_drive_dir()
        if logger:
            logger.log("info", "google drive detection", drive_detected=bool(base))
        if base:
            drive_folder = os.path.join(base, ARCHIVE_NAME)
            print(f"\nMirroring to Google Drive: {drive_folder}")
            n = attach.mirror_tree(output_dir, drive_folder)
            if logger:
                logger.metric(mirrored_files=n)
        else:
            print("\n(No Google Drive detected — saved locally only. Install Google "
                  "Drive for desktop or pass --drive PATH.)")

    if not do_verify:
        return None
    print("\nVerifying (device vs local vs Google Drive)…")
    res = attach.verify_archive(db_path=db_path, output_dir=output_dir,
                                drive_mirror=drive_folder, expect_drive=expect_drive)
    if logger and isinstance(res, dict):
        logger.metric(verify_complete=res.get("complete"),
                      in_local=res.get("in_local"), in_drive=res.get("in_drive"),
                      verify_offloaded=res.get("offloaded"),
                      missing_local=res.get("missing_local"),
                      missing_drive=res.get("missing_drive"))
    # Make sure the freshly written report is on Drive too.
    if drive_folder:
        for fn in ("VERIFY_REPORT.md", "verify_diff.json", "index.html"):
            src = os.path.join(output_dir, fn)
            if os.path.exists(src):
                try:
                    attach.shutil.copy2(src, os.path.join(drive_folder, fn))
                except Exception:
                    pass
    return res


def main():
    ap = argparse.ArgumentParser(
        description="One command: export all messages + media inline, local + "
                    "Google Drive, verified.")
    ap.add_argument("--dest", metavar="PATH",
                    help="Local archive folder (default ~/Downloads/Desmond_Message_Archive).")
    ap.add_argument("--drive", metavar="PATH",
                    help="Google Drive folder to mirror into (default: auto-detected).")
    ap.add_argument("--no-drive", action="store_true",
                    help="Keep the local copy only; don't mirror to Google Drive.")
    ap.add_argument("--photos-videos", action="store_true",
                    help="Only images and videos (skip audio/files).")
    ap.add_argument("--newest", action="store_true",
                    help="Show newest messages first (default: oldest first).")
    ap.add_argument("--retry", nargs="?", type=int, const=3, default=None, metavar="N",
                    help="Build + mirror + verify in a loop (default 3) until complete.")
    ap.add_argument("--no-verify", action="store_true", help="Skip verification.")
    ap.add_argument("--db", metavar="PATH", default=attach.MESSAGES_DB,
                    help="Path to chat.db (default: ~/Library/Messages/chat.db).")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"Could not find your Messages database at {args.db}")
        print("This runs on a Mac with Messages. If Terminal lacks access: System "
              "Settings → Privacy & Security → Full Disk Access → enable Terminal.")
        sys.exit(1)

    output_dir = os.path.expanduser(args.dest) if args.dest else default_local_dir()
    drive_override = os.path.expanduser(args.drive) if args.drive else None
    order = "newest" if args.newest else "oldest"
    expect_drive = not args.no_drive

    try:
        logger = dlog.RunLogger("desmond_export")
        logger.log("info", "args", order=order, photos_videos=args.photos_videos,
                   to_drive=expect_drive, dest=output_dir,
                   drive=(drive_override or ""), retry=args.retry)
    except Exception:
        logger = None

    status = "ok"
    done = False
    try:
        if args.retry is not None:
            attempts = max(1, args.retry)
            res = None
            for i in range(attempts):
                print(f"\n===== Attempt {i + 1} of {attempts} =====")
                if logger:
                    logger.log("info", "attempt", n=i + 1, of=attempts)
                res = run_once(args.db, output_dir, order, args.photos_videos,
                               expect_drive, drive_override, do_verify=True, logger=logger)
                if res and res.get("complete"):
                    print(f"\n✅ Archive complete after {i + 1} pass(es).")
                    break
            else:
                print("\n⚠️  Still incomplete — see VERIFY_REPORT.md. Download any "
                      "offloaded iCloud items, then run --retry again.")
            done = bool(res and res.get("complete"))
        else:
            res = run_once(args.db, output_dir, order, args.photos_videos,
                           expect_drive, drive_override, do_verify=not args.no_verify,
                           logger=logger)
            done = (res is None) or bool(res.get("complete"))
    except Exception as e:
        status = "error"
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("\nIf this is a permissions error: give Terminal Full Disk Access "
              "(System Settings → Privacy & Security), then restart Terminal.")
        if logger:
            logger.exception("fatal error")

    if logger:
        log_path = logger.close(status=status, output_dir=output_dir, complete=done)
        print(f"\n📝 Run log (safe to share — counts/env/errors only, no message "
              f"text or names): {log_path}")

    if status == "ok":
        try:
            subprocess.run(["open", os.path.join(output_dir, "index.html")], check=False)
        except Exception:
            pass
    sys.exit(0 if (done and status == "ok") else 1)


if __name__ == "__main__":
    main()
