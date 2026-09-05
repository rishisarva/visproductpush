#!/usr/bin/env python3
"""
visions_sync.py
===============

Keeps visionsjersey.com (WooCommerce) in sync with a Shopify supplier feed.

Selling price = supplier price + MARGIN   (default 170)
    supplier 440  ->  live 610

Commands
--------
    python visions_sync.py test            # check both ends, change nothing
    python visions_sync.py sync --dry-run  # show exactly what would change
    python visions_sync.py sync            # apply changes
    python visions_sync.py wipe --yes      # delete every synced product

Setup
-----
    pip install requests

    export WC_URL="https://visionsjersey.com"
    export WC_KEY="ck_..."
    export WC_SECRET="cs_..."

Cron, every 4 hours:
    0 */4 * * * cd /path/to && /usr/bin/python3 visions_sync.py sync >> sync.log 2>&1
"""

from __future__ import annotations

import argparse
import html
import logging
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterator

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

import sync_cloud


# ==========================================================================
# Settings
# ==========================================================================

# ------------------------------------------------------------------ #
#  EDIT THESE FOUR LINES, THEN SAVE. That's the whole setup.          #
# ------------------------------------------------------------------ #

WC_URL = "https://visionsjersey.com"
WC_KEY = "ck_paste_your_key_here"
WC_SECRET = "cs_paste_your_secret_here"

MARGIN = 170.0            # added to every supplier price: 440 -> 610

# ------------------------------------------------------------------ #
#  Below here you can leave alone.                                    #
# ------------------------------------------------------------------ #

SUPPLIER_URL = "https://www.thayyilsports.in"
SUPPLIER_COLLECTION = "best-selling"
SKU_PREFIX = "TS-"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

log = logging.getLogger("sync")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ==========================================================================
# Pricing
# ==========================================================================

def sell_price(supplier_price: float, margin: float, round_to: int) -> float:
    """440 + 170 = 610"""
    price = supplier_price + margin
    if round_to > 0:
        price = math.ceil(price / round_to) * round_to
    return round(price, 2)


