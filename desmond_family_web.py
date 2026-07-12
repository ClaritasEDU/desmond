#!/usr/bin/env python3
"""
Desmond Family — the in-browser flow. One command, zero file shuffling:

    cd ~/desmond
    python3 desmond_family_web.py

Your browser opens a private page served ONLY on this computer
(127.0.0.1). The wizard walks both parents through four steps:

    1. Names + consent — each parent explicitly agrees on screen; the
       consent trail is embedded in the result.
    2. Messages — each parent attaches theirs by PLUGGING IN, not exporting:
         • "Messages on this Mac" — read directly (nothing to plug in)
         • iPhone — plug it in, make/refresh the local backup when asked
           (Finder / iTunes / Apple Devices), the wizard reads it in place
         • Android — plug it in with USB debugging on; read live over the
           cable (android_adb_exporter)
         • or drop a file on the page (a Desmond messages.json, or an SMS
           Backup & Restore .xml straight off the phone's storage)
       One parent on iPhone and the other on Android is fully supported —
       every source lands in the same export shape before federating.
    3. Calendars — each parent clicks **Connect Google** or **Connect
       Microsoft** and signs in; no secret iCal links to hunt down
       (desmond_calendar_auth; a paste-a-link fallback hides under
       "Advanced" for iCloud published calendars).
    4. The gap report — rendered right on the page: events only on one
       calendar, texts only one parent received, threads that only ever
       talk to one of you.

Privacy model
-------------
Everything stays in this process's memory. NOTHING is written to disk
unless you click "Save archive" on the report (PII-safe run logs are the
one exception — counts and timings only, never message text or names).
Closing the terminal forgets everything. The server binds to 127.0.0.1
and rejects cross-site requests, same as the Desmond picker.

ParentPoint note: this file is also the reference client for the pure
in-memory pipeline (desmond_sources → desmond_calendar_auth →
federate_family_data). A hosted ParentPoint can reuse those modules
verbatim; only this thin HTTP/HTML layer is local-specific.
"""

import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import desmond_calendar_auth as cal_auth
import desmond_sources as sources
from desmond_consolidate import fetch_calendar_feed, normalize_feed_url, _feed_label
from desmond_family import (federate_family_data, federate_family,
                            parse_calendar, DEFAULT_OUTPUT_DIR)
from desmond_log import RunLogger

PORT = 8756          # first choice; main() walks forward if it's taken
MAX_UPLOAD = 1 << 30  # 1 GB — SMS backups with inline media get big

LOG = None           # RunLogger, set in main()


def _log(level, msg, **fields):
    if LOG:
        LOG.log(level, msg, **fields)


# ---------------------------------------------------------------------------
# Wizard session — all state lives here, in memory only
# ---------------------------------------------------------------------------

SESSION = {}


def reset_session():
    SESSION.clear()
    SESSION.update({
        "parents": [],          # [{"name", "agreed_at"}]
        "messages": {},         # name -> export dict
        "message_labels": {},   # name -> human label
        "calendars": {},        # name -> [events]
        "calendar_labels": {},  # name -> human label
        "pending_google": {},   # oauth state -> {verifier, parent, redirect}
        "pending_ms": {},       # parent -> device-code payload
    })


reset_session()


class WizardError(Exception):
    """A user-facing problem; its text is shown in the browser as-is."""


def _parent_names():
    return [p["name"] for p in SESSION["parents"]]


def _require_consent(parent=None):
    if len(SESSION["parents"]) < 2:
        raise WizardError("Start with step 1 — both names and both consent "
                          "boxes are required first.")
    if parent is not None and parent not in _parent_names():
        raise WizardError(f"Unknown parent {parent!r}.")


def set_consent(parents):
    """parents: [{"name": str}, {"name": str}] with both boxes ticked
    (the client only posts when both are)."""
    names = [str(p.get("name", "")).strip() for p in (parents or [])]
    names = [n for n in names if n]
    if len(names) != 2 or names[0].lower() == names[1].lower():
        raise WizardError("Two different names are needed.")
    reset_session()
    now = datetime.now().isoformat(timespec="seconds")
    SESSION["parents"] = [{"name": n, "agreed_at": now} for n in names]
    _log("info", "consent recorded", parents=2)


def attach_messages(parent, export, label):
    _require_consent(parent)
    SESSION["messages"][parent] = export
    SESSION["message_labels"][parent] = label
    _log("info", "messages attached", label=label,
         count=export.get("total_messages", len(export.get("messages", []))))


def attach_calendar(parent, events, label):
    _require_consent(parent)
    SESSION["calendars"][parent] = events
    SESSION["calendar_labels"][parent] = label
    _log("info", "calendar attached", label=label, count=len(events))


