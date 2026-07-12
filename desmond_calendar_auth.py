#!/usr/bin/env python3
"""
Desmond Calendar Auth — read each parent's calendar by SIGNING IN, not by
hunting for secret iCal links. Google (OAuth loopback + PKCE) and Microsoft
(device code) flows, stdlib only, tokens cached privately so the second run
is one click.

Why two different flows
-----------------------
- **Google** requires a browser redirect for Calendar scope, so the family
  web wizard (which is already a local web server) handles the round trip:
  build the consent URL with google_auth_url(), send the parent's browser
  there, and Google redirects back to the wizard with a code that
  google_exchange_code() turns into tokens.
- **Microsoft** supports the friendlier device-code flow: the wizard shows
  "go to microsoft.com/devicelink and type ABC-DEF-GHI", the parent signs in
  on ANY device, and microsoft_poll_token() picks up the tokens. No
  redirect plumbing at all.

One-time developer setup (you, once — NOT each parent)
------------------------------------------------------
Each provider requires the APP (Desmond / ParentPoint) to be registered
once; parents never see this part.

Google (~5 minutes):
  1. console.cloud.google.com → create a project (name: Desmond)
  2. "APIs & Services" → "Enabled APIs" → enable **Google Calendar API**
  3. "OAuth consent screen" → External → fill the two required fields →
     add the parents as Test users (or publish the app later)
  4. "Credentials" → "Create credentials" → "OAuth client ID" →
     Application type **Desktop app**
  5. Copy the client ID and client secret into the config (below)

Microsoft (~5 minutes):
  1. portal.azure.com → "App registrations" → "New registration"
  2. Supported account types: **Personal Microsoft accounts and any
     organizational directory**
  3. After creating: "Authentication" → set **"Allow public client
     flows"** to Yes
  4. Copy the "Application (client) ID" into the config (below)

Config lives in ~/.desmond/oauth_clients.json (created chmod 600):

    {"google":    {"client_id": "…", "client_secret": "…"},
     "microsoft": {"client_id": "…"}}

or via env vars DESMOND_GOOGLE_CLIENT_ID / DESMOND_GOOGLE_CLIENT_SECRET /
DESMOND_MS_CLIENT_ID. Until a provider is configured, the web wizard shows
these setup steps instead of that provider's Connect button (and the
paste-an-iCal-link fallback still works).

Tokens are cached in ~/.desmond/calendar_tokens.json (chmod 600), keyed by
account email, refresh-token included — so reconnecting a calendar next
month is a single click, not a new sign-in.

What comes out
--------------
fetch_calendar_events(provider, tokens) returns the SAME normalized event
list desmond_family.parse_calendar() produces, so OAuth calendars federate
identically to .ics ones:

    {"title", "start" (ISO), "end", "all_day", "location",
     "description", "status", "calendar"}

Apple/iCloud note: Apple offers no comparable OAuth API for third parties.
iCloud-calendar parents should either use the Google/Microsoft calendar the
school already invites them on, or fall back to an iCloud published-calendar
link. (On-device calendar reading is a future ParentPoint-mobile feature.)
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENTS_CONFIG = os.path.expanduser("~/.desmond/oauth_clients.json")
TOKENS_CONFIG = os.path.expanduser("~/.desmond/calendar_tokens.json")

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_SCOPES = "openid email https://www.googleapis.com/auth/calendar.readonly"

MS_DEVICECODE = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
MS_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MS_GRAPH = "https://graph.microsoft.com/v1.0"
MS_SCOPES = "openid email offline_access Calendars.Read"

# How far around "now" to pull events. Gap reporting defaults to the last
# 30 days + upcoming, so this window comfortably covers it.
FETCH_DAYS_BACK = 120
FETCH_DAYS_FORWARD = 400


class AuthError(Exception):
    """Sign-in failed or a provider isn't configured; the message is
    parent-readable and shown verbatim in the web UI."""


# ---------------------------------------------------------------------------
# HTTP (single funnel so tests stub the network in one place)
# ---------------------------------------------------------------------------

def _http(url, data=None, headers=None, method=None):
    """POST form data (dict) or GET. Returns parsed JSON. OAuth error
    responses (4xx with JSON bodies) are returned, not raised, so callers
    can read error codes like authorization_pending."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            raise AuthError(f"{url.split('?')[0]}: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise AuthError(f"Couldn't reach {urllib.parse.urlsplit(url).hostname}"
                        f" — is this computer online? ({e})")


