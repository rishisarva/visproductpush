#!/usr/bin/env python3
"""
Visions Jersey — MS Retro stock study v1 (1 Oct 2026)

Runs at the end of every sync (GitHub Actions). Each run:
  1. records, for every LIVE MS Retro product, the exact pieces left per size
     (from the live checker with &full=1) into the Supabase table vj_stock_log;
  2. rewrites docs/stock-study.md — a plain-English report: average pieces per
     size, pieces sold per day, sizes that ran out and what they had a day
     before, and a recommended "hide when ≤ N left" bar.

Env: SUPABASE_URL, SUPABASE_KEY (service role), optional STUDY_URL
     (default https://vj-images.pages.dev/stock), STUDY_MAX (default 150),
     STUDY_DAYS (default 14).
"""
import json, os, statistics, sys, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

SUPA = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = os.environ.get('SUPABASE_KEY', '')
CHECK = os.environ.get('STUDY_URL', 'https://vj-images.pages.dev/stock')
MAXP = int(os.environ.get('STUDY_MAX', '150'))
DAYS = int(os.environ.get('STUDY_DAYS', '14'))
OUT = os.environ.get('STUDY_OUT', 'docs/stock-study.md')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'}
UA = {'User-Agent': 'Mozilla/5.0 VisionsJersey-stock-study/1'}


def say(m): print(m, flush=True)


def req(method, url, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        t = resp.read()
        return json.loads(t) if t.strip() else None


def live_products():
    rows = req('GET', f'{SUPA}/rest/v1/vj_review?select=sku,product_id,name&status=eq.live&order=product_id.asc', headers=H) or []
    out = [{'pid': str(r['product_id']), 'handle': r['sku'][3:], 'name': r.get('name') or ''}
           for r in rows if (r.get('sku') or '').startswith('MS-') and r.get('product_id')]
    return out[:MAXP]


def check(handle):
    try:
        j = req('GET', f'{CHECK}?h={urllib.parse.quote(handle)}&full=1', timeout=45)
        if j and j.get('ok') and isinstance(j.get('qty'), dict):
            return j['qty']
    except Exception:
        pass
    return None


def snapshot(products):
    now = datetime.now(timezone.utc).isoformat()
    rows, missed = [], 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for p, qty in zip(products, pool.map(lambda p: check(p['handle']), products)):
            if qty is None:
                missed += 1
                continue
            for size, n in qty.items():
                if isinstance(n, (int, float)):
                    rows.append({'at': now, 'product_id': p['pid'], 'handle': p['handle'], 'size': size, 'qty': int(n)})
    for i in range(0, len(rows), 500):
        req('POST', f'{SUPA}/rest/v1/vj_stock_log', rows[i:i + 500], headers={**H, 'Prefer': 'return=minimal'})
    say(f'Recorded {len(rows)} size counts for {len(products) - missed} product(s) ({missed} could not be checked)')
    return rows


def history():
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).isoformat()
    rows, off = [], 0
    while True:
        batch = req('GET', f'{SUPA}/rest/v1/vj_stock_log?select=at,product_id,size,qty&at=gte.{urllib.parse.quote(since)}'
                           f'&order=at.asc&limit=5000&offset={off}', headers=H) or []
        rows += batch
        if len(batch) < 5000:
            break
        off += 5000
    return rows


SIZE_ORDER = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '2XL', '3XL', '4XL', '5XL']
size_key = lambda s: (SIZE_ORDER.index(s.upper()) if s.upper() in SIZE_ORDER else 99, s)


