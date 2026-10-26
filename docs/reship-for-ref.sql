-- =====================================================================
-- Reship add-on #2 — run ONCE in Supabase → SQL Editor (jkuqcnsquzwcgoxbhjfm).
-- 1) lets the customer's order page read reship status by order (any device),
--    so an already-paid reship never asks the customer to pay a second time;
-- 2) backfills the WooCommerce order id onto each reship from shop_orders,
--    so the dashboard can show it in "To order" (this is what finds VJ-L9KXM5's order).
-- Safe to re-run.
-- =====================================================================

create or replace function public.vj_reship_for_ref(p_ref text)
returns table(paid boolean, reshipped boolean, amount numeric)
language sql security definer set search_path = public as $$
  select r.paid, r.reshipped, r.amount
  from public.vj_reship r
  where r.ref = p_ref
  order by r.paid desc, r.created_at desc
  limit 1;
$$;
grant execute on function public.vj_reship_for_ref(text) to anon, authenticated;

-- Fill in the WooCommerce order id (and name) for any reship that doesn't have it yet,
-- matched on the storefront ref. This is what makes VJ-L9KXM5 show in "To order".
update public.vj_reship v
   set woo_order_id = s.woo_order_id,
       customer     = coalesce(v.customer, s.customer->>'name')
  from public.shop_orders s
 where v.ref = s.ref
   and v.woo_order_id is null
   and s.woo_order_id is not null;