def money(value: Any) -> str:
    """WooCommerce compares prices as strings; normalise both sides."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


# ==========================================================================
# Supplier side (Shopify storefront JSON)
# ==========================================================================

@dataclass
class Variant:
    sku: str
    size: str
    supplier_price: float
    price: float
    in_stock: bool
    weight_kg: float
    image: str = ""


@dataclass
class SupplierProduct:
    sku: str
    name: str
    description: str
    category: str
    tags: list[str]
    images: list[str]
    option_name: str                       # usually "Size"
    variants: list[Variant] = field(default_factory=list)

    @property
    def sizes(self) -> list[str]:
        return [v.size for v in self.variants]

    @property
    def any_in_stock(self) -> bool:
        return any(v.in_stock for v in self.variants)


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_supplier(base_url: str, collection: str, margin: float,
                   round_to: int, keep_html: bool,
                   delay: float = 0.6) -> list[SupplierProduct]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    base = base_url.rstrip("/")
    endpoint = (f"{base}/collections/{collection}/products.json"
                if collection else f"{base}/products.json")

    products: list[SupplierProduct] = []

    for page in range(1, 51):
        resp = session.get(endpoint, params={"limit": 250, "page": page}, timeout=30)
        if resp.status_code == 404:
            raise SystemExit(f"404 at {endpoint} — check the collection handle.")
        if resp.status_code == 430 or resp.status_code == 429:
            log.warning("Rate limited, backing off 10s")
            time.sleep(10)
            continue
        resp.raise_for_status()

        try:
            batch = resp.json().get("products", [])
        except ValueError:
            raise SystemExit(
                f"{endpoint} returned HTML, not JSON. The store may have the "
                f"JSON feed disabled or be blocking this request."
            )

        if not batch:
            break

        for raw in batch:
            product = _normalise(raw, base, margin, round_to, keep_html)
            if product:
                products.append(product)

        if len(batch) < 250:
            break
        time.sleep(delay)

    return products


def _normalise(raw: dict, base: str, margin: float,
               round_to: int, keep_html: bool) -> SupplierProduct | None:
    handle = raw.get("handle") or str(raw.get("id", ""))
    if not handle:
        return None

    parent_sku = f"{SKU_PREFIX}{handle}"

    option_names = [o.get("name", "") for o in raw.get("options", [])]
    option_name = option_names[0] if option_names else ""
    if option_name.strip().lower() == "title":
        option_name = ""

    variants: list[Variant] = []
    for v in raw.get("variants", []):
        try:
            supplier_price = float(v.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if supplier_price <= 0:
            continue

        size = str(v.get("option1") or "").strip()
        if option_name and not size:
            continue

        variants.append(Variant(
            sku=f"{parent_sku}-{v.get('id')}",
            size=size,
            supplier_price=supplier_price,
            price=sell_price(supplier_price, margin, round_to),
            in_stock=bool(v.get("available", True)),
            weight_kg=round((v.get("grams") or 0) / 1000.0, 3),
            image=(v.get("featured_image") or {}).get("src", "") or "",
        ))

    if not variants:
        return None

    # Collapse duplicate sizes, keeping the first
    seen: set[str] = set()
    unique: list[Variant] = []
    for v in variants:
        key = v.size.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(v)

    body = raw.get("body_html", "") or ""
    return SupplierProduct(
        sku=parent_sku,
        name=(raw.get("title") or "").strip(),
        description=body.strip() if keep_html else strip_html(body),
        category=(raw.get("product_type") or "").strip() or "Jerseys",
        tags=[t for t in (raw.get("tags") or []) if isinstance(t, str)],
        images=[i["src"] for i in raw.get("images", []) if i.get("src")],
        option_name=option_name,
        variants=unique,
    )


# ==========================================================================
# WooCommerce client
# ==========================================================================

class Woo:
    def __init__(self, url: str, key: str, secret: str, timeout: int = 60):
        self.base = url.rstrip("/") + "/wp-json/wc/v3"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (key, secret)
        self.session.headers.update({"User-Agent": "visions-sync/1.0"})
        self._attr_cache: dict[str, int] = {}
        self._term_cache: dict[int, set[str]] = {}

    def call(self, method: str, path: str, **kwargs) -> Any:
        for attempt in range(6):
            try:
                resp = self.session.request(method, self.base + path,
                                            timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt == 3:
                    raise
                log.warning("Network error (%s), retry %d", exc, attempt + 1)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code in (429, 502, 503, 504) and attempt < 5:
                wait = 2 ** attempt
                if resp.status_code == 429:
                    # the host is throttling us; back off hard and respect
                    # Retry-After when the server sends one
                    try:
                        wait = max(wait, int(resp.headers.get("Retry-After", 0)))
                    except ValueError:
                        pass
                    wait = max(wait, 5 * (attempt + 1))
                log.warning("HTTP %s, waiting %ss (attempt %d)",
                            resp.status_code, wait, attempt + 1)
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise RuntimeError(
                    f"{method} {path} -> {resp.status_code}: {resp.text[:300]}"
                )
            return resp.json() if resp.text else {}
        raise RuntimeError(f"{method} {path} failed after retries")

    # ---- reads ----------------------------------------------------------

    def list_products(self, sku_prefix: str) -> list[dict]:
        out: list[dict] = []
        for page in range(1, 101):
            batch = self.call("GET", "/products", params={
                "per_page": 100, "page": page, "status": "any",
                "orderby": "id", "order": "asc",
            })
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
        return [p for p in out if (p.get("sku") or "").startswith(sku_prefix)]

    def find_by_sku(self, sku: str) -> int | None:
        """Look up a product id by SKU, or None."""
        try:
            found = self.call("GET", "/products",
                              params={"sku": sku, "status": "any"})
        except RuntimeError:
            return None
        return found[0]["id"] if found else None

    def variations(self, product_id: int) -> list[dict]:
        out: list[dict] = []
        for page in range(1, 21):
            batch = self.call("GET", f"/products/{product_id}/variations",
                              params={"per_page": 100, "page": page})
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
        return out

    def load_variations_bulk(self, products: list[dict],
                             workers: int = 1,
                             pause: float = 0.25) -> dict[int, list[dict]]:
        """
        Read variations for every variable product.

        Deliberately gentle: shared hosts rate-limit aggressively, and a
        burst of parallel requests earns a 429 that costs far more time
        than the pauses do.
        """
        variable = [p for p in products if p.get("type") == "variable"]
        result: dict[int, list[dict]] = {}
        if not variable:
            return result

        if workers <= 1:
            for i, p in enumerate(variable, start=1):
                try:
                    result[p["id"]] = self.variations(p["id"])
                except Exception as exc:  # noqa: BLE001
                    log.error("Could not read variations for #%s: %s", p["id"], exc)
                    result[p["id"]] = []
                if i % 50 == 0:
                    log.info("   read variations for %d/%d", i, len(variable))
                time.sleep(pause)
            return result

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.variations, p["id"]): p["id"]
                       for p in variable}
            for future, pid in futures.items():
                try:
                    result[pid] = future.result()
                except Exception as exc:  # noqa: BLE001
                    log.error("Could not read variations for #%s: %s", pid, exc)
                    result[pid] = []
        return result

    # ---- global attribute (so layered-nav size filters work) ------------

    def attribute_id(self, name: str) -> int:
        key = name.strip().lower()
        if key in self._attr_cache:
            return self._attr_cache[key]

        for attr in self.call("GET", "/products/attributes"):
            if attr.get("name", "").strip().lower() == key:
                self._attr_cache[key] = attr["id"]
                return attr["id"]

        created = self.call("POST", "/products/attributes", json={
            "name": name, "slug": re.sub(r"\W+", "-", key).strip("-"),
            "type": "select", "order_by": "menu_order", "has_archives": False,
        })
        self._attr_cache[key] = created["id"]
        return created["id"]

    def ensure_terms(self, attr_id: int, values: list[str]) -> None:
        if attr_id not in self._term_cache:
            existing: set[str] = set()
            for page in range(1, 11):
                batch = self.call("GET", f"/products/attributes/{attr_id}/terms",
                                  params={"per_page": 100, "page": page})
                if not batch:
                    break
                existing.update(t["name"].strip().lower() for t in batch)
                if len(batch) < 100:
                    break
            self._term_cache[attr_id] = existing

        known = self._term_cache[attr_id]
        missing = [v for v in values if v.strip().lower() not in known]
        for value in missing:
            try:
                self.call("POST", f"/products/attributes/{attr_id}/terms",
                          json={"name": value})
                known.add(value.strip().lower())
            except RuntimeError as exc:
                if "term_exists" in str(exc):
                    known.add(value.strip().lower())
                else:
                    raise


# ==========================================================================
# Reconcile
# ==========================================================================

@dataclass
class Stats:
    created: int = 0
    price_updates: int = 0
    stock_updates: int = 0
    sizes_added: int = 0
    sizes_retired: int = 0
    relisted: int = 0
    drafted: int = 0
    unchanged: int = 0
    blocked_skipped: int = 0
    blocked_removed: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (
            f"created {self.created} | price changes {self.price_updates} | "
            f"stock changes {self.stock_updates} | sizes +{self.sizes_added} "
            f"-{self.sizes_retired} | relisted {self.relisted} | "
            f"drafted {self.drafted} | unchanged {self.unchanged} | "
            f"blocked {self.blocked_skipped + self.blocked_removed} | "
            f"errors {self.errors}"
        )


class Syncer:
    def __init__(self, woo: Woo, args):
        self.woo = woo
        self.args = args
        self.stats = Stats()
        self.dry = args.dry_run

    # ---- helpers --------------------------------------------------------

    def _attr_block(self, option_name: str, values: list[str]) -> list[dict]:
        if not option_name:
            return []
        if self.args.local_attributes:
            return [{"name": option_name, "visible": True,
                     "variation": True, "options": values}]
        attr_id = self.woo.attribute_id(option_name)
        self.woo.ensure_terms(attr_id, values)
        return [{"id": attr_id, "visible": True,
                 "variation": True, "options": values}]

    def _variation_attr(self, option_name: str, value: str) -> list[dict]:
        if not option_name:
            return []
        if self.args.local_attributes:
            return [{"name": option_name, "option": value}]
        return [{"id": self.woo.attribute_id(option_name), "option": value}]

    # ---- create ---------------------------------------------------------

    def create(self, product: SupplierProduct) -> None:
        variable = bool(product.option_name)
        log.info("CREATE  %s  (%d sizes)", product.name[:50], len(product.variants))

        if self.dry:
            self.stats.created += 1
            return

        payload: dict[str, Any] = {
            "name": product.name,
            "sku": product.sku,
            "type": "variable" if variable else "simple",
            "status": "publish",
            "catalog_visibility": "visible",
            "description": product.description,
            "categories": [{"name": product.category}],
            "tags": [{"name": t} for t in product.tags[:10]],
            "images": [{"src": src} for src in product.images[:self.args.max_images]],
            "attributes": self._attr_block(product.option_name, product.sizes),
        }

        if not variable:
            v = product.variants[0]
            payload["regular_price"] = money(v.price)
            payload["manage_stock"] = False
            payload["stock_status"] = "instock" if v.in_stock else "outofstock"
        else:
            payload["stock_status"] = "instock" if product.any_in_stock else "outofstock"

        # WooCommerce briefly locks a SKU while it creates the product.
        # If we hit that lock, wait for it to clear, then check whether the
        # product actually landed before trying again.
        created = None
        for attempt in range(3):
            try:
                created = self.woo.call("POST", "/products", json=payload)
                break
            except RuntimeError as exc:
                text = str(exc).lower()

                if "under processing" in text:
                    # WooCommerce could not claim a lock on this SKU, so it
                    # deleted the product it had just made. This is almost
                    # always an orphaned SKU row left in wc_product_meta_lookup
                    # by an earlier deleted product. Waiting never clears it,
                    # so fail fast with something actionable.
                    existing_id = self.woo.find_by_sku(product.sku)
                    if existing_id:
                        log.info("   it landed anyway as #%s", existing_id)
                        created = {"id": existing_id}
                        break
                    raise RuntimeError(
                        f"SKU {product.sku} is blocked by a stale database row. "
                        f"No product actually uses it. Clear the orphaned row "
                        f"from wc_product_meta_lookup to fix this permanently."
                    ) from None

                transient = ("already present" in text
                             or "duplicated sku" in text)
                if not transient or attempt == 2:
                    raise
                wait = 15 * (attempt + 1)
                log.warning("SKU busy for %s, waiting %ss", product.sku, wait)
                time.sleep(wait)
                existing_id = self.woo.find_by_sku(product.sku)
                if existing_id:
                    log.info("   it landed anyway as #%s", existing_id)
                    created = {"id": existing_id}
                    break

        if created is None:
            raise RuntimeError(f"could not create {product.sku}")

        pid = created["id"]

        if variable:
            batch = [{
                "sku": v.sku,
                "regular_price": money(v.price),
                "manage_stock": False,
                "stock_status": "instock" if v.in_stock else "outofstock",
                "attributes": self._variation_attr(product.option_name, v.size),
                **({"weight": str(v.weight_kg)} if v.weight_kg else {}),
                **({"image": {"src": v.image}} if v.image and self.args.variant_images else {}),
            } for v in product.variants]

            for chunk in _chunks(batch, 50):
                self.woo.call("POST", f"/products/{pid}/variations/batch",
                              json={"create": chunk})

        self.stats.created += 1

    # ---- update ---------------------------------------------------------

    def update(self, product: SupplierProduct, existing: dict,
               existing_variations: list[dict]) -> None:
        pid = existing["id"]
        parent_changes: dict[str, Any] = {}

        # Product was drafted earlier because it vanished, and it's back
        if existing.get("status") != "publish":
            parent_changes["status"] = "publish"
            self.stats.relisted += 1
            log.info("RELIST  %s", product.name[:50])

        want_parent_stock = "instock" if product.any_in_stock else "outofstock"
        if existing.get("stock_status") != want_parent_stock:
            parent_changes["stock_status"] = want_parent_stock

        if self.args.sync_titles and existing.get("name") != product.name:
            parent_changes["name"] = product.name

        if not product.option_name:
            # simple product
            v = product.variants[0]
            if money(existing.get("regular_price")) != money(v.price):
                log.info("PRICE   %s  %s -> %s", product.name[:40],
                         existing.get("regular_price"), money(v.price))
                parent_changes["regular_price"] = money(v.price)
                self.stats.price_updates += 1
            if parent_changes and not self.dry:
                self.woo.call("PUT", f"/products/{pid}", json=parent_changes)
            if not parent_changes:
                self.stats.unchanged += 1
            return

        by_sku = {v.get("sku"): v for v in existing_variations if v.get("sku")}
        supplier_skus = {v.sku for v in product.variants}

        to_create: list[dict] = []
        to_update: list[dict] = []
        touched = False

        for v in product.variants:
            current = by_sku.get(v.sku)

            if current is None:
                # A size the supplier has started carrying
                log.info("SIZE +  %s  [%s]", product.name[:40], v.size)
                to_create.append({
                    "sku": v.sku,
                    "regular_price": money(v.price),
                    "manage_stock": False,
                    "stock_status": "instock" if v.in_stock else "outofstock",
                    "attributes": self._variation_attr(product.option_name, v.size),
                    **({"weight": str(v.weight_kg)} if v.weight_kg else {}),
                    **({"image": {"src": v.image}} if v.image and self.args.variant_images else {}),
                })
                self.stats.sizes_added += 1
                touched = True
                continue

            delta: dict[str, Any] = {}

            if money(current.get("regular_price")) != money(v.price):
                log.info("PRICE   %s [%s]  %s -> %s", product.name[:36], v.size,
                         current.get("regular_price"), money(v.price))
                delta["regular_price"] = money(v.price)
                self.stats.price_updates += 1

            want_stock = "instock" if v.in_stock else "outofstock"
            if current.get("stock_status") != want_stock:
                log.info("STOCK   %s [%s]  -> %s", product.name[:36], v.size, want_stock)
                delta["stock_status"] = want_stock
                delta["manage_stock"] = False
                self.stats.stock_updates += 1

            if delta:
                delta["id"] = current["id"]
                to_update.append(delta)
                touched = True

        # Sizes that disappeared from the supplier feed entirely
        for sku, current in by_sku.items():
            if sku in supplier_skus:
                continue
            if self.args.prune_sizes:
                log.info("SIZE -  %s  [%s] delete", product.name[:40], sku)
                if not self.dry:
                    self.woo.call("DELETE", f"/products/{pid}/variations/{current['id']}",
                                  params={"force": True})
                self.stats.sizes_retired += 1
                touched = True
            elif current.get("stock_status") != "outofstock":
                log.info("SIZE -  %s  [%s] -> outofstock", product.name[:40], sku)
                to_update.append({"id": current["id"], "stock_status": "outofstock",
                                  "manage_stock": False})
                self.stats.sizes_retired += 1
                touched = True

        # Parent attribute list must contain every size we now offer
        live_sizes = product.sizes
        current_options: list[str] = []
        for attr in existing.get("attributes", []):
            if attr.get("name", "").strip().lower() == product.option_name.strip().lower():
                current_options = attr.get("options", [])
        if sorted(o.lower() for o in current_options) != sorted(s.lower() for s in live_sizes):
            parent_changes["attributes"] = self._attr_block(product.option_name, live_sizes)
            touched = True

        if self.dry:
            if not touched and not parent_changes:
                self.stats.unchanged += 1
            return

        if parent_changes:
            self.woo.call("PUT", f"/products/{pid}", json=parent_changes)

        if to_create or to_update:
            for chunk in _chunks(to_create, 50):
                self.woo.call("POST", f"/products/{pid}/variations/batch",
                              json={"create": chunk})
            for chunk in _chunks(to_update, 50):
                self.woo.call("POST", f"/products/{pid}/variations/batch",
                              json={"update": chunk})

        if not touched and not parent_changes:
            self.stats.unchanged += 1

    # ---- products that vanished from the feed ---------------------------

    def retire(self, existing: dict) -> None:
        name = existing.get("name", "")[:50]
        if self.args.delete_missing:
            log.info("DELETE  %s (gone from supplier)", name)
            if not self.dry:
                self.woo.call("DELETE", f"/products/{existing['id']}",
                              params={"force": True})
        else:
            if existing.get("status") == "draft":
                return
            log.info("DRAFT   %s (gone from supplier)", name)
            if not self.dry:
                self.woo.call("PUT", f"/products/{existing['id']}",
                              json={"status": "draft", "stock_status": "outofstock"})
        self.stats.drafted += 1

    # ---- driver ---------------------------------------------------------

    def run(self, supplier: list[SupplierProduct]) -> Stats:
        log.info("Reading existing products from WooCommerce...")
        existing_list = self.woo.list_products(SKU_PREFIX)
        log.info("Found %d previously synced products", len(existing_list))

        # Products the owner removed in the app. These are deleted if present
        # and never created again, however often this runs.
        self.blocked = sync_cloud.blocked_skus()
        if self.blocked:
            log.info("Blocklist holds %d product(s)", len(self.blocked))
            for prod in existing_list:
                sku = prod.get("sku")
                if sku in self.blocked:
                    try:
                        self.woo.call("DELETE", f"/products/{prod['id']}",
                                      params={"force": True})
                        log.info("REMOVED %s (blocked in app)",
                                 prod.get("name", "")[:46])
                        self.stats.blocked_removed += 1
                    except Exception as exc:  # noqa: BLE001
                        log.error("Could not remove blocked %s: %s", sku, exc)
            existing_list = [p for p in existing_list
                             if p.get("sku") not in self.blocked]
            before = len(supplier)
            supplier = [p for p in supplier if p.sku not in self.blocked]
            self.stats.blocked_skipped = before - len(supplier)

        variations_map = self.woo.load_variations_bulk(
            existing_list,
            workers=getattr(self.args, 'workers', 1),
            pause=getattr(self.args, 'read_pause', 0.25))
        by_sku = {p["sku"]: p for p in existing_list}
        supplier_by_sku = {p.sku: p for p in supplier}

        for i, product in enumerate(supplier, start=1):
            try:
                current = by_sku.get(product.sku)
                if current is None:
                    self.create(product)
                else:
                    self.update(product, current, variations_map.get(current["id"], []))
            except Exception as exc:  # noqa: BLE001
                self.stats.errors += 1
                log.error("FAILED  %s: %s", product.name[:45], exc)

            if i % 25 == 0:
                log.info("...%d/%d processed", i, len(supplier))
            time.sleep(self.args.throttle)

        for sku, current in by_sku.items():
            if sku in supplier_by_sku:
                continue
            try:
                self.retire(current)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors += 1
                log.error("FAILED retiring %s: %s", sku, exc)

        return self.stats


def _chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ==========================================================================
# Commands
# ==========================================================================

def have_keys(args) -> bool:
    """True only if real-looking credentials are present."""
    return (
        bool(args.wc_key) and bool(args.wc_secret)
        and "paste_your" not in args.wc_key
        and "paste_your" not in args.wc_secret
    )


def require_keys(args) -> None:
    if not have_keys(args):
        sys.exit(
            "\nWooCommerce keys are not set.\n"
            "Open visions_sync.py, find the EDIT THESE FOUR LINES block at the\n"
            "top, and paste your ck_... and cs_... between the quotes. Save,\n"
            "then run this again.\n"
        )


def load_supplier(args) -> list[SupplierProduct]:
    products = fetch_supplier(args.supplier, args.collection, args.margin,
                              args.round_to, args.keep_html)

    override = getattr(args, "attribute_name", "")
    if override:
        for p in products:
            if p.option_name:
                p.option_name = override

    if args.skip_sold_out:
        before = len(products)
        products = [p for p in products if p.any_in_stock]
        skipped = before - len(products)
        if skipped:
            log.info("Skipped %d fully sold-out products", skipped)
    return products


def cmd_test(args) -> int:
    print("=" * 68)
    print("SUPPLIER")
    print("=" * 68)
    supplier = load_supplier(args)
    variants = sum(len(p.variants) for p in supplier)
    in_stock = sum(1 for p in supplier for v in p.variants if v.in_stock)
    print(f"  products     : {len(supplier)}")
    print(f"  variants     : {variants}  ({in_stock} in stock, "
          f"{variants - in_stock} out of stock)")
    print(f"  margin       : +{args.margin:.0f}")

    print("\n  sample:")
    for p in supplier[:4]:
        print(f"    {p.name[:52]}")
        for v in p.variants[:5]:
            flag = "in stock " if v.in_stock else "SOLD OUT "
            print(f"      {v.size:<8} {flag} {v.supplier_price:>7.0f} "
                  f"+ {args.margin:.0f}  =  {v.price:>7.0f}")
        if len(p.variants) > 5:
            print(f"      ... {len(p.variants) - 5} more sizes")
        print(f"      images: {len(p.images)}")

    print()
    print("=" * 68)
    print("WOOCOMMERCE")
    print("=" * 68)
    if not have_keys(args):
        print("  keys not set yet — paste them into the top of this file")
        return 0

    woo = Woo(args.wc_url, args.wc_key, args.wc_secret)
    try:
        woo.call("GET", "/products", params={"per_page": 1})
        print(f"  connection   : OK  ({args.wc_url})")
    except Exception as exc:  # noqa: BLE001
        print(f"  connection   : FAILED — {exc}")
        return 1

    mine = woo.list_products(SKU_PREFIX)
    published = sum(1 for p in mine if p.get("status") == "publish")
    print(f"  synced items : {len(mine)}  ({published} published, "
          f"{len(mine) - published} draft)")

    supplier_skus = {p.sku for p in supplier}
    live_skus = {p["sku"] for p in mine}
    print(f"  would create : {len(supplier_skus - live_skus)}")
    print(f"  would retire : {len(live_skus - supplier_skus)}")
    print(f"  would check  : {len(supplier_skus & live_skus)}")
    print("\nAll good. Next:  python visions_sync.py sync --dry-run")
    return 0


def cmd_sync(args) -> int:
    require_keys(args)

    started = time.time()
    log.info("=" * 60)
    log.info("Sync start%s", "  [DRY RUN — nothing will change]" if args.dry_run else "")

    supplier = load_supplier(args)
    log.info("Supplier feed: %d products, %d variants",
             len(supplier), sum(len(p.variants) for p in supplier))

    if not supplier:
        log.error("Supplier feed came back empty — aborting so we don't draft "
                  "the whole catalogue by mistake.")
        return 1

    if args.limit:
        supplier = supplier[:args.limit]
        log.info("Limited to first %d products", len(supplier))

    woo = Woo(args.wc_url, args.wc_key, args.wc_secret)
    stats = Syncer(woo, args).run(supplier)

    elapsed = time.time() - started
    log.info("-" * 60)
    log.info("Done in %.1fs — %s", elapsed, stats.summary())

    # Tell the app what happened, and mirror the catalogue so its grid loads
    # instantly even on a phone the site's wall will not talk to.
    try:
        live = woo.list_products(SKU_PREFIX)
        sync_cloud.snapshot_products(live, supplier)
        sync_cloud.report_run(
            stats,
            supplier_products=len(supplier),
            site_products=len(live),
            seconds=elapsed,
            blocked=stats.blocked_skipped + stats.blocked_removed,
        )
        log.info("Reported to the app (%d products mirrored)", len(live))
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not report to the app: %s", exc)

    # A handful of product-level failures is normal on shared hosting and
    # should not stop the rest of the pipeline. Only fail the run if a large
    # share of products errored, which means something is actually wrong.
    if stats.errors and stats.errors > max(5, len(supplier) * 0.10):
        log.error("Too many failures (%d) — flagging this run as failed",
                  stats.errors)
        return 1
    if stats.errors:
        log.warning("%d product(s) failed; they will be retried next run",
                    stats.errors)
    return 0


def wipe_products(woo: Woo, everything: bool, dry_run: bool) -> tuple[int, int]:
    """Delete products. everything=True clears the whole shop."""
    targets = woo.list_products("" if everything else SKU_PREFIX)

    if not targets:
        print("  nothing to delete")
        return 0, 0

    deleted = failed = 0
    for i, product in enumerate(targets, start=1):
        try:
            if not dry_run:
                woo.call("DELETE", f"/products/{product['id']}", params={"force": True})
            deleted += 1
            if i % 20 == 0 or i == len(targets):
                print(f"  deleted {i}/{len(targets)}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [{i}/{len(targets)}] FAILED: {exc}")
        time.sleep(0.15)

    return deleted, failed


def cmd_wipe(args) -> int:
    require_keys(args)
    woo = Woo(args.wc_url, args.wc_key, args.wc_secret)

    scope = "EVERY product" if args.all else f"products with SKU prefix '{SKU_PREFIX}'"
    count = len(woo.list_products("" if args.all else SKU_PREFIX))

    print(f"About to permanently delete {count} items ({scope}) from {args.wc_url}.")
    print("Images stay in the media library. Orders are not affected.")

    if not args.yes:
        if input("Type DELETE to confirm: ").strip() != "DELETE":
            print("Cancelled.")
            return 0

    deleted, failed = wipe_products(woo, args.all, args.dry_run)
    print(f"\nDeleted {deleted}, failed {failed}.")
    return 1 if failed else 0


def cmd_first_run(args) -> int:
    """Clear the shop, then load the full supplier catalogue."""
    require_keys(args)

    print("=" * 68)
    print("FIRST RUN")
    print("=" * 68)
    print(f"  1. delete every existing product on {args.wc_url}")
    print(f"  2. upload the full catalogue from {args.supplier}")
    print(f"     at supplier price + {args.margin:.0f}")
    print()
    print("  This uploads hundreds of images and can take 30-90 minutes.")
    print("  Leave the Terminal window open until it finishes.")
    print("  After this, use 'sync' — it only touches what changed.")
    print()

    if not args.yes and not args.dry_run:
        if input("Type YES to begin: ").strip() != "YES":
            print("Cancelled.")
            return 0

    woo = Woo(args.wc_url, args.wc_key, args.wc_secret)

    print("\nStep 1 of 2 — clearing existing products")
    deleted, failed = wipe_products(woo, everything=True, dry_run=args.dry_run)
    print(f"  removed {deleted} ({failed} failed)")

    print("\nStep 2 of 2 — uploading supplier catalogue")
    supplier = load_supplier(args)
    if not supplier:
        log.error("Supplier feed empty — stopping.")
        return 1
    if args.limit:
        supplier = supplier[:args.limit]

    log.info("Uploading %d products (%d variants)",
             len(supplier), sum(len(p.variants) for p in supplier))

    started = time.time()
    stats = Syncer(woo, args).run(supplier)

    print()
    log.info("Finished in %.0f minutes — %s",
             (time.time() - started) / 60, stats.summary())
    print("\nDone. From now on just run:  python visions_sync.py sync")
    return 1 if stats.errors else 0


# ==========================================================================
# CLI
# ==========================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--supplier", default=SUPPLIER_URL)
        sp.add_argument("--collection", default=SUPPLIER_COLLECTION,
                        help="Collection handle; '' for the whole catalogue")
        sp.add_argument("--margin", type=float, default=MARGIN,
                        help="Added to every supplier price")
        sp.add_argument("--round-to", type=int, default=0,
                        help="Round final price up to a multiple of this")
        sp.add_argument("--keep-html", action="store_true",
                        help="Keep the supplier's HTML description as-is")
        sp.add_argument("--wc-url", default=os.getenv("WC_URL") or WC_URL)
        sp.add_argument("--wc-key", default=os.getenv("WC_KEY") or WC_KEY)
        sp.add_argument("--wc-secret", default=os.getenv("WC_SECRET") or WC_SECRET)
        sp.add_argument("--attribute-name", default="",
                        help="Use this WooCommerce attribute name instead of the "
                             "supplier's, e.g. 'Sizes' to match an existing "
                             "attribute that already has swatches configured")
        sp.add_argument("--skip-sold-out", action="store_true",
                        help="Ignore products where every size is out of stock")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("-v", "--verbose", action="store_true")

    t = sub.add_parser("test", help="Check both ends, change nothing")
    common(t)

    def sync_flags(sp):
        sp.add_argument("--limit", type=int, default=0, help="Only process N products")
        sp.add_argument("--throttle", type=float, default=0.35,
                        help="Pause between products, seconds")
        sp.add_argument("--workers", type=int, default=1,
                        help="Parallel reads. 1 is safest on shared hosting.")
        sp.add_argument("--read-pause", type=float, default=0.25,
                        help="Pause between variation reads, seconds")
        sp.add_argument("--max-images", type=int, default=6,
                        help="Images per product on first creation")
        sp.add_argument("--variant-images", action="store_true",
                        help="Also attach per-size images")
        sp.add_argument("--local-attributes", action="store_true",
                        help="Per-product attributes instead of a global Size "
                             "attribute (global is default; needed for size filters)")
        sp.add_argument("--sync-titles", action="store_true",
                        help="Overwrite product titles you may have edited")
        sp.add_argument("--prune-sizes", action="store_true",
                        help="Delete removed sizes instead of marking out of stock")
        sp.add_argument("--delete-missing", action="store_true",
                        help="Delete vanished products instead of drafting them")

    s = sub.add_parser("sync", help="Reconcile WooCommerce against the supplier")
    common(s)
    sync_flags(s)

    f = sub.add_parser("first-run",
                       help="Clear the shop, then upload the whole catalogue")
    common(f)
    sync_flags(f)
    f.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    w = sub.add_parser("wipe", help="Delete products")
    common(w)
    w.add_argument("--all", action="store_true",
                   help="Delete EVERY product, not just synced ones")
    w.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    return p


def main() -> None:
    args = build_parser().parse_args()
    setup_logging(getattr(args, "verbose", False))

    handlers = {"test": cmd_test, "sync": cmd_sync,
                "first-run": cmd_first_run, "wipe": cmd_wipe}
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
