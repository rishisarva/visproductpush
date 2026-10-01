#!/usr/bin/env python3
"""
Visions Jersey photo builder — v1.17 (1 Oct 2026) · checker backs off for 5 minutes when MS Retro says 'too many attempts' (429) instead of retrying on every visit · v1.16: checker reads quantities from BOTH Shopify answers (error 'only add N' or OK with a capped quantity), never mistakes a year in the title for a count, &debug=1 shows MS Retro's raw reply · v1.15: live checker learns exact pieces left from Shopify's cart limit (MS Retro publishes no counts): low sizes (≤2) always, full counts with &full=1 · v1.14: live checker also returns per-size QUANTITIES when MS Retro's data has them (qty) · v1.13: also DELETES removed leftovers (old Thayyil) at the source: shop_products rows + the plugin's Supabase product list and Meta catalogue · v1.12: the product list and Meta catalogue on Cloudflare keep ONLY MS Retro products (removed Thayyil leftovers are dropped) · v1.11: adds the LIVE stock checker (vj-images.pages.dev/stock?h=…, asks MS Retro right now) and tells the shop which MS Retro product each item is · v1.10: product details also come from MS Retro's TAGS (their grey labels), not only the description · v1.9: adds MS Retro's product details (from their description) to the Cloudflare product list, shown on product pages · v1.8: also publishes the Meta catalogue (meta-products.csv) on Cloudflare with every photo link pointing to Cloudflare JPEGs, so Meta stops downloading full-size photos from Supabase · v1.7: also publishes the product list (shop-products.json) to Cloudflare, so shoppers stop downloading it from Supabase · v1.6: on GitHub: 2 photos at a time + 3 retries when the image proxy refuses (fixes the 124 failed photos) · v1.5: a failed upload now turns the GitHub run RED instead of green · v1.4: runs in the visproductpush repo (GitHub Actions) with its existing SUPABASE_URL / SUPABASE_KEY secrets; downloads full-size originals and falls back to images.weserv.nl when WordPress blocks GitHub (same as image_cdn.py)

What it does, every time you run it:
  1. Reads your live product list (the same one the shop uses).
  2. Reads your edited photos from the dashboard (Supabase vj_shots).
  3. For every product photo slot:
       - your EDITED photo is used if you replaced that slot  (edited always wins)
       - otherwise the SUPPLIER photo is used
  4. Makes small, fast WebP copies (card size + product-page size).
  5. Uploads them to your free Cloudflare Pages photo site (vj-images).

Whatever the product sync does later, it can't overwrite your edits here,
because the edits are merged in by this script on every run.

Safe to run again and again: photos already made are skipped, so a re-run
only processes new products and newly edited photos.

Run by hand:            <python> vj_images.py
Turn on automatic mode: <python> vj_images.py --install-auto   (runs every 30 min while the Mac is on)
Turn it off:            <python> vj_images.py --remove-auto
"""
import csv, getpass, html as _html, hashlib, io, json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

SUPA = os.environ.get('VJ_SUPA', 'https://amgiihalfdhogzjrxinu.supabase.co')
# Public "anon" key — the same one already in your shop's config.js. Not a secret.
ANON = os.environ.get('VJ_ANON', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFtZ2lpaGFsZmRob2d6anJ4aW51Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODMwMTMwMDYsImV4cCI6MjA5ODU4OTAwNn0.9_Xgmdp79CtMgXUhL4W3PbC2ZXz_eSbJCItIUHZSrvw')
SERVICE = os.environ.get('VJ_SERVICE_KEY', '')   # the repo's existing SUPABASE_KEY secret (GitHub only)
APIKEY = SERVICE or ANON
PROJECT = os.environ.get('VJ_PROJECT', 'vj-images')
PUBLIC = os.environ.get('VJ_PUBLIC', f'https://{PROJECT}.pages.dev')   # where the photos are served from
MS_STORE = os.environ.get('VJ_MS_STORE', 'https://msretro.com')          # for product details
HOME = os.path.expanduser(os.environ.get('VJ_HOME', '~/vj-images'))
SITE, CACHE = os.path.join(HOME, 'site'), os.path.join(HOME, 'cache')
SESSION = os.path.join(HOME, 'session.json')      # remembered dashboard sign-in (this Mac only)
LASTUP = os.path.join(HOME, 'last-upload.txt')     # what was uploaded last time
LOG = os.path.join(HOME, 'log.txt')
AGENT = os.path.expanduser('~/Library/LaunchAgents/com.visionsjersey.photos.plist')
AUTO = '--auto' in sys.argv or '--ci' in sys.argv
CI = '--ci' in sys.argv          # GitHub Actions: login comes from the VJ_EMAIL / VJ_PASSWORD secrets
PHOTOS = os.path.join(SITE, 'p')
# Card = 840 px wide (420 shown at 2x), product page = 1600 px wide.
SIZES = {'sm': (840, 80), 'lg': (1600, 82), 'mt': (1080, 85)}   # mt = JPEG for the Meta catalogue
EXT = lambda size: 'jpg' if size == 'mt' else 'webp'
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh) VisionsJerseyPhotoBuilder/1'}


