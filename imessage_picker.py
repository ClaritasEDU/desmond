#!/usr/bin/env python3
"""
Desmond Picker - a privacy-first browser interface for exporting iMessages.

Pick one or more people, choose a time range, then PREVIEW exactly what
would be exported before anything is written to disk. Trim it down with
content filters, keyword include/exclude, a message cap, redaction, and
per-message deselection. Only what you approve gets saved.

Output lands in your Google Drive folder (auto-detected) or ~/Downloads —
each pick gets its own folder with conversation.html (photos/videos inline,
newest/oldest toggle), conversation.md, messages.json/csv, and attachments/.

Run with:  python3 imessage_picker.py
Opens automatically in your browser. Everything is processed locally; nothing
is uploaded by this tool itself. (If you keep the Google Drive mirror on, the
Drive desktop app then syncs that copy to your Google account — turn the
"☁︎ Also copy to Google Drive" toggle off for a purely local export.)
"""

import os
import re
import csv
import sys
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
    """Local primary save location for picks (~/Downloads/Desmond_Message_Picks).
    Each export is also mirrored to Google Drive when available, so a pick lives
    in BOTH places. The local copy is always kept."""
    return os.path.expanduser("~/Downloads/Desmond_Message_Picks")


def drive_picks_base(override=None):
    """The Google Drive folder picks are mirrored into, or None if no Drive."""
    if override:
        return os.path.expanduser(override)
    gd = attach.find_google_drive_dir()
    return os.path.join(gd, "Desmond_Message_Picks") if gd else None


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
    "1d": "Last 24 hours", "7d": "Last week", "30d": "Last month",
    "90d": "Last 3 months", "180d": "Last 6 months", "365d": "Last year",
    "all": "All time", "custom": "Custom range",
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
        elif chunk[0] == 0x82:  # 4-byte little-endian length (long messages)
            length = int.from_bytes(chunk[1:5], "little")
            chunk = chunk[5:]
        else:
            length = chunk[0]
            chunk = chunk[1:]
        text = chunk[:length].decode("utf-8", errors="ignore")
        # U+FFFC marks where an inline attachment sat; it's noise in text.
        text = text.replace("￼", "").strip()
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
    days = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
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
    return sqlite3.connect(attach._ro_uri(MESSAGES_DB), uri=True)


_conv_name_cache = {}


def conversation_name(handle_id, chat_id, display_name, cursor):
    """Derive a human-readable conversation name (matches the main exporter).
    Cached per (handle, chat) — resolving names runs per MESSAGE, and the SQL
    lookups behind group participants made the picker crawl on big histories."""
    key = (handle_id, chat_id, display_name)
    hit = _conv_name_cache.get(key)
    if hit is not None:
        return hit
    # SHARED NAMING RULE (same across every Desmond module): a chat whose
    # identifier starts with "chat" is a group; anything else ("+1512…",
    # "kate@example.com") is a direct chat named for that ONE counterpart,
    # kept as the full identifier when no contact matches. Never abbreviate a
    # number to its last four digits — two strangers ending in the same four
    # digits would silently merge into one "person".
    if display_name:
        result = (display_name, "group")
    elif chat_id:
        if str(chat_id).startswith("chat"):
            participants = core.get_chat_participants(chat_id, cursor)
            result = (participants or chat_id, "group")
        else:
            result = (core.lookup_contact_name(chat_id) or chat_id, "direct")
    elif handle_id:
        result = (core.get_contact_name(handle_id, cursor), "direct")
    else:
        result = ("Unknown", "direct")
    _conv_name_cache[key] = result
    return result


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
    """Map message_id -> list of attachment dicts (id + category + real file path)."""
    cursor.execute("""
        SELECT message_attachment_join.message_id, attachment.ROWID,
               attachment.mime_type, attachment.filename, attachment.transfer_name
        FROM attachment
        JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id
    """)
    result = defaultdict(list)
    for msg_id, att_id, mime_type, filename, transfer_name in cursor.fetchall():
        # Real chat.db rows often have a NULL mime_type (HEIC/MOV especially);
        # fall back to the filename's extension so they still render inline
        # and count as photos/videos for --photos-videos + verify.
        category = attach.categorize(mime_type, transfer_name or filename)
        result[msg_id].append({
            "id": att_id, "category": category, "mime": mime_type,
            "filename": filename, "transfer_name": transfer_name,
        })
    return result


def list_people():
    """Return [{name, type, count, last}] for every conversation, busiest first."""
    ensure_contacts()
    conn = open_db()
    cursor = conn.cursor()
    meta = {}
    seen = set()   # a message joined to two chats (merged SMS/iMessage) counts once
    for row in iter_messages(cursor):
        rowid, _, date, _, handle_id, _, _, chat_id, display_name, _ = row
        dt = core.convert_apple_time(date)
        if dt is None:
            continue
        name, ctype = conversation_name(handle_id, chat_id, display_name, cursor)
        name = str(name)
        if (rowid, name) in seen:
            continue
        seen.add((rowid, name))
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
    if text:
        text = text.replace("￼", "").strip()   # inline-attachment marker

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


PROGRESS_EVERY = 5000   # rows between "Reading messages…" progress lines


def gather(f, progress=None):
    """Run all filters and return the list of approved-by-rule records.
    `progress(n_seen, n_kept)` is called every PROGRESS_EVERY rows so a long
    read can show signs of life (the one-shot prints a line per call)."""
    ensure_contacts()
    people = set(f.get("people") or [])
    since, until = resolve_range(f.get("range", "all"), f.get("start") or None, f.get("end") or None)
    direction = f.get("direction", "both")
    # An explicit empty list means "nothing" (every content toggle off) —
    # only a MISSING key falls back to everything.
    types = f.get("types")
    types = set(["text", "attachments", "reactions"] if types is None else types)
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
    if progress:
        progress(0, 0)
    att = attachments_for(cursor)

    out = []
    seen = set()   # a message joined to two chats (merged SMS/iMessage) exports once
    n_seen = 0
    for row in iter_messages(cursor, since, until):
        n_seen += 1
        if progress and n_seen % PROGRESS_EVERY == 0:
            progress(n_seen, len(out))
        rowid, _, _, is_from_me, handle_id, _, _, chat_id, display_name, _ = row
        name, _ = conversation_name(handle_id, chat_id, display_name, cursor)
        name = str(name)
        if people and name not in people:
            continue
        if rowid in seen:
            continue
        seen.add(rowid)
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
            # Scrub EVERY text field that reaches a saved file: conversation.html,
            # .md and messages.json all read text_plain, not just text.
            rec["text"] = redact_text(rec["text"])
            rec["text_plain"] = redact_text(rec["text_plain"])
            rec["redacted"] = True
        out.append(rec)
    conn.close()
    if progress:
        progress(n_seen, len(out))

    out.sort(key=lambda r: r["timestamp"])
    if cap and len(out) > cap:
        out = out[-cap:]  # most recent N
    return out


