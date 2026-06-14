#!/usr/bin/env python3
"""
Desmond Picker - a privacy-first browser interface for exporting iMessages.

Pick one or more people, choose a time range, then PREVIEW exactly what
would be exported before anything is written to disk. Trim it down with
content filters, keyword include/exclude, a message cap, redaction, and
per-message deselection. Only what you approve gets saved.

Output lands in ~/Downloads/iMessages_Export/_picks/.

Run with:  python3 imessage_picker.py
Opens automatically in your browser. Nothing is uploaded anywhere.
"""

import os
import re
import csv
import json
import shutil
import sqlite3
import webbrowser
import threading
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Reuse the battle-tested contact + timestamp helpers from the main exporter
import imessage_exporter as core
# Reuse Google Drive detection + filename helpers from the attachment archiver
import imessage_attachments as attach

MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")


def default_dest():
    """Default save location: a detected Google Drive folder, else ~/Downloads.

    Either way the picks land in a clearly named folder. With Google Drive for
    desktop installed, that folder auto-uploads; otherwise drag it to
    drive.google.com to upload manually.
    """
    drive = attach.find_google_drive_dir()
    base = drive if drive else os.path.expanduser("~/Downloads")
    return os.path.join(base, "Desmond_Message_Picks")


OUTPUT_DIR = default_dest()
PORT = 8765
ATTACH_SUBDIR = "attachments"
# Formats browsers can't show natively → also make a JPG copy (originals kept).
WEB_CONVERT_EXTS = {".heic", ".heif", ".tif", ".tiff"}

# Apple stores dates as nanoseconds since 2001-01-01
APPLE_EPOCH_OFFSET = 978307200

# How many messages to render in the preview pane (export is not limited by this)
PREVIEW_LIMIT = 1500

# Readable reaction labels for the transcript
REACTIONS = {
    2000: "loved", 2001: "liked", 2002: "disliked",
    2003: "laughed at", 2004: "emphasized", 2005: "questioned",
    3000: "removed loved", 3001: "removed liked", 3002: "removed disliked",
    3003: "removed laughed", 3004: "removed emphasized", 3005: "removed questioned",
}

RANGE_LABELS = {
    "1d": "Last 24 hours", "7d": "Last 7 days", "30d": "Last 30 days",
    "90d": "Last 90 days", "365d": "Last year", "all": "All time",
    "custom": "Custom range",
}

_contacts_loaded = False


def ensure_contacts():
    global _contacts_loaded
    if not _contacts_loaded:
        core.load_contacts()
        _contacts_loaded = True


