#!/usr/bin/env python3
"""
Desmond Picker - a simple browser interface for exporting iMessages.

Pick a person, pick a time range (last day / week / month / etc.),
click Export. Output lands in ~/Downloads/iMessages_Export/_picks/.

Run with:  python3 imessage_picker.py
Opens automatically in your browser. Nothing is uploaded anywhere.
"""

import os
import re
import csv
import json
import sqlite3
import webbrowser
import threading
import subprocess
from io import StringIO
from datetime import datetime, timedelta
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Reuse the battle-tested contact + timestamp helpers from the main exporter
import imessage_exporter as core

MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")
OUTPUT_DIR = os.path.expanduser("~/Downloads/iMessages_Export/_picks")
PORT = 8765

# Apple stores dates as nanoseconds since 2001-01-01
APPLE_EPOCH_OFFSET = 978307200

# Readable reaction labels for the transcript
REACTIONS = {
    2000: "loved", 2001: "liked", 2002: "disliked",
    2003: "laughed at", 2004: "emphasized", 2005: "questioned",
    3000: "removed loved", 3001: "removed liked", 3002: "removed disliked",
    3003: "removed laughed", 3004: "removed emphasized", 3005: "removed questioned",
}

RANGE_LABELS = {
    "1d": "Last 24 hours",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "365d": "Last year",
    "all": "All time",
    "custom": "Custom range",
}

_contacts_loaded = False


def ensure_contacts():
    global _contacts_loaded
    if not _contacts_loaded:
        core.load_contacts()
        _contacts_loaded = True


def apple_cutoff(days_back):
    """Return an Apple-format timestamp for `days_back` days ago."""
    cutoff = datetime.now() - timedelta(days=days_back)
    return int((cutoff.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def apple_from_date(date_str, end_of_day=False):
    """Convert a YYYY-MM-DD string to an Apple-format timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt + timedelta(days=1)  # inclusive of the end date
    return int((dt.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


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
    """Yield message rows (optionally bounded by Apple timestamps)."""
    clauses = []
    params = []
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
               message.balloon_bundle_id, chat.chat_identifier, chat.display_name
        FROM message
        LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
        {where}
        ORDER BY message.date ASC
    """, params)
    return cursor.fetchall()


def attachments_for(cursor, since_apple=None):
    """Map message_id -> list of attachment category labels."""
    cursor.execute("""
        SELECT message_attachment_join.message_id, attachment.mime_type, attachment.transfer_name
        FROM attachment
        JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id
    """)
    result = defaultdict(list)
    for msg_id, mime_type, transfer_name in cursor.fetchall():
        if mime_type and mime_type.startswith("image"):
            result[msg_id].append("photo")
        elif mime_type and mime_type.startswith("video"):
            result[msg_id].append("video")
        elif mime_type and mime_type.startswith("audio"):
            result[msg_id].append("audio")
        else:
            result[msg_id].append("file")
    return result


def list_people():
    """Return [{name, type, count, last}] for every conversation, busiest first."""
    ensure_contacts()
    conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)
    cursor = conn.cursor()
    meta = {}
    for row in iter_messages(cursor):
        rowid, text, date, is_from_me, handle_id, assoc, balloon, chat_id, display_name = row
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
    people = sorted(meta.values(), key=lambda x: x["count"], reverse=True)
    return people


def resolve_range(range_key, start=None, end=None):
    """Translate a range selection into (since_apple, until_apple) bounds."""
    days = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365}
    if range_key in days:
        return apple_cutoff(days[range_key]), None
    if range_key == "all":
        return None, None
    if range_key == "custom":
        since = apple_from_date(start) if start else None
        until = apple_from_date(end, end_of_day=True) if end else None
        return since, until
    return None, None


