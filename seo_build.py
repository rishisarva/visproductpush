#!/usr/bin/env python3
"""
seo_build.py — prerenders shop.visionsjersey.com for search engines.

The storefront is a single-page app: every URL currently returns the same
index.html with the same title, and every product name is fetched by
JavaScript. Google sees one page, not a shop.

This writes a real HTML file for every product and category. Each one is the
same storefront, byte for byte, with three things changed in the <head> and a
small block of crawlable text added before the app boots:

  * a real <title>, description and canonical
  * Product / ItemList / BreadcrumbList JSON-LD
  * a <div> of plain text that the app deletes on first paint

Performance is unchanged. Nothing new is downloaded, nothing blocks rendering,
and the injected text is removed before the user sees anything. Same JS, same
CSS, same fonts, same 94.

    python seo_build.py --src visions-shop-site --out dist
    python seo_build.py --src visions-shop-site --out dist --report
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

SITE = "https://shop.visionsjersey.com"
SUPA = "https://amgiihalfdhogzjrxinu.supabase.co"
FEED = f"{SUPA}/storage/v1/object/public/feeds/shop-products.json"

# Categories are matched against the product title. Order matters: the first
# match wins, so the more specific patterns come first.
CATEGORIES = [
    ("retro-jerseys", "Retro Football Jerseys", r"\b(19|20)\d{2}\s*-\s*\d{2}\b|\bRETRO\b",
     "Classic football jerseys from seasons gone by, in stitched fabric that holds its colour wash after wash. "
     "Every retro jersey ships from India in 24-48 hours, so you're wearing it this weekend, not next month."),
    ("polo-jerseys", "Polo Collar Football Jerseys", r"\bPOLO\b",
     "Football jerseys with a proper polo collar — the cut that works on match day and looks right off the pitch too. "
     "Stitched collars, buttoned plackets, sizes S to XXL."),
    ("full-sleeve-jerseys", "Full Sleeve Football Jerseys", r"\bFULL\s*SLEEVE\b|\bFULLSLEEVE\b",
     "Long sleeve football jerseys for cooler evenings and winter kickabouts. Same fabric and stitching as the "
     "short sleeve versions, with full-length sleeves and ribbed cuffs."),
    ("half-sleeve-jerseys", "Half Sleeve Football Jerseys", r"\bFIVE\s*SLEEVE\b|\bFIVESLEEVE\b",
     "Half sleeve football jerseys that sit just above the elbow. Lightweight, breathable, and cut a little "
     "fitted — order one size up from your usual."),
    ("embroidered-jerseys", "Embroidered Football Jerseys", r"\bEMBROID",
     "Football jerseys with embroidered detailing rather than heat-pressed prints. Stitched badges last longer, "
     "don't crack or peel, and look sharper up close."),
    ("premium-jerseys", "Premium Football Jerseys", r"\bPREMIUM\b",
     "Our top-tier football jerseys: heavier stitched fabric, double-stitched hems and embroidered details. "
     "Checked piece by piece before packing."),
    ("training-jerseys", "Training Jerseys", r"\bTRAINING\b",
     "Lightweight training jerseys for practice sessions and gym days. Quick-drying fabric, relaxed fit."),
    ("new-season-kits", "New Season Football Jerseys", r"\b2[5-9]\s*-\s*2[6-9]\b",
     "The latest season's football jerseys, in stock and dispatched within 24-48 hours. Buy any two and save ₹100."),
]


# Price pages. Champions Kit and Limeroad both rank "football jersey under
# ₹___" with a dedicated URL per bucket. Buckets are set to the real catalogue
# range — the cheapest jersey is ₹520, so an "under 500" page would be empty
# and Google treats empty category pages as thin.
_PRICE_LIVE = []
PRICE_PAGES = [
    ("under-600",  600,  "Football Jerseys Under ₹600",
     "Football jerseys priced under ₹600, every one in stitched fabric rather than cheap sublimation prints. "
     "Same 24-48 hour dispatch as everything else we sell, and buying two still gets you ₹100 off."),
    ("under-700",  700,  "Football Jerseys Under ₹700",
     "Our full range of football jerseys under ₹700 — retro kits, new season kits, polo collar and full sleeve. "
     "Stitched, checked before packing, dispatched within 24-48 hours across India."),
]


# ---------------------------------------------------------------- helpers

def esc(s):
    return html.escape(str(s or ""), quote=True)


def fetch_products():
    r = requests.get(FEED, timeout=60)
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else data.get("products", [])
    out = []
    for p in rows:
        slug = (p.get("slug") or "").strip()
        name = (p.get("name") or "").strip()
        if not slug or not name:
            continue
        imgs = p.get("images") or ([p["thumb"]] if p.get("thumb") else [])
        out.append({
            "slug": slug,
            "name": name,
            "price": p.get("price"),
            "mrp": p.get("mrp"),
            "images": [i for i in imgs if i][:4],
            "sizes": [s for s in (p.get("sizes") or []) if s],
            "in_stock": bool(p.get("in_stock", True)),
        })
    return out


MIN_CATEGORY = 6   # fewer than this and the page is too thin to rank


# Price pages are matched on the real price, never the title, so the page can't
# lie. "Under 500" is what people search, but nothing here is under 500 — a page
# with that name would bounce every click. 700 is honest.
PRICE_CATEGORIES = [
    ("under-700", "Football Jerseys Under ₹700", 700,
     "Every football jersey we sell for under ₹700 — retro and new season, polo and full sleeve, "
     "in the same stitched fabric as the rest of the range. Dispatched in 24-48 hours across India, "
     "and buying any two saves ₹100."),
]

# One flat list of every category so the other functions don't care which kind.
ALL_CATEGORIES = [(s_, t_, i_) for s_, t_, _p, i_ in CATEGORIES] + \
                 [(s_, t_, i_) for s_, t_, _c, i_ in PRICE_CATEGORIES]


def categorise(products):
    """A jersey belongs to every category it matches — a polo, embroidered,
    premium jersey sits in all three. First-match-wins starved most pages."""
    buckets = defaultdict(list)
    for p in products:
        up = p["name"].upper()
        for slug, title, pattern, _intro in CATEGORIES:
            if re.search(pattern, up):
                buckets[slug].append(p)
        for slug, title, cap, _intro in PRICE_CATEGORIES:
            if p["price"] and float(p["price"]) < cap:
                buckets[slug].append(p)
    return {k: v for k, v in buckets.items() if len(v) >= MIN_CATEGORY}


def meta_description(p):
    bits = []
    if p["price"]:
        bits.append(f"₹{int(float(p['price']))}")
    if p["sizes"]:
        bits.append("sizes " + ", ".join(p["sizes"][:5]))
    tail = " · ".join(bits)
    d = f"{p['name']}. {tail}. Dispatched in 24-48 hours across India. Buy any 2 jerseys and get ₹100 off. Secure UPI payment."
    return re.sub(r"\s+", " ", d).strip()[:158]


def product_jsonld(p):
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": p["name"],
        "url": f"{SITE}/jersey/{p['slug']}",
        "image": [
            {"@type": "ImageObject", "url": u, "caption": alt_text(p, i)}
            for i, u in enumerate(p["images"])
        ],
        "brand": {"@type": "Brand", "name": "Visions Jersey"},
        "offers": {
            "@type": "Offer",
            "url": f"{SITE}/jersey/{p['slug']}",
            "priceCurrency": "INR",
            "price": str(p["price"] or ""),
            "availability": "https://schema.org/InStock" if p["in_stock"] else "https://schema.org/OutOfStock",
            "seller": {"@type": "Organization", "name": "Visions Jersey"},
        },
    }
    if p["sizes"]:
        data["size"] = p["sizes"]
    return data


def breadcrumb_jsonld(trail):
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": n, "item": u}
            for i, (n, u) in enumerate(trail)
        ],
    }


# ---------------------------------------------------------------- rendering

HEAD_RE = re.compile(
    r'<title>.*?</title>\s*<meta name="description" content=".*?">',
    re.S,
)


def render(shell, *, title, desc, canonical, jsonld, visible, image=None):
    """Swap the head block and drop a crawlable text block in before the app."""
    head = (
        f"<title>{esc(title)}</title>\n"
        f'<meta name="description" content="{esc(desc)}">\n'
        f'<link rel="canonical" href="{esc(canonical)}">\n'
        f'<meta property="og:type" content="product">\n'
        f'<meta property="og:title" content="{esc(title)}">\n'
        f'<meta property="og:description" content="{esc(desc)}">\n'
        f'<meta property="og:url" content="{esc(canonical)}">\n'
        + (f'<meta property="og:image" content="{esc(image)}">\n' if image else "")
        + '<meta name="twitter:card" content="summary_large_image">'
    )
    out, n = HEAD_RE.subn(lambda _: head, shell, count=1)
    if not n:
        raise SystemExit("Could not find the title/description block to replace.")

    for block in jsonld:
        out = out.replace(
            "</head>",
            '<script type="application/ld+json">'
            + json.dumps(block, ensure_ascii=False, separators=(",", ":"))
            + "</script>\n</head>",
            1,
        )

    # Crawlable text. Removed on the app's first paint, so a shopper never sees
    # it — but it is in the HTML that Googlebot downloads, which is the point.
    seo = (
        '<div id="seoPre" style="position:absolute;left:-9999px;top:0;width:1px;height:1px;overflow:hidden">'
        + visible
        + "</div>"
        + "<script>document.addEventListener('DOMContentLoaded',function(){var e=document.getElementById('seoPre');if(e)e.remove();});</script>"
    )
    return out.replace("<body>", "<body>\n" + seo, 1)


def alt_text(p, i):
    views = ["front view", "back view", "detail", "close-up"]
    return f"{p['name'].title()} football jersey — {views[i] if i < len(views) else 'photo'}"


def product_html(shell, p, related=(), cat=None):
    price = f"₹{int(float(p['price']))}" if p["price"] else ""
    # Images with descriptive alt text. Googlebot indexes these for image search
    # even though the div is hidden — alt is read from the HTML, not the screen.
    imgs = "".join(
        f'<img src="{esc(u)}" alt="{esc(alt_text(p, i))}" width="800" height="800" loading="lazy">'
        for i, u in enumerate(p["images"])
    )
    rel = "".join(
        f'<li><a href="/jersey/{esc(r["slug"])}">{esc(r["name"])}</a></li>' for r in related
    )
    visible = (
        f"<h1>{esc(p['name'])}</h1>"
        f"<p>{esc(price)}</p>"
        + imgs
        + (f"<p>Available sizes: {esc(', '.join(p['sizes']))}</p>" if p["sizes"] else "")
        + "<p>Stitched fabric that holds its colour after washing. Every piece is checked before it's packed. "
          "Dispatched in 24-48 hours across India. Buy any 2 jerseys and get ₹100 off. Secure UPI payment.</p>"
        + (f'<p>Category: <a href="/c/{esc(cat[0])}">{esc(cat[1])}</a></p>' if cat else "")
        + (f"<h2>You might also like</h2><ul>{rel}</ul>" if rel else "")
        + '<p><a href="/shop">All football jerseys</a></p>'
    )
    return render(
        shell,
        title=f"{p['name']} — Visions Jersey",
        desc=meta_description(p),
        canonical=f"{SITE}/jersey/{p['slug']}",
        image=p["images"][0] if p["images"] else None,
        jsonld=[
            product_jsonld(p),
            breadcrumb_jsonld([("Home", SITE), ("Jerseys", f"{SITE}/shop"), (p["name"], f"{SITE}/jersey/{p['slug']}")]),
        ],
        visible=visible,
    )


def category_html(shell, slug, title, items, intro):
    lis = "".join(
        f'<li><a href="/jersey/{esc(p["slug"])}">{esc(p["name"])}</a>'
        + (f' — ₹{int(float(p["price"]))}' if p["price"] else "")
        + "</li>"
        for p in items
    )
    others = "".join(
        f'<li><a href="/c/{esc(s2)}">{esc(t2)}</a></li>' for s2, t2, _i in ALL_CATEGORIES if s2 != slug
    )
    visible = (
        f"<h1>{esc(title)}</h1>"
        f"<p>{esc(intro)}</p>"
        f"<p>{len(items)} jerseys in stock. Dispatched in 24-48 hours across India. Buy any 2 and get ₹100 off. Secure UPI payment.</p>"
        f"<ul>{lis}</ul>"
        f"<h2>More football jerseys</h2><ul>{others}</ul>"
        f'<p><a href="/shop">All football jerseys</a></p>'
    )
    itemlist = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": title,
        "numberOfItems": len(items),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "url": f"{SITE}/jersey/{p['slug']}", "name": p["name"]}
            for i, p in enumerate(items[:60])
        ],
    }
    return render(
        shell,
        title=f"{title} — Buy Online in India | Visions Jersey",
        desc=f"Shop {len(items)} {title.lower()} from ₹520. Dispatched in 24-48 hours across India. Buy any 2 and get ₹100 off. Secure UPI payment.",
        canonical=f"{SITE}/c/{slug}",
        jsonld=[itemlist, breadcrumb_jsonld([("Home", SITE), (title, f"{SITE}/c/{slug}")])],
        visible=visible,
    )


def price_html(shell, slug, cap, title, intro, items):
    lis = "".join(
        f'<li><a href="/jersey/{esc(p["slug"])}">{esc(p["name"])}</a> — ₹{int(float(p["price"]))}</li>'
        for p in items
    )
    cats = "".join(f'<li><a href="/c/{esc(s2)}">{esc(t2)}</a></li>' for s2, t2, _i in ALL_CATEGORIES)
    faq = [
        ("Are these the same quality as the higher-priced jerseys?",
         "Yes. Price differences come from construction — embroidered versus heat-pressed badges, polo collar versus round neck — not from fabric. Everything is stitched, checked before packing."),
        ("How fast is delivery?",
         "Dispatched within 24-48 hours of payment, delivered in about 5-6 days anywhere in India. A tracking number appears on your order page when it ships."),
        ("Does the Buy 2 offer apply?",
         "Yes. Add any two jerseys and ₹100 comes off automatically at checkout."),
    ]
    faq_html = "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in faq)
    visible = (
        f"<h1>{esc(title)}</h1><p>{esc(intro)}</p>"
        f"<p>{len(items)} jerseys under ₹{cap}.</p><ul>{lis}</ul>"
        f"<h2>Frequently asked</h2>{faq_html}"
        f"<h2>Browse by style</h2><ul>{cats}</ul>"
    )
    faq_ld = {
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq],
    }
    itemlist = {
        "@context": "https://schema.org", "@type": "ItemList", "name": title,
        "numberOfItems": len(items),
        "itemListElement": [{"@type": "ListItem", "position": i + 1,
                             "url": f"{SITE}/jersey/{p['slug']}", "name": p["name"]}
                            for i, p in enumerate(items[:60])],
    }
    return render(
        shell,
        title=f"{title} — {len(items)} Kits, Ships in 24-48 hrs | Visions Jersey",
        desc=f"{len(items)} football jerseys under ₹{cap}. Stitched fabric, dispatched in 24-48 hours across India. Buy any 2, get ₹100 off. Secure UPI.",
        canonical=f"{SITE}/p/{slug}",
        jsonld=[itemlist, faq_ld, breadcrumb_jsonld([("Home", SITE), (title, f"{SITE}/p/{slug}")])],
        visible=visible,
    )


def sitemap(products, buckets):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [(SITE + "/", "1.0"), (SITE + "/shop", "0.9")]
    urls += [(f"{SITE}/c/{s}", "0.8") for s in buckets]
    urls += [(f"{SITE}/p/{s}", "0.8") for s in globals().get("_PRICE_LIVE", [])]
    urls += [(f"{SITE}/jersey/{p['slug']}", "0.7") for p in products]
    body = "".join(
        f"<url><loc>{esc(u)}</loc><lastmod>{today}</lastmod><priority>{pr}</priority></url>"
        for u, pr in urls
    )
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemap s.org/schemas/sitemap/0.9">'.replace(
        "sitemap s", "sitemaps"
    ) + body + "</urlset>"



def org_jsonld():
    return {
        "@context": "https://schema.org",
        "@type": "OnlineStore",
        "name": "Visions Jersey",
        "url": SITE,
        "logo": f"{SITE}/logo.png",
        "description": "Football jersey store in India. Retro kits, polo collar and full sleeve jerseys, dispatched in 24-48 hours.",
        "areaServed": "IN",
        "currenciesAccepted": "INR",
        "paymentAccepted": "UPI",
    }


def website_jsonld():
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Visions Jersey",
        "url": SITE,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": f"{SITE}/shop?q={{search_term_string}}"},
            "query-input": "required name=search_term_string",
        },
    }


def home_html(shell, products, buckets):
    cats = "".join(
        f'<li><a href="/c/{esc(s)}">{esc(t)}</a> — {len(buckets.get(s, []))} jerseys</li>'
        for s, t, _i in ALL_CATEGORIES if buckets.get(s)
    )
    picks = "".join(
        f'<li><a href="/jersey/{esc(p["slug"])}">{esc(p["name"])}</a></li>' for p in products[:12]
    )
    visible = (
        "<h1>Football Jerseys Online in India — Retro Kits, Polo Collar & Full Sleeve</h1>"
        "<p>Visions Jersey is an online football jersey store shipping across India. "
        f"{len(products)} jerseys in stock — retro classics, new season kits, polo collar and full sleeve — "
        "in stitched fabric that holds its colour. Every order is dispatched within 24-48 hours, "
        "and buying any two jerseys saves you ₹100. Prepaid via UPI, tracking on every order.</p>"
        f"<h2>Shop by style</h2><ul>{cats}</ul>"
        "<h2>Shop by price</h2><ul>"
        + "".join(f'<li><a href="/p/{esc(s)}">{esc(t)}</a></li>' for s, _c, t, _i in PRICE_PAGES)
        + "</ul>"
        f"<h2>Latest football jerseys</h2><ul>{picks}</ul>"
        '<p><a href="/shop">See all football jerseys</a></p>'
    )
    return render(
        shell,
        title="Football Jerseys Online India — Retro, Polo & Full Sleeve | Visions Jersey",
        desc=f"Football jersey store in India. {len(products)} retro and new season jerseys from ₹520, dispatched in 24-48 hours. Buy any 2, get ₹100 off. Secure UPI.",
        canonical=SITE + "/",
        image=f"{SITE}/hero.jpg",
        jsonld=[org_jsonld(), website_jsonld()],
        visible=visible,
    )


def shop_html(shell, products, buckets):
    lis = "".join(
        f'<li><a href="/jersey/{esc(p["slug"])}">{esc(p["name"])}</a>'
        + (f' — ₹{int(float(p["price"]))}' if p["price"] else "") + "</li>"
        for p in products
    )
    cats = "".join(f'<li><a href="/c/{esc(s)}">{esc(t)}</a></li>' for s, t, _i in ALL_CATEGORIES if buckets.get(s))
    visible = (
        f"<h1>All Football Jerseys — {len(products)} in stock</h1>"
        "<p>Every football jersey we sell, dispatched in 24-48 hours across India. Buy any 2 and get ₹100 off.</p>"
        f"<h2>Browse by style</h2><ul>{cats}</ul><ul>{lis}</ul>"
    )
    itemlist = {
        "@context": "https://schema.org", "@type": "ItemList",
        "name": "All football jerseys", "numberOfItems": len(products),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": f"{SITE}/jersey/{p['slug']}", "name": p["name"]}
            for i, p in enumerate(products[:100])
        ],
    }
    return render(
        shell,
        title=f"All Football Jerseys — {len(products)} Kits in Stock | Visions Jersey",
        desc=f"Browse all {len(products)} football jerseys from ₹520. Retro, polo collar, full sleeve. Dispatched in 24-48 hours across India.",
        canonical=SITE + "/shop",
        jsonld=[itemlist, breadcrumb_jsonld([("Home", SITE), ("All jerseys", SITE + "/shop")])],
        visible=visible,
    )


def image_sitemap(products):
    body = ""
    for p in products:
        if not p["images"]:
            continue
        imgs = "".join(
            f"<image:image><image:loc>{esc(u)}</image:loc>"
            f"<image:caption>{esc(alt_text(p, i))}</image:caption></image:image>"
            for i, u in enumerate(p["images"])
        )
        body += f"<url><loc>{esc(SITE)}/jersey/{esc(p['slug'])}</loc>{imgs}</url>"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">' + body + "</urlset>")


def sitemap_index():
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{SITE}/sitemap-pages.xml</loc></sitemap>"
            f"<sitemap><loc>{SITE}/sitemap-images.xml</loc></sitemap>"
            "</sitemapindex>")


# ---------------------------------------------------------------- report

def build_report(products, buckets, issues):
    rows = "".join(
        f"<tr><td>{esc(p['name'])}</td><td>{len(meta_description(p))}</td>"
        f"<td>{len(p['images'])}</td><td>{'yes' if p['price'] else '<b style=color:#c00>no</b>'}</td>"
        f"<td><a href='/jersey/{esc(p['slug'])}' target=_blank>open</a></td></tr>"
        for p in products
    )
    cats = "".join(f"<tr><td>{esc(t)}</td><td>{len(buckets.get(s, []))}</td>"
                   f"<td><a href='/c/{esc(s)}' target=_blank>/c/{esc(s)}</a></td></tr>"
                   for s, t, _i in ALL_CATEGORIES)
    probs = "".join(f"<li>{esc(i)}</li>" for i in issues) or "<li>None found.</li>"
    return f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content=noindex>
<title>SEO status — Visions Jersey</title>
<style>body{{font:14px/1.6 system-ui;margin:0;padding:28px;background:#fafafa;color:#111}}
h1{{margin:0 0 4px}}.m{{color:#666;margin:0 0 24px}}
.cards{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:26px}}
.c{{background:#fff;border:1px solid #e5e5e5;border-radius:12px;padding:16px 20px;min-width:140px}}
.c b{{display:block;font-size:26px}}.c span{{color:#666;font-size:12px}}
table{{border-collapse:collapse;width:100%;background:#fff;margin-bottom:26px}}
th,td{{border:1px solid #e5e5e5;padding:7px 10px;text-align:left;font-size:13px}}
th{{background:#f4f4f4}}h2{{margin:26px 0 10px;font-size:16px}}</style>
<h1>SEO status</h1>
<p class=m>Rebuilt {datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M')} UTC · every deploy regenerates this</p>
<div class=cards>
  <div class=c><b>{len(products)}</b><span>product pages</span></div>
  <div class=c><b>{len(buckets)}</b><span>category pages</span></div>
  <div class=c><b>{len(products) + len(buckets) + 2}</b><span>URLs in sitemap</span></div>
  <div class=c><b>{len(issues)}</b><span>issues</span></div>
</div>
<h2>Issues</h2><ul>{probs}</ul>
<h2>Categories</h2><table><tr><th>Category</th><th>Products</th><th>URL</th></tr>{cats}</table>
<h2>Products</h2><table><tr><th>Title</th><th>Desc length</th><th>Images</th><th>Price</th><th></th></tr>{rows}</table>
"""


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="visions-shop-site")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    shell_path = os.path.join(args.src, "storefront-index.html")
    if not os.path.exists(shell_path):
        sys.exit(f"Not found: {shell_path}")
    shell = open(shell_path, encoding="utf-8").read()

    print("Fetching products…")
    products = fetch_products()
    print(f"  {len(products)} products")
    buckets = categorise(products)

    # copy the site across untouched
    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    shutil.copytree(args.src, args.out)
    shutil.copy(shell_path, os.path.join(args.out, "index.html"))

    cat_of = {}
    for slug, title, _p, _i in CATEGORIES:
        for p in buckets.get(slug, []):
            cat_of.setdefault(p["slug"], (slug, title))

    for p in products:
        cat = cat_of.get(p["slug"])
        # Related = up to 6 others in the same category, so every product page
        # links sideways and no product is a dead end.
        pool = buckets.get(cat[0], []) if cat else products
        related = [r for r in pool if r["slug"] != p["slug"]][:6]
        d = os.path.join(args.out, "jersey", p["slug"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(product_html(shell, p, related, cat))

    for slug, title, intro in ALL_CATEGORIES:
        items = buckets.get(slug) or []
        if not items:
            continue
        d = os.path.join(args.out, "c", slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(category_html(shell, slug, title, items, intro))

    live = {k: v for k, v in buckets.items() if v}

    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(home_html(shell, products, live))
    os.makedirs(os.path.join(args.out, "shop"), exist_ok=True)
    with open(os.path.join(args.out, "shop", "index.html"), "w", encoding="utf-8") as fh:
        fh.write(shop_html(shell, products, live))

    price_live = []
    global _PRICE_LIVE
    for slug, cap, title, intro in PRICE_PAGES:
        items = sorted([p for p in products if p["price"] and float(p["price"]) < cap],
                       key=lambda p: float(p["price"]))
        if len(items) < 6:
            continue          # too thin to be worth a page
        price_live.append(slug)
        _PRICE_LIVE = price_live
        d = os.path.join(args.out, "p", slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(price_html(shell, slug, cap, title, intro, items))

    with open(os.path.join(args.out, "sitemap-pages.xml"), "w", encoding="utf-8") as fh:
        fh.write(sitemap(products, live))
    with open(os.path.join(args.out, "sitemap-images.xml"), "w", encoding="utf-8") as fh:
        fh.write(image_sitemap(products))
    with open(os.path.join(args.out, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write(sitemap_index())

    # Real files must win; the SPA fallback only catches what isn't generated.
    with open(os.path.join(args.out, "_redirects"), "w", encoding="utf-8") as fh:
        fh.write("/jersey/*  /jersey/:splat/index.html  200\n"
                 "/c/*       /c/:splat/index.html       200\n"
                 "/shop      /shop/index.html           200\n"
                 "/p/*       /p/:splat/index.html       200\n"
                 "/*         /index.html                200\n")

    issues = []
    titles = Counter(p["name"] for p in products)
    for name, n in titles.items():
        if n > 1:
            issues.append(f"Duplicate product title ({n}×): {name}")
    for p in products:
        if not p["price"]:
            issues.append(f"No price, so no valid Product schema: {p['name']}")
        if not p["images"]:
            issues.append(f"No image: {p['name']}")
        if len(p["name"]) > 65:
            issues.append(f"Title over 65 chars, will be truncated in search: {p['name'][:60]}…")
    uncategorised = [p for p in products if not any(p in v for v in buckets.values())]
    if uncategorised:
        issues.append(f"{len(uncategorised)} products match no category and are only reachable from the sitemap")

    if args.report:
        with open(os.path.join(args.out, "seo-status.html"), "w", encoding="utf-8") as fh:
            fh.write(build_report(products, {k: v for k, v in buckets.items() if v}, issues))

    print(f"  {len(products)} product pages")
    print(f"  {len([1 for s, _t, _i in ALL_CATEGORIES if buckets.get(s)])} category pages")
    print(f"  {len(issues)} issues")
    print(f"Done → {args.out}")


if __name__ == "__main__":
    main()