def wizard_state():
    """Everything the page needs to draw itself, refreshed on every poll."""
    avail = sources.detect_available()
    return {
        "parents": _parent_names(),
        "messages": {
            name: {"label": SESSION["message_labels"].get(name),
                   "count": SESSION["messages"][name].get(
                       "total_messages",
                       len(SESSION["messages"][name].get("messages", [])))}
            for name in SESSION["messages"]
        },
        "calendars": {
            name: {"label": SESSION["calendar_labels"].get(name),
                   "count": len(SESSION["calendars"][name])}
            for name in SESSION["calendars"]
        },
        "available": {
            "platform": avail["platform"],
            "mac_messages": avail["mac_messages"],
            "iphone_backups": avail["iphone_backups"],
            "adb_installed": avail["adb_installed"],
            "android_devices": avail["android_devices"],
        },
        "providers": cal_auth.provider_status(),
        "saved_accounts": cal_auth.saved_accounts(),
        "pending_ms": {p: {"user_code": v["user_code"],
                           "verification_uri": v["verification_uri"]}
                       for p, v in SESSION["pending_ms"].items()},
    }


def _federation_inputs():
    _require_consent()
    names = _parent_names()
    msg_have = [n for n in names if n in SESSION["messages"]]
    cal_have = [n for n in names if n in SESSION["calendars"]]
    if len(msg_have) == 1:
        raise WizardError(
            f"Messages are attached for {msg_have[0]} only — attach "
            f"{[n for n in names if n not in msg_have][0]}'s too, or detach "
            "and compare calendars only.")
    if len(cal_have) == 1:
        raise WizardError(
            f"A calendar is connected for {cal_have[0]} only — connect "
            f"{[n for n in names if n not in cal_have][0]}'s too, or skip "
            "calendars for both.")
    if not msg_have and not cal_have:
        raise WizardError("Attach messages and/or calendars for both "
                          "parents first (steps 2 and 3).")
    message_exports = ([(n, SESSION["messages"][n]) for n in names]
                       if msg_have else None)
    calendar_exports = ([(n, SESSION["calendars"][n]) for n in names]
                        if cal_have else None)
    consent_records = [{"participant": p["name"], "agreed_at": p["agreed_at"],
                        "via": "desmond_family_web"}
                       for p in SESSION["parents"]]
    return message_exports, calendar_exports, consent_records


def build_report(since=None, all_history=False, keywords=None):
    message_exports, calendar_exports, consent_records = _federation_inputs()
    if not since and not all_history:
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    result = federate_family_data(
        message_exports=message_exports, calendar_exports=calendar_exports,
        consented=True, consent_records=consent_records,
        since=since or None, keywords=keywords or None)
    counts = result["family"]["gap_counts"]
    _log("info", "report built", **counts)
    return {"family": result["family"], "gaps_md": result["gaps_md"],
            "summary_md": result["summary_md"]}


def save_archive():
    """The ONE action that writes family data to disk — explicit click."""
    message_exports, calendar_exports, consent_records = _federation_inputs()
    res = federate_family(
        message_exports=message_exports, calendar_exports=calendar_exports,
        output_dir=DEFAULT_OUTPUT_DIR, consented=True,
        consent_records=consent_records, verbose=False)
    _log("info", "archive saved", dir=res["output_dir"])
    return res


# ---------------------------------------------------------------------------
# Message-source actions
# ---------------------------------------------------------------------------

def source_mac(parent):
    _require_consent(parent)      # consent gates every read, not just attach
    export = sources.read_mac_messages()
    attach_messages(parent, export, "Messages on this Mac")


def source_iphone(parent, path):
    _require_consent(parent)
    backups = {b["path"]: b for b in sources.find_iphone_backups()}
    if path not in backups:
        raise WizardError("That backup isn't there any more — hit Rescan.")
    export = sources.read_iphone_backup(path)
    attach_messages(parent, export,
                    f"iPhone backup — {backups[path]['name']}")


def source_android(parent, serial=None):
    _require_consent(parent)
    export = sources.read_android_usb(serial or None)
    attach_messages(parent, export, "Android phone over USB")


def source_upload(parent, data, filename):
    _require_consent(parent)
    export = sources.parse_upload(data, filename)
    attach_messages(parent, export, f"File: {filename}")


def source_detach(parent, kind):
    _require_consent(parent)
    store, labels = (("messages", "message_labels") if kind == "messages"
                     else ("calendars", "calendar_labels"))
    SESSION[store].pop(parent, None)
    SESSION[labels].pop(parent, None)


# ---------------------------------------------------------------------------
# Calendar actions (OAuth first, link as fallback)
# ---------------------------------------------------------------------------

