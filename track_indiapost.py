"""
track_indiapost.py  —  our own India Post tracking (no 17TRACK, no Shipway, ₹0)
===============================================================================

India Post's new website talks to its own server without a captcha. This script
asks it the same way the website does, for every parcel you shipped in the last
45 days, and writes the result where the app already looks:

    vj_track                  → dashboard: Orders → Shipped, Notifications
    shop_orders.track_status  → customer's order page ("Out for delivery", "In transit · at Mumbai NSH")
    woo_commands (track_set)  → the plugin emails the customer (out / attempt failed /
                                delivered / returned) and tells you on Telegram (RTO, delivered)

Runs from GitHub Actions every 30 minutes (workflows/track-indiapost.yml).

Test by hand:   python track_indiapost.py --test CL123456789IN

Needs SUPABASE_URL + SUPABASE_KEY (same secrets the sync already has).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import track_source_17track as t17          # used when the TRACK17_KEY secret is set
except Exception:                                 # noqa: BLE001
    t17 = None
USE17 = bool(t17 and t17.ENABLED)

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
KEY = os.getenv("SUPABASE_KEY") or ""
HEAD = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

IP = "https://www.indiapost.gov.in"
# The page the website uses and the server-action id it sends. Both can change
# when India Post redeploys; find_action() rediscovers them from the site's own
# JavaScript and the result is cached in vj_prefs (key "indiapost_action").
KNOWN = {"path": "/", "action": "7f04c72a185756a3940501a7996e6b83e2a72a488e", "key": "ARTICLE_TRACKING_URL"}
PATHS = ["/", "/track-consignment", "/track-result/article-tracking/U2FsdGVkX1%2Fs4MUbcvdCbgb%2FplQunYwb2VGSBAhMiHMAfnJ8QlHTOBGP0WQ7CZSQ"]
# The action is called as [<which URL>, "GET", null, <number>]; <which URL> is a
# key the server maps to the real tracking API. Keys we try, best first; the
# site's own JavaScript is searched for more when none of these work.
KEYS = ["ARTICLE_TRACKING_URL", "TRACK_CONSIGNMENT_URL", "CONSIGNMENT_TRACKING_URL", "TRACKING_URL", "TRACK_ARTICLE_URL",
        "ARTICLE_TRACK_URL", "TRACK_URL", "CONSIGNMENT_URL", "ARTICLE_URL", "SPEED_POST_TRACKING_URL"]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1")
PACE = 2.0            # seconds between two India Post requests — be a polite guest
MAX_PER_RUN = 150
IST = timezone(timedelta(hours=5, minutes=30))
LABEL = {"registered": "Shipped", "transit": "In transit", "out": "Out for delivery",
         "failed": "Delivery attempted — re-attempt soon", "delivered": "Delivered",
         "returning": "Returning to sender", "rto": "Returned to us — reship available",
         "expired": "No recent update", "exception": "Delivery issue"}
MAIL_STAGES = ("out", "failed", "delivered", "rto", "returning", "exception")

S = requests.Session()
S.headers["User-Agent"] = UA


# ──────────────────────────────────────────────────────────────
# Supabase
# ──────────────────────────────────────────────────────────────

def sb(method, path, body=None, prefer=None):
    if not (URL and KEY):
        return None
    h = dict(HEAD)
    if prefer:
        h["Prefer"] = prefer
    try:
        r = requests.request(method, f"{URL}/rest/v1/{path}", headers=h, json=body, timeout=30)
    except requests.RequestException as exc:
        print(f"   supabase: {method} {path} failed: {exc}")
        return None
    if r.status_code >= 400:
        print(f"   supabase: {method} {path} -> {r.status_code} {r.text[:160]}")
        return None
    return r.json() if r.text.strip() else []


def pref_get(key):
    rows = sb("GET", f"vj_prefs?select=value&key=eq.{key}") or []
    return rows[0].get("value") if rows else None


def pref_set(key, value):
    sb("POST", "vj_prefs?on_conflict=key", [{"key": key, "value": value}],
       prefer="resolution=merge-duplicates,return=minimal")


# ──────────────────────────────────────────────────────────────
# India Post
# ──────────────────────────────────────────────────────────────

def _post(path, action, number, key="ARTICLE_TRACKING_URL"):
    r = S.post(IP + path, timeout=40, data=f'["{key}","GET",null,"{number}"]',
               headers={"Content-Type": "text/plain;charset=UTF-8", "Accept": "text/x-component",
                        "Next-Action": action, "Origin": IP, "Referer": IP + path})
    return r.status_code, r.text


def _extract(text):
    """The reply is a React stream; somewhere in it sits {"data": ...}. Pull that out."""
    dec = json.JSONDecoder()
    for m in re.finditer(r'\{"data":', text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
            return obj
        except ValueError:
            continue
    return None


def _looks_right(obj, number=""):
    """A real answer: {"data": …} that is not empty and mentions the number (or scan words)."""
    if not (isinstance(obj, dict) and "data" in obj and obj["data"] not in (None, "", [], {})):
        return False
    if obj.get("error"):
        return False
    # Real scans: at least one event with text (an empty/money-order/other
    # endpoint answers with nulls), or the number itself in the reply.
    return bool(events_of(obj["data"])) or (number and number.lower() in json.dumps(obj).lower())


DEBUG = []   # what India Post answered, for the test report


def _note(path, action, code, text, key=""):
    DEBUG.append(f"{code} · action {action[:8]}… · key {key} · {path[:40]}\n{text[:700]}")


GUESS_PAGES = ["/track-consignment", "/tracking", "/track", "/track-result", "/trackconsignment", "/consignment-tracking",
               "/track-your-consignment", "/services/tracking", "/en/track-consignment", "/track-result/article-tracking"]


def _get(path, rsc=False):
    h = {"RSC": "1"} if rsc else {}
    try:
        r = S.get(IP + path, timeout=25, headers=h)
        return r.status_code, r.text
    except requests.RequestException:
        return 0, ""


def site_js():
    """The site's JavaScript files: the home page's, plus those of every page that
    looks like a tracking page (links found in the site + a list of likely paths).
    It's a Next.js "app router" site, so a page's own scripts are only listed in its
    React payload (fetched with the RSC header), not in the home page."""
    pages = ["/"]
    code, html = _get("/")
    code2, flight = _get("/", rsc=True)
    for text in (html, flight):
        for href in re.findall(r'href\\?":\\?"(/[A-Za-z0-9_\-/]*)', text) + re.findall(r'href="(/[A-Za-z0-9_\-/]*)"', text):
            if re.search(r"track|consign", href, re.I) and href not in pages:
                pages.append(href)
    for g in GUESS_PAGES:
        if g not in pages:
            pages.append(g)
    chunks, found_pages = [], []
    for page in pages[:14]:
        got = False
        for rsc in (False, True):
            code, text = _get(page, rsc=rsc)
            if code != 200 and page != "/":
                continue
            for src in re.findall(r'(?:/_next/)?static/chunks/[A-Za-z0-9_\-/.\[\]%]+\.js', text):
                src = IP + (src if src.startswith("/_next/") else "/_next/" + src)
                if src not in chunks:
                    chunks.append(src); got = True
        if got:
            found_pages.append(page)
    js = []
    for src in chunks[:90]:
        try:
            js.append((src, S.get(src, timeout=25).text))
        except requests.RequestException:
            pass
    trackish = [c.rsplit("/", 1)[-1] for c in chunks if re.search(r"track|consign", c, re.I)]
    DEBUG.append(f"site: pages with scripts {found_pages} · {len(js)} scripts read · tracking-looking scripts: {trackish[:8]}")
    return js


def find_keys(js):
    """Strings like SOMETHING_URL in the site's code, tracking-related ones first."""
    keys = []
    for src, code in js:
        for k in re.findall(r'["\']([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)["\']', code):
            if k not in keys and (k.endswith("_URL") or re.search(r"TRACK|CONSIGN|ARTICLE", k)):
                keys.append(k)
    def score(k):
        sc = 0
        if re.search(r"ARTICLE|CONSIGN", k): sc -= 4
        if "TRACK" in k: sc -= 2
        if re.search(r"MONEY|PINCODE|POSTOFFICE|POST_OFFICE|BRANCH|COMPLAINT|OTP|LOGIN|PAYMENT|BANK|INSUR|PHILAT|LOGISTIC|PARCEL_BOOK|BOOKING", k): sc += 5
        return (sc, len(k))
    keys.sort(key=score)
    DEBUG.append("keys in site code: " + ", ".join(keys[:40]))
    return keys