def say(msg):
    print(msg, flush=True)
    if AUTO:
        os.makedirs(HOME, exist_ok=True)
        with open(LOG, 'a') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M  ') + str(msg).strip() + '\n')


def need_pillow():
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        say('\nOne-time setup needed. Copy this, press Enter, then run the script again:\n')
        say('   python3 -m venv ~/vj-images/venv && ~/vj-images/venv/bin/pip install pillow\n')
        return False


def get(url, headers=None, timeout=40):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def supa_send(method, path, data=None, ctype='application/json', extra=None):
    '''Write to Supabase with the service key (GitHub runs only).'''
    req = urllib.request.Request(f'{SUPA}{path}', data=data, method=method,
                                 headers={**UA, 'apikey': SERVICE, 'Authorization': f'Bearer {SERVICE}',
                                          'Content-Type': ctype, **(extra or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def purge_leftovers(keep, feed_json=None):
    '''v1.13: delete products that are not MS Retro (old Thayyil) from the plugin's
    Supabase copies, so no page anywhere can show them. Safety: needs a proper
    MS Retro list, and never deletes more than 60% of anything.'''
    if not (CI and SERVICE) or len(keep) < 5:
        return
    try:                                   # 1) the live table the shop re-checks stock against
        rows = json.loads(get(f'{SUPA}/rest/v1/shop_products?select=id', {'apikey': SERVICE, 'Authorization': f'Bearer {SERVICE}'}))
        stale = [str(r['id']) for r in rows if str(r.get('id')) not in keep]
        if stale and len(stale) <= 0.6 * len(rows):
            for i in range(0, len(stale), 100):
                supa_send('DELETE', '/rest/v1/shop_products?id=in.(' + ','.join(stale[i:i + 100]) + ')', extra={'Prefer': 'return=minimal'})
            say(f'Deleted {len(stale)} leftover product(s) from shop_products')
        elif stale:
            say(f'  ! shop_products: {len(stale)} of {len(rows)} rows are not MS Retro — too many, left alone (check it)')
    except Exception as e:
        say(f'  (shop_products clean-up skipped: {type(e).__name__})')
    if feed_json is not None:              # 2) the plugin's product list file on Supabase
        try:
            supa_send('POST', '/storage/v1/object/feeds/shop-products.json', feed_json, 'application/json', {'x-upsert': 'true'})
            say('Supabase product list rewritten without the leftovers')
        except Exception as e:
            say(f'  (Supabase product list not rewritten: {type(e).__name__})')


def post_json(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={**UA, 'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def save_session(d):
    with open(SESSION, 'w') as f:
        json.dump({'refresh_token': d['refresh_token']}, f)
    os.chmod(SESSION, 0o600)   # only your Mac user can read it


def login():
    # 0) GitHub Actions: sign in with the login stored in GitHub's encrypted secrets
    if CI and SERVICE:
        return SERVICE                      # same key image_cdn.py uses — no login needed
    if CI:
        email, pw = os.environ.get('VJ_EMAIL', ''), os.environ.get('VJ_PASSWORD', '')
        if not email or not pw:
            sys.exit('Missing secrets in GitHub: SUPABASE_KEY (already in the visproductpush repo) or VJ_EMAIL / VJ_PASSWORD.')
        try:
            return post_json(f'{SUPA}/auth/v1/token?grant_type=password', {'email': email, 'password': pw}, {'apikey': ANON})['access_token']
        except urllib.error.HTTPError:
            sys.exit('The dashboard login in GitHub secrets is wrong (VJ_EMAIL / VJ_PASSWORD).')
    # 1) remembered sign-in (no password needed)
    if os.path.exists(SESSION):
        try:
            rt = json.load(open(SESSION))['refresh_token']
            d = post_json(f'{SUPA}/auth/v1/token?grant_type=refresh_token', {'refresh_token': rt}, {'apikey': ANON})
            save_session(d)
            return d['access_token']
        except Exception:
            if AUTO:
                sys.exit('Signed out — run the script once by hand to sign in again.')
    if AUTO:
        sys.exit('Not signed in yet — run the script once by hand first.')
    # 2) first time: ask once, then remember
    say('Sign in with your dashboard login (only the first time — this Mac remembers it after).')
    email = input('  Email or username: ').strip()
    if '@' not in email:
        email = ''.join(c for c in email.lower() if c.isalnum() or c in '._-') + '@visionsjersey.app'
    pw = getpass.getpass('  Password: ') if sys.stdin.isatty() else sys.stdin.readline().rstrip('\n')
    try:
        d = post_json(f'{SUPA}/auth/v1/token?grant_type=password', {'email': email, 'password': pw}, {'apikey': ANON})
    except urllib.error.HTTPError:
        sys.exit('Wrong login. Run it again and use the same login as your dashboard.')
    save_session(d)
    return d['access_token']


def full_size(u):
    """WordPress resized copies end in -WxH (photo-300x300.jpg). Start from the
    original, exactly like image_cdn.py does."""
    return re.sub(r"-\d{2,4}x\d{2,4}(?=\.(?:jpe?g|png|webp)(?:\?|$))", "", str(u), flags=re.I)


def fetch_photo(url):
    """Up to 3 tries: the free image proxy refuses when it's asked too fast."""
    last = None
    for wait in (0, 4, 12):
        if wait:
            time.sleep(wait)
        try:
            return fetch_photo_once(url)
        except Exception as e:
            last = e
    raise last


def fetch_photo_once(url):
    """Direct first; if WordPress's bot wall answers instead of the photo (it blocks
    GitHub's servers), go through images.weserv.nl — the same fallback image_cdn.py uses."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            if (r.headers.get('Content-Type') or '').startswith('image'):
                return r.read()
    except Exception:
        pass
    proxy = os.environ.get('VJ_PROXY', 'https://images.weserv.nl/') + '?url=' + urllib.parse.quote(re.sub(r'^https?://', '', url), safe='')
    req = urllib.request.Request(proxy, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        if not (r.headers.get('Content-Type') or '').startswith('image'):
            raise RuntimeError('proxy did not return an image')
        return r.read()


def cached_download(url):
    """Download once, keep it — re-runs never download the same photo again."""
    key = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(CACHE, key)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read()
    data = fetch_photo(url)
    tmp = f'{path}.{os.getpid()}.{id(data)}.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    return data


def make_webp(raw, width, quality, dest, fmt='WEBP'):
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.load()
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert('RGB')
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    tmp = dest + '.tmp'
    if fmt == 'JPEG':
        im.save(tmp, 'JPEG', quality=quality, optimize=True, progressive=True)
    else:
        im.save(tmp, 'WEBP', quality=quality, method=4)
    os.replace(tmp, dest)


_DETAIL_SKIP = re.compile(r'ms\s*retro|whats\s*app|https?:|www\.|\d{10}|size\s*chart|instagram|\bcall\b|contact|\bcod\b|cash on|order now|dm\b', re.I)


STOCK_FUNCTION = r"""// Live stock checker — part of the Visions Jersey photo site.
// GET /stock?h=<ms retro product handle>  ->  {"ok":true,"sizes":{"S":true,"M":false,...}}
// Asks MS Retro (Shopify) right now; each answer is reused for 15 s (browsers never store it).
// MS Retro doesn't publish counts, but Shopify's cart says how many it CAN add when
// asked for too many ("You can only add 2 of this item to your cart"). We ask for
// LOW_PROBE (3): success = at least 3 left (fine to sell); a limit = the exact number
// left. With &full=1 we ask for 9999 to learn the exact count. It's a throwaway cart
// on their side (no order, no email); answers are reused for 2 minutes.
const LOW_PROBE = 3;
let LAST_PROBE = null;                       // for &debug=1
function readCount(text) {                  // the real count in Shopify's message — never a year from the title
  const t = String(text || '');
  const m = t.match(/only\s+(?:add|have|has)\s+(\d+)/i) || t.match(/\ball\s+(\d+)\b/i) || t.match(/\b(\d+)\s+(?:left|in stock|available|items?)\b/i);
  return m ? Number(m[1]) : null;
}
const COOL = new Request('https://vj-images.pages.dev/_qty2/cooldown');   // set for 5 min after a 429
let LIMITED = false;
async function probeQty(variantId, want) {
  const key = new Request('https://vj-images.pages.dev/_qty2/' + variantId + '/' + want);
  const cache = typeof caches !== 'undefined' ? caches.default : null;
  if (cache) { const hit = await cache.match(key); if (hit) { const j = await hit.json(); return j.n; } }
  if (cache && await cache.match(COOL)) { LIMITED = true; return null; }      // MS Retro asked us to slow down
  let n = null;
  try {
    const r = await fetch('https://msretro.com/cart/add.js', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0 (compatible; VisionsJersey-stock/1.0)' },
      body: JSON.stringify({ items: [{ id: variantId, quantity: want }] }),
    });
    const text = await r.text();
    LAST_PROBE = { variant: variantId, asked: want, status: r.status, reply: text.slice(0, 400) };
    let j = {}; try { j = JSON.parse(text); } catch (e) {}
    if (r.ok) {
      // style 2: Shopify added what it had and said OK — the quantity it added is the count
      const items = (j && j.items) || (j && j.id ? [j] : []);
      const it = items.find((x) => String(x.variant_id || x.id) === String(variantId)) || items[0];
      const q = it && typeof it.quantity === 'number' ? it.quantity : null;
      n = q != null && q < want ? q : null;                // less than asked = that's all there is
    } else if (r.status === 422) {
      n = readCount((j && (j.description || j.message)) || text);   // style 1: "You can only add 12 …"
    } else if (r.status === 429) {
      LIMITED = true;                                               // back off for 5 minutes
      if (cache) { try { await cache.put(COOL, new Response('1', { headers: { 'Cache-Control': 'public, max-age=300' } })); } catch (e) {} }
    }
  } catch (e) { LAST_PROBE = { variant: variantId, asked: want, error: String(e) }; }
  if (cache && n != null) { try { await cache.put(key, new Response(JSON.stringify({ n }), { headers: { 'Cache-Control': 'public, max-age=120' } })); } catch (e) {} }
  return n;
}

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const h = (url.searchParams.get('h') || '').trim().toLowerCase();
  const head = { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json; charset=utf-8' };
  if (!/^[a-z0-9][a-z0-9_-]{0,250}$/.test(h)) {
    return new Response(JSON.stringify({ ok: false, error: 'bad handle' }), { status: 400, headers: head });
  }
  try {
    const r = await fetch('https://msretro.com/products/' + h + '.js', {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; VisionsJersey-stock/1.0)', 'Accept': 'application/json' },
      cf: { cacheTtl: 15, cacheEverything: true },
    });
    if (r.status === 404) {
      return new Response(JSON.stringify({ ok: true, gone: true, sizes: {} }), { headers: { ...head, 'Cache-Control': 'no-store' } });
    }
    if (!r.ok) throw new Error('status ' + r.status);
    const p = await r.json();
    const sizes = {}, qty = {}, full = url.searchParams.get('full') === '1';
    const probes = [];
    for (const v of p.variants || []) {
      const name = String(v.option1 || '').trim();     // same as the sync: option 1 is the size
      if (!name) continue;
      sizes[name] = !!v.available || !!sizes[name];
      if (typeof v.inventory_quantity === 'number' && v.inventory_management === 'shopify' && v.inventory_policy !== 'continue') {
        qty[name] = (qty[name] || 0) + Math.max(0, v.inventory_quantity);          // if the store ever publishes counts
      } else if (v.available && v.inventory_management === 'shopify') {
        probes.push(probeQty(v.id, full ? 9999 : LOW_PROBE).then((n) => { if (n != null) qty[name] = (qty[name] || 0) + n; }));
      } else if (!v.available && v.inventory_management === 'shopify') {
        qty[name] = qty[name] || 0;
      }
    }
    await Promise.all(probes);
    const body = { ok: true, sizes, qty, at: Date.now() };
    if (LIMITED) body.limited = true;          // counts paused: MS Retro said "too many attempts"
    LIMITED = false;
    if (url.searchParams.get('debug') === '1') body.debug = LAST_PROBE;   // MS Retro's raw reply to the last probe
    return new Response(JSON.stringify(body), { headers: { ...head, 'Cache-Control': 'no-store' } });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false }), { status: 502, headers: head });
  }
}
"""


def write_stock_function():
    d = os.path.join(HOME, 'functions')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'stock.js'), 'w') as f:
        f.write(STOCK_FUNCTION)


def detail_lines(body):
    '''MS Retro description -> short clean lines ("Round neck", "Embroidery logo" ...).'''
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', body or '', flags=re.S | re.I)
    t = re.sub(r'<br\s*/?>|</(p|li|div|h\d|tr)>', '\n', t, flags=re.I)
    t = _html.unescape(re.sub(r'<[^>]+>', ' ', t))
    out, seen = [], set()
    for line in t.split('\n'):
        line = re.sub(r'\s+', ' ', line).strip(' \u2022-\u2013\u00b7*:')
        if not line or len(line) > 160 or _DETAIL_SKIP.search(line) or line.lower() in seen:
            continue
        seen.add(line.lower())
        out.append(line[0].upper() + line[1:])
    return out[:10]


_TAG_SKIP = re.compile(r'^(p\s?\d+|all|new|new arrivals?|sale|hot|best ?sellers?|trending|featured|top|popular|'
                       r'football|football jerseys?|jerseys?|retro|retro jerseys?|club|clubs?|country|national|kit|kits|ms ?retro.*)$', re.I)


def tag_lines(tags):
    '''MS Retro's tags ("Round neck", "half sleeve", "Embroidery logo" ...) -> detail lines.'''
    if isinstance(tags, str):
        tags = tags.split(',')
    out = []
    for t in tags or []:
        t = re.sub(r'\s+', ' ', str(t)).strip(' \u2022-\u2013\u00b7*:')
        if not t or len(t) > 60 or _TAG_SKIP.match(t) or _DETAIL_SKIP.search(t):
            continue
        out.append(t[0].upper() + t[1:])
    return out


def ms_details(token):
    '''{shop product id: [detail lines]} for MS Retro products (label MS-).'''
    try:
        rows = json.loads(get(f'{SUPA}/rest/v1/vj_review?select=sku,product_id',
                              {'apikey': APIKEY, 'Authorization': f'Bearer {token}'}))
        by_handle = {r['sku'][3:]: str(r['product_id']) for r in rows
                     if (r.get('sku') or '').startswith('MS-') and r.get('product_id')}
        if not by_handle:
            return {}
        out = {}
        for page in range(1, 9):
            batch = json.loads(get(f'{MS_STORE}/products.json?limit=250&page={page}')).get('products', [])
            for prod in batch:
                pid = by_handle.get(prod.get('handle', ''))
                lines = []
                if pid:
                    seen = set()
                    for line in tag_lines(prod.get('tags')) + detail_lines(prod.get('body_html', '')):
                        if line.lower() not in seen:
                            seen.add(line.lower()); lines.append(line)
                    lines = lines[:10]
                if lines:
                    out[pid] = lines
            if len(batch) < 250:
                break
        return out
    except Exception as e:   # details are a nice-to-have: never block photos
        say(f'  (product details skipped: {type(e).__name__})')
        return {}


def ms_handles(token):
    '''{shop product id: MS Retro handle} for MS Retro products (label MS-).'''
    try:
        rows = json.loads(get(f'{SUPA}/rest/v1/vj_review?select=sku,product_id',
                              {'apikey': APIKEY, 'Authorization': f'Bearer {token}'}))
        return {str(r['product_id']): r['sku'][3:] for r in rows
                if (r.get('sku') or '').startswith('MS-') and r.get('product_id')}
    except Exception as e:
        say(f'  (MS Retro product links skipped: {type(e).__name__})')
        return {}


def build(token):
    feed_raw = get(f'{SUPA}/storage/v1/object/public/feeds/shop-products.json')
    feed = json.loads(feed_raw)
    # v1.7: publish a copy of the product list on Cloudflare too. The shop and
    # /join read it from there first (Supabase only as a fallback).
    # v1.9: MS Retro products get a "details" list (from their description),
    # shown above the sizes on the product page. Everything else is unchanged.
    details = ms_details(token)
    handles = ms_handles(token)          # v1.11: lets the product page ask MS Retro for live stock
    if handles:
        # v1.12: only MS Retro products are real products now. Anything else in the
        # plugin's list is a removed leftover (old Thayyil) — drop it everywhere
        # (product list, photos, Meta catalogue) so nobody can see or order it.
        before = len(feed)
        feed = [p for p in feed if str(p.get('id', '')) in handles]
        feed_raw = json.dumps(feed, ensure_ascii=False, separators=(',', ':')).encode()
        removed = before - len(feed)
        if removed:
            say(f'Removed {removed} product(s) that are not MS Retro (old leftovers)')
        # v1.13: and delete them at the source (only if it isn't most of the list)
        purge_leftovers(set(handles), feed_raw if removed and removed <= 0.6 * before else None)
    os.makedirs(os.path.join(SITE, 'feeds'), exist_ok=True)
    with open(os.path.join(SITE, 'feeds', 'shop-products.json'), 'wb') as f:
        if details or handles:
            copy = json.loads(feed_raw)
            for p in copy:
                d = details.get(str(p.get('id', '')))
                if d:
                    p['details'] = d
                h = handles.get(str(p.get('id', '')))
                if h:
                    p['ms'] = h
            f.write(json.dumps(copy, ensure_ascii=False, separators=(',', ':')).encode())
            say(f'Product details added for {sum(1 for p in copy if p.get("details"))} MS Retro product(s)')
            say(f'Live stock check ready for {sum(1 for p in copy if p.get("ms"))} MS Retro product(s)')
        else:
            f.write(feed_raw)
    say(f'Products in the shop: {len(feed)}')
    rows = json.loads(get(f'{SUPA}/rest/v1/vj_shots?select=product_id,shots',
                          {'apikey': APIKEY, 'Authorization': f'Bearer {token}'}))
    edits = {str(r['product_id']): (r.get('shots') or {}) for r in rows}
    n_edit_slots = sum(1 for s in edits.values() for v in s.values() if v)
    say(f'Edited photo slots found in the dashboard: {n_edit_slots}')

    photo_map, keep, jobs = {}, set(), []
    edited_used = 0
    for p in feed:
        pid = str(p.get('id', ''))
        imgs = [u for u in (p.get('images') or []) if u]
        if not pid or not imgs:
            continue
        mine = edits.get(pid, {})
        for idx, supplier_url in enumerate(imgs):
            edited_url = mine.get(str(idx))
            src = edited_url or full_size(supplier_url)          # EDITED ALWAYS WINS
            h = hashlib.sha1(src.encode()).hexdigest()[:12]      # new photo -> new name, never stale
            names = {s_: f'{pid}-{idx}-{h}-{s_}.{EXT(s_)}' for s_ in SIZES}
            jobs.append((pid, idx, h, src, names, p.get('name', pid), bool(edited_url)))

    todo = [j_ for j_ in jobs if not all(os.path.exists(os.path.join(PHOTOS, n)) for n in j_[4].values())]
    say(f'Photo slots: {len(jobs)} · already made: {len(jobs) - len(todo)} · to make now: {len(todo)}')

    def work(job):
        pid, idx, h, src, names, name, _ = job
        try:
            raw = cached_download(src)
            for s_, (w, q) in SIZES.items():
                make_webp(raw, w, q, os.path.join(PHOTOS, names[s_]), 'JPEG' if s_ == 'mt' else 'WEBP')
            return True, job, ''
        except Exception as e:
            return False, job, type(e).__name__ + (f' {e.code}' if hasattr(e, 'code') else '')

    failed_ids = set()
    done = 0
    with ThreadPoolExecutor(max_workers=2 if CI else 6) as pool:   # GitHub goes through the proxy: be gentle
        for ok, job, err in pool.map(work, todo):
            done += 1
            if not ok:
                failed_ids.add((job[0], job[1]))
                say(f'  ! could not make photo {job[1] + 1} of {job[5]} ({err}) — the shop will use its backup for it')
            if done % 10 == 0 or done == len(todo):
                say(f'  [{done}/{len(todo)}] made · last: {job[5][:48]}')

    made = len(todo) - len(failed_ids)
    for pid, idx, h, src, names, name, was_edit in jobs:
        if (pid, idx) in failed_ids:
            continue
        keep.update(names.values())
        photo_map.setdefault(pid, {})[str(idx)] = h
        if was_edit:
            edited_used += 1
    skipped, failed = len(jobs) - len(todo), len(failed_ids)

    # Remove photos that no longer belong to any product (keeps the site small and tidy).
    for f in os.listdir(PHOTOS):
        if (f.endswith('.webp') or f.endswith('-mt.jpg')) and f not in keep:
            os.remove(os.path.join(PHOTOS, f))

    with open(os.path.join(SITE, 'map.json'), 'w') as f:
        json.dump(photo_map, f, separators=(',', ':'))
    meta_catalogue(feed, photo_map, keep={str(p.get('id', '')) for p in feed} if handles else None)
    with open(os.path.join(SITE, '_headers'), 'w') as f:
        f.write('/p/*\n  Cache-Control: public, max-age=31536000, immutable\n  Access-Control-Allow-Origin: *\n'
                '/map.json\n  Cache-Control: public, max-age=300\n  Access-Control-Allow-Origin: *\n'
                '/feeds/*\n  Cache-Control: public, max-age=120\n  Access-Control-Allow-Origin: *\n')
    with open(os.path.join(SITE, 'index.html'), 'w') as f:
        f.write('<!doctype html><title>Visions Jersey photos</title><p>Photo files for shop.visionsjersey.com</p>')
    total = sum(len(v) for v in photo_map.values())
    say(f'\nPhotos ready: {total}  (new: {made}, already made: {skipped}, your edited photos used: {edited_used}, failed: {failed})')
    return total


def meta_catalogue(feed, photo_map, keep=None):
    """v1.8: a copy of the Meta catalogue file where every photo link that we
    have on Cloudflare points to Cloudflare (a JPEG, your edited photo if you
    made one) instead of a full-size original on Supabase or WordPress.
    Links we can't match are left exactly as they were."""
    try:
        raw = get(f'{SUPA}/storage/v1/object/public/feeds/meta-products.csv')
    except Exception as e:  # no catalogue file: nothing to do
        say(f'  (Meta catalogue not copied: {type(e).__name__})')
        return
    by_url = {}
    for p in feed:
        pid = str(p.get('id', ''))
        for idx, u in enumerate([u for u in (p.get('images') or []) if u]):
            by_url.setdefault(u, (pid, idx)); by_url.setdefault(full_size(u), (pid, idx))
    swapped = total = 0
    def swap(u):
        nonlocal swapped, total
        u = u.strip()
        if not u:
            return u
        total += 1
        k = by_url.get(u) or by_url.get(full_size(u))
        h = photo_map.get(k[0], {}).get(str(k[1])) if k else None
        if not h:
            return u
        swapped += 1
        return f'{PUBLIC}/p/{k[0]}-{k[1]}-{h}-mt.jpg'
    text = raw.decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return
    head = rows[0]
    if keep is not None and 'id' in head:          # v1.12: only current (MS Retro) products
        idc = head.index('id')
        dropped = len(rows) - 1
        rows = [head] + [r for r in rows[1:] if idc < len(r) and r[idc].strip() in keep]
        dropped -= len(rows) - 1
        if dropped:
            say(f'Meta catalogue: removed {dropped} product(s) that are not MS Retro')
            if CI and SERVICE and dropped <= 0.6 * (dropped + len(rows) - 1):   # v1.13: fix the Supabase copy too
                try:
                    out0 = io.StringIO(); csv.writer(out0, lineterminator='\n').writerows(rows)
                    supa_send('POST', '/storage/v1/object/feeds/meta-products.csv', out0.getvalue().encode('utf-8'), 'text/csv', {'x-upsert': 'true'})
                    say('Supabase Meta catalogue rewritten without the leftovers')
                except Exception as e:
                    say(f'  (Supabase Meta catalogue not rewritten: {type(e).__name__})')
    cols = [i for i, c in enumerate(head) if c in ('image_link', 'additional_image_link')]
    for r in rows[1:]:
        for i in cols:
            if i < len(r):
                r[i] = ','.join(swap(u) for u in r[i].split(',') if u.strip())
    os.makedirs(os.path.join(SITE, 'feeds'), exist_ok=True)
    out = io.StringIO(); csv.writer(out, lineterminator='\n').writerows(rows)
    with open(os.path.join(SITE, 'feeds', 'meta-products.csv'), 'w', encoding='utf-8') as f:
        f.write(out.getvalue())
    say(f'Meta catalogue: {swapped} of {total} photo links now point to Cloudflare')


