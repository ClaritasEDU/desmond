#!/usr/bin/env python3
"""
Synthetic test for desmond_calendar_auth.py — a stub stands in for Google's
and Microsoft's servers, so this verifies both sign-in flows end to end
(PKCE URL building, code exchange, device-code polling, refresh), the
private token cache, and event normalization. No network, no real accounts.
"""

import base64
import json
import os
import stat
import tempfile
import urllib.parse

import desmond_calendar_auth as ca


def fake_id_token(email):
    payload = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


def main():
    failures = []

    def check(cond, label):
        print(("PASS" if cond else "FAIL") + ": " + label)
        if not cond:
            failures.append(label)

    tmp = tempfile.mkdtemp()
    ca.CLIENTS_CONFIG = os.path.join(tmp, "oauth_clients.json")
    ca.TOKENS_CONFIG = os.path.join(tmp, "calendar_tokens.json")
    for var in ("DESMOND_GOOGLE_CLIENT_ID", "DESMOND_GOOGLE_CLIENT_SECRET",
                "DESMOND_MS_CLIENT_ID"):
        os.environ.pop(var, None)

    # ---- unconfigured providers are reported, not crashed ----
    check(ca.provider_status() == {"google": False, "microsoft": False},
          "no config -> both providers off")
    try:
        ca.google_begin("http://127.0.0.1:1/oauth/google", "s")
        check(False, "google_begin without config raises AuthError")
    except ca.AuthError:
        check(True, "google_begin without config raises AuthError")

    # ---- configure via file (chmod 600 pattern) + env override ----
    ca._save_private_json(ca.CLIENTS_CONFIG, {
        "google": {"client_id": "gid", "client_secret": "gsecret"},
        "microsoft": {"client_id": "mid"}})
    check(ca.provider_status() == {"google": True, "microsoft": True},
          "config file enables both providers")
    mode = stat.S_IMODE(os.stat(ca.CLIENTS_CONFIG).st_mode)
    check(mode == 0o600, f"client config written private (got {oct(mode)})")

    # ---- Google: auth URL + PKCE ----
    url, verifier = ca.google_begin("http://127.0.0.1:8756/oauth/google",
                                    "state123")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    check(q["client_id"] == ["gid"] and q["state"] == ["state123"]
          and q["code_challenge_method"] == ["S256"]
          and "calendar.readonly" in q["scope"][0]
          and q["access_type"] == ["offline"],
          "google auth URL carries PKCE, state, offline access, calendar scope")
    check(len(verifier) >= 43, "PKCE verifier is long enough")

    # ---- Google: code exchange + token cache ----
    calls = []

    def http_google(url, data=None, headers=None, method=None):
        calls.append((url, data))
        if url == ca.GOOGLE_TOKEN and data.get("grant_type") == "authorization_code":
            check(data["code_verifier"] == verifier,
                  "exchange sends the same PKCE verifier")
            return {"access_token": "AT1", "refresh_token": "RT1",
                    "id_token": fake_id_token("chris@example.com")}
        if url == ca.GOOGLE_TOKEN and data.get("grant_type") == "refresh_token":
            return {"access_token": "AT2"}
        raise AssertionError(f"unexpected call {url}")

    tokens = ca.google_exchange_code("thecode", verifier,
                                     "http://127.0.0.1:8756/oauth/google",
                                     http=http_google)
    check(tokens["email"] == "chris@example.com",
          "account email pulled from the id_token")
    saved = ca.saved_accounts()
    check(saved == [{"provider": "google", "email": "chris@example.com"}],
          f"refresh token cached for one-click reuse (got {saved})")
    mode = stat.S_IMODE(os.stat(ca.TOKENS_CONFIG).st_mode)
    check(mode == 0o600, f"token cache written private (got {oct(mode)})")
    t2 = ca.google_refresh("chris@example.com", http=http_google)
    check(t2["access_token"] == "AT2" and t2["refresh_token"] == "RT1",
          "google_refresh reuses the cached refresh token")

    # ---- Google: event fetch + normalization ----
    def http_gcal(url, data=None, headers=None, method=None):
        check(headers.get("Authorization") == "Bearer AT2",
              "calendar request sends the bearer token") \
            if "calendarList" in url else None
        if "calendarList" in url:
            return {"items": [{"id": "primary", "summary": "Family"},
                              {"id": "work", "summary": "Work",
                               "selected": False}]}
        if "/calendars/primary/events" in url:
            return {"items": [
                {"summary": "Emma dentist",
                 "start": {"dateTime": "2026-07-20T14:00:00-05:00"},
                 "end": {"dateTime": "2026-07-20T15:00:00-05:00"},
                 "location": "Smile Dental"},
                {"summary": "School assembly",
                 "start": {"date": "2026-07-24"},
                 "end": {"date": "2026-07-25"}},
                {"start": {}},   # broken event dropped
            ]}
        raise AssertionError(f"unexpected call {url}")

    events = ca.fetch_google_events("AT2", http=http_gcal, now=1783504800)
    check(len(events) == 2, f"2 events fetched, deselected calendar and "
          f"broken event skipped (got {len(events)})")
    dentist = next(e for e in events if e["title"] == "Emma dentist")
    check(dentist["start"].startswith("2026-07-20T14:00")
          and dentist["location"] == "Smile Dental"
          and dentist["calendar"] == "Family" and not dentist["all_day"],
          "timed Google event normalized")
    assembly = next(e for e in events if e["title"] == "School assembly")
    check(assembly["all_day"] and assembly["start"] == "2026-07-24",
          "all-day Google event normalized")

    # ---- Microsoft: device code flow ----
    ms_state = {"polls": 0}

    def http_ms(url, data=None, headers=None, method=None):
        if url == ca.MS_DEVICECODE:
            return {"device_code": "DC1", "user_code": "ABC-DEF",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "interval": 5}
        if url == ca.MS_TOKEN and data.get("device_code") == "DC1":
            ms_state["polls"] += 1
            if ms_state["polls"] < 2:
                return {"error": "authorization_pending"}
            return {"access_token": "MAT", "refresh_token": "MRT",
                    "id_token": fake_id_token("kate@outlook.com")}
        if url.startswith(ca.MS_GRAPH):
            return {"value": [
                {"subject": "Soccer photos",
                 "start": {"dateTime": "2026-07-25T09:00:00.0000000"},
                 "end": {"dateTime": "2026-07-25T10:00:00.0000000"},
                 "location": {"displayName": "Field 3"}},
                {"subject": "Cancelled thing", "isCancelled": True,
                 "start": {"dateTime": "2026-07-26T09:00:00.0000000"}},
            ]}
        raise AssertionError(f"unexpected call {url}")

    begin = ca.microsoft_begin(http=http_ms)
    check(begin["user_code"] == "ABC-DEF",
          "device flow returns the code to show the parent")
    check(ca.microsoft_poll_token("DC1", http=http_ms) is None,
          "pending poll returns None (keep waiting)")
    tokens = ca.microsoft_poll_token("DC1", http=http_ms)
    check(tokens and tokens["email"] == "kate@outlook.com",
          "second poll lands the tokens + email")
    accounts = {(a["provider"], a["email"]) for a in ca.saved_accounts()}
    check(("microsoft", "kate@outlook.com") in accounts,
          "microsoft refresh token cached too")

    events = ca.fetch_microsoft_events("MAT", http=http_ms, now=1783504800)
    check(len(events) == 2 and events[0]["title"] == "Soccer photos"
          and events[0]["location"] == "Field 3"
          and events[1].get("status") == "cancelled",
          "Graph events normalized (incl. cancelled flag)")

    # ---- unified entry + forgetting ----
    evs = ca.fetch_calendar_events("microsoft", {"access_token": "MAT"},
                                   http=http_ms, now=1783504800)
    check(len(evs) == 2, "fetch_calendar_events dispatches by provider")
    check(ca.forget_account("google", "chris@example.com"),
          "forget_account removes a cached account")
    check(all(a["provider"] != "google" for a in ca.saved_accounts()),
          "forgotten account is gone from saved_accounts")

    # Declined sign-in is a clear error, not a hang.
    def http_declined(url, data=None, headers=None, method=None):
        return {"error": "authorization_declined"}
    try:
        ca.microsoft_poll_token("DC1", http=http_declined)
        check(False, "declined sign-in raises AuthError")
    except ca.AuthError:
        check(True, "declined sign-in raises AuthError")

    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
