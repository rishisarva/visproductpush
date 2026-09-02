#!/usr/bin/env python3
"""
pipeline.py
===========

Runs the branding cleanup automatically and publishes a dashboard you can
check from your phone.

Commands
--------
    python pipeline.py auto        find branding, clean it, record what changed
    python pipeline.py revert --products 22651,22659
    python pipeline.py draft  --products 22651
    python pipeline.py skip   --products 22651     never touch this one again
    python pipeline.py unskip --products 22651
    python pipeline.py build                       rebuild the dashboard only

State lives in state/history.json and the dashboard in docs/, both committed
back to the repo by the workflow so nothing is lost between runs.

Detection logic is imported from brand_clean.py, so there is one place to
improve it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

try:
    import requests
    from PIL import Image
except ImportError:
    sys.exit("Missing dependencies. Run:  pip install requests pillow")

from brand_clean import clean, crop_around, Site
from wordmark import find_wordmark


STATE_DIR = "state"
STATE_FILE = f"{STATE_DIR}/history.json"
DOCS_DIR = "docs"
IMG_DIR = f"{DOCS_DIR}/img"
SKU_PREFIX = "TS-"

# Bump this whenever the detection logic changes. Products checked under an
# older version are looked at again automatically, so an improvement is never
# hidden behind the memory of what was already inspected.
DETECTOR_VERSION = 7

KEEP_EDITS = 80          # how many recent edits the dashboard shows
THUMB_WIDTH = 460


# ==========================================================================
# State
# ==========================================================================

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as fh:
                state = json.load(fh)
        except (ValueError, OSError):
            state = {}
    else:
        state = {}
    state.setdefault("runs", [])
    state.setdefault("products", {})
    state.setdefault("skip", [])
    state.setdefault("checked", {})     # pid -> {"at":..., "fingerprint":...}

    if state.get("detector_version") != DETECTOR_VERSION:
        if state["checked"]:
            print(f"Detection logic changed (v{state.get('detector_version')} "
                  f"-> v{DETECTOR_VERSION}); re-examining every product once.")
        state["checked"] = {}
        state["detector_version"] = DETECTOR_VERSION

    return state


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    state["detector_version"] = DETECTOR_VERSION
    state["runs"] = state["runs"][-40:]
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=1)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ==========================================================================
# Auto clean
# ==========================================================================

def thumb(img: Image.Image, path: str) -> None:
    im = img.convert("RGB")
    if im.width > THUMB_WIDTH:
        h = int(im.height * THUMB_WIDTH / im.width)
        im = im.resize((THUMB_WIDTH, h), Image.LANCZOS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, quality=86, optimize=True)


def cmd_auto(args) -> int:
    site = Site(args)
    state = load_state()
    skip = set(str(s) for s in state["skip"])

    print(f"Run at {now_iso()}")
    products = site.products()
    print(f"{len(products)} products on the site")

    if not args.dry_run:
        site.check_media()

    run = {"at": now_iso(), "checked": 0, "edited": 0,
           "images": 0, "errors": 0, "skipped": len(skip)}
    why = Counter()          # why images were left alone
    changed_products: list[dict] = []

    # Work through the catalogue in small batches. The host throttles image
    # downloads hard, so a run that tries all 180 products spends most of its
    # time being refused. Products never checked come first, then the oldest.
    def sort_key(p):
        pid = str(p["id"])
        seen = state["checked"].get(pid, {})
        return (1 if seen else 0, seen.get("at", ""))

    queue = []
    for p in products:
        pid = str(p["id"])
        if pid in skip or not p.get("images"):
            continue
        fingerprint = ",".join(str(i.get("id")) for i in p.get("images", []))
        seen = state["checked"].get(pid)
        if (not getattr(args, "recheck", False)
                and seen and seen.get("fingerprint") == fingerprint):
            continue                    # unchanged since we last looked
        queue.append(p)

    queue.sort(key=sort_key)
    total_pending = len(queue)
    if args.max_products:
        queue = queue[:args.max_products]

    print(f"{total_pending} products need checking, doing {len(queue)} this run")
    run["pending"] = total_pending

    for product in queue:
        pid = str(product["id"])
        name = product.get("name", "")

        record = state["products"].get(pid, {})
        already = {int(k) for k in record.get("cleaned_indexes", [])}

        images = product.get("images", [])
        run["checked"] += 1

        new_list: list[dict] = []
        edits: list[dict] = []

        for idx, image in enumerate(images):
            src, media_id = image.get("src"), image.get("id")
            if not src:
                continue

            # already handled on a previous run
            if idx in already:
                if media_id:
                    new_list.append({"id": media_id})
                continue

            try:
                img = site.download(src)
                hit = find_wordmark(img)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! read {name[:38]} image {idx}: {exc}")
                run["errors"] += 1
                if media_id:
                    new_list.append({"id": media_id})
                continue

            if hit is None:
                why["no wordmark read"] += 1
                if getattr(args, "explain", False):
                    print(f"  skip {name[:38]} image {idx}: no wordmark read")
                if media_id:
                    new_list.append({"id": media_id})
                continue

            box = hit.box
            reason = f"read '{hit.text[:26]}' [{hit.how}]"
            print(f"  clean {name[:42]} image {idx}  {reason}")

            if args.dry_run:
                if media_id:
                    new_list.append({"id": media_id})
                edits.append({"i": idx, "old": media_id, "new": None,
                              "reason": reason})
                continue

            try:
                fixed = clean(img, box)
                tag = f"{pid}_{idx}"
                thumb(crop_around(img.convert("RGB"), box),
                      f"{IMG_DIR}/{tag}_before.jpg")
                thumb(crop_around(fixed, box), f"{IMG_DIR}/{tag}_after.jpg")

                buf = io.BytesIO()
                fixed.convert("RGB").save(buf, format="JPEG",
                                          quality=92, optimize=True)
                new_id = site.upload(buf.getvalue(), f"{tag}-clean.jpg")
                new_list.append({"id": new_id})
                edits.append({"i": idx, "old": media_id, "new": new_id,
                              "reason": reason})
            except Exception as exc:  # noqa: BLE001
                print(f"  ! clean {name[:38]}: {exc}")
                run["errors"] += 1
                if media_id:
                    new_list.append({"id": media_id})

            time.sleep(args.delay)

        if not args.dry_run:
            state["checked"][pid] = {
                "at": now_iso(),
                "fingerprint": ",".join(str(i.get("id")) for i in images),
            }

        if not edits:
            continue

        if args.dry_run:
            run["edited"] += 1
            run["images"] += len(edits)
            continue

        try:
            site.set_images(product["id"], new_list)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! update {name[:38]}: {exc}")
            run["errors"] += 1
            continue

        prior = state["products"].get(pid, {})
        originals = prior.get("original_images") or [
            i.get("id") for i in images if i.get("id")]

        state["products"][pid] = {
            "name": name,
            "url": product.get("permalink", ""),
            "sku": product.get("sku", ""),
            "original_images": originals,
            "cleaned_indexes": sorted(already | {e["i"] for e in edits}),
            "edits": (prior.get("edits", []) + edits)[-12:],
            "last": now_iso(),
        }

        run["edited"] += 1
        run["images"] += len(edits)
        changed_products.append({"pid": pid, "name": name,
                                 "edits": edits,
                                 "url": product.get("permalink", "")})
        time.sleep(args.delay)

    run["changed"] = [c["pid"] for c in changed_products]
    state["runs"].append(run)

    if not args.dry_run:
        save_state(state)
        prune_images(state)
        build_dashboard(state)

    print(f"\nChecked {run['checked']} products, "
          f"edited {run['edited']} ({run['images']} images), "
          f"{run['errors']} errors.")

    if why:
        print("\nImages left alone, by reason:")
        for reason, count in why.most_common():
            print(f"  {count:5d}  {reason}")
    return 0


# ==========================================================================
# Undo / safety
# ==========================================================================

def parse_ids(raw: str) -> list[str]:
    return [p.strip() for p in raw.replace(" ", ",").split(",") if p.strip()]


def cmd_revert(args) -> int:
    site = Site(args)
    state = load_state()
    targets = parse_ids(args.products)
    done = 0

    for pid in targets:
        rec = state["products"].get(pid)
        if not rec:
            print(f"  ? {pid} has no recorded edits, nothing to undo")
            continue
        originals = rec.get("original_images") or []
        if not originals:
            print(f"  ? {pid} has no stored originals")
            continue
        try:
            site.set_images(int(pid), [{"id": i} for i in originals])
            rec["cleaned_indexes"] = []
            rec["reverted"] = now_iso()
            if pid not in state["skip"]:
                state["skip"].append(pid)      # don't re-clean what you undid
            done += 1
            print(f"  restored {rec.get('name','')[:46]} and added to skip list")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {pid}: {exc}")

    save_state(state)
    build_dashboard(state)
    print(f"\nReverted {done}.")
    return 0


def cmd_undo_last(args) -> int:
    """
    Put back the originals for every product the last run edited.

    Unlike `revert`, this does not add them to the skip list: the point is to
    undo a bad detector change, so they should be looked at again once the
    detection is fixed.
    """
    site = Site(args)
    state = load_state()

    if not state["runs"]:
        print("No runs recorded.")
        return 0

    last = state["runs"][-1]
    targets = last.get("changed", [])
    print(f"Last run was {last.get('at','?')}, it edited {len(targets)} products.")

    if not targets:
        return 0

    done = failed = 0
    for pid in targets:
        rec = state["products"].get(str(pid))
        if not rec or not rec.get("original_images"):
            print(f"  ? {pid} has no stored originals")
            continue
        try:
            site.set_images(int(pid), [{"id": i} for i in rec["original_images"]])
            rec["cleaned_indexes"] = []
            rec["edits"] = []
            rec["reverted"] = now_iso()
            state["checked"].pop(str(pid), None)   # let it be looked at again
            done += 1
            print(f"  restored {rec.get('name','')[:46]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {pid}: {exc}")
            failed += 1
        time.sleep(args.delay)

    save_state(state)
    build_dashboard(state)
    print(f"\nRestored {done} products, {failed} failed.")
    return 1 if failed else 0


def cmd_draft(args) -> int:
    """Take a product off the shop without deleting it."""
    site = Site(args)
    state = load_state()
    done = 0

    for pid in parse_ids(args.products):
        try:
            site.wc.put(f"{site.root}/wp-json/wc/v3/products/{pid}",
                        json={"status": "draft"}, timeout=60).raise_for_status()
            rec = state["products"].setdefault(pid, {})
            rec["drafted"] = now_iso()
            if pid not in state["skip"]:
                state["skip"].append(pid)
            done += 1
            print(f"  drafted {pid}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {pid}: {exc}")

    save_state(state)
    build_dashboard(state)
    print(f"\nDrafted {done}. They are hidden from the shop, not deleted.")
    return 0


def cmd_skip(args) -> int:
    state = load_state()
    for pid in parse_ids(args.products):
        if pid not in state["skip"]:
            state["skip"].append(pid)
            print(f"  will never touch {pid}")
    save_state(state)
    build_dashboard(state)
    return 0


def cmd_unskip(args) -> int:
    state = load_state()
    for pid in parse_ids(args.products):
        if pid in state["skip"]:
            state["skip"].remove(pid)
            print(f"  {pid} back in scope")
    save_state(state)
    build_dashboard(state)
    return 0


# ==========================================================================
# Dashboard
# ==========================================================================

def prune_images(state: dict) -> None:
    """Keep the repo small: only thumbnails still shown on the dashboard."""
    if not os.path.isdir(IMG_DIR):
        return
    wanted: set[str] = set()
    for pid, rec in recent_edits(state):
        for e in rec["edits"]:
            wanted.add(f"{pid}_{e['i']}_before.jpg")
            wanted.add(f"{pid}_{e['i']}_after.jpg")
    for name in os.listdir(IMG_DIR):
        if name not in wanted:
            try:
                os.remove(os.path.join(IMG_DIR, name))
            except OSError:
                pass


def recent_edits(state: dict):
    items = [(pid, rec) for pid, rec in state["products"].items()
             if rec.get("edits") and not rec.get("reverted")]
    items.sort(key=lambda kv: kv[1].get("last", ""), reverse=True)
    out, count = [], 0
    for pid, rec in items:
        out.append((pid, rec))
        count += len(rec["edits"])
        if count >= KEEP_EDITS:
            break
    return out


def build_dashboard(state: dict) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    runs = state["runs"]
    last = runs[-1] if runs else {}

    cards = []
    for pid, rec in recent_edits(state):
        shots = []
        for e in rec["edits"]:
            b = f"img/{pid}_{e['i']}_before.jpg"
            a = f"img/{pid}_{e['i']}_after.jpg"
            if not os.path.exists(f"{DOCS_DIR}/{b}"):
                continue
            shots.append(
                f'<div class=pair>'
                f'<figure><img src="{b}" loading=lazy><figcaption>before</figcaption></figure>'
                f'<figure><img src="{a}" loading=lazy><figcaption>after</figcaption></figure>'
                f'</div>')
        if not shots:
            continue
        link = (f'<a class=view href="{rec["url"]}" target=_blank>view on site</a>'
                if rec.get("url") else "")
        cards.append(f"""
