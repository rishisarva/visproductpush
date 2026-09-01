#!/usr/bin/env python3
"""
brand_clean.py
==============

Removes supplier branding from the hanger clip in product photos.

The clip appears at a different place and size in every photo — full shots,
close-ups, back shots — so this does not assume a fixed position. It finds
the clip in each image by looking for the pale horizontal bar near the top,
then looks for dark text sitting on it.

Nothing is uploaded until you have looked at it.

    pip install requests pillow

Workflow
--------
    # 1. Download every image, detect branding, write before/after crops
    #    to ./review/ . Nothing touches your site.
    python brand_clean.py review

    # 2. Open ./review/index.html in a browser. Look at each pair.
    #    Note any product numbers that look wrong.

    # 3. Upload the cleaned images (optionally skipping bad ones)
    python brand_clean.py apply
    python brand_clean.py apply --skip 14,27

Needs a WordPress Application Password as well as the WooCommerce keys:
    wp-admin > Users > Profile > Application Passwords
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import time

try:
    import requests
    from PIL import Image, ImageFilter
except ImportError:
    sys.exit("Missing dependencies. Run:  pip install requests pillow")


# ------------------------------------------------------------------ #
#  EDIT THESE, THEN SAVE                                              #
# ------------------------------------------------------------------ #

WC_URL = "https://visionsjersey.com"
WC_KEY = "ck_paste_your_key_here"
WC_SECRET = "cs_paste_your_secret_here"

WP_USER = "user"
WP_APP_PASSWORD = "xxxx xxxx xxxx xxxx xxxx xxxx"

# ------------------------------------------------------------------ #

SKU_PREFIX = "TS-"
REVIEW_DIR = "review"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ==========================================================================
# Detection
# ==========================================================================

def find_clip(img: Image.Image):
    """
    Find the hanger clip bar and any dark text on it.

    Returns (band, textbox, clip_mean) in pixel coordinates, where band is
    (y0, y1) and textbox is (x0, y0, x1, y1) or None.

    Scale-independent: it searches for the clip rather than assuming where
    it is, so it works on close-ups and wide shots alike.
    """
    g = img.convert("L")
    w, h = g.size
    cx0, cx1 = int(w * 0.25), int(w * 0.75)
    step = max(1, (cx1 - cx0) // 140)

    rows = []
    for y in range(int(h * 0.01), int(h * 0.55)):
        vals = [g.getpixel((x, y)) for x in range(cx0, cx1, step)]
        rows.append((y, statistics.mean(vals)))
    if len(rows) < 10:
        return None, None, 0.0

    # the wall is the bright, flat area at the very top
    wall = statistics.median([m for _, m in rows[:max(6, len(rows) // 10)]])

    # the clip is a sustained band moderately darker than the wall.
    # the garment is much darker, so an upper bound separates them.
    best = None
    run: list[int] = []
    min_thickness = max(4, int(h * 0.006))
    for y, m in rows:
        drop = wall - m
        if 5 < drop < 55:
            run.append(y)
        else:
            if len(run) >= min_thickness:
                best = (run[0], run[-1])
                break
            run = []
    if best is None and len(run) >= min_thickness:
        best = (run[0], run[-1])
    if best is None:
        return None, None, wall

    by0, by1 = best
    by1 = min(by1, by0 + int(h * 0.14))          # a clip is thin

    vals = [g.getpixel((x, y)) for y in range(by0, by1 + 1)
            for x in range(cx0, cx1, step)]
    clip_mean = statistics.mean(vals)
    cut = clip_mean - 65

    # column and row density, so stray dark pixels don't inflate the box
    colcount = {}
    for x in range(cx0, cx1):
        colcount[x] = sum(1 for y in range(by0, by1 + 1)
                          if g.getpixel((x, y)) < cut)
    if not colcount or max(colcount.values()) == 0:
        return (by0, by1), None, clip_mean

    peak = max(colcount.values())
    if peak < max(2, (by1 - by0) * 0.12):
        return (by0, by1), None, clip_mean

    keep = [x for x, n in colcount.items() if n >= peak * 0.30]
    if len(keep) < max(6, (cx1 - cx0) * 0.04):
        return (by0, by1), None, clip_mean

    tx0, tx1 = min(keep), max(keep) + 1

    rowcount = [(y, sum(1 for x in range(tx0, tx1) if g.getpixel((x, y)) < cut))
                for y in range(by0, by1 + 1)]
    rpeak = max((n for _, n in rowcount), default=0)
    if rpeak == 0:
        return (by0, by1), None, clip_mean

    # Take only the FIRST cluster of dark rows. Anything further down is the
    # garment's collar, which is also dark but must not be touched. The two
    # are separated by a gap of clean clip rows.
    floor = max(1, rpeak * 0.20)
    ty0 = ty1 = None
    gap = 0
    for y, n in rowcount:
        if n >= floor:
            if ty0 is None:
                ty0 = y
            ty1 = y + 1
            gap = 0
        elif ty0 is not None:
            gap += 1
            if gap >= 2:
                break

    if ty0 is None:
        return (by0, by1), None, clip_mean

    # recompute the horizontal extent using only the text rows
    colcount2 = {x: sum(1 for y in range(ty0, ty1) if g.getpixel((x, y)) < cut)
                 for x in range(cx0, cx1)}
    peak2 = max(colcount2.values(), default=0)
    if peak2:
        keep2 = [x for x, n in colcount2.items() if n >= peak2 * 0.30]
        if len(keep2) >= 6:
            tx0, tx1 = min(keep2), max(keep2) + 1

    # Return whatever mark we found. Whether it is really supplier branding
    # on a wooden clip is decided in looks_like_logo, which reports its
    # reasoning — deciding it here would hide why an image was rejected.
    return (by0, by1), (tx0, ty0, tx1, ty1), clip_mean


def _sits_on_clip(img: Image.Image, box, band, explain: bool = False):
    """
    True only if this really is the hanger clip.

    White shirts are pale and unsaturated exactly like the wooden clip, so
    brightness cannot separate them. Two things can:
      - wood is warm, its red channel clearly above blue; fabric is neutral
      - a clip has plain wall above it; a player's name has more shirt above it
    """
    rgb = img.convert("RGB")
    g = img.convert("L")
    w, h = g.size
    x0, y0, x1, y1 = box
    by0 = band[0]

    def no(msg):
        return (False, msg) if explain else False

    def yes():
        return (True, "ok") if explain else True

    if y0 > h * 0.45:            # a clip hangs near the top of the frame
        return no(f"too low in frame (y {y0/h:.2f})")

    pad = max(3, (y1 - y0))
    stride = max(1, (x1 - x0) // 40)

    def sample(ya, yb, xa, xb, xs):
        pts = [(x, y) for y in range(max(0, ya), min(h, yb))
               for x in range(max(0, xa), min(w, xb), xs)]
        if len(pts) < 6:
            return None
        lum = [g.getpixel(q) for q in pts]
        px = [rgb.getpixel(q) for q in pts]
        r = statistics.mean(q[0] for q in px)
        gg = statistics.mean(q[1] for q in px)
        b = statistics.mean(q[2] for q in px)
        mx, mn = max(r, gg, b), min(r, gg, b)
        return {"lum": statistics.mean(lum), "sd": statistics.pstdev(lum),
                "warm": r - b, "sat": 0 if mx == 0 else (mx - mn) / mx}

    above = sample(y0 - pad, y0, x0, x1, stride)
    below = sample(y1, y1 + pad, x0, x1, stride)
    if not above or not below:
        return no("not enough surrounding pixels")

    for label, srf in (("above", above), ("below", below)):
        if srf["lum"] < 100:
            return no(f"{label} too dark (lum {srf['lum']:.0f})")
        if srf["sd"] > 45:
            return no(f"{label} too uneven (sd {srf['sd']:.0f})")
        if srf["sat"] > 0.30:
            return no(f"{label} too colourful (sat {srf['sat']:.2f})")
    if abs(above["lum"] - below["lum"]) > 55:
        return no(f"uneven around mark ({abs(above['lum']-below['lum']):.0f})")

    # the material must be warm wood, not white fabric
    warm = (above["warm"] + below["warm"]) / 2
    sat = (above["sat"] + below["sat"]) / 2
    if warm < 14:
        return no(f"not warm enough for wood (R-B {warm:.0f})")
    if sat < 0.055:
        return no(f"too neutral for wood (sat {sat:.3f})")

    # and there must be plain wall above the clip
    wall = sample(by0 - int(h * 0.06) - 4, by0 - 2,
                  int(w * 0.30), int(w * 0.70), max(1, int(w * 0.01)))
    if not wall:
        return no("no wall sample above")
    if wall["lum"] < 130:
        return no(f"nothing wall-like above (lum {wall['lum']:.0f})")
    if wall["warm"] > warm - 4:
        return no(f"above is as warm as the clip ({wall['warm']:.0f} vs {warm:.0f})")

    return yes()


def looks_like_logo(img: Image.Image, band, box, clip_mean: float,
                    strict: float = 1.0) -> tuple[bool, str]:
    """
    Decide whether a detected mark is really the supplier logo.

    Returns (ok, reason). `strict` scales the thresholds: above 1.0 is
    fussier and catches less, below 1.0 is looser and catches more.

    A missed logo costs two minutes to fix by hand. A wrongly edited photo
    goes live looking damaged, so this errs towards rejecting.
    """
    g = img.convert("L")
    w, h = g.size
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return False, "empty box"

    ratio = bw / bh
    dark = sum(1 for y in range(y0, y1) for x in range(x0, x1)
               if g.getpixel((x, y)) < clip_mean - 65)
    ink = 100.0 * dark / (bw * bh)
    note = f"logo (w/h {ratio:.1f}, ink {ink:.0f}%)"

    # a wordmark is a wide, thin strip
    if ratio < 2.0 * strict:
        return False, f"too square ({ratio:.1f})"
    if bw > w * 0.55:
        return False, "too wide for a logo"
    if bh > h * 0.10:
        return False, "too tall for a logo"

    # it has real ink in it, but is not a solid dark block
    if ink < 8:
        return False, f"too faint ({ink:.0f}%)"
    if ink > 75 / max(0.5, strict):
        return False, f"solid block ({ink:.0f}%)"

    verdict = _sits_on_clip(img, box, band, explain=True)
    ok_clip, why = verdict if isinstance(verdict, tuple) else (verdict, "")
    if not ok_clip:
        return False, f"rejected: {why}"

    return True, note


def clean(img: Image.Image, textbox) -> Image.Image:
    """
    Rebuild the text area from the clip pixels directly above and below it.
    The clip is a smooth bar, so the rebuilt strip blends in rather than
    reading as a blur patch or a coloured rectangle.
    """
    out = img.convert("RGB").copy()
    x0, y0, x1, y1 = textbox
    w, h = out.size

    # The detector finds the dense core of the text; letter tops and tails
    # sit just outside it, so grow the box a little before rebuilding.
    grow_y = max(2, int((y1 - y0) * 0.55))
    grow_x = max(2, int((x1 - x0) * 0.03))
    x0 = max(0, x0 - grow_x); x1 = min(w, x1 + grow_x)
    y0 = max(1, y0 - grow_y); y1 = min(h - 1, y1 + grow_y)

    pad = max(2, (y1 - y0) // 3)

    src = img.convert("RGB").load()
    dst = out.load()
    span = max(1, y1 - y0)
    top_from = max(0, y0 - pad)
    bot_to = min(h, y1 + pad)

    for x in range(x0, x1):
        above = [src[x, y] for y in range(top_from, y0)] or [src[x, y0]]
        below = [src[x, y] for y in range(y1, bot_to)] or [src[x, y1 - 1]]
        ca = tuple(sum(c[i] for c in above) // len(above) for i in range(3))
        cb = tuple(sum(c[i] for c in below) // len(below) for i in range(3))
        for y in range(y0, y1):
            t = (y - y0) / span
            dst[x, y] = tuple(int(ca[i] * (1 - t) + cb[i] * t) for i in range(3))

    f = 4
    fx0, fy0 = max(0, x0 - f), max(0, y0 - f)
    fx1, fy1 = min(w, x1 + f), min(h, y1 + f)
    band = out.crop((fx0, fy0, fx1, fy1)).filter(ImageFilter.GaussianBlur(1.1))
    out.paste(band, (fx0, fy0))
    return out


def crop_around(img: Image.Image, box, margin: float = 1.6) -> Image.Image:
    """Cut a viewable region around the clip for the review sheet."""
    w, h = img.size
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0, y1 - y0
    mx, my = int(cw * margin), int(ch * margin * 2.2)
    return img.crop((max(0, x0 - mx), max(0, y0 - my),
                     min(w, x1 + mx), min(h, y1 + my)))


# ==========================================================================
# Site
# ==========================================================================

class Site:
    def __init__(self, args):
        self.root = args.wc_url.rstrip("/")
        self.wc = requests.Session()
        self.wc.auth = (args.wc_key, args.wc_secret)
        self.wc.headers.update({"User-Agent": UA})
        self.wp = requests.Session()
        self.wp.auth = (args.wp_user, args.wp_password)
        self.wp.headers.update({"User-Agent": UA})

    def products(self) -> list[dict]:
        out: list[dict] = []
        for page in range(1, 101):
            r = self.wc.get(f"{self.root}/wp-json/wc/v3/products",
                            params={"per_page": 100, "page": page,
                                    "status": "any"}, timeout=60)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
        return [p for p in out if (p.get("sku") or "").startswith(SKU_PREFIX)]

    def download(self, url: str, attempts: int = 5) -> Image.Image:
        """
        Fetch an image, backing off when the host throttles us.

        Shared hosting rate-limits plain file requests as well as the API,
        and a burst of image downloads trips it within seconds. Waiting is
        far cheaper than losing the image.
        """
        last = None
        for attempt in range(attempts):
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            except requests.RequestException as exc:
                last = exc
                time.sleep(3 * (attempt + 1))
                continue

            if r.status_code == 429 or r.status_code in (502, 503, 504):
                wait = 5 * (attempt + 1)
                try:
                    wait = max(wait, int(r.headers.get("Retry-After", 0)))
                except ValueError:
                    pass
                if attempt < attempts - 1:
                    time.sleep(wait)
                    continue

            r.raise_for_status()
            return Image.open(io.BytesIO(r.content))

        raise RuntimeError(f"could not fetch image after {attempts} tries: {last}")

    def check_media(self) -> None:
        r = self.wp.get(f"{self.root}/wp-json/wp/v2/media",
                        params={"per_page": 1}, timeout=30)
        if r.status_code == 401:
            sys.exit("\nWordPress rejected the Application Password.\n"
                     "Check WP_USER is your login name and the password was\n"
                     "copied exactly, spaces included.\n")
        r.raise_for_status()

    def upload(self, data: bytes, filename: str) -> int:
        r = self.wp.post(
            f"{self.root}/wp-json/wp/v2/media",
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "Content-Type": "image/jpeg"},
            data=data, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"upload: {r.status_code} {r.text[:200]}")
        return r.json()["id"]

    def set_images(self, pid: int, images: list[dict]) -> None:
        r = self.wc.put(f"{self.root}/wp-json/wc/v3/products/{pid}",
                        json={"images": images}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"update {pid}: {r.status_code} {r.text[:200]}")


# ==========================================================================
# review
# ==========================================================================

def cmd_review(args) -> int:
    site = Site(args)
    os.makedirs(f"{REVIEW_DIR}/clean", exist_ok=True)
    os.makedirs(f"{REVIEW_DIR}/pairs", exist_ok=True)

    print("Reading products...")
    products = site.products()
    print(f"{len(products)} products\n")

    manifest: list[dict] = []
    hits = 0

    for pi, product in enumerate(products, start=1):
        name = product.get("name", "")
        entry = {"n": pi, "id": product["id"], "name": name,
                 "sku": product.get("sku"), "images": []}

        for ii, image in enumerate(product.get("images", [])):
            src, mid = image.get("src"), image.get("id")
            if not src:
                continue
            rec = {"i": ii, "src": src, "media_id": mid, "branded": False}

            try:
                img = site.download(src)
                band, box, cm = find_clip(img)
            except Exception as exc:  # noqa: BLE001
                rec["error"] = str(exc)
                entry["images"].append(rec)
                continue

            if box:
                ok, reason = looks_like_logo(img, band, box, cm, args.strict)
                rec["reason"] = reason
                if ok:
                    fixed = clean(img, box)
                    tag = f"{pi:03d}_{ii}"
                    fixed.convert("RGB").save(
                        f"{REVIEW_DIR}/clean/{tag}.jpg", quality=93)

                    before = crop_around(img.convert("RGB"), box)
                    after = crop_around(fixed, box)
                    ph = max(before.height, after.height)
                    sheet = Image.new("RGB", (before.width + after.width + 12,
                                              ph), (255, 255, 255))
                    sheet.paste(before, (0, 0))
                    sheet.paste(after, (before.width + 12, 0))
                    scale = min(3.0, max(1.0, 900 / max(1, sheet.width)))
                    sheet = sheet.resize((int(sheet.width * scale),
                                          int(sheet.height * scale)),
                                         Image.LANCZOS)
                    sheet.save(f"{REVIEW_DIR}/pairs/{tag}.jpg", quality=92)

                    rec["branded"] = True
                    rec["box"] = box
                    hits += 1

            entry["images"].append(rec)

        manifest.append(entry)
        if pi % 20 == 0:
            print(f"  ...{pi}/{len(products)}  ({hits} marked so far)")
        time.sleep(args.delay)

    with open(f"{REVIEW_DIR}/manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=1)

    write_html(manifest)

    flagged = [e for e in manifest if any(i["branded"] for i in e["images"])]
    print(f"\nMarked {hits} images across {len(flagged)} products.")
    print(f"Open {REVIEW_DIR}/index.html in your browser.")
    print("Left is the original, right is the cleaned version.")
    print("\nThen:  python brand_clean.py apply")
    return 0


def write_html(manifest: list[dict]) -> None:
    rows = []
    for e in manifest:
        marked = [i for i in e["images"] if i["branded"]]
        if not marked:
            continue
        imgs = "".join(
            f'<div class=p><img src="pairs/{e["n"]:03d}_{i["i"]}.jpg">'
            f'<div class=m>image {i["i"]} &middot; {i.get("reason","")}</div></div>'
            for i in marked)
        rows.append(
            f'<div class=r><h3>#{e["n"]} &middot; {e["name"]}</h3>{imgs}</div>')

    html = """<!doctype html><meta charset=utf-8>
