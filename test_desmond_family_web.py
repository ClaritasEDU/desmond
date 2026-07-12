#!/usr/bin/env python3
"""
Synthetic test for desmond_family_web.py — boots the real wizard server on
an ephemeral port and drives it over real HTTP, exactly like the browser
does: consent → attach ONE parent's iPhone-shaped messages + the OTHER
parent's Android XML (the mixed-household case) → connect both calendars
through stubbed Google/Microsoft sign-ins → pull the gap report → save the
archive. Also checks the security posture (no state before consent,
cross-site POSTs blocked, nothing on disk until Save).
"""

import base64
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import desmond_family_web as web
import desmond_calendar_auth as ca


def msg(ts, conv, sender, from_me, text, conv_type="direct"):
    return {"timestamp": ts, "date": ts[:10], "time": ts[11:19],
            "conversation": conv, "conversation_type": conv_type,
            "sender": sender, "is_from_me": from_me, "message_type": "text",
            "text": text, "has_attachment": False, "reaction": None}


CHRIS_JSON = json.dumps({          # the iPhone parent (any Desmond export)
    "conversations": [{"name": "Kate", "type": "direct"},
                      {"name": "Smile Dental", "type": "direct"}],
    "messages": [
        msg("2026-07-01T09:00:00", "Kate", "Me", True, "On my way"),
        msg("2026-07-01T09:05:00", "Kate", "Kate", False, "Grab milk"),
        msg("2026-07-08T10:00:00", "Smile Dental", "Smile Dental", False,
            "Reminder: Emma's cleaning Jul 20 at 2pm"),
    ]}).encode()

KATE_XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<smses count="3">
  <sms address="+15125550777" date="1782896400000" type="1"
       body="On my way" contact_name="Chris" />
  <sms address="+15125550777" date="1782896700000" type="2"
       body="Grab milk" contact_name="Chris" />
  <sms address="+15125550142" date="1783245600000" type="1"
       body="Practice moved to 5pm" contact_name="Coach Dan" />
