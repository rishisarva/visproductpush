"""
track_source_17track.py — parcel scans from 17TRACK's official API (free quota: 100 parcels / month)
====================================================================================================

Used by track_indiapost.py when the GitHub secret TRACK17_KEY is set. Everything
downstream (Supabase rows, customer emails, Telegram, dashboard, order page) is
unchanged — only where the scans come from differs.

  register      a parcel once (counts 1 against the monthly quota)
  gettrackinfo  all moving parcels, 40 per call, every run
  stoptrack     parcels that are delivered / back with us (frees nothing, but stops polling)

Docs: https://api.17track.net/en/doc  (v2.2)
"""

from __future__ import annotations

import os
import time

import requests

KEY = os.getenv("TRACK17_KEY", "").strip()
CARRIER = int(os.getenv("TRACK17_CARRIER", "0") or 0)     # 0 = let 17TRACK detect India Post from the number
API = "https://api.17track.net/track/v2.2/"
ENABLED = bool(KEY)


def _call(path, body):
    r = requests.post(API + path, json=body, timeout=40, headers={"17token": KEY, "Content-Type": "application/json"})
    try:
        j = r.json()
    except ValueError:
        return {"code": r.status_code, "error": r.text[:200]}
    return j


def _chunks(seq, n=40):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def register(numbers):
    """→ (accepted set, {number: reason} rejected)"""
    ok, bad = set(), {}
    for part in _chunks(list(numbers)):
        body = [{"number": n, **({"carrier": CARRIER} if CARRIER else {})} for n in part]
        j = _call("register", body)
        d = j.get("data") or {}
        for a in d.get("accepted") or []:
            ok.add(a.get("number"))
        for r in d.get("rejected") or []:
            msg = (r.get("error") or {}).get("message", "rejected")
            if "already" in msg.lower() or (r.get("error") or {}).get("code") == -18019901:   # -18019901: already registered
                ok.add(r.get("number"))
            else:
                bad[r.get("number")] = msg
        if j.get("code") not in (0, None):
            for n in part:
                bad.setdefault(n, f"17TRACK: {j.get('message') or j.get('error') or j.get('code')}")
        time.sleep(0.5)
    return ok, bad


def stop(numbers):
    for part in _chunks(list(numbers)):
        try:
            _call("stoptrack", [{"number": n} for n in part])
        except requests.RequestException:
            pass


def info(numbers):
    """→ {number: data-dict in India Post's shape, or None when 17TRACK has nothing yet}"""
    out = {}
    for part in _chunks(list(numbers)):
        j = _call("gettrackinfo", [{"number": n} for n in part])
        d = j.get("data") or {}
        for a in d.get("accepted") or []:
            out[a.get("number")] = _convert(a.get("track_info") or {})
        for r in d.get("rejected") or []:
            out[r.get("number")] = None
        time.sleep(0.5)
    return out


def raw(number):
    """17TRACK's untouched answer for one number (for the test report)."""
    return _call("gettrackinfo", [{"number": number}])


def _convert(ti):
    """17TRACK track_info → {"tracking_details": [{date, time, office, event}], "status17": "...", "sub17": "..."}"""
    events = []
    for prov in ((ti.get("tracking") or {}).get("providers") or []):
        for e in prov.get("events") or []:
            t = e.get("time_iso") or e.get("time_utc") or ""
            events.append({"date": t, "time": "", "office": e.get("location") or "", "event": e.get("description") or ""})
    latest = ti.get("latest_status") or {}
    if not events and not latest.get("status"):
        return None
    return {"tracking_details": events, "status17": latest.get("status") or "", "sub17": latest.get("sub_status") or "",
            "latest17": ti.get("latest_event") or {}}


def stage_from_17(data, keyword_stage):
    """17TRACK's own status beats keyword guessing when it has one."""
    st, sub = data.get("status17") or "", data.get("sub17") or ""
    if st == "Delivered":
        return "delivered"
    if st == "OutForDelivery":
        return "out"
    if st == "DeliveryFailure":
        return "failed"
    if st == "Exception":
        if "Returned" in sub:
            return "rto"
        if "Returning" in sub:
            return "returning"
        return "exception" if keyword_stage in ("transit", "") else keyword_stage
    if st == "Expired":
        return "expired"
    if st in ("InTransit", "InfoReceived", "AvailableForPickup"):
        return keyword_stage if keyword_stage in ("out", "failed", "returning") else "transit"
    if st == "NotFound":
        return "registered"
    return keyword_stage
