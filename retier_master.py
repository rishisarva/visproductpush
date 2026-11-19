#!/usr/bin/env python3
"""One-off: pull LIVE "Master" kits into Photos -> Review.

The premium re-review (visions_sync.py rereview-premium) only touches MS Retro's
SITE-priced premium set, so "Master" kits that happen to be priced on the normal
list are skipped. This sweep catches those: every published MS- product whose
title says "master" is flipped to vj_review = pending and set to draft (off the
shop), exactly the way rereview-premium does it.

Run with --dry-run first to see the list; without it to do it.
"""
import os
import re
import sys
import time

import visions_sync as vs
import sync_cloud

DRY = "--dry-run" in sys.argv
RX = re.compile(r"\bmaster\b", re.I)   # the 'Master' tier the premium run left behind

woo = vs.Woo(os.environ["WC_URL"], os.environ["WC_KEY"], os.environ["WC_SECRET"])
prods = woo.list_products("MS-")
targets = [p for p in prods
           if p.get("status") == "publish" and RX.search(p.get("name") or "")]
print(f"{len(targets)} live 'Master' kit(s) to pull into Review", flush=True)

pulled = 0
for p in targets:
    name = (p.get("name") or "")[:60]
    if DRY:
        print(f"WOULD PULL  {name}  (#{p.get('id')})", flush=True)
        continue
    try:
        sync_cloud.review_mark(p.get("sku"), "pending")   # flip its live/approved row back to pending
        woo.call("PUT", f"/products/{p['id']}",
                 json={"status": "draft", "stock_status": "outofstock"})   # off the shop
        pulled += 1
        print(f"PULLED      {name}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {p.get('sku')}: {exc}", flush=True)
    time.sleep(0.3)

print(f"Done — {'would pull' if DRY else 'pulled'} "
      f"{len(targets) if DRY else pulled} Master kit(s) into Review", flush=True)