def fingerprint():
    files = sorted(os.listdir(PHOTOS))
    feed = b''
    for name in ('shop-products.json', 'meta-products.csv'):
        fp = os.path.join(SITE, 'feeds', name)
        feed += open(fp, 'rb').read() if os.path.exists(fp) else b''
    feed += STOCK_FUNCTION.encode()      # a new checker version also counts as "something new"
    return hashlib.sha1((open(os.path.join(SITE, 'map.json')).read() + '|'.join(files)).encode() + feed).hexdigest()


def deploy():
    fp = fingerprint()
    if os.path.exists(LASTUP) and open(LASTUP).read().strip() == fp:
        say('Nothing new since the last upload — Cloudflare is already up to date.')
        return True
    if CI and not (os.environ.get('CLOUDFLARE_API_TOKEN') and os.environ.get('CLOUDFLARE_ACCOUNT_ID')):
        sys.exit('Missing CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID secrets in GitHub.')
    if not shutil.which('npx'):
        say('\nAlmost done — uploading needs Node.js (free). Install the LTS version from https://nodejs.org ,')
        say('then run this script again. Your photos are already made and will not be rebuilt.')
        return False
    say('\nUploading to Cloudflare… (the first time, a browser window opens: log in to Cloudflare and click Allow)')
    subprocess.run(['npx', '--yes', 'wrangler@4', 'pages', 'project', 'create', PROJECT, '--production-branch', 'main'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # already exists? that's fine
    write_stock_function()
    r = subprocess.run(['npx', '--yes', 'wrangler@4', 'pages', 'deploy', SITE, '--project-name', PROJECT,
                        '--branch', 'main', '--commit-dirty=true'], cwd=HOME,
                       stdin=subprocess.DEVNULL if AUTO else None,
                       stdout=open(LOG, 'a') if (AUTO and not CI) else None, stderr=subprocess.STDOUT if (AUTO and not CI) else None)
    if r.returncode != 0:
        say('\nUpload did not finish. Send Claude a screenshot of the lines above.')
        return False
    with open(LASTUP, 'w') as f:
        f.write(fp)
    say(f'\nDONE. Your photo site: https://{PROJECT}.pages.dev  (if Cloudflare gave it a different name, it is printed above)')
    say('Send that address to Claude. Run this script again whenever you add products or edit photos.')
    return True


def install_auto():
    if not os.path.exists(SESSION):
        sys.exit('First run the script once normally (to sign in), then run it with --install-auto.')
    py, me = sys.executable, os.path.abspath(__file__)
    path = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.visionsjersey.photos</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{me}</string><string>--auto</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{os.path.join(HOME, 'auto-output.txt')}</string>
  <key>StandardErrorPath</key><string>{os.path.join(HOME, 'auto-output.txt')}</string>
</dict></plist>
"""
    os.makedirs(os.path.dirname(AGENT), exist_ok=True)
    subprocess.run(['launchctl', 'unload', AGENT], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    with open(AGENT, 'w') as f:
        f.write(plist)
    subprocess.run(['launchctl', 'load', AGENT])
    say('Automatic mode is ON: every 30 minutes (while this Mac is on) new products and edited photos go to Cloudflare by themselves.')
    say(f'What it did is written to: {LOG}')


def remove_auto():
    subprocess.run(['launchctl', 'unload', AGENT], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    if os.path.exists(AGENT):
        os.remove(AGENT)
    say('Automatic mode is OFF.')


def main():
    say('Visions Jersey photo builder v1.17' + (' (GitHub Actions run)' if CI else ' (automatic run)' if AUTO else '') + '\n')
    if '--install-auto' in sys.argv:
        return install_auto()
    if '--remove-auto' in sys.argv:
        return remove_auto()
    if not need_pillow():
        return
    for d in (SITE, CACHE, PHOTOS):
        os.makedirs(d, exist_ok=True)
    token = login()
    if build(token) == 0:
        sys.exit('No photos were made — send Claude a screenshot of this window.')
    if '--no-upload' not in sys.argv:
        if not deploy() and CI:
            sys.exit('UPLOAD TO CLOUDFLARE FAILED — the shop is still showing the previous photos. Send Claude the lines above.')


if __name__ == '__main__':
    main()
