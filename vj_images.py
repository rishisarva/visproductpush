#!/usr/bin/env python3
"""
Visions Jersey photo builder — v1.8 (28 Sep 2026) · also publishes the Meta catalogue (meta-products.csv) on Cloudflare with every photo link pointing to Cloudflare JPEGs, so Meta stops downloading full-size photos from Supabase · v1.7: also publishes the product list (shop-products.json) to Cloudflare, so shoppers stop downloading it from Supabase · v1.6: on GitHub: 2 photos at a time + 3 retries when the image proxy refuses (fixes the 124 failed photos) · v1.5: a failed upload now turns the GitHub run RED instead of green · v1.4: runs in the visproductpush repo (GitHub Actions) with its existing SUPABASE_URL / SUPABASE_KEY secrets; downloads full-size originals and falls back to images.weserv.nl when WordPress blocks GitHub (same as image_cdn.py)

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
import csv, getpass, hashlib, io, json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

SUPA = os.environ.get('VJ_SUPA', 'https://amgiihalfdhogzjrxinu.supabase.co')
# Public "anon" key — the same one already in your shop's config.js. Not a secret.
ANON = os.environ.get('VJ_ANON', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFtZ2lpaGFsZmRob2d6anJ4aW51Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODMwMTMwMDYsImV4cCI6MjA5ODU4OTAwNn0.9_Xgmdp79CtMgXUhL4W3PbC2ZXz_eSbJCItIUHZSrvw')
SERVICE = os.environ.get('VJ_SERVICE_KEY', '')   # the repo's existing SUPABASE_KEY secret (GitHub only)
APIKEY = SERVICE or ANON
PROJECT = os.environ.get('VJ_PROJECT', 'vj-images')
PUBLIC = os.environ.get('VJ_PUBLIC', f'https://{PROJECT}.pages.dev')   # where the photos are served from
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


def build(token):
    feed_raw = get(f'{SUPA}/storage/v1/object/public/feeds/shop-products.json')
    feed = json.loads(feed_raw)
    # v1.7: publish an exact copy of the product list on Cloudflare too. The
    # shop and /join read it from there first (Supabase only as a fallback).
    os.makedirs(os.path.join(SITE, 'feeds'), exist_ok=True)
    with open(os.path.join(SITE, 'feeds', 'shop-products.json'), 'wb') as f:
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
    meta_catalogue(feed, photo_map)
    with open(os.path.join(SITE, '_headers'), 'w') as f:
        f.write('/p/*\n  Cache-Control: public, max-age=31536000, immutable\n  Access-Control-Allow-Origin: *\n'
                '/map.json\n  Cache-Control: public, max-age=300\n  Access-Control-Allow-Origin: *\n'
                '/feeds/*\n  Cache-Control: public, max-age=120\n  Access-Control-Allow-Origin: *\n')
    with open(os.path.join(SITE, 'index.html'), 'w') as f:
        f.write('<!doctype html><title>Visions Jersey photos</title><p>Photo files for shop.visionsjersey.com</p>')
    total = sum(len(v) for v in photo_map.values())
    say(f'\nPhotos ready: {total}  (new: {made}, already made: {skipped}, your edited photos used: {edited_used}, failed: {failed})')
    return total


def meta_catalogue(feed, photo_map):
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
    r = subprocess.run(['npx', '--yes', 'wrangler@4', 'pages', 'deploy', SITE, '--project-name', PROJECT,
                        '--branch', 'main', '--commit-dirty=true'],
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
    say('Visions Jersey photo builder v1.8' + (' (GitHub Actions run)' if CI else ' (automatic run)' if AUTO else '') + '\n')
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