# ---------------------------------------------------------------------------
# Redaction (Feature 2) — scrub sensitive info from exported text only.
# The Messages database itself is never modified.
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
ADDR_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.]+\s){1,4}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|"
    r"Court|Ct|Way|Place|Pl|Terrace|Ter|Circle|Cir|Highway|Hwy)\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"\(?\+?\d[\d\-\.\s\(\)]{7,}\d")
LONGNUM_RE = re.compile(r"\b\d{9,}\b")


def decode_attributed_body(data):
    """Extract message text from Apple's binary `attributedBody` field.

    Modern macOS frequently leaves `message.text` NULL and stores the actual
    text in `attributedBody` (an NSAttributedString typedstream). Without this,
    many sent messages and link/formatted messages get dropped entirely.
    """
    if not data:
        return None
    try:
        if isinstance(data, str):
            return data or None
        chunk = data.split(b"NSString")[1][5:]  # skip class metadata bytes
        if chunk[0] == 0x81:  # 2-byte little-endian length follows
            length = int.from_bytes(chunk[1:3], "little")
            chunk = chunk[3:]
        else:
            length = chunk[0]
            chunk = chunk[1:]
        text = chunk[:length].decode("utf-8", errors="ignore").strip()
        return text or None
    except Exception:
        return None


def redact_text(text):
    if not text:
        return text
    text = EMAIL_RE.sub("[email]", text)
    text = SSN_RE.sub("[id-number]", text)
    text = ADDR_RE.sub("[address]", text)
    text = PHONE_RE.sub(
        lambda m: "[phone]" if sum(c.isdigit() for c in m.group()) >= 7 else m.group(),
        text,
    )
    text = LONGNUM_RE.sub("[number]", text)
    return text


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def apple_cutoff(days_back):
    cutoff = datetime.now() - timedelta(days=days_back)
    return int((cutoff.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def apple_from_date(date_str, end_of_day=False):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt + timedelta(days=1)  # inclusive of the end date
    return int((dt.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def resolve_range(range_key, start=None, end=None):
    days = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365}
    if range_key in days:
        return apple_cutoff(days[range_key]), None
    if range_key == "custom":
        since = apple_from_date(start) if start else None
        until = apple_from_date(end, end_of_day=True) if end else None
        return since, until
    return None, None  # "all"


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------
def open_db():
    return sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)


def conversation_name(handle_id, chat_id, display_name, cursor):
    """Derive a human-readable conversation name (matches the main exporter)."""
    if display_name:
        return display_name, "group"
    if chat_id:
        name = core.lookup_contact_name(chat_id)
        if name == chat_id or str(name).startswith("chat"):
            participants = core.get_chat_participants(chat_id, cursor)
            if participants:
                return participants, "group"
            return chat_id, "unknown"
        return name, "direct"
    if handle_id:
        return core.get_contact_name(handle_id, cursor), "direct"
    return "Unknown", "direct"


def iter_messages(cursor, since_apple=None, until_apple=None):
    clauses, params = [], []
    if since_apple is not None:
        clauses.append("message.date >= ?")
        params.append(since_apple)
    if until_apple is not None:
        clauses.append("message.date < ?")
        params.append(until_apple)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cursor.execute(f"""
        SELECT message.ROWID, message.text, message.date, message.is_from_me,
               message.handle_id, message.associated_message_type,
               message.balloon_bundle_id, chat.chat_identifier, chat.display_name,
               message.attributedBody
        FROM message
        LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
        {where}
        ORDER BY message.date ASC
    """, params)
    return cursor.fetchall()


def attachments_for(cursor):
    """Map message_id -> list of attachment dicts (category + real file path)."""
    cursor.execute("""
        SELECT message_attachment_join.message_id, attachment.mime_type,
               attachment.filename, attachment.transfer_name
        FROM attachment
        JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id
    """)
    result = defaultdict(list)
    for msg_id, mime_type, filename, transfer_name in cursor.fetchall():
        if mime_type and mime_type.startswith("image"):
            category = "photo"
        elif mime_type and mime_type.startswith("video"):
            category = "video"
        elif mime_type and mime_type.startswith("audio"):
            category = "audio"
        else:
            category = "file"
        result[msg_id].append({
            "category": category, "mime": mime_type,
            "filename": filename, "transfer_name": transfer_name,
        })
    return result


def list_people():
    """Return [{name, type, count, last}] for every conversation, busiest first."""
    ensure_contacts()
    conn = open_db()
    cursor = conn.cursor()
    meta = {}
    for row in iter_messages(cursor):
        _, _, date, _, handle_id, _, _, chat_id, display_name, _ = row
        dt = core.convert_apple_time(date)
        if dt is None:
            continue
        name, ctype = conversation_name(handle_id, chat_id, display_name, cursor)
        name = str(name)
        entry = meta.get(name)
        if entry is None:
            meta[name] = {"name": name, "type": ctype, "count": 1, "last": dt.isoformat()}
        else:
            entry["count"] += 1
            if dt.isoformat() > entry["last"]:
                entry["last"] = dt.isoformat()
    conn.close()
    return sorted(meta.values(), key=lambda x: x["count"], reverse=True)


# ---------------------------------------------------------------------------
# Gathering (applies Features 2-5 + multi-person + direction)
# ---------------------------------------------------------------------------
def make_record(row, att, cursor, person, want_text, want_att, want_react):
    rowid, text, date, is_from_me, handle_id, assoc, balloon, chat_id, display_name, attributed = row
    dt = core.convert_apple_time(date)
    if dt is None:
        return None

    # Recover text that Apple stashed in attributedBody instead of message.text
    if not text:
        text = decode_attributed_body(attributed)

    sender = "Me" if is_from_me else (
        core.get_contact_name(handle_id, cursor) if handle_id else "Unknown")
    atts = att.get(rowid, [])

    if assoc and assoc in REACTIONS:
        if not want_react:
            return None
        reaction = REACTIONS[assoc]
        content, mtype = (text or reaction), "reaction"
        text_plain = content
        cats, files = [], []
    else:
        reaction = None
        cats = [a["category"] for a in atts]
        text_part = text if (text and want_text) else ""
        att_part = " ".join(f"[{c}]" for c in cats) if (cats and want_att) else ""
        if text_part and att_part:
            content, mtype = f"{text_part} {att_part}", "text_with_attachment"
        elif text_part:
            content, mtype = text_part, "text"
        elif att_part:
            content, mtype = att_part, "attachment"
        else:
            return None
        text_plain = text_part
        cats = cats if want_att else []
        files = atts if want_att else []

    return {
        "id": rowid,
        "person": person,
        "timestamp": dt.isoformat(),
        "date": dt.strftime("%Y-%m-%d"),
        "time": dt.strftime("%H:%M:%S"),
        "sender": sender,
        "is_from_me": bool(is_from_me),
        "message_type": mtype,
        "text": content,
        "text_plain": text_plain,
        "has_attachment": len(files) > 0,
        "attachment_types": cats,
        "attachments": files,
        "reaction": reaction,
    }


def gather(f):
    """Run all filters and return the list of approved-by-rule records."""
    ensure_contacts()
    people = set(f.get("people") or [])
    since, until = resolve_range(f.get("range", "7d"), f.get("start") or None, f.get("end") or None)
    direction = f.get("direction", "both")
    types = set(f.get("types") or ["text", "attachments", "reactions"])
    want_text = "text" in types
    want_att = "attachments" in types
    want_react = "reactions" in types
    include = [k.strip().lower() for k in (f.get("include") or "").split(",") if k.strip()]
    exclude = [k.strip().lower() for k in (f.get("exclude") or "").split(",") if k.strip()]
    redact = bool(f.get("redact"))
    cap = f.get("cap")
    cap = int(cap) if str(cap or "").strip().isdigit() else None

    conn = open_db()
    cursor = conn.cursor()
    att = attachments_for(cursor)

    out = []
    for row in iter_messages(cursor, since, until):
        _, _, _, is_from_me, handle_id, _, _, chat_id, display_name, _ = row
        name, _ = conversation_name(handle_id, chat_id, display_name, cursor)
        name = str(name)
        if people and name not in people:
            continue
        if direction == "mine" and not is_from_me:
            continue
        if direction == "theirs" and is_from_me:
            continue
        rec = make_record(row, att, cursor, name, want_text, want_att, want_react)
        if rec is None:
            continue
        hay = rec["text"].lower()
        if include and not any(k in hay for k in include):
            continue
        if exclude and any(k in hay for k in exclude):
            continue
        if redact:
            rec["text"] = redact_text(rec["text"])
            rec["redacted"] = True
        out.append(rec)
    conn.close()

    out.sort(key=lambda r: r["timestamp"])
    if cap and len(out) > cap:
        out = out[-cap:]  # most recent N
    return out


def safe_name(name):
    return "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in str(name)).strip()


def filter_summary(f):
    bits = [RANGE_LABELS.get(f.get("range", "7d"), f.get("range", ""))]
    d = {"both": "both directions", "mine": "only my messages", "theirs": "only their messages"}
    bits.append(d.get(f.get("direction", "both"), ""))
    bits.append("includes: " + ", ".join(f.get("types") or []))
    if f.get("cap"):
        bits.append(f"most recent {f['cap']}")
    if f.get("include"):
        bits.append(f"only containing: {f['include']}")
    if f.get("exclude"):
        bits.append(f"excluding: {f['exclude']}")
    bits.append("redacted" if f.get("redact") else "not redacted")
    return " · ".join(b for b in bits if b)


def apply_order(records, order):
    """Records arrive oldest→newest; return them in the requested order."""
    return list(reversed(records)) if order == "newest" else list(records)


def _h(text):
    return (str(text or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _att_filename(rec, original):
    """Date/time stamp FIRST, then the people in the chat, then the original name."""
    when = f"{rec.get('date', '')}_{rec.get('time', '')[:5].replace(':', '')}"
    people = safe_name(rec.get("person") or "Unknown")[:40]
    return attach.safe_name_keep_ext(f"{when}_{people}_{original}")


def copy_attachment(a, rec, folder):
    """Copy ONE real attachment into <folder>/attachments/, preserving the
    original file byte-for-byte. Returns a display dict for the transcript."""
    src = os.path.expanduser((a or {}).get("filename") or "")
    original = ((a or {}).get("transfer_name")
               or (os.path.basename(src) if src else "") or "file")
    category = (a or {}).get("category") or "file"
    if not src or not os.path.exists(src):
        return {"category": category, "name": original, "missing": True}
    adir = os.path.join(folder, ATTACH_SUBDIR)
    os.makedirs(adir, exist_ok=True)
    dest = os.path.join(adir, _att_filename(rec, original))
    if os.path.exists(dest):
        root, ext = os.path.splitext(dest)
        dest = f"{root}_{abs(hash(src)) % 100000}{ext}"
    try:
        shutil.copy2(src, dest)  # byte-for-byte copy of the original + its mtime
    except Exception:
        return {"category": category, "name": original, "missing": True}
    rel = os.path.relpath(dest, folder).replace(os.sep, "/")
    display = rel
    if os.path.splitext(dest)[1].lower() in WEB_CONVERT_EXTS:
        # Also make a JPG so HEIC/TIFF show in any browser; the original is kept.
        jpg = os.path.splitext(dest)[0] + ".jpg"
        try:
            subprocess.run(["sips", "-s", "format", "jpeg", dest, "--out", jpg],
                           check=True, capture_output=True)
            display = os.path.relpath(jpg, folder).replace(os.sep, "/")
        except Exception:
            pass
    return {"category": category, "name": original, "missing": False,
            "mime": (a or {}).get("mime"), "path": rel, "display": display}


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
 body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e7eaf0;}
 .wrap{max-width:820px;margin:0 auto;padding:24px 18px 80px;}
 h1{font-size:22px;margin:0 0 4px;}
 .sub{color:#9aa3b2;font-size:13px;margin:0 0 14px;}
 .bar{position:sticky;top:0;background:#0f1115cc;backdrop-filter:blur(6px);padding:10px 0;border-bottom:1px solid #2a2f3a;margin-bottom:6px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:5;}
 button{background:#22304a;color:#fff;border:1px solid #4f8cff;border-radius:8px;padding:8px 12px;font-size:13.5px;cursor:pointer;}
 .person{margin:24px 0 4px;font-size:16px;font-weight:700;color:#4f8cff;border-top:1px solid #2a2f3a;padding-top:16px;}
 .day{color:#9aa3b2;font-size:12px;text-transform:uppercase;letter-spacing:.04em;margin:16px 0 6px;}
 .m{padding:7px 0;border-bottom:1px solid #181b22;}
 .m .meta{color:#9aa3b2;font-size:11.5px;margin-bottom:2px;}
 .m .who{color:#4f8cff;font-weight:600;} .m.me .who{color:#2e9d6f;}
 .m .txt{white-space:pre-wrap;}
 .m.react .txt{color:#9aa3b2;font-style:italic;}
 img.att{display:block;margin:6px 0;max-width:340px;max-height:340px;border-radius:10px;border:1px solid #2a2f3a;}
 video.att,audio.att{display:block;margin:6px 0;width:340px;max-width:100%;}
 .miss{color:#d6a;} a.file{color:#4f8cff;}
</style></head><body><div class="wrap">
<h1>__TITLE__</h1>
<p class="sub">__SUMMARY__</p>
<div class="bar">
  <button id="toggle">↕ Order: <b id="ord"></b></button>
  <span class="sub" id="count" style="margin:0"></span>
</div>
<div id="out"></div>
</div>
<script>
const RECORDS = __RECORDS__;
let order = "__DEFAULT_ORDER__";
function esc(s){return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function media(m){
  if(m.missing) return '<div class="miss">['+esc(m.category)+' — not downloaded from iCloud]</div>';
  const p=esc(m.path), d=esc(m.display||m.path);
  if(m.category==="photo") return '<a href="'+p+'" target="_blank"><img class="att" loading="lazy" src="'+d+'"></a>';
  if(m.category==="video") return '<video class="att" controls preload="metadata" src="'+p+'"></video>';
  if(m.category==="audio") return '<audio class="att" controls src="'+p+'"></audio>';
  return '<a class="file" href="'+p+'" target="_blank">📎 '+esc(m.name)+'</a>';
}
function render(){
  document.getElementById("ord").textContent = (order==="newest"?"newest first":"oldest first");
  const recs = RECORDS.slice().sort((a,b)=> a.timestamp<b.timestamp?-1:(a.timestamp>b.timestamp?1:0));
  if(order==="newest") recs.reverse();
  const out=document.getElementById("out"); out.innerHTML="";
  const byPerson={}, pOrder=[];
  recs.forEach(r=>{ if(!(r.person in byPerson)){byPerson[r.person]=[];pOrder.push(r.person);} byPerson[r.person].push(r); });
  pOrder.forEach(person=>{
    const h=document.createElement("div"); h.className="person"; h.textContent=person; out.appendChild(h);
    let lastDay="";
    byPerson[person].forEach(r=>{
      if(r.date!==lastDay){ lastDay=r.date; const d=document.createElement("div"); d.className="day"; d.textContent=r.date; out.appendChild(d); }
      const m=document.createElement("div");
      m.className="m"+(r.is_from_me?" me":"")+(r.message_type==="reaction"?" react":"");
      let html='<div class="meta">'+esc(r.time.slice(0,5))+' · <span class="who">'+esc(r.sender)+'</span></div>';
      if(r.text_plain) html+='<div class="txt">'+esc(r.text_plain)+'</div>';
      (r.media||[]).forEach(mm=> html+=media(mm));
      m.innerHTML=html; out.appendChild(m);
    });
  });
  document.getElementById("count").textContent = recs.length.toLocaleString()+" messages";
}
document.getElementById("toggle").onclick=()=>{ order=(order==="newest"?"oldest":"newest"); render(); };
render();
</script></body></html>"""


def render_html(records, people, summary, default_order):
    title = "Messages — " + (", ".join(people) if people else "export")
    payload = json.dumps(records).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__TITLE__", _h(title))
            .replace("__SUMMARY__", _h(summary))
            .replace("__RECORDS__", payload)
            .replace("__DEFAULT_ORDER__", "newest" if default_order == "newest" else "oldest"))


def export_records(records, people, f):
    """Write the approved messages as an inline-media HTML transcript (plus
    markdown / JSON / CSV), copying the real attachments alongside. Returns a
    result dict."""
    if not records:
        return {"ok": False, "error": "Nothing to export — every message was filtered out or deselected."}

    order = f.get("order", "oldest")
    include_att = "attachments" in (f.get("types") or [])
    dest_root = os.path.expanduser(f.get("dest") or "") or OUTPUT_DIR

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = safe_name(people[0]) if len(people) == 1 else f"{len(people)}_people"
    folder = os.path.join(dest_root, f"{label}_{f.get('range', 'range')}_{stamp}")
    os.makedirs(folder, exist_ok=True)

    records = apply_order(records, order)

    # Copy the real attachment files; build per-message media lists.
    att_saved = att_missing = 0
    media_by_id = {}
    for r in records:
        media = []
        if include_att:
            for a in (r.get("attachments") or []):
                info = copy_attachment(a, r, folder)
                media.append(info)
                if info.get("missing"):
                    att_missing += 1
                else:
                    att_saved += 1
        media_by_id[r["id"]] = media

    summary = filter_summary(f)
    if include_att:
        summary += f" · {att_saved} attachments saved"
        if att_missing:
            summary += f" ({att_missing} not downloaded)"

    dates = [r["date"] for r in records]
    first_date, last_date = min(dates), max(dates)

    # 1. HTML transcript — inline photos/videos, newest/oldest toggle.
    html_records = [{
        "person": r["person"], "date": r["date"], "time": r["time"],
        "timestamp": r["timestamp"], "sender": r["sender"],
        "is_from_me": r["is_from_me"], "message_type": r["message_type"],
        "text_plain": (r.get("text_plain")
                       or (r.get("text", "") if r["message_type"] == "reaction" else "")),
        "media": media_by_id[r["id"]],
    } for r in records]
    with open(os.path.join(folder, "conversation.html"), "w", encoding="utf-8") as hf:
        hf.write(render_html(html_records, people, summary, order))

    # 2. Readable markdown — grouped by person then day, in the chosen order.
    by_person = defaultdict(list)
    for r in records:
        by_person[r["person"]].append(r)
    with open(os.path.join(folder, "conversation.md"), "w", encoding="utf-8") as md:
        md.write("# Message export\n\n")
        md.write(f"**People:** {', '.join(people)}  \n")
        md.write(f"**Messages:** {len(records):,}  \n")
        md.write(f"**Filters:** {summary}  \n")
        md.write(f"**Order:** {'newest first' if order == 'newest' else 'oldest first'}  \n")
        md.write(f"**Range:** {first_date} to {last_date}\n")
        for person, rows in by_person.items():
            md.write(f"\n---\n\n# {person}\n")
            last_day = None
            for r in rows:
                if r["date"] != last_day:
                    md.write(f"\n## {r['date']}\n\n")
                    last_day = r["date"]
                body = f"*{r.get('text', '')}*" if r["message_type"] == "reaction" else (r.get("text_plain") or "")
                md.write(f"**{r['time'][:5]} — {r['sender']}:** {body}\n\n")
                for m in media_by_id[r["id"]]:
                    if m.get("missing"):
                        md.write(f"  - _[{m['category']} — not downloaded from iCloud]_\n")
                    elif m["category"] == "photo":
                        md.write(f"  ![{m['name']}]({m['display']})\n")
                    else:
                        md.write(f"  - [📎 {m['name']}]({m['path']})\n")

    # 3. JSON (with media paths)
    json_messages = []
    for r in records:
        d = {k: r.get(k) for k in ("id", "timestamp", "date", "time", "person",
             "sender", "is_from_me", "message_type", "text", "text_plain",
             "attachment_types", "reaction")}
        d["media"] = media_by_id[r["id"]]
        json_messages.append(d)
    with open(os.path.join(folder, "messages.json"), "w", encoding="utf-8") as jf:
        json.dump({
            "people": people, "filters": summary, "order": order,
            "exported": datetime.now().isoformat(),
            "message_count": len(records),
            "attachments_saved": att_saved, "attachments_missing": att_missing,
            "messages": json_messages,
        }, jf, indent=2)

    # 4. CSV
    fields = ["timestamp", "date", "time", "person", "sender", "is_from_me",
              "message_type", "text", "has_attachment", "attachment_types",
              "reaction", "attachment_files"]
    with open(os.path.join(folder, "messages.csv"), "w", newline="", encoding="utf-8") as cf:
        writer = csv.DictWriter(cf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = r.copy()
            row["attachment_types"] = ",".join(row.get("attachment_types") or [])
            row["attachment_files"] = ",".join(
                m["path"] for m in media_by_id[r["id"]] if not m.get("missing"))
            writer.writerow(row)

    try:
        subprocess.run(["open", folder], check=False)
    except Exception:
        pass

    return {"ok": True, "count": len(records), "folder": folder,
            "first": first_date, "last": last_date,
            "attachments_saved": att_saved, "attachments_missing": att_missing}


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Desmond — Message Picker</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --line:#2a2f3a; --txt:#e7eaf0; --mut:#9aa3b2; --accent:#4f8cff; --ok:#2e7d4f; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .wrap { max-width:680px; margin:0 auto; padding:28px 18px 80px; }
  h1 { font-size:24px; margin:0 0 2px; }
  .sub { color:var(--mut); margin:0 0 22px; font-size:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; margin-bottom:16px; }
  .card h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--mut); margin:0 0 12px; }
  input[type=text], input[type=number], input[type=date], select { width:100%; padding:10px 12px; background:#0f1115;
    color:var(--txt); border:1px solid var(--line); border-radius:9px; font-size:15px; }
  .row { display:flex; gap:10px; }
  .row > div { flex:1; }
  .mut { color:var(--mut); font-size:12.5px; margin-top:8px; }
  /* people picker */
  #plist { max-height:210px; overflow:auto; margin-top:10px; border:1px solid var(--line); border-radius:9px; }
  .prow { display:flex; align-items:center; gap:10px; padding:8px 12px; cursor:pointer; border-bottom:1px solid #20242d; }
  .prow:last-child { border-bottom:0; }
  .prow:hover { background:#20242d; }
  .prow .nm { flex:1; }
  .prow .ct { color:var(--mut); font-size:12px; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .chip { background:var(--accent); color:#fff; border-radius:20px; padding:4px 10px; font-size:12.5px; }
  /* ranges */
  .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:9px; }
  .opt { padding:10px; border:1px solid var(--line); border-radius:9px; text-align:center; cursor:pointer; user-select:none; transition:.12s; font-size:13.5px; }
  .opt:hover { border-color:var(--accent); }
  .opt.on { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
  .custom { display:none; gap:10px; margin-top:10px; }
  .custom.show { display:flex; }
  .toggles { display:flex; flex-wrap:wrap; gap:9px; }
  .tg { padding:9px 13px; border:1px solid var(--line); border-radius:9px; cursor:pointer; user-select:none; font-size:13.5px; }
  .tg.on { background:#22304a; border-color:var(--accent); color:#fff; }
  .seg { display:flex; border:1px solid var(--line); border-radius:9px; overflow:hidden; }
  .seg div { flex:1; text-align:center; padding:10px; cursor:pointer; font-size:13.5px; }
  .seg div.on { background:var(--accent); color:#fff; font-weight:600; }
  label.lbl { display:block; font-size:12.5px; color:var(--mut); margin-bottom:5px; }
  button { width:100%; padding:14px; background:var(--accent); color:#fff; border:0; border-radius:10px; font-size:16px; font-weight:600; cursor:pointer; }
  button.ghost { background:#222732; border:1px solid var(--line); }
  button:disabled { opacity:.5; cursor:default; }
  .bar { display:flex; gap:10px; }
  /* preview */
  #preview { display:none; }
  .pvhead { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; flex-wrap:wrap; }
  .pvhead .small { font-size:12.5px; color:var(--mut); }
  .msgs { max-height:380px; overflow:auto; border:1px solid var(--line); border-radius:9px; }
  .m { display:flex; gap:10px; padding:8px 12px; border-bottom:1px solid #20242d; font-size:13.5px; }
  .m:last-child { border-bottom:0; }
  .m .meta { color:var(--mut); font-size:11.5px; white-space:nowrap; }
  .m.off { opacity:.4; }
  .m .body b { color:var(--accent); }
  .result { padding:15px; border-radius:10px; margin-top:16px; display:none; }
  .result.ok { display:block; background:#16321f; border:1px solid var(--ok); }
  .result.err { display:block; background:#321616; border:1px solid #a33; }
  .result code { background:#0008; padding:2px 6px; border-radius:5px; word-break:break-all; }
  .linkbtn { background:none; border:0; color:var(--accent); cursor:pointer; font-size:12.5px; width:auto; padding:0; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📲 Desmond</h1>
  <p class="sub">Pick people and a range, preview exactly what would leave Messages, trim it, then save.</p>

  <div class="card">
    <h2>1 · Who</h2>
    <input type="text" id="search" placeholder="Search conversations…" autocomplete="off">
    <div class="chips" id="chips"></div>
    <div id="plist"><div class="mut" style="padding:12px">Loading conversations…</div></div>
    <div class="mut" id="phint"></div>
  </div>

  <div class="card">
    <h2>2 · How far back</h2>
    <div class="grid" id="ranges">
      <div class="opt" data-r="1d">Last 24 hours</div>
      <div class="opt on" data-r="7d">Last 7 days</div>
      <div class="opt" data-r="30d">Last 30 days</div>
      <div class="opt" data-r="90d">Last 90 days</div>
      <div class="opt" data-r="365d">Last year</div>
      <div class="opt" data-r="all">All time</div>
    </div>
    <div class="opt" data-r="custom" style="margin-top:9px">Custom date range</div>
    <div class="custom" id="custom">
      <div><label class="lbl">From</label><input type="date" id="start"></div>
      <div><label class="lbl">To</label><input type="date" id="end"></div>
    </div>
  </div>

  <div class="card">
    <h2>3 · Limit what's included</h2>
    <label class="lbl">Content types</label>
    <div class="toggles" id="types">
      <div class="tg on" data-t="text">Text</div>
      <div class="tg on" data-t="attachments">📎 Photos / videos / files</div>
      <div class="tg on" data-t="reactions">Reactions</div>
    </div>
    <div class="mut">Turn on “Photos / videos / files” to copy the real
      attachments into the export (originals preserved) and show them inline in
      the transcript.</div>
    <label class="lbl" style="margin-top:14px">Direction</label>
    <div class="seg" id="dir">
      <div class="on" data-d="both">Both</div>
      <div data-d="mine">Only me</div>
      <div data-d="theirs">Only them</div>
    </div>
    <label class="lbl" style="margin-top:14px">Order (toggle anytime in the saved file too)</label>
    <div class="seg" id="order">
      <div class="on" data-o="oldest">Oldest first</div>
      <div data-o="newest">Newest first</div>
    </div>
    <div class="row" style="margin-top:14px">
      <div>
        <label class="lbl">Only messages containing (comma-sep)</label>
        <input type="text" id="include" placeholder="e.g. dinner, trip">
      </div>
      <div>
        <label class="lbl">Exclude messages containing</label>
        <input type="text" id="exclude" placeholder="e.g. password, ssn">
      </div>
    </div>
    <div class="row" style="margin-top:14px">
      <div>
        <label class="lbl">Most recent N (blank = no limit)</label>
        <input type="number" id="cap" min="1" placeholder="e.g. 500">
      </div>
      <div>
        <label class="lbl">Privacy</label>
        <div class="tg" id="redact" style="text-align:center" data-on="0">🔒 Scrub phones, emails, addresses</div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>4 · Where to save</h2>
    <input type="text" id="dest" value="__DEFAULT_DEST__">
    <div class="mut">Defaults to <b>Google Drive</b> if Google Drive for desktop
      is installed (it then uploads automatically) — otherwise to your
      <b>Downloads</b> folder, which you can drag to drive.google.com. Either way
      the whole export, attachments included, lives in this one folder.</div>
  </div>

  <div class="bar">
    <button id="go">Preview →</button>
  </div>

  <div class="card" id="preview" style="margin-top:16px">
    <div class="pvhead">
      <h2 style="margin:0">Preview</h2>
      <div class="small" id="pvcount"></div>
    </div>
    <div class="small" style="margin-bottom:10px">
      Uncheck any message to leave it out.
      <button class="linkbtn" id="selall">select all</button> ·
      <button class="linkbtn" id="selnone">none</button>
    </div>
    <div class="msgs" id="msgs"></div>
    <div class="bar" style="margin-top:14px">
      <button class="ghost" id="back" style="flex:0 0 130px">← Adjust</button>
      <button id="save">Save export</button>
    </div>
  </div>

  <div class="result" id="result"></div>
</div>

<script>
const state = { people: new Set(), range: "7d", dir: "both", order: "oldest", redact: false, shown: [] };

function $(id){ return document.getElementById(id); }

// ---- people picker ----
let allPeople = [];
function renderPeople(filter) {
  const q = (filter||"").toLowerCase();
  const list = $("plist"); list.innerHTML = "";
  const shown = allPeople.filter(p => p.name.toLowerCase().includes(q)).slice(0, 300);
  shown.forEach(p => {
    const row = document.createElement("div");
    row.className = "prow";
    const checked = state.people.has(p.name);
    row.innerHTML = `<input type="checkbox" ${checked?"checked":""}>
      <span class="nm">${p.name}</span>
      <span class="ct">${p.count.toLocaleString()} · ${p.type}</span>`;
    row.onclick = (e) => {
      if (e.target.tagName !== "INPUT") row.querySelector("input").click();
    };
    row.querySelector("input").onclick = (e) => {
      e.stopPropagation();
      if (e.target.checked) state.people.add(p.name); else state.people.delete(p.name);
      renderChips();
    };
    list.appendChild(row);
  });
  if (!shown.length) list.innerHTML = '<div class="mut" style="padding:12px">No matches.</div>';
}
function renderChips() {
  const c = $("chips"); c.innerHTML = "";
  state.people.forEach(name => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name + "  ✕";
    chip.onclick = () => { state.people.delete(name); renderChips(); renderPeople($("search").value); };
    c.appendChild(chip);
  });
}
$("search").oninput = e => renderPeople(e.target.value);

fetch("/api/people").then(r => r.json()).then(people => {
  allPeople = people;
  renderPeople("");
  $("phint").textContent = people.length + " conversations found. Busiest first. Pick one or several.";
}).catch(() => {
  $("plist").innerHTML = '<div class="mut" style="padding:12px">Could not read Messages. '
    + 'Give Terminal Full Disk Access, then restart it.</div>';
});

// ---- ranges ----
document.querySelectorAll("#ranges .opt, [data-r=custom]").forEach(el => el.onclick = () => {
  document.querySelectorAll(".opt").forEach(o => o.classList.remove("on"));
  el.classList.add("on");
  state.range = el.dataset.r;
  $("custom").classList.toggle("show", state.range === "custom");
});

// ---- type toggles ----
document.querySelectorAll("#types .tg").forEach(el => el.onclick = () => el.classList.toggle("on"));
// ---- direction ----
document.querySelectorAll("#dir div").forEach(el => el.onclick = () => {
  document.querySelectorAll("#dir div").forEach(d => d.classList.remove("on"));
  el.classList.add("on"); state.dir = el.dataset.d;
});
// ---- order ----
document.querySelectorAll("#order div").forEach(el => el.onclick = () => {
  document.querySelectorAll("#order div").forEach(d => d.classList.remove("on"));
  el.classList.add("on"); state.order = el.dataset.o;
});
// ---- redact ----
$("redact").onclick = () => {
  state.redact = !state.redact;
  $("redact").classList.toggle("on", state.redact);
};

function collect() {
  const types = [...document.querySelectorAll("#types .tg.on")].map(t => t.dataset.t);
  return {
    people: [...state.people], range: state.range,
    start: $("start").value, end: $("end").value,
    direction: state.dir, types,
    include: $("include").value, exclude: $("exclude").value,
    cap: $("cap").value, redact: state.redact,
    order: state.order, dest: $("dest").value,
  };
}

// ---- preview ----
$("go").onclick = () => {
  if (!state.people.size) { alert("Pick at least one person first."); return; }
  const btn = $("go"); btn.disabled = true; btn.textContent = "Loading preview…";
  $("result").className = "result";
  fetch("/api/preview", { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(collect()) })
  .then(r => r.json()).then(d => {
    btn.disabled = false; btn.textContent = "Preview →";
    if (!d.ok) { showErr(d.error); return; }
    state.shown = d.records;
    renderPreview(d);
  }).catch(e => { btn.disabled = false; btn.textContent = "Preview →"; showErr(e); });
};

function esc(s){ return (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function renderPreview(d) {
  const box = $("msgs"); box.innerHTML = "";
  d.records.forEach(r => {
    const m = document.createElement("label");
    m.className = "m"; m.dataset.id = r.id;
    const body = r.message_type === "reaction" ? "<i>"+esc(r.text)+"</i>" : esc(r.text);
    m.innerHTML = `<input type="checkbox" checked>
      <span class="meta">${r.date} ${r.time.slice(0,5)}<br>${esc(r.person)}</span>
      <span class="body"><b>${esc(r.sender)}:</b> ${body}</span>`;
    m.querySelector("input").onchange = e => m.classList.toggle("off", !e.target.checked);
    box.appendChild(m);
  });
  let note = `${d.total.toLocaleString()} messages match`;
  if (d.total > d.records.length) note += ` · showing first ${d.records.length.toLocaleString()} (the rest are still included)`;
  if (d.redacted) note += " · 🔒 redacted";
  $("pvcount").textContent = note;
  $("preview").style.display = "block";
  $("preview").scrollIntoView({behavior:"smooth"});
}
$("selall").onclick = () => $("msgs").querySelectorAll("input").forEach(i => { i.checked=true; i.dispatchEvent(new Event("change")); });
$("selnone").onclick = () => $("msgs").querySelectorAll("input").forEach(i => { i.checked=false; i.dispatchEvent(new Event("change")); });
$("back").onclick = () => { $("preview").style.display="none"; window.scrollTo({top:0,behavior:"smooth"}); };

// ---- save ----
$("save").onclick = () => {
  const deselected = [...$("msgs").querySelectorAll(".m")]
    .filter(m => !m.querySelector("input").checked)
    .map(m => parseInt(m.dataset.id));
  const btn = $("save"); btn.disabled = true; btn.textContent = "Saving…";
  fetch("/api/export", { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ ...collect(), deselected }) })
  .then(r => r.json()).then(d => {
    btn.disabled = false; btn.textContent = "Save export";
    const res = $("result");
    if (d.ok) {
      res.className = "result ok";
      const att = d.attachments_saved ? ` · <b>${d.attachments_saved.toLocaleString()}</b> attachments` : "";
      const miss = d.attachments_missing ? ` (${d.attachments_missing} not downloaded from iCloud)` : "";
      res.innerHTML = `✅ Saved <b>${d.count.toLocaleString()}</b> messages${att}${miss} (${d.first} → ${d.last}).`
        + `<br><br><code>${esc(d.folder)}</code>`
        + `<br><br>Open <code>conversation.html</code> to read it with photos & videos inline — toggle newest/oldest at the top. `
        + `If Google Drive for desktop is on, it's uploading now; otherwise drag this folder to drive.google.com.`;
    } else { res.className = "result err"; res.innerHTML = "⚠️ " + esc(d.error || "Failed."); }
    res.scrollIntoView({behavior:"smooth"});
  }).catch(e => { btn.disabled=false; btn.textContent="Save export"; showErr(e); });
};
function showErr(msg){ const res=$("result"); res.className="result err"; res.innerHTML="⚠️ "+esc(""+msg); }
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or "{}")

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            page = PAGE.replace("__DEFAULT_DEST__", _h(default_dest()))
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/people":
            try:
                self._send(200, json.dumps(list_people()))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        try:
            payload = self._read_json()
            if self.path == "/api/preview":
                records = apply_order(gather(payload), payload.get("order", "oldest"))
                shown = records[:PREVIEW_LIMIT]
                self._send(200, json.dumps({
                    "ok": True, "total": len(records), "records": shown,
                    "redacted": bool(payload.get("redact")),
                }))
            elif self.path == "/api/export":
                deselected = set(payload.get("deselected") or [])
                records = [r for r in gather(payload) if r["id"] not in deselected]
                self._send(200, json.dumps(export_records(records, payload.get("people") or [], payload)))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}))


def main():
    if not os.path.exists(MESSAGES_DB):
        print("Could not find your Messages database at ~/Library/Messages/chat.db")
        print("This tool only runs on a Mac with the Messages app set up.")
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print("=" * 52)
    print("  Desmond Picker is running.")
    print(f"  Open this in your browser:  {url}")
    print("  Press Control-C here to stop.")
    print("=" * 52)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. See you in another life, brother.")
        server.shutdown()


if __name__ == "__main__":
    main()