def google_start(parent, port):
    _require_consent(parent)
    state = secrets.token_urlsafe(24)
    redirect = f"http://127.0.0.1:{port}/oauth/google"
    url, verifier = cal_auth.google_begin(redirect, state)
    SESSION["pending_google"][state] = {"verifier": verifier,
                                        "parent": parent,
                                        "redirect": redirect}
    return url


def google_finish(params):
    """Handle Google's redirect. Returns (parent, error_or_None)."""
    state = (params.get("state") or [""])[0]
    pending = SESSION["pending_google"].pop(state, None)
    if not pending:
        return None, "This sign-in link is stale — go back and click Connect again."
    if params.get("error"):
        return pending["parent"], f"Google said: {params['error'][0]}"
    code = (params.get("code") or [""])[0]
    try:
        tokens = cal_auth.google_exchange_code(code, pending["verifier"],
                                               pending["redirect"])
        events = cal_auth.fetch_calendar_events("google", tokens)
    except cal_auth.AuthError as e:
        return pending["parent"], str(e)
    attach_calendar(pending["parent"], parse_calendar(events, "google"),
                    f"Google Calendar — {tokens['email']}")
    return pending["parent"], None


def microsoft_start(parent):
    _require_consent(parent)
    resp = cal_auth.microsoft_begin()
    SESSION["pending_ms"][parent] = resp
    return {"user_code": resp["user_code"],
            "verification_uri": resp["verification_uri"]}


def microsoft_poll(parent):
    pending = SESSION["pending_ms"].get(parent)
    if not pending:
        raise WizardError("No Microsoft sign-in in progress — click Connect.")
    tokens = cal_auth.microsoft_poll_token(pending["device_code"])
    if tokens is None:
        return {"pending": True}
    SESSION["pending_ms"].pop(parent, None)
    events = cal_auth.fetch_calendar_events("microsoft", tokens)
    attach_calendar(parent, parse_calendar(events, "microsoft"),
                    f"Outlook — {tokens['email']}")
    return {"pending": False}


def saved_account_connect(parent, provider, email):
    _require_consent(parent)
    tokens = (cal_auth.google_refresh(email) if provider == "google"
              else cal_auth.microsoft_refresh(email))
    events = cal_auth.fetch_calendar_events(provider, tokens)
    label = ("Google Calendar" if provider == "google" else "Outlook")
    attach_calendar(parent, parse_calendar(events, provider),
                    f"{label} — {email}")


