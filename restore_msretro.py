#!/usr/bin/env python3
"""
Visions Jersey — MS Retro restore v1 (3 Oct 2026)

Puts an MS Retro backup (from backup_msretro.py) back, only where something is
missing or different — running it when nothing was lost changes nothing.

  * vj_review rows   → restored (approved/live status, price type you picked)
  * edited photos    → any photo file that no longer opens is uploaded again
                        from the backup, to the same place
  * vj_shots rows    → restored (which photo sits in which slot)

It does NOT touch WooCommerce: MS Retro products are only ever hidden, never
deleted. Switching supplier.txt back to MS and running the sync republishes them
with these photos.

Usage:  python restore_msretro.py msretro-backup-2026-10-03-1200.zip
Env:    SUPABASE_URL, SUPABASE_KEY (service role)
"""
import json, os, sys, urllib.parse, urllib.request, zipfile

SUPA = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = os.environ.get('SUPABASE_KEY', '')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
UA = {'User-Agent': 'Mozilla/5.0 VisionsJersey-restore/1'}


def say(m): print(m, flush=True)


def call(method, path, body=None, headers=None, raw=None, ctype='application/json'):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(f'{SUPA}{path}', data=data, method=method,
                               headers={**UA, **H, 'Content-Type': ctype, **(headers or {})})
    with urllib.request.urlopen(r, timeout=60) as resp:
        b = resp.read()
        return json.loads(b) if b.strip() else None


def opens(url):
    try:
        r = urllib.request.Request(url, method='HEAD', headers=UA)
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status < 400
    except Exception:  # noqa: BLE001
        return False


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: restore_msretro.py <backup.zip>')
    if not (SUPA and KEY):
        sys.exit('SUPABASE_URL / SUPABASE_KEY missing')
    z = zipfile.ZipFile(sys.argv[1])
    load = lambda n: json.loads(z.read(n).decode('utf-8'))
    man = load('manifest.json')
    say(f"Backup from {man['made']}: {man['review_rows']} review rows, {man['shots_rows']} products with photos, {man['image_files']} photo files")

    review = load('vj_review.json')
    for i in range(0, len(review), 200):
        call('POST', '/rest/v1/vj_review?on_conflict=sku', review[i:i + 200],
             {'Prefer': 'resolution=merge-duplicates,return=minimal'})
    say(f'Review rows restored: {len(review)}')

    files = load('images.json')
    marker = '/storage/v1/object/public/'
    reup = 0
    for url, name in files.items():
        if opens(url):
            continue
        if marker not in url:
            say(f'  ! {url[:80]} no longer opens and is not in Supabase storage — kept the backup copy only')
            continue
        path = url.split(marker, 1)[1].split('?')[0]          # bucket/object/path
        ext = os.path.splitext(name)[1].lower()
        ctype = {'.png': 'image/png', '.webp': 'image/webp'}.get(ext, 'image/jpeg')
        call('POST', '/storage/v1/object/' + urllib.parse.quote(path), raw=z.read(name), ctype=ctype,
             headers={'x-upsert': 'true'})
        reup += 1
    say(f'Photo files uploaded again: {reup} (the rest still open fine)')

    shots = load('vj_shots.json')
    for i in range(0, len(shots), 200):
        call('POST', '/rest/v1/vj_shots?on_conflict=product_id', shots[i:i + 200],
             {'Prefer': 'resolution=merge-duplicates,return=minimal'})
    say(f'Photo slots restored for {len(shots)} product(s)')
    say('Done. Now set supplier.txt to MS and run "Sync products" to bring MS Retro back.')


if __name__ == '__main__':
    main()
