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


def snapshot_products(products: list[dict], supplier=None, prefix: str = "") -> None:
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
    # Only clear rows with this run's own label, so two suppliers never wipe
    # each other's products from the app.
    gone = [sku for sku in seen_before if sku not in live and sku.startswith(prefix)]
    for sku in gone:
        _call("DELETE", f"sync_products?sku=eq.{sku}",
              extra_headers={"Prefer": "return=minimal"})


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────
# Review list (MS Retro and other suppliers run with --review)
#   Table vj_review: one row per product waiting in the dashboard's
#   Photos → Review tab.  status: pending | approved | live | rejected | gone
# ──────────────────────────────────────────────────────────────

def forget_products(prefix: str) -> int:
    """Remove one supplier's products (by SKU label) from the app's product list."""
    if not ENABLED or not prefix or len(prefix) < 2:
        return 0
    rows = _call("GET", "sync_products?select=sku") or []
    gone = [r["sku"] for r in rows if (r.get("sku") or "").startswith(prefix)]
    for sku in gone:
        _call("DELETE", f"sync_products?sku=eq.{sku}", extra_headers={"Prefer": "return=minimal"})
    return len(gone)


_TITLE_COL = None


def has_title_column() -> bool:
    """True when vj_review has the supplier_title column (run the SQL once to add it)."""
    global _TITLE_COL
    if _TITLE_COL is None:
        _TITLE_COL = ENABLED and _call("GET", "vj_review?select=supplier_title&limit=1") is not None
    return bool(_TITLE_COL)


def review_rows() -> dict:
    """sku -> row, for the products of every review-mode supplier."""
    rows = []
    if has_title_column():
        rows = _call("GET", "vj_review?select=sku,status,product_id,price_auto,price_note,supplier_title") or []
    if not rows:
        rows = _call("GET", "vj_review?select=sku,status,product_id,price_auto,price_note") or []
    if not rows:   # older table without the price columns: fall back to the basics
        rows = _call("GET", "vj_review?select=sku,status,product_id") or []
    return {r["sku"]: r for r in rows if r.get("sku")}


def review_add(product, product_id, supplier_url: str = "") -> None:
    """A new hidden product: put it on the Review list. An existing row is
    never overwritten (so an approval or rejection is never undone)."""
    if not ENABLED:
        return
    handle = product.sku.split("-", 1)[1] if "-" in product.sku else product.sku
    try:
        price = min(v.price for v in product.variants) if product.variants else None
    except Exception:  # noqa: BLE001
        price = None
    row = {
        "sku": product.sku,
        "product_id": str(product_id),
        "name": (product.name or "")[:200],
        "supplier_url": f"{supplier_url.rstrip('/')}/products/{handle}" if supplier_url else "",
        "supplier_images": (product.images or [])[:10],
        "price": price,
        "price_auto": getattr(product, "price_auto", "") or "",
        "price_note": getattr(product, "price_note", "") or "",
        "status": "pending",
    }
    if has_title_column():                                   # the supplier's own title, for Copy details
        row["supplier_title"] = (getattr(product, "raw_title", "") or product.name or "")[:250]
    _call("POST", "vj_review?on_conflict=sku", json=[row],
          extra_headers={"Prefer": "resolution=ignore-duplicates,return=minimal"})


def prebook_drop(sku: str) -> None:
    """Pre-book product: take it out of the app's catalogue and off the
    Review list, so it cannot be seen or approved anywhere."""
    if not ENABLED or not sku:
        return
    _call("DELETE", f"sync_products?sku=eq.{sku}",
          extra_headers={"Prefer": "return=minimal"})
    _call("DELETE", f"vj_review?sku=eq.{sku}",
          extra_headers={"Prefer": "return=minimal"})


def price_overrides() -> dict:
    """sku -> jersey type you picked in the Review tab, e.g. 'HS|CN|EMB'."""
    rows = _call("GET", "vj_review?select=sku,price_type") or []
    return {r["sku"]: r["price_type"] for r in rows if r.get("sku") and r.get("price_type")}


def review_update(sku: str, fields: dict) -> None:
    if not ENABLED or not sku:
        return
    from datetime import datetime, timezone
    _call("PATCH", f"vj_review?sku=eq.{sku}", json={**fields, "updated_at": datetime.now(timezone.utc).isoformat()},
          extra_headers={"Prefer": "return=minimal"})


def review_mark(sku: str, status: str) -> None:
    if not ENABLED or not sku:
        return
    from datetime import datetime, timezone
    _call("PATCH", f"vj_review?sku=eq.{sku}",
          json={"status": status, "updated_at": datetime.now(timezone.utc).isoformat()},
          extra_headers={"Prefer": "return=minimal"})


def edited_images(product_id) -> list:
    """Your edited photos for a product (dashboard Photos tab), in slot order,
    skipping slots you removed. Used as the product's photos when it goes live."""
    rows = _call("GET", f"vj_shots?product_id=eq.{product_id}&select=shots,removed") or []
    if not rows:
        return []
    shots = rows[0].get("shots") or {}
    removed = {int(x) for x in (rows[0].get("removed") or []) if str(x).lstrip("-").isdigit()}
    out = []
    for k in sorted(shots, key=lambda x: int(x) if str(x).isdigit() else 999):
        if str(k).isdigit() and int(k) not in removed and shots[k]:
            out.append(shots[k])
    return out