def probe(js):
    """For the report: where the tracking strings live in the site's code (so the key can be read by eye)."""
    for src, code in js:
        name = src.rsplit("/", 1)[-1]
        for m in re.finditer(r'[A-Z][A-Z0-9_]{4,}_URL', code):
            k = m.group(0)
            if re.search(r"TRACK|CONSIGN|ARTICLE", k):
                a, b = max(0, m.start() - 160), min(len(code), m.end() + 160)
                DEBUG.append(f"probe: {name} · {k}\n" + code[a:b].replace("\n", " "))
        for m in re.finditer(KNOWN["action"][:12], code):
            a, b = max(0, m.start() - 200), min(len(code), m.end() + 200)
            DEBUG.append(f"probe: {name} · action id context\n" + code[a:b].replace("\n", " "))
        for m in re.finditer(r'(?i)article[_-]?track|track[_-]?article|consignment[_-]?track', code):
            a, b = max(0, m.start() - 120), min(len(code), m.end() + 120)
            DEBUG.append(f"probe: {name} · '{m.group(0)}'\n" + code[a:b].replace("\n", " "))
    seen = set(); out = []
    for d in DEBUG:
        if d.startswith("probe:") and d in seen:
            continue
        seen.add(d); out.append(d)
    DEBUG[:] = out[:60]


