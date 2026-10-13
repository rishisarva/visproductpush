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
KNOWN = {"path": "/track-result/article-tracking/U2FsdGVkX1%2Fs4MUbcvdCbgb%2FplQunYwb2VGSBAhMiHMAfnJ8QlHTOBGP0WQ7CZSQ",
         "action": "7f04c72a185756a3940501a7996e6b83e2a72a488e"}
PATHS = [KNOWN["path"], "/track-consignment", "/"]
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

def _post(path, action, number):
    r = S.post(IP + path, timeout=25, data=f'["ARTICLE_TRACKING_URL","GET",null,"{number}"]',
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
    blob = json.dumps(obj)[:20000].lower()
    return (number and number.lower() in blob) or "tracking" in blob or "event" in blob or "office" in blob


DEBUG = []   # what India Post answered, for the test report


def _note(path, action, code, text):
    DEBUG.append(f"{code} · action {action[:8]}… · {path[:48]}\n{text[:1200]}")


def find_action(sample):
    """Rediscover the server-action id from the website's own JavaScript."""
    print("   looking for India Post's current tracking action…")
    cands, chunks = [], []
    for page in ("/track-consignment", "/"):
        try:
            html = S.get(IP + page, timeout=25).text
        except requests.RequestException:
            continue
        for src in re.findall(r'src="([^"]*/_next/static/[^"]+\.js[^"]*)"', html):
            src = src if src.startswith("http") else IP + src
            if src not in chunks:
                chunks.append(src)
    for src in chunks[:60]:
        try:
            js = S.get(src, timeout=25).text
        except requests.RequestException:
            continue
        ids = re.findall(r'["\']([0-9a-f]{40,42})["\']', js)
        hot = "ARTICLE_TRACKING" in js or "track" in src.lower()
        for i in ids:
            if i not in cands:
                cands.insert(0, i) if hot else cands.append(i)
    print(f"   {len(chunks)} scripts, {len(cands)} candidate ids")
    for action in cands[:80]:
        for path in PATHS:
            try:
                code, text = _post(path, action, sample)
            except requests.RequestException:
                continue
            if code == 200 and _looks_right(_extract(text), sample):
                print(f"   found: action {action[:10]}… via {path[:40]}")
                return {"path": path, "action": action}
            time.sleep(0.4)
    DEBUG.append(f"discovery: {len(chunks)} scripts, {len(cands)} ids tried, none answered")
    return None


def fetch(number, cfg, allow_discover=True):
    """→ (data dict or None, cfg, note)"""
    code, empty = 0, False
    actions = [cfg["action"]] + ([] if cfg["action"] == KNOWN["action"] else [KNOWN["action"]])
    for action in actions:
        for path in [cfg["path"]] + [p for p in PATHS if p != cfg["path"]]:
            try:
                code, text = _post(path, action, number)
            except requests.RequestException as exc:
                return None, cfg, f"network: {exc}"
            _note(path, action, code, text)
            obj = _extract(text)
            if code == 200 and _looks_right(obj, number):
                if path != cfg["path"] or action != cfg["action"]:
                    cfg = {"path": path, "action": action}
                    pref_set("indiapost_action", cfg)
                return obj["data"], cfg, ""
            if code == 200 and isinstance(obj, dict) and obj.get("data") in (None, "", [], {}):
                empty = True          # answered, but nothing for this number
            time.sleep(0.5)
    if allow_discover:
        new = find_action(number)
        if new:
            pref_set("indiapost_action", new)
            return fetch(number, new, allow_discover=False)
    if empty:
        return None, cfg, "India Post answered but had no details for this number"
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
    """Find the list of scan events inside the reply and normalise each one."""
    best = []

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

    walk(data)
    out = []
    for e in best:
        date = _pick(e, "date", "time", "when")
        time_ = _pick(e, "time") if not re.search(r"\d:\d", date) else ""
        out.append({"text": _pick(e, "event", "status", "desc", "activity", "remark", skip=("date", "time", "office", "code", "_id", "pin")),
                    "where": _pick(e, "office", "location", "place", "city", "hub", skip=("date", "time", "code", "_id", "pin")),
                    "when": parse_dt((date + " " + time_).strip()), "raw": e})
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
    known = {str(r["order_id"]): r for r in (sb("GET", "vj_track?select=order_id,number,stage,status,location,event_at,attempts,done,updated_at&limit=1000") or [])}
    # finished parcels are left alone — unless the order got a NEW tracking number (reshipped after an RTO)
    for o in orders:
        k = known.get(str(o["woo_order_id"]))
        if k and (k.get("number") or "") and k["number"] != o["tracking"].strip().upper():
            known[str(o["woo_order_id"])] = {"order_id": k["order_id"], "reship": True}
    todo = [o for o in orders if (known.get(str(o["woo_order_id"])) or {}).get("stage") not in ("delivered", "rto")]
    print(f"{len(orders)} parcels of the last 60 days, {len(todo)} still moving")
    cfg = pref_get("indiapost_action") or KNOWN
    changed = mailed = failed = 0
    for o in todo[:MAX_PER_RUN]:
        num = o["tracking"].strip().upper()
        oid = str(o["woo_order_id"])
        data, cfg, note = fetch(num, cfg)
        time.sleep(PACE)
        if data is None:
            failed += 1
            print(f"   {num} #{oid}: {note}")
            if "did not answer" in note or "network" in note:
                break   # the site changed or is down; the rest would fail too — try again next run
            continue
        s = summarise(num, data)
        prev = known.get(oid) or {}
        cust = o.get("customer") if isinstance(o.get("customer"), dict) else {}
        row = {"order_id": oid, "number": num, "stage": s["stage"], "status": s["text"][:120],
               "description": s["text"][:300], "location": s["where"][:120],
               "event_at": s["when"].isoformat() if s["when"] else None, "attempts": s["attempts"],
               "raw": {"events": [{"text": e["text"], "where": e["where"], "when": e["when"].isoformat() if e["when"] else None} for e in s["events"]][-12:]},
               "updated_at": datetime.now(timezone.utc).isoformat()}
        for k_src, k_dst in (("name", "name"), ("email", "email"), ("phone", "phone")):
            if cust.get(k_src):
                row[k_dst] = str(cust[k_src])[:120]
        if s["stage"] == "delivered":
            row["done"] = True
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
    print(f"done: {changed} moved, {mailed} customer updates queued, {failed} not answered")


def test(number):
    cfg = (pref_get("indiapost_action") if URL and KEY else None) or KNOWN
    data, cfg, note = fetch(number, cfg)
    lines = [f"# India Post test · {number}", ""]
    if data is None:
        lines += [f"**Did not work:** {note}", "", "India Post may have changed its site, or the number is not in their system yet.",
                  "", "## What India Post answered (for the developer)", "```"] + [d.replace("`", "'") for d in DEBUG[:12]] + ["```"]
    else:
        s = summarise(number, data)
        lines += [f"**Works.** Stage: `{s['stage']}` → customer sees **{label(s['stage'], s['where'])}** · failed attempts so far: {s['attempts']}",
                  f"Latest scan: {s['text']} · {s['where']} · {s['when']}", "", "## Scan history"]
        lines += [f"- {e['when']} · {e['text']} · {e['where']}" for e in s["events"]] or ["(no events found in the reply — see raw below)"]
        lines += ["", "## Raw reply (for the developer)", "```json", json.dumps(data, indent=1, ensure_ascii=False)[:6000], "```"]
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
