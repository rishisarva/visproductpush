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

v3 (24 Oct 2026): also writes small WebP copies of every card and photo — r/s/<name>.webp (360 px wide, ~20 KB, the
review strip) and r/m/<name>.webp (720 px, the pop-up) — and lists them in reviews.json (card_s / card_m / images_s /
images_m). The shop shows those straight from Cloudflare instead of sending each full 1080x1920 card (~300 KB)
through the free image proxy, which is what made the review photos slow.

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
    from PIL import Image, ImageDraw, ImageFont, ImageOps
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

# ───────────── v2: one 9:16 review card per review (photos collage + caption) ─────────────
CARD_W, CARD_H = 1080, 1920
PHOTO_H = 1330                      # photo area; the rest is the caption panel
BG, PANEL, INK, MUTED, CREAM = (11, 11, 12), (20, 22, 31), (240, 232, 213), (154, 156, 166), (240, 232, 213)
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/Library/Fonts", "C:/Windows/Fonts"]


def font(size, bold=False):
    names = ["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf"]
    for d in FONT_DIRS:
        for n in names:
            f = os.path.join(d, n)
            if os.path.exists(f):
                return ImageFont.truetype(f, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def cover(im, w, h, radius=28):
    """Photo cropped to fill w×h, rounded corners."""
    im = ImageOps.exif_transpose(im).convert("RGB")
    im = ImageOps.fit(im, (w, h), Image.LANCZOS, centering=(0.5, 0.4))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    out = Image.new("RGB", (w, h), BG)
    out.paste(im, (0, 0), mask)
    return out


_EMOJI = None


def clean_text(t):
    """Drop emoji / pictographs (the card font has no glyphs for them) and tidy spaces."""
    global _EMOJI
    import re
    if _EMOJI is None:
        _EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
    t = _EMOJI.sub("", t or "")
    return re.sub(r"[ \t]+", " ", t).strip()


def wrap(draw, text, fnt, width):
    lines, cur = [], ""
    for word in text.replace("\n", " \n ").split(" "):
        if word == "\n":
            lines.append(cur); cur = ""; continue
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= width or not cur:
            cur = trial
        else:
            lines.append(cur); cur = word
    if cur:
        lines.append(cur)
    return lines


def make_card(photos, name, caption):
    """photos: list of PIL images (1-4 used). → JPEG bytes, 1080×1920."""
    card = Image.new("RGB", (CARD_W, CARD_H), BG)
    pics = photos[:4]
    g, pad = 10, 24                                   # gap between photos, outer padding
    W, H = CARD_W - 2 * pad, PHOTO_H - pad
    x0, y0 = pad, pad
    if len(pics) == 1:
        card.paste(cover(pics[0], W, H), (x0, y0))
    elif len(pics) == 2:
        w = (W - g) // 2
        card.paste(cover(pics[0], w, H), (x0, y0)); card.paste(cover(pics[1], w, H), (x0 + w + g, y0))
    elif len(pics) == 3:
        wl = int(W * 0.62); wr = W - wl - g; hh = (H - g) // 2
        card.paste(cover(pics[0], wl, H), (x0, y0))
        card.paste(cover(pics[1], wr, hh), (x0 + wl + g, y0)); card.paste(cover(pics[2], wr, hh), (x0 + wl + g, y0 + hh + g))
    else:
        w = (W - g) // 2; hh = (H - g) // 2
        for i, im in enumerate(pics):
            card.paste(cover(im, w, hh), (x0 + (i % 2) * (w + g), y0 + (i // 2) * (hh + g)))
    # watermark: small "Visions Jersey" in the middle of the photos, low opacity
    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    wm_font = font(58, True)
    wm = "Visions Jersey"
    tw = ld.textlength(wm, font=wm_font)
    wx, wy = (CARD_W - tw) / 2, PHOTO_H / 2 - 30
    ld.text((wx + 2, wy + 2), wm, font=wm_font, fill=(0, 0, 0, 70))          # soft shadow so it reads on light photos
    ld.text((wx, wy), wm, font=wm_font, fill=(255, 255, 255, 80))           # ~30% white
    card = Image.alpha_composite(card.convert("RGBA"), layer).convert("RGB")
    # caption panel
    d = ImageDraw.Draw(card)
    top = PHOTO_H + 10
    d.rounded_rectangle((pad, top, CARD_W - pad, CARD_H - pad), radius=32, fill=PANEL)
    y = top + 44
    d.text((pad + 44, y), "★★★★★", font=font(40, True), fill=CREAM); y += 70
    cap = clean_text(caption)
    f_cap = font(44); lines = wrap(d, "\u201c" + cap + "\u201d" if cap else "", f_cap, CARD_W - 2 * pad - 88)
    max_lines = 7
    if len(lines) > max_lines:
        lines = lines[:max_lines]; lines[-1] = lines[-1].rstrip(" \u201d.,") + "\u2026\u201d"
    for ln in lines:
        d.text((pad + 44, y), ln, font=f_cap, fill=INK); y += 58
    y += 18
    who = clean_text(name).split(" ")[0] if clean_text(name) else ""
    if who:
        d.text((pad + 44, y), "\u2014 " + who, font=font(40, True), fill=CREAM); y += 60
    d.text((pad + 44, CARD_H - pad - 70), "Verified buyer  \u00b7  shop.visionsjersey.com", font=font(28), fill=MUTED)
    out = io.BytesIO()
    card.save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return out.getvalue()


def ensure_card(item):
    """Build <short>-card.jpg for a review from its photos in the repo (once; or when missing)."""
    short = item["id"].replace("-", "")[:10]
    name = f"{short}-card.jpg"
    path = os.path.join(REPO_DIR, name)
    if item.get("card") == name and os.path.exists(path):
        return False
    pics = []
    for f in item.get("images") or []:
        fp = os.path.join(REPO_DIR, f)
        if os.path.exists(fp):
            try:
                pics.append(Image.open(fp))
            except Exception:  # noqa: BLE001
                pass
    if not pics:
        return False
    open(path, "wb").write(make_card(pics, item.get("name") or "", item.get("caption") or ""))
    item["card"] = name
    return True


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
            item = {"id": rid, "name": (r.get("name") or "")[:60], "caption": (r.get("caption") or "")[:600],
                    "at": r.get("created_at"), "images": files}
            items.append(item)
            have.add(rid)
            ensure_card(item)
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


SMALL = {"s": (360, 70), "m": (720, 78)}     # v3: folder -> (width, WebP quality)


def small_name(f):
    return os.path.splitext(f)[0] + ".webp"


def make_small(src, folder, f):
    """r/<folder>/<name>.webp from the repo photo; made once (the photo site folder is kept between runs)."""
    w, q = SMALL[folder]
    dst = os.path.join(SITE, "r", folder, small_name(f))
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return True
    try:
        im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
        if im.width > w:
            im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im.save(dst, "WEBP", quality=q, method=6)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"   small copy of {f} not made ({exc})")
        return False


def write_site(items):
    """Photo site files for Cloudflare: /r/<file>.jpg, /r/s|m/<file>.webp and /feeds/reviews.json."""
    rdir = os.path.join(SITE, "r")
    os.makedirs(rdir, exist_ok=True)
    os.makedirs(os.path.join(SITE, "feeds"), exist_ok=True)
    wanted, small_ok = set(), set()
    for it in items:
        for f in list(it["images"]) + ([it["card"]] if it.get("card") else []):
            wanted.add(f)
            src, dst = os.path.join(REPO_DIR, f), os.path.join(rdir, f)
            if os.path.exists(src) and (not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src)):
                shutil.copyfile(src, dst)
            if os.path.exists(src) and all(make_small(src, d, f) for d in SMALL):
                small_ok.add(f)
    for f in os.listdir(rdir):
        if os.path.isfile(os.path.join(rdir, f)) and f not in wanted:
            os.remove(os.path.join(rdir, f))
    keep_small = {small_name(f) for f in small_ok}
    for d in SMALL:
        sd = os.path.join(rdir, d)
        for f in (os.listdir(sd) if os.path.isdir(sd) else []):
            if f not in keep_small:
                os.remove(os.path.join(sd, f))

    def sm(f, d):
        return f"/r/{d}/{small_name(f)}" if f in small_ok else None

    feed = [{"id": it["id"], "name": it["name"], "caption": it["caption"], "at": it["at"],
             "card": ("/r/" + it["card"]) if it.get("card") else None,
             "card_s": sm(it["card"], "s") if it.get("card") else None,
             "card_m": sm(it["card"], "m") if it.get("card") else None,
             "images": ["/r/" + f for f in it["images"]],
             "images_s": [sm(f, "s") for f in it["images"]],
             "images_m": [sm(f, "m") for f in it["images"]]} for it in items]
    json.dump(feed, open(os.path.join(SITE, "feeds", "reviews.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def main():
    if not (URL and KEY):
        sys.exit("SUPABASE_URL / SUPABASE_KEY missing")
    items = load_index()
    n = publish_approved(items)
    made = sum(1 for it in items if ensure_card(it))        # cards for reviews published before v2
    if made:
        print(f"   built {made} review card(s) for earlier reviews")
    gone = clear_rejected()
    save_index(items)
    write_site(items)
    print(f"reviews: {n} published this run, {gone} rejected removed, {len(items)} live in total")


if __name__ == "__main__":
    main()