def find_action(sample, cfg):
    """Rediscover the URL key and/or the server-action id from the website's own JavaScript."""
    print("   looking for India Post's current tracking action…")
    js = site_js()
    keys = [k for k in find_keys(js) if k not in KEYS] 
    actions = [cfg["action"]] + ([KNOWN["action"]] if cfg["action"] != KNOWN["action"] else [])
    for src, code in js:
        hot = "_URL" in code or "track" in src.lower()
        for i in re.findall(r'["\']([0-9a-f]{40,42})["\']', code):
            if i not in actions:
                actions.insert(1, i) if hot else actions.append(i)
    print(f"   {len(js)} scripts, {len(keys)} new keys, {len(actions)} action ids")
    tried = 0
    for action in actions[:60]:
        for key in (KEYS + keys)[:40]:
            try:
                code, text = _post("/", action, sample, key)
            except requests.RequestException:
                continue
            tried += 1
            obj = _extract(text)
            if obj is not None and action == actions[0]:
                _note("/", action, code, text, key)
            if _looks_right(obj, sample):
                print(f"   found: action {action[:10]}… key {key}")
                return {"path": "/", "action": action, "key": key}
            if obj is None:
                break        # this action id doesn't exist at all — no point trying keys with it
            time.sleep(0.3)
    DEBUG.append(f"discovery: {tried} combinations tried, none answered")
    probe(js)
    return None