</smses>"""

GOOGLE_EVENTS = [{"title": "Soccer Photos", "start": "2026-07-25T09:00:00",
                  "all_day": False, "calendar": "Family"}]
MS_EVENTS = [{"title": "Soccer Photos", "start": "2026-07-25T09:00:00",
              "all_day": False, "calendar": "Outlook"},
             {"title": "School Assembly", "start": "2026-07-24",
              "all_day": True, "calendar": "Outlook"}]


def main():
    failures = []

    def check(cond, label):
        print(("PASS" if cond else "FAIL") + ": " + label)
        if not cond:
            failures.append(label)

    tmp = tempfile.mkdtemp()
    ca.CLIENTS_CONFIG = os.path.join(tmp, "oauth_clients.json")
    ca.TOKENS_CONFIG = os.path.join(tmp, "calendar_tokens.json")
    ca._save_private_json(ca.CLIENTS_CONFIG, {
        "google": {"client_id": "gid", "client_secret": "gs"},
        "microsoft": {"client_id": "mid"}})

    # Stub the provider network calls the wizard makes.
    def fake_id_token(email):
        p = base64.urlsafe_b64encode(
            json.dumps({"email": email}).encode()).decode().rstrip("=")
        return f"h.{p}.s"

    def fake_http(url, data=None, headers=None, method=None):
        if url == ca.GOOGLE_TOKEN:
            return {"access_token": "AT", "refresh_token": "RT",
                    "id_token": fake_id_token("chris@example.com")}
        if "calendarList" in url:
            return {"items": [{"id": "primary", "summary": "Family",
                               "primary": True}]}
        if "/events" in url:
            return {"items": [
                {"summary": e["title"],
                 "start": ({"date": e["start"]} if e["all_day"]
                           else {"dateTime": e["start"]})}
                for e in GOOGLE_EVENTS]}
        if url == ca.MS_DEVICECODE:
            return {"device_code": "DC", "user_code": "ABC-123",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "interval": 0}
        if url == ca.MS_TOKEN:
            return {"access_token": "MAT", "refresh_token": "MRT",
                    "id_token": fake_id_token("kate@outlook.com")}
        if url.startswith(ca.MS_GRAPH):
            return {"value": [
                {"subject": e["title"], "isAllDay": e["all_day"],
                 "start": {"dateTime": e["start"] +
                           ("T00:00:00" if e["all_day"] else "")}}
                for e in MS_EVENTS]}
        raise AssertionError(f"unexpected {url}")

    ca._http = fake_http     # single funnel — patches every flow at once

    # Boot the real server on an ephemeral port.
    web.reset_session()
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    web.PORT = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{web.PORT}"

    def call(path, body=None, raw=None, headers=None, expect_error=False):
        data = raw if raw is not None else (
            json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except Exception:
                return e.code, {}

    # ---- the page and state come up ----
    with urllib.request.urlopen(base + "/") as r:
        page = r.read().decode()
    check("Desmond Family" in page and "consent" in page.lower(),
          "wizard page serves with the consent step")
    code, st = call("/api/state")
    check(code == 200 and st["state"]["parents"] == [],
          "state starts empty")

    # ---- nothing works before consent ----
    code, resp = call("/api/source/mac", {"parent": "Chris"})
    check(code == 400 and "step 1" in resp["error"],
          "sources are locked until consent")
    code, resp = call("/api/report", {})
    check(code == 400, "report is locked until consent")

    # ---- cross-site requests are blocked ----
    code, resp = call("/api/consent",
                      {"parents": [{"name": "A"}, {"name": "B"}]},
                      headers={"Origin": "https://evil.example"})
    check(code == 403, "cross-site POST rejected")

    # ---- DNS-rebinding guard: wrong Host header rejected on GET and POST ----
    req = urllib.request.Request(base + "/api/state",
                                 headers={"Host": "evil.example"})
    try:
        urllib.request.urlopen(req)
        check(False, "rebound Host on GET rejected")
    except urllib.error.HTTPError as e:
        check(e.code == 403, "rebound Host on GET rejected")
    code, resp = call("/api/reset", {}, headers={"Host": "evil.example:80"})
    check(code == 403, "rebound Host on POST rejected")

    # ---- consent ----
    code, resp = call("/api/consent",
                      {"parents": [{"name": "Chris"}, {"name": "Kate"}]})
    check(code == 200 and resp["ok"], "consent for two named parents")
    code, resp = call("/api/consent", {"parents": [{"name": "Solo"}]})
    check(code == 400, "consent requires two different names")
    call("/api/consent", {"parents": [{"name": "Chris"}, {"name": "Kate"}]})

    # ---- mixed household: iPhone-shaped JSON + Android XML ----
    code, resp = call("/api/source/upload?parent=Chris&filename=messages.json",
                      raw=CHRIS_JSON)
    check(code == 200, "Chris's (iPhone) messages attached via upload")
    code, resp = call("/api/source/upload?parent=Kate&filename=sms.xml",
                      raw=KATE_XML)
    check(code == 200, "Kate's (Android) SMS XML attached via upload")
    code, st = call("/api/state")
    m = st["state"]["messages"]
    check(m["Chris"]["count"] == 3 and m["Kate"]["count"] == 3,
          f"both parents show attached counts (got {m})")

    # ---- calendars via sign-in (stubbed providers) ----
    code, resp = call("/api/calendar/google/start", {"parent": "Chris"})
    check(code == 200 and resp["url"].startswith(ca.GOOGLE_AUTH),
          "google connect returns the provider sign-in URL")
    state_param = dict(p.split("=", 1) for p in
                       resp["url"].split("?", 1)[1].split("&"))["state"]
    with urllib.request.urlopen(
            f"{base}/oauth/google?code=X&state={state_param}") as r:
        html = r.read().decode()
    check("connected" in html.lower() and "Chris" in html,
          "google redirect lands, exchanges, and confirms in-browser")

    # A provider exploding mid-redirect must show the friendly page, not
    # drop the connection (do_GET exception guard).
    code2, resp2 = call("/api/calendar/google/start", {"parent": "Chris"})
    state2 = dict(p.split("=", 1) for p in
                  resp2["url"].split("?", 1)[1].split("&"))["state"]
    real_http, boom = ca._http, (lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("provider exploded")))
    ca._http = boom
    try:
        req = urllib.request.Request(
            f"{base}/oauth/google?code=X&state={state2}")
        try:
            with urllib.request.urlopen(req) as r:
                body, status = r.read().decode(), r.status
        except urllib.error.HTTPError as e:
            body, status = e.read().decode(), e.code
        check(status == 500 and "didn't finish" in body,
              "provider crash during redirect shows the warning page")
    finally:
        ca._http = real_http

    code, resp = call("/api/calendar/microsoft/start", {"parent": "Kate"})
    check(code == 200 and resp["user_code"] == "ABC-123",
          "microsoft connect shows the device code")
    code, resp = call("/api/calendar/microsoft/poll", {"parent": "Kate"})
    check(code == 200 and resp["pending"] is False,
          "microsoft poll completes the sign-in")
    code, st = call("/api/state")
    c = st["state"]["calendars"]
    check("chris@example.com" in c["Chris"]["label"]
          and "kate@outlook.com" in c["Kate"]["label"],
          f"calendars labeled with the signed-in accounts (got {c})")

    # ---- the report: iPhone parent vs Android parent ----
    code, resp = call("/api/report", {"since": "2026-06-01"})
    check(code == 200, "report builds")
    gaps = resp["family"]["gaps"]
    threads = [g["conversation"] for g in gaps["threads"]]
    check(threads == ["Smile Dental", "Coach Dan"] or
          sorted(threads) == ["Coach Dan", "Smile Dental"],
          f"cross-platform thread gaps found (got {threads})")
    cal_titles = [g["title"] for g in gaps["calendar"]]
    check(cal_titles == ["School Assembly"],
          f"calendar gap found across Google vs Outlook (got {cal_titles})")
    check(resp["family"]["messages"]["deduplicated"] == 2,
          "the couple's own thread deduplicated across iPhone and Android")
    consent = resp["family"]["consent"]["records"]
    check(len(consent) == 2 and all(r["via"] == "desmond_family_web"
                                    for r in consent),
          "consent trail from the wizard is embedded in the payload")

    # ---- nothing on disk until Save ----
    outdir = os.path.join(tmp, "archive")
    web.DEFAULT_OUTPUT_DIR = outdir
    check(not os.path.exists(outdir), "nothing written before Save")
    code, resp = call("/api/save", {})
    check(code == 200 and os.path.isfile(os.path.join(outdir,
                                                      "FAMILY_GAPS.md")),
          "Save writes the archive where it said it would")

    # ---- rescan forces a fresh device scan ----
    code, resp = call("/api/rescan", {})
    check(code == 200 and resp["ok"], "rescan endpoint answers")

    # ---- detach + reset ----
    code, resp = call("/api/source/detach", {"parent": "Kate",
                                             "kind": "messages"})
    code, st = call("/api/state")
    check("Kate" not in st["state"]["messages"], "detach removes a source")
    code, resp = call("/api/report", {})
    check(code == 400 and "Chris only" in resp["error"],
          "one-sided messages give a fix-it error, not a bogus report")
    call("/api/reset", {})
    code, st = call("/api/state")
    check(st["state"]["parents"] == [], "reset forgets everything")

    server.shutdown()
    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