def safe_name(name):
    return "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in str(name)).strip()


def filter_summary(f):
    bits = [RANGE_LABELS.get(f.get("range", "all"), f.get("range", ""))]
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
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


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
    already = False
    if os.path.exists(dest):
        # Same bytes already here (a re-run / --retry pass) → reuse it; only
        # take a suffixed name for a genuinely different file.
        try:
            already = os.path.getsize(dest) == os.path.getsize(src)
        except OSError:
            already = False
        if not already:
            root, ext = os.path.splitext(dest)
            dest = f"{root}_{abs(hash(src)) % 100000}{ext}"
            try:
                already = (os.path.exists(dest)
                           and os.path.getsize(dest) == os.path.getsize(src))
            except OSError:
                already = False
    if not already:
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
            if not os.path.exists(jpg):
                subprocess.run(["sips", "-s", "format", "jpeg", dest, "--out", jpg],
                               check=True, capture_output=True)
            display = os.path.relpath(jpg, folder).replace(os.sep, "/")
        except Exception:
            pass
    return {"id": (a or {}).get("id"), "category": category, "name": original,
            "missing": False, "mime": (a or {}).get("mime"),
            "path": rel, "display": display}


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
 .m .txt{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;}
 .m.react .txt{color:#9aa3b2;font-style:italic;}
 img.att{display:block;margin:6px 0;max-width:340px;max-height:340px;border-radius:10px;border:1px solid #2a2f3a;}
 video.att,audio.att{display:block;margin:6px 0;width:340px;max-width:100%;}
 .miss{color:#d6a;} a.file{color:#4f8cff;}
 .pdfhint{display:none;color:#9aa3b2;font-size:12.5px;}
 /* PDF / print: white page, every message shown, photos kept inline, no
    message split across pages. Videos/audio can't print — show a caption. */
 @media print{
   body{background:#fff;color:#111;font-size:11.5px;line-height:1.45;}
   .wrap{max-width:none;padding:0;}
   .bar,.pdfhint{display:none!important;}
   h1{color:#111;} .sub,.day,.m .meta{color:#555;}
   .person{color:#1a4f9c;border-top:0;page-break-before:always;break-before:page;margin-top:0;padding-top:0;font-size:16px;}
   .person:first-child{page-break-before:auto;break-before:auto;}
   .m{border-bottom:1px solid #e3e3e3;page-break-inside:avoid;break-inside:avoid;}
   .m .who{color:#1a4f9c;} .m.me .who{color:#1f7a4f;}
   img.att{max-width:300px;max-height:300px;border:1px solid #ccc;}
   video.att,audio.att{display:none;}
   .printcap{display:block;color:#555;font-style:italic;}
   a{color:inherit;text-decoration:none;}
 }
 .printcap{display:none;}
</style></head><body><div class="wrap">
<h1>__TITLE__</h1>
<p class="sub">__SUMMARY__</p>
<div class="bar">
  <button id="toggle">↕ Order: <b id="ord"></b></button>
  <button id="more">Show next 100</button>
  <button id="all">Show all</button>
  <button id="pdf" title="Shows every message, then opens Print — choose 'Save as PDF'">🖨 Save as PDF</button>
  <span class="sub" id="count" style="margin:0"></span>
</div>
<p class="pdfhint" id="pdfhint">Preparing every message and photo for printing… the Print window opens when they're loaded. In it, pick <b>Save as PDF</b> (Safari: the PDF menu at the bottom-left; Chrome: Destination → Save as PDF).</p>
<div id="out"></div>
</div>
<script>
const RECORDS = __RECORDS__;
const PAGE_SIZE = 100;            // paginate so huge threads don't crash the browser
let order = "__DEFAULT_ORDER__";
let sorted = [], shown = 0, lastPerson = null, lastDay = null;
function esc(s){return (s||"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function media(m){
  if(m.missing) return '<div class="miss">['+esc(m.category)+' — not downloaded from iCloud]</div>';
  const p=esc(m.path), d=esc(m.display||m.path);
  if(m.category==="photo") return '<a href="'+p+'" target="_blank"><img class="att" loading="lazy" src="'+d+'"></a>';
  if(m.category==="video") return '<video class="att" controls preload="metadata" src="'+p+'"></video><span class="printcap">[video: '+esc(m.name)+']</span>';
  if(m.category==="audio") return '<audio class="att" controls src="'+p+'"></audio><span class="printcap">[audio: '+esc(m.name)+']</span>';
  return '<a class="file" href="'+p+'" target="_blank">📎 '+esc(m.name)+'</a>';
}
function msgRow(r){
  const m=document.createElement("div");
  m.className="m"+(r.is_from_me?" me":"")+(r.message_type==="reaction"?" react":"");
  let html='<div class="meta">'+esc(r.time.slice(0,5))+' · <span class="who">'+esc(r.sender)+'</span></div>';
  if(r.text_plain) html+='<div class="txt">'+esc(r.text_plain)+'</div>';
  (r.media||[]).forEach(mm=> html+=media(mm));
  m.innerHTML=html; return m;
}
function appendPage(){
  const out=document.getElementById("out");
  const end=Math.min(shown+PAGE_SIZE, sorted.length);
  for(let i=shown;i<end;i++){
    const r=sorted[i];
    if(r.person!==lastPerson){ lastPerson=r.person; lastDay=null;
      const h=document.createElement("div"); h.className="person"; h.textContent=r.person; out.appendChild(h); }
    if(r.date!==lastDay){ lastDay=r.date;
      const d=document.createElement("div"); d.className="day"; d.textContent=r.date; out.appendChild(d); }
    out.appendChild(msgRow(r));
  }
  shown=end; updateBar();
}
function updateBar(){
  document.getElementById("ord").textContent=(order==="newest"?"newest first":"oldest first");
  document.getElementById("count").textContent="showing "+shown.toLocaleString()+" of "+sorted.length.toLocaleString();
  const remaining=sorted.length-shown;
  const more=document.getElementById("more"), all=document.getElementById("all");
  more.style.display = remaining>0 ? "" : "none";
  all.style.display  = remaining>0 ? "" : "none";
  if(remaining>0) more.textContent="Show next "+Math.min(PAGE_SIZE,remaining);
}
function reset(){
  // Group by conversation first, then by time inside each — so an export of
  // several threads reads as separate conversations, not one interleaved feed.
  const byTime=(a,b)=> a.timestamp<b.timestamp?-1:(a.timestamp>b.timestamp?1:0);
  sorted=RECORDS.slice().sort((a,b)=>{
    const p=String(a.person).localeCompare(String(b.person));
    if(p!==0) return p;
    return order==="newest" ? byTime(b,a) : byTime(a,b);
  });
  document.getElementById("out").innerHTML=""; shown=0; lastPerson=null; lastDay=null;
  appendPage();
}
document.getElementById("toggle").onclick=()=>{ order=(order==="newest"?"oldest":"newest"); reset(); };
document.getElementById("more").onclick=()=>appendPage();
document.getElementById("all").onclick=()=>{ while(shown<sorted.length) appendPage(); };
function waitForImages(){
  // lazy-loaded photos must be fetched before print, or the PDF has blank boxes
  const imgs=[...document.querySelectorAll("img.att")];
  imgs.forEach(i=>{ i.loading="eager"; });
  return Promise.all(imgs.map(i=> (i.complete ? Promise.resolve() :
    new Promise(res=>{ i.onload=res; i.onerror=res; }))));
}
async function saveAsPdf(){
  const hint=document.getElementById("pdfhint"); hint.style.display="block";
  while(shown<sorted.length) appendPage();
  await waitForImages();
  await new Promise(r=>setTimeout(r,300));
  hint.style.display="none";
  window.print();
}
document.getElementById("pdf").onclick=saveAsPdf;
reset();
// ?print=1 → every message on the page, photos loaded, ready for headless
// printing (desmond_pdf.py). MUST run after reset() has populated `sorted`.
if(new URLSearchParams(location.search).get("print")==="1"){
  while(shown<sorted.length) appendPage();
  window.__desmondReady = waitForImages().then(()=>{ window.__desmondPrintReady=true; });
}
</script></body></html>"""


def json_for_script(obj):
    """JSON that is safe to inline inside a <script> block. Escaping every "<"
    means no message text can open a comment ("<!--") or close the script
    ("</script>") — either would let a texter's message break or hijack the
    transcript page. U+2028/2029 are line terminators in JS but not JSON."""
    return (json.dumps(obj)
            .replace("<", "\\u003c")
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029"))


def render_html(records, people, summary, default_order):
    title = "Messages — " + (", ".join(people) if people else "export")
    payload = json_for_script(records)
    return (HTML_TEMPLATE
            .replace("__TITLE__", _h(title))
            .replace("__SUMMARY__", _h(summary))
            .replace("__RECORDS__", payload)
            .replace("__DEFAULT_ORDER__", "newest" if default_order == "newest" else "oldest"))


# ---- export progress (the page polls /api/progress/<job>) ----------------
_JOBS = {}
_JOBS_LOCK = threading.Lock()


class Progress:
    """Phase + percent for one export job. Weights are rough shares of wall
    time so the bar moves steadily: attachments and PDFs dominate."""
    PHASES = [("read", "Reading messages", 5), ("copy", "Copying photos & files", 30),
              ("write", "Writing transcript files", 5), ("sections", "PDF per conversation", 25),
              ("pdf", "Combined PDF", 25), ("drive", "Mirroring to Google Drive", 10)]

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"phase": "read", "label": "Starting…", "done": 0, "total": 0,
                      "percent": 0, "finished": False, "result": None, "log": []}

    def update(self, phase, done=0, total=0, label=None):
        idx = [k for k, _, _ in self.PHASES].index(phase)
        before = sum(w for _, _, w in self.PHASES[:idx])
        weight = self.PHASES[idx][2]
        frac = (done / total) if total else 0.0
        with self.lock:
            st = self.state
            st.update(phase=phase, done=done, total=total,
                      label=label or self.PHASES[idx][1],
                      percent=min(99, int(before + weight * frac)))
            line = f"{st['label']}" + (f" ({done:,} of {total:,})" if total else "")
            if not st["log"] or st["log"][-1] != line:
                st["log"].append(line)
                st["log"] = st["log"][-12:]

    def finish(self, result):
        with self.lock:
            self.state.update(finished=True, result=result, percent=100,
                              label="Done" if result.get("ok") else "Failed")

    def snapshot(self):
        with self.lock:
            return dict(self.state)


def start_export_job(payload):
    """Run the export on a background thread; return its job id at once."""
    import uuid
    job = uuid.uuid4().hex
    prog = Progress()
    with _JOBS_LOCK:
        _JOBS[job] = prog

    def run():
        try:
            prog.update("read", label="Reading messages")
            deselected = set(payload.get("deselected") or [])
            records = [r for r in gather(payload) if r["id"] not in deselected]
            result = export_records(records, payload.get("people") or [], payload,
                                    progress=prog)
        except Exception as e:
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        prog.finish(result)

    threading.Thread(target=run, daemon=True).start()
    return job


def export_records(records, people, f, progress=None):
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
    n_with_att = sum(1 for r in records if r.get("attachments")) if include_att else 0
    n_done = 0
    for r in records:
        media = []
        if include_att and r.get("attachments") and progress:
            n_done += 1
            if n_done % 10 == 0 or n_done == n_with_att:
                progress.update("copy", n_done, n_with_att)
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

    if progress:
        progress.update("write")
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
            # Message text is written by whoever texted you — neutralize
            # leading formula characters so Excel/Sheets shows them as text
            # instead of executing =/+/-/@ formulas.
            text = row.get("text")
            if isinstance(text, str) and text[:1] in ("=", "+", "-", "@"):
                row["text"] = "'" + text
            writer.writerow(row)

    # 5. Mirror the whole export to Google Drive (so picks live local + Drive).
    #    The local export is already complete on disk at this point — a Drive
    #    hiccup (unmounted, full, a file in the way) must NOT turn a finished
    #    export into an error; it's reported as a warning beside the local path.
    drive_folder = None
    drive_error = None
    if f.get("mirror_drive", True):
        drive_base = drive_picks_base(f.get("drive"))
        if drive_base:
            drive_folder = os.path.join(drive_base, os.path.basename(folder))
            copy_errors = []
            if progress:
                progress.update("drive")
            try:
                attach.mirror_tree(folder, drive_folder, errors=copy_errors)
            except OSError as e:
                drive_error = f"Google Drive copy failed ({e}); the local export is complete."
                drive_folder = None
            else:
                if copy_errors:
                    drive_error = (f"{len(copy_errors)} file(s) failed to copy to "
                                   f"Google Drive; the local export is complete.")

    # 6. Verify the pick is present in both places; write a per-export report.
    saved_media = [(r, m) for r in records for m in media_by_id[r["id"]]
                   if not m.get("missing") and m.get("path")]
    in_local = sum(1 for _r, m in saved_media
                   if os.path.exists(os.path.join(folder, m["path"])))
    in_drive = 0
    missing_drive = []
    if drive_folder:
        for _r, m in saved_media:
            if os.path.exists(os.path.join(drive_folder, m["path"])):
                in_drive += 1
            else:
                missing_drive.append((_r, m))
    write_pick_report(folder, drive_folder, people, summary, att_saved, att_missing,
                      in_local, in_drive, saved_media, missing_drive,
                      drive_error=drive_error)

    # 6. ONE PDF of the whole thing, in order, photos inline — the thing most
    #    people actually want to hand to someone. Rendered from the transcript
    #    by a headless Chrome/Edge/Chromium already on the Mac; if none is
    #    installed, the transcript's own "Save as PDF" button is the fallback.
    n_photos = sum(1 for ms in media_by_id.values() for m in ms
                   if m.get("category") == "photo" and not m.get("missing"))
    range_key = f.get("range", "range")

    # 6a. With several conversations: ONE PDF PER CONVERSATION first, rendered
    #     one at a time (small, quick, progress visible, one failure can't
    #     take the others down), then the combined PDF.
    pdf_sections = []
    if len(people) > 1:
        by_conv = defaultdict(list)
        for hr in html_records:
            by_conv[hr["person"]].append(hr)
        names = sorted(by_conv, key=lambda n: str(n))
        sec_dir = os.path.join(folder, "sections")
        os.makedirs(sec_dir, exist_ok=True)
        for i, name in enumerate(names, start=1):
            recs_i = by_conv[name]
            sec_html = os.path.join(sec_dir, f"{i:02d}_{safe_name(name)}.html")
            sec_summary = f"{len(recs_i):,} messages · section {i} of {len(names)}"
            with open(sec_html, "w", encoding="utf-8") as hf:
                # Attachments live one level up from sections/ — point there.
                hf.write(render_html(
                    [dict(r, media=[dict(m, path="../" + m["path"], display="../" + m.get("display", m["path"]))
                                    if not m.get("missing") else m for m in r["media"]])
                     for r in recs_i], [name], sec_summary, order))
            n_ph = sum(1 for r in recs_i for m in r["media"]
                       if m.get("category") == "photo" and not m.get("missing"))
            print(f"Section {i} of {len(names)}: {name} ({len(recs_i):,} messages)", flush=True)
            if progress:
                progress.update("sections", i - 1, len(names),
                                label=f"PDF {i} of {len(names)}: {name}")
            sec_pdf, sec_err = make_pdf(folder, f"{i:02d}_{safe_name(name)}", range_key,
                                        n_messages=len(recs_i), n_photos=n_ph, html_path=sec_html)
            pdf_sections.append({"name": name, "pdf": sec_pdf, "error": sec_err,
                                 "messages": len(recs_i)})

    if progress:
        progress.update("pdf", label=f"Combined PDF ({len(records):,} messages, {n_photos:,} photos)")
    pdf_path, pdf_error = make_pdf(folder, label, range_key,
                                   n_messages=len(records), n_photos=n_photos)

    try:
        # Open the PDF itself when we have one; otherwise the folder.
        subprocess.run(["open", pdf_path or folder], check=False)
    except Exception:
        pass

    return {"ok": True, "count": len(records), "folder": folder,
            "pdf_path": pdf_path, "pdf_error": pdf_error, "pdf_sections": pdf_sections,
            "drive_folder": drive_folder, "drive_error": drive_error,
            "first": first_date, "last": last_date,
            "attachments_saved": att_saved, "attachments_missing": att_missing,
            "in_local": in_local, "in_drive": in_drive,
            "missing_drive": len(missing_drive)}


_PREVIEW_CACHE = os.path.join(os.path.expanduser("~/Library/Caches"), "Desmond", "preview")
_BROWSER_IMAGE_EXTS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                       ".gif": "image/gif", ".webp": "image/webp"}


def _preview_cache_name(att_id, src):
    """Cache file name that changes whenever the SOURCE changes. Keyed on the
    attachment ROWID alone, a restored chat.db that reuses an ID, or a
    replaced original, would keep serving the old JPEG — a stale preview of
    an unrelated photo. Path + size + mtime make the key follow the file."""
    import hashlib
    st = os.stat(src)
    ident = f"{os.path.abspath(src)}|{st.st_size}|{int(st.st_mtime)}"
    return f"{att_id}_{hashlib.sha1(ident.encode('utf-8')).hexdigest()[:12]}.jpg"


def preview_photo_path(att_id):
    """(path, mime) of a browser-displayable file for a photo attachment, or
    (None, None). HEIC/TIFF are converted once with macOS `sips` into a small
    cache under ~/Library/Caches/Desmond (never touching the original)."""
    try:
        conn = open_db()
        try:
            row = conn.execute(
                "SELECT filename, mime_type, transfer_name FROM attachment WHERE ROWID = ?",
                (att_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None, None
    if not row or not row[0]:
        return None, None
    filename, mime, transfer = row
    if attach.categorize(mime, transfer or filename) != "photo":
        return None, None
    src = os.path.expanduser(filename)
    if not os.path.isfile(src):
        return None, None
    ext = os.path.splitext(src)[1].lower()
    if ext in _BROWSER_IMAGE_EXTS:
        return src, _BROWSER_IMAGE_EXTS[ext]
    if ext in WEB_CONVERT_EXTS:
        try:
            os.makedirs(_PREVIEW_CACHE, exist_ok=True)
            out = os.path.join(_PREVIEW_CACHE, _preview_cache_name(att_id, src))
            if not os.path.isfile(out):
                subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "800", src, "--out", out],
                               check=True, capture_output=True)
            return out, "image/jpeg"
        except Exception:
            return None, None
    # Unknown image type — let the browser try.
    return src, (mime or "application/octet-stream")


def make_pdf(folder, label, range_key, n_messages=0, n_photos=0, html_path=None):
    """Render <folder>/conversation.html to <folder>/<label>_<range>.pdf.
    Returns (pdf_path, None) or (None, reason). The reason is also printed to
    the Terminal window and written to PDF_README.txt in the folder, so it can
    never silently vanish."""
    def fail(reason):
        print(f"\n⚠️  No automatic PDF: {reason}", flush=True)
        try:
            with open(os.path.join(folder, "PDF_README.txt"), "w", encoding="utf-8") as fh:
                fh.write("No automatic PDF was made.\n\nReason: " + reason + "\n\n"
                         "To make one yourself: open conversation.html in this folder, click "
                         "\"Save as PDF\" at the top, then choose Save as PDF in the print window.\n")
        except OSError:
            pass
        return None, reason
    try:
        import desmond_pdf
    except ImportError:
        return fail("desmond_pdf.py is missing next to imessage_picker.py.")
    browser = desmond_pdf.find_browser()
    if not browser:
        return fail("No Chrome/Edge/Chromium on this Mac for automatic PDFs. Install Google "
                    "Chrome (free) and Save again, or open conversation.html and click "
                    "\u201cSave as PDF\u201d.")
    html = html_path or os.path.join(folder, "conversation.html")
    pdf = os.path.join(folder, f"{label}_{range_key}.pdf")
    budget = desmond_pdf.render_budget(n_messages, n_photos)
    print(f"\nRendering PDF ({n_messages:,} messages, {n_photos:,} photos). This runs "
          "until it finishes — a big thread can take several minutes…", flush=True)
    err = desmond_pdf.convert(browser, html, pdf, budget_ms=budget)
    if err:
        return fail(f"PDF could not be rendered ({err}). Open conversation.html and click "
                    "\u201cSave as PDF\u201d.")
    print(f"PDF written: {pdf}", flush=True)
    return pdf, None


def write_pick_report(folder, drive_folder, people, summary, att_saved, att_missing,
                      in_local, in_drive, saved_media, missing_drive,
                      drive_error=None):
    """Per-export verification report: how many attachments in the local export
    vs Google Drive, and the list of any that didn't mirror."""
    missing_local = [(r, m) for r, m in saved_media
                     if not os.path.exists(os.path.join(folder, m["path"]))]

    def items(pairs):
        return [{"person": r["person"], "date": r["date"],
                 "name": m.get("name"), "path": m.get("path")} for r, m in pairs]

    with open(os.path.join(folder, "verify_diff.json"), "w", encoding="utf-8") as jf:
        json.dump({
            "generated": datetime.now().isoformat(),
            "people": people, "filters": summary,
            "counts": {"attachments_saved": att_saved, "offloaded": att_missing,
                       "in_local": in_local, "in_drive": in_drive,
                       "drive_mirror": bool(drive_folder)},
            "missing_local": items(missing_local),
            "missing_drive": items(missing_drive),
        }, jf, indent=2)

    ok = (in_local == att_saved) and (not drive_folder or in_drive == att_saved)
    verdict = ("✅ present in all places" if ok and not att_missing else
               "✅ local + Drive complete; some offloaded in iCloud" if ok else
               "⚠️ some attachments missing — re-export")
    with open(os.path.join(folder, "VERIFY_REPORT.md"), "w", encoding="utf-8") as mf:
        mf.write("# Pick Verification Report\n\n")
        mf.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n")
        mf.write(f"**People:** {', '.join(people)}  \n")
        mf.write(f"**Filters:** {summary}\n\n")
        mf.write("## Attachments per place\n\n| Place | Attachments |\n|---|---|\n")
        mf.write(f"| Saved by this pick | {att_saved} "
                 f"({att_missing} offloaded/not downloaded) |\n")
        mf.write(f"| Local export | {in_local} / {att_saved} |\n")
        drive_cell = f"{in_drive} / {att_saved}" if drive_folder else "not mirrored"
        mf.write(f"| Google Drive | {drive_cell} |\n\n")
        mf.write(f"**Verdict:** {verdict}\n")
        if drive_error:
            mf.write(f"\n**Google Drive warning:** {drive_error}\n")
        if missing_drive:
            mf.write(f"\n## Missing from Google Drive ({len(missing_drive)})\n\n")
            for r, m in missing_drive[:500]:
                mf.write(f"- {r['date']} · {r['person']} · {m.get('name')}\n")

    if drive_folder:
        for fn in ("VERIFY_REPORT.md", "verify_diff.json"):
            try:
                shutil.copy2(os.path.join(folder, fn), os.path.join(drive_folder, fn))
            except Exception:
                pass


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
  .m .body img.pv { display:block; margin:6px 0 2px; max-width:180px; max-height:180px; border-radius:8px; border:1px solid var(--line); }
  .m .body .pvtag { display:inline-block; margin-top:4px; color:var(--mut); font-size:12px; }
  /* Full-page gate while the server reads every conversation out of Messages.
     Nothing can be clicked until the list is real. */
  #loading { position:fixed; inset:0; background:var(--bg); z-index:50; display:flex; align-items:center; justify-content:center; text-align:center; padding:24px; }
  #loading .box { max-width:460px; }
  #loading h2 { font-size:20px; margin:0 0 10px; color:var(--txt); text-transform:none; letter-spacing:0; }
  #loading p { color:var(--mut); margin:6px 0; }
  #loading .spin { width:34px; height:34px; border:3px solid var(--line); border-top-color:var(--accent); border-radius:50%; margin:0 auto 18px; animation:spin 1s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #loading.err .spin { display:none; }
  #loading.err h2 { color:#e88; }
  #prog { display:none; margin-top:16px; padding:15px; border-radius:10px; background:var(--card); border:1px solid var(--line); }
  #prog .bar { height:14px; background:#0f1115; border:1px solid var(--line); border-radius:8px; overflow:hidden; margin:8px 0; }
  #prog .fill { height:100%; width:0; background:var(--accent); transition:width .4s; }
  #prog .pct { font-weight:700; }
  #prog .log { color:var(--mut); font-size:12.5px; margin-top:6px; white-space:pre-line; }
  .result { padding:15px; border-radius:10px; margin-top:16px; display:none; }
  .result.ok { display:block; background:#16321f; border:1px solid var(--ok); }
  .result.err { display:block; background:#321616; border:1px solid #a33; }
  .result code { background:#0008; padding:2px 6px; border-radius:5px; word-break:break-all; }
  .linkbtn { background:none; border:0; color:var(--accent); cursor:pointer; font-size:12.5px; width:auto; padding:0; }
</style>
</head>
<body>
<div id="loading"><div class="box">
  <div class="spin"></div>
  <h2 id="ltitle">Reading your conversations…</h2>
  <p id="lmsg">Desmond is reading every conversation straight from Messages on this Mac (read-only) so the list below is complete. On a big history this takes a minute or two.</p>
  <p class="mut" id="lelapsed"></p>
  <p class="mut" id="lretry" style="display:none"><button id="retry" style="background:var(--accent);color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:14px;cursor:pointer">Try again</button></p>
</div></div>
<div class="wrap">
  <h1>📲 Desmond</h1>
  <p class="sub">Pick people and a range, preview exactly what would leave Messages, trim it, then save.</p>

  <div class="card">
    <h2>1 · Who</h2>
    <input type="text" id="search" placeholder="Search conversations… (a name, a number, a group)" autocomplete="off">
    <div class="mut" style="margin:6px 0 8px">
      <button class="linkbtn" id="pickshown">Select all shown</button> ·
      <button class="linkbtn" id="clearshown">Clear all shown</button>
      <span id="pickhint"></span>
    </div>
    <div class="chips" id="chips"></div>
    <div id="plist"><div class="mut" style="padding:12px">Loading conversations…</div></div>
    <div class="mut" id="phint"></div>
  </div>

  <div class="card">
    <h2>2 · How far back</h2>
    <div class="grid" id="ranges">
      <div class="opt on" data-r="all">All time</div>
      <div class="opt" data-r="7d">1 week</div>
      <div class="opt" data-r="30d">1 month</div>
      <div class="opt" data-r="90d">3 months</div>
      <div class="opt" data-r="180d">6 months</div>
      <div class="opt" data-r="365d">1 year</div>
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
    <label class="lbl">Local folder (always kept)</label>
    <input type="text" id="dest" value="__DEFAULT_DEST__">
    <div class="tg on" id="mirror" style="text-align:center;margin-top:12px">☁︎ Also copy to Google Drive</div>
    <div class="mut" style="margin-top:8px">Google Drive: <code>__DRIVE_DEST__</code></div>
    <div class="mut">Saved locally first, then mirrored to Google Drive — so each
      pick (attachments included) lives in <b>both</b> places. After saving, it's
      verified and a <code>VERIFY_REPORT.md</code> is written.</div>
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

      <div id="prog">
      <div><span class="pct" id="ppct">0%</span> · <span id="plabel">Starting…</span></div>
      <div class="bar"><div class="fill" id="pfill"></div></div>
      <div class="log" id="plog"></div>
    </div>
    <div class="result" id="result"></div>
</div>

<script>
const state = { people: new Set(), range: "all", dir: "both", order: "oldest", redact: false, mirror: true, shown: [],
                previewed: null };   // the exact filters the visible preview was built from

function $(id){ return document.getElementById(id); }
// Every value that reaches innerHTML goes through this — conversation names
// and message text are written by whoever texted you, not by us.
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
// Any control change means the preview no longer matches what Save would do:
// hide it, so the only way to save is to preview the current settings first.
function invalidatePreview(){
  state.previewed = null;
  $("preview").style.display = "none";
}

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
      <span class="nm">${esc(p.name)}</span>
      <span class="ct">${Number(p.count||0).toLocaleString()} · ${esc(p.type)}</span>`;
    row.onclick = (e) => {
      if (e.target.tagName !== "INPUT") row.querySelector("input").click();
    };
    row.querySelector("input").onclick = (e) => {
      e.stopPropagation();
      if (e.target.checked) state.people.add(p.name); else state.people.delete(p.name);
      renderChips(); invalidatePreview();
    };
    list.appendChild(row);
  });
  if (!shown.length) list.innerHTML = '<div class="mut" style="padding:12px">No matches.</div>';
  lastShown = shown;
  $("pickhint").textContent = q ? `(${shown.length} match "${filter}")` : `(${shown.length} shown)`;
}
let lastShown = [];
$("pickshown").onclick = () => {
  lastShown.forEach(p => state.people.add(p.name));
  renderChips(); renderPeople($("search").value); invalidatePreview();
};
$("clearshown").onclick = () => {
  lastShown.forEach(p => state.people.delete(p.name));
  renderChips(); renderPeople($("search").value); invalidatePreview();
};
function renderChips() {
  const c = $("chips"); c.innerHTML = "";
  state.people.forEach(name => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name + "  ✕";
    chip.onclick = () => { state.people.delete(name); renderChips(); renderPeople($("search").value); invalidatePreview(); };
    c.appendChild(chip);
  });
}
$("search").oninput = e => renderPeople(e.target.value);

// ---- load the conversation list (gated: nothing is clickable until it's real) ----
let loadTimer = null;
function loadPeople() {
  const t0 = Date.now();
  $("loading").className = "";
  $("ltitle").textContent = "Reading your conversations…";
  $("lmsg").textContent = "Desmond is reading every conversation straight from Messages on this Mac (read-only) so the list below is complete. On a big history this takes a minute or two.";
  $("lretry").style.display = "none";
  clearInterval(loadTimer);
  loadTimer = setInterval(() => { $("lelapsed").textContent = Math.round((Date.now() - t0) / 1000) + " seconds so far…"; }, 1000);
  fetch("/api/people").then(r => { if (!r.ok) throw new Error("server said " + r.status); return r.json(); }).then(people => {
    clearInterval(loadTimer);
    allPeople = people;
    renderPeople("");
    $("phint").textContent = people.length + " conversations found. Busiest first. Pick one or several.";
    $("loading").style.display = "none";
  }).catch(err => {
    clearInterval(loadTimer);
    $("loading").className = "err";
    $("ltitle").textContent = "Could not read Messages";
    $("lmsg").textContent = "Give Terminal Full Disk Access (System Settings → Privacy & Security), quit Terminal with Cmd+Q, and double-click desmond_picker.command again. (" + err.message + ")";
    $("lelapsed").textContent = "";
    $("lretry").style.display = "block";
    $("plist").innerHTML = '<div class="mut" style="padding:12px">Could not read Messages.</div>';
  });
}
$("retry").onclick = loadPeople;
loadPeople();

// ---- ranges ----
document.querySelectorAll("#ranges .opt, [data-r=custom]").forEach(el => el.onclick = () => {
  document.querySelectorAll(".opt").forEach(o => o.classList.remove("on"));
  el.classList.add("on");
  state.range = el.dataset.r;
  $("custom").classList.toggle("show", state.range === "custom");
  invalidatePreview();
});

// ---- type toggles ----
document.querySelectorAll("#types .tg").forEach(el => el.onclick = () => { el.classList.toggle("on"); invalidatePreview(); });
// ---- direction ----
document.querySelectorAll("#dir div").forEach(el => el.onclick = () => {
  document.querySelectorAll("#dir div").forEach(d => d.classList.remove("on"));
  el.classList.add("on"); state.dir = el.dataset.d; invalidatePreview();
});
// ---- order ----
document.querySelectorAll("#order div").forEach(el => el.onclick = () => {
  document.querySelectorAll("#order div").forEach(d => d.classList.remove("on"));
  el.classList.add("on"); state.order = el.dataset.o; invalidatePreview();
});
// ---- redact ----
$("redact").onclick = () => {
  state.redact = !state.redact;
  $("redact").classList.toggle("on", state.redact);
  invalidatePreview();
};
// ---- google drive mirror ----
$("mirror").onclick = () => {
  state.mirror = !state.mirror;
  $("mirror").classList.toggle("on", state.mirror);
  invalidatePreview();
};
// ---- free-text controls (dates, keywords, cap, destination) ----
["start", "end", "include", "exclude", "cap", "dest"].forEach(id => $(id).oninput = invalidatePreview);

function collect() {
  const types = [...document.querySelectorAll("#types .tg.on")].map(t => t.dataset.t);
  return {
    people: [...state.people], range: state.range,
    start: $("start").value, end: $("end").value,
    direction: state.dir, types,
    include: $("include").value, exclude: $("exclude").value,
    cap: $("cap").value, redact: state.redact,
    order: state.order, dest: $("dest").value, mirror_drive: state.mirror,
  };
}

// ---- preview ----
$("go").onclick = () => {
  if (!state.people.size) { alert("Pick at least one person first."); return; }
  const btn = $("go"); btn.disabled = true; btn.textContent = "Loading preview…";
  $("result").className = "result";
  const filters = collect();   // snapshot: Save exports exactly THIS, not later edits
  fetch("/api/preview", { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(filters) })
  .then(r => r.json()).then(d => {
    btn.disabled = false; btn.textContent = "Preview →";
    if (!d.ok) { showErr(d.error); return; }
    state.shown = d.records;
    state.previewed = filters;
    renderPreview(d);
  }).catch(e => { btn.disabled = false; btn.textContent = "Preview →"; showErr(e); });
};

function renderPreview(d) {
  const box = $("msgs"); box.innerHTML = "";
  d.records.forEach(r => {
    const m = document.createElement("label");
    m.className = "m"; m.dataset.id = r.id;
    // The text field carries "[photo]" stand-ins; show the real photos too, so
    // the preview is what the PDF will contain.
    const shown = r.message_type === "reaction" ? "<i>"+esc(r.text)+"</i>" : esc(r.text_plain || (r.attachments && r.attachments.length ? "" : r.text));
    let media = "";
    (r.attachments || []).forEach(a => {
      if (a.category === "photo") media += `<img class="pv" loading="lazy" src="/api/media/${Number(a.id)}" alt="photo" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'pvtag',textContent:'[photo — not downloaded from iCloud]'}))">`;
      else media += `<span class="pvtag">[${esc(a.category)}${a.transfer_name ? ": " + esc(a.transfer_name) : ""}]</span> `;
    });
    m.innerHTML = `<input type="checkbox" checked>
      <span class="meta">${esc(r.date)} ${esc(String(r.time||"").slice(0,5))}<br>${esc(r.person)}</span>
      <span class="body"><b>${esc(r.sender)}:</b> ${shown}${media}</span>`;
    m.querySelector("input").onchange = e => m.classList.toggle("off", !e.target.checked);
    box.appendChild(m);
  });
  let note = `${d.total.toLocaleString()} messages match · photos shown here are the real files that go into the PDF`;
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
  if (!state.previewed) { showErr("Preview first — the settings changed since the last preview."); return; }
  const deselected = [...$("msgs").querySelectorAll(".m")]
    .filter(m => !m.querySelector("input").checked)
    .map(m => parseInt(m.dataset.id));
  const btn = $("save"); btn.disabled = true; btn.textContent = "Saving…";
  $("result").className = "result"; $("result").innerHTML = "";
  $("prog").style.display = "block"; $("pfill").style.width = "0%"; $("ppct").textContent = "0%";
  $("plabel").textContent = "Starting…"; $("plog").textContent = "";
  $("prog").scrollIntoView({behavior:"smooth"});
  fetch("/api/export", { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ ...state.previewed, deselected }) })
  .then(r => r.json()).then(start => {
    if (!start.ok) { btn.disabled=false; btn.textContent="Save export"; $("prog").style.display="none"; showErr(start.error || "Failed."); return; }
    return new Promise((resolve, reject) => {
      const t0 = Date.now();
      const tick = () => fetch("/api/progress/" + start.job).then(r => r.json()).then(p => {
        const secs = Math.round((Date.now() - t0) / 1000);
        const el = secs >= 60 ? `${Math.floor(secs/60)}m ${secs%60}s` : `${secs}s`;
        $("pfill").style.width = p.percent + "%"; $("ppct").textContent = p.percent + "%";
        $("plabel").textContent = p.label + (p.total ? ` (${Number(p.done).toLocaleString()} of ${Number(p.total).toLocaleString()})` : "") + ` · ${el} elapsed`;
        $("plog").textContent = (p.log || []).join("\n");
        if (p.finished) resolve(p.result); else setTimeout(tick, 700);
      }).catch(reject);
      tick();
    });
  }).then(d => {
    if (!d) return;
    btn.disabled = false; btn.textContent = "Save export";
    $("prog").style.display = "none";
    const res = $("result");
    if (d.ok) {
      res.className = "result ok";
      const att = d.attachments_saved ? ` · <b>${Number(d.attachments_saved).toLocaleString()}</b> attachments` : "";
      const miss = d.attachments_missing ? ` (${Number(d.attachments_missing)} not downloaded from iCloud)` : "";
      let where = d.pdf_path
        ? `<br><br>📄 <b>Your PDF</b> (everything in order, photos inline) — it just opened: <code>${esc(d.pdf_path)}</code>`
        : `<br><br>⚠️ No automatic PDF: ${esc(d.pdf_error || "")}`;
      if (d.pdf_sections && d.pdf_sections.length) {
        where += `<br><br><b>One PDF per conversation</b> (same folder):`;
        d.pdf_sections.forEach(sct => {
          where += sct.pdf ? `<br>📄 ${esc(sct.name)} — ${Number(sct.messages).toLocaleString()} messages — <code>${esc(sct.pdf.split("/").pop())}</code>`
                           : `<br>⚠️ ${esc(sct.name)} — no PDF: ${esc(sct.error || "")}`;
        });
      }
      where += `<br>Folder with the transcript + the original photo/video files: <code>${esc(d.folder)}</code>`;
      if (d.drive_folder) where += `<br>Google Drive: <code>${esc(d.drive_folder)}</code>`;
      if (d.drive_error) where += `<br>⚠️ Google Drive: ${esc(d.drive_error)}`;
      let vr = "";
      if (d.attachments_saved) {
        vr = `<br><br>Verified — local ${Number(d.in_local)}/${Number(d.attachments_saved)}`
           + (d.drive_folder ? `, Drive ${Number(d.in_drive)}/${Number(d.attachments_saved)}` : "")
           + (d.missing_drive ? ` ⚠️ ${Number(d.missing_drive)} not yet on Drive` : " ✅");
      }
      res.innerHTML = `✅ Saved <b>${Number(d.count).toLocaleString()}</b> messages${att}${miss} (${esc(d.first)} → ${esc(d.last)}).`
        + where + vr
        + `<br><br><code>conversation.html</code> in that folder is the same thing as a web page (videos play there; the PDF shows a caption for them). <code>VERIFY_REPORT.md</code> has the per-place check.`;
    } else { res.className = "result err"; res.innerHTML = "⚠️ " + esc(d.error || "Failed."); }
    res.scrollIntoView({behavior:"smooth"});
  }).catch(e => { btn.disabled=false; btn.textContent="Save export"; $("prog").style.display="none"; showErr(e); });
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

    def _same_origin(self):
        """Only our own page may call the POST endpoints. A random website the
        user visits while the picker runs can fire cross-origin POSTs at
        127.0.0.1 (a CORS 'simple request' needs no preflight) — without this
        check it could silently trigger a full export to disk/Drive."""
        origin = self.headers.get("Origin")
        if origin is None:            # same-machine curl/scripts have no Origin
            return True
        return origin.rstrip("/") in (f"http://127.0.0.1:{PORT}",
                                      f"http://localhost:{PORT}")

    def _host_ok(self):
        """Refuse requests whose Host header isn't our own loopback address.
        A DNS-rebinding attack (evil.example resolving to 127.0.0.1) lets a
        web page read /api/people and POST exports; the browser sends the
        attacker's hostname in Host, so this check blocks it."""
        host = (self.headers.get("Host") or "").strip().lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self._host_ok():
            self._send(403, json.dumps({"error": "unexpected Host header"}))
            return
        if self.path.startswith("/api/media/"):
            self._serve_media(self.path[len("/api/media/"):])
            return
        if self.path.startswith("/api/progress/"):
            job = self.path[len("/api/progress/"):].split("?")[0]
            with _JOBS_LOCK:
                prog = _JOBS.get(job)
            if not prog:
                self._send(404, json.dumps({"error": "no such job"}))
                return
            self._send(200, json.dumps(prog.snapshot()))
            return
        if self.path == "/" or self.path.startswith("/index"):
            page = PAGE.replace("__DEFAULT_DEST__", _h(default_dest()))
            page = page.replace(
                "__DRIVE_DEST__",
                _h(drive_picks_base() or "not detected — will save locally only"))
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/people":
            try:
                self._send(200, json.dumps(list_people()))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _serve_media(self, ident):
        """Serve the REAL photo for one attachment so the preview shows what the
        PDF will contain. Only photos, only by attachment ROWID looked up in
        chat.db (never a client-supplied path), only on the loopback Host."""
        try:
            att_id = int(ident.split("?")[0])
        except ValueError:
            self._send(404, json.dumps({"error": "bad id"}))
            return
        path, mime = preview_photo_path(att_id)
        if not path:
            self._send(404, json.dumps({"error": "no such photo"}))
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self._send(404, json.dumps({"error": "unreadable"}))
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            if not self._host_ok():
                self._send(403, json.dumps({"ok": False,
                                            "error": "unexpected Host header"}))
                return
            if not self._same_origin():
                self._send(403, json.dumps({"ok": False,
                                            "error": "cross-origin request refused"}))
                return
            payload = self._read_json()
            if self.path == "/api/preview":
                records = apply_order(gather(payload), payload.get("order", "oldest"))
                shown = records[:PREVIEW_LIMIT]
                self._send(200, json.dumps({
                    "ok": True, "total": len(records), "records": shown,
                    "redacted": bool(payload.get("redact")),
                }))
            elif self.path == "/api/export":
                people = payload.get("people") or []
                if not people:
                    # An empty selection must export NOTHING — without this
                    # guard it would export every conversation.
                    self._send(200, json.dumps({
                        "ok": False,
                        "error": "No people selected — pick at least one "
                                 "conversation before exporting."}))
                    return
                # Runs in the background; the page polls /api/progress/<job>.
                self._send(200, json.dumps({"ok": True, "job": start_export_job(payload)}))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}))


PORT_RANGE = range(8765, 8786)   # 8765..8785: try the next one if a picker is already up


def bind_server(ports=PORT_RANGE):
    """Bind the picker to the first free port in `ports`, updating the module-wide
    PORT so the URL, the Host allow-list and the same-origin check all agree.
    Returns the server, or None if every port is busy."""
    global PORT
    for port in ports:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            continue
        PORT = port
        return server
    return None


def main():
    import desmond_sources
    state = desmond_sources.messages_db_state(MESSAGES_DB)
    if state == "no_access":
        print(desmond_sources.FDA_FIX_MESSAGE)
        if sys.platform == "darwin":
            try:  # open the exact settings pane so nothing needs to be hunted for
                subprocess.run(["open", "x-apple.systempreferences:com.apple."
                                "preference.security?Privacy_AllFiles"], check=False)
            except Exception:
                pass
        sys.exit(3)   # 3 = Full Disk Access needed (launcher keys off this)
    if state != "ok":
        print("Could not find your Messages database at ~/Library/Messages/chat.db")
        print("This tool only runs on a Mac with the Messages app set up.")
        sys.exit(1)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    server = bind_server()
    if server is None:
        print(f"Every port from {PORT_RANGE[0]} to {PORT_RANGE[-1]} is busy — is another "
              "Desmond Picker already running? Close it (Control-C in its Terminal "
              "window) and try again.")
        sys.exit(2)
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
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
