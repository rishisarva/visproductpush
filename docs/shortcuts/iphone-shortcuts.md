# Visions Jersey — iPhone Shortcuts (reference & restore guide)

Documented 2026-10-26 from screenshots, so these can be rebuilt if they're ever deleted.
Secret values are shown **masked** (first chars only). Get the full values from the
dashboard (**More → Settings → iPhone notifications**) — never commit raw tokens.

**Supabase projects used**
- Payments & expense: `https://jkuqcnsquzwcgoxbhjfm.supabase.co`
- Reminders: `https://amgiihalfdhogzjrxinu.supabase.co`  ⚠️ *different project — verify this is current, not an old one.*

Common headers for every Supabase RPC call:
- `apikey: eyJhbGciOiJIUzI1…`  (the project's **anon** key — from config.js / Supabase dashboard)
- `Content-Type: application/json`

---

## 1. PAYMENT✅  — payment verification (bank SMS → mark order paid)

**Trigger (Automation):** When I receive a message where **Message contains `Received Rs.`**
· Automation **ON** · **Confirm Before Run OFF**. (Also accepts share-sheet text; if no input, Ask For Text.)

**Steps**
1. **Get contents of** `https://jkuqcnsquzwcgoxbhjfm.supabase.co/rest/v1/rpc/vj_payment_sms`
   - Method **POST**
   - Body (JSON): `token = f30c0080572bcc…`  ·  `sms = Shortcut Input`
2. **Get Value** for `msg` in Contents of URL
3. **Match** `RINGNOW` in Contents of URL → **Repeat with each item in Matches** →
   **Get contents of** `https://visionsjersey.com/wp-json/vj/v1/tick` (GET)  *(nudges the plugin to import the order now)*
4. **Match** `VJNOTE` (Case Sensitive) → **Repeat** → Get file from Shortcuts at `shopify_sale_sound.mp3` → **Play sound**  *(sale sound)*
5. **Match** `VJNOTE` (Case Sensitive) → **Repeat** → **Show notification** (Dictionary Value, Play Sound)

> So `vj_payment_sms` parses the bank SMS, matches it to a pending order by its exact amount, marks it paid,
> and returns `msg` plus flags `RINGNOW` (tick the site) and `VJNOTE` (notify + sale sound).

---

## 2. Expense — log a business expense

**Steps**
1. **Choose from menu** "Which expense?": `Essentials` / `Lifestyle` / `Personal` (each branch just sets Text = that label; the chosen label = **Menu Result**)
2. **Ask for Number** "Amount" (= **Provided Input**)
3. **Get contents of** `https://jkuqcnsquzwcgoxbhjfm.supabase.co/rest/v1/rpc/vj_add_expense`
   - Method **POST**
   - Body (JSON): `p_key = 5cb9f6a4d1314e…`  ·  `p_cat = Menu Result`  ·  `p_amount = Provided Input`

---

## 3. Vj Reminders — scheduled reminder notification

**Trigger:** Run Shortcut automation — **At 12:35 PM, Run "Vj Reminders"**.

**Steps**
1. **Get contents of** `https://amgiihalfdhogzjrxinu.supabase.co/rest/v1/rpc/vj_notify`
   - Method **POST**
   - Body (JSON): `token = f30c0080572bcc…`
2. **Get Value** for `summary` in Contents of URL
3. **If** Dictionary Value has any value → **Show notification** (Dictionary Value, Play Sound) · Otherwise · End If

---

## Reship context (the gap we're fixing)

- A returned parcel's order page tells the customer to raise a ticket (`/ticket?o=<ref>&c=delivery`) and **pay ₹80** reshipping.
- The customer pays ₹80 by UPI with note **`Reship <ref>`** (e.g. `Reship VJ-L9KXM5`).
- `vj_payment_sms` matches payments by each order's **unique amount**, but reship is a flat ₹80 → **no match**, so reships don't surface in the dashboard.
- `<ref>` (e.g. `VJ-L9KXM5`) = storefront `shop_orders.ref` → WooCommerce via plugin meta **`_vj_ref`** and the order note "Storefront reference: …".
- Fix in progress: detect a reship payment and show that order **pinned with a red "RESHIP" label** in the dashboard Orders tab.