def fetch(number, cfg, allow_discover=True):
    """→ (data dict or None, cfg, note)"""
    cfg = {**KNOWN, **{k: v for k, v in (cfg or {}).items() if v}}
    code, empty = 0, False
    keys = [cfg["key"]] + [k for k in KEYS if k != cfg["key"]]
    for key in keys:
        for path in [cfg["path"]] + [p for p in PATHS if p != cfg["path"]]:
            try:
                code, text = _post(path, cfg["action"], number, key)
            except requests.RequestException as exc:
                return None, cfg, f"network: {exc}"
            _note(path, cfg["action"], code, text, key)
            obj = _extract(text)
            if _looks_right(obj, number):
                if path != cfg["path"] or key != cfg["key"]:
                    cfg = {"path": path, "action": cfg["action"], "key": key}
                    pref_set("indiapost_action2", cfg)
                return obj["data"], cfg, ""
            if isinstance(obj, dict) and not obj.get("error") and (obj.get("data") in (None, "", [], {})
                    or (key == cfg["key"] and isinstance(obj["data"], dict) and "tracking_details" in obj["data"])):
                empty = True          # answered properly (the shape we know), but no scans for this number yet
                break
            if obj is None:
                break                 # this page doesn't run the action; next key won't help on it either
            time.sleep(0.5)
        if empty:
            break
    if allow_discover and not empty:
        new = find_action(number, cfg)
        if new:
            pref_set("indiapost_action2", new)
            return fetch(number, new, allow_discover=False)
    if empty:
        return None, cfg, "India Post answered but has no scans for this number yet"
    return None, cfg, f"India Post did not answer the way the website expects (last HTTP {code})"


# ──────────────────────────────────────────────────────────────
# Read the reply (whatever shape it has)
# ──────────────────────────────────────────────────────────────

DATE_FMTS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
             "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
             "%d %b %Y %H:%M", "%d %b %Y", "%d-%b-%Y %H:%M", "%d-%b-%Y")


def parse_dt(s):
    s = (s or "").strip()
    if not s:
        return None
    for f in DATE_FMTS:
        try:
            return datetime.strptime(s[:len(f) + 4].strip(), f).replace(tzinfo=IST)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pick(d, *words, skip=()):
    for k, v in d.items():
        lk = k.lower()
        if any(w in lk for w in words) and not any(x in lk for x in skip) \
                and isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return ""


def events_of(data):
    """Find the list of scan events inside the reply and normalise each one.
    Known shape (Oct 2026): {"booking_details": {...}, "tracking_details": [{"date", "time", "office", "officeid", "event"}]}"""
    best = []
    if isinstance(data, dict) and isinstance(data.get("tracking_details"), list):
        best = [e for e in data["tracking_details"] if isinstance(e, dict) and (e.get("event") or e.get("status"))]
        if not best:
            return []

    def walk(x):
        nonlocal best
        if isinstance(x, list) and x and all(isinstance(i, dict) for i in x):
            keys = " ".join(k.lower() for k in x[0].keys())
            if any(w in keys for w in ("event", "status", "office", "location", "date", "time")) and len(x) >= len(best):
                best = x
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    if not best:
        walk(data)
    out = []
    for e in best:
        date = _pick(e, "date", "time", "when")
        time_ = _pick(e, "time") if not re.search(r"\d:\d", date) else ""
        out.append({"text": _pick(e, "event", "status", "desc", "activity", "remark", skip=("date", "time", "office", "code", "_id", "pin")),
                    "where": _pick(e, "office", "location", "place", "city", "hub", skip=("date", "time", "code", "_id", "pin")),
                    "when": parse_dt((date + " " + time_).strip()), "raw": e})
    out = [o for o in out if o["text"]]
    if out and all(o["when"] for o in out):
        out.sort(key=lambda o: o["when"])
    return out


def stage_of(events, data):
    """Our stage from the scan history (chronological)."""
    seen_return = False
    stage = "transit"
    texts = [e["text"] for e in events]
    for t in texts:
        tl = t.lower()
        if re.search(r"return|rts|rto|redirect", tl):
            seen_return = True
            stage = "returning"
        elif re.search(r"out for delivery|out-for-delivery", tl):
            stage = "out"
        elif re.search(r"unsuccess|undeliver|not deliver|could not|attempt|refus|absent|door ?lock|unclaimed|no such|wrong address|insufficient", tl):
            stage = "failed"
        elif re.search(r"\bdeliver", tl):
            stage = "rto" if seen_return else "delivered"
        elif re.search(r"booked|received|bagged|dispatch|transit|despatch|arrived|closed|opened|sent", tl):
            stage = "transit" if not seen_return else "returning"
    top = ""
    if isinstance(data, dict):
        top = _pick(data, "current_status", "status", "delivery_status", "article_status", skip=("date", "time", "code"))
    if not events and top:
        return stage_of([{"text": top, "where": "", "when": None}], None)
    return stage