def calendar_link(parent, url):
    """Advanced fallback (iCloud published calendars etc.)."""
    _require_consent(parent)
    normalized = normalize_feed_url(url)
    if not normalized:
        raise WizardError("That doesn't look like an https:// or webcal:// "
                          "calendar address.")
    text = fetch_calendar_feed(normalized)
    events = parse_calendar(text, _feed_label(normalized))
    attach_calendar(parent, events,
                    f"Calendar feed — {_feed_label(normalized)}")


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "DesmondFamily/1"

    def log_message(self, fmt, *args):      # quiet the default stderr spam
        _log("info", "http", line=(fmt % args))

    # -- helpers ------------------------------------------------------------
    def _same_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin.rstrip("/") in (f"http://127.0.0.1:{PORT}",
                                      f"http://localhost:{PORT}")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text, code=200):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1 << 20))
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            raise WizardError("Bad request body.")

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            return self._html(PAGE)
        if url.path == "/api/state":
            return self._json({"ok": True, "state": wizard_state()})
        if url.path == "/oauth/google":
            params = urllib.parse.parse_qs(url.query)
            parent, error = google_finish(params)
            if error:
                return self._html(OAUTH_PAGE.format(
                    icon="⚠️", title="Sign-in didn't finish",
                    detail=error.replace("<", "&lt;")))
            return self._html(OAUTH_PAGE.format(
                icon="✅", title="Google Calendar connected",
                detail=f"Connected for {parent}. Close this tab and return "
                       "to the Desmond wizard."))
        return self._json({"ok": False, "error": "Not found"}, 404)

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        if not self._same_origin():
            return self._json({"ok": False, "error": "Cross-site request "
                               "blocked."}, 403)
        url = urllib.parse.urlsplit(self.path)
        try:
            return self._route_post(url)
        except WizardError as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except (sources.SourceError, cal_auth.AuthError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:                       # never a blank 500
            if LOG:
                LOG.exception("unhandled")
            return self._json({"ok": False,
                               "error": f"Unexpected problem: {e}"}, 500)

    def _route_post(self, url):
        path = url.path
        if path == "/api/consent":
            set_consent(self._body_json().get("parents"))
            return self._json({"ok": True})
        if path == "/api/reset":
            reset_session()
            return self._json({"ok": True})

        if path == "/api/source/upload":
            q = urllib.parse.parse_qs(url.query)
            parent = (q.get("parent") or [""])[0]
            filename = os.path.basename((q.get("filename") or ["upload"])[0])
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                raise WizardError("That file is over 1 GB. In SMS Backup & "
                                  "Restore, back up WITHOUT media and use "
                                  "that smaller file.")
            data = self.rfile.read(length)
            source_upload(parent, data, filename)
            return self._json({"ok": True})

        body = self._body_json()
        parent = body.get("parent", "")
        if path == "/api/source/mac":
            source_mac(parent)
        elif path == "/api/source/iphone":
            source_iphone(parent, body.get("path", ""))
        elif path == "/api/source/android":
            source_android(parent, body.get("serial"))
        elif path == "/api/source/detach":
            source_detach(parent, body.get("kind", "messages"))
        elif path == "/api/calendar/google/start":
            return self._json({"ok": True,
                               "url": google_start(parent, PORT)})
        elif path == "/api/calendar/microsoft/start":
            return self._json({"ok": True,
                               **microsoft_start(parent)})
        elif path == "/api/calendar/microsoft/poll":
            return self._json({"ok": True, **microsoft_poll(parent)})
        elif path == "/api/calendar/saved":
            saved_account_connect(parent, body.get("provider", ""),
                                  body.get("email", ""))
        elif path == "/api/calendar/link":
            calendar_link(parent, body.get("url", ""))
        elif path == "/api/report":
            return self._json({"ok": True, **build_report(
                since=body.get("since"),
                all_history=bool(body.get("all")),
                keywords=[k for k in (body.get("keywords") or []) if k])})
        elif path == "/api/save":
            res = save_archive()
            return self._json({"ok": True, "output_dir": res["output_dir"],
                               "gaps_path": res["gaps_path"]})
        else:
            return self._json({"ok": False, "error": "Not found"}, 404)
        return self._json({"ok": True})


OAUTH_PAGE = """<!doctype html><meta charset="utf-8">
<body style="font-family:-apple-system,system-ui,sans-serif;display:grid;
place-items:center;height:95vh;background:#f6f7f9">
<div style="text-align:center;max-width:26em">
<div style="font-size:56px">{icon}</div><h2>{title}</h2>
<p style="color:#444">{detail}</p></div>"""


# The single-page wizard. No external assets — everything inline.
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Desmond Family</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c1e21;--sub:#5b6570;--line:#e4e7eb;
--accent:#2f6fed;--ok:#188650;--warn:#b54708;--bad:#c0392b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,system-ui,"Segoe UI",sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;margin:.2em 0 0}
h1+p{color:var(--sub);margin-top:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px 22px;margin-top:18px}
.card h2{font-size:18px;margin:0 0 4px}
.card .hint{color:var(--sub);font-size:14px;margin:0 0 14px}
.badge{display:inline-block;font-size:12px;font-weight:600;border-radius:99px;
padding:2px 10px;margin-left:8px;vertical-align:2px}
.badge.done{background:#e6f4ec;color:var(--ok)}
.badge.todo{background:#eef1f5;color:var(--sub)}
input[type=text]{font:inherit;padding:8px 10px;border:1px solid var(--line);
border-radius:8px;width:100%;max-width:260px}
label.chk{display:flex;gap:8px;align-items:flex-start;font-size:14px;
color:var(--sub);margin-top:8px;max-width:420px}
button{font:inherit;font-size:14px;font-weight:600;border:1px solid var(--line);
background:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:disabled{opacity:.45;cursor:default}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:10px}
.parent{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}
.parent h3{margin:0 0 6px;font-size:15px}
.status-ok{color:var(--ok);font-weight:600;font-size:14px}
.err{color:var(--bad);font-size:14px;margin-top:8px;white-space:pre-wrap}
.note{color:var(--sub);font-size:13px;margin-top:8px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
.code{font-size:22px;letter-spacing:2px;font-weight:700}
details{margin-top:10px}summary{cursor:pointer;color:var(--sub);font-size:14px}
.gap{border:1px solid var(--line);border-left:4px solid var(--warn);
border-radius:8px;padding:10px 14px;margin-top:10px;font-size:15px}
.gap .who{color:var(--sub);font-size:13px}
.gap.cal{border-left-color:var(--accent)}
.allgood{border:1px solid var(--line);border-left:4px solid var(--ok);
border-radius:8px;padding:10px 14px;margin-top:10px;color:var(--ok)}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);
border-top-color:var(--accent);border-radius:50%;
animation:r .8s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes r{to{transform:rotate(360deg)}}
h4.gaphead{margin:20px 0 2px}
.drop{border:2px dashed var(--line);border-radius:8px;padding:10px 14px;
font-size:14px;color:var(--sub)}
.drop.over{border-color:var(--accent);color:var(--accent)}
footer{color:var(--sub);font-size:13px;margin-top:26px;text-align:center}
</style></head><body><div class="wrap">
<h1>👨‍👩‍👧 Desmond Family</h1>
<p>Find what one of you has that the other doesn&rsquo;t &mdash; missed
texts, missing calendar events. Everything stays on this computer, in
memory, until you say otherwise.</p>

<div class="card" id="card-consent">
 <h2>1. Who&rsquo;s comparing? <span class="badge todo" id="b-consent">to do</span></h2>
 <p class="hint">Both people must agree &mdash; this combines your private
 messages and calendars into one view you&rsquo;ll both see.</p>
 <div class="row"><input type="text" id="name1" placeholder="Parent 1 (e.g. Chris)">
 <input type="text" id="name2" placeholder="Parent 2 (e.g. Kate)"></div>
 <label class="chk"><input type="checkbox" id="agree1"><span id="agree1t">Parent 1
 agrees to combine their messages/calendar into this shared view</span></label>
 <label class="chk"><input type="checkbox" id="agree2"><span id="agree2t">Parent 2
 agrees to combine their messages/calendar into this shared view</span></label>
 <div class="row"><button class="primary" id="btn-consent">Start</button>
 <button id="btn-reset" style="display:none">Start over</button></div>
 <div class="err" id="err-consent"></div>
</div>

<div class="card" id="card-messages" style="display:none">
 <h2>2. Messages <span class="badge todo" id="b-messages">to do</span></h2>
 <p class="hint">Attach each phone&rsquo;s texts. Plug the phone into THIS
 computer &mdash; no exporting, no emailing files around. (You can also skip
 messages and compare calendars only.)</p>
 <div id="msg-parents"></div>
 <div class="err" id="err-messages"></div>
</div>

<div class="card" id="card-calendars" style="display:none">
 <h2>3. Calendars <span class="badge todo" id="b-calendars">to do</span></h2>
 <p class="hint">Each parent signs in &mdash; that&rsquo;s it. Google and
 Microsoft/Outlook are supported. (Skippable: compare messages only.)</p>
 <div id="cal-parents"></div>
 <div class="err" id="err-calendars"></div>
</div>

<div class="card" id="card-report" style="display:none">
 <h2>4. The gap report</h2>
 <p class="hint">What only one of you has. Default window: the last 30 days
 plus everything upcoming.</p>
 <div class="row">
  <label style="font-size:14px">Since <input type="text" id="since"
   placeholder="YYYY-MM-DD" style="max-width:130px"></label>
  <label style="font-size:14px"><input type="checkbox" id="allhist"> all history</label>
  <input type="text" id="kw" placeholder="only mentioning… (optional)"
   style="max-width:220px">
  <button class="primary" id="btn-report">Show me the gaps</button>
 </div>
 <div class="err" id="err-report"></div>
 <div id="report"></div>
 <div class="row" id="save-row" style="display:none">
  <button id="btn-save">Save archive to this computer</button>
  <span class="note" id="save-note"></span>
 </div>
</div>

<footer>Desmond Family &mdash; local only (127.0.0.1). Nothing leaves this
computer; nothing is saved unless you click Save.
<br>&ldquo;See you in another life, brother.&rdquo;</footer>
</div>
<script>
"use strict";
let S=null, msPoll={};
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",
">":"&gt;",'"':"&quot;"}[c]));
async function api(path,body){
  const r=await fetch(path,{method:"POST",headers:{"Content-Type":
  "application/json"},body:JSON.stringify(body||{})});
  const j=await r.json().catch(()=>({ok:false,error:"Server hiccup"}));
  if(!j.ok) throw new Error(j.error||"Something went wrong");
  return j;
}
async function refresh(){
  try{const r=await fetch("/api/state");const j=await r.json();
  S=j.state;draw();}catch(e){}
}
function busy(el,on){el.querySelectorAll("button").forEach(b=>b.disabled=on);}

