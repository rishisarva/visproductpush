#!/usr/bin/env python3
"""
Visions Jersey — MS Retro backup v1 (3 Oct 2026)

Saves everything needed to bring MS Retro back exactly as it is now:
  * vj_review   — every MS Retro product's review row (approved/live status,
                  the jersey type / price you picked, MS Retro links and photos)
  * vj_shots    — which edited photo sits in which slot, for every MS Retro product
  * the edited photo FILES themselves (downloaded, not just their links)
  * vj_prefs    — dashboard settings
  * WooCommerce — every MS- product (id, name, slug, status, price, photos)

Everything goes into one zip: msretro-backup-YYYY-MM-DD-HHMM.zip
(The GitHub workflow attaches it to a Release, so it is kept forever.)

Env: SUPABASE_URL, SUPABASE_KEY (service role), WC_URL, WC_KEY, WC_SECRET
"""
import json, os, sys, time, urllib.parse, urllib.request, zipfile
from datetime import datetime, timezone

SUPA = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = os.environ.get('SUPABASE_KEY', '')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
UA = {'User-Agent': 'Mozilla/5.0 VisionsJersey-backup/1'}
OUT = os.environ.get('BACKUP_DIR', 'backup')


def say(m): print(m, flush=True)


def get(url, headers=None, raw=False, timeout=60):
    r = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        b = resp.read()
        return b if raw else json.loads(b or b'null')


def rest(path):
    """All rows of a REST query (pages of 1000)."""
    out, off = [], 0
    while True:
        sep = '&' if '?' in path else '?'
        batch = get(f'{SUPA}/rest/v1/{path}{sep}limit=1000&offset={off}', H) or []
        out += batch
        if len(batch) < 1000:
            return out
        off += 1000


def woo_products():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import visions_sync
        woo = visions_sync.Woo(os.environ['WC_URL'], os.environ['WC_KEY'], os.environ['WC_SECRET'])
        keep = ('id', 'name', 'slug', 'sku', 'status', 'regular_price', 'price', 'stock_status', 'images', 'attributes')
        return [{k: p.get(k) for k in keep} for p in woo.list_products('MS-')]
    except Exception as e:  # noqa: BLE001
        say(f'  (WooCommerce list skipped: {type(e).__name__}: {e})')
        return None


def main():
    if not (SUPA and KEY):
        sys.exit('SUPABASE_URL / SUPABASE_KEY missing')
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M')
    os.makedirs(os.path.join(OUT, 'images'), exist_ok=True)

    review = rest('vj_review?select=*&sku=like.MS-*&order=sku.asc')
    ms_ids = {str(r.get('product_id')) for r in review if r.get('product_id')}
    say(f'MS Retro review rows: {len(review)} ({sum(1 for r in review if r.get("status") in ("live", "approved"))} approved/live)')

    shots = [r for r in rest('vj_shots?select=*') if str(r.get('product_id')) in ms_ids]
    say(f'Products with your edited photos: {len(shots)}')

    try:
        prefs = rest('vj_prefs?select=*')
    except Exception as e:  # noqa: BLE001
        prefs = []
        say(f'  (vj_prefs skipped: {type(e).__name__})')

    files, failed = {}, []
    for row in shots:
        pid = str(row.get('product_id'))
        for slot, url in (row.get('shots') or {}).items():
            if not url:
                continue
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or '.jpg'
            if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
                ext = '.jpg'
            name = f'images/{pid}/{slot}{ext}'
            try:
                data = get(url, raw=True, timeout=60)
                os.makedirs(os.path.join(OUT, 'images', pid), exist_ok=True)
                with open(os.path.join(OUT, name), 'wb') as f:
                    f.write(data)
                files[url] = name
            except Exception as e:  # noqa: BLE001
                failed.append(url)
                say(f'  ! could not download {url[:90]} ({type(e).__name__})')
            time.sleep(0.05)
    say(f'Edited photo files saved: {len(files)}' + (f' ({len(failed)} failed)' if failed else ''))

    woo = woo_products()
    if woo is not None:
        say(f'WooCommerce MS Retro products: {len(woo)} ({sum(1 for p in woo if p.get("status") == "publish")} live)')

    def dump(name, obj):
        with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    dump('vj_review.json', review)
    dump('vj_shots.json', shots)
    dump('vj_prefs.json', prefs)
    dump('images.json', files)
    if woo is not None:
        dump('woo_products.json', woo)
    manifest = {'made': stamp, 'review_rows': len(review), 'shots_rows': len(shots),
                'image_files': len(files), 'image_failed': failed, 'woo_products': None if woo is None else len(woo)}
    dump('manifest.json', manifest)

    zname = f'msretro-backup-{stamp}.zip'
    with zipfile.ZipFile(zname, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, names in os.walk(OUT):
            for n in names:
                p = os.path.join(root, n)
                z.write(p, os.path.relpath(p, OUT))
    say(f'Backup written: {zname} ({os.path.getsize(zname) / 1e6:.1f} MB)')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'zip={zname}\ntag=msretro-backup-{stamp}\n')
    if failed:
        say('NOTE: some photos could not be downloaded — see manifest.json')


if __name__ == '__main__':
    main()