# ---------------------------------------------------------------------------
# Client config + token cache
# ---------------------------------------------------------------------------

def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_private_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def get_clients():
    """Configured OAuth clients, env vars winning over the config file."""
    cfg = _load_json(CLIENTS_CONFIG)
    google = dict(cfg.get("google") or {})
    ms = dict(cfg.get("microsoft") or {})
    if os.environ.get("DESMOND_GOOGLE_CLIENT_ID"):
        google["client_id"] = os.environ["DESMOND_GOOGLE_CLIENT_ID"]
    if os.environ.get("DESMOND_GOOGLE_CLIENT_SECRET"):
        google["client_secret"] = os.environ["DESMOND_GOOGLE_CLIENT_SECRET"]
    if os.environ.get("DESMOND_MS_CLIENT_ID"):
        ms["client_id"] = os.environ["DESMOND_MS_CLIENT_ID"]
    return {"google": google, "microsoft": ms}


def provider_status():
    """{"google": bool, "microsoft": bool} — which Connect buttons the web
    wizard can show."""
    c = get_clients()
    return {"google": bool(c["google"].get("client_id")
                           and c["google"].get("client_secret")),
            "microsoft": bool(c["microsoft"].get("client_id"))}


def saved_accounts():
    """Accounts with cached refresh tokens: [{"provider", "email"}] — lets
    the wizard offer 'Use chris@gmail.com (connected before)' one-click."""
    cache = _load_json(TOKENS_CONFIG)
    out = []
    for provider in ("google", "microsoft"):
        for email in sorted((cache.get(provider) or {})):
            out.append({"provider": provider, "email": email})
    return out


def _store_tokens(provider, email, tokens):
    cache = _load_json(TOKENS_CONFIG)
    entry = cache.setdefault(provider, {}).setdefault(email, {})
    entry.update({k: v for k, v in tokens.items() if v is not None})
    entry["stored_at"] = int(time.time())
    _save_private_json(TOKENS_CONFIG, cache)


def _saved_tokens(provider, email):
    return (_load_json(TOKENS_CONFIG).get(provider) or {}).get(email)


def forget_account(provider, email):
    cache = _load_json(TOKENS_CONFIG)
    if (cache.get(provider) or {}).pop(email, None) is not None:
        _save_private_json(TOKENS_CONFIG, cache)
        return True
    return False