/* ---------- consent ---------- */
["name1","name2"].forEach((id,i)=>$(id).addEventListener("input",()=>{
  const v=$(id).value.trim()||("Parent "+(i+1));
  $("agree"+(i+1)+"t").textContent=v+
  " agrees to combine their messages/calendar into this shared view";
}));
$("btn-consent").onclick=async()=>{
  $("err-consent").textContent="";
  if(!$("agree1").checked||!$("agree2").checked){
    $("err-consent").textContent="Both consent boxes are required.";return;}
  try{await api("/api/consent",{parents:[{name:$("name1").value},
    {name:$("name2").value}]});await refresh();}
  catch(e){$("err-consent").textContent=e.message;}
};
$("btn-reset").onclick=async()=>{await api("/api/reset");location.reload();};

/* ---------- messages step ---------- */
function msgParentHtml(name){
  const at=S.messages[name];
  if(at) return `<div class="parent"><h3>${esc(name)}</h3>
   <span class="status-ok">✅ ${esc(at.label)} — ${at.count.toLocaleString()}
   messages</span> <button data-detach-m="${esc(name)}">detach</button></div>`;
  const a=S.available;let rows="";
  if(a.mac_messages) rows+=`<button data-mac="${esc(name)}">💻 Use this
   Mac's Messages</button>`;
  const ok=a.iphone_backups.filter(b=>b.has_messages);
  rows+=ok.map(b=>`<button data-iphone="${esc(name)}"
   data-path="${esc(b.path)}">📱 iPhone: ${esc(b.name)}
   ${b.date?"("+esc(b.date.slice(0,10))+")":""}</button>`).join("");
  const enc=a.iphone_backups.filter(b=>!b.has_messages);
  const dev=a.android_devices;
  if(dev.some(d=>d.state==="device"))
    rows+=`<button data-android="${esc(name)}">🤖 Read Android phone
     over USB</button>`;
  rows+=`<button data-rescan="1">🔄 Rescan</button>`;
  let notes="";
  if(enc.length) notes+=`<div class="note">⚠️ Found a backup for
   ${esc(enc[0].name)} but it's encrypted/unreadable — plug the iPhone in,
   untick "Encrypt local backup", back up again, then Rescan.</div>`;
  if(dev.some(d=>d.state==="unauthorized")) notes+=`<div class="note">🤖 An
   Android phone is waiting — unlock it and tap <b>Allow</b> on the "Allow
   USB debugging?" prompt, then Rescan.</div>`;
  notes+=`<details><summary>My phone isn't listed</summary><div class="note">
   <b>iPhone:</b> plug it in and make a local backup — Mac: Finder → your
   iPhone → "Back Up Now" (untick encryption). Windows: iTunes or Apple
   Devices app → Back Up Now. Then hit Rescan.<br>
   <b>Android:</b> Settings → About phone → tap "Build number" 7× →
   Developer options → USB debugging ON → plug in → tap Allow → Rescan.
   ${a.adb_installed?"":"(This computer also needs Google's free "+
   "platform-tools: developer.android.com/tools/releases/platform-tools)"}
   <br><b>RCS note (Android):</b> "chat features" conversations can't be
   read by any tool without rooting; SMS — where school/dentist reminders
   live — is captured.</div></details>
   <div class="drop" data-drop="${esc(name)}">…or drop a file here:
   a Desmond <span class="mono">messages.json</span>, or an SMS Backup &amp;
   Restore <span class="mono">.xml</span> straight off the phone's storage
   <input type="file" style="display:none"></div>`;
  return `<div class="parent"><h3>${esc(name)}</h3>
   <div class="row">${rows}</div>${notes}</div>`;
}