def build_record(row, attachments_by_msg, cursor):
    """Turn a raw row into a clean message dict (or None to skip)."""
    rowid, text, date, is_from_me, handle_id, assoc, balloon, chat_id, display_name = row
    dt = core.convert_apple_time(date)
    if dt is None:
        return None

    sender = "Me" if is_from_me else (
        core.get_contact_name(handle_id, cursor) if handle_id else "Unknown")

    attachments = attachments_by_msg.get(rowid, [])
    reaction = None
    msg_type = "text"
    content = text

    if assoc and assoc in REACTIONS:
        msg_type = "reaction"
        reaction = REACTIONS[assoc]
        content = text or reaction
    elif attachments:
        msg_type = "text_with_attachment" if text else "attachment"
        if not text:
            content = " ".join(f"[{a}]" for a in attachments)
    elif not text and balloon:
        msg_type = "special"
        content = "[app content]"
    elif not text:
        return None  # empty/system message

    return {
        "timestamp": dt.isoformat(),
        "date": dt.strftime("%Y-%m-%d"),
        "time": dt.strftime("%H:%M:%S"),
        "sender": sender,
        "is_from_me": bool(is_from_me),
        "message_type": msg_type,
        "text": content,
        "has_attachment": len(attachments) > 0,
        "attachment_types": attachments,
        "reaction": reaction,
    }


def safe_name(name):
    return "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in str(name)).strip()


