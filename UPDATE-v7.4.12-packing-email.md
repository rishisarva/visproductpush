# v7.4.12 — the new 24-hour "still on track" email

## What this adds
About **a day after someone pays**, if their order **still hasn't shipped**, they now
get one short, friendly email: *"Packed and on its way out — still on track to ship
very soon."* It keeps customers calm during the normal wait before dispatch.

It only ever sends **once per order**, and it **skips** any order that has already
shipped, is completed, was refunded/cancelled, or has no email address. So it can't
clash with your existing "Order confirmed" or "Shipped" emails — nobody gets
"it's packed" after they've already been told "it's shipped."

## How to go live (this does NOT happen automatically)
Pushing to GitHub is only a backup — it does **not** update your live site.
To actually turn it on:

1. In WordPress, go to **Plugins → Add New → Upload Plugin** (or replace the file via
   your usual method), and upload the new **`visions-jersey-push.php`** (v7.4.12).
2. That's it — it starts working the moment the new version is active.

## How to safely test it on ONE order (no risk to customers)
The email fires automatically ~24h after payment, so to see it sooner without waiting:

**Easiest safe test — send one to yourself:**
1. Place a small real test order on the shop using **your own email**, and pay it
   (or pick one of your own recent paid orders that hasn't shipped yet).
2. Temporarily shorten the 24-hour wait to 1 hour by visiting this admin link
   (you must be logged into WordPress admin):
   `https://YOUR-WORDPRESS-SITE/wp-admin/?vj_packing_hours=1`
3. Wait ~1 hour. The email lands in your inbox (it goes out on the normal
   per-minute mail cycle). Check the wording and the look.
4. **Put it back to 24 hours** when you're done:
   `https://YOUR-WORDPRESS-SITE/wp-admin/?vj_packing_hours=24`

To turn the whole feature **off** at any time:
   `https://YOUR-WORDPRESS-SITE/wp-admin/?vj_packing_hours=0`
(24 = default, 0 = off. The number is "hours after payment before the email sends.")

Your own test order is the only one affected while testing — real customers' orders
still wait the full 24 hours unless you change the number, and each still only ever
gets this email once.

## Nothing else changed
No existing email, template, or setting was modified. The new email reuses your exact
branded template (logo, order box, progress bar) and the same outbox/Resend sending
path as every other email.
