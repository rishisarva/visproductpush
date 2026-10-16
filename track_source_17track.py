"""
track_source_17track.py — parcel scans from 17TRACK's official API, across SEVERAL accounts
=============================================================================================

Each 17TRACK account comes with a one-time allowance of registrations (200 on a new free
account). To last longer, GitHub can hold several accounts' keys and spread parcels over
them:

  GitHub secret  TRACK17_KEYS  = key1,key2,key3      (comma-separated, any number of keys)
  (or the old    TRACK17_KEY   = one key — still works; it is simply the first account)

Rules the tracker follows:
  • A parcel is registered ONCE, on the account with the most credits left at that moment.
  • Every later question about that parcel (scans, stop) goes to the SAME account — 17TRACK
    only answers for numbers an account registered itself. The tracker remembers the account
    per parcel in vj_track.raw.reg17 (a short fingerprint of the key, never the key itself).
  • If an account runs dry or rejects a registration, the next account is tried.
  • The run log prints what is left on every account.

Keys are identified by a fingerprint (first 8 hex of sha1) so you can reorder or rotate the
secret without losing track of which account owns which parcel. Keep the ORIGINAL key in the
list for as long as its parcels are still moving.

Docs: https://api.17track.net/en/doc  (v2.2: register · gettrackinfo · stoptrack · getquota)
"""

from __future__ import annotations

import hashlib
import os
import time

import requests

API = "https://api.17track.net/track/v2.2/"
CARRIER = int(os.getenv("TRACK17_CARRIER", "0") or 0)     # 0 = let 17TRACK detect India Post from the number


def _keys_from_env() -> list[str]:
    raw = os.getenv("TRACK17_KEYS", "") or ""
    keys = [k.strip() for k in raw.replace("\n", ",").replace(";", ",").split(",") if k.strip()]
    one = (os.getenv("TRACK17_KEY", "") or "").strip()
    if one and one not in keys:
        keys.insert(0, one)
    return keys


def fingerprint(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:8]


class Account:
    def __init__(self, key: str):
        self.key = key
        self.fp = fingerprint(key)
        self.remain: int | None = None          # credits left, from getquota (None = unknown)
        self.total: int | None = None
        self.dead = False                       # key rejected / account gone this run
        self.error = ""

    def call(self, path: str, body) -> dict:
        try:
            r = requests.post(API + path, json=body, timeout=40,
                              headers={"17token": self.key, "Content-Type": "application/json"})
        except requests.RequestException as exc:
            return {"code": -1, "error": f"network: {exc}"}
        try:
            j = r.json()
        except ValueError:
            return {"code": r.status_code, "error": r.text[:200]}
        if j.get("code") == 401 or r.status_code == 401:
            self.dead = True
            self.error = "key not accepted (401)"
        return j

    def quota(self) -> None:
        """Ask 17TRACK how many registrations are left on this account."""
        j = self.call("getquota", {})
        d = j.get("data") or {}
        if isinstance(d, list) and d:
            d = d[0]
        if not isinstance(d, dict):
            return
        remain = d.get("quota_remain", d.get("remain", d.get("quota_remaining")))
        total = d.get("quota_total", d.get("total"))
        try:
            if remain is not None:
                self.remain = int(remain)
            if total is not None:
                self.total = int(total)
        except (TypeError, ValueError):
            pass


ACCOUNTS: list[Account] = [Account(k) for k in _keys_from_env()]
ENABLED = bool(ACCOUNTS)
KEY = ACCOUNTS[0].key if ACCOUNTS else ""            # kept for anything that still reads it

_owner: dict[str, str] = {}                           # number → account fingerprint (this run)
_quota_checked = False


def _by_fp(fp) -> Account | None:
    for a in ACCOUNTS:
        if a.fp == fp:
            return a
    return None


def _default() -> Account | None:
    return ACCOUNTS[0] if ACCOUNTS else None


def _chunks(seq, n=40):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _check_quota() -> None:
    global _quota_checked
    if _quota_checked:
        return
    _quota_checked = True
    for a in ACCOUNTS:
        a.quota()
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# public interface (same names the tracker already uses)
# ---------------------------------------------------------------------------

def assign(number: str, fp) -> None:
    """Tell the module which account registered this number (from vj_track.raw.reg17).
    Old rows store True instead of a fingerprint → the first (original) account."""
    if not number:
        return
    if isinstance(fp, str) and _by_fp(fp):
        _owner[number] = fp
    elif fp and _default():
        _owner[number] = _default().fp