def _email_from_id_token(id_token):
    """The signed id_token's payload carries the account email. Signature is
    NOT verified — fine here, because the token came to us directly from the
    provider's own token endpoint over TLS."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Google — authorization code + PKCE through the wizard's own server
# ---------------------------------------------------------------------------

def google_begin(redirect_uri, state):
    """Start a Google sign-in. Returns (auth_url, code_verifier); send the
    browser to auth_url and keep code_verifier for google_exchange_code()."""
    clients = get_clients()["google"]
    if not clients.get("client_id"):
        raise AuthError("Google Calendar isn't configured yet — see the "
                        "one-time setup in desmond_calendar_auth.py.")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {
        "client_id": clients["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",       # refresh token → one-click next time
        "prompt": "consent select_account",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return GOOGLE_AUTH + "?" + urllib.parse.urlencode(params), verifier


def google_exchange_code(code, code_verifier, redirect_uri, http=None):
    """Trade the redirect's code for tokens. Returns
    {"access_token", "refresh_token", "email"} and caches them."""
    http = http or _http
    clients = get_clients()["google"]
    resp = http(GOOGLE_TOKEN, data={
        "client_id": clients["client_id"],
        "client_secret": clients.get("client_secret", ""),
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    if "access_token" not in resp:
        raise AuthError("Google sign-in failed: "
                        + str(resp.get("error_description")
                              or resp.get("error") or resp))
    email = _email_from_id_token(resp.get("id_token", "")) or "google-account"
    tokens = {"access_token": resp["access_token"],
              "refresh_token": resp.get("refresh_token"), "email": email}
    _store_tokens("google", email, tokens)
    return tokens


def google_refresh(email, http=None):
    """One-click reconnect from the cached refresh token."""
    saved = _saved_tokens("google", email)
    if not saved or not saved.get("refresh_token"):
        raise AuthError(f"No saved Google sign-in for {email} — connect it "
                        "once with the Connect button.")
    http = http or _http
    clients = get_clients()["google"]
    resp = http(GOOGLE_TOKEN, data={
        "client_id": clients["client_id"],
        "client_secret": clients.get("client_secret", ""),
        "refresh_token": saved["refresh_token"],
        "grant_type": "refresh_token",
    })
    if "access_token" not in resp:
        raise AuthError("The saved Google sign-in expired — connect the "
                        "account again.")
    tokens = {"access_token": resp["access_token"],
              "refresh_token": saved["refresh_token"], "email": email}
    _store_tokens("google", email, tokens)
    return tokens


def fetch_google_events(access_token, days_back=FETCH_DAYS_BACK,
                        days_forward=FETCH_DAYS_FORWARD, http=None,
                        now=None):
    """Every event on every calendar the account has selected, normalized to
    the desmond_family event shape."""
    http = http or _http
    now = now or time.time()
    time_min = _rfc3339(now - days_back * 86400)
    time_max = _rfc3339(now + days_forward * 86400)
    headers = {"Authorization": f"Bearer {access_token}"}

    cal_list = http(f"{GOOGLE_API}/users/me/calendarList", headers=headers)
    if "items" not in cal_list:
        raise AuthError("Google Calendar refused the request — reconnect "
                        "the account. (" + str(cal_list.get("error", "")) + ")")
    events = []
    for cal in cal_list.get("items", []):
        if cal.get("selected") is False:
            continue
        cal_name = cal.get("summaryOverride") or cal.get("summary") or "Calendar"
        page = None
        while True:
            params = {"singleEvents": "true", "maxResults": "2500",
                      "timeMin": time_min, "timeMax": time_max}
            if page:
                params["pageToken"] = page
            resp = http(f"{GOOGLE_API}/calendars/"
                        + urllib.parse.quote(cal["id"], safe="")
                        + "/events?" + urllib.parse.urlencode(params),
                        headers=headers)
            for item in resp.get("items", []):
                ev = _google_event(item, cal_name)
                if ev:
                    events.append(ev)
            page = resp.get("nextPageToken")
            if not page:
                break
    events.sort(key=lambda e: e["start"])
    return events


def _google_event(item, cal_name):
    start = item.get("start") or {}
    all_day = "date" in start
    start_iso = start.get("dateTime") or start.get("date")
    if not start_iso:
        return None
    end = item.get("end") or {}
    ev = {"title": item.get("summary") or "(untitled)",
          "start": start_iso, "all_day": all_day, "calendar": cal_name}
    if end.get("dateTime") or end.get("date"):
        ev["end"] = end.get("dateTime") or end.get("date")
    if item.get("location"):
        ev["location"] = item["location"]
    if item.get("description"):
        ev["description"] = item["description"][:500]
    if item.get("status"):
        ev["status"] = item["status"]
    return ev


# ---------------------------------------------------------------------------
# Microsoft — device code flow (no redirect needed)
# ---------------------------------------------------------------------------

def microsoft_begin(http=None):
    """Start a Microsoft sign-in. Returns the provider's device-code
    payload: user_code (show it big), verification_uri (where the parent
    types it), device_code + interval (for polling)."""
    http = http or _http
    clients = get_clients()["microsoft"]
    if not clients.get("client_id"):
        raise AuthError("Microsoft calendar isn't configured yet — see the "
                        "one-time setup in desmond_calendar_auth.py.")
    resp = http(MS_DEVICECODE, data={"client_id": clients["client_id"],
                                     "scope": MS_SCOPES})
    if "device_code" not in resp:
        raise AuthError("Microsoft sign-in couldn't start: "
                        + str(resp.get("error_description") or resp))
    return resp


def microsoft_poll_token(device_code, http=None):
    """One poll of the token endpoint. Returns tokens dict when the parent
    has finished signing in, None while still pending, raises AuthError on
    a real failure (declined, expired)."""
    http = http or _http
    clients = get_clients()["microsoft"]
    resp = http(MS_TOKEN, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": clients["client_id"],
        "device_code": device_code,
    })
    if "access_token" in resp:
        email = (_email_from_id_token(resp.get("id_token", ""))
                 or "microsoft-account")
        tokens = {"access_token": resp["access_token"],
                  "refresh_token": resp.get("refresh_token"), "email": email}
        _store_tokens("microsoft", email, tokens)
        return tokens
    err = resp.get("error")
    if err in ("authorization_pending", "slow_down"):
        return None
    raise AuthError("Microsoft sign-in "
                    + ("was declined." if err == "authorization_declined"
                       else "code expired — start over."
                       if err == "expired_token"
                       else f"failed: {resp.get('error_description') or err}"))


def microsoft_refresh(email, http=None):
    saved = _saved_tokens("microsoft", email)
    if not saved or not saved.get("refresh_token"):
        raise AuthError(f"No saved Microsoft sign-in for {email} — connect "
                        "it once with the Connect button.")
    http = http or _http
    clients = get_clients()["microsoft"]
    resp = http(MS_TOKEN, data={
        "grant_type": "refresh_token",
        "client_id": clients["client_id"],
        "refresh_token": saved["refresh_token"],
        "scope": MS_SCOPES,
    })
    if "access_token" not in resp:
        raise AuthError("The saved Microsoft sign-in expired — connect the "
                        "account again.")
    tokens = {"access_token": resp["access_token"],
              "refresh_token": resp.get("refresh_token",
                                        saved["refresh_token"]),
              "email": email}
    _store_tokens("microsoft", email, tokens)
    return tokens


def fetch_microsoft_events(access_token, days_back=FETCH_DAYS_BACK,
                           days_forward=FETCH_DAYS_FORWARD, http=None,
                           now=None):
    """Every event in the account's calendar view, normalized."""
    http = http or _http
    now = now or time.time()
    headers = {"Authorization": f"Bearer {access_token}",
               'Prefer': 'outlook.timezone="UTC"'}
    params = urllib.parse.urlencode({
        "startDateTime": _rfc3339(now - days_back * 86400),
        "endDateTime": _rfc3339(now + days_forward * 86400),
        "$top": "500",
    })
    url = f"{MS_GRAPH}/me/calendarView?{params}"
    events = []
    while url:
        resp = http(url, headers=headers)
        if "value" not in resp:
            raise AuthError("Microsoft calendar refused the request — "
                            "reconnect the account. ("
                            + str(resp.get("error", "")) + ")")
        for item in resp["value"]:
            ev = _microsoft_event(item)
            if ev:
                events.append(ev)
        url = resp.get("@odata.nextLink")
    events.sort(key=lambda e: e["start"])
    return events


def _microsoft_event(item):
    start = (item.get("start") or {}).get("dateTime")
    if not start:
        return None
    ev = {"title": item.get("subject") or "(untitled)",
          "start": start, "all_day": bool(item.get("isAllDay")),
          "calendar": "Outlook"}
    end = (item.get("end") or {}).get("dateTime")
    if end:
        ev["end"] = end
    loc = (item.get("location") or {}).get("displayName")
    if loc:
        ev["location"] = loc
    if item.get("bodyPreview"):
        ev["description"] = item["bodyPreview"][:500]
    if item.get("isCancelled"):
        ev["status"] = "cancelled"
    return ev


# ---------------------------------------------------------------------------
# The one call the wizard makes after any sign-in
# ---------------------------------------------------------------------------

def fetch_calendar_events(provider, tokens, http=None, now=None):
    """provider + tokens (from exchange/poll/refresh) → normalized events."""
    if provider == "google":
        return fetch_google_events(tokens["access_token"], http=http, now=now)
    if provider == "microsoft":
        return fetch_microsoft_events(tokens["access_token"], http=http,
                                      now=now)
    raise AuthError(f"Unknown calendar provider {provider!r}")


def _rfc3339(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