def export_pick(person, range_key, start=None, end=None):
    """Export one conversation within a time range. Returns a result dict."""
    ensure_contacts()
    since, until = resolve_range(range_key, start, end)

    conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)
    cursor = conn.cursor()
    attachments_by_msg = attachments_for(cursor)

    records = []
    for row in iter_messages(cursor, since, until):
        _, _, _, _, handle_id, _, _, chat_id, display_name = row
        name, _ = conversation_name(handle_id, chat_id, display_name, cursor)
        if str(name) != person:
            continue
        record = build_record(row, attachments_by_msg, cursor)
        if record:
            records.append(record)
    conn.close()

    if not records:
        return {"ok": False, "error": "No messages found for that person in that time range."}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(OUTPUT_DIR, f"{safe_name(person)}_{range_key}_{stamp}")
    os.makedirs(folder, exist_ok=True)

    # 1. Readable transcript grouped by date
    transcript = os.path.join(folder, "conversation.md")
    by_date = defaultdict(list)
    for r in records:
        by_date[r["date"]].append(r)
    with open(transcript, "w", encoding="utf-8") as f:
        f.write(f"# Messages with {person}\n\n")
        f.write(f"**Range:** {RANGE_LABELS.get(range_key, range_key)}  \n")
        f.write(f"**Messages:** {len(records):,}  \n")
        f.write(f"**From:** {records[0]['date']} **to** {records[-1]['date']}\n\n")
        for date_str in sorted(by_date):
            f.write(f"\n## {date_str}\n\n")
            for r in by_date[date_str]:
                line = r["text"]
                if r["message_type"] == "reaction":
                    line = f"*{r['text']}*"
                f.write(f"**{r['time'][:5]} — {r['sender']}:** {line}\n\n")

    # 2. JSON for Claude
    json_path = os.path.join(folder, "messages.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "person": person,
            "range": RANGE_LABELS.get(range_key, range_key),
            "exported": datetime.now().isoformat(),
            "message_count": len(records),
            "messages": records,
        }, f, indent=2)

    # 3. CSV for spreadsheets
    csv_path = os.path.join(folder, "messages.csv")
    fields = ["timestamp", "date", "time", "sender", "is_from_me",
              "message_type", "text", "has_attachment", "attachment_types", "reaction"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            row = r.copy()
            row["attachment_types"] = ",".join(row["attachment_types"])
            writer.writerow(row)

    # Try to open the folder in Finder
    try:
        subprocess.run(["open", folder], check=False)
    except Exception:
        pass

    return {
        "ok": True,
        "count": len(records),
        "folder": folder,
        "first": records[0]["date"],
        "last": records[-1]["date"],
    }


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Desmond — Message Picker</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --line:#2a2f3a; --txt:#e7eaf0; --mut:#9aa3b2; --accent:#4f8cff; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .wrap { max-width:620px; margin:0 auto; padding:32px 20px 64px; }
  h1 { font-size:26px; margin:0 0 4px; }
  .sub { color:var(--mut); margin:0 0 28px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px; margin-bottom:20px; }
  label { display:block; font-weight:600; margin-bottom:10px; }
  input, select { width:100%; padding:12px 14px; background:#0f1115; color:var(--txt);
    border:1px solid var(--line); border-radius:10px; font-size:16px; }
  .ranges { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .range { padding:12px; border:1px solid var(--line); border-radius:10px; text-align:center;
    cursor:pointer; user-select:none; transition:.15s; }
  .range:hover { border-color:var(--accent); }
  .range.on { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
  .custom { display:none; gap:10px; margin-top:12px; }
  .custom.show { display:flex; }
  .custom > div { flex:1; }
  .custom label { font-weight:500; font-size:13px; color:var(--mut); }
  button { width:100%; padding:15px; background:var(--accent); color:#fff; border:0;
    border-radius:10px; font-size:17px; font-weight:600; cursor:pointer; margin-top:8px; }
  button:disabled { opacity:.5; cursor:default; }
  .result { padding:16px; border-radius:10px; margin-top:18px; display:none; }
  .result.ok { display:block; background:#16321f; border:1px solid #2e7d4f; }
  .result.err { display:block; background:#321616; border:1px solid #a33; }
  .result code { background:#0008; padding:2px 6px; border-radius:5px; word-break:break-all; }
  .hint { color:var(--mut); font-size:13px; margin-top:8px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📲 Desmond</h1>
  <p class="sub">Export your texts with one person, for any time range.</p>

  <div class="card">
    <label for="person">Who do you want to export?</label>
    <select id="person"><option>Loading conversations…</option></select>
    <p class="hint" id="people-hint"></p>
  </div>

  <div class="card">
    <label>How far back?</label>
    <div class="ranges" id="ranges">
      <div class="range" data-r="1d">Last 24 hours</div>
      <div class="range on" data-r="7d">Last 7 days</div>
      <div class="range" data-r="30d">Last 30 days</div>
      <div class="range" data-r="90d">Last 90 days</div>
      <div class="range" data-r="365d">Last year</div>
      <div class="range" data-r="all">All time</div>
    </div>
    <div class="range" data-r="custom" style="margin-top:10px">Custom date range</div>
    <div class="custom" id="custom">
      <div><label>From</label><input type="date" id="start"></div>
      <div><label>To</label><input type="date" id="end"></div>
    </div>
  </div>

  <button id="go">Export messages</button>
  <div class="result" id="result"></div>
</div>

<script>
let range = "7d";

function pickRange(el) {
  document.querySelectorAll(".range").forEach(r => r.classList.remove("on"));
  el.classList.add("on");
  range = el.dataset.r;
  document.getElementById("custom").classList.toggle("show", range === "custom");
}
document.querySelectorAll(".range").forEach(el => el.onclick = () => pickRange(el));

fetch("/api/people").then(r => r.json()).then(people => {
  const sel = document.getElementById("person");
  sel.innerHTML = "";
  people.forEach(p => {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = `${p.name}  (${p.count.toLocaleString()} msgs)`;
    sel.appendChild(o);
  });
  document.getElementById("people-hint").textContent =
    people.length + " conversations found. Most active are at the top.";
}).catch(() => {
  document.getElementById("people-hint").textContent =
    "Could not read Messages. Make sure Terminal has Full Disk Access, then restart.";
});

document.getElementById("go").onclick = () => {
  const btn = document.getElementById("go");
  const res = document.getElementById("result");
  const person = document.getElementById("person").value;
  res.className = "result";
  btn.disabled = true; btn.textContent = "Exporting…";
  fetch("/api/export", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      person, range,
      start: document.getElementById("start").value,
      end: document.getElementById("end").value,
    })
  }).then(r => r.json()).then(d => {
    btn.disabled = false; btn.textContent = "Export messages";
    if (d.ok) {
      res.className = "result ok";
      res.innerHTML = `✅ Exported <b>${d.count.toLocaleString()}</b> messages `
        + `(${d.first} → ${d.last}).<br><br>Saved to:<br><code>${d.folder}</code>`
        + `<br><br>The folder just opened in Finder. Upload <code>messages.json</code> to Claude.`;
    } else {
      res.className = "result err";
      res.innerHTML = "⚠️ " + (d.error || "Something went wrong.");
    }
  }).catch(e => {
    btn.disabled = false; btn.textContent = "Export messages";
    res.className = "result err";
    res.innerHTML = "⚠️ " + e;
  });
};
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/people":
            try:
                self._send(200, json.dumps(list_people()))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/api/export":
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or "{}")
            result = export_pick(
                payload.get("person"),
                payload.get("range", "7d"),
                payload.get("start") or None,
                payload.get("end") or None,
            )
            self._send(200, json.dumps(result))
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
    print("=" * 50)
    print("  Desmond Picker is running.")
    print(f"  Open this in your browser:  {url}")
    print("  Press Control-C here to stop.")
    print("=" * 50)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. See you in another life, brother.")
        server.shutdown()


if __name__ == "__main__":
    main()