def report(rows):
    if not rows:
        return '# MS Retro stock study\n\nNo data yet — the first snapshot is taken on the next sync.\n'
    series = {}          # (product, size) -> [(t, qty)]
    for r in rows:
        t = datetime.fromisoformat(r['at'].replace('Z', '+00:00'))
        series.setdefault((r['product_id'], r['size']), []).append((t, int(r['qty'])))
    for k in series:
        series[k].sort()
    latest_t = max(t for s in series.values() for t, _ in s)
    first_t = min(t for s in series.values() for t, _ in s)
    span_days = max((latest_t - first_t).total_seconds() / 86400, 1 / 24)

    # ---- pieces per size right now (latest snapshot of each product/size)
    now_by_size = {}
    for (pid, size), s in series.items():
        now_by_size.setdefault(size, []).append(s[-1][1])
    # ---- sales per day per size (sum of decreases, ignore restocks) and stockouts
    sold_by_size, stockouts = {}, []          # stockouts: (size, qty 24h before running out)
    for (pid, size), s in series.items():
        sold = 0
        for (t0, q0), (t1, q1) in zip(s, s[1:]):
            if q1 < q0:
                sold += q0 - q1
            if q0 > 0 and q1 == 0:
                before = [q for t, q in s if t <= t1 - timedelta(hours=24)]
                stockouts.append((size, before[-1] if before else q0))
        sold_by_size.setdefault(size, []).append(sold)
    sizes = sorted(now_by_size, key=size_key)

    L = ['# MS Retro stock study', '',
         f'Data: {len(rows):,} size counts, {len({k[0] for k in series})} live products, '
         f'{first_t.strftime("%d %b %H:%M")} → {latest_t.strftime("%d %b %H:%M")} UTC ({span_days:.1f} days).', '',
         '## Pieces left per size right now', '',
         '| Size | Products | Average left | Median | 0 left | 1–2 left | 3–5 left | 6–10 | 11+ |', '|---|---|---|---|---|---|---|---|---|']
    for sz in sizes:
        v = now_by_size[sz]
        b = lambda lo, hi: sum(1 for x in v if lo <= x <= hi)
        L.append(f'| {sz} | {len(v)} | {statistics.mean(v):.1f} | {statistics.median(v):.0f} | {b(0, 0)} | {b(1, 2)} | {b(3, 5)} | {b(6, 10)} | {b(11, 10**9)} |')
    L += ['', '## How fast sizes sell (pieces per product per day, from the counts going down)', '',
          '| Size | Avg pieces sold / day per product | Busiest product / day |', '|---|---|---|']
    for sz in sizes:
        v = [x / span_days for x in sold_by_size.get(sz, [0])]
        L.append(f'| {sz} | {statistics.mean(v):.2f} | {max(v):.1f} |')
    L += ['', '## Sizes that ran out', '']
    if stockouts:
        befores = [b for _, b in stockouts]
        L += [f'{len(stockouts)} size(s) went to 0 in this period. A day before running out they had on average '
              f'**{statistics.mean(befores):.1f} pieces** (median {statistics.median(befores):.0f}, most {max(befores)}).', '']
        by = {}
        for sz, b in stockouts:
            by.setdefault(sz, []).append(b)
        L += ['| Size | Ran out | Pieces a day before (avg / max) |', '|---|---|---|']
        for sz in sorted(by, key=size_key):
            L.append(f'| {sz} | {len(by[sz])} | {statistics.mean(by[sz]):.1f} / {max(by[sz])} |')
        idx = max(0, -(-len(befores) * 9 // 10) - 1)          # the 90th-percentile run-out
        rec = min(10, max(2, int(sorted(befores)[idx])))
        L += ['', f'## Recommended bar: hide a size when **{rec} or fewer** are left', '',
              f'That covers 9 in 10 of the run-outs seen so far (a size with more than {rec} pieces almost never ran out within a day). '
              'To apply it: in index.html change `const LOW_STOCK_HIDE = 2;` to that number. It works live, no sync needed.']
    else:
        L += ['No size has run out yet in this period, so there is nothing to learn from yet. Keep the current bar (2) and check back in a few days.']
    L += ['', f'_Updated {datetime.now(timezone.utc).strftime("%d %b %Y %H:%M")} UTC by stock_study.py — one snapshot per sync run._']
    return '\n'.join(L) + '\n'


def main():
    if not (SUPA and KEY):
        sys.exit('SUPABASE_URL / SUPABASE_KEY missing')
    if 'report' not in sys.argv:
        products = live_products()
        say(f'Live MS Retro products to study: {len(products)}')
        if products:
            snapshot(products)
    rows = history()
    md = report(rows)
    os.makedirs(os.path.dirname(OUT) or '.', exist_ok=True)
    with open(OUT, 'w') as f:
        f.write(md)
    say('\n'.join(md.split('\n')[:14]))
    say(f'Report written to {OUT}')


if __name__ == '__main__':
    main()
