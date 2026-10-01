"""
Pre-booking filter.

MS Retro lists some jerseys as pre-book / pre-order items: the jersey is not
in hand, the customer pays and waits. We do not sell those. This module is the
one place that decides what counts as a pre-book product, so the sync, the
review page and the photo pipeline all agree.

Anything matching is:
  * never created on the website,
  * never added to the dashboard's Review list,
  * removed from the website and from the app's catalogue if it is already there.
"""

from __future__ import annotations

import re

# "book" on its own is too common (Facebook, booklet), so every pattern needs
# the pre-booking sense spelled out.
PATTERNS = [
    r"pre[\s\-_.]*book(?:ing|ings|ed|s)?",      # pre-book, pre book, prebooking
    r"pre[\s\-_.]*order(?:ing|ed|s)?",          # pre-order, preorder
    r"\bbooking?s?\s+(?:open|start|starts|started|now|only|closed)",
    r"\bopen\s+for\s+bookings?\b",
    r"\bbookings?\s+(?:jersey|kit|product|item)s?\b",
    r"\badvance\s+book(?:ing|ings|ed)?\b",
    r"\bbook\s+(?:now|your|yours)\b",
    r"\bon\s+bookings?\b",
    r"\bbooking\s+amount\b",
    r"\bpre[\s\-_.]*sale\b",
    r"\bmade\s+to\s+order\b",
]

RE = re.compile("|".join(PATTERNS), re.I)


def match(*texts) -> str:
    """Return the matched phrase if any text looks like a pre-book listing."""
    for text in texts:
        if not text:
            continue
        if isinstance(text, (list, tuple, set)):
            found = match(*[str(t) for t in text])
            if found:
                return found
            continue
        found = RE.search(str(text))
        if found:
            return found.group(0).strip()
    return ""


def supplier_match(product) -> str:
    """Check a SupplierProduct from the feed."""
    return match(
        getattr(product, "name", ""),
        getattr(product, "category", ""),
        getattr(product, "sku", ""),
        getattr(product, "tags", []) or [],
        (getattr(product, "description", "") or "")[:600],
    )


def woo_match(product: dict) -> str:
    """Check a WooCommerce product dict from the REST API."""
    if not isinstance(product, dict):
        return ""
    return match(
        product.get("name", ""),
        product.get("slug", ""),
        product.get("sku", ""),
        [c.get("name", "") for c in (product.get("categories") or []) if isinstance(c, dict)],
        [t.get("name", "") for t in (product.get("tags") or []) if isinstance(t, dict)],
        (product.get("short_description") or "")[:600],
        (product.get("description") or "")[:600],
    )


def row_match(row: dict) -> str:
    """Check a mirrored row (app catalogue / review list)."""
    if not isinstance(row, dict):
        return ""
    return match(row.get("name", ""), row.get("sku", ""), row.get("slug", ""))