def key_of(number: str):
    """Fingerprint of the account that owns this number — what the tracker stores in reg17."""
    return _owner.get(number)


def quota_line() -> str:
    """One line for the run log: credits left on every account."""
    _check_quota()
    parts = []
    for i, a in enumerate(ACCOUNTS, 1):
        if a.dead:
            parts.append(f"account {i} ({a.fp}): {a.error or 'not working'}")
        elif a.remain is None:
            parts.append(f"account {i} ({a.fp}): quota unknown")
        else:
            parts.append(f"account {i} ({a.fp}): {a.remain}{'/' + str(a.total) if a.total else ''} left")
    return " · ".join(parts) if parts else "no 17TRACK key"


def _live_sorted():
    """Usable accounts, most credits first (unknown quota counts as plenty, so a brand-new
    account whose quota call failed still gets used)."""
    live = [a for a in ACCOUNTS if not a.dead and (a.remain is None or a.remain > 0)]
    return sorted(live, key=lambda a: -(a.remain if a.remain is not None else 10 ** 6))


def register(numbers):
    """→ (accepted set, {number: reason} rejected). Each number goes to the account with the
    most credits left; a rejection for quota moves on to the next account."""
    _check_quota()
    ok, bad = set(), {}
    pending = [n for n in numbers if n]
    tried: dict[str, set[str]] = {}
    while pending:
        acc = next((a for a in _live_sorted() if any(a.fp not in tried.get(n, set()) for n in pending)), None)
        if acc is None:
            break
        part = [n for n in pending if acc.fp not in tried.get(n, set())]
        if acc.remain is not None:
            part = part[:acc.remain] or part[:1]
        body = [{"number": n, **({"carrier": CARRIER} if CARRIER else {})} for n in part]
        j = acc.call("register", body)
        d = j.get("data") or {}
        done = set()
        for a_ in d.get("accepted") or []:
            n = a_.get("number"); ok.add(n); _owner[n] = acc.fp; done.add(n)
            if acc.remain is not None:
                acc.remain = max(0, acc.remain - 1)
        for r in d.get("rejected") or []:
            n = r.get("number"); err = r.get("error") or {}
            msg = str(err.get("message", "rejected")); code = err.get("code")
            if code == -18019901 or "already" in msg.lower():          # already registered on THIS account
                ok.add(n); _owner[n] = acc.fp; done.add(n)
            elif code in (-18019902, -18019903) or any(w in msg.lower() for w in ("quota", "insufficient", "balance", "exceed")):
                acc.remain = 0                                          # this account is dry → try the next one
            else:
                bad[n] = msg; done.add(n)
        if j.get("code") not in (0, None):
            acc.dead = True
            acc.error = f"{j.get('message') or j.get('error') or j.get('code')}"
        for n in part:
            tried.setdefault(n, set()).add(acc.fp)
        pending = [n for n in pending if n not in done]
        time.sleep(0.5)
    for n in pending:
        bad.setdefault(n, "every 17TRACK account is out of credits (or not working)")
    return ok, bad


def _grouped(numbers):
    groups: dict[str, list[str]] = {}
    for n in numbers:
        acc = _by_fp(_owner.get(n)) or _default()
        if acc is not None:
            groups.setdefault(acc.fp, []).append(n)
    return groups


def info(numbers):
    """→ {number: data-dict in India Post's shape, or None when 17TRACK has nothing yet}.
    Each number is asked from the account that registered it."""
    out = {}
    for fp, nums in _grouped(numbers).items():
        acc = _by_fp(fp)
        for part in _chunks(nums):
            j = acc.call("gettrackinfo", [{"number": n} for n in part])
            d = j.get("data") or {}
            for a_ in d.get("accepted") or []:
                out[a_.get("number")] = _convert(a_.get("track_info") or {})
            for r in d.get("rejected") or []:
                out[r.get("number")] = None
            time.sleep(0.5)
    return out


def stop(numbers):
    for fp, nums in _grouped(numbers).items():
        acc = _by_fp(fp)
        for part in _chunks(nums):
            acc.call("stoptrack", [{"number": n} for n in part])


def raw(number):
    """17TRACK's untouched answer for one number (for the test report)."""
    acc = _by_fp(_owner.get(number)) or _default()
    return acc.call("gettrackinfo", [{"number": number}]) if acc else {}


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