# ──────────────────────────────────────────────────────────────
# One parcel
# ──────────────────────────────────────────────────────────────

def summarise(number, data):
    ev = events_of(data)
    stage = stage_of(ev, data)
    last = ev[-1] if ev else {"text": "", "where": "", "when": None}
    attempts = sum(1 for e in ev if stage_of([e], None) == "failed")
    return {"stage": stage, "text": last["text"], "where": last["where"], "when": last["when"], "events": ev, "attempts": attempts}


def customer_events(events):
    """The scan timeline the customer's order page shows (newest first, last 8), in plain words."""
    out = []
    for e in reversed(events[-8:]):
        st = stage_of([e], None)
        out.append({"t": e["when"].isoformat() if e["when"] else None, "s": st,
                    "text": (e["text"] or LABEL.get(st, ""))[:90], "where": (e["where"] or "")[:60]})
    return out


def label(stage, where):
    lab = LABEL.get(stage, "In transit")
    if stage in ("transit", "returning", "out") and where:
        lab += " · at " + where
    return lab


def run():
    if not (URL and KEY):
        sys.exit("SUPABASE_URL / SUPABASE_KEY missing")
    since = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    orders = sb("GET", "shop_orders?select=ref,woo_order_id,tracking,customer,created_at,track_status"
                       f"&tracking=not.is.null&created_at=gte.{since}&order=created_at.desc&limit=400") or []
    orders = [o for o in orders if re.fullmatch(r"[A-Z]{2}\d{9}IN", (o.get("tracking") or "").strip().upper()) and o.get("woo_order_id")]
    known = {str(r["order_id"]): r for r in (sb("GET", "vj_track?select=order_id,number,stage,status,location,event_at,attempts,done,updated_at,raw&limit=1000") or [])}
    # finished parcels are left alone — unless the order got a NEW tracking number (reshipped after an RTO)
    for o in orders:
        k = known.get(str(o["woo_order_id"]))
        if k and (k.get("number") or "") and k["number"] != o["tracking"].strip().upper():
            known[str(o["woo_order_id"])] = {"order_id": k["order_id"], "reship": True}
    todo = [o for o in orders if (known.get(str(o["woo_order_id"])) or {}).get("stage") not in ("delivered", "rto")]
    print(f"{len(orders)} parcels of the last 60 days, {len(todo)} still moving")
    cfg = pref_get("indiapost_action2")
    got17 = {}
    if USE17:
        # Scans from 17TRACK's API (India Post's own site needs a captcha now).
        nums = [o["tracking"].strip().upper() for o in todo[:MAX_PER_RUN]]
        need = [n for n, o in zip(nums, todo) if not ((known.get(str(o["woo_order_id"])) or {}).get("raw") or {}).get("reg17")]
        ok, bad = t17.register(need) if need else (set(), {})
        for n, why in bad.items():
            print(f"   {n}: 17TRACK did not accept it ({why})")
        got17 = t17.info([n for n in nums if n not in bad])
        print(f"   17TRACK: {len(need)} newly registered, {sum(1 for v in got17.values() if v)} with scans")
        cfg = cfg or dict(KNOWN)
    elif not cfg:
        # No remembered endpoint: find it using parcels shipped a while ago (they have scans;
        # a parcel booked an hour ago answers "no scans" from every endpoint and proves nothing).
        for o in list(reversed(todo))[:4]:
            new = find_action(o["tracking"].strip().upper(), dict(KNOWN))
            if new:
                cfg = new
                pref_set("indiapost_action2", cfg)
                break
            time.sleep(PACE)
        if not cfg:
            print("could not find India Post's tracking endpoint with the 4 oldest parcels — send this whole output to Claude:")
            shown = set()
            for d in DEBUG:
                if d.startswith(("site:", "keys in", "discovery", "probe")) or " · key " in d:
                    if d[:80] in shown: continue
                    shown.add(d[:80]); print("   " + d.replace("\n", "\n      "))
            return
    changed = mailed = failed = 0
    empties = 0
    net_fail = 0          # consecutive network problems
    run_empty = 0         # consecutive "no scans" — 3 in a row means the remembered endpoint is wrong
    for o in todo[:MAX_PER_RUN]:
        num = o["tracking"].strip().upper()
        oid = str(o["woo_order_id"])
        if USE17:
            data = got17.get(num)
            note = "" if data else ("India Post answered but has no scans for this number yet" if num in got17 else "17TRACK did not accept this number")
        else:
            data, cfg, note = fetch(num, cfg)
            time.sleep(PACE)
        if data is None and "no scans" in note and not USE17:
            run_empty += 1
            prev_stage = (known.get(oid) or {}).get("stage")
            if run_empty >= 3 or prev_stage not in (None, "", "registered"):
                # a parcel that already had scans can't have none now → the endpoint must be re-found
                new = find_action(num, cfg)
                if new:
                    cfg = new
                    pref_set("indiapost_action2", cfg)
                    data, cfg, note = fetch(num, cfg, allow_discover=False)
                    run_empty = 0
        elif data is not None:
            run_empty = 0
        if data is None:
            failed += 1
            empties += "no scans" in note or "no details" in note
            print(f"   {num} #{oid}: {note}")
            if "network" in note:
                net_fail += 1
                if net_fail >= 3:
                    print("   India Post is not answering — stopping, next run in 30 minutes")
                    break
                time.sleep(5)
                continue
            net_fail = 0
            if "did not answer" in note:
                break   # the site changed; the rest would fail too — try again next run
            continue
        net_fail = 0
        s = summarise(num, data)
        if USE17:
            s["stage"] = t17.stage_from_17(data, s["stage"])
            if s["stage"] == "registered" and not s["events"]:
                s["text"] = s["text"] or "Registered — waiting for the first scan"
        prev = known.get(oid) or {}
        cust = o.get("customer") if isinstance(o.get("customer"), dict) else {}
        row = {"order_id": oid, "number": num, "stage": s["stage"], "status": s["text"][:120],
               "description": s["text"][:300], "location": s["where"][:120],
               "event_at": s["when"].isoformat() if s["when"] else None, "attempts": s["attempts"],
               "raw": {"events": [{"text": e["text"], "where": e["where"], "when": e["when"].isoformat() if e["when"] else None} for e in s["events"]][-12:], "reg17": USE17},
               "updated_at": datetime.now(timezone.utc).isoformat()}
        for k_src, k_dst in (("name", "name"), ("email", "email"), ("phone", "phone")):
            if cust.get(k_src):
                row[k_dst] = str(cust[k_src])[:120]
        if s["stage"] == "delivered":
            row["done"] = True
        if USE17 and s["stage"] in ("delivered", "rto"):
            t17.stop([num])
        if prev.get("reship"):
            row["done"] = False
        sb("POST", "vj_track?on_conflict=order_id", [row], prefer="resolution=merge-duplicates,return=minimal")
        patch = {"track_status": label(s["stage"], s["where"]), "track_at": row["updated_at"], "track_events": customer_events(s["events"])}
        sb("PATCH", f"shop_orders?ref=eq.{o['ref']}", patch, prefer="return=minimal")
        moved = prev.get("stage") != s["stage"] or (prev.get("location") or "") != (s["where"] or "")
        if moved:
            changed += 1
        print(f"   {num} #{oid}: {s['stage']:<10} {s['text'][:50]}{(' · ' + s['where']) if s['where'] else ''}")
        # Emails / Telegram go through the plugin (woo_commands → track_set), and
        # only when something the customer should hear about happened:
        #   out for delivery (each day it goes out), every failed attempt,
        #   delivered, returning to us, back with us (RTO).
        # Never for the many in-transit scans, and never for things that
        # happened days ago (first run on old parcels).
        fresh = not s["when"] or (datetime.now(timezone.utc) - s["when"]) < timedelta(hours=48)
        new_stage = prev.get("stage") != s["stage"]
        again = s["stage"] in ("out", "failed") and (prev.get("event_at") or "") != (row["event_at"] or "") \
            and (s["stage"] != "failed" or s["attempts"] > int(prev.get("attempts") or 0))
        if (new_stage or again) and s["stage"] in MAIL_STAGES and fresh:
            day = (s["when"] or datetime.now(timezone.utc)).astimezone(IST).strftime("%Y-%m-%d")
            dup = sb("GET", f"woo_commands?select=id&order_id=eq.{oid}&action=eq.track_set&params->>stage=eq.{s['stage']}"
                            f"&params->>day=eq.{day}&limit=1") or []
            if not dup:
                sb("POST", "woo_commands", {"order_id": oid, "action": "track_set",
                   "params": {"stage": s["stage"], "desc": s["text"][:200], "where": s["where"][:120],
                              "when": row["event_at"], "day": day, "attempts": s["attempts"], "status": "indiapost"}}, prefer="return=minimal")
                mailed += 1
    if mailed:
        sb("POST", "rpc/vj_wake_wordpress", {})   # plugin runs the queue now instead of within the minute
    if empties >= 3 and empties == failed and changed == 0 and len(todo) == failed:
        sb("DELETE", "vj_prefs?key=eq.indiapost_action2")    # every parcel "empty" = the remembered endpoint is probably wrong; search again next run
        print("   remembered endpoint forgotten — will search again next run")
    print(f"done: {changed} moved, {mailed} customer updates queued, {failed} not answered")