<article class=card>
  <h3>{rec.get('name','')}</h3>
  <div class=meta>id <code>{pid}</code> &middot; {rec.get('last','')} {link}</div>
  {''.join(shots)}
  <div class=undo>Wrong? Run the <b>Fix a product</b> action with id <code>{pid}</code></div>
</article>""")

    history = "".join(
        f"<tr><td>{r.get('at','')}</td><td>{r.get('edited',0)}</td>"
        f"<td>{r.get('images',0)}</td><td>{r.get('errors',0)}</td></tr>"
        for r in reversed(runs[-12:]))

    skipped = ", ".join(f"<code>{s}</code>" for s in state["skip"]) or "none"

    html = f"""<!doctype html>
<html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Visions Jersey &middot; image cleanup</title>
<style>
 :root {{ color-scheme: light dark; }}
 * {{ box-sizing: border-box; }}
 body {{ font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        margin:0; padding:16px; background:#f6f7f9; color:#1a1a1a;
        max-width:760px; margin-inline:auto; }}
 h1 {{ font-size:20px; margin:4px 0 2px; }}
 .sub {{ color:#666; font-size:13px; margin-bottom:16px; }}
 .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px;
          margin-bottom:20px; }}
 .stat {{ background:#fff; border-radius:10px; padding:10px 8px; text-align:center;
         box-shadow:0 1px 2px rgba(0,0,0,.08); }}
 .stat b {{ display:block; font-size:20px; }}
 .stat span {{ font-size:11px; color:#666; text-transform:uppercase;
              letter-spacing:.04em; }}
 .card {{ background:#fff; border-radius:12px; padding:14px; margin-bottom:14px;
         box-shadow:0 1px 3px rgba(0,0,0,.09); }}
 .card h3 {{ font-size:15px; margin:0 0 4px; }}
 .meta {{ font-size:12px; color:#666; margin-bottom:10px; }}
 .view {{ margin-left:8px; }}
 .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px; }}
 .pair img {{ width:100%; border-radius:6px; border:1px solid #e2e2e2;
             display:block; }}
 figcaption {{ font-size:11px; color:#888; text-align:center; margin-top:2px; }}
 figure {{ margin:0; }}
 .undo {{ font-size:12px; background:#fff8e1; border-radius:6px;
         padding:8px 10px; color:#5d4a00; margin-top:6px; }}
 table {{ width:100%; border-collapse:collapse; background:#fff;
         border-radius:10px; overflow:hidden; font-size:13px; }}
 th,td {{ padding:8px 10px; text-align:left; border-bottom:1px solid #eee; }}
 th {{ background:#fafafa; font-size:11px; text-transform:uppercase;
      color:#666; letter-spacing:.04em; }}
 h2 {{ font-size:15px; margin:26px 0 8px; }}
 .note {{ font-size:12px; color:#666; margin-top:8px; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background:#16181c; color:#e8e8e8; }}
   .card,.stat,table {{ background:#212429; box-shadow:none; }}
   th {{ background:#1b1e22; }} td,th {{ border-color:#2c3036; }}
   .undo {{ background:#2e2a1a; color:#e8d9a0; }}
   .pair img {{ border-color:#333; }}
 }}
</style>

<h1>Image cleanup</h1>
<div class=sub>Last run {last.get('at','never')}</div>

<div class=stats>
  <div class=stat><b>{last.get('checked',0)}</b><span>checked</span></div>
  <div class=stat><b>{last.get('edited',0)}</b><span>products</span></div>
  <div class=stat><b>{last.get('images',0)}</b><span>images</span></div>
  <div class=stat><b>{last.get('errors',0)}</b><span>errors</span></div>
</div>
<div class=stats>
  <div class=stat><b>{last.get('pending',0)}</b><span>still to check</span></div>
</div>

<h2>Recently cleaned</h2>
{''.join(cards) if cards else '<div class=card>Nothing cleaned yet.</div>'}

<h2>Run history</h2>
<table>
 <tr><th>when</th><th>products</th><th>images</th><th>errors</th></tr>
 {history or '<tr><td colspan=4>no runs yet</td></tr>'}
</table>

<h2>Never touched</h2>
<div class=card>{skipped}</div>
<div class=note>To undo an edit or hide a product from your phone, open the
repository on GitHub, go to Actions, choose <b>Fix a product</b>, and run it
with the product id.</div>
</html>"""

    with open(f"{DOCS_DIR}/index.html", "w") as fh:
        fh.write(html)
    open(f"{DOCS_DIR}/.nojekyll", "w").close()


def cmd_score(args) -> int:
    """Report which images carry the supplier mark. Changes nothing."""
    site = Site(args)
    products = site.products()
    print(f"{len(products)} products\n")

    hits, misses = [], 0
    for i, product in enumerate(products, start=1):
        name = product.get("name", "")
        for idx, image in enumerate(product.get("images", [])):
            src = image.get("src")
            if not src:
                continue
            try:
                img = site.download(src)
                hit = find_wordmark(img)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {name[:34]} image {idx}: {exc}")
                continue
            if hit:
                W, H = img.size
                hits.append((product["id"], name[:40], idx,
                             hit.box[0] / W, hit.box[2] / W,
                             hit.box[1] / H, hit.box[3] / H, hit.text[:26]))
            else:
                misses += 1
            time.sleep(args.delay)
        if i % 20 == 0:
            print(f"  ...{i}/{len(products)}")

    print(f"\nFound the mark on {len(hits)} images; {misses} had none.\n")
    print(f"{'id':>7} {'img':>3}  where                      read as")
    print("-" * 78)
    for pid, name, idx, x0, x1, y0, y1, text in hits:
        print(f"{pid:>7} {idx:>3}  x {x0:.2f}-{x1:.2f} y {y0:.2f}-{y1:.2f}  "
              f"{text}  {name}")
    return 0


def cmd_build(args) -> int:
    state = load_state()
    build_dashboard(state)
    print(f"Wrote {DOCS_DIR}/index.html")
    return 0


# ==========================================================================
# CLI
# ==========================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--wc-url", default=os.getenv("WC_URL", ""))
        sp.add_argument("--wc-key", default=os.getenv("WC_KEY", ""))
        sp.add_argument("--wc-secret", default=os.getenv("WC_SECRET", ""))
        sp.add_argument("--wp-user", default=os.getenv("WP_USER", ""))
        sp.add_argument("--wp-password", default=os.getenv("WP_APP_PASSWORD", ""))
        sp.add_argument("--delay", type=float, default=1.5)

    a = sub.add_parser("auto", help="Find and clean branding, unattended")
    common(a)
    a.add_argument("--strict", type=float, default=1.0,
                   help="Above 1.0 is fussier and edits less")
    a.add_argument("--max-products", type=int, default=25,
                   help="Products to inspect per run. Small keeps runs short "
                        "and stays under the host's rate limits.")
    a.add_argument("--recheck", action="store_true",
                   help="Ignore what was checked before and look at everything")
    a.add_argument("--explain", action="store_true",
                   help="Print why each image was left alone")
    a.add_argument("--dry-run", action="store_true")

    for name, help_text in (("revert", "Put the original images back"),
                            ("draft", "Hide a product from the shop"),
                            ("skip", "Never touch this product"),
                            ("unskip", "Allow this product again")):
        sp = sub.add_parser(name, help=help_text)
        common(sp)
        sp.add_argument("--products", required=True,
                        help="Product ids, comma separated")

    u = sub.add_parser("undo-last",
                       help="Undo every edit the most recent run made")
    common(u)

    sc = sub.add_parser("score",
                        help="Score every image against the wordmark, change nothing")
    common(sc)

    b = sub.add_parser("build", help="Rebuild the dashboard from saved state")
    common(b)

    return p


def main() -> None:
    args = build_parser().parse_args()
    handlers = {"auto": cmd_auto, "revert": cmd_revert, "draft": cmd_draft,
                "skip": cmd_skip, "unskip": cmd_unskip, "build": cmd_build,
                "undo-last": cmd_undo_last, "score": cmd_score}
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