<title>Branding review</title>
<style>
 body{font:14px -apple-system,sans-serif;margin:24px;background:#fafafa}
 h1{font-size:20px} h3{font-size:14px;margin:18px 0 6px;color:#333}
 .r{background:#fff;padding:12px 16px;margin-bottom:14px;border-radius:8px;
    box-shadow:0 1px 3px rgba(0,0,0,.12)}
 .p img{max-width:100%;border:1px solid #ddd;border-radius:4px;margin:4px 0}
 .m{font-size:11px;color:#888;margin-bottom:8px}
 .note{background:#fff8e1;border-left:4px solid #fbc02d;padding:10px 14px;
       margin-bottom:20px;border-radius:4px}
</style>
<h1>Branding review</h1>
<div class=note>Left half of each strip is the original, right half is cleaned.
Note the <b>#numbers</b> of any that look wrong, then run
<code>python brand_clean.py apply --skip 3,17</code> to leave those alone.</div>
""" + "\n".join(rows)

    with open(f"{REVIEW_DIR}/index.html", "w") as fh:
        fh.write(html)


# ==========================================================================
# apply
# ==========================================================================

def cmd_apply(args) -> int:
    site = Site(args)
    path = f"{REVIEW_DIR}/manifest.json"
    if not os.path.exists(path):
        sys.exit("No review found. Run:  python brand_clean.py review")

    manifest = json.load(open(path))
    skip = {int(s) for s in args.skip.split(",") if s.strip().isdigit()}

    todo = [e for e in manifest
            if any(i["branded"] for i in e["images"]) and e["n"] not in skip]
    print(f"{len(todo)} products to update"
          f"{f', skipping {sorted(skip)}' if skip else ''}\n")

    if not todo:
        return 0
    if not args.dry_run:
        site.check_media()

    done = failed = 0
    for e in todo:
        images: list[dict] = []
        changed = False

        for rec in e["images"]:
            tag = f'{e["n"]:03d}_{rec["i"]}'
            local = f"{REVIEW_DIR}/clean/{tag}.jpg"

            if rec["branded"] and os.path.exists(local):
                if args.dry_run:
                    print(f"  would replace #{e['n']} image {rec['i']}"
                          f"  {e['name'][:40]}")
                    images.append({"id": rec["media_id"]})
                    changed = True
                    continue
                try:
                    with open(local, "rb") as fh:
                        new_id = site.upload(fh.read(), f"{tag}-clean.jpg")
                    images.append({"id": new_id})
                    changed = True
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! upload #{e['n']}: {exc}")
                    failed += 1
                    if rec["media_id"]:
                        images.append({"id": rec["media_id"]})
            elif rec.get("media_id"):
                images.append({"id": rec["media_id"]})

        if not changed or args.dry_run:
            if changed:
                done += 1
            continue

        try:
            site.set_images(e["id"], images)
            done += 1
            print(f"  #{e['n']} {e['name'][:46]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {e['name'][:40]}: {exc}")
            failed += 1
        time.sleep(args.delay)

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {done}, "
          f"{failed} errors.")
    if not args.dry_run and done:
        print("Originals remain in the media library if you need to revert.")
    return 1 if failed else 0


# ==========================================================================
# CLI
# ==========================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--wc-url", default=os.getenv("WC_URL") or WC_URL)
        sp.add_argument("--wc-key", default=os.getenv("WC_KEY") or WC_KEY)
        sp.add_argument("--wc-secret", default=os.getenv("WC_SECRET") or WC_SECRET)
        sp.add_argument("--wp-user", default=os.getenv("WP_USER") or WP_USER)
        sp.add_argument("--wp-password",
                        default=os.getenv("WP_APP_PASSWORD") or WP_APP_PASSWORD)
        sp.add_argument("--delay", type=float, default=0.15)

    r = sub.add_parser("review", help="Detect branding, write ./review/, upload nothing")
    common(r)
    r.add_argument("--strict", type=float, default=1.0,
                   help="Raise above 1.0 to reject more, lower to accept more")

    a = sub.add_parser("apply", help="Upload the cleaned images")
    common(a)
    a.add_argument("--skip", default="",
                   help="Product numbers to leave alone, e.g. 3,17,42")
    a.add_argument("--dry-run", action="store_true")

    return p


def main() -> None:
    args = build_parser().parse_args()
    if "paste_your" in args.wc_key:
        sys.exit("Set WC_KEY and WC_SECRET at the top of brand_clean.py")
    sys.exit({"review": cmd_review, "apply": cmd_apply}[args.command](args))


if __name__ == "__main__":
    main()