def test(number):
    cfg = dict(KNOWN)                 # a test always searches afresh and re-saves what works
    if USE17:
        ok, bad = t17.register([number])
        data = t17.info([number]).get(number) if number not in bad else None
        note = bad.get(number) or ("17TRACK has no scans for it yet (it fetches India Post within a few minutes of registering — run the test again in 10 minutes)")
        if data is not None:
            data["_src"] = "17TRACK"
    else:
        data, cfg, note = fetch(number, cfg)
        if data is not None and URL and KEY:
            pref_set("indiapost_action2", cfg)
    lines = [f"# India Post test · {number}", ""]
    if data is None:
        lines += [f"**Did not work:** {note}", "", "If this parcel was booked today it may simply have no scans yet — test with one that India Post's site already shows scans for.",
                  "", "## What India Post answered (for the developer)", "```"] \
                 + [d.replace("`", "'") for d in DEBUG[:4]] + [d.replace("`", "'") for d in DEBUG[4:] if d.startswith(("site:", "keys in", "discovery", "probe")) or " · key " in d] + ["```"]
    else:
        s = summarise(number, data)
        if USE17:
            s["stage"] = t17.stage_from_17(data, s["stage"])
        lines += [f"**Works{' (via 17TRACK)' if USE17 else ''}.** Stage: `{s['stage']}` → customer sees **{label(s['stage'], s['where'])}** · failed attempts so far: {s['attempts']}",
                  f"Endpoint: key `{cfg.get('key')}` · action `{cfg.get('action', '')[:10]}…`",
                  f"Latest scan: {s['text']} · {s['where']} · {s['when']}", "", "## Scan history"]
        lines += [f"- {e['when']} · {e['text']} · {e['where']}" for e in s["events"]] or ["(no events found in the reply — see raw below)"]
        lines += ["", "## Raw reply (for the developer)", "```json", json.dumps(data, indent=1, ensure_ascii=False)[:6000], "```"]
    lines += ["", "```"] + [d for d in DEBUG if d.startswith(("site:", "keys in", "discovery", "found"))] + ["```"]
    text = "\n".join(lines)
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return data is not None


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        sys.exit(0 if test(sys.argv[2].strip().upper()) else 1)
    run()
