"""
sync_cloud.py
=============

The bridge between the supplier sync and the app.

The site's bot wall blocks traffic going *into* WordPress, but nothing stops
either side talking to Supabase. So the sync script reports what it did and
reads the blocklist here, and the app reads the same tables. Same idea as the
woo_mirror bridge the plugin already uses.

Needs two environment variables, set as GitHub secrets:

    SUPABASE_URL   https://xxxx.supabase.co
    SUPABASE_KEY   the service_role key (server side only, never in the app)

If they are missing the sync still runs normally; it just skips reporting.
"""

from __future__ import annotations

import os
import sys

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")


URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
KEY = os.getenv("SUPABASE_KEY") or ""
ENABLED = bool(URL and KEY)

_HEADERS = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
}


def _call(method: str, path: str, **kwargs):
    if not ENABLED:
        return None
    try:
        r = requests.request(method, f"{URL}/rest/v1/{path}",
                             headers={**_HEADERS, **kwargs.pop("extra_headers", {})},
                             timeout=30, **kwargs)
        if r.status_code >= 400:
            print(f"   cloud: {method} {path} -> {r.status_code} {r.text[:120]}")
            return None
        return r.json() if r.text.strip() else []
    except requests.RequestException as exc:
        print(f"   cloud: {method} {path} failed: {exc}")
        return None


# ──────────────────────────────────────────────────────────────
# Blocklist
# ──────────────────────────────────────────────────────────────

def blocked_skus() -> set[str]:
    """SKUs the owner has removed. These are never created again."""
    rows = _call("GET", "sync_blocklist?select=sku")
    if not rows:
        return set()
    return {r["sku"] for r in rows if r.get("sku")}


# ──────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────

def report_run(stats, *, supplier_products: int, site_products: int,
               seconds: float, blocked: int = 0, note: str = "") -> None:
    """Record one run so the app can show what happened and when."""
    if not ENABLED:
        return
    row = {
        "ok": stats.errors == 0,
        "supplier_products": supplier_products,
        "site_products": site_products,
        "created": stats.created,
        "price_changes": stats.price_updates,
        "stock_changes": stats.stock_updates,
        "sizes_added": stats.sizes_added,
        "sizes_removed": stats.sizes_retired,
        "relisted": stats.relisted,
        "drafted": stats.drafted,
        "blocked": blocked,
        "errors": stats.errors,
        "seconds": int(seconds),
        "note": note[:400],
    }
    _call("POST", "sync_runs", json=row,
          extra_headers={"Prefer": "return=minimal"})


def snapshot_products(products: list[dict], supplier=None) -> None:
    """
    Mirror the live catalogue so the app's grid loads instantly, and still
    works on a phone the site's wall refuses to talk to.

    products: WooCommerce product dicts, as returned by the REST API.
    supplier: the SupplierProduct list, which is where size names and their
              stock come from. Woo would need one request per product to tell
              us the same thing.
    """
    if not ENABLED or not products:
        return

    sizes_by_sku = {}
    if supplier:
        for sp in supplier:
            try:
                sizes_by_sku[sp.sku] = (
                    [v.size for v in sp.variants if v.size],
                    [v.size for v in sp.variants if v.size and v.in_stock],
                )
            except AttributeError:
                pass

    # Keep the date a product was first mirrored, so the app can mark new
    # arrivals. Overwriting it every run would make everything look new.
    seen_before = {}
    for row in (_call("GET", "sync_products?select=sku,first_seen") or []):
        if row.get("sku"):
            seen_before[row["sku"]] = row.get("first_seen")

    rows = []
    for p in products:
        sku = p.get("sku") or ""
        if not sku:
            continue
        images = p.get("images") or []
        all_sizes, in_stock_sizes = sizes_by_sku.get(sku, (None, None))
        row = {
            "sku": sku,
            "product_id": p.get("id"),
            "name": (p.get("name") or "")[:200],
            "image": (images[0].get("src") if images else "") or "",
            "price": _num(p.get("price")),
            "in_stock": p.get("stock_status") != "outofstock",
            "permalink": p.get("permalink") or "",
        }
        if all_sizes is not None:
            row["sizes"] = all_sizes
            row["sizes_stock"] = in_stock_sizes
        if seen_before.get(sku):
            row["first_seen"] = seen_before[sku]
        rows.append(row)

    for i in range(0, len(rows), 100):
        _call("POST", "sync_products", json=rows[i:i + 100],
              extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    # drop rows for products that no longer exist on the site
    live = {r["sku"] for r in rows}
    gone = [sku for sku in seen_before if sku not in live]
    for sku in gone:
        _call("DELETE", f"sync_products?sku=eq.{sku}",
              extra_headers={"Prefer": "return=minimal"})


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