/* ---------- calendars step ---------- */
function calParentHtml(name){
  const at=S.calendars[name];
  if(at) return `<div class="parent"><h3>${esc(name)}</h3>
   <span class="status-ok">✅ ${esc(at.label)} — ${at.count.toLocaleString()}
   events</span> <button data-detach-c="${esc(name)}">detach</button></div>`;
  const p=S.providers;let rows="",notes="";
  if(p.google) rows+=`<button data-google="${esc(name)}">Connect Google
   Calendar</button>`;
  if(p.microsoft) rows+=`<button data-ms="${esc(name)}">Connect
   Microsoft / Outlook</button>`;
  for(const acc of S.saved_accounts){
    if((acc.provider==="google"&&p.google)||(acc.provider==="microsoft"&&p.microsoft))
      rows+=`<button data-saved="${esc(name)}" data-provider="${esc(acc.provider)}"
       data-email="${esc(acc.email)}">↻ ${esc(acc.email)}</button>`;}
  const ms=S.pending_ms[name];
  if(ms) notes+=`<div class="note">On any device, go to
   <b>${esc(ms.verification_uri)}</b> and enter
   <span class="code mono">${esc(ms.user_code)}</span>
   <span class="spin"></span> waiting for sign-in…</div>`;
  if(!p.google&&!p.microsoft) notes+=`<div class="note">⚠️ Calendar sign-in
   isn't configured on this computer yet (one-time developer setup — see
   the top of <span class="mono">desmond_calendar_auth.py</span>). The link
   option below still works.</div>`;
  notes+=`<details><summary>Advanced: paste a calendar link (iCloud
   published calendars)</summary>
   <div class="row"><input type="text" data-linkinput="${esc(name)}"
   placeholder="https://… or webcal://…" style="max-width:340px">
   <button data-link="${esc(name)}">Add</button></div></details>`;
  return `<div class="parent"><h3>${esc(name)}</h3>
   <div class="row">${rows}</div>${notes}</div>`;
}

