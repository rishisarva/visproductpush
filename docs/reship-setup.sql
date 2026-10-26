-- =====================================================================
-- Visions Jersey · Reship payments (Option A) — SELF-CONTAINED
-- Run ONCE in Supabase → SQL Editor  (project jkuqcnsquzwcgoxbhjfm).
-- Does NOT touch vj_payment_sms or any existing object. Safe to re-run.
-- Before running: replace <<PUT-YOUR-PAYMENT-TOKEN-HERE>> near the bottom
-- with the same token your PAYMENT shortcut sends (the f30c0080572bcc… one).
-- =====================================================================

create table if not exists public.vj_reship (
  id           bigint generated always as identity primary key,
  ref          text not null,                   -- storefront order ref (shop_orders.ref), e.g. VJ-L9KXM5
  woo_order_id text,                             -- filled from shop_orders when known
  customer     text,                             -- name for the dashboard
  amount       numeric(8,2) not null,            -- unique payable amount, ~80.xx
  paid         boolean not null default false,
  reshipped    boolean not null default false,   -- you set this once you've reshipped
  created_at   timestamptz not null default now(),
  paid_at      timestamptz
);

-- one OPEN (unpaid) reship per order, and a unique amount among unpaid ones
create unique index if not exists vj_reship_open_ref on public.vj_reship (ref)    where not paid;
create unique index if not exists vj_reship_open_amt on public.vj_reship (amount) where not paid;

alter table public.vj_reship enable row level security;

-- Dashboard (admins on shop_admins) can read + update. Same admin check your other tables use.
drop policy if exists "vj_reship admin read"  on public.vj_reship;
create policy "vj_reship admin read"  on public.vj_reship for select to authenticated
  using (exists (select 1 from public.shop_admins a where lower(a.email) = lower(auth.jwt()->>'email')));
drop policy if exists "vj_reship admin write" on public.vj_reship;
create policy "vj_reship admin write" on public.vj_reship for update to authenticated
  using (exists (select 1 from public.shop_admins a where lower(a.email) = lower(auth.jwt()->>'email')))
  with check (true);

-- ---- RPC: start a reship (the shop calls this with the anon key) ---------------
-- Returns the existing open reship for this order, or creates one with a unique ~80.xx amount.
create or replace function public.vj_reship_start(p_ref text)
returns table(id bigint, amount numeric)
language plpgsql security definer set search_path = public as $$
declare v_id bigint; v_amt numeric; v_woo text; v_name text; tries int := 0;
begin
  select r.id, r.amount into v_id, v_amt from public.vj_reship r where r.ref = p_ref and not r.paid limit 1;
  if v_id is not null then return query select v_id, v_amt; return; end if;

  begin   -- customer name + woo id from shop_orders if we have them (customer is jsonb)
    select so.woo_order_id, coalesce(so.customer->>'name', '')
      into v_woo, v_name from public.shop_orders so where so.ref = p_ref limit 1;
  exception when others then v_woo := null; v_name := null; end;

  loop
    tries := tries + 1;
    v_amt := 80 + (floor(random()*99)+1)/100.0;          -- 80.01 .. 80.99
    begin
      insert into public.vj_reship(ref, woo_order_id, customer, amount)
      values (p_ref, v_woo, nullif(v_name,''), v_amt) returning id into v_id;
      return query select v_id, v_amt; return;
    exception when unique_violation then
      if tries > 60 then raise exception 'could not allocate a reship amount'; end if;
    end;
  end loop;
end $$;
grant execute on function public.vj_reship_start(text) to anon, authenticated;

-- ---- RPC: reship state (the pay page polls this; the order page reads it) -------
create or replace function public.vj_reship_state(p_id bigint)
returns table(paid boolean, reshipped boolean, amount numeric, ref text)
language sql security definer set search_path = public as $$
  select r.paid, r.reshipped, r.amount, r.ref from public.vj_reship r where r.id = p_id;
$$;
grant execute on function public.vj_reship_state(bigint) to anon, authenticated;

-- ---- RPC: match a reship payment from the bank SMS -----------------------------
-- Your PAYMENT shortcut adds ONE extra step that POSTs {token, sms} here.
-- It finds the unpaid reship whose unique amount appears in the SMS and marks it paid.
create or replace function public.vj_reship_sms(token text, sms text)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_amt numeric; r public.vj_reship;
begin
  if token is distinct from '<<PUT-YOUR-PAYMENT-TOKEN-HERE>>' then
    return jsonb_build_object('ok', false, 'why', 'bad token');
  end if;
  select (m)[1]::numeric into v_amt
    from regexp_matches(sms, '(?:rs\.?|inr|₹)\s*([0-9]{1,6}\.[0-9]{2})', 'i') as m limit 1;
  if v_amt is null then return jsonb_build_object('ok', false, 'why', 'no amount in sms'); end if;

  update public.vj_reship set paid = true, paid_at = now()
   where amount = v_amt and not paid and not reshipped
   returning * into r;

  if r.id is null then return jsonb_build_object('ok', false, 'why', 'no open reship for that amount', 'amount', v_amt); end if;
  return jsonb_build_object('ok', true, 'VJRESHIP', true, 'ref', r.ref, 'customer', r.customer,
    'msg', 'Reship paid · ' || coalesce(r.customer,'') || ' · ' || r.ref);
end $$;
grant execute on function public.vj_reship_sms(text, text) to anon, authenticated;

-- ---- Pin the order already paid the old way (VJ-L9KXM5, ₹80 on 2026-10-26) -----
-- Marked paid so it shows pinned red in the dashboard right away. (Add the name if you like.)
insert into public.vj_reship(ref, customer, amount, paid, paid_at)
values ('VJ-L9KXM5', 'Suraj Kumar Karakaria', 80.00, true, now())
on conflict do nothing;
