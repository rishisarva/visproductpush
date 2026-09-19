#!/usr/bin/env python3
"""
image_cdn.py — mirror product photos into Supabase Storage as pre-sized WebP.

What it does
------------
Photos currently travel: WordPress -> images.weserv.nl -> browser. The proxy
adds a hop and resizes on every request, which is slow on a cold cache — the
exact condition PageSpeed measures. This copies each photo once, already resized
and already WebP, into your own bucket. After that: Supabase CDN -> browser.

Safe to run on a schedule
-------------------------
Every photo's filename contains a short hash of its source URL:

    cdn/22440-0-a1b2c3d4-lg.webp
        ^id  ^n ^hash    ^size

So a re-run is cheap and correct:

  * jersey unchanged      -> filename identical, already in the bucket, skipped
  * supplier swaps a photo-> URL differs -> new hash -> new file uploaded, and
                             the old one is deleted on the same run
  * new jersey            -> uploaded
  * jersey removed        -> its files are deleted

A run with nothing new takes seconds and uploads nothing.

Setup (once)
------------
    pip install requests pillow

Supabase -> Storage -> bucket `product-images`, Public ON.

Run
---
    export SUPABASE_URL="https://amgiihalfdhogzjrxinu.supabase.co"
    export SUPABASE_KEY="eyJ..."             # service_role — same value as the GitHub secret
    python3 image_cdn.py

Options
-------
    --first-only   mirror only the front photo (8 MB instead of ~33 MB)
    --no-prune     never delete anything, even files nothing points at
    --dry-run      show what would happen, change nothing
"""

import hashlib
import io
import json
import os
import sys
import time

import requests
from PIL import Image

SUPA = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = "product-images"
PREFIX = "cdn"

# 840 covers the product hero (420 CSS px at 2x); 420 covers grid cards.
SIZES = {"lg": 840, "sm": 420}
QUALITY = 82
MAX_IMAGES = 4          # per product, when not --first-only

FIRST_ONLY = "--first-only" in sys.argv
NO_PRUNE = "--no-prune" in sys.argv
DRY = "--dry-run" in sys.argv

if not SUPA or not KEY:
    sys.exit("Set SUPABASE_URL and SUPABASE_KEY first (the same secrets sync.yml already uses).")

S = requests.Session()
S.headers.update({"User-Agent": "visions-jersey-image-cdn/2.0"})
AUTH = {"Authorization": f"Bearer {KEY}"}


def short_hash(url: str) -> str:
    """Identifies the source photo. A different URL means a different file."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def products():
    """The live shop_products table — the same source the plugin builds the
    snapshot from, so a jersey added minutes ago is already here."""
    r = S.get(f"{SUPA}/rest/v1/shop_products",
              params={"select": "id,slug,name,thumb,images,in_stock", "limit": "2000"},
              headers={**AUTH, "apikey": KEY}, timeout=30)
    r.raise_for_status()
    return r.json()


def existing():
    have, offset = set(), 0
    while True:
        r = S.post(f"{SUPA}/storage/v1/object/list/{BUCKET}",
                   headers={**AUTH, "Content-Type": "application/json"},
                   json={"prefix": PREFIX + "/", "limit": 1000, "offset": offset},
                   timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        have.update(PREFIX + "/" + o["name"] for o in batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return have


def upload(path, data):
    if DRY:
        return
    r = S.post(f"{SUPA}/storage/v1/object/{BUCKET}/{path}",
               headers={**AUTH, "Content-Type": "image/webp", "x-upsert": "true",
                        # A year: the hash in the filename changes whenever the
                        # photo does, so a cached copy can never go stale.
                        "cache-control": "public, max-age=31536000, immutable"},
               data=data, timeout=60)
    r.raise_for_status()


def remove(paths):
    if DRY or not paths:
        return
    for i in range(0, len(paths), 100):
        S.delete(f"{SUPA}/storage/v1/object/{BUCKET}",
                 headers={**AUTH, "Content-Type": "application/json"},
                 json={"prefixes": paths[i:i + 100]}, timeout=30)


def convert(raw, width):
    im = Image.open(io.BytesIO(raw))
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "WEBP", quality=QUALITY, method=5)
    return out.getvalue()


def main():
    items = products()
    have = existing()
    print(f"{len(items)} products · {len(have)} files already in the bucket"
          + (" · DRY RUN" if DRY else ""))

    manifest, wanted = {}, set()
    made = skipped = failed = 0

    for p in items:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue

        urls, seen = [], set()
        for u in [p.get("thumb")] + list(p.get("images") or []):
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        urls = urls[:1] if FIRST_ONLY else urls[:MAX_IMAGES]

        entry = {}
        for idx, src in enumerate(urls):
            h = short_hash(src)
            names = {k: f"{PREFIX}/{pid}-{idx}-{h}-{k}.webp" for k in SIZES}
            wanted.update(names.values())

            if all(n in have for n in names.values()):
                entry[str(idx)] = h
                skipped += 1
                continue
            try:
                raw = S.get(src, timeout=45).content
                for k, w in SIZES.items():
                    upload(names[k], convert(raw, w))
                entry[str(idx)] = h
                made += 1
                print(f"  new   {pid}-{idx}  {p.get('name', '')[:44]}")
            except Exception as e:
                failed += 1
                print(f"  FAIL  {pid}-{idx}  {type(e).__name__}: {e}")
            time.sleep(0.05)

        if entry:
            manifest[pid] = entry

    # Anything the manifest no longer points at: an old photo the supplier
    # replaced, or a jersey that's gone. Left alone the bucket would only grow.
    stale = sorted(have - wanted)
    if stale and not NO_PRUNE:
        print(f"\nremoving {len(stale)} file(s) nothing points at")
        remove(stale)

    body = json.dumps(manifest, separators=(",", ":")).encode()
    if not DRY:
        r = S.post(f"{SUPA}/storage/v1/object/feeds/image-cdn.json",
                   headers={**AUTH, "Content-Type": "application/json", "x-upsert": "true",
                            "cache-control": "public, max-age=120"},
                   data=body, timeout=30)
        r.raise_for_status()

    print(f"\nuploaded {made} · unchanged {skipped} · failed {failed} · pruned {0 if NO_PRUNE else len(stale)}")
    print(f"manifest: {len(manifest)} products, {len(body)} bytes")
    if made == 0 and not stale:
        print("nothing changed — the storefront is already serving every photo from Supabase")


if __name__ == "__main__":
    main()