/* ---------- draw ---------- */
function draw(){
  const started=S.parents.length===2;
  $("b-consent").className="badge "+(started?"done":"todo");
  $("b-consent").textContent=started?"done":"to do";
  $("btn-consent").style.display=started?"none":"";
  $("btn-reset").style.display=started?"":"none";
  ["name1","name2","agree1","agree2"].forEach(id=>$(id).disabled=started);
  if(started){$("name1").value=S.parents[0];$("name2").value=S.parents[1];
    $("agree1").checked=$("agree2").checked=true;}
  $("card-messages").style.display=started?"":"none";
  $("card-calendars").style.display=started?"":"none";
  $("card-report").style.display=started?"":"none";
  if(!started) return;
  const mDone=Object.keys(S.messages).length,
        cDone=Object.keys(S.calendars).length;
  $("b-messages").className="badge "+(mDone===2?"done":"todo");
  $("b-messages").textContent=mDone===2?"done":(mDone+"/2");
  $("b-calendars").className="badge "+(cDone===2?"done":"todo");
  $("b-calendars").textContent=cDone===2?"done":(cDone+"/2");
  $("msg-parents").innerHTML=S.parents.map(msgParentHtml).join("");
  $("cal-parents").innerHTML=S.parents.map(calParentHtml).join("");
  wire();
}

function wire(){
  document.querySelectorAll("[data-mac]").forEach(b=>b.onclick=
    ()=>act(b,"/api/source/mac",{parent:b.dataset.mac},"err-messages"));
  document.querySelectorAll("[data-iphone]").forEach(b=>b.onclick=
    ()=>act(b,"/api/source/iphone",{parent:b.dataset.iphone,
      path:b.dataset.path},"err-messages"));
  document.querySelectorAll("[data-android]").forEach(b=>b.onclick=
    ()=>act(b,"/api/source/android",{parent:b.dataset.android},"err-messages"));
  document.querySelectorAll("[data-rescan]").forEach(b=>b.onclick=refresh);
  document.querySelectorAll("[data-detach-m]").forEach(b=>b.onclick=
    ()=>act(b,"/api/source/detach",{parent:b.dataset.detachM,
      kind:"messages"},"err-messages"));
  document.querySelectorAll("[data-detach-c]").forEach(b=>b.onclick=
    ()=>act(b,"/api/source/detach",{parent:b.dataset.detachC,
      kind:"calendars"},"err-calendars"));
  document.querySelectorAll("[data-google]").forEach(b=>b.onclick=async()=>{
    $("err-calendars").textContent="";
    try{const r=await api("/api/calendar/google/start",
      {parent:b.dataset.google});window.open(r.url,"_blank");}
    catch(e){$("err-calendars").textContent=e.message;}});
  document.querySelectorAll("[data-ms]").forEach(b=>b.onclick=async()=>{
    $("err-calendars").textContent="";
    try{await api("/api/calendar/microsoft/start",{parent:b.dataset.ms});
      startMsPoll(b.dataset.ms);await refresh();}
    catch(e){$("err-calendars").textContent=e.message;}});
  document.querySelectorAll("[data-saved]").forEach(b=>b.onclick=
    ()=>act(b,"/api/calendar/saved",{parent:b.dataset.saved,
      provider:b.dataset.provider,email:b.dataset.email},"err-calendars"));
  document.querySelectorAll("[data-link]").forEach(b=>b.onclick=()=>{
    const inp=document.querySelector(
      `[data-linkinput="${CSS.escape(b.dataset.link)}"]`);
    act(b,"/api/calendar/link",{parent:b.dataset.link,url:inp.value},
      "err-calendars");});
  document.querySelectorAll("[data-drop]").forEach(z=>{
    const input=z.querySelector("input");
    z.onclick=()=>input.click();
    input.onchange=()=>upload(z.dataset.drop,input.files[0]);
    z.ondragover=e=>{e.preventDefault();z.classList.add("over");};
    z.ondragleave=()=>z.classList.remove("over");
    z.ondrop=e=>{e.preventDefault();z.classList.remove("over");
      if(e.dataTransfer.files[0]) upload(z.dataset.drop,
        e.dataTransfer.files[0]);};});
}
async function act(btn,path,body,errId){
  $(errId).textContent="";busy(btn.closest(".card"),true);
  const old=btn.textContent;btn.innerHTML='<span class="spin"></span>working…';
  try{await api(path,body);}catch(e){$(errId).textContent=e.message;}
  btn.textContent=old;busy(btn.closest(".card"),false);await refresh();
}
async function upload(parent,file){
  if(!file) return;$("err-messages").textContent="";
  try{
    const r=await fetch("/api/source/upload?"+new URLSearchParams(
      {parent,filename:file.name}),{method:"POST",body:file});
    const j=await r.json();if(!j.ok) throw new Error(j.error);
  }catch(e){$("err-messages").textContent=e.message;}
  await refresh();
}
function startMsPoll(parent){
  clearInterval(msPoll[parent]);
  msPoll[parent]=setInterval(async()=>{
    try{const r=await api("/api/calendar/microsoft/poll",{parent});
      if(!r.pending){clearInterval(msPoll[parent]);await refresh();}}
    catch(e){clearInterval(msPoll[parent]);
      $("err-calendars").textContent=e.message;await refresh();}
  },5000);
}

