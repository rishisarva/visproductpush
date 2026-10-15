"""
track_source_aftership.py — parcel scans from AfterShip's API (Free plan: 50 shipments / month, every month)
=========================================================================================================

Used by track_indiapost.py when the GitHub secret AFTERSHIP_KEY is set (it takes
priority over 17TRACK). Same interface as track_source_17track: register / info /
stop / stage_from. API docs: https://www.aftership.com/docs/tracking/quickstart
"""

from __future__ import annotations

import os
import time

import requests

KEY = os.getenv("AFTERSHIP_KEY", "").strip()
SLUG = os.getenv("AFTERSHIP_SLUG", "india-post").strip() or "india-post"
API = "https://api.aftership.com/tracking/2024-10"
ENABLED = bool(KEY)
H = {"as-api-key": KEY, "Content-Type": "application/json"}


def _call(method, path, body=None):
    r = requests.request(method, API + path, json=body, headers=H, timeout=40)
    try:
        j = r.json()
    except ValueError:
        j = {}
    j["_status"] = r.status_code
    return j


def register(numbers):
    ok, bad = set(), {}
    for n in numbers:
        j = _call("POST", "/trackings", {"tracking": {"tracking_number": n, "slug": SLUG}})
        code = (j.get("meta") or {}).get("code", j.get("_status"))
        if code in (201, 200) or code == 4003:          # 4003 = already exists
            ok.add(n)
        else:
            bad[n] = (j.get("meta") or {}).get("message") or f"AfterShip HTTP {j.get('_status')}"
        time.sleep(0.4)
    return ok, bad


def stop(numbers):
    for n in numbers:
        try:
            _call("DELETE", f"/trackings/{SLUG}/{n}")
        except requests.RequestException:
            pass


def info(numbers):
    out = {}
    for n in numbers:
        j = _call("GET", f"/trackings/{SLUG}/{n}")
        t = (j.get("data") or {}).get("tracking") or {}
        out[n] = _convert(t) if t else None
        time.sleep(0.4)
    return out


def _convert(t):
    events = [{"date": c.get("checkpoint_time") or "", "time": "", "office": c.get("location") or c.get("city") or "",
               "event": c.get("message") or ""} for c in (t.get("checkpoints") or [])]
    tag = t.get("tag") or ""
    if not events and tag in ("", "Pending"):
        return None
    return {"tracking_details": events, "status17": tag, "sub17": t.get("subtag") or ""}


def stage_from_17(data, keyword_stage):
    """AfterShip tag → our stage (name kept so track_indiapost.py can treat both sources alike)."""
    tag, sub = data.get("status17") or "", data.get("sub17") or ""
    if tag == "Delivered":
        return "delivered"
    if tag == "OutForDelivery":
        return "out"
    if tag == "AttemptFail":
        return "failed"
    if tag == "Exception":
        if sub in ("Exception_010", "Exception_011") or "return" in keyword_stage:   # 010 returning, 011 returned
            return "rto" if sub == "Exception_011" or keyword_stage == "rto" else "returning"
        return "exception"
    if tag == "Expired":
        return "expired"
    if tag in ("InTransit", "InfoReceived", "AvailableForPickup"):
        return keyword_stage if keyword_stage in ("out", "failed", "returning") else "transit"
    if tag == "Pending":
        return "registered"
    return keyword_stage
