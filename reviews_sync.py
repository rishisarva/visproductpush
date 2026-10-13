"""
reviews_sync.py — customer reviews: Supabase (pending) → GitHub repo (kept) → Cloudflare (served)
================================================================================================

A customer sends photos + one caption from shop.visionsjersey.com/review. They
wait in Supabase (table vj_reviews, bucket "reviews") until you tap Approve in
the dashboard (More → Reviews). This script, run by the hourly sync:

  1. copies every approved review's photos into this repo (reviews/…jpg, resized,
     kept forever — your archive) and writes reviews/index.json
  2. deletes the photos from Supabase (keeps it light)
  3. deletes rejected reviews and their photos
  4. writes the photo site files (VJ_HOME/site/r/*.jpg + feeds/reviews.json),
     which vj_images.py then uploads to Cloudflare with the product photos

The shop reads https://vj-images.pages.dev/feeds/reviews.json (newest first,
the site shuffles them). Needs SUPABASE_URL + SUPABASE_KEY (service role).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from datetime import datetime, timezone

try:
    import requests
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("pip install requests pillow")

URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
KEY = os.getenv("SUPABASE_KEY") or ""
HEAD = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
REPO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reviews")
INDEX = os.path.join(REPO_DIR, "index.json")
HOME = os.path.expanduser(os.environ.get("VJ_HOME", "~/vj-images"))
SITE = os.path.join(HOME, "site")
MAX_PX, QUALITY = 1200, 82


def sb(method, path, body=None, prefer=None):
    h = {**HEAD, "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    r = requests.request(method, f"{URL}/rest/v1/{path}", headers=h, json=body, timeout=30)
    if r.status_code >= 400:
        print(f"   supabase {method} {path} -> {r.status_code} {r.text[:150]}")
        return None
    return r.json() if r.text.strip() else []


def storage_get(path):
    r = requests.get(f"{URL}/storage/v1/object/reviews/{path}", headers=HEAD, timeout=60)
    return r.content if r.status_code == 200 else None


def storage_delete(paths):
    if not paths:
        return
    requests.delete(f"{URL}/storage/v1/object/reviews", headers={**HEAD, "Content-Type": "application/json"},
                    json={"prefixes": paths}, timeout=30)


def to_jpeg(raw):
    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((MAX_PX, MAX_PX))
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    return out.getvalue()


def load_index():
    if os.path.exists(INDEX):
        try:
            return json.load(open(INDEX, encoding="utf-8"))
        except ValueError:
            pass
    return []


def save_index(items):
    os.makedirs(REPO_DIR, exist_ok=True)
    items.sort(key=lambda r: r.get("at") or "", reverse=True)
    json.dump(items, open(INDEX, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def publish_approved(items):
    rows = sb("GET", "vj_reviews?select=id,name,caption,order_ref,images,created_at&status=eq.approved&published=eq.false&limit=50") or []
    done = 0
    have = {r["id"] for r in items}
    for r in rows:
        rid = str(r["id"])
        short = rid.replace("-", "")[:10]
        files = []
        for n, path in enumerate(r.get("images") or []):
            raw = storage_get(path)
            if not raw:
                print(f"   {short}: photo missing in storage ({path})")
                continue
            try:
                jpg = to_jpeg(raw)
            except Exception as exc:  # noqa: BLE001
                print(f"   {short}: bad image ({exc})")
                continue
            name = f"{short}-{n + 1}.jpg"
            os.makedirs(REPO_DIR, exist_ok=True)
            open(os.path.join(REPO_DIR, name), "wb").write(jpg)
            files.append(name)
        if not files:
            sb("PATCH", f"vj_reviews?id=eq.{rid}", {"status": "rejected"}, prefer="return=minimal")
            continue
        if rid not in have:
            items.append({"id": rid, "name": (r.get("name") or "")[:60], "caption": (r.get("caption") or "")[:600],
                          "at": r.get("created_at"), "images": files})
            have.add(rid)
        sb("PATCH", f"vj_reviews?id=eq.{rid}", {"published": True, "published_at": datetime.now(timezone.utc).isoformat()}, prefer="return=minimal")
        storage_delete(r.get("images") or [])
        done += 1
        print(f"   published {short}: {len(files)} photo(s) · {(r.get('name') or '')[:30]}")
    return done


def clear_rejected():
    rows = sb("GET", "vj_reviews?select=id,images&status=eq.rejected&limit=100") or []
    for r in rows:
        storage_delete(r.get("images") or [])
        sb("DELETE", f"vj_reviews?id=eq.{r['id']}", prefer="return=minimal")
    return len(rows)


def write_site(items):
    """Photo site files for Cloudflare: /r/<file>.jpg and /feeds/reviews.json."""
    rdir = os.path.join(SITE, "r")
    os.makedirs(rdir, exist_ok=True)
    os.makedirs(os.path.join(SITE, "feeds"), exist_ok=True)
    wanted = set()
    for it in items:
        for f in it["images"]:
            wanted.add(f)
            src, dst = os.path.join(REPO_DIR, f), os.path.join(rdir, f)
            if os.path.exists(src) and (not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src)):
                shutil.copyfile(src, dst)
    for f in os.listdir(rdir):
        if f not in wanted:
            os.remove(os.path.join(rdir, f))
    feed = [{"id": it["id"], "name": it["name"], "caption": it["caption"], "at": it["at"],
             "images": ["/r/" + f for f in it["images"]]} for it in items]
    json.dump(feed, open(os.path.join(SITE, "feeds", "reviews.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def main():
    if not (URL and KEY):
        sys.exit("SUPABASE_URL / SUPABASE_KEY missing")
    items = load_index()
    n = publish_approved(items)
    gone = clear_rejected()
    save_index(items)
    write_site(items)
    print(f"reviews: {n} published this run, {gone} rejected removed, {len(items)} live in total")


if __name__ == "__main__":
    main()