/* ---------- report ---------- */
$("btn-report").onclick=async()=>{
  $("err-report").textContent="";$("report").innerHTML=
  '<div class="note"><span class="spin"></span>Comparing…</div>';
  try{
    const kw=$("kw").value.trim();
    const r=await api("/api/report",{since:$("since").value.trim()||null,
      all:$("allhist").checked,keywords:kw?[kw]:[]});
    renderReport(r.family);$("save-row").style.display="";
  }catch(e){$("err-report").textContent=e.message;$("report").innerHTML="";}
};
function renderReport(f){
  const g=f.gaps,me=f.participants;let h="";
  const total=g.calendar.length+g.messages.length+g.threads.length;
  if(total===0){h='<div class="allgood">✅ No gaps found — everything in '+
    'scope is on both of your phones/calendars.</div>';}
  if(f.calendar!==null&&f.calendar!==undefined){
    h+=`<h4 class="gaphead">📅 Calendar — only on one calendar
      (${g.calendar.length})</h4>`;
    if(!g.calendar.length&&total) h+='<div class="allgood">✅ Your calendars agree.</div>';
    for(const ev of g.calendar){
      const when=ev.all_day?ev.start.slice(0,10)+" (all day)"
        :ev.start.slice(0,10)+" "+ev.start.slice(11,16);
      h+=`<div class="gap cal"><b>${esc(when)} — ${esc(ev.title)}</b>
       ${ev.location?"@ "+esc(ev.location):""}
       <div class="who">On ${esc(ev.seen_by.join(", "))}'s calendar —
       ${esc(ev.missing_for.join(", "))} doesn't have it</div></div>`;}
  }
  if(f.messages!==null&&f.messages!==undefined){
    h+=`<h4 class="gaphead">💬 Texts only one of you received
      (${g.messages.length})</h4>`;
    if(!g.messages.length&&total) h+='<div class="allgood">✅ Shared threads match.</div>';
    for(const m of g.messages){
      h+=`<div class="gap"><b>${esc(m.date)} ${esc(m.time.slice(0,5))} ·
       ${esc(m.conversation)}:</b> ${esc(m.text)}
       <div class="who">Only ${esc(m.owner)} got this —
       ${esc(m.missing_for.join(", "))} has the thread but not the
       message</div></div>`;}
    h+=`<h4 class="gaphead">📥 People who only text one of you
      (${g.threads.length})</h4>`;
    if(!g.threads.length&&total) h+='<div class="allgood">✅ Every contact reaches you both.</div>';
    for(const t of g.threads){
      h+=`<div class="gap"><b>${esc(t.conversation)}</b> only texts
       ${esc(t.owner)} — ${t.incoming_count} incoming message(s), last
       ${esc((t.last_message||"").slice(0,10))}
       <div class="who">${esc(t.missing_for.join(", "))} never hears from
       them — worth forwarding or adding them to the thread</div></div>`;}
  }
  $("report").innerHTML=h;
}
$("btn-save").onclick=async()=>{
  $("save-note").textContent="";
  try{const r=await api("/api/save");
    $("save-note").textContent="Saved → "+r.output_dir;}
  catch(e){$("save-note").textContent=e.message;}
};

refresh();setInterval(refresh,4000);
</script></body></html>
"""


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    global PORT, LOG
    LOG = RunLogger("desmond_family_web")
    server = None
    for port in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            PORT = port
            break
        except OSError:
            continue
    if server is None:
        print("❌ Couldn't find a free local port.")
        sys.exit(1)

    url = f"http://127.0.0.1:{PORT}/"
    print("👨‍👩‍👧 Desmond Family — private local page")
    print(f"   Open: {url}")
    print("   Everything stays in memory on this computer. Ctrl-C to quit "
          "(forgets everything).")
    _log("info", "server started", port=PORT)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n   Bye — nothing was kept unless you clicked Save.")
        LOG.close(status="ok")


if __name__ == "__main__":
    main()
