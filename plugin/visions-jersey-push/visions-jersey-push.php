<?php
/**
 * Plugin Name: Visions Jersey Push
 * Description: Instant order push notifications + secure issue-email endpoint for the Visions Jersey app.
 * Version: 7.4.3
 * Author: Visions Jersey
 */

if (!defined('ABSPATH')) exit;

// ----- Settings page: WordPress admin → Settings → VJ Push -----
add_action('admin_menu', function () {
    add_options_page('VJ Push', 'VJ Push', 'manage_options', 'vj-push', 'vj_push_settings_page');
});

add_action('admin_init', function () {
    register_setting('vj_push_group', 'vj_push_tokens');
    register_setting('vj_push_group', 'vj_email_secret');
});

function vj_push_settings_page() { ?>
    <div class="wrap">
        <h1>Visions Jersey Push</h1>
        <form method="post" action="options.php">
            <?php settings_fields('vj_push_group'); ?>
            <h3>Push tokens</h3>
            <p>App → Settings → Get push token. One per line.</p>
            <textarea name="vj_push_tokens" rows="4" cols="70" placeholder="ExponentPushToken[xxxxxxxx]"><?php
                echo esc_textarea(get_option('vj_push_tokens', ''));
            ?></textarea>
            <h3>Email secret</h3>
            <p>Must match the "Email secret" in the app's Settings. Used to authorise the Announce feature.</p>
            <input type="text" name="vj_email_secret" size="50"
                   value="<?php echo esc_attr(get_option('vj_email_secret', '')); ?>"
                   placeholder="any strong random secret" />
            <?php submit_button('Save'); ?>
        </form>
    </div>
<?php }

// ----- v6.6: private door for the GitHub sync when the host walls off /wp-json/ -----
// The hosting firewall sometimes answers GitHub's servers with its own HTML 401 page on
// /wp-json/… (datacenter IPs). The same WooCommerce REST calls are also reachable here:
//   POST https://visionsjersey.com/?vj_rest=1   header x-vj-secret: <Email secret>
//   JSON body {"method":"GET","path":"/wc/v3/products","query":{...},"body":{...}}
// The request is run inside WordPress (rest_do_request) as the first administrator, and
// the REST answer comes back unchanged with its status code. Only the secret opens it.
add_action('wp_loaded', function () {          // after every plugin (WooCommerce included) has set up
    if (!isset($_GET['vj_rest'])) return;
    nocache_headers();
    header('Content-Type: application/json; charset=utf-8');
    $secret = (string) get_option('vj_email_secret', '');
    $got = isset($_SERVER['HTTP_X_VJ_SECRET']) ? (string) $_SERVER['HTTP_X_VJ_SECRET'] : '';
    if ($secret === '' || $got === '' || !hash_equals($secret, $got)) {
        status_header(403); echo wp_json_encode(array('code' => 'vj_rest_forbidden', 'message' => 'secret missing or wrong')); exit;
    }
    $in = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($in) || empty($in['path']) || strpos($in['path'], '/') !== 0 || strpos($in['path'], '..') !== false) {
        status_header(400); echo wp_json_encode(array('code' => 'vj_rest_bad_request', 'message' => 'need {"method","path"}')); exit;
    }
    $admins = get_users(array('role' => 'administrator', 'number' => 1, 'orderby' => 'ID', 'order' => 'ASC', 'fields' => 'ID'));
    if (empty($admins)) { status_header(500); echo wp_json_encode(array('code' => 'vj_rest_no_admin')); exit; }
    wp_set_current_user((int) $admins[0]);
    $method = strtoupper(isset($in['method']) ? (string) $in['method'] : 'GET');
    if (!in_array($method, array('GET', 'POST', 'PUT', 'PATCH', 'DELETE'), true)) $method = 'GET';
    $req = new WP_REST_Request($method, $in['path']);
    if (!empty($in['query']) && is_array($in['query'])) $req->set_query_params($in['query']);
    if (isset($in['body']) && is_array($in['body'])) { $req->set_body_params($in['body']); $req->set_body(wp_json_encode($in['body'])); $req->set_header('Content-Type', 'application/json'); }
    $res = rest_do_request($req);
    $server = rest_get_server();
    $data = $server->response_to_data($res, false);
    status_header($res->get_status());
    foreach (array('X-WP-Total', 'X-WP-TotalPages') as $h) {
        $hs = $res->get_headers(); if (isset($hs[$h])) header($h . ': ' . $hs[$h]);
    }
    echo wp_json_encode($data);
    exit;
}, 1);

// ----- Instant push on every new order -----
add_action('woocommerce_new_order', 'vj_push_notify_new_order', 20, 1);

// ----- Telegram notifications (for iPhone / anywhere) -----
// Web apps can't receive real push on iOS without heavy infrastructure, so we
// notify through Telegram instead: instant, free, reliable iPhone pushes.
// One-time setup (admin, logged into WP, in a browser):
//   1. In Telegram, talk to @BotFather → /newbot → copy the bot TOKEN.
//   2. Open your new bot in Telegram and press START.
//   3. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates and copy your
//      numeric chat "id" from the JSON.
//   4. Save both here:  /wp-admin/?vj_set_telegram=1&token=<TOKEN>&chat=<ID>
//   5. Test:            /wp-admin/?vj_test_telegram=1
add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_set_telegram'])) {
        $token = isset($_GET['token']) ? sanitize_text_field(wp_unslash($_GET['token'])) : '';
        $chat  = isset($_GET['chat'])  ? sanitize_text_field(wp_unslash($_GET['chat']))  : '';
        if ($token && $chat) {
            update_option('vj_telegram', array('token' => $token, 'chat' => $chat), false);
            wp_die('Telegram saved ✓ — now test with /wp-admin/?vj_test_telegram=1', 'VJ Telegram');
        }
        wp_die('Usage: /wp-admin/?vj_set_telegram=1&token=BOT_TOKEN&chat=CHAT_ID', 'VJ Telegram');
    }
    if (isset($_GET['vj_test_telegram'])) {
        $ok = vj_telegram_send("✅ Test from Visions Jersey — notifications are working!");
        wp_die($ok ? 'Sent ✓ — check Telegram on your iPhone.' : 'Failed — check token/chat id (re-run vj_set_telegram).', 'VJ Telegram');
    }
});

function vj_telegram_send($text) {
    $cfg = get_option('vj_telegram');
    if (empty($cfg['token']) || empty($cfg['chat'])) return false;
    $res = wp_remote_post('https://api.telegram.org/bot' . $cfg['token'] . '/sendMessage', array(
        'timeout' => 8,
        'body' => array('chat_id' => $cfg['chat'], 'text' => $text),
    ));
    return !is_wp_error($res) && wp_remote_retrieve_response_code($res) === 200;
}

// Payment info used in the Telegram payment-request message.
// Change anytime (admin, logged in):
//   /wp-admin/?vj_set_payinfo=1&upi=YOUR@UPI&partial=150
add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_set_payinfo'])) {
        $upi = isset($_GET['upi']) ? sanitize_text_field(wp_unslash($_GET['upi'])) : '';
        $partial = isset($_GET['partial']) ? absint($_GET['partial']) : 150;
        if ($upi) {
            update_option('vj_payinfo', array('upi' => $upi, 'partial' => $partial), false);
            wp_die('Pay info saved ✓ — UPI ' . esc_html($upi) . ', partial ₹' . $partial, 'VJ Pay Info');
        }
        wp_die('Usage: /wp-admin/?vj_set_payinfo=1&upi=name@bank&partial=150', 'VJ Pay Info');
    }
});

// v5.7 — the exact amount the customer must pay. Every storefront order gets its
// own amount (total minus a few paise, e.g. ₹609.37) so the bank SMS alone tells
// which order was paid. Saved on the order at import; older orders fall back to
// the storefront row, then to the plain total.
function vj_pay_amount($order) {
    $m = $order->get_meta('_vj_pay_amount');
    if ($m !== '' && $m !== null && (float) $m > 0) return (float) $m;
    $raw = vj_supa_req('GET', 'shop_orders?select=pay_amount&woo_order_id=eq.' . rawurlencode((string) $order->get_id()) . '&limit=1');
    if (is_string($raw)) {
        $rows = json_decode($raw, true);
        if (is_array($rows) && !empty($rows[0]['pay_amount'])) {
            $order->update_meta_data('_vj_pay_amount', (string) $rows[0]['pay_amount']);
            $order->save();
            return (float) $rows[0]['pay_amount'];
        }
    }
    return (float) $order->get_total();
}
function vj_pay_amount_txt($order) {
    return number_format(vj_pay_amount($order), 2, '.', '');
}

// Build the FULL payment-request message (the exact approved template).
function vj_build_paymsg($order) {
    $pay = get_option('vj_payinfo');
    $upi = !empty($pay['upi']) ? $pay['upi'] : 'iblamerishi@fam';
    $total = (float) $order->get_total();
    $fmt = function ($n) { return rtrim(rtrim(number_format($n, 2, '.', ''), '0'), '.'); };
    $totalTxt = vj_pay_amount_txt($order);   // v5.7: the exact amount (with paise), not the rounded total
    $first = $order->get_billing_first_name();
    $num = $order->get_order_number();
    return "Hi {$first}! 👋⚽\n"
        . "Your order *#{$num}* (₹{$totalTxt}) is ready to ship, but payment is still pending.\n\n"
        . "*⭐ PREPAID — Recommended ✅*\n"
        . "• Pay ₹{$totalTxt} securely via UPI.\n"
        . "• Your order is confirmed automatically within minutes.\n"
        . "• No payment or hassle at delivery.\n"
        . "• Prepaid orders are packed and shipped on priority. 🚀📦\n\n"
        . "💸 UPI: {$upi}\n\n"
        . "Once you've paid, there's nothing else to do — your order is confirmed by itself and we get it packed for dispatch. ✅📦\n\n"
        . "Having any issue with payment? Reply *PROBLEM* and we'll help you out. 🙌\n"
        . "— Visions Jersey";
}

function vj_paymsg_sig($order_id) {
    return substr(hash_hmac('sha256', 'vjpay' . $order_id, wp_salt('auth')), 0, 16);
}

// Copy page: /wp-json/vj/v1/paymsg?order=123&sig=… — shows the full message
// with a big Copy button. Signed link (only the Telegram button knows it).
add_action('rest_api_init', function () {
    register_rest_route('vj/v1', '/paymsg', array(
        'methods' => 'GET',
        'permission_callback' => '__return_true',
        'callback' => function ($req) {
            $oid = absint($req->get_param('order'));
            $sig = sanitize_text_field($req->get_param('sig'));
            if (!$oid || !hash_equals(vj_paymsg_sig($oid), $sig)) {
                return new WP_Error('vj_bad_sig', 'Invalid link.', array('status' => 403));
            }
            $order = wc_get_order($oid);
            if (!$order) return new WP_Error('vj_no_order', 'Order not found.', array('status' => 404));
            $msg = vj_build_paymsg($order);
            header('Content-Type: text/html; charset=utf-8');
            echo '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
               . '<body style="margin:0;background:#0B0B0D;color:#F4F2EC;font-family:-apple-system,sans-serif;padding:18px">'
               . '<button onclick="navigator.clipboard.writeText(document.getElementById(\'m\').innerText).then(()=>{this.innerText=\'✅ Copied — paste in WhatsApp\'})" '
               . 'style="width:100%;padding:16px;font-size:17px;font-weight:800;background:#3DDC84;border:0;border-radius:12px;margin-bottom:14px">📋 Copy message</button>'
               . '<pre id="m" style="white-space:pre-wrap;background:#161619;border:1px solid #26262E;border-radius:12px;padding:14px;font-size:14px;line-height:1.5">'
               . esc_html($msg) . '</pre></body>';
            exit;
        },
    ));
});

// ===== SUPABASE BRIDGE (v3.0) =====
// The site's firewall (StackProtect) blocks the app from calling wp-json —
// but WordPress itself can freely push OUT. So the plugin mirrors every order
// INTO Supabase, and executes app commands (status changes, refunds) queued
// there. The app then never needs wp-json at all.
// One-time setup (admin, logged in):
//   /wp-admin/?vj_set_supabase=1&key=YOUR_SERVICE_ROLE_KEY
// (Supabase dashboard → Project Settings → API → service_role secret)
// Test: /wp-admin/?vj_test_supabase=1

// v7.2: the project address is a setting now (it used to be fixed here), so moving to a
// new Supabase project is one link — address + key are saved together:
//   /wp-admin/?vj_set_supabase=1&url=https://NEWREF.supabase.co&key=NEW_SERVICE_ROLE_KEY
// Until that link is used, it keeps using the original project, exactly as before.
define('VJ_SUPA_URL', rtrim((string) (get_option('vj_supabase_url') ?: 'https://amgiihalfdhogzjrxinu.supabase.co'), '/'));

add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_set_supabase'])) {
        $key = isset($_GET['key']) ? sanitize_text_field(wp_unslash($_GET['key'])) : '';
        $url = isset($_GET['url']) ? esc_url_raw(trim(wp_unslash($_GET['url']))) : '';
        if ($url !== '' && !preg_match('#^https://[a-z0-9]{20}\.supabase\.co/?$#', $url)) wp_die('That address doesn\'t look like a Supabase project (https://xxxxxxxxxxxxxxxxxxxx.supabase.co). Nothing was changed.', 'VJ Supabase');
        if ($url !== '' && !$key) wp_die('Give the address AND its service_role key together: /wp-admin/?vj_set_supabase=1&url=…&key=… — nothing was changed.', 'VJ Supabase');
        if ($key) {
            $old = VJ_SUPA_URL;
            if ($url !== '') update_option('vj_supabase_url', rtrim($url, '/'), false);
            update_option('vj_supabase_key', $key, false);
            delete_option('vj_supa_lasterr');
            wp_die('Saved ✓<br>Project: <b>' . esc_html($url !== '' ? rtrim($url, '/') : $old) . '</b>' . ($url !== '' && rtrim($url, '/') !== $old ? '<br>(was ' . esc_html($old) . ')' : '')
                . '<br><br>Now test: <a href="' . esc_url(admin_url('?vj_test_supabase=1')) . '">/wp-admin/?vj_test_supabase=1</a>', 'VJ Supabase');
        }
        wp_die('Usage: /wp-admin/?vj_set_supabase=1&url=https://xxxx.supabase.co&key=SERVICE_ROLE_KEY<br>Current project: <b>' . esc_html(VJ_SUPA_URL) . '</b>', 'VJ Supabase');
    }
    // Full backfill, paged so it can't time out:
    //   /wp-admin/?vj_backfill=1            → first 50, then auto-continues
    //   /wp-admin/?vj_backfill=1&offset=50  → resume manually if needed
    if (isset($_GET['vj_backfill'])) {
        $offset = isset($_GET['offset']) ? absint($_GET['offset']) : 0;
        $batch = 50;
        $ids = vj_supa_recent_order_ids(5000);          // newest → oldest
        $total = count($ids);
        $slice = array_slice($ids, $offset, $batch);
        $done = 0;
        foreach ($slice as $oid) { if (vj_supa_push_order($oid)) $done++; }
        $next = $offset + count($slice);
        $err = get_option('vj_supa_lasterr');
        $msg = '<h2 style="font-family:sans-serif">Backfill</h2>'
             . '<p style="font-family:sans-serif;font-size:16px">Mirrored <b>' . $next . '</b> of <b>' . $total . '</b> orders'
             . ($done < count($slice) ? ' (' . (count($slice) - $done) . ' failed in this batch)' : '') . '</p>';
        if ($err) $msg .= '<p style="font-family:sans-serif"><code>' . esc_html($err) . '</code></p>';
        if ($next < $total) {
            $url = admin_url('?vj_backfill=1&offset=' . $next);
            $msg .= '<p style="font-family:sans-serif">Continuing automatically…</p>'
                 . '<script>setTimeout(function(){location.href=' . wp_json_encode($url) . ';}, 1200);</script>'
                 . '<p><a href="' . esc_url($url) . '">Click here if it does not continue</a></p>';
        } else {
            $msg .= '<p style="font-family:sans-serif;color:green;font-size:18px"><b>✅ Done — all orders are mirrored.</b></p>';
        }
        wp_die($msg, 'VJ Backfill');
    }
    if (isset($_GET['vj_backfill_supabase'])) {
        // Full backfill, paged: /wp-admin/?vj_backfill_supabase=1&n=200
        $n = isset($_GET['n']) ? max(1, min(1000, absint($_GET['n']))) : 200;
        $ids = vj_supa_recent_order_ids($n);
        $pushed = 0;
        foreach ($ids as $oid) { if (vj_supa_push_order($oid)) $pushed++; }
        wp_die('Backfill done ✓<br>Orders scanned: <b>' . count($ids) . '</b><br>Mirrored: <b>' . $pushed . '</b>'
            . '<br><br>(Only processing / completed / refunded are mirrored — pending junk is skipped.)', 'VJ Supabase');
    }
    if (isset($_GET['vj_test_supabase'])) {
        delete_option('vj_supa_lasterr');
        $n = vj_supa_sweep(5);
        $found = (int) get_option('vj_supa_found');
        $err = get_option('vj_supa_lasterr');
        if ($n === false) wp_die('Failed — Supabase key not saved yet.', 'VJ Supabase');
        $msg = 'Project: <b>' . esc_html(VJ_SUPA_URL) . '</b><br>Orders found on site: <b>' . $found . '</b><br>Pushed to Supabase: <b>' . $n . '</b>';
        if (!$found) {
            // No orders found at all → prove the connection separately.
            $probe = vj_supa_req('POST', 'woo_mirror?on_conflict=id', array(array('id' => 'probe-test', 'status' => 'probe', 'payload' => array('probe' => true), 'updated_at' => gmdate('c'))));
            $msg .= '<br><br>No orders were found by the plugin (WooCommerce query returned nothing).'
                 . '<br>Connection probe: ' . ($probe !== false ? '<b>OK ✓</b> — a row "probe-test" now exists in woo_mirror, so Supabase + key are fine.' : '<b>FAILED</b>');
        }
        if ($err) $msg .= '<br><br>Last Supabase error: <code>' . esc_html($err) . '</code>';
        wp_die($msg, 'VJ Supabase');
    }
});

function vj_supa_req($method, $path, $body = null) {
    $key = get_option('vj_supabase_key');
    if (!$key) return false;
    $args = array('method' => $method, 'timeout' => 12, 'headers' => array(
        'apikey' => $key, 'Authorization' => 'Bearer ' . $key,
        'Content-Type' => 'application/json', 'Prefer' => 'resolution=merge-duplicates,return=minimal',
    ));
    if ($body !== null) $args['body'] = wp_json_encode($body);
    $res = wp_remote_request(VJ_SUPA_URL . '/rest/v1/' . $path, $args);
    if (is_wp_error($res)) { update_option('vj_supa_lasterr', 'network: ' . $res->get_error_message(), false); return false; }
    $code = wp_remote_retrieve_response_code($res);
    // Success bodies are often EMPTY (Prefer: return=minimal) — return the body
    // when there is one, otherwise TRUE. Returning '' made successful pushes
    // look like failures ("Pushed 0" while rows were actually landing).
    if ($code >= 200 && $code < 300) { $b = wp_remote_retrieve_body($res); return ($b === '' || $b === null) ? true : $b; }
    update_option('vj_supa_lasterr', $code . ': ' . substr((string) wp_remote_retrieve_body($res), 0, 220), false);
    return false;
}

// Build the order payload in the exact shape the app uses.
function vj_supa_payload($order) {
    $items = array();
    foreach ($order->get_items() as $li) {
        $size = null;
        foreach ($li->get_meta_data() as $m) {
            $d = $m->get_data();
            if (isset($d['key']) && preg_match('/size/i', $d['key'])) { $size = strtoupper((string) $d['value']); break; }
        }
        $img = null; $sku = ''; $permalink = '';
        $prod = $li->get_product();
        if ($prod) {
            $iid = $prod->get_image_id(); if ($iid) $img = wp_get_attachment_image_url($iid, 'medium');
            $sku = (string) $prod->get_sku();
            // Parent SKU for variations (the TS-handle lives on the parent).
            if ($sku === '' || strpos($sku, 'TS-') !== 0) {
                $parent_id = $prod->get_parent_id();
                if ($parent_id) { $pp = wc_get_product($parent_id); if ($pp) { $psku = (string) $pp->get_sku(); if ($psku) $sku = $psku; } }
            }
            $permalink = get_permalink($prod->get_parent_id() ?: $prod->get_id());
        }
        $items[] = array('name' => $li->get_name(), 'qty' => $li->get_quantity(), 'size' => $size,
            'image' => $img, 'total' => (float) $li->get_total(), 'sku' => $sku, 'permalink' => $permalink);
    }
    return array(
        'number' => (string) $order->get_order_number(),
        'status' => $order->get_status(),
        'name' => trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name()),
        'firstName' => $order->get_billing_first_name(), 'lastName' => $order->get_billing_last_name(),
        'email' => $order->get_billing_email(), 'phone' => $order->get_billing_phone(),
        'address1' => $order->get_billing_address_1(), 'address2' => $order->get_billing_address_2(),
        'city' => $order->get_billing_city(), 'state' => $order->get_billing_state(),
        'postcode' => $order->get_billing_postcode(), 'country' => $order->get_billing_country(),
        'total' => (float) $order->get_total(), 'subtotal' => (float) $order->get_subtotal(),
        'discountTotal' => (float) $order->get_discount_total(), 'shippingTotal' => (float) $order->get_shipping_total(),
        'paymentMethod' => $order->get_payment_method_title() ?: $order->get_payment_method(),
        'dateCreated' => $order->get_date_created() ? $order->get_date_created()->date('c') : null,
        'items' => $items,
    );
}

function vj_supa_push_order($order_id) {
    $order = wc_get_order($order_id);
    if (!$order) return false;
    // Keep the mirror clean: only paid/settled orders live there. If an order
    // leaves those statuses (cancelled, back to pending), delete its row so
    // the app's desk drops it everywhere.
    if (!in_array($order->get_status(), array('processing', 'completed', 'refunded'), true)) {
        vj_supa_req('DELETE', 'woo_mirror?id=eq.' . rawurlencode((string) $order->get_id()));
        return false;
    }
    return vj_supa_req('POST', 'woo_mirror?on_conflict=id', array(array(
        'id' => (string) $order->get_id(),
        'status' => $order->get_status(),
        'payload' => vj_supa_payload($order),
        'updated_at' => gmdate('c'),
    )));
}

// Mirror on every order event.
add_action('woocommerce_new_order', function ($oid) { vj_supa_push_order($oid); }, 30, 1);
add_action('woocommerce_order_status_changed', function ($oid) { vj_supa_push_order($oid); }, 30, 1);
add_action('woocommerce_update_order', function ($oid) { vj_supa_push_order($oid); }, 30, 1);

// Sweep: push the latest N orders (self-heal + first-time backfill), and
// execute commands the app queued (status changes, refunds).
function vj_supa_recent_order_ids($limit = 100) {
    global $wpdb;
    $ids = array();
    // Only the statuses the app's Order Desk cares about — mirroring pending
    // /failed/cancelled junk would bloat the table for no benefit.
    if (function_exists('wc_get_orders')) {
        $r = wc_get_orders(array('limit' => $limit, 'orderby' => 'date', 'order' => 'DESC', 'return' => 'ids',
            'status' => array('processing', 'completed', 'refunded')));
        if (is_array($r)) $ids = $r;
    }
    if (empty($ids)) { // HPOS table
        $t = $wpdb->prefix . 'wc_orders';
        if ($wpdb->get_var("SHOW TABLES LIKE '$t'") === $t) {
            $ids = $wpdb->get_col($wpdb->prepare("SELECT id FROM $t WHERE type = 'shop_order' AND status IN ('wc-processing','wc-completed','wc-refunded') ORDER BY date_created_gmt DESC LIMIT %d", $limit));
        }
    }
    if (empty($ids)) { // legacy posts table
        $ids = $wpdb->get_col($wpdb->prepare("SELECT ID FROM {$wpdb->posts} WHERE post_type = 'shop_order' AND post_status IN ('wc-processing','wc-completed','wc-refunded') ORDER BY post_date DESC LIMIT %d", $limit));
    }
    return array_map('absint', (array) $ids);
}

function vj_supa_sweep($limit = 100) {
    if (!get_option('vj_supabase_key')) return false;
    $orders = vj_supa_recent_order_ids($limit);
    update_option('vj_supa_found', count($orders), false);
    $n = 0;
    foreach ($orders as $oid) { if (vj_supa_push_order($oid)) $n++; }
    vj_supa_run_commands();
    return $n;
}

function vj_supa_run_commands() {
    // v7.0: a command can carry params.send_after (ISO time, UTC) — e.g. a refund +
    // its email scheduled from the dashboard. It waits until that moment.
    $now = gmdate('Y-m-d\TH:i:s.000\Z');
    $raw = vj_supa_req('GET', 'woo_commands?done=eq.false&or=' . rawurlencode('(params->>send_after.is.null,params->>send_after.lte."' . $now . '")') . '&order=created_at.asc&limit=25');
    $cmds = $raw ? json_decode($raw, true) : null;
    if (!is_array($cmds) || isset($cmds['code'])) {          // filter not accepted → plain list, skip future ones below
        $raw = vj_supa_req('GET', 'woo_commands?done=eq.false&order=created_at.asc&limit=60');
        $cmds = $raw ? json_decode($raw, true) : null;
    }
    if (!is_array($cmds)) return;
    foreach ($cmds as $c) {
        if (!empty($c['params']['send_after']) && strtotime((string) $c['params']['send_after']) > time()) continue;   // v7.0: not yet
        $ok = false; $err = '';
        try {
            $order = wc_get_order(absint($c['order_id']));
            if ($order) {
                if ($c['action'] === 'status' && !empty($c['params']['status'])) {
                    $order->update_status(sanitize_key($c['params']['status']), 'Via Visions Jersey app (Supabase bridge)');
                    $ok = true;
                } elseif ($c['action'] === 'refund' && isset($c['params']['amount'])) {
                    $r = wc_create_refund(array('order_id' => $order->get_id(), 'amount' => (float) $c['params']['amount'], 'reason' => isset($c['params']['reason']) ? sanitize_text_field($c['params']['reason']) : ''));
                    $ok = !is_wp_error($r); if (!$ok) $err = $r->get_error_message();
                } elseif ($c['action'] === 'tracking' && !empty($c['params']['tracking'])) {
                    // Tracking number from the app → stored on the order and
                    // marked completed, which the storefront then shows.
                    $order->update_meta_data('_vj_tracking', sanitize_text_field($c['params']['tracking']));
                    $order->save();
                    $order->update_status('completed', 'Shipped via Visions Jersey app');
                    $ok = true;
                } elseif ($c['action'] === 'reship' && !empty($c['params']['tracking'])) {
                    // v6.4: parcel came back (RTO), customer paid the reship, you sent it again with a
                    // NEW tracking number from the dashboard. Swap the number, forget the old parcel's
                    // stages/emails, tell the customer — the tracker then follows the new number.
                    $new = strtoupper(preg_replace('/\s+/', '', sanitize_text_field($c['params']['tracking'])));
                    $old = vj_track_number($order);
                    $order->update_meta_data('_vj_tracking', $new);
                    $order->update_meta_data('_vj_track_num', $new);
                    $order->update_meta_data('_vj_track_mailed', array());
                    $order->update_meta_data('_vj_track_stage', 'shipped');
                    $order->update_meta_data('_vj_reship_count', (int) $order->get_meta('_vj_reship_count') + 1);
                    $order->add_order_note('Reshipped from the app: ' . $new . ($old ? ' (was ' . $old . ')' : ''));
                    $order->save();
                    if ($order->get_status() !== 'completed') $order->update_status('completed', 'Reshipped via Visions Jersey app');
                    $ok = (bool) vj_mail_shipped($order, '', true);
                    if (!$ok) $err = 'reship email not queued (no billing email?)';
                } elseif ($c['action'] === 'refund_mail') {
                    // v7.1: the branded "Refund initiated" email (same look as Shipped / Delivered)
                    $pp = $c['params'];
                    $ok = (bool) vj_mail_refund($order, isset($pp['amount']) ? (float) $pp['amount'] : 0, isset($pp['reason']) ? (string) $pp['reason'] : '');
                    if (!$ok) $err = 'refund email not queued (no billing email?)';
                } elseif ($c['action'] === 'exchange_offer' && !empty($c['params']['token'])) {
                    // v7.1: "pick another jersey instead" — link to /exchange/<token>
                    $pp = $c['params'];
                    $ok = (bool) vj_mail_exchange_offer($order, sanitize_text_field($pp['token']), isset($pp['item_name']) ? (string) $pp['item_name'] : '', isset($pp['item_size']) ? (string) $pp['item_size'] : '', isset($pp['count']) ? absint($pp['count']) : 0);
                    if ($ok) $order->add_order_note('Exchange offered to the customer (link ' . VJ_MAIL_SHOP . '/exchange/' . sanitize_text_field($pp['token']) . ')');
                    if (!$ok) $err = 'exchange email not queued (no billing email?)';
                } elseif ($c['action'] === 'exchange' && !empty($c['params']['product_id'])) {
                    // v7.1: the customer picked a jersey on the exchange page → swap it in the order
                    $r = vj_exchange_apply($order, $c['params']);
                    $ok = $r === true; if (!$ok) $err = (string) $r;
                } elseif ($c['action'] === 'email' && !empty($c['params']['subject'])) {
                    // Customer email queued by the app (shipped / issue / refund
                    // notices) — sent from inside the site, so the firewall
                    // blocking wp-json can't stop customer comms.
                    $to = $order->get_billing_email();
                    if ($to) {
                        $body = isset($c['params']['message']) ? (string) $c['params']['message'] : '';
                        $ok = wp_mail($to, sanitize_text_field($c['params']['subject']),
                            $body, array('Content-Type: text/plain; charset=UTF-8'));
                        if (!$ok) $err = 'wp_mail failed';
                    } else { $err = 'order has no billing email'; }
                } elseif ($c['action'] === 'track_set' && !empty($c['params']['stage'])) {
                    // v5.8: a shipment stage set by hand in the dashboard — same
                    // customer email / Telegram / mirror as a tracking update.
                    // v6.0: also what track_indiapost.py (GitHub) sends, with the
                    // India Post scan text, office and time.
                    $pp = $c['params'];
                    $GLOBALS['vj_track_ctx'] = array('attempts' => isset($pp['attempts']) ? absint($pp['attempts']) : 0, 'day' => isset($pp['day']) ? sanitize_text_field($pp['day']) : gmdate('Y-m-d'));   // v6.1
                    vj_track_apply($order, sanitize_key($pp['stage']),
                        !empty($pp['desc']) ? sanitize_text_field($pp['desc']) : 'Updated from the dashboard',
                        !empty($pp['where']) ? sanitize_text_field($pp['where']) : '',
                        !empty($pp['when']) ? sanitize_text_field($pp['when']) : gmdate('c'),
                        !empty($pp['status']) ? sanitize_text_field($pp['status']) : 'manual');
                    $ok = true;
                } elseif ($c['action'] === 'billing' && !empty($c['params']) && is_array($c['params'])) {
                    $order->set_address(array_map('sanitize_text_field', $c['params']), 'billing');
                    $order->save(); $ok = true;
                }
                if ($ok) vj_supa_push_order($order->get_id());
            } else $err = 'order not found';
        } catch (Exception $e) { $err = $e->getMessage(); }
        vj_supa_req('PATCH', 'woo_commands?id=eq.' . rawurlencode($c['id']), array('done' => true, 'ok' => $ok, 'error' => $err ?: null, 'done_at' => gmdate('c')));
    }
}

// Run commands + sweep on a tight schedule (piggybacks site traffic).
add_filter('cron_schedules', function ($s) { $s['vj_minute'] = array('interval' => 60, 'display' => 'Every minute (VJ)'); return $s; });
add_action('init', function () {
    if (!wp_next_scheduled('vj_supa_tick')) wp_schedule_event(time() + 60, 'vj_minute', 'vj_supa_tick');
});
add_action('vj_supa_tick', function () { vj_supa_run_commands(); });
add_action('init', function () {
    if (!wp_next_scheduled('vj_supa_hourly')) wp_schedule_event(time() + 300, 'hourly', 'vj_supa_hourly');
});
add_action('vj_supa_hourly', function () { vj_supa_sweep(100); });

// ===== FAST STOREFRONT BRIDGE (v3.5) =====
// Mirrors the product catalogue OUT to Supabase (so the static storefront can
// render instantly), and pulls storefront orders IN, creating real WooCommerce
// orders. The customer never touches this slow site.
//   Sync catalogue now: /wp-admin/?vj_sync_products=1&n=300

function vj_shop_product_row($product) {
    if (!$product || $product->get_status() !== 'publish') return null;
    if ($product->get_catalog_visibility() === 'hidden') return null;
    $images = array();
    $thumb = '';
    $iid = $product->get_image_id();
    if ($iid) {
        $images[] = wp_get_attachment_image_url($iid, 'large');
        // Grid cards only need ~300px. Sending the big file for every card was
        // costing megabytes on the storefront's first paint.
        $thumb = wp_get_attachment_image_url($iid, 'medium');
    }
    foreach (array_slice($product->get_gallery_image_ids(), 0, 9) as $gid) {  // main + up to 9 gallery images
        $u = wp_get_attachment_image_url($gid, 'large'); if ($u) $images[] = $u;
    }
    // Your own photos win. Anything you replaced in the app is pinned, and the
    // supplier sync must never put their picture back. Sizes, stock and price
    // still update as normal — only the image is protected.
    $images = vj_apply_pinned_images($product->get_slug(), $images);
    if (isset($images[0]) && vj_pinned_map_has($product->get_slug(), 0)) $thumb = $images[0];
    $sizes = array();
    if ($product->is_type('variable')) {
        foreach ($product->get_available_variations() as $v) {
            $label = '';
            foreach ($v['attributes'] as $k => $val) { if (stripos($k, 'size') !== false) { $label = strtoupper($val); break; } }
            if ($label === '') continue;
            $sizes[] = array('size' => $label, 'in_stock' => !empty($v['is_in_stock']), 'price' => (float) $v['display_price']);
        }
    }
    $cats = array();
    foreach (wp_get_post_terms($product->get_id(), 'product_cat') as $t) $cats[] = $t->name;
    // v4.6: price comes from the sizes themselves, not the parent's cached
    // summary price. The supplier sync edits size prices via the API and the
    // parent cache was not being refreshed on this host, so the storefront
    // kept showing the old price while the WordPress product page (which
    // reads the sizes) showed the new one. Lowest in-stock size wins; falls
    // back to any size, then to the parent value as before.
    $price = (float) $product->get_price();
    if ($sizes) {
        $in = array(); $all = array();
        foreach ($sizes as $sz) { if ($sz['price'] > 0) { $all[] = $sz['price']; if ($sz['in_stock']) $in[] = $sz['price']; } }
        if ($in) $price = min($in); elseif ($all) $price = min($all);
    }
    return array(
        'id' => (string) $product->get_id(),
        'slug' => $product->get_slug(),
        'name' => $product->get_name(),
        'price' => $price,
        'images' => $images,
        'thumb' => $thumb ?: (isset($images[0]) ? $images[0] : ''),
        'sizes' => $sizes,
        'categories' => $cats,
        'in_stock' => $product->is_in_stock(),
        'updated_at' => gmdate('c'),
    );
}

function vj_sync_products_to_shop($limit = 300) {
    $ids = wc_get_products(array('limit' => $limit, 'status' => 'publish', 'return' => 'ids', 'orderby' => 'date', 'order' => 'DESC'));
    $rows = array(); $n = 0;
    foreach ($ids as $pid) {
        $row = vj_shop_product_row(wc_get_product($pid));
        if (!$row) continue;
        $rows[] = $row;
        if (count($rows) >= 25) { if (vj_supa_req('POST', 'shop_products?on_conflict=id', $rows)) $n += count($rows); $rows = array(); }
    }
    if ($rows && vj_supa_req('POST', 'shop_products?on_conflict=id', $rows)) $n += count($rows);
    return $n;
}

// Keep the storefront fresh automatically.
add_action('woocommerce_update_product', function ($pid) {
    $row = vj_shop_product_row(wc_get_product($pid));
    if ($row) vj_supa_req('POST', 'shop_products?on_conflict=id', array($row));
    else vj_supa_req('DELETE', 'shop_products?id=eq.' . rawurlencode((string) $pid));
}, 40, 1);

// Storefront orders → real WooCommerce orders.
function vj_pull_shop_orders() {
    $raw = vj_supa_req('GET', 'shop_orders?done=eq.false&order=created_at.asc&limit=20');
    if (!$raw) return 0;
    $list = json_decode($raw, true);
    if (!is_array($list)) return 0;
    $made = 0;
    foreach ($list as $so) {
        $err = ''; $woo_id = null;
        try {
            $order = wc_create_order();
            $c = is_array($so['customer']) ? $so['customer'] : array();
            $address = array(
                'first_name' => isset($c['name']) ? sanitize_text_field(strtok($c['name'], ' ')) : '',
                'last_name'  => isset($c['name']) ? sanitize_text_field(trim(substr($c['name'], strpos($c['name'], ' ') ?: strlen($c['name'])))) : '',
                'email'      => isset($c['email']) ? sanitize_email($c['email']) : '',
                'phone'      => isset($c['phone']) ? sanitize_text_field($c['phone']) : '',
                'address_1'  => isset($c['address1']) ? sanitize_text_field($c['address1']) : '',
                'address_2'  => isset($c['address2']) ? sanitize_text_field($c['address2']) : '',
                'city'       => isset($c['city']) ? sanitize_text_field($c['city']) : '',
                'state'      => isset($c['state']) ? sanitize_text_field($c['state']) : '',
                'postcode'   => isset($c['postcode']) ? sanitize_text_field($c['postcode']) : '',
                'country'    => 'IN',
            );
            $order->set_address($address, 'billing');
            $order->set_address($address, 'shipping');
            foreach ((array) $so['items'] as $it) {
                $prod = wc_get_product(absint($it['product_id']));
                if (!$prod) continue;
                $qty = max(1, absint(isset($it['qty']) ? $it['qty'] : 1));
                $item_id = $order->add_product($prod, $qty);
                if ($item_id && !empty($it['size'])) {
                    $item = $order->get_item($item_id);
                    if ($item) { $item->add_meta_data('Sizes', sanitize_text_field($it['size']), true); $item->save(); }
                }
            }
            $order->set_created_via('visions-storefront');
            // Meta browser cookies, passed through from the storefront. These let
            // the Conversions API match this order back to the ad click that
            // caused it. Without them Meta can still count the sale, but the
            // attribution is much weaker.
            foreach (array('fbp' => '_vj_fbp', 'fbc' => '_vj_fbc', 'ua' => '_vj_ua') as $k => $meta) {
                if (!empty($so[$k])) $order->update_meta_data($meta, sanitize_text_field($so[$k]));
            }
            if (!empty($so['pay_amount'])) $order->update_meta_data('_vj_pay_amount', (string) (float) $so['pay_amount']);   // v5.7
            if (!empty($so['ref'])) {
                $order->update_meta_data('_vj_ref', sanitize_text_field($so['ref']));
                $order->add_order_note('Storefront reference: ' . sanitize_text_field($so['ref']));
            }
            $order->add_order_note('Placed on the fast storefront' . (!empty($so['note']) ? ' — ' . sanitize_text_field($so['note']) : ''));
            // Shipping + coupon come through in the note; add a shipping line so
            // the Woo total matches what the customer actually saw and paid.
            $note = isset($so['note']) ? (string) $so['note'] : '';
            if (preg_match('/shipping (\d+(?:\.\d+)?)/i', $note, $m) && (float) $m[1] > 0) {
                $ship = new WC_Order_Item_Shipping();
                $ship->set_method_title('Flat rate — Pan India');
                $ship->set_total((float) $m[1]);
                $order->add_item($ship);
            }
            if (preg_match('/coupon ([A-Z0-9]+) -(\d+(?:\.\d+)?)/i', $note, $m2) && (float) $m2[2] > 0) {
                $fee = new WC_Order_Item_Fee();
                $fee->set_name('Coupon ' . strtoupper($m2[1]));
                $fee->set_total(-1 * (float) $m2[2]);
                $order->add_item($fee);
            }
            $order->calculate_totals();
            $order->update_status('pending', 'Awaiting payment (storefront order)');
            $woo_id = $order->get_id();
            $made++;
            // Notify only now — the order finally has a name, items and a total.
            vj_telegram_new_order($woo_id);
        } catch (Exception $e) { $err = $e->getMessage(); }
        vj_supa_req('PATCH', 'shop_orders?id=eq.' . rawurlencode($so['id']),
            array('done' => true, 'woo_order_id' => $woo_id ? (string) $woo_id : null, 'error' => $err ?: null));
    }
    return $made;
}
add_action('vj_supa_tick', 'vj_pull_shop_orders');

// ---- Instant order creation ----
// WordPress cron only fires when the site gets traffic, so a storefront order
// could sit for a minute. The storefront pings this the moment an order is
// placed, and the order appears in WooCommerce within a second or two.
add_action('rest_api_init', function () {
    register_rest_route('vj/v1', '/tick', array(
        'methods'  => array('GET', 'POST'),
        'permission_callback' => '__return_true',
        'callback' => function () {
            $made = vj_pull_shop_orders();
            // v5.7: also run queued commands now (the payment Shortcut pings this
            // right after a bank SMS, so "paid" → Processing takes seconds, not a minute).
            if (function_exists('vj_supa_run_commands')) { try { vj_supa_run_commands(); } catch (Exception $e) {} }
            $r = new WP_REST_Response(array('imported' => $made), 200);
            $r->header('Access-Control-Allow-Origin', '*');
            return $r;
        },
    ));
});

// ---- Settings screen: Settings → Visions Jersey ----
add_action('admin_menu', function () {
    add_options_page('Visions Jersey', 'Visions Jersey', 'manage_options', 'visions-jersey', 'vj_settings_page');
});

function vj_settings_page() {
    if (!current_user_can('manage_options')) return;

    if (isset($_POST['vj_save']) && check_admin_referer('vj_settings')) {
        $pay = array('upi' => sanitize_text_field(wp_unslash($_POST['vj_upi'] ?? '')));
        update_option('vj_payinfo', $pay, false);
        update_option('vj_payee_name', sanitize_text_field(wp_unslash($_POST['vj_payee'] ?? 'Visions Jersey')), false);
        update_option('vj_wa_number', preg_replace('/\D+/', '', wp_unslash($_POST['vj_wa'] ?? '')), false);
        update_option('vj_from_email', sanitize_email(wp_unslash($_POST['vj_from_email'] ?? '')), false);
        update_option('vj_reminders_on', isset($_POST['vj_reminders']) ? 1 : 0, false);
        // --- supplier sync watchdog (v4.4) ---
        update_option('vj_gh_repo', sanitize_text_field(wp_unslash($_POST['vj_gh_repo'] ?? '')), false);
        update_option('vj_gh_workflow', sanitize_text_field(wp_unslash($_POST['vj_gh_workflow'] ?? 'sync.yml')), false);
        update_option('vj_gh_branch', sanitize_text_field(wp_unslash($_POST['vj_gh_branch'] ?? 'main')), false);
        $gh_tok = trim((string) wp_unslash($_POST['vj_gh_token'] ?? ''));
        // Blank means "leave the saved token alone" — so the masked field
        // below can be submitted empty without wiping a working token.
        if ($gh_tok !== '') update_option('vj_gh_token', $gh_tok, false);
        update_option('vj_gh_on', isset($_POST['vj_gh_on']) ? 1 : 0, false);
        update_option('vj_kn_on', isset($_POST['vj_kn_on']) ? 1 : 0, false);   // v4.5 stock message
        update_option('vj_capi_on', isset($_POST['vj_capi_on']) ? 1 : 0, false);            // v5.0
        update_option('vj_tg_meta_only', isset($_POST['vj_tg_meta_only']) ? 1 : 0, false);  // v5.2
        update_option('vj_capi_pixel', sanitize_text_field(wp_unslash($_POST['vj_capi_pixel'] ?? '')), false);
        $capi_tok = trim((string) wp_unslash($_POST['vj_capi_token'] ?? ''));
        if ($capi_tok !== '') update_option('vj_capi_token', $capi_tok, false);
        update_option('vj_redirect_on', isset($_POST['vj_redirect_on']) ? 1 : 0, false);   // v4.6 send shoppers to the fast store
        echo '<div class="notice notice-success"><p>Saved ✓</p></div>';
    }

    $pay = get_option('vj_payinfo', array());
    $upi = $pay['upi'] ?? '';
    $payee = get_option('vj_payee_name', 'Visions Jersey');
    $wa = get_option('vj_wa_number', '917434882164');
    $from = get_option('vj_from_email', get_option('admin_email'));
    $rem = get_option('vj_reminders_on', 1);
    $gh_repo = get_option('vj_gh_repo', '');
    $gh_wf = get_option('vj_gh_workflow', 'sync.yml');
    $gh_br = get_option('vj_gh_branch', 'main');
    $gh_tok_set = get_option('vj_gh_token', '') !== '';
    $gh_on = get_option('vj_gh_on', 0);
    $gh_last = vj_gh_last_run_age();
    $kn_on = get_option('vj_kn_on', 1);
    $capi_on = get_option('vj_capi_on', 0);
    $capi_pixel = get_option('vj_capi_pixel', '');
    $capi_tok_set = get_option('vj_capi_token', '') !== '';
    $rd_on = get_option('vj_redirect_on', 0);
    ?>
    <div class="wrap">
      <h1>Visions Jersey</h1>
      <form method="post">
        <?php wp_nonce_field('vj_settings'); ?>
        <table class="form-table" role="presentation">
          <tr>
            <th scope="row"><label for="vj_upi">UPI ID</label></th>
            <td><input name="vj_upi" id="vj_upi" type="text" class="regular-text" value="<?php echo esc_attr($upi); ?>" placeholder="yourname@bank">
              <p class="description">Used on the storefront payment screen, the QR code and all reminder emails.</p></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_payee">Payee name</label></th>
            <td><input name="vj_payee" id="vj_payee" type="text" class="regular-text" value="<?php echo esc_attr($payee); ?>">
              <p class="description">Shown inside the customer's UPI app when they scan.</p></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_wa">WhatsApp number</label></th>
            <td><input name="vj_wa" id="vj_wa" type="text" class="regular-text" value="<?php echo esc_attr($wa); ?>" placeholder="917434882164">
              <p class="description">Country code, no “+”. Used in emails and payment messages.</p></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_from_email">Send emails from</label></th>
            <td><input name="vj_from_email" id="vj_from_email" type="email" class="regular-text" value="<?php echo esc_attr($from); ?>">
              <p class="description">Best deliverability: an address on your own domain, e.g. orders@visionsjersey.com</p></td>
          </tr>
          <tr>
            <th scope="row">Payment reminders</th>
            <td><label><input name="vj_reminders" type="checkbox" <?php checked($rem, 1); ?>> Email customers about unpaid orders (one email, 20 minutes after the order)</label>
              <p class="description">Each order gets at most three emails, then we stop. Uncheck to disable entirely.</p></td>
          </tr>
        </table>

        <h2>Supplier sync watchdog</h2>
        <p class="description" style="max-width:640px">
          GitHub's own schedule sometimes skips a run. This checks every 15 minutes how long
          it has been since the last successful sync, and starts one itself if the gap gets
          too big. GitHub's schedule keeps running as normal — this only steps in when a run
          goes missing.
          <?php if ($gh_last === null) : ?>
            <br><b>Last sync: unknown</b> (no runs found in Supabase yet).
          <?php else : ?>
            <br><b>Last sync: <?php echo esc_html(vj_gh_ago($gh_last)); ?> ago.</b>
          <?php endif; ?>
        </p>
        <table class="form-table" role="presentation">
          <tr>
            <th scope="row">Watchdog</th>
            <td><label><input name="vj_gh_on" type="checkbox" <?php checked($gh_on, 1); ?>> Start a sync if the last one was over <?php echo (int) VJ_GH_MAX_GAP_H; ?> hours ago</label></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_gh_repo">GitHub repo</label></th>
            <td><input name="vj_gh_repo" id="vj_gh_repo" type="text" class="regular-text" value="<?php echo esc_attr($gh_repo); ?>" placeholder="username/visions-sync">
              <p class="description">Same value as in the app's Settings screen.</p></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_gh_workflow">Workflow file</label></th>
            <td><input name="vj_gh_workflow" id="vj_gh_workflow" type="text" class="regular-text" value="<?php echo esc_attr($gh_wf); ?>" placeholder="sync.yml"></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_gh_branch">Branch</label></th>
            <td><input name="vj_gh_branch" id="vj_gh_branch" type="text" class="regular-text" value="<?php echo esc_attr($gh_br); ?>" placeholder="main"></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_gh_token">Access token</label></th>
            <td><input name="vj_gh_token" id="vj_gh_token" type="password" class="regular-text" value="" autocomplete="new-password" placeholder="<?php echo $gh_tok_set ? 'saved — leave blank to keep it' : 'github_pat_...'; ?>">
              <p class="description">Fine-grained token with Actions: read and write. Leave blank to keep the one already saved.</p></td>
          </tr>
          <tr>
            <th scope="row">Stock message</th>
            <td><label><input name="vj_kn_on" type="checkbox" <?php checked($kn_on, 1); ?>> After every supplier sync, post the full product list to Telegram for the WhatsApp Business AI</label>
              <p class="description">Two Telegram messages: an instruction to replace the old knowledge, then a .txt file with every product, price, sizes in stock and link.</p></td>
          </tr>
          <tr>
            <th scope="row">Telegram alerts</th>
            <td><label><input name="vj_tg_meta_only" type="checkbox" <?php checked(get_option('vj_tg_meta_only'), 1); ?>> Only alert me about orders that came from a Meta ad</label>
              <p class="description">Storefront orders only — orders placed on visionsjersey.com never trigger an alert. Leave unticked to be told about every storefront order.</p></td>
          </tr>
          <tr>
            <th scope="row">Meta Purchase on payment</th>
            <td><label><input name="vj_capi_on" type="checkbox" <?php checked($capi_on, 1); ?>> Tell Meta a purchase happened only when the order is marked paid</label>
              <p class="description">Sends the Purchase event from this server instead of the browser. Someone tapping "send screenshot" without paying can no longer cost you ad money.<br>
              Last sent: <?php echo esc_html(get_option('vj_capi_last', 'never')); ?><?php $e = get_option('vj_capi_lasterr', ''); echo $e ? ' · last error: <code>' . esc_html($e) . '</code>' : ''; ?><br>
              <?php $cs = vj_capi_status(); echo wp_kses_post($cs['html']); ?></p></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_capi_pixel">Meta pixel ID</label></th>
            <td><input name="vj_capi_pixel" id="vj_capi_pixel" type="text" class="regular-text" value="<?php echo esc_attr($capi_pixel); ?>" placeholder="1849071612747524">
              <p class="description">The same pixel the storefront uses.</p></td>
          </tr>
          <tr>
            <th scope="row"><label for="vj_capi_token">Conversions API token</label></th>
            <td><input name="vj_capi_token" id="vj_capi_token" type="password" class="regular-text" value="" autocomplete="new-password" placeholder="<?php echo $capi_tok_set ? 'saved — leave blank to keep it' : 'EAAG...'; ?>">
              <p class="description">Events Manager → your pixel → Settings → Conversions API → Generate access token. Leave blank to keep the saved one.</p></td>
          </tr>
          <tr>
            <th scope="row">Send shoppers to the fast store</th>
            <td><label><input name="vj_redirect_on" type="checkbox" <?php checked($rd_on, 1); ?>> Redirect visionsjersey.com shop pages to shop.visionsjersey.com</label>
              <p class="description">Home → shop home · /product/&lt;slug&gt; → /jersey/&lt;slug&gt; · shop &amp; category pages → /shop.<br>
              WP admin, login, the REST API, order tracking and emails are untouched. Permanent (301) redirects, so Google moves your rankings across too. Off by default.</p></td>
          </tr>
        </table>
        <p><button class="button button-primary" name="vj_save" value="1">Save changes</button></p>
      </form>

      <hr>
      <h2>Tools</h2>
      <p>
        <a class="button" href="<?php echo esc_url(admin_url('?vj_sync_products=1&n=300')); ?>">Sync products to storefront</a>
        <a class="button" href="<?php echo esc_url(admin_url('?vj_pull_shop_orders=1')); ?>">Import storefront orders now</a>
        <a class="button" href="<?php echo esc_url(admin_url('?vj_test_telegram=1')); ?>">Test Telegram</a>
        <a class="button" href="<?php echo esc_url(admin_url('?vj_test_supabase=1')); ?>">Test Supabase</a>
        <a class="button" href="<?php echo esc_url(admin_url('?vj_run_sync=1')); ?>">Run supplier sync now</a>
        <a class="button" href="<?php echo esc_url(admin_url('?vj_send_knowledge=1')); ?>">Send stock message to Telegram now</a>
        <a class="button" href="<?php echo esc_url(admin_url('?vj_build_feed=1')); ?>">Build Meta catalog feed now</a>
        <?php $fu = get_option('vj_feed_url', ''); if ($fu) : ?>
          <p class="description" style="margin-top:8px">Meta catalog feed URL (paste into Commerce Manager → Data sources → Scheduled feed):<br>
          <code style="user-select:all"><?php echo esc_html($fu); ?></code><br>
          Rebuilt after every supplier sync and every hour. Last built: <?php echo esc_html(get_option('vj_feed_at', 'never')); ?></p>
        <?php endif; ?>
      </p>
    </div>
    <?php
}

// ---- Unpaid order follow-up ----
// Prepaid means some people place an order and never pay. This nudges them
// automatically: a reminder email to the customer 30 minutes in, and a
// Telegram ping to you with a ready WhatsApp link so you can chase personally.
add_action('init', function () {
    if (!wp_next_scheduled('vj_unpaid_check')) wp_schedule_event(time() + 600, 'vj_minute', 'vj_unpaid_check');
});
add_action('vj_unpaid_check', function () {
    if (!get_option('vj_reminders_on', 1)) return;   // switch on the settings page
    // v6.1: ONE email, 20 minutes after an unpaid order (was 30 min / 24 h / 48 h).
    $stages = array(
        1 => 20 * MINUTE_IN_SECONDS,
    );
    $orders = wc_get_orders(array(
        'limit' => 30,
        'status' => array('pending'),
        'date_created' => '>' . (time() - 5 * DAY_IN_SECONDS),   // stop chasing after 5 days
        'orderby' => 'date', 'order' => 'DESC',
    ));
    foreach ($orders as $order) {
        if ($order->get_created_via() !== 'visions-storefront') continue;
        $created = $order->get_date_created();
        if (!$created) continue;
        $age = time() - $created->getTimestamp();
        $done = (int) $order->get_meta('_vj_nudge_stage');
        $stage = 0;
        foreach ($stages as $n => $after) { if ($age >= $after) $stage = $n; }
        if ($stage === 0 || $stage <= $done) continue;

        if (vj_send_unpaid_email($order, $stage)) {
            $order->update_meta_data('_vj_nudge_stage', $stage);
            $order->save();
        }
    }
});

// The reminder email. Contains everything needed to pay in one tap: the exact
// amount (with paise, so the bank SMS matches), a scannable UPI QR and the UPI id.
function vj_send_unpaid_email($order, $stage = 1) {
    // v6.1: one professional email in the house theme — exact amount, QR, the
    // website route (My profile → your email → Pay), and why to trust us.
    $to = $order->get_billing_email();
    if (!$to) return false;
    $pay = get_option('vj_payinfo');
    $upi = !empty($pay['upi']) ? $pay['upi'] : 'iblamerishi@fam';
    $first = $order->get_billing_first_name() ?: 'there';
    $num = $order->get_order_number();
    $total = vj_pay_amount($order);
    $totalTxt = number_format($total, 2, '.', '');
    $ref = $order->get_meta('_vj_ref');
    $upiLink = 'upi://pay?pa=' . rawurlencode($upi) . '&pn=' . rawurlencode(get_option('vj_payee_name', 'Visions Jersey'))
             . '&am=' . $totalTxt . '&cu=INR&tn=' . rawurlencode($ref ?: (string) $num);
    $qr = 'https://api.qrserver.com/v1/create-qr-code/?size=360x360&margin=8&data=' . rawurlencode($upiLink);
    $orderUrl = vj_mail_order_url($order);
    $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ' 👋 Your order is saved and your size is set aside, but the payment hasn\'t reached us yet. It takes under a minute — scan the code below, or open your order on the website.')
        . vj_mail_box($order, array('Amount to pay' => '<span style="font-size:22px;font-weight:900">₹' . vj_mail_e($totalTxt) . '</span>', 'Shipping' => 'Free · India Post'), true)
        . '<div style="text-align:center;border:1px solid #E6E7EB;border-radius:16px;padding:18px 14px;margin:16px 0">'
        . '<div style="font-size:11px;font-weight:800;letter-spacing:.8px;color:#9A9CA6">SCAN WITH ANY UPI APP</div>'
        . '<img src="' . esc_url($qr) . '" width="190" height="190" alt="UPI QR" style="display:block;margin:8px auto;border-radius:10px">'
        . '<div style="font-size:12.5px;color:#6B6E7A">or pay <b style="color:#14161F">₹' . vj_mail_e($totalTxt) . '</b> to <b style="color:#14161F">' . vj_mail_e($upi) . '</b></div></div>'
        . vj_mail_btn($orderUrl, 'Open my order & pay')
        . vj_mail_p('On the website: <b>shop.visionsjersey.com</b> → tap the <b>profile icon</b> at the top → enter the email you ordered with (<b>' . vj_mail_e($to) . '</b>) → open this order → <b>Pay</b>. Your payment confirms by itself within seconds, and you get the confirmation email right after.', true)
        . '<div style="background:#14161F;border-radius:16px;padding:18px;margin:18px 0;color:#F0E8D5">'
        . '<div style="font-size:15px;font-weight:900;margin-bottom:10px">Why 70,000+ fans order from us</div>'
        . '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="font-size:13px;line-height:1.5;color:#E3DCC8">'
        . '<tr><td style="padding:4px 0">✅ &nbsp;Every jersey checked by hand before it\'s packed — fabric, print, badge</td></tr>'
        . '<tr><td style="padding:4px 0">🚚 &nbsp;Prepaid = priority dispatch in 24–48 h, free shipping, tracking by email</td></tr>'
        . '<tr><td style="padding:4px 0">📸 &nbsp;Real photos from real customers on our <a href="' . VJ_MAIL_SHOP . '/reviews" style="color:#F0E8D5;font-weight:800">review wall</a></td></tr>'
        . '<tr><td style="padding:4px 0">🔁 &nbsp;Wrong size? Reply within the hour and we swap it before packing</td></tr>'
        . '</table></div>'
        . vj_mail_note('⏳ <b>Offer prices are limited.</b> The price on this order is held only while your size is reserved — popular sizes sell out, and the discount doesn\'t carry over to a new order.', 'warn')
        . vj_mail_p('Already paid? Then ignore this — it confirms by itself within minutes, and we check every payment ourselves too.', true);
    $html = vj_mail_wrap('Your jersey is reserved — complete the payment in under a minute.', 'Payment pending', 'Your jersey is reserved, ' . vj_mail_e($first), 'warn', $inner);
    return vj_mail_send($order, $to, 'Your jersey is reserved — complete payment · #' . $num, $html, 'unpaid');
}


// Push status + tracking back to storefront orders so customers can follow
// their parcel in "My orders". Runs on the minute tick.
function vj_sync_shop_order_status() {
    $raw = vj_supa_req('GET', 'shop_orders?select=id,woo_order_id&woo_order_id=not.is.null&status=neq.completed&limit=50');
    if (!$raw) return;
    $rows = json_decode($raw, true);
    if (!is_array($rows)) return;
    foreach ($rows as $r) {
        $order = wc_get_order(absint($r['woo_order_id']));
        if (!$order) continue;
        // Tracking can live in several plugins' meta keys — check the usual ones.
        $tracking = '';
        foreach (array('_tracking_number', '_vj_tracking', '_wc_shipment_tracking_number') as $key) {
            $v = $order->get_meta($key);
            if ($v) { $tracking = is_array($v) ? reset($v) : (string) $v; break; }
        }
        if (!$tracking) {
            $items = $order->get_meta('_wc_shipment_tracking_items');
            if (is_array($items) && !empty($items[0]['tracking_number'])) $tracking = $items[0]['tracking_number'];
        }
        vj_supa_req('PATCH', 'shop_orders?id=eq.' . rawurlencode($r['id']), array(
            'status' => $order->get_status(),
            'tracking' => $tracking ?: null,
        ));
    }
}
add_action('vj_supa_tick', 'vj_sync_shop_order_status');

add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_sync_products'])) {
        $n = isset($_GET['n']) ? absint($_GET['n']) : 300;
        $done = vj_sync_products_to_shop($n);
        $err = get_option('vj_supa_lasterr');
        wp_die('Products mirrored to the storefront: <b>' . (int) $done . '</b>'
            . ($err ? '<br>Last Supabase message: <code>' . esc_html($err) . '</code>' : ''), 'VJ Storefront');
    }
    if (isset($_GET['vj_pull_shop_orders'])) {
        wp_die('Storefront orders imported: <b>' . vj_pull_shop_orders() . '</b>', 'VJ Storefront');
    }
});


// Attribution breakdown for one order: /wp-admin/?vj_meta=26032
// Linked from the "Check source" button on every Telegram alert.
add_action('admin_init', function () {
    if (!isset($_GET['vj_meta']) || !current_user_can('manage_options')) return;
    $oid = absint($_GET['vj_meta']);
    $o = wc_get_order($oid);
    if (!$o) wp_die('Order not found.', 'VJ order source');

    $fbc  = (string) $o->get_meta('_vj_fbc');
    $fbp  = (string) $o->get_meta('_vj_fbp');
    $ref  = (string) $o->get_meta('_vj_ref');
    $sent = $o->get_meta('_vj_capi_sent');
    $real = vj_is_real_click($fbc);

    // The click id carries the ad id inside it when the click was genuine.
    $adid = '';
    if ($real && preg_match('/adid([A-Za-z0-9_-]{6,})/', $fbc, $m)) $adid = $m[1];

    $verdict = $real
        ? '<h2 style="color:#0a7">✅ Real Meta ad click</h2>'
        : ($fbc !== ''
            ? '<h2 style="color:#b8860b">⚠️ Has an fbc cookie, but not a valid click</h2><p>The click id is missing or is the literal word <code>fbclid</code>. Meta will not attribute this — treat it as organic.</p>'
            : ($fbp !== ''
                ? '<h2 style="color:#666">👥 Facebook user, but not an ad click</h2>'
                : '<h2 style="color:#666">❌ Not from Meta</h2>'));

    $row = function ($k, $v) {
        return '<tr><td style="padding:7px 12px;border:1px solid #ddd;font-weight:700;white-space:nowrap">' . esc_html($k)
             . '</td><td style="padding:7px 12px;border:1px solid #ddd;word-break:break-all">' . ($v !== '' ? esc_html($v) : '<i style="color:#999">empty</i>') . '</td></tr>';
    };

    wp_die(
        '<div style="font:14px/1.6 system-ui;max-width:760px">'
        . '<h1 style="margin:0 0 4px">Order #' . esc_html($o->get_order_number()) . '</h1>'
        . '<p style="color:#666;margin:0 0 18px">' . esc_html(trim($o->get_billing_first_name() . ' ' . $o->get_billing_last_name()))
        . ' · ₹' . esc_html(number_format((float) $o->get_total(), 2))
        . ' · ' . esc_html(wc_get_order_status_name($o->get_status())) . '</p>'
        . $verdict
        . '<table style="border-collapse:collapse;width:100%;margin-top:14px">'
        . $row('Ad ID', $adid)
        . $row('_vj_fbc', $fbc)
        . $row('_vj_fbp', $fbp)
        . $row('Storefront ref', $ref)
        . $row('Purchase sent to Meta', $sent ? date_i18n('j M Y, g:i a', (int) $sent) : '')
        . '</table>'
        . '<p style="margin-top:18px"><a href="' . esc_url(admin_url('post.php?post=' . $oid . '&action=edit')) . '">Open the order</a></p>'
        . '<p style="color:#999;font-size:12px">Ads Manager is the final word on attribution — Meta validates the click id itself and will not credit an invalid one.</p>'
        . '</div>',
        'Order ' . $o->get_order_number() . ' — source'
    );
});


// Why didn't an alert arrive? Runs the exact same checks the notifier runs and
// reports which one stopped it.  /wp-admin/?vj_tg_debug=1        newest order
//                                /wp-admin/?vj_tg_debug=26032    a specific one
add_action('admin_init', function () {
    if (!isset($_GET['vj_tg_debug']) || !current_user_can('manage_options')) return;

    // ?vj_tg_debug=1 means "the newest order", not "order number 1" — so only
    // treat the value as an id when an order with that id actually exists.
    $oid = absint($_GET['vj_tg_debug']);
    $o   = $oid > 1 ? wc_get_order($oid) : null;
    if (!$o) {
        $q = wc_get_orders(array(
            'limit'   => 1,
            'orderby' => 'date',
            'order'   => 'DESC',
            'status'  => array_keys(wc_get_order_statuses()),
            'return'  => 'ids',
        ));
        if ($q) { $oid = (int) $q[0]; $o = wc_get_order($oid); }
    }
    if (!$o) wp_die('No order found. Try a specific id, e.g. /wp-admin/?vj_tg_debug=26032', 'VJ Telegram debug');

    $cfg  = get_option('vj_telegram');
    $fbc  = (string) $o->get_meta('_vj_fbc');
    $real = vj_is_real_click($fbc);
    $metaOnly = (bool) get_option('vj_tg_meta_only');
    $storefront = $o->get_created_via() === 'visions-storefront' || $o->get_meta('_vj_ref') !== '';

    $line = function ($ok, $label, $detail = '') {
        return '<li style="margin:6px 0">' . ($ok ? '✅' : '❌') . ' <b>' . esc_html($label) . '</b>'
             . ($detail ? ' — <span style="color:#666">' . esc_html($detail) . '</span>' : '') . '</li>';
    };

    $blocked = '';
    if (empty($cfg['token']) || empty($cfg['chat'])) $blocked = 'Telegram token or chat id is not saved.';
    elseif (!$storefront) $blocked = 'This order was not placed on the fast storefront, so no alert is sent by design.';
    elseif ($metaOnly && !$real) $blocked = 'The "Meta ads only" filter is on and this order has no valid ad click. Untick it in Settings to be told about every storefront order.';

    $out = '<div style="font:14px/1.6 system-ui;max-width:720px">'
        . '<h1>Telegram alert check — order #' . esc_html($o->get_order_number()) . '</h1><ul style="list-style:none;padding:0">'
        . $line(!empty($cfg['token']) && !empty($cfg['chat']), 'Telegram configured', !empty($cfg['chat']) ? 'chat ' . $cfg['chat'] : 'not saved')
        . $line($storefront, 'Placed on the fast storefront', $o->get_created_via())
        . $line(!$metaOnly || $real, 'Passes the "Meta ads only" filter', $metaOnly ? ($real ? 'real ad click' : 'filter ON, no valid click') : 'filter is off')
        . '</ul>';

    $out .= $blocked
        ? '<p style="background:#fff4e5;padding:12px;border-radius:8px"><b>No alert would be sent.</b><br>' . esc_html($blocked) . '</p>'
        : '<p style="background:#e7f7e7;padding:12px;border-radius:8px"><b>An alert should have been sent.</b> Use the button below to send it again now.</p>';

    $out .= '<p><a class="button button-primary" href="' . esc_url(admin_url('?vj_tg_debug=' . $oid . '&send=1')) . '">Send this alert now</a></p>';

    if (isset($_GET['send'])) {
        $before = get_option('vj_tg_meta_only');
        update_option('vj_tg_meta_only', 0, false);   // force it through for the test
        vj_telegram_new_order($oid);
        update_option('vj_tg_meta_only', $before, false);
        $out .= '<p style="color:#0a7"><b>Sent — check Telegram.</b> If nothing arrives, the token or chat id is wrong.</p>';
    }

    wp_die($out . '</div>', 'VJ Telegram debug');
});

// An _fbc cookie looks like fb.1.<timestamp>.<clickid>. A browser can end up
// holding one whose last segment is the literal word "fbclid" — that happens
// when a URL carried the parameter name with no value. It is not a click and
// Meta will not attribute it, so it must not be reported as one.
function vj_is_real_click($fbc) {
    if (!$fbc) return false;
    $parts = explode('.', $fbc, 4);
    if (count($parts) < 4) return false;
    $click = $parts[3];
    return strlen($click) > 20 && strtolower($click) !== 'fbclid';
}

// New order → Telegram.
//
// This used to hang off woocommerce_new_order, which fires the instant
// wc_create_order() runs — before the address, the line items or the totals
// have been written. That's why every alert read "₹0.00" with no name and a
// generic item: the order genuinely was empty at that moment.
//
// It's now called explicitly from the storefront import, once the order is
// complete and saved. Two useful side effects: orders placed on
// visionsjersey.com no longer trigger an alert at all, and the message can
// report whether the sale came from a Meta ad.
function vj_telegram_new_order($order_id) {
    $order = wc_get_order($order_id);
    if (!$order) return;
    $cfg = get_option('vj_telegram');
    if (empty($cfg['token']) || empty($cfg['chat'])) return;

    $fbc = (string) $order->get_meta('_vj_fbc');
    $fbp = (string) $order->get_meta('_vj_fbp');
    $real = vj_is_real_click($fbc);

    // Optional filter: only shout about orders Meta can be credited for.
    if (get_option('vj_tg_meta_only') && !$real) return;
    if (get_option('vj_tg_new_orders', '0') !== '1') return;        // v6.1: OFF by default (Telegram = delivered + RTO only); turn on with /wp-admin/?vj_tg_new_orders=1
    $name  = trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name());
    $total = (float) $order->get_total();
    $items = array();
    foreach ($order->get_items() as $it) $items[] = $it->get_name() . ' ×' . $it->get_quantity();
    $src = $real ? '📣 From a Meta ad'
         : ($fbc !== '' ? '⚠️ Has an fbc cookie, but not a valid click — treat as organic'
         : ($fbp !== '' ? '👥 Facebook user, not an ad click' : '🌐 Organic / direct'));

    $city  = trim($order->get_billing_city() . ', ' . $order->get_billing_state());
    $phone = (string) $order->get_billing_phone();
    $ref   = (string) $order->get_meta('_vj_ref');

    $alert = "🆕 New order #" . $order->get_order_number() . "\n"
        . $src . "\n\n"
        . "👤 " . ($name !== '' ? $name : '—') . "\n"
        . "📞 " . ($phone !== '' ? $phone : '—') . "\n"
        . "📍 " . ($city !== ', ' ? $city : '—') . "\n"
        . "💰 ₹" . number_format($total, 2) . "\n"
        . ((float) $order->get_meta('_vj_pay_amount') > 0 ? "💳 Pays ₹" . number_format((float) $order->get_meta('_vj_pay_amount'), 2) . " (bank SMS amount)\n" : '')
        . "\n👕 " . implode("\n👕 ", array_slice($items, 0, 6));
    if ($ref !== '') $alert .= "\n\n🔖 " . $ref;

    $body = array('chat_id' => $cfg['chat'], 'text' => $alert);

    // Always offer the full attribution breakdown for this order, whatever its
    // status — one tap instead of typing the URL by hand.
    $srcBtn = array(array('text' => '🔍 Check source', 'url' => admin_url('?vj_meta=' . $order_id)));

    // Unpaid order → attach a "Copy payment msg" button to THIS message.
    // Telegram caps button-copies at 256 chars, so the button carries a
    // condensed version of the payment message (all key info kept).
    if (in_array($order->get_status(), array('pending', 'on-hold', 'failed'), true)) {
        // v5.7: no "Send on WhatsApp" button any more — payments are matched from
        // the bank SMS automatically; "Mark paid" stays for the odd manual case.
        $rows = array();
        $rows[] = array(array('text' => '✅ Mark paid', 'url' => rest_url('vj/v1/markpaid') . '?order=' . $order_id . '&sig=' . vj_paymsg_sig($order_id)));
        $rows[] = $srcBtn;
        $body['reply_markup'] = wp_json_encode(array('inline_keyboard' => $rows));
    } else {
        $body['reply_markup'] = wp_json_encode(array('inline_keyboard' => array($srcBtn)));
    }
    wp_remote_post('https://api.telegram.org/bot' . $cfg['token'] . '/sendMessage', array('timeout' => 8, 'body' => $body));
}

// Tapping "Mark paid" in Telegram opens this signed link, which moves the order
// to Processing — which in turn fires the Conversions API Purchase. Same
// signature scheme as the copy-message link, so no bot webhook is needed.
add_action('rest_api_init', function () {
    register_rest_route('vj/v1', '/markpaid', array(
        'methods' => 'GET',
        'permission_callback' => '__return_true',
        'callback' => function ($req) {
            $oid = absint($req->get_param('order'));
            $sig = sanitize_text_field($req->get_param('sig'));
            if (!$oid || !hash_equals(vj_paymsg_sig($oid), $sig)) {
                return new WP_Error('vj_bad_sig', 'Invalid link.', array('status' => 403));
            }
            $order = wc_get_order($oid);
            if (!$order) return new WP_Error('vj_no_order', 'Order not found.', array('status' => 404));

            $already = !in_array($order->get_status(), array('pending', 'on-hold', 'failed'), true);
            if (!$already) {
                $order->payment_complete();
                $order->add_order_note('Marked paid from Telegram.');
            }

            $name = trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name());
            header('Content-Type: text/html; charset=utf-8');
            echo '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
               . '<div style="font:16px/1.5 system-ui;padding:34px;text-align:center">'
               . ($already ? '<h2>Already marked paid</h2>' : '<h2 style="color:#0a7">✅ Marked paid</h2>')
               . '<p>Order #' . esc_html($order->get_order_number()) . ' — ' . esc_html($name) . '<br>'
               . '₹' . esc_html(number_format((float) $order->get_total(), 2)) . '</p>'
               . '<p style="color:#666;font-size:13px">Status: ' . esc_html(wc_get_order_status_name($order->get_status())) . '</p>'
               . '</div>';
            exit;
        },
    ));
});

function vj_push_notify_new_order($order_id) {
    $raw = get_option('vj_push_tokens', '');
    $tokens = array_filter(array_map('trim', explode("\n", $raw)));
    if (empty($tokens)) return;
    $order = wc_get_order($order_id);
    if (!$order) return;
    $name = trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name());
    if ($name === '') $name = 'Customer';
    $messages = array();
    foreach ($tokens as $t) {
        $messages[] = array(
            'to' => $t,
            'title' => '⚽ New order #' . $order->get_order_number(),
            'body' => $name . ' — ₹' . $order->get_total() . ' (' . $order->get_status() . ')',
            'sound' => 'order_sound.wav',
            'channelId' => 'new-orders',
            'priority' => 'high',
        );
    }
    wp_remote_post('https://exp.host/--/api/v2/push/send', array(
        'headers' => array('Content-Type' => 'application/json'),
        'body' => wp_json_encode($messages),
        'timeout' => 10,
        'blocking' => false,
    ));
}

// ----- CORS for the web app -----
// The Visions Jersey web app runs on its own domain (Netlify) and calls this
// site's REST API from the browser. Browsers block cross-origin requests unless
// the API answers with these headers. This does NOT open your site up: every
// request still needs the same WooCommerce keys / Application Password — CORS
// only lets the browser deliver the request. Zero effect on normal visitors.
add_action('rest_api_init', function () {
    remove_filter('rest_pre_serve_request', 'rest_send_cors_headers');
    add_filter('rest_pre_serve_request', function ($value) {
        $origin = get_http_origin();
        if ($origin) {
            header('Access-Control-Allow-Origin: ' . esc_url_raw($origin));
            header('Vary: Origin', false);
            header('Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS');
            header('Access-Control-Allow-Headers: Authorization, Content-Type, Content-Disposition, X-HTTP-Method-Override');
            header('Access-Control-Allow-Credentials: true');
            header('Access-Control-Max-Age: 600');
        }
        return $value;
    });
}, 15);
// Answer preflight OPTIONS requests instantly (browsers send these before
// PUT/DELETE/Authorization requests).
add_action('init', function () {
    if (isset($_SERVER['REQUEST_METHOD']) && $_SERVER['REQUEST_METHOD'] === 'OPTIONS'
        && isset($_SERVER['REQUEST_URI']) && strpos($_SERVER['REQUEST_URI'], '/wp-json/') !== false) {
        $origin = get_http_origin();
        if ($origin) {
            header('Access-Control-Allow-Origin: ' . esc_url_raw($origin));
            header('Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS');
            header('Access-Control-Allow-Headers: Authorization, Content-Type, Content-Disposition, X-HTTP-Method-Override');
            header('Access-Control-Allow-Credentials: true');
            header('Access-Control-Max-Age: 600');
        }
        status_header(200);
        exit;
    }
}, 1);

// ----- Image proxy for the web app -----
// Browsers block cross-origin downloads of raw image files (static files never
// get CORS headers — the web server serves them before PHP runs). This endpoint
// streams a media-library image WITH CORS headers so the web app can download
// or share it. Locked to this site's own /wp-content/uploads/ images only.
add_action('rest_api_init', function () {
    register_rest_route('vj/v1', '/img', array(
        'methods' => 'GET',
        'permission_callback' => '__return_true',
        'callback' => function ($req) {
            $src = esc_url_raw($req->get_param('src'));
            $site = wp_parse_url(home_url());
            $u = wp_parse_url($src);
            if (!$u || empty($u['host']) || strcasecmp($u['host'], $site['host']) !== 0) {
                return new WP_Error('bad_src', 'Only images from this site can be proxied.', array('status' => 400));
            }
            $path = isset($u['path']) ? $u['path'] : '';
            if (strpos($path, '/wp-content/uploads/') === false) {
                return new WP_Error('bad_path', 'Only media library images can be proxied.', array('status' => 400));
            }
            $resp = wp_remote_get($src, array('timeout' => 20));
            if (is_wp_error($resp) || wp_remote_retrieve_response_code($resp) !== 200) {
                return new WP_Error('fetch_failed', 'Could not read the image.', array('status' => 502));
            }
            $body = wp_remote_retrieve_body($resp);
            $type = wp_remote_retrieve_header($resp, 'content-type');
            $origin = get_http_origin();
            if ($origin) {
                header('Access-Control-Allow-Origin: ' . esc_url_raw($origin));
                header('Vary: Origin', false);
            }
            header('Content-Type: ' . ($type ? $type : 'image/jpeg'));
            header('Content-Length: ' . strlen($body));
            header('Cache-Control: public, max-age=86400');
            echo $body;
            exit;
        },
    ));
});

// ----- Secure issue-email endpoint (used by the app's Announce tab) -----
add_action('rest_api_init', function () {
    register_rest_route('vj/v1', '/send-issue-email', array(
        'methods' => 'POST',
        'permission_callback' => function ($req) {
            $secret = get_option('vj_email_secret', '');
            $got = $req->get_header('x-vj-secret');
            return $secret !== '' && hash_equals($secret, (string) $got);
        },
        'callback' => function ($req) {
            $p = $req->get_json_params();
            $order_id = isset($p['order_id']) ? intval($p['order_id']) : 0;
            $subject  = isset($p['subject']) ? sanitize_text_field($p['subject']) : '';
            $message  = isset($p['message']) ? wp_kses_post($p['message']) : '';
            if (!$order_id || !$subject || !$message)
                return new WP_REST_Response(array('sent' => false, 'error' => 'Missing order_id/subject/message'), 400);
            $order = wc_get_order($order_id);
            if (!$order)
                return new WP_REST_Response(array('sent' => false, 'error' => 'Order not found'), 404);
            $to = $order->get_billing_email();
            if (!$to)
                return new WP_REST_Response(array('sent' => false, 'error' => 'Order has no email'), 400);
            $headers = array('Content-Type: text/html; charset=UTF-8');
            $html = nl2br(esc_html($message));
            $ok = wp_mail($to, $subject, $html, $headers);
            return new WP_REST_Response(array('sent' => (bool) $ok, 'to' => $to, 'error' => $ok ? null : 'wp_mail failed'), $ok ? 200 : 500);
        },
    ));
});

// ----- Cache purge on product hide/show -----
// FULL purge-all across every known cache system, fired whenever a product's
// visibility changes. Heavier than a per-page purge, but guarantees hide/show
// takes effect instantly for customers with zero manual steps — no matter which
// cache plugin or host-level cache the site runs.
function vj_purge_all_caches() {
    // LiteSpeed Cache
    if (has_action('litespeed_purge_all')) do_action('litespeed_purge_all');
    // WP Rocket
    if (function_exists('rocket_clean_domain')) rocket_clean_domain();
    // W3 Total Cache
    if (function_exists('w3tc_flush_all')) w3tc_flush_all();
    // WP Super Cache
    if (function_exists('wp_cache_clear_cache')) wp_cache_clear_cache();
    // SG Optimizer (SiteGround)
    if (function_exists('sg_cachepress_purge_cache')) sg_cachepress_purge_cache();
    // WP Fastest Cache
    if (function_exists('wpfc_clear_all_cache')) wpfc_clear_all_cache(true);
    // Hummingbird
    if (has_action('wphb_clear_page_cache')) do_action('wphb_clear_page_cache');
    // Breeze (Cloudways)
    if (has_action('breeze_clear_all_cache')) do_action('breeze_clear_all_cache');
    // Cache Enabler
    if (class_exists('Cache_Enabler') && method_exists('Cache_Enabler', 'clear_complete_cache')) Cache_Enabler::clear_complete_cache();
    // Comet Cache
    if (class_exists('comet_cache') && method_exists('comet_cache', 'clear')) comet_cache::clear();
    // Swift Performance
    if (class_exists('Swift_Performance_Cache') && method_exists('Swift_Performance_Cache', 'clear_all_cache')) Swift_Performance_Cache::clear_all_cache();
    // WP-Optimize
    if (class_exists('WP_Optimize') && function_exists('wpo_cache_flush')) wpo_cache_flush();
    // Nginx Helper (many hosts)
    if (has_action('rt_nginx_helper_purge_all')) do_action('rt_nginx_helper_purge_all');
    // Autoptimize
    if (class_exists('autoptimizeCache') && method_exists('autoptimizeCache', 'clearall')) autoptimizeCache::clearall();
    // Elementor CSS cache (theme uses Elementor)
    if (did_action('elementor/loaded')) {
        try { \Elementor\Plugin::$instance->files_manager->clear_cache(); } catch (\Throwable $e) {}
    }
    // Cloudflare official plugin (auto-purge hook)
    if (class_exists('\CF\WordPress\Hooks')) {
        try { do_action('cloudflare_purge_everything'); } catch (\Throwable $e) {}
    }
    // StackCache (20i hosting edge cache) — best-effort: call any purge API it
    // exposes, under every naming convention we can anticipate.
    foreach (array('stack_cache_purge_everything', 'stackcache_purge_all', 'stack_cache_purge', 'stackcache_purge') as $fn) {
        if (function_exists($fn)) { try { $fn(); } catch (\Throwable $e) {} }
    }
    foreach (array('stack_cache_purge_everything', 'stackcache_purge', 'stack_cache_purge') as $hook) {
        if (has_action($hook)) do_action($hook);
    }
    foreach (array('StackCache', 'Stack_Cache', 'StackCachePlugin', 'TwentyI_StackCache') as $cls) {
        if (!class_exists($cls)) continue;
        foreach (array('purge_everything', 'purgeEverything', 'purge_all', 'purgeAll', 'purge', 'flush') as $m) {
            if (!method_exists($cls, $m)) continue;
            try { $cls::$m(); } catch (\Throwable $e) { try { $o = new $cls(); $o->$m(); } catch (\Throwable $e2) {} }
        }
    }
    // WordPress object cache
    if (function_exists('wp_cache_flush')) wp_cache_flush();
}

add_action('transition_post_status', 'vj_purge_on_product_toggle', 10, 3);
function vj_purge_on_product_toggle($new_status, $old_status, $post) {
    if (!$post || $post->post_type !== 'product') return;
    if ($new_status === $old_status) return;
    $relevant = in_array($new_status, array('publish', 'draft'), true)
             || in_array($old_status, array('publish', 'draft'), true);
    if (!$relevant) return;
    vj_purge_all_caches();
}

// ----- StackCache inspector -----
// StackCache uses a private purge mechanism our generic hooks can't reach.
// This tool locates its plugin files on disk and prints the function names,
// classes, hooks, and AJAX/REST actions inside — so we can wire the EXACT
// purge call it uses internally. Visit while logged in as admin:
//     /wp-admin/?vj_stackcache_info=1
add_action('admin_init', 'vj_stackcache_info_tool');
function vj_stackcache_info_tool() {
    if (!isset($_GET['vj_stackcache_info'])) return;
    if (!current_user_can('manage_options')) wp_die('Admins only.');

    $out = array();
    $dirs = array(WPMU_PLUGIN_DIR, WP_PLUGIN_DIR);
    $found = array();
    foreach ($dirs as $dir) {
        if (!is_dir($dir)) continue;
        $iter = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS));
        foreach ($iter as $file) {
            $path = $file->getPathname();
            if (substr($path, -4) !== '.php') continue;
            $rel = str_replace(ABSPATH, '', $path);
            // match by filename OR by content mention of stack cache
            $nameHit = (stripos(basename($path), 'stack') !== false);
            if (!$nameHit) {
                // Only content-scan small-ish files to keep this fast.
                if ($file->getSize() > 400000) continue;
                $head = @file_get_contents($path, false, null, 0, 4000);
                if ($head === false || stripos($head, 'stackcache') === false && stripos($head, 'stack cache') === false && stripos($head, '_stack_cache') === false) continue;
            }
            $found[] = $path;
        }
    }

    if (empty($found)) {
        $out[] = 'No StackCache files found in plugins/ or mu-plugins/.';
        $out[] = 'It may live outside WordPress (server level). Check hosting panel.';
    }

    foreach (array_slice($found, 0, 6) as $path) {
        $rel = str_replace(ABSPATH, '', $path);
        $out[] = str_repeat('=', 70);
        $out[] = 'FILE: ' . $rel . '  (' . number_format(filesize($path)) . ' bytes)';
        $src = @file_get_contents($path);
        if ($src === false) { $out[] = '  (unreadable)'; continue; }

        preg_match_all('/^\s*(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(([^)]*)\)/mi', $src, $fns);
        preg_match_all('/class\s+(\w+)/i', $src, $classes);
        preg_match_all('/add_action\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,/i', $src, $actions);
        preg_match_all('/wp_ajax_(\w+)/i', $src, $ajax);
        preg_match_all('/register_rest_route\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]/i', $src, $rest);
        preg_match_all('/wp_remote_(?:get|post|request)\s*\(\s*([^,\)]{0,120})/i', $src, $remotes);
        preg_match_all('/[\'"](https?:\/\/[^\'"]{5,100})[\'"]/i', $src, $urls);

        if (!empty($classes[1])) $out[] = '  CLASSES: ' . implode(', ', array_unique($classes[1]));
        if (!empty($fns[1]))     $out[] = '  FUNCTIONS: ' . implode(', ', array_unique($fns[1]));
        if (!empty($actions[1])) $out[] = '  HOOKED ACTIONS: ' . implode(', ', array_unique($actions[1]));
        if (!empty($ajax[1]))    $out[] = '  AJAX ACTIONS: ' . implode(', ', array_unique($ajax[1]));
        if (!empty($rest[1])) {
            $routes = array();
            foreach ($rest[1] as $i => $ns) $routes[] = $ns . (isset($rest[2][$i]) ? $rest[2][$i] : '');
            $out[] = '  REST ROUTES: ' . implode(', ', array_unique($routes));
        }
        if (!empty($remotes[1])) $out[] = '  HTTP CALLS: ' . implode(' | ', array_map('trim', array_unique($remotes[1])));
        if (!empty($urls[1]))    $out[] = '  URLS: ' . implode(' | ', array_unique($urls[1]));

        // Show any function whose name mentions purge/flush/clear in full.
        foreach (array_unique($fns[1]) as $fn) {
            if (!preg_match('/purge|flush|clear|invalidat/i', $fn)) continue;
            if (preg_match('/function\s+' . preg_quote($fn, '/') . '\s*\([^)]*\)\s*\{(.{0,1200})/is', $src, $m)) {
                $out[] = '  --- body of ' . $fn . '() (first 1200 chars) ---';
                $out[] = '  ' . str_replace("\n", "\n  ", trim($m[1]));
            }
        }
    }
    wp_die('<h2>VJ StackCache Inspector</h2><pre>' . esc_html(implode("\n", $out)) . '</pre>', 'VJ StackCache Inspector');
}

// ----- Product-page cache TTL cap -----
// Last-resort safety net: tell edge caches (StackCache, Hostinger CDN, etc.)
// to keep product pages at most 5 minutes. Even if every purge hook above
// fails, a stale hidden/shown state can only survive 300 seconds.
add_action('template_redirect', 'vj_product_cache_ttl', 5);
function vj_product_cache_ttl() {
    if (is_user_logged_in()) return;
    if (!function_exists('is_product') || !is_product()) return;
    if (!headers_sent()) {
        header('Cache-Control: public, max-age=300, s-maxage=300');
    }
}

// ----- Hard 404 guard for hidden products -----
// Customers hitting the direct URL of a hidden product get a genuine 404 —
// whether it's hidden via draft status OR via catalog_visibility='hidden'.
// Staff who can edit products still see the page normally (for previewing).
// Because catalog-visibility hiding never changes the post status, the slug
// can never be regenerated — the URL is untouchable by design.
add_action('template_redirect', 'vj_draft_product_404');
function vj_draft_product_404() {
    if (!is_singular('product')) return;
    $post = get_queried_object();
    if (!$post) return;
    if (current_user_can('edit_products')) return; // staff can preview

    $blocked = false;
    if ($post->post_status !== 'publish') {
        $blocked = true;
    } else if (function_exists('wc_get_product')) {
        $product = wc_get_product($post->ID);
        if ($product && $product->get_catalog_visibility() === 'hidden') $blocked = true;
    }
    if (!$blocked) return;

    global $wp_query;
    $wp_query->set_404();
    status_header(404);
    nocache_headers();
}

// Purge whenever a product's catalog visibility changes (hide/show from the
// app never changes post status, so transition_post_status won't fire — this
// hook catches it). We track the last-known visibility in post meta so plain
// stock updates don't trigger a full purge storm.
add_action('woocommerce_update_product', 'vj_purge_on_visibility_change', 20);
function vj_purge_on_visibility_change($product_id) {
    static $busy = false;      // reentrancy guard: our own touch re-fires this hook
    if ($busy) return;
    if (!function_exists('wc_get_product')) return;
    $product = wc_get_product($product_id);
    if (!$product) return;
    $vis = $product->get_catalog_visibility();
    $prev = get_post_meta($product_id, '_vj_last_visibility', true);
    if ($prev === $vis) return; // visibility unchanged → no purge needed
    update_post_meta($product_id, '_vj_last_visibility', $vis);
    if ($prev === '') return;   // first time we see this product → just record

    $busy = true;
    vj_purge_all_caches();
    // Edge caches like StackCache (20i) ignore plugin purge hooks but DO purge
    // when WordPress genuinely saves a post. So we "touch" the product with a
    // no-op save, firing the exact save_post/post_updated signals edge caches
    // watch. This is what makes hide/show instant on 20i hosting.
    wp_update_post(array('ID' => $product_id));
    $busy = false;
}

// ----- Slug lock: hide products via DRAFT without ever breaking the URL -----
// The app's "Hide" button sets a product to draft (truly invisible: gone from
// shop, search, categories, AND the direct URL 404s — zero customer distraction).
// The one danger of draft is WordPress regenerating the slug on re-publish.
// This lock snapshots the slug the moment a product is drafted, then force-
// restores that exact slug when it's published again. URL never changes.
add_filter('wp_insert_post_data', 'vj_slug_lock', 10, 2);
function vj_slug_lock($data, $postarr) {
    if (!isset($data['post_type']) || $data['post_type'] !== 'product') return $data;
    $id = isset($postarr['ID']) ? intval($postarr['ID']) : 0;
    if (!$id) return $data;

    if ($data['post_status'] === 'draft') {
        // Going into the vault: remember the current live slug (only once).
        $existing = get_post_meta($id, '_vj_locked_slug', true);
        if (empty($existing)) {
            $current = get_post_field('post_name', $id);
            if (!empty($current)) update_post_meta($id, '_vj_locked_slug', $current);
        }
    }

    if ($data['post_status'] === 'publish') {
        // Coming out of the vault: force the exact original slug back.
        $locked = get_post_meta($id, '_vj_locked_slug', true);
        if (!empty($locked)) {
            $data['post_name'] = $locked;
            delete_post_meta($id, '_vj_locked_slug');
        } else {
            // No snapshot (product was drafted BEFORE this plugin was installed).
            // Fallback: keep whatever slug the product already has in the DB —
            // never let WordPress regenerate it from the title.
            $current = get_post_field('post_name', $id);
            if (!empty($current)) $data['post_name'] = $current;
        }
    }
    return $data;
}

// ----- Slug inspector -----
// Shows the ground truth for a URL that "should work but 404s". Visit while
// logged in as admin:
//   /wp-admin/?vj_check_slug=the-product-slug-from-the-url
// Lists EVERY post in the DB (any status, incl. trash) whose slug equals,
// starts with, or is a prefix of the given slug — with ID, status, exact DB
// slug, title, and what WordPress resolves the slug to.
add_action('admin_init', 'vj_check_slug_tool');
function vj_check_slug_tool() {
    if (!isset($_GET['vj_check_slug'])) return;
    if (!current_user_can('manage_options')) wp_die('Admins only.');
    global $wpdb;
    $slug = sanitize_title(wp_unslash($_GET['vj_check_slug']));
    if (empty($slug)) wp_die('Give a slug: ?vj_check_slug=your-product-slug');

    $like1 = $wpdb->esc_like($slug) . '%';   // slug, slug-2, slug__trashed…
    $rows = $wpdb->get_results($wpdb->prepare(
        "SELECT ID, post_name, post_status, post_type, post_title, post_modified
         FROM {$wpdb->posts}
         WHERE (post_name LIKE %s OR %s LIKE CONCAT(post_name, '%%'))
           AND post_type IN ('product','page','post')
         ORDER BY post_type, post_status, ID",
        $like1, $slug
    ));

    $out = array("Searched slug: {$slug}", str_repeat('-', 60));
    if (empty($rows)) {
        $out[] = 'NO posts found with this or a similar slug. The URL never pointed';
        $out[] = 'at an existing post — check for a typo, or the product was deleted.';
    }
    foreach ($rows as $r) {
        $exact = ($r->post_name === $slug) ? '  <-- EXACT MATCH' : '';
        $out[] = "#{$r->ID} [{$r->post_type}/{$r->post_status}] slug='{$r->post_name}'{$exact}";
        $out[] = "    title: " . mb_substr($r->post_title, 0, 80);
        $out[] = "    modified: {$r->post_modified}  edit: " . admin_url("post.php?post={$r->ID}&action=edit");
    }
    $out[] = str_repeat('-', 60);

    // What does WordPress actually resolve this slug to right now?
    $resolved = get_page_by_path($slug, OBJECT, 'product');
    if ($resolved) {
        $out[] = "WordPress resolves '{$slug}' to product #{$resolved->ID} (status: {$resolved->post_status}).";
        if ($resolved->post_status !== 'publish') {
            $out[] = ">>> That product is NOT published — this is why the URL 404s for customers.";
        } else if (function_exists('wc_get_product')) {
            $pr = wc_get_product($resolved->ID);
            if ($pr && $pr->get_catalog_visibility() === 'hidden') {
                $out[] = ">>> Product is catalog-HIDDEN — our plugin serves customers a 404 on purpose. Tap Show in the app to make the URL live.";
            } else {
                $out[] = ">>> Product is published + visible. If the URL still 404s, permalinks are stale: go to Settings → Permalinks and click Save Changes.";
            }
        }
    } else {
        $out[] = "WordPress does NOT resolve '{$slug}' to any product.";
        $out[] = ">>> No published product owns this exact slug — see the list above for where it went (a -2 copy, a trashed copy, or a draft).";
    }
    wp_die('<h2>VJ Slug Inspector</h2><pre>' . esc_html(implode("\n", $out)) . '</pre>', 'VJ Slug Inspector');
}

// ----- One-time slug repair tool -----
// For products whose slug already got broken (WordPress appended -2 / -3 on a
// republish that happened before the slug lock was installed). Visit, while
// logged into WP admin:
//     https://YOURSITE.com/wp-admin/?vj_repair_slugs=1        (dry run — shows what would change)
//     https://YOURSITE.com/wp-admin/?vj_repair_slugs=go       (actually fixes)
// It finds products with a slug ending in -2/-3/etc where the base slug is
// free, and strips the suffix, restoring the original URL.
add_action('admin_init', 'vj_repair_slugs_tool');
function vj_repair_slugs_tool() {
    if (!isset($_GET['vj_repair_slugs'])) return;
    if (!current_user_can('manage_options')) wp_die('Admins only.');
    global $wpdb;
    $apply = ($_GET['vj_repair_slugs'] === 'go');

    $rows = $wpdb->get_results(
        "SELECT ID, post_name, post_status FROM {$wpdb->posts}
         WHERE post_type = 'product'
           AND post_name REGEXP '-[0-9]+$'
         LIMIT 2000"
    );
    $report = array();
    foreach ($rows as $r) {
        $base = preg_replace('/-[0-9]+$/', '', $r->post_name);
        if (empty($base)) continue;
        // Is the base slug free (no other product/post using it)?
        $taken = $wpdb->get_var($wpdb->prepare(
            "SELECT ID FROM {$wpdb->posts}
             WHERE post_name = %s AND ID != %d AND post_status != 'trash'
             LIMIT 1", $base, $r->ID
        ));
        if ($taken) { $report[] = "SKIP #{$r->ID}: '{$r->post_name}' → '{$base}' (base slug taken by post #{$taken})"; continue; }
        if ($apply) {
            $wpdb->update($wpdb->posts, array('post_name' => $base), array('ID' => $r->ID));
            clean_post_cache($r->ID);
            $report[] = "FIXED #{$r->ID}: '{$r->post_name}' → '{$base}'";
        } else {
            $report[] = "WOULD FIX #{$r->ID}: '{$r->post_name}' → '{$base}'";
        }
    }
    if (empty($report)) $report[] = 'No products with numeric-suffix slugs found. Nothing to repair.';
    $mode = $apply ? 'APPLIED' : 'DRY RUN (add =go to apply)';
    wp_die('<h2>VJ Slug Repair — ' . esc_html($mode) . '</h2><pre>' . esc_html(implode("\n", $report)) . '</pre>', 'VJ Slug Repair');
}

// ----- Daily wp-cron: unshipped orders older than 2 days -> push to ALL saved tokens -----
// Reaches both admin AND employee phones (local reminders only hit the phone
// that logged the order — this fixes that gap). Business rule: max ship 4 days.
register_activation_hook(__FILE__, function () {
    if (!wp_next_scheduled('vj_daily_unshipped_check')) {
        // Run once a day at ~10am server time.
        wp_schedule_event(strtotime('tomorrow 10:00'), 'daily', 'vj_daily_unshipped_check');
    }
});
register_deactivation_hook(__FILE__, function () {
    wp_clear_scheduled_hook('vj_daily_unshipped_check');
});
// Belt-and-braces: also ensure the job is scheduled on every admin page load.
add_action('admin_init', function () {
    if (!wp_next_scheduled('vj_daily_unshipped_check')) {
        wp_schedule_event(time() + 60, 'daily', 'vj_daily_unshipped_check');
    }
});

add_action('vj_daily_unshipped_check', 'vj_daily_unshipped_check_run');
function vj_daily_unshipped_check_run() {
    $raw = get_option('vj_push_tokens', '');
    $tokens = array_filter(array_map('trim', explode("\n", $raw)));
    if (empty($tokens)) return;
    if (!function_exists('wc_get_orders')) return;

    // Fetch orders created 2-6 days ago, still not fulfilled.
    $orders = wc_get_orders(array(
        'limit'        => 50,
        'status'       => array('processing', 'on-hold', 'pending'),
        'date_created' => (strtotime('-6 days')) . '...' . (strtotime('-2 days')),
        'orderby'      => 'date',
        'order'        => 'ASC',
    ));
    if (empty($orders)) return;

    $messages = array();
    foreach ($orders as $order) {
        $created = $order->get_date_created();
        if (!$created) continue;
        $days = floor((time() - $created->getTimestamp()) / DAY_IN_SECONDS);
        if ($days < 2) continue;
        $name = trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name());
        if ($name === '') $name = 'Customer';
        $urgent = $days >= 4;
        $title = $urgent
            ? '🚨 Order #' . $order->get_order_number() . ' — ' . $days . ' days old!'
            : '📦 Order #' . $order->get_order_number() . ' — shipped yet?';
        $body = $urgent
            ? $name . ' — past the 4-day ship limit. Ship it NOW or refund.'
            : $name . ' — ' . $days . ' days old. Max ship is 4 days.';
        foreach ($tokens as $t) {
            $messages[] = array(
                'to' => $t,
                'title' => $title,
                'body' => $body,
                'sound' => 'order_sound.wav',
                'channelId' => 'new-orders',
                'priority' => 'high',
            );
        }
    }
    if (empty($messages)) return;

    // Expo push API accepts up to 100 messages per request.
    foreach (array_chunk($messages, 90) as $batch) {
        wp_remote_post('https://exp.host/--/api/v2/push/send', array(
            'headers' => array('Content-Type' => 'application/json'),
            'body' => wp_json_encode($batch),
            'timeout' => 10,
            'blocking' => false,
        ));
    }
}

// ----- Only decrement stock when an order enters "processing" status -----
// Default Woo drops stock the moment a pending/on-hold order is created — which
// means a customer who never actually pays still eats inventory. This filter
// blocks the reduction unless the order status is currently "processing".
// Restoration on cancel/refund still runs normally through Woo's core.
add_filter('woocommerce_can_reduce_order_stock', function ($can_reduce, $order) {
    if (!$order || !is_a($order, 'WC_Order')) return $can_reduce;
    return $order->get_status() === 'processing' ? $can_reduce : false;
}, 10, 2);

// Belt-and-braces: when an order actually MOVES into processing (e.g. payment
// confirmed for a pending order, or a partial COD flips over), make sure Woo
// reduces stock for it — even if the filter above blocked an earlier attempt.
add_action('woocommerce_order_status_processing', function ($order_id) {
    $order = wc_get_order($order_id);
    if (!$order) return;
    if (function_exists('wc_maybe_reduce_stock_levels')) {
        wc_maybe_reduce_stock_levels($order_id);
    }
}, 20);

// =====================================================================
//  SUPPLIER SYNC WATCHDOG (v4.4)
//
//  GitHub's scheduled workflows are best-effort — when their runners are
//  busy, a scheduled run is delayed or dropped, which is why the supplier
//  sync sometimes goes 10 hours instead of the expected gap. GitHub also
//  disables schedules in a repo with no commits for 60 days.
//
//  This watchdog runs on the site (which is always up), checks how long
//  it has been since the last row in Supabase `sync_runs`, and fires the
//  same GitHub workflow_dispatch the app's "RUN SYNC NOW" button uses if
//  the gap is too big. GitHub's own schedule is untouched and keeps
//  running; this only covers the runs it drops.
// =====================================================================

// Start a sync when the last one is older than this many hours.
if (!defined('VJ_GH_MAX_GAP_H')) define('VJ_GH_MAX_GAP_H', 2.5);
// Never fire twice inside this window, whatever else happens.
if (!defined('VJ_GH_COOLDOWN_S')) define('VJ_GH_COOLDOWN_S', 1800);

// Seconds since the newest row in sync_runs, or null if we can't tell.
function vj_gh_last_run_age() {
    $body = vj_supa_req('GET', 'sync_runs?select=ran_at&order=ran_at.desc&limit=1');
    if ($body === false || $body === true || !is_string($body)) return null;
    $rows = json_decode($body, true);
    if (!is_array($rows) || !isset($rows[0]['ran_at'])) return null;
    $t = strtotime((string) $rows[0]['ran_at']);
    if (!$t) return null;
    $age = time() - $t;
    return $age >= 0 ? $age : 0;
}

// "3 hours 12 min" — for the settings page.
function vj_gh_ago($seconds) {
    $seconds = (int) $seconds;
    $h = (int) floor($seconds / 3600);
    $m = (int) floor(($seconds % 3600) / 60);
    if ($h <= 0) return $m . ' min';
    return $h . ' hr ' . $m . ' min';
}

// Fire the workflow. Returns true, or a string describing the failure.
function vj_gh_dispatch() {
    $repo = trim((string) get_option('vj_gh_repo', ''));
    $wf = trim((string) get_option('vj_gh_workflow', 'sync.yml'));
    $branch = trim((string) get_option('vj_gh_branch', 'main'));
    $token = trim((string) get_option('vj_gh_token', ''));
    if ($repo === '' || $wf === '' || $token === '') return 'GitHub repo, workflow file or token not set.';

    $url = 'https://api.github.com/repos/' . $repo . '/actions/workflows/' . rawurlencode($wf) . '/dispatches';
    $res = wp_remote_post($url, array(
        'timeout' => 20,
        'headers' => array(
            'Accept' => 'application/vnd.github+json',
            'Authorization' => 'Bearer ' . $token,
            'X-GitHub-Api-Version' => '2022-11-28',
            'User-Agent' => 'VisionsJerseyPlugin',
            'Content-Type' => 'application/json',
        ),
        'body' => wp_json_encode(array('ref' => ($branch !== '' ? $branch : 'main'))),
    ));
    if (is_wp_error($res)) {
        update_option('vj_gh_lasterr', 'network: ' . $res->get_error_message(), false);
        return 'network: ' . $res->get_error_message();
    }
    $code = (int) wp_remote_retrieve_response_code($res);
    if ($code === 204) {                       // GitHub returns 204 No Content on success
        update_option('vj_gh_lastfire', time(), false);
        delete_option('vj_gh_lasterr');
        return true;
    }
    $msg = $code . ': ' . substr((string) wp_remote_retrieve_body($res), 0, 220);
    update_option('vj_gh_lasterr', $msg, false);
    return $msg;
}

// The check itself. Also used by the "Run supplier sync now" button, which
// passes $force so it fires regardless of gap and cooldown.
function vj_gh_watchdog($force = false) {
    if (!$force) {
        if (!get_option('vj_gh_on', 0)) return 'watchdog off';
        $lastFire = (int) get_option('vj_gh_lastfire', 0);
        if ($lastFire && (time() - $lastFire) < VJ_GH_COOLDOWN_S) return 'cooling down';
        $age = vj_gh_last_run_age();
        // Unknown age → do nothing rather than fire blindly every 15 minutes.
        if ($age === null) return 'last run unknown';
        if ($age < VJ_GH_MAX_GAP_H * 3600) return 'recent enough';
    }
    return vj_gh_dispatch();
}

// Every 15 minutes, piggybacking site traffic like the other VJ crons.
add_filter('cron_schedules', function ($s) {
    if (!isset($s['vj_quarter'])) $s['vj_quarter'] = array('interval' => 900, 'display' => 'Every 15 minutes (VJ)');
    return $s;
});
add_action('init', function () {
    if (!wp_next_scheduled('vj_gh_check')) wp_schedule_event(time() + 900, 'vj_quarter', 'vj_gh_check');
});
add_action('vj_gh_check', function () { vj_gh_watchdog(false); });

// Manual trigger from the settings page.
add_action('admin_init', function () {
    if (!isset($_GET['vj_run_sync']) || !current_user_can('manage_options')) return;
    $r = vj_gh_watchdog(true);
    if ($r === true) {
        wp_die('Supplier sync started ✓<br><br>GitHub is running the workflow now. Results appear in the app\'s Supplier sync screen in a few minutes.', 'VJ Supplier sync');
    }
    wp_die('Could not start the sync.<br><br><code>' . esc_html((string) $r) . '</code>', 'VJ Supplier sync');
});

// =====================================================================
//  STOCK MESSAGE FOR THE WHATSAPP BUSINESS AI (v4.5)
//
//  After each supplier sync, post the complete product list to Telegram:
//    1) a short instruction: delete the old knowledge, load this one
//    2) a .txt file — every product with team, price, sizes in stock, link
//  Telegram caps a text message at 4,096 characters and the full list is
//  many times that, so it goes as a file. Same layout as the app's
//  "📋 Stock message" button, built from the same product data.
// =====================================================================

function vj_kn_size_rank($sz) {
    $order = array('XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL', '4XL', '5XL');
    $i = array_search(strtoupper((string) $sz), $order, true);
    return $i === false ? 99 : $i;
}

// Returns array('text' => ..., 'total' => n, 'in' => n, 'out' => n).
function vj_build_product_knowledge() {
    $ids = wc_get_products(array('limit' => 1000, 'status' => 'publish', 'return' => 'ids', 'orderby' => 'title', 'order' => 'ASC'));
    $ins = array(); $outs = array();
    foreach ($ids as $pid) {
        $row = vj_shop_product_row(wc_get_product($pid));
        if (!$row || empty($row['name'])) continue;
        $inS = array(); $outS = array();
        foreach ((array) $row['sizes'] as $sz) {
            if (empty($sz['size'])) continue;
            if (!empty($sz['in_stock'])) $inS[] = strtoupper($sz['size']); else $outS[] = strtoupper($sz['size']);
        }
        usort($inS, function ($a, $b) { return vj_kn_size_rank($a) - vj_kn_size_rank($b); });
        usort($outS, function ($a, $b) { return vj_kn_size_rank($a) - vj_kn_size_rank($b); });
        $live = count($row['sizes']) ? count($inS) > 0 : !empty($row['in_stock']);
        $entry = array('row' => $row, 'in' => $inS, 'out' => $outS, 'live' => $live);
        if ($live) $ins[] = $entry; else $outs[] = $entry;
    }

    $lines = array();
    $lines[] = 'VISIONS JERSEY — PRODUCT KNOWLEDGE (exported ' . date_i18n('j M Y') . ')';
    $lines[] = "Rule for AI: share the product's Link when a customer shows interest. Stock changes daily — if unsure, say 'check the link, team will confirm'. For OUT OF STOCK products, suggest 2-3 in-stock alternatives (same team if possible).";
    $lines[] = '';
    foreach (array_merge($ins, $outs) as $e) {
        $r = $e['row'];
        $team = '';
        foreach ((array) $r['categories'] as $c) {
            if ($c !== '' && !preg_match('/^(uncategori[sz]ed|all|jerseys?|football|new|latest|retro|sale)$/i', $c)) { $team = $c; break; }
        }
        $price = ((float) $r['price'] > 0) ? '₹' . round((float) $r['price']) : 'See website';
        $lines[] = 'Product: ' . trim($r['name']);
        if ($team !== '') $lines[] = 'Team: ' . $team;
        $lines[] = 'Price: ' . $price . ' (+ ₹80 shipping)';
        if ($e['live']) {
            if ($e['in']) $lines[] = 'Sizes in stock: ' . implode(', ', $e['in']);
            if ($e['out']) $lines[] = 'Out of stock sizes: ' . implode(', ', $e['out']);
        } else {
            $lines[] = 'Status: OUT OF STOCK (suggest alternatives)';
        }
        $lines[] = 'Link: https://shop.visionsjersey.com/jersey/' . $r['slug'];
        $lines[] = '';
    }
    return array(
        'text' => trim(implode("\n", $lines)) . "\n",
        'total' => count($ins) + count($outs), 'in' => count($ins), 'out' => count($outs),
    );
}

// Send a .txt file to the Telegram chat (sendDocument, multipart).
function vj_telegram_send_document($filename, $content, $caption = '') {
    $cfg = get_option('vj_telegram');
    if (empty($cfg['token']) || empty($cfg['chat'])) return false;
    $b = 'VJ' . wp_generate_password(20, false, false);
    $body  = "--$b\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n" . $cfg['chat'] . "\r\n";
    if ($caption !== '') $body .= "--$b\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n" . $caption . "\r\n";
    $body .= "--$b\r\nContent-Disposition: form-data; name=\"document\"; filename=\"$filename\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n" . $content . "\r\n";
    $body .= "--$b--\r\n";
    $res = wp_remote_post('https://api.telegram.org/bot' . $cfg['token'] . '/sendDocument', array(
        'timeout' => 20,
        'headers' => array('Content-Type' => 'multipart/form-data; boundary=' . $b),
        'body' => $body,
    ));
    return !is_wp_error($res) && wp_remote_retrieve_response_code($res) === 200;
}

// Build + post. Returns true or an error string.
function vj_send_product_knowledge() {
    $k = vj_build_product_knowledge();
    if ($k['total'] < 1) return 'No published products found.';
    $when = date_i18n('j M Y, g:i a');
    $instr = "🧠 STOCK UPDATE for WhatsApp Business AI — $when\n\n"
           . "Delete the full product knowledge you currently have and upload the file below. "
           . "This is the full, updated list of ALL our products.\n\n"
           . "{$k['total']} products · {$k['in']} in stock · {$k['out']} out of stock";
    if (!vj_telegram_send($instr)) return 'Telegram not configured or the instruction message failed.';
    $fname = 'visions-jersey-products-' . date_i18n('Y-m-d-Hi') . '.txt';
    if (!vj_telegram_send_document($fname, $k['text'], 'Product knowledge — ' . $k['total'] . ' products')) return 'The product file failed to send.';
    update_option('vj_kn_lastsent', time(), false);
    return true;
}

// Timestamp of the newest sync_runs row, or 0 if unknown.
function vj_gh_last_run_ts() {
    $body = vj_supa_req('GET', 'sync_runs?select=ran_at&order=ran_at.desc&limit=1');
    if ($body === false || $body === true || !is_string($body)) return 0;
    $rows = json_decode($body, true);
    if (!is_array($rows) || !isset($rows[0]['ran_at'])) return 0;
    $t = strtotime((string) $rows[0]['ran_at']);
    return $t ? (int) $t : 0;
}

// Piggybacks the 15-minute watchdog tick: if a sync has finished since the
// last message we sent, send a fresh one. At most one message per sync run.
add_action('vj_gh_check', function () {
    if (!get_option('vj_kn_on', 1)) return;
    $ts = vj_gh_last_run_ts();
    if (!$ts) return;
    $lastRun = (int) get_option('vj_kn_lastrun', 0);
    if ($ts <= $lastRun) return;                       // nothing new since last send
    // Give the plugin's product mirror a moment to catch up with the run.
    if (time() - $ts < 120) return;
    update_option('vj_kn_lastrun', $ts, false);        // mark first so a slow send can't double-post
    vj_send_product_knowledge();
}, 20);

// Manual button.
add_action('admin_init', function () {
    if (!isset($_GET['vj_send_knowledge']) || !current_user_can('manage_options')) return;
    $r = vj_send_product_knowledge();
    if ($r === true) wp_die('Stock message sent to Telegram ✓<br><br>Two messages: the instruction, then the product file.', 'VJ Stock message');
    wp_die('Could not send.<br><br><code>' . esc_html((string) $r) . '</code>', 'VJ Stock message');
});

// =====================================================================
//  STOREFRONT PRICE FIX + OPTIONAL REDIRECT (v4.6)
// =====================================================================

// -- 1. Variation changes now reach the storefront -----------------------
// The supplier sync edits prices on the size variations, which fires the
// variation hooks below — not woocommerce_update_product — so the parent
// row in shop_products was never refreshed. Each parent is queued once and
// pushed 90 seconds later, so a batch of 40 size edits = one push.
function vj_queue_parent_push($variation_id) {
    $v = wc_get_product($variation_id);
    if (!$v) return;
    $pid = $v->is_type('variation') ? $v->get_parent_id() : $v->get_id();
    if (!$pid) return;
    if (!wp_next_scheduled('vj_push_parent', array((int) $pid))) {
        wp_schedule_single_event(time() + 90, 'vj_push_parent', array((int) $pid));
    }
}
add_action('woocommerce_update_product_variation', 'vj_queue_parent_push', 40, 1);
add_action('woocommerce_save_product_variation',   'vj_queue_parent_push', 40, 1);
add_action('vj_push_parent', function ($pid) {
    $p = wc_get_product($pid);
    if (!$p) return;
    $row = vj_shop_product_row($p);
    if ($row) vj_supa_req('POST', 'shop_products?on_conflict=id', array($row));
}, 10, 1);

// -- 2. Belt and braces: full product mirror ------------------------------
// (a) right after every supplier sync run is detected (same tick as the
//     Telegram stock message, run first so the file has the new prices), and
// (b) once an hour regardless, in case anything else changes a price.
add_action('vj_gh_check', function () {
    $ts = vj_gh_last_run_ts();
    if (!$ts) return;
    $last = (int) get_option('vj_mirror_lastrun', 0);
    if ($ts <= $last) return;
    if (time() - $ts < 90) return;               // let WooCommerce finish saving
    update_option('vj_mirror_lastrun', $ts, false);
    vj_sync_products_to_shop(500);
}, 15);                                           // before the stock message (priority 20)
add_action('vj_supa_hourly', function () { vj_sync_products_to_shop(500); }, 20);

// -- 3. Optional: send shoppers from the WordPress site to the fast store ---
// Only public catalogue pages. Admin, login, REST, cron, WooCommerce account
// and order pages, and anything that isn't a plain GET are left alone.
add_action('template_redirect', function () {
    if (!get_option('vj_redirect_on', 0)) return;
    if (is_admin() || wp_doing_ajax() || wp_doing_cron()) return;
    if (defined('REST_REQUEST') && REST_REQUEST) return;
    if (!isset($_SERVER['REQUEST_METHOD']) || $_SERVER['REQUEST_METHOD'] !== 'GET') return;
    if (is_user_logged_in() && current_user_can('edit_posts')) return;   // you, previewing
    if (function_exists('is_account_page') && (is_account_page() || is_checkout() || is_cart() || is_wc_endpoint_url())) return;
    if (isset($_GET['vj_no_redirect'])) return;

    $shop = 'https://shop.visionsjersey.com';
    $to = '';
    if (is_front_page() || is_home()) {
        $to = $shop . '/';
    } elseif (is_singular('product')) {
        $to = $shop . '/jersey/' . get_post_field('post_name', get_queried_object_id());
    } elseif ((function_exists('is_shop') && is_shop()) || is_product_category() || is_product_tag() || is_post_type_archive('product')) {
        $to = $shop . '/shop';
    }
    if ($to) { wp_redirect($to, 301); exit; }
});

// =====================================================================
//  META CATALOG FEED (v4.7)
//
//  A CSV of every published product in the exact layout Meta's catalog
//  reads. Links point at the fast store. Uploaded to a public Supabase
//  Storage bucket ("feeds") so Meta can fetch it on a schedule without
//  going through the host's bot wall. Rebuilt after each supplier sync
//  and every hour, same triggers as the storefront mirror.
// =====================================================================

function vj_build_meta_feed() {
    $ids = wc_get_products(array('limit' => 1000, 'status' => 'publish', 'return' => 'ids', 'orderby' => 'date', 'order' => 'DESC'));
    $out = fopen('php://temp', 'w+');
    fputcsv($out, array('id', 'title', 'description', 'availability', 'condition', 'price', 'link', 'image_link', 'additional_image_link', 'brand', 'product_type', 'google_product_category'));
    $n = 0;
    foreach ($ids as $pid) {
        $row = vj_shop_product_row(wc_get_product($pid));
        if (!$row || empty($row['images'][0]) || empty($row['slug'])) continue;
        $sizes = array();
        foreach ((array) $row['sizes'] as $sz) if (!empty($sz['in_stock'])) $sizes[] = strtoupper($sz['size']);
        $desc = vj_nice_title($row['name']) . '. Premium football jersey, quality checked, shipped across India in 24-48 hours.'
              . ($sizes ? ' Sizes in stock: ' . implode(', ', $sizes) . '.' : '');
        $cat = '';
        foreach ((array) $row['categories'] as $c) { if ($c !== '' && !preg_match('/^(uncategori[sz]ed|all|jerseys?)$/i', $c)) { $cat = $c; break; } }
        fputcsv($out, array(
            $row['id'],
            mb_substr(vj_nice_title($row['name']), 0, 150),
            mb_substr($desc, 0, 5000),
            !empty($row['in_stock']) ? 'in stock' : 'out of stock',
            'new',
            number_format((float) $row['price'], 2, '.', '') . ' INR',
            'https://shop.visionsjersey.com/jersey/' . $row['slug'],
            $row['images'][0],
            implode(',', array_slice($row['images'], 1, 4)),
            'Visions Jersey',
            $cat !== '' ? 'Football Jerseys > ' . $cat : 'Football Jerseys',
            '5322',   // Apparel & Accessories > Clothing > Activewear (Meta/Google taxonomy id)
        ));
        $n++;
    }
    rewind($out); $csv = stream_get_contents($out); fclose($out);
    return array('csv' => $csv, 'count' => $n);
}

// Upload to Supabase Storage. Returns the public URL or a string error.
function vj_upload_meta_feed($csv) {
    $key = get_option('vj_supabase_key');
    if (!$key) return 'Supabase key not set.';
    $path = 'feeds/meta-products.csv';
    $res = wp_remote_request(VJ_SUPA_URL . '/storage/v1/object/' . $path, array(
        'method' => 'POST', 'timeout' => 30,
        'headers' => array(
            'apikey' => $key, 'Authorization' => 'Bearer ' . $key,
            'Content-Type' => 'text/csv', 'x-upsert' => 'true', 'Cache-Control' => 'max-age=300',
        ),
        'body' => $csv,
    ));
    if (is_wp_error($res)) return 'network: ' . $res->get_error_message();
    $code = (int) wp_remote_retrieve_response_code($res);
    if ($code < 200 || $code >= 300) return $code . ': ' . substr((string) wp_remote_retrieve_body($res), 0, 220);
    $url = VJ_SUPA_URL . '/storage/v1/object/public/' . $path;
    update_option('vj_feed_url', $url, false);
    update_option('vj_feed_at', date_i18n('j M Y, g:i a'), false);
    delete_option('vj_feed_lasterr');
    return $url;
}

// -- Storefront product snapshot (v4.8) ----------------------------------
// The same rows the storefront reads from shop_products, saved as one JSON
// file on Supabase Storage. The shop loads this first (it's served from a
// CDN and answers in ~200 ms versus ~1.3 s for the live query), then checks
// the live table in the background. In-stock products only, newest first —
// exactly what the storefront's own query returns.
// v6.8: jerseys sold per product in the last 30 days (paid orders only).
function vj_sold_30d() {
    $out = array();
    if (!function_exists('wc_get_orders')) return $out;
    $orders = wc_get_orders(array('status' => array('wc-processing', 'wc-completed'), 'date_created' => '>' . (time() - 30 * 86400), 'limit' => 600, 'return' => 'objects'));
    foreach ($orders as $o) foreach ($o->get_items() as $it) {
        $pid = (string) $it->get_product_id();
        if ($pid === '' || $pid === '0') continue;
        $out[$pid] = (isset($out[$pid]) ? $out[$pid] : 0) + (int) $it->get_quantity();
    }
    return $out;
}

function vj_upload_products_snapshot() {
    $key = get_option('vj_supabase_key');
    if (!$key) return false;
    $ids = wc_get_products(array('limit' => 1000, 'status' => 'publish', 'return' => 'ids'));
    $sold = vj_sold_30d();                                   // v6.8: real orders → "Trending now" on the shop
    $rows = array();
    foreach ($ids as $pid) {
        $product = wc_get_product($pid);
        $row = vj_shop_product_row($product);
        if ($row && !empty($row['in_stock'])) {
            $dc = $product->get_date_created();
            $row['created_at'] = $dc ? $dc->date('c') : '';
            $row['sold_30d'] = isset($sold[(string) $pid]) ? (int) $sold[(string) $pid] : 0;
            $rows[] = $row;
        }
    }
    // newest first, matching order=updated_at.desc (all rows share this run's
    // timestamp, so fall back to product id descending as the tiebreak)
    usort($rows, function ($a, $b) { return (int) $b['id'] - (int) $a['id']; });
    $rows = array_slice($rows, 0, 500);
    $res = wp_remote_request(VJ_SUPA_URL . '/storage/v1/object/feeds/shop-products.json', array(
        'method' => 'POST', 'timeout' => 30,
        'headers' => array(
            'apikey' => $key, 'Authorization' => 'Bearer ' . $key,
            'Content-Type' => 'application/json', 'x-upsert' => 'true', 'Cache-Control' => 'max-age=300',
        ),
        'body' => wp_json_encode($rows),
    ));
    if (is_wp_error($res)) return false;
    $code = (int) wp_remote_retrieve_response_code($res);
    if ($code >= 200 && $code < 300) { update_option('vj_snapshot_at', date_i18n('j M Y, g:i a'), false); return true; }
    update_option('vj_snapshot_lasterr', $code . ': ' . substr((string) wp_remote_retrieve_body($res), 0, 200), false);
    return false;
}

function vj_refresh_meta_feed() {
    vj_upload_products_snapshot();   // same triggers as the feed: after sync, hourly, manual button
    $f = vj_build_meta_feed();
    if ($f['count'] < 1) { update_option('vj_feed_lasterr', 'No products with images.', false); return 'No products with images.'; }
    $r = vj_upload_meta_feed($f['csv']);
    if (strpos((string) $r, 'https://') !== 0) update_option('vj_feed_lasterr', $r, false);
    return $r;
}

// Same triggers as the storefront mirror: after each sync run, and hourly.
add_action('vj_gh_check', function () {
    $ts = vj_gh_last_run_ts();
    if (!$ts) return;
    $last = (int) get_option('vj_feed_lastrun', 0);
    if ($ts <= $last || time() - $ts < 120) return;
    update_option('vj_feed_lastrun', $ts, false);
    vj_refresh_meta_feed();
}, 17);
add_action('vj_supa_hourly', function () { vj_refresh_meta_feed(); }, 25);

// Manual button.
add_action('admin_init', function () {
    if (!isset($_GET['vj_build_feed']) || !current_user_can('manage_options')) return;
    $r = vj_refresh_meta_feed();
    if (strpos((string) $r, 'https://') === 0) {
        wp_die('Meta catalog feed built and uploaded ✓<br><br>Feed URL:<br><code>' . esc_html($r) . '</code><br><br>Paste this into Commerce Manager → your catalog → Data sources → Add items → Data feed → Scheduled.', 'VJ Meta feed');
    }
    wp_die('Could not build the feed.<br><br><code>' . esc_html((string) $r) . '</code><br><br>If it says the bucket does not exist, run the SQL I gave you first.', 'VJ Meta feed');
});

// =====================================================================
//  META PURCHASE ON REAL PAYMENT — CONVERSIONS API (v5.0)
//
//  The storefront used to tell Meta "Purchase" when the customer tapped
//  SEND PAYMENT SCREENSHOT. That is a claim, not a payment — someone can
//  tap it and never pay, and Meta then spends money finding more people
//  like them.
//
//  Now the browser only sends a harmless "PaymentClaimed" signal, and the
//  REAL Purchase is sent from here, once the order is actually marked
//  paid (processing or completed). The event id is the storefront ref, so
//  if a browser Purchase ever fires too, Meta de-duplicates them.
// =====================================================================

function vj_capi_hash($v) {
    $v = trim(strtolower((string) $v));
    return $v === '' ? '' : hash('sha256', $v);
}

// Phone must be digits with country code, no plus sign.
function vj_capi_phone($v) {
    $d = preg_replace('/\D+/', '', (string) $v);
    if ($d === '') return '';
    if (strlen($d) === 10) $d = '91' . $d;          // Indian mobile without code
    return hash('sha256', $d);
}

function vj_send_purchase_to_meta($order_id) {
    if (!get_option('vj_capi_on', 0)) return 'off';
    $pixel = trim((string) get_option('vj_capi_pixel', ''));
    $token = trim((string) get_option('vj_capi_token', ''));
    if ($pixel === '' || $token === '') return 'pixel id or token not set';

    $order = wc_get_order($order_id);
    if (!$order) return 'order not found';

    // Once per order, ever.
    if ($order->get_meta('_vj_capi_sent')) return 'already sent';

    $ref = $order->get_meta('_vj_ref');
    $event_id = $ref ? $ref : ('wc-' . $order->get_id());

    $user = array();
    if ($order->get_billing_email())      $user['em'] = array(vj_capi_hash($order->get_billing_email()));
    if ($order->get_billing_phone())      $user['ph'] = array(vj_capi_phone($order->get_billing_phone()));
    if ($order->get_billing_first_name()) $user['fn'] = array(vj_capi_hash($order->get_billing_first_name()));
    if ($order->get_billing_last_name())  $user['ln'] = array(vj_capi_hash($order->get_billing_last_name()));
    if ($order->get_billing_city())       $user['ct'] = array(vj_capi_hash($order->get_billing_city()));
    if ($order->get_billing_postcode())   $user['zp'] = array(vj_capi_hash($order->get_billing_postcode()));
    $user['country'] = array(vj_capi_hash('in'));
    // These two are what actually tie the sale to the ad click.
    if ($order->get_meta('_vj_fbp')) $user['fbp'] = $order->get_meta('_vj_fbp');
    if ($order->get_meta('_vj_fbc')) $user['fbc'] = $order->get_meta('_vj_fbc');
    if ($order->get_meta('_vj_ua'))  $user['client_user_agent'] = $order->get_meta('_vj_ua');
    if ($order->get_customer_ip_address()) $user['client_ip_address'] = $order->get_customer_ip_address();

    $ids = array(); $num = 0;
    foreach ($order->get_items() as $it) {
        $pid = $it->get_product_id();
        if ($pid) $ids[] = (string) $pid;
        $num += (int) $it->get_quantity();
    }

    // Meta accepts events up to 7 days old. Use the time the order was placed
    // so the attribution window lines up with the ad click.
    $when = $order->get_date_created() ? $order->get_date_created()->getTimestamp() : time();
    if (time() - $when > 6 * 86400) $when = time() - 6 * 86400;

    $payload = array('data' => array(array(
        'event_name' => 'Purchase',
        'event_time' => $when,
        'event_id' => $event_id,
        'event_source_url' => 'https://shop.visionsjersey.com/',
        'action_source' => 'website',
        'user_data' => $user,
        'custom_data' => array(
            'currency' => 'INR',
            'value' => (float) $order->get_total(),
            'content_type' => 'product',
            'content_ids' => $ids,
            'num_items' => $num,
            'order_id' => (string) $order->get_order_number(),
        ),
    )));

    $res = wp_remote_post('https://graph.facebook.com/v21.0/' . $pixel . '/events?access_token=' . rawurlencode($token), array(
        'timeout' => 20,
        'headers' => array('Content-Type' => 'application/json'),
        'body' => wp_json_encode($payload),
    ));
    if (is_wp_error($res)) { update_option('vj_capi_lasterr', 'network: ' . $res->get_error_message(), false); return 'network error'; }
    $code = (int) wp_remote_retrieve_response_code($res);
    $body = (string) wp_remote_retrieve_body($res);
    if ($code >= 200 && $code < 300) {
        $order->update_meta_data('_vj_capi_sent', time());
        $order->add_order_note('Purchase sent to Meta (Conversions API) — ₹' . (float) $order->get_total());
        $order->save();
        update_option('vj_capi_last', date_i18n('j M Y, g:i a') . ' · order ' . $order->get_order_number(), false);
        delete_option('vj_capi_lasterr');
        return true;
    }
    update_option('vj_capi_lasterr', $code . ': ' . substr($body, 0, 220), false);
    return $code . ': ' . substr($body, 0, 200);
}

// Fires the moment YOU mark the order paid — from the app, or in WP admin.
add_action('woocommerce_order_status_processing', function ($oid) { vj_send_purchase_to_meta($oid); }, 40, 1);
add_action('woocommerce_order_status_completed',  function ($oid) { vj_send_purchase_to_meta($oid); }, 40, 1);
add_action('woocommerce_payment_complete',        function ($oid) { vj_send_purchase_to_meta($oid); }, 40, 1);

// Manual: ?vj_capi_test=<order number> in wp-admin, to prove it works.
add_action('admin_init', function () {
    if (!isset($_GET['vj_capi_test']) || !current_user_can('manage_options')) return;
    $oid = (int) $_GET['vj_capi_test'];
    $r = vj_send_purchase_to_meta($oid);
    if ($r === true) wp_die('Purchase sent to Meta for order ' . $oid . ' ✓<br><br>Check Events Manager → Test events, or the Overview a few minutes from now.', 'VJ Conversions API');
    wp_die('Not sent.<br><br><code>' . esc_html((string) $r) . '</code>', 'VJ Conversions API');
});

// ---------------------------------------------------------------------
//  v6.7 SAFETY NET — paid orders that never reached Meta
//
//  The hook above only fires at the moment an order turns Processing. If the
//  switch was off, the token was missing/expired, or the site was unreachable
//  at that second, the sale was simply never sent — and Meta's pixel showed
//  "PaymentClaimed" but no "Purchase". This sweep looks at every paid order
//  of the last 7 days (Meta accepts events up to 7 days old) that has no
//  _vj_capi_sent mark and sends it. Runs by itself every 20 minutes
//  (piggybacking the Supabase tick) and on demand:
//      /wp-admin/?vj_capi_backfill=1
// ---------------------------------------------------------------------
function vj_capi_unsent($limit = 120) {
    if (!function_exists('wc_get_orders')) return array();
    $orders = wc_get_orders(array(
        'status'       => array('wc-processing', 'wc-completed'),
        'date_created' => '>' . (time() - 7 * 86400 + 3600),     // just inside Meta's 7-day window
        'limit'        => $limit,
        'orderby'      => 'date',
        'order'        => 'DESC',
        'return'       => 'objects',
    ));
    $out = array();
    foreach ($orders as $o) {
        if ($o->get_meta('_vj_capi_sent')) continue;
        if ((float) $o->get_total() <= 0) continue;
        $out[] = $o;
    }
    return $out;
}

function vj_capi_sweep($max = 40) {
    if (!get_option('vj_capi_on', 0)) return array('skipped' => 'switch off');
    $res = array('sent' => 0, 'failed' => 0, 'errors' => array());
    foreach (vj_capi_unsent($max) as $o) {
        $r = vj_send_purchase_to_meta($o->get_id());
        if ($r === true) $res['sent']++;
        else { $res['failed']++; $res['errors'][$o->get_order_number()] = (string) $r; if ($res['failed'] >= 3) break; }   // token broken? stop hammering
    }
    if ($res['sent']) update_option('vj_capi_sweep_last', date_i18n('j M Y, g:i a') . ' · ' . $res['sent'] . ' sent', false);
    return $res;
}

// Every minute tick → at most one sweep per 20 minutes.
add_action('vj_supa_tick', function () {
    if (!get_option('vj_capi_on', 0)) return;
    if (get_transient('vj_capi_sweep_lock')) return;
    set_transient('vj_capi_sweep_lock', 1, 20 * MINUTE_IN_SECONDS);
    vj_capi_sweep(40);
}, 20);

// Plain-English state for the settings page.
function vj_capi_status() {
    $on = (bool) get_option('vj_capi_on', 0);
    $pixel = trim((string) get_option('vj_capi_pixel', ''));
    $tok = trim((string) get_option('vj_capi_token', '')) !== '';
    $waiting = count(vj_capi_unsent(120));
    $why = array();
    if (!$on) $why[] = 'the switch above is OFF';
    if ($pixel === '') $why[] = 'no pixel ID';
    if (!$tok) $why[] = 'no Conversions API token';
    $url = esc_url(admin_url('?vj_capi_backfill=1'));
    if ($why) $html = '<b style="color:#b32d2e">Not sending</b> — ' . esc_html(implode(', ', $why)) . '. Fix, Save, then open <a href="' . $url . '">Send the last 7 days now</a>.';
    elseif ($waiting) $html = '<b style="color:#b45309">' . $waiting . ' paid order' . ($waiting === 1 ? '' : 's') . ' from the last 7 days not sent yet</b> — sent automatically within 20 min, or <a href="' . $url . '">send them now</a>.';
    else $html = '<b style="color:#0a7">Up to date</b> — every paid order of the last 7 days has been sent to Meta.' . (get_option('vj_capi_sweep_last') ? ' Last catch-up: ' . esc_html(get_option('vj_capi_sweep_last')) : '');
    return array('ok' => !$why, 'waiting' => $waiting, 'html' => $html);
}

// Manual: send every unsent paid order of the last 7 days, with a report.
add_action('admin_init', function () {
    if (!isset($_GET['vj_capi_backfill']) || !current_user_can('manage_options')) return;
    delete_transient('vj_capi_sweep_lock');
    $before = count(vj_capi_unsent(120));
    $r = vj_capi_sweep(120);
    $lines = array('<h2>Meta Purchase catch-up</h2>');
    if (isset($r['skipped'])) $lines[] = '<p style="color:#b32d2e">Nothing sent: ' . esc_html($r['skipped']) . '. Turn on "Meta Purchase on payment" in Settings → VJ Push and save.</p>';
    else {
        $lines[] = '<p>Paid orders (last 7 days) waiting before: <b>' . $before . '</b><br>Sent now: <b style="color:#0a7">' . (int) $r['sent'] . '</b><br>Failed: <b style="color:' . ($r['failed'] ? '#b32d2e' : '#0a7') . '">' . (int) $r['failed'] . '</b></p>';
        foreach ($r['errors'] as $num => $err) $lines[] = '<p>#' . esc_html($num) . ': <code>' . esc_html($err) . '</code></p>';
        $lines[] = '<p style="color:#666">Orders older than 7 days cannot be sent — Meta refuses events older than that. From now on every paid order goes within seconds, and this catch-up also runs by itself every 20 minutes.</p>';
    }
    wp_die(implode('', $lines) . '<p><a href="' . esc_url(admin_url('options-general.php?page=visions-jersey')) . '">← back to settings</a></p>', 'VJ Conversions API');
});

// =====================================================================
//  EMAIL LIST FROM CHECKOUT (v5.1)
//
//  The storefront saves an email into Supabase `shop_leads` as soon as it
//  is typed at checkout — before the order is finished. This marks anyone
//  who has actually bought as 'customer', so marketing stops for them.
//
//  Marking happens the moment an order reaches processing (which is when
//  you mark it paid) or completed.
// =====================================================================

function vj_mark_lead_customer($order_id) {
    $order = wc_get_order($order_id);
    if (!$order) return;
    $email = strtolower(trim((string) $order->get_billing_email()));
    if ($email === '' || !is_email($email)) return;
    // PATCH is a no-op if the address was never captured, which is fine.
    vj_supa_req('PATCH', 'shop_leads?email=eq.' . rawurlencode($email), array(
        'status' => 'customer',
        'updated_at' => gmdate('c'),
    ));
}
add_action('woocommerce_order_status_processing', 'vj_mark_lead_customer', 50, 1);
add_action('woocommerce_order_status_completed',  'vj_mark_lead_customer', 50, 1);

// How many addresses are on the list, shown on the settings page.
function vj_lead_counts() {
    $body = vj_supa_req('GET', 'shop_leads?select=status');
    if (!is_string($body)) return null;
    $rows = json_decode($body, true);
    if (!is_array($rows)) return null;
    $open = 0; $cust = 0;
    foreach ($rows as $r) {
        if (($r['status'] ?? '') === 'customer') $cust++; else $open++;
    }
    return array('open' => $open, 'customer' => $cust, 'total' => count($rows));
}

// Download the marketing list as CSV — everyone who is not already a customer.
add_action('admin_init', function () {
    if (!isset($_GET['vj_leads_csv']) || !current_user_can('manage_options')) return;
    $body = vj_supa_req('GET', 'shop_leads?select=*&status=eq.open&order=updated_at.desc&limit=5000');
    $rows = is_string($body) ? json_decode($body, true) : array();
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename=visions-leads-' . date('Y-m-d') . '.csv');
    $out = fopen('php://output', 'w');
    fputcsv($out, array('email', 'name', 'cart_total', 'last_seen'));
    foreach ((array) $rows as $r) {
        fputcsv($out, array($r['email'] ?? '', $r['name'] ?? '', $r['total'] ?? '', $r['updated_at'] ?? ''));
    }
    fclose($out);
    exit;
});

// v7.4.1 — Everyone who has PAID (processing + completed), one row per person,
// in the column names Meta's customer-list upload recognises on its own.
// Open /wp-admin/?vj_buyers_csv=1 while logged in as admin → file downloads.
// Upload it in Meta → Audiences → Create → Custom audience → Customer list.
// Meta hashes the file in your browser before anything is sent.
add_action('admin_init', function () {
    if (!isset($_GET['vj_buyers_csv']) || !current_user_can('manage_options')) return;
    if (function_exists('wp_raise_memory_limit')) wp_raise_memory_limit('admin');
    if (function_exists('set_time_limit')) @set_time_limit(180);
    try {
    $states = (function_exists('WC') && WC()->countries) ? (array) WC()->countries->get_states('IN') : array();
    $people = array();
    // 200 at a time; only real orders (refunds share the same statuses and have no customer details)
    for ($page = 1; $page <= 50; $page++) {
    $batch = wc_get_orders(array('type' => 'shop_order', 'status' => array('wc-processing', 'wc-completed'), 'limit' => 200, 'page' => $page, 'orderby' => 'ID', 'order' => 'ASC'));
    if (empty($batch)) break;
    foreach ($batch as $o) {
        if (!($o instanceof WC_Order)) continue;
        $sp = method_exists($o, 'get_shipping_phone') ? $o->get_shipping_phone() : '';
        $d = preg_replace('/\D/', '', (string) ($o->get_billing_phone() ?: $sp));
        if (strlen($d) === 11 && $d[0] === '0') $d = substr($d, 1);
        if (strlen($d) === 10) $d = '91' . $d;
        $phone = (strlen($d) === 12 && strpos($d, '91') === 0) ? $d : '';
        $email = strtolower(trim((string) $o->get_billing_email()));
        if (!is_email($email) || preg_match('/visionsjersey|example\.|^test@/', $email)) $email = '';
        $key = $phone ?: $email;
        if ($key === '') continue;
        if (isset($people[$key])) {                       // repeat buyer: keep first row, fill a missing email
            if ($people[$key]['email'] === '' && $email !== '') $people[$key]['email'] = $email;
            continue;
        }
        $first = trim((string) ($o->get_billing_first_name() ?: $o->get_shipping_first_name()));
        $last  = trim((string) ($o->get_billing_last_name()  ?: $o->get_shipping_last_name()));
        if ($last === '' && strpos($first, ' ') !== false) { $last = substr($first, strpos($first, ' ') + 1); $first = strtok($first, ' '); }
        $st = (string) ($o->get_billing_state() ?: $o->get_shipping_state());
        if (isset($states[$st])) $st = $states[$st];       // "MH" → "Maharashtra"
        $people[$key] = array(
            'email'   => $email,
            'phone'   => $phone,
            'fn'      => strtolower($first),
            'ln'      => strtolower($last),
            'ct'      => strtolower((string) ($o->get_billing_city() ?: $o->get_shipping_city())),
            'st'      => strtolower($st),
            'zip'     => substr(preg_replace('/\D/', '', (string) ($o->get_billing_postcode() ?: $o->get_shipping_postcode())), 0, 6),
            'country' => 'in',
        );
    }
    if (count($batch) < 200) break;
    }
    } catch (\Throwable $e) {
        wp_die('Buyers export stopped: ' . esc_html($e->getMessage()) . ' (line ' . (int) $e->getLine() . '). Nothing was changed on the site — send this text to Claude.', 'VJ buyers export');
    }
    if (!$people) wp_die('No paid orders found (processing / completed). Nothing to export.', 'VJ buyers export');
    nocache_headers();
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename=visions-buyers-meta-' . date('Y-m-d') . '.csv');
    $out = fopen('php://output', 'w');
    fputcsv($out, array('email', 'phone', 'fn', 'ln', 'ct', 'st', 'zip', 'country'));
    foreach ($people as $p) fputcsv($out, array_values($p));
    fclose($out);
    exit;
});

// =====================================================================
//  YOUR OWN PRODUCT PHOTOS (v5.3)
//
//  The app writes a row into Supabase `product_edits` when you replace a
//  product photo, keyed "<slug>#<index>" with pinned = true.
//
//  Everything that leaves this site — the storefront mirror, the JSON
//  snapshot and the Meta catalogue feed — swaps in your image for that
//  slot. The supplier can change their photo as often as they like; yours
//  stays. Sizes, stock and price keep syncing normally.
//
//  The map is fetched once per request and cached for 5 minutes, so a full
//  178-product sync makes one call, not 178.
// =====================================================================

function vj_pinned_map() {
    static $map = null;
    if ($map !== null) return $map;

    $cached = get_transient('vj_pinned_images');
    if (is_array($cached)) { $map = $cached; return $map; }

    $map = array();
    $body = vj_supa_req('GET', 'product_edits?select=product_id,edited_url,pinned&pinned=is.true');
    if (is_string($body)) {
        $rows = json_decode($body, true);
        if (is_array($rows)) {
            foreach ($rows as $r) {
                if (empty($r['product_id']) || empty($r['edited_url'])) continue;
                $map[$r['product_id']] = $r['edited_url'];
            }
        }
    }
    set_transient('vj_pinned_images', $map, 5 * MINUTE_IN_SECONDS);
    return $map;
}

function vj_pinned_map_has($slug, $index) {
    $map = vj_pinned_map();
    return isset($map[$slug . '#' . $index]);
}

// Swap in your photo wherever you've set one for that position.
function vj_apply_pinned_images($slug, $images) {
    $map = vj_pinned_map();
    if (!$map) return $images;
    $out = array_values((array) $images);
    foreach ($out as $i => $src) {
        $key = $slug . '#' . $i;
        if (isset($map[$key])) $out[$i] = $map[$key];
    }
    // A photo pinned at a slot the supplier no longer sends still gets used.
    for ($i = count($out); $i < 10; $i++) {
        $key = $slug . '#' . $i;
        if (isset($map[$key])) $out[] = $map[$key]; else break;
    }
    return $out;
}

// Clearing the cache makes a change visible immediately instead of in 5 min.
add_action('admin_init', function () {
    if (!isset($_GET['vj_flush_images']) || !current_user_can('manage_options')) return;
    delete_transient('vj_pinned_images');
    $n = count(vj_pinned_map());
    wp_die('Pinned-image cache cleared. ' . (int) $n . ' pinned photo(s) found.<br><br>Run <b>Sync products to storefront</b> to push them out now.', 'VJ product photos');
});

// =====================================================================
//  PUSH ONE PRODUCT AFTER A PHOTO IS PINNED (v5.4)
//
//  The app calls this the moment you finish replacing a product's photos.
//  It pushes THAT ONE product to the storefront immediately — nothing else
//  is touched, and no full catalogue rebuild happens.
//
//  The snapshot file the storefront loads first covers every product, so
//  rebuilding it per product would be wasteful. Instead one rebuild is
//  scheduled a few minutes after your LAST pin, so a run of 20 products
//  costs one rebuild rather than twenty.
// =====================================================================

define('VJ_SNAPSHOT_DEBOUNCE', 180);   // seconds after the last pin

function vj_push_one_product_by_slug($slug) {
    $slug = sanitize_title((string) $slug);
    if ($slug === '') return array('ok' => false, 'why' => 'no slug');

    // Your pins may have been written a second ago — never serve a stale map.
    delete_transient('vj_pinned_images');

    $posts = get_posts(array(
        'post_type' => 'product', 'name' => $slug, 'post_status' => 'publish',
        'numberposts' => 1, 'fields' => 'ids',
    ));
    if (!$posts) return array('ok' => false, 'why' => 'product not found');

    $row = vj_shop_product_row(wc_get_product($posts[0]));
    if (!$row) return array('ok' => false, 'why' => 'product not publishable');

    // vj_supa_req already sends Prefer: resolution=merge-duplicates.
    $res = vj_supa_req('POST', 'shop_products?on_conflict=id', array($row));
    $ok = ($res === true || is_string($res));

    // One snapshot rebuild after the last pin, not one per pin.
    wp_clear_scheduled_hook('vj_rebuild_snapshot');
    wp_schedule_single_event(time() + VJ_SNAPSHOT_DEBOUNCE, 'vj_rebuild_snapshot');

    return array('ok' => $ok, 'slug' => $slug, 'images' => isset($row['images']) ? count((array) $row['images']) : 0);
}

add_action('vj_rebuild_snapshot', function () {
    delete_transient('vj_pinned_images');
    if (function_exists('vj_upload_products_snapshot')) vj_upload_products_snapshot();
    if (function_exists('vj_build_meta_feed')) vj_refresh_meta_feed();
});

// The app pings this. Same pattern as /tick: open, harmless, and it can only
// re-publish a product that already exists on this site.
add_action('rest_api_init', function () {
    register_rest_route('vj/v1', '/pin', array(
        'methods'  => array('GET', 'POST'),
        'permission_callback' => '__return_true',
        'callback' => function ($req) {
            $out = vj_push_one_product_by_slug($req->get_param('slug'));
            $r = new WP_REST_Response($out, 200);
            $r->header('Access-Control-Allow-Origin', '*');
            return $r;
        },
    ));
});

// Manual fallback, in case the ping is blocked:
//   /wp-admin/?vj_push_one=<slug>
add_action('admin_init', function () {
    if (!isset($_GET['vj_push_one']) || !current_user_can('manage_options')) return;
    $out = vj_push_one_product_by_slug($_GET['vj_push_one']);
    wp_die('<pre>' . esc_html(print_r($out, true)) . '</pre>', 'VJ push one product');
});

// =====================================================================
//  ORDER STATUS REACHES THE CUSTOMER IMMEDIATELY (v5.5)
//
//  vj_sync_shop_order_status() only runs on WordPress cron, and WP cron
//  only fires when somebody visits the site. So a customer could mark-paid
//  order sat on "Pending payment" on shop.visionsjersey.com for a long time.
//
//  This pushes the status to that customer's order row the moment the
//  status changes — from the app, or from WP admin. The cron sweep stays as
//  a backstop for anything this misses.
// =====================================================================

function vj_push_status_to_storefront($order_id) {
    $order = wc_get_order($order_id);
    if (!$order) return false;

    // Tracking can live in several plugins' meta keys — check the usual ones.
    $tracking = '';
    foreach (array('_tracking_number', '_vj_tracking', '_wc_shipment_tracking_number') as $key) {
        $v = $order->get_meta($key);
        if ($v) { $tracking = is_array($v) ? reset($v) : (string) $v; break; }
    }
    if (!$tracking) {
        $items = $order->get_meta('_wc_shipment_tracking_items');
        if (is_array($items) && !empty($items[0]['tracking_number'])) $tracking = $items[0]['tracking_number'];
    }

    $body = array('status' => $order->get_status(), 'tracking' => $tracking ?: null);

    // Match on the storefront reference when we have it — that is the row the
    // customer is actually looking at. Fall back to the WooCommerce id.
    $ref = $order->get_meta('_vj_ref');
    if ($ref) {
        $res = vj_supa_req('PATCH', 'shop_orders?ref=eq.' . rawurlencode($ref), $body);
        if ($res !== false) return true;
    }
    return vj_supa_req('PATCH', 'shop_orders?woo_order_id=eq.' . rawurlencode((string) $order->get_id()), $body) !== false;
}

add_action('woocommerce_order_status_changed', function ($oid) { vj_push_status_to_storefront($oid); }, 35, 1);
add_action('woocommerce_payment_complete',     function ($oid) { vj_push_status_to_storefront($oid); }, 35, 1);

// Manual check: /wp-admin/?vj_push_status=<woocommerce order number>
add_action('admin_init', function () {
    if (!isset($_GET['vj_push_status']) || !current_user_can('manage_options')) return;
    $oid = absint($_GET['vj_push_status']);
    $order = wc_get_order($oid);
    if (!$order) wp_die('No order ' . $oid);
    $ok = vj_push_status_to_storefront($oid);
    wp_die('Order ' . $oid . ' · status <b>' . esc_html($order->get_status()) . '</b> · ref <b>'
        . esc_html((string) $order->get_meta('_vj_ref')) . '</b><br><br>'
        . ($ok ? 'Pushed to the storefront ✓' : 'Push failed — last Supabase error: <code>'
        . esc_html((string) get_option('vj_supa_lasterr', 'none')) . '</code>'), 'VJ order status');
});

// =====================================================================
//  PROPER CAPITALISATION FOR THE META FEED (v5.6)
//
//  Meta flags catalogue titles that are entirely uppercase — 169 of your
//  170 items were marked "should fix", which holds ads back.
//
//  This converts "REAL MADRID 26-27 HOME KIT EMBROIDERY PREMIUM" into
//  "Real Madrid 26-27 Home Kit Embroidery Premium" for the FEED ONLY.
//  Your storefront, your app and WooCommerce keep the titles exactly as
//  they are — nothing else changes.
// =====================================================================

function vj_nice_title($name) {
    $name = trim(preg_replace('/\s+/', ' ', (string) $name));
    if ($name === '') return '';

    // Already mixed case? Leave it alone — someone typed it deliberately.
    if ($name !== mb_strtoupper($name, 'UTF-8')) return $name;

    // Words that must stay capitalised as they are.
    $keep = array(
        'FC','AC','SC','CF','AFC','PSG','RM','UCL','UEFA','GK','XI','II','III','IV',
        'USA','UAE','UK','AS','SS','BVB','ATM','RCD','PSV','AZ','FK','NK','HSV','VFB','VFL','TSG','SV',
        'XL','XXL','XXXL','2XL','3XL','S','M','L',
    );
    $keepMap = array_flip($keep);

    // Small words stay lowercase unless they start the title.
    $small = array_flip(array('of','the','and','a','an','in','on','at','to','for','with','vs'));

    $out = array();
    $words = explode(' ', $name);
    foreach ($words as $i => $w) {
        if ($w === '') continue;
        $bare = preg_replace('/[^A-Za-z0-9]/', '', $w);

        // Leave anything with digits untouched: 26-27, 2011-12, 1998-99.
        if (preg_match('/\d/', $w)) { $out[] = $w; continue; }

        // Short all-caps tokens are initials, not words: B&R, R&D, AC/DC.
        if (mb_strlen($bare, 'UTF-8') <= 3 && preg_match('/[&\/.]/', $w)) { $out[] = $w; continue; }

        if (isset($keepMap[mb_strtoupper($bare, 'UTF-8')])) { $out[] = mb_strtoupper($w, 'UTF-8'); continue; }

        $lower = mb_strtolower($w, 'UTF-8');
        if ($i > 0 && isset($small[$lower])) { $out[] = $lower; continue; }

        // Capitalise after a hyphen or apostrophe too: "Saint-Germain", "O'Neill".
        $out[] = preg_replace_callback('/(^|[-\'&\/])([a-z\x{00E0}-\x{00FF}])/u', function ($m) {
            return $m[1] . mb_strtoupper($m[2], 'UTF-8');
        }, $lower);
    }
    return implode(' ', $out);
}

// =====================================================================
//  v5.8/5.9 — Shipment updates for customers (India Post via Shipway or 17TRACK)
//
//  Setup once (admin, logged in) — Shipway (Indian, pays by UPI):
//     /wp-admin/?vj_set_shipway=1&user=YOUR_SHIPWAY_LOGIN_EMAIL&key=YOUR_LICENCE_KEY
//  (or 17TRACK: /wp-admin/?vj_set_17track=1&key=KEY — then paste the webhook URL it shows)
//  Shipway has no webhook, so the plugin asks it every 20 minutes about each
//  parcel that is still on the way (max 25 parcels per minute).
//
//  How it works
//   1. The moment an order gets its tracking number (Shipped in the dashboard
//      / app, or WP admin) it is registered with 17TRACK. A sweep every hour
//      catches anything missed.
//   2. 17TRACK pushes every status change to /wp-json/vj/v1/track-hook.
//      Customer emails:  out for delivery · delivery attempt failed
//                        (re-attempt) · delivered · parcel returned to us (RTO:
//                        reship ₹80, WhatsApp number).  Each at most once.
//      You (Telegram):   every exception / failed attempt / RTO / expired.
//   3. Every update is mirrored to Supabase (vj_track) for the dashboard's
//      Shipped list and Notifications, and to shop_orders.track_status for
//      the customer's order page.
//   4. The dashboard can also set a stage by hand (woo_commands action
//      'track_set') — same emails, same bookkeeping — for the rare parcel
//      17TRACK can't follow.
// =====================================================================

define('VJ_TRACK_API', 'https://api.17track.net/track/v2.2/');
define('VJ_SHIPWAY_API', 'https://shipway.in/api/');
function vj_track_provider() { return get_option('vj_shipway_key') ? 'shipway' : (get_option('vj_17track_key') ? '17track' : ''); }
function vj_shipway_call($path, $body) {
    $body = array_merge(array('username' => get_option('vj_shipway_user'), 'password' => get_option('vj_shipway_key')), $body);
    $res = wp_remote_post(VJ_SHIPWAY_API . $path, array('timeout' => 15, 'headers' => array('Content-Type' => 'application/json'), 'body' => wp_json_encode($body)));
    if (is_wp_error($res)) return array('error' => $res->get_error_message());
    $j = json_decode(wp_remote_retrieve_body($res), true);
    return is_array($j) ? $j : array('error' => 'bad reply: ' . substr((string) wp_remote_retrieve_body($res), 0, 120));
}
add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_set_shipway'])) {
        $u = isset($_GET['user']) ? sanitize_text_field(wp_unslash($_GET['user'])) : '';
        $k = isset($_GET['key']) ? sanitize_text_field(wp_unslash($_GET['key'])) : '';
        if ($u && $k) { update_option('vj_shipway_user', $u, false); update_option('vj_shipway_key', $k, false); }
        if (!get_option('vj_shipway_key')) wp_die('Usage: /wp-admin/?vj_set_shipway=1&user=LOGIN_EMAIL&key=LICENCE_KEY', 'VJ Tracking');
        // find India Post's carrier id once
        $list = vj_shipway_call('carriers', array());
        $found = ''; $names = array();
        foreach ((is_array($list) ? $list : array()) as $row) {
            if (!is_array($row)) continue;
            $name = isset($row['name']) ? $row['name'] : (isset($row['carrier_name']) ? $row['carrier_name'] : '');
            $id = isset($row['id']) ? $row['id'] : (isset($row['carrier_id']) ? $row['carrier_id'] : '');
            if ($name) $names[] = $id . ' = ' . $name;
            if (!$found && preg_match('/india\s*post|speed\s*post/i', (string) $name)) $found = (string) $id;
        }
        if (isset($_GET['carrier'])) $found = sanitize_text_field(wp_unslash($_GET['carrier']));
        if ($found) update_option('vj_shipway_carrier', $found, false);
        wp_die('Shipway saved ✓ (' . esc_html(get_option('vj_shipway_user')) . ')<br>India Post carrier id: <b>' . esc_html(get_option('vj_shipway_carrier') ?: 'NOT FOUND — add &carrier=ID from the list below') . '</b><br><br>'
            . 'Test: <code>/wp-admin/?vj_track_test=1&amp;order=ORDER_NUMBER</code><br><br><small>' . esc_html(implode(' · ', array_slice($names, 0, 120))) . '</small>', 'VJ Tracking');
    }
    if (isset($_GET['vj_admin_mail'])) {              // v6.5: 1 = WooCommerce's admin "new order" emails back on, 0 = off
        update_option('vj_admin_mail', absint($_GET['vj_admin_mail']) ? '1' : '0', false);
        wp_die('WooCommerce admin "new order" emails: <b>' . (get_option('vj_admin_mail', '0') === '1' ? 'ON' : 'OFF') . '</b>', 'VJ Email');
    }
    if (isset($_GET['vj_tg_new_orders'])) {           // 0 = stop the "new order" Telegram messages, 1 = back on
        update_option('vj_tg_new_orders', absint($_GET['vj_tg_new_orders']) ? '1' : '0', false);
        wp_die('New-order Telegram messages: <b>' . (get_option('vj_tg_new_orders', '1') === '0' ? 'OFF' : 'ON') . '</b>', 'VJ Telegram');
    }
});
define('VJ_RESHIP_FEE', 80);
define('VJ_RESHIP_WA', '');                      // v6.9: no WhatsApp number — customers confirm in their ticket chat

add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_set_17track'])) {
        $key = isset($_GET['key']) ? sanitize_text_field(wp_unslash($_GET['key'])) : '';
        if ($key) update_option('vj_17track_key', $key, false);
        if (!get_option('vj_track_secret')) update_option('vj_track_secret', wp_generate_password(24, false, false), false);
        $hook = rest_url('vj/v1/track-hook') . '?k=' . get_option('vj_track_secret');
        wp_die('17TRACK key ' . (get_option('vj_17track_key') ? 'saved ✓' : 'MISSING — add &key=YOUR_KEY') . '<br><br>'
            . 'Webhook URL — paste this in 17TRACK → API → Settings → Webhook:<br><code>' . esc_html($hook) . '</code><br><br>'
            . 'Test: <code>/wp-admin/?vj_track_test=1&amp;order=ORDER_NUMBER</code> registers that order\'s tracking number now.', 'VJ Tracking');
    }
    if (isset($_GET['vj_track_test'])) {
        $o = wc_get_order(absint($_GET['order']));
        if (!$o) wp_die('Order not found', 'VJ Tracking');
        $r = vj_track_register($o, true);
        wp_die('<pre>' . esc_html(print_r($r, true)) . '</pre>', 'VJ Tracking');
    }
});

function vj_track_number($order) {
    foreach (array('_vj_tracking', '_tracking_number', '_wc_shipment_tracking_number') as $key) {
        $v = $order->get_meta($key);
        if ($v) return strtoupper(preg_replace('/\s+/', '', is_array($v) ? reset($v) : (string) $v));
    }
    $items = $order->get_meta('_wc_shipment_tracking_items');
    if (is_array($items) && !empty($items[0]['tracking_number'])) return strtoupper(preg_replace('/\s+/', '', $items[0]['tracking_number']));
    return '';
}

// Register a tracking number with 17TRACK (once per order). Returns an array for the test page.
function vj_track_register($order, $force = false) {
    $prov = vj_track_provider();
    if (!$prov) return array('error' => 'no tracking provider set (vj_set_shipway / vj_set_17track)');
    $num = vj_track_number($order);
    if (!$num) return array('error' => 'order has no tracking number');
    if (!$force && $order->get_meta('_vj_track_reg')) return array('skipped' => 'already registered');
    if ($prov === 'shipway') {
        $items = array(); foreach ($order->get_items() as $it) $items[] = $it->get_name();
        $j = vj_shipway_call('pushOrderData', array(
            'carrier_id' => (string) get_option('vj_shipway_carrier', ''), 'awb' => $num, 'order_id' => (string) $order->get_id(),
            'email' => $order->get_billing_email() ?: 'N/A', 'phone' => preg_replace('/\D+/', '', (string) $order->get_billing_phone()) ?: 'N/A',
            'first_name' => $order->get_billing_first_name() ?: 'N/A', 'last_name' => $order->get_billing_last_name() ?: 'N/A',
            'products' => mb_substr(implode(', ', $items) ?: 'Jersey', 0, 35), 'company' => 'Visions Jersey',
        ));
        $ok = isset($j['status']) && strtolower((string) $j['status']) === 'success';
        if ($ok || (isset($j['message']) && stripos((string) $j['message'], 'already') !== false)) {
            $order->update_meta_data('_vj_track_reg', gmdate('c')); $order->update_meta_data('_vj_track_num', $num); $order->save();
            vj_track_mirror($order, 'registered', '', 'Tracking number registered — waiting for the first scan', '', '');
            return array('ok' => true, 'number' => $num, 'provider' => 'shipway', 'raw' => $j);
        }
        $msg = isset($j['message']) ? $j['message'] : (isset($j['error']) ? $j['error'] : 'unknown');
        $order->update_meta_data('_vj_track_err', $msg); $order->save();
        return array('error' => $msg, 'number' => $num, 'raw' => $j);
    }
    $key = get_option('vj_17track_key');
    $body = array(array('number' => $num, 'tag' => (string) $order->get_id()));
    $res = wp_remote_post(VJ_TRACK_API . 'register', array('timeout' => 15,
        'headers' => array('17token' => $key, 'Content-Type' => 'application/json'),
        'body' => wp_json_encode($body)));
    if (is_wp_error($res)) return array('error' => $res->get_error_message());
    $j = json_decode(wp_remote_retrieve_body($res), true);
    $accepted = isset($j['data']['accepted']) ? $j['data']['accepted'] : array();
    $rejected = isset($j['data']['rejected']) ? $j['data']['rejected'] : array();
    $already = false;
    foreach ($rejected as $r) {                       // "already registered" counts as success
        if (isset($r['error']['code']) && (int) $r['error']['code'] === -18019901) $already = true;
    }
    if ($accepted || $already) {
        $order->update_meta_data('_vj_track_reg', gmdate('c'));
        $order->update_meta_data('_vj_track_num', $num);
        $order->save();
        vj_track_mirror($order, 'registered', '', 'Tracking number registered — waiting for the first scan', '', '');
        return array('ok' => true, 'number' => $num, 'already' => $already);
    }
    $msg = isset($rejected[0]['error']['message']) ? $rejected[0]['error']['message'] : (isset($j['code']) ? 'code ' . $j['code'] : 'unknown');
    $order->update_meta_data('_vj_track_err', $msg); $order->save();
    return array('error' => $msg, 'number' => $num, 'raw' => $j);
}

// Register as soon as a tracking number lands (the dashboard's Shipped, the app, WP admin).
add_action('woocommerce_order_status_completed', function ($oid) {
    $o = wc_get_order($oid); if ($o && vj_track_number($o)) vj_track_register($o);
}, 40, 1);
add_action('vj_supa_tick', function () {
    // Hourly sweep: completed orders of the last 30 days with a tracking number but not registered yet.
    $last = (int) get_option('vj_track_sweep_at', 0);
    if (time() - $last < 3600 || !vj_track_provider()) return;
    update_option('vj_track_sweep_at', time(), false);
    $orders = wc_get_orders(array('status' => array('completed'), 'limit' => 60, 'orderby' => 'date', 'order' => 'DESC',
        'date_created' => '>' . (time() - 30 * DAY_IN_SECONDS)));
    foreach ($orders as $o) {
        if ($o->get_meta('_vj_track_reg') || !vj_track_number($o)) continue;
        vj_track_register($o);
    }
});

// Shipway has no webhook: every 20 minutes, ask about each parcel still on the
// way (shipped in the last 25 days, not delivered / not back), 25 per minute.
add_action('vj_supa_tick', function () {
    if (vj_track_provider() !== 'shipway') return;
    $queue = get_option('vj_track_queue', array()); if (!is_array($queue)) $queue = array();
    if (!$queue) {
        if (time() - (int) get_option('vj_track_poll_at', 0) < 20 * 60) return;
        update_option('vj_track_poll_at', time(), false);
        $orders = wc_get_orders(array('status' => array('completed'), 'limit' => 300, 'orderby' => 'date', 'order' => 'DESC',
            'date_created' => '>' . (time() - 25 * DAY_IN_SECONDS), 'return' => 'ids'));
        foreach ($orders as $oid) {
            $o = wc_get_order($oid); if (!$o || !$o->get_meta('_vj_track_reg')) continue;
            if (in_array($o->get_meta('_vj_track_stage'), array('delivered', 'rto'), true)) continue;
            $queue[] = $oid;
        }
        if (!$queue) return;
    }
    $batch = array_splice($queue, 0, 25);
    update_option('vj_track_queue', $queue, false);
    foreach ($batch as $oid) {
        $o = wc_get_order($oid); if (!$o) continue;
        $j = vj_shipway_call('getOrderShipmentDetails', array('order_id' => (string) $oid));
        $code = isset($j['current_status_code']) ? strtoupper((string) $j['current_status_code']) : '';
        if ($code === '') continue;
        $scan = (isset($j['scan']) && is_array($j['scan']) && $j['scan']) ? end($j['scan']) : array();
        $desc = isset($scan['status_detail']) ? $scan['status_detail'] : (isset($j['current_status']) ? $j['current_status'] : '');
        $where = isset($scan['location']) ? $scan['location'] : '';
        $when = isset($scan['time']) ? $scan['time'] : '';
        vj_track_apply($o, vj_track_stage_shipway($code), $desc, $where, $when, $code);
    }
});
function vj_track_stage_shipway($code) {
    $map = array('DEL' => 'delivered', 'OOD' => 'out', 'RTD' => 'rto', 'RTO' => 'returning',
        'UND' => 'failed', '23' => 'failed', '24' => 'failed', '25' => 'failed', 'CNA' => 'failed', 'CRTA' => 'failed',
        '22' => 'exception', 'NFI' => 'exception', 'ODA' => 'exception', 'DEX' => 'exception', 'ONH' => 'exception', 'LOST' => 'exception', 'NWI' => 'exception', 'CAN' => 'exception',
        'INT' => 'transit', 'PKP' => 'transit', 'SCH' => 'transit', 'DNB' => 'transit', 'SMD' => 'transit', 'DRE' => 'transit', 'OTH' => 'transit', 'PNR' => 'transit');
    return isset($map[$code]) ? $map[$code] : 'transit';
}

// Mirror the latest state to Supabase (dashboard + customer's order page).
function vj_track_mirror($order, $stage, $status, $desc, $where, $when) {
    $num = vj_track_number($order);
    vj_supa_req('POST', 'vj_track?on_conflict=order_id', array(array(
        'order_id' => (string) $order->get_id(), 'number' => $num,
        'name' => trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name()),
        'phone' => (string) $order->get_billing_phone(), 'email' => (string) $order->get_billing_email(),
        'stage' => $stage, 'status' => $status, 'description' => mb_substr((string) $desc, 0, 300),
        'location' => mb_substr((string) $where, 0, 120), 'event_at' => $when ?: null,
        'shipped_at' => $order->get_date_completed() ? $order->get_date_completed()->date('c') : null,
        'updated_at' => gmdate('c'),
    )));
    $label = vj_track_label($stage);
    if ($where && in_array($stage, array('transit', 'out', 'returning'), true)) $label .= ' · at ' . $where;   // v6.0 "currently at …"
    $ref = $order->get_meta('_vj_ref');
    $patch = array('track_status' => $label, 'track_at' => gmdate('c'));
    if ($ref) vj_supa_req('PATCH', 'shop_orders?ref=eq.' . rawurlencode($ref), $patch);
    else vj_supa_req('PATCH', 'shop_orders?woo_order_id=eq.' . rawurlencode((string) $order->get_id()), $patch);
}

function vj_track_label($stage) {
    $m = array('registered' => 'Shipped', 'transit' => 'In transit', 'out' => 'Out for delivery', 'failed' => 'Delivery attempted — re-attempt soon',
               'delivered' => 'Delivered', 'returning' => 'Returning to sender', 'rto' => 'Returned to us — reship available', 'expired' => 'No recent update', 'exception' => 'Delivery issue');
    return isset($m[$stage]) ? $m[$stage] : '';
}

// 17TRACK status → our stage.
function vj_track_stage($status, $sub) {
    $status = (string) $status; $sub = (string) $sub;
    if ($status === 'Delivered') return 'delivered';
    if ($status === 'OutForDelivery') return 'out';
    if ($status === 'DeliveryFailure') return 'failed';
    if ($status === 'Exception') {
        if (stripos($sub, 'Returned') !== false) return 'rto';
        if (stripos($sub, 'Returning') !== false) return 'returning';
        if (stripos($sub, 'Rejected') !== false || stripos($sub, 'NoBody') !== false) return 'failed';
        return 'exception';
    }
    if ($status === 'Expired') return 'expired';
    if (in_array($status, array('InTransit', 'InfoReceived', 'AvailableForPickup'), true)) return 'transit';
    return '';
}

// Apply a stage to an order: customer email (once per stage), your Telegram, mirror.
function vj_track_apply($order, $stage, $desc = '', $where = '', $when = '', $status = '') {
    if (!$stage) return;
    $num = vj_track_number($order);
    $first = $order->get_billing_first_name();
    $numtxt = $order->get_order_number();
    $sent = $order->get_meta('_vj_track_mailed'); if (!is_array($sent)) $sent = array();
    if ($num && $order->get_meta('_vj_track_num') !== $num) { $sent = array(); $order->update_meta_data('_vj_track_num', $num); }   // v6.1: reshipped with a new number → every email is news again
    $mail_stage = in_array($stage, array('out', 'failed', 'delivered', 'returning', 'rto'), true) ? $stage : '';
    // v6.1: "out" and "failed" are news every day they happen (one email per day); the rest once.
    $ctx = isset($GLOBALS['vj_track_ctx']) && is_array($GLOBALS['vj_track_ctx']) ? $GLOBALS['vj_track_ctx'] : array();
    $key = in_array($mail_stage, array('out', 'failed'), true) ? $mail_stage . '|' . (!empty($ctx['day']) ? $ctx['day'] : gmdate('Y-m-d')) : $mail_stage;
    // v6.3: an order the tracker sees for the first time days after shipping gets only the
    // email that still matters now (delivered / returning / back with us) — never a late "out for delivery".
    $done = $order->get_date_completed(); $age = $done ? time() - $done->getTimestamp() : 0;
    if (!$order->get_meta('_vj_track_stage') && $age > 3 * DAY_IN_SECONDS && in_array($mail_stage, array('out', 'failed'), true)) $mail_stage = '';
    if ($mail_stage && !in_array($key, $sent, true) && $order->get_billing_email()) {
        $ctx['desc'] = $desc; $ctx['where'] = $where; $ctx['when'] = $when;
        if (vj_mail_track($order, $mail_stage, $ctx)) { $sent[] = $key; $order->update_meta_data('_vj_track_mailed', $sent); }
    }
    $last = $order->get_meta('_vj_track_stage');
    $stage_changed = ($last !== $stage);
    if ($stage_changed) {
        $order->update_meta_data('_vj_track_stage', $stage);
        $order->add_order_note('Tracking: ' . vj_track_label($stage) . ($desc ? ' — ' . $desc : ''));
    }
    // Telegram: delivered and RTO once each, PLUS every new scan while a parcel is on its
    // way back (so an RTO can be followed place-by-place, not just "RTO started"). A return
    // movement is one unique scan — keyed by its time/place so the same move never pings twice.
    if (in_array($stage, array('delivered', 'returning', 'rto'), true)) {
        $scan_key = trim(($when ? $when : '') . '|' . ($where ? $where : '') . '|' . ($desc ? $desc : ''));
        $return_move = ($stage === 'returning' && !$stage_changed && $scan_key !== '' && $scan_key !== (string) $order->get_meta('_vj_track_tg_scan'));
        if ($stage_changed || $return_move) {
            $name = trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name());
            if ($return_move) {
                vj_telegram_send("↩️ On its way back to us\n#" . $numtxt . ' · ' . $name
                    . "\n" . $num . ($where ? "\n📍 Now at " . $where : '') . ($desc ? "\n" . $desc : ''));
            } else {
                $icons = array('delivered' => '✅ Delivered', 'returning' => '↩️ RTO started (returning to sender)', 'rto' => '📦 RTO received — parcel is back');
                vj_telegram_send($icons[$stage] . "\n#" . $numtxt . ' · ' . $name
                    . ' · ' . $order->get_billing_phone() . "\n" . $num . ($desc ? "\n" . $desc : '') . ($where ? ' · ' . $where : '')
                    . ($stage === 'rto' ? "\nCustomer emailed: reship ₹" . VJ_RESHIP_FEE . ' — they confirm payment in their ticket chat (dashboard → More → Chats)' : ''));
            }
            $order->update_meta_data('_vj_track_tg_scan', $scan_key);
        }
    }
    $order->save();
    vj_track_mirror($order, $stage, $status, $desc, $where, $when);
}

function vj_track_email($order, $stage) { return vj_mail_track($order, $stage, array()); }   // v6.1: see vj_mail_track

// 17TRACK → here, on every status change.
add_action('rest_api_init', function () {
    register_rest_route('vj/v1', '/track-hook', array(
        'methods' => array('POST', 'GET'),
        'permission_callback' => '__return_true',
        'callback' => function ($req) {
            $secret = get_option('vj_track_secret', '');
            if (!$secret || !hash_equals($secret, (string) $req->get_param('k'))) return new WP_REST_Response(array('ok' => false), 403);
            if ($req->get_method() === 'GET') return new WP_REST_Response(array('ok' => true, 'msg' => 'webhook reachable'), 200);
            $p = $req->get_json_params();
            $d = isset($p['data']) ? $p['data'] : array();
            $num = isset($d['number']) ? strtoupper((string) $d['number']) : '';
            $tag = isset($d['tag']) ? absint($d['tag']) : 0;
            $info = isset($d['track_info']) ? $d['track_info'] : array();
            $status = isset($info['latest_status']['status']) ? $info['latest_status']['status'] : '';
            $sub = isset($info['latest_status']['sub_status']) ? $info['latest_status']['sub_status'] : '';
            $ev = isset($info['latest_event']) ? $info['latest_event'] : array();
            $order = $tag ? wc_get_order($tag) : null;
            if (!$order && $num) {
                $found = wc_get_orders(array('limit' => 1, 'meta_key' => '_vj_track_num', 'meta_value' => $num));
                $order = $found ? $found[0] : null;
            }
            if (!$order) return new WP_REST_Response(array('ok' => true, 'ignored' => 'no order for ' . $num), 200);
            $stage = vj_track_stage($status, $sub);
            vj_track_apply($order, $stage, isset($ev['description']) ? $ev['description'] : '', isset($ev['location']) ? $ev['location'] : '',
                isset($ev['time_iso']) ? $ev['time_iso'] : '', $status . ($sub ? ' / ' . $sub : ''));
            return new WP_REST_Response(array('ok' => true, 'order' => $order->get_id(), 'stage' => $stage), 200);
        },
    ));
});

// =====================================================================
//  v6.1 — CUSTOMER EMAILS, ONE THEME + SENDING THROUGH GMAIL (13 Oct 2026)
//
//  Every email the customer gets now looks the same: white card, the
//  Visions Jersey wordmark, a progress bar (Ordered → Packed → Shipped →
//  Out for delivery → Delivered), the order box, one clear button.
//
//    Payment received      replaces WooCommerce's "processing" email
//    Shipped               replaces WooCommerce's "completed" email (tracking number)
//    Out for delivery      from the India Post tracker (GitHub), each day it goes out
//    Delivery attempted    every failed attempt (1st, 2nd, 3rd)
//    Delivered             + "share a photo" → shop.visionsjersey.com/review
//    Coming back to us     RTO started (once)
//    Back with us          RTO received: what happened, ₹80 reship QR,
//                          "Send screenshot on WhatsApp" button
//
//  Sending: WordPress normally mails from the hosting server, which has no
//  identity Gmail trusts → spam. With Gmail accounts added here, every email
//  goes out through Gmail's own servers (SPF/DKIM signed by Google), rotating
//  across the accounts you add:
//      /wp-admin/?vj_smtp_add=1&email=you@gmail.com&pass=APP_PASSWORD
//      /wp-admin/?vj_smtp_list=1          /wp-admin/?vj_smtp_remove=1&email=…
//      /wp-admin/?vj_mail_test=1&to=you@gmail.com&kind=processing   (kind = processing · shipped ·
//                                        out · failed · delivered · returning · rto · unpaid · all)
// =====================================================================

if (!defined('VJ_MAIL_SHOP')) define('VJ_MAIL_SHOP', 'https://shop.visionsjersey.com');
if (!defined('VJ_WA_SUPPORT')) define('VJ_WA_SUPPORT', '');     // v6.9: no business WhatsApp number anywhere
if (!defined('VJ_MAIL_LOGO')) define('VJ_MAIL_LOGO', 'https://shop.visionsjersey.com/logo.png');

// =====================================================================
//  v6.8 — SENDING THROUGH RESEND (19 Oct 2026)
//
//  When a Resend API key is saved, EVERY email WordPress sends (ours, WooCommerce's,
//  password resets) goes to Resend over HTTPS instead of SMTP. No ports to open on the
//  host, DKIM/SPF signed by the verified domain, and the outbox / daily limit / priority
//  logic above keeps working unchanged because this sits underneath wp_mail().
//
//      /wp-admin/?vj_resend_set=1&key=re_xxx&from=hello@visionsjersey.com   turn on (quota → 95/day for the free plan; add &quota=2900 on a paid plan)
//      /wp-admin/?vj_resend_status=1                                       what's set + last error
//      /wp-admin/?vj_resend_off=1                                          back to SMTP / hosting server
//      /wp-admin/?vj_mail_test=1&to=you@gmail.com&kind=all                 the usual samples now go out through Resend
// =====================================================================
function vj_resend_key()  { return (string) get_option('vj_resend_key', ''); }
function vj_resend_from() { return (string) get_option('vj_resend_from', get_option('vj_from_email', '')); }

add_filter('pre_wp_mail', function ($null, $atts) {
    $key = vj_resend_key(); if (!$key) return $null;
    $from_email = vj_resend_from(); if (!$from_email) return $null;
    $to = isset($atts['to']) ? $atts['to'] : array();
    if (!is_array($to)) $to = preg_split('/[,;]+/', (string) $to);
    $to = array_values(array_filter(array_map('trim', $to)));
    if (!count($to)) return $null;
    $subject = (string) (isset($atts['subject']) ? $atts['subject'] : '');
    $message = (string) (isset($atts['message']) ? $atts['message'] : '');
    $headers = isset($atts['headers']) ? $atts['headers'] : array();
    if (!is_array($headers)) $headers = explode("\n", str_replace("\r\n", "\n", (string) $headers));
    $from_name = 'Visions Jersey'; $reply = ''; $is_html = false; $extra = array();
    foreach ($headers as $h) {
        if (strpos($h, ':') === false) continue;
        list($k, $v) = array_map('trim', explode(':', $h, 2)); $lk = strtolower($k);
        if ($lk === 'content-type') { if (stripos($v, 'text/html') !== false) $is_html = true; }
        elseif ($lk === 'from') { if (preg_match('/^(.*?)\s*<[^>]+>$/', $v, $m) && trim($m[1], " \"'")) $from_name = trim($m[1], " \"'"); }   // address stays the verified one
        elseif ($lk === 'reply-to') { $reply = preg_match('/<([^>]+)>/', $v, $m) ? $m[1] : $v; }
        elseif ($lk === 'x-entity-ref-id') { $extra['X-Entity-Ref-ID'] = $v; }
    }
    if (!$is_html && preg_match('/<(html|table|div|p|a)\b/i', $message)) $is_html = true;
    $body = array('from' => $from_name . ' <' . $from_email . '>', 'to' => $to, 'subject' => $subject);
    $body[$is_html ? 'html' : 'text'] = $message;
    $body['reply_to'] = $reply ?: $from_email;
    if ($extra) $body['headers'] = $extra;
    $res = wp_remote_post('https://api.resend.com/emails', array('timeout' => 20,
        'headers' => array('Authorization' => 'Bearer ' . $key, 'Content-Type' => 'application/json'),
        'body' => wp_json_encode($body)));
    $code = is_wp_error($res) ? 0 : (int) wp_remote_retrieve_response_code($res);
    if ($code >= 200 && $code < 300) {
        do_action('wp_mail_succeeded', array('to' => $to, 'subject' => $subject, 'message' => $message, 'headers' => $headers, 'attachments' => array()));
        return true;
    }
    $msg = is_wp_error($res) ? $res->get_error_message() : ('HTTP ' . $code . ' ' . substr((string) wp_remote_retrieve_body($res), 0, 300));
    update_option('vj_resend_last_err', gmdate('c') . ' ' . $msg, false);
    do_action('wp_mail_failed', new WP_Error('resend', $msg, array('to' => $to, 'subject' => $subject)));
    return false;
}, 10, 2);

add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_resend_set'])) {
        $key = isset($_GET['key']) ? trim(sanitize_text_field(wp_unslash($_GET['key']))) : '';
        $from = isset($_GET['from']) ? sanitize_email(wp_unslash($_GET['from'])) : '';
        if (!preg_match('/^re_[A-Za-z0-9_]{10,}$/', $key) || !$from) wp_die('Usage: /wp-admin/?vj_resend_set=1&key=re_xxxxxxxx&from=hello@visionsjersey.com', 'VJ Email');
        update_option('vj_resend_key', $key, false);
        update_option('vj_resend_from', $from, false);
        update_option('vj_from_email', $from, false);                      // From / Reply-To on every customer email
        $q = isset($_GET['quota']) ? absint($_GET['quota']) : 95;          // Resend free = 100/day; stay under it
        update_option('vj_mail_quota', max(10, $q), false);
        $mq = isset($_GET['month']) ? absint($_GET['month']) : 2900;       // Resend free = 3,000/month; stay under it
        update_option('vj_mail_quota_month', $mq, false);
        update_option('vj_mail_hold', 0, false);                           // release anything waiting
        delete_option('vj_resend_last_err');
        wp_die('Resend ON ✓ sending as Visions Jersey &lt;' . esc_html($from) . '&gt; · limits ' . max(10, $q) . '/day, ' . $mq . '/month'
            . '<br>Test: /wp-admin/?vj_mail_test=1&amp;to=YOUR@EMAIL&amp;kind=all', 'VJ Email');
    }
    if (isset($_GET['vj_resend_off'])) { delete_option('vj_resend_key'); wp_die('Resend OFF — back to SMTP accounts / hosting server.', 'VJ Email'); }
    if (isset($_GET['vj_resend_status'])) {
        $k = vj_resend_key();
        wp_die(($k ? 'Resend ON · key ' . esc_html(substr($k, 0, 6)) . '…' . esc_html(substr($k, -4)) . ' · from ' . esc_html(vj_resend_from()) : 'Resend OFF')
            . '<br>Today: ' . (int) vj_mail_today()['n'] . ' / ' . (int) get_option('vj_mail_quota', 290)
            . ' · This month: ' . (int) vj_mail_month()['n'] . ' / ' . ((int) get_option('vj_mail_quota_month', 0) ?: 'no cap')
            . '<br>Waiting in outbox: ' . (int) $GLOBALS['wpdb']->get_var("SELECT COUNT(*) FROM " . vj_outbox_table() . " WHERE sent_at IS NULL AND expires > '" . gmdate('Y-m-d H:i:s') . "'")
            . '<br>Last error: ' . esc_html((string) get_option('vj_resend_last_err', 'none')), 'VJ Email');
    }
});

// ---------- sending through Gmail (rotating) ----------
add_action('phpmailer_init', function ($m) {
    if (vj_resend_key()) return;   // v6.8: Resend handles sending
    $accts = get_option('vj_smtp', array());
    if (!is_array($accts) || !count($accts)) return;
    $i = (int) get_option('vj_smtp_i', 0);
    $a = $accts[$i % count($accts)];
    update_option('vj_smtp_i', $i + 1, false);
    try {
        $m->isSMTP();
        $m->Host = !empty($a['host']) ? $a['host'] : 'smtp.gmail.com';
        $m->Port = !empty($a['port']) ? (int) $a['port'] : 587;
        $m->SMTPSecure = $m->Port === 465 ? 'ssl' : 'tls';
        $m->SMTPAuth = true;
        $m->Username = $a['email'];
        $m->Password = $a['pass'];
        $m->Timeout = 20;
        // Gmail sends as the signed-in account; a service like Brevo sends as the verified sender you give with &from=
        $m->setFrom(!empty($a['from']) ? $a['from'] : $a['email'], 'Visions Jersey', false);
        $reply = get_option('vj_from_email', '');
        if ($reply && !count($m->getReplyToAddresses())) $m->addReplyTo($reply, 'Visions Jersey');
    } catch (Exception $e) {}
});
add_filter('wp_mail_from_name', function ($n) { return 'Visions Jersey'; });

add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_smtp_add'])) {
        $email = isset($_GET['email']) ? sanitize_email(wp_unslash($_GET['email'])) : '';
        $pass = isset($_GET['pass']) ? str_replace(' ', '', sanitize_text_field(wp_unslash($_GET['pass']))) : '';
        if (!$email || !$pass) wp_die('Usage: /wp-admin/?vj_smtp_add=1&email=you@gmail.com&pass=APP_PASSWORD (the 16-letter app password, spaces don\'t matter)', 'VJ Email');
        $accts = get_option('vj_smtp', array()); if (!is_array($accts)) $accts = array();
        $accts = array_values(array_filter($accts, function ($x) use ($email) { return strcasecmp($x['email'], $email) !== 0; }));
        $accts[] = array('email' => $email, 'pass' => $pass,
            'host' => isset($_GET['host']) ? sanitize_text_field(wp_unslash($_GET['host'])) : '', 'port' => isset($_GET['port']) ? absint($_GET['port']) : 0,
            'from' => isset($_GET['from']) ? sanitize_email(wp_unslash($_GET['from'])) : '');   // v6.1: Brevo/Zoho: the verified sender address
        update_option('vj_smtp', $accts, false);
        wp_die('Added ✓ ' . esc_html($email) . ' — ' . count($accts) . ' sending account(s). Test: /wp-admin/?vj_mail_test=1&to=' . esc_html($email), 'VJ Email');
    }
    if (isset($_GET['vj_smtp_remove'])) {
        $email = isset($_GET['email']) ? sanitize_email(wp_unslash($_GET['email'])) : '';
        $accts = get_option('vj_smtp', array()); if (!is_array($accts)) $accts = array();
        $accts = array_values(array_filter($accts, function ($x) use ($email) { return strcasecmp($x['email'], $email) !== 0; }));
        update_option('vj_smtp', $accts, false);
        wp_die('Removed. ' . count($accts) . ' sending account(s) left.', 'VJ Email');
    }
    if (isset($_GET['vj_smtp_list'])) {
        $accts = get_option('vj_smtp', array()); if (!is_array($accts)) $accts = array();
        $out = count($accts) ? '' : 'No Gmail accounts yet — emails go out through the hosting server (spam-prone).';
        foreach ($accts as $x) $out .= '• ' . esc_html($x['email']) . (!empty($x['host']) ? ' via ' . esc_html($x['host']) : ' via Gmail') . (!empty($x['from']) ? ' · sends as ' . esc_html($x['from']) : '') . "<br>";
        wp_die('Sending accounts (' . count($accts) . ', rotating in this order):<br>' . $out . '<br>Sent so far: ' . (int) get_option('vj_smtp_i', 0), 'VJ Email');
    }
    if (isset($_GET['vj_mail_test'])) {
        $to = isset($_GET['to']) ? sanitize_email(wp_unslash($_GET['to'])) : '';
        $kind = isset($_GET['kind']) ? sanitize_key($_GET['kind']) : 'processing';
        if (!$to) wp_die('Usage: /wp-admin/?vj_mail_test=1&to=you@gmail.com&kind=processing|shipped|reship|out|failed|delivered|returning|rto|all', 'VJ Email');
        $oid = isset($_GET['order']) ? absint($_GET['order']) : 0;
        $order = $oid ? wc_get_order($oid) : null;
        if (!$order) { $found = wc_get_orders(array('limit' => 1, 'status' => array('processing', 'completed'), 'orderby' => 'date', 'order' => 'DESC')); $order = $found ? $found[0] : null; }
        if (!$order) wp_die('No order to use as a sample. Add &order=ORDER_ID', 'VJ Email');
        $kinds = $kind === 'all' ? array('unpaid', 'processing', 'shipped', 'out', 'failed', 'delivered', 'returning', 'rto', 'reship') : array($kind);
        $GLOBALS['vj_mail_direct'] = true;   // samples skip the outbox
        $res = array();
        foreach ($kinds as $k) {
            $ctx = array('attempts' => $k === 'failed' ? 2 : 3, 'desc' => 'Unsuccessful delivery — addressee not available', 'where' => 'Andheri East S.O.', 'test' => true);
            if ($k === 'unpaid') { $saved = $order->get_billing_email(); $order->set_billing_email($to); $ok = vj_send_unpaid_email($order, 1); $order->set_billing_email($saved); }
            elseif ($k === 'processing') $ok = vj_mail_processing($order, $to);
            elseif ($k === 'shipped') $ok = vj_mail_shipped($order, $to);
            elseif ($k === 'reship') $ok = vj_mail_shipped($order, $to, true);
            else $ok = vj_mail_track($order, $k, $ctx, $to);
            $res[] = $k . ': ' . ($ok ? 'sent ✓' : 'FAILED');
        }
        wp_die('Sample of order #' . esc_html($order->get_order_number()) . ' → ' . esc_html($to) . '<br>' . implode('<br>', $res) . '<br><br>Sending accounts: ' . count((array) get_option('vj_smtp', array())) . ' (0 = hosting server). Check the inbox AND the spam folder.', 'VJ Email');
    }
});

// WooCommerce's own "processing" and "completed" emails are replaced by ours.
add_filter('woocommerce_email_enabled_customer_processing_order', '__return_false');
add_filter('woocommerce_email_enabled_customer_completed_order', '__return_false');
// v6.5: WooCommerce's admin "… placed a new order #…" emails (to YOU, one per status hop → often two
// per order) are off too; the dashboard and Telegram already tell you. Back on: /wp-admin/?vj_admin_mail=1
add_filter('woocommerce_email_enabled_new_order', function ($on) { return get_option('vj_admin_mail', '0') === '1' ? $on : false; });
add_action('woocommerce_order_status_processing', function ($oid) {
    $o = wc_get_order($oid); if (!$o || $o->get_meta('_vj_mail_processing')) return;
    if (vj_mail_processing($o)) { $o->update_meta_data('_vj_mail_processing', gmdate('c')); $o->save(); }
}, 25, 1);
function vj_mail_shipped_maybe($oid) {
    $o = is_object($oid) ? $oid : wc_get_order($oid);
    if (!$o || $o->get_status() !== 'completed' || $o->get_meta('_vj_mail_shipped') || !vj_track_number($o)) return;
    // v6.3: "Shipped" is news only right after shipping. An order completed days ago
    // (old parcels the tracker starts following, re-saves, manual edits) gets nothing —
    // the tracker's own stage email (delivered / RTO…) is the one that matters there.
    $done = $o->get_date_completed(); $age = $done ? time() - $done->getTimestamp() : PHP_INT_MAX;
    if ($age > 3 * DAY_IN_SECONDS || $o->get_meta('_vj_track_stage')) { $o->update_meta_data('_vj_mail_shipped', 'skipped-old'); $o->save(); return; }
    if (vj_mail_shipped($o)) { $o->update_meta_data('_vj_mail_shipped', gmdate('c')); $o->save(); }
}
add_action('woocommerce_order_status_completed', 'vj_mail_shipped_maybe', 25, 1);
add_action('woocommerce_update_order', 'vj_mail_shipped_maybe', 25, 1);   // tracking number added after completion

// ---------- building blocks ----------
function vj_mail_e($s) { return esc_html((string) $s); }
function vj_mail_order_url($order) { $ref = $order->get_meta('_vj_ref'); return VJ_MAIL_SHOP . ($ref ? '/order/' . rawurlencode($ref) : '/account'); }
function vj_mail_wa($order, $text) { return vj_mail_ticket_url($order, 'delivery'); }   // v6.9: no WhatsApp number — opens a ticket (chat) instead
function vj_mail_ticket_url($order, $cat = 'other') { $ref = $order->get_meta('_vj_ref') ?: (string) $order->get_order_number(); return VJ_MAIL_SHOP . '/ticket?o=' . rawurlencode($ref) . '&c=' . rawurlencode($cat); }

function vj_mail_btn($url, $label, $dark = true) {
    $bg = $dark ? '#202844' : '#ffffff'; $fg = $dark ? '#F0E8D5' : '#202844'; $bd = '#202844';   // v6.8: the storefront's navy + cream
    return '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:0 auto"><tr><td style="border-radius:999px;background:' . $bg . ';border:2px solid ' . $bd . '">'
        . '<a href="' . esc_url($url) . '" style="display:inline-block;padding:14px 28px;font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;font-weight:800;color:' . $fg . ';text-decoration:none;letter-spacing:.2px">' . $label . '</a></td></tr></table>';
}

// Ordered → Packed → Shipped → Out for delivery → Delivered  (5 steps; $on = how many are done; $bad = true paints the last done step red)
function vj_mail_steps($on, $bad = false, $labels = null) {
    $labels = $labels ?: array('Ordered', 'Packed', 'Shipped', 'Out for delivery', 'Delivered');
    $h = '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin:18px 0 6px"><tr>';
    $n = count($labels);
    foreach ($labels as $i => $l) {
        $done = $i < $on; $cur = $i === $on - 1;
        $color = $done ? ($cur && $bad ? '#C62828' : '#1B8A4C') : '#D9DADF';
        $h .= '<td align="center" valign="top" width="' . (100 / $n) . '%" style="padding:0 2px">'
            . '<div style="height:6px;border-radius:6px;background:' . $color . '"></div>'
            . '<div style="font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:10.5px;font-weight:' . ($cur ? '800' : '600') . ';color:' . ($done ? '#14161F' : '#9A9CA6') . ';margin-top:6px;line-height:1.2">' . $l . '</div></td>';
    }
    return $h . '</tr></table>';
}

function vj_mail_items($order) {
    $h = '';
    foreach ($order->get_items() as $it) {
        $size = '';
        foreach ($it->get_meta_data() as $m) { $d = $m->get_data(); if (isset($d['key']) && preg_match('/size/i', $d['key'])) { $size = $d['value']; break; } }
        $img = '';
        $p = $it->get_product();
        if ($p) { $iid = $p->get_image_id() ?: ($p->get_parent_id() ? wc_get_product($p->get_parent_id())->get_image_id() : 0); if ($iid) $img = wp_get_attachment_image_url($iid, 'woocommerce_thumbnail'); }
        $h .= '<tr><td width="64" valign="top" style="padding:8px 10px 8px 0">' . ($img ? '<img src="' . esc_url($img) . '" width="64" height="64" alt="" style="display:block;width:64px;height:64px;object-fit:cover;border-radius:10px;background:#f2f2f4">' : '<div style="width:64px;height:64px;border-radius:10px;background:#f2f2f4"></div>') . '</td>'
            . '<td valign="middle" style="padding:8px 0;font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;color:#14161F;line-height:1.35"><b>' . vj_mail_e($it->get_name()) . '</b><br><span style="color:#6B6E7A;font-size:12.5px">' . ($size ? 'Size ' . vj_mail_e($size) . ' · ' : '') . 'Qty ' . (int) $it->get_quantity() . '</span></td>'
            . '<td align="right" valign="middle" style="padding:8px 0 8px 10px;font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;font-weight:700;color:#14161F;white-space:nowrap">₹' . number_format((float) $it->get_total(), 0) . '</td></tr>';
    }
    return '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">' . $h . '</table>';
}

function vj_mail_box($order, $extraRows = array(), $withItems = true) {
    $rows = '';
    foreach ($extraRows as $k => $v) {
        $rows .= '<tr><td style="padding:6px 0;font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:12.5px;color:#6B6E7A">' . $k . '</td><td align="right" style="padding:6px 0;font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:13.5px;font-weight:700;color:#14161F">' . $v . '</td></tr>';
    }
    return '<div style="border:1px solid #E6E7EB;border-radius:14px;padding:14px 16px;margin:18px 0">'
        . '<div style="font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:11px;font-weight:800;letter-spacing:.8px;color:#9A9CA6">ORDER #' . vj_mail_e($order->get_order_number()) . ' · ' . vj_mail_e($order->get_date_created() ? $order->get_date_created()->date_i18n('j M Y') : '') . '</div>'
        . ($withItems ? vj_mail_items($order) : '')
        . ($rows ? '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="border-top:1px solid #EEEFF2;margin-top:6px;padding-top:6px">' . $rows . '</table>' : '')
        . '</div>';
}

// $tone: good (green) · info (ink) · warn (amber) · bad (red)
function vj_mail_wrap($preheader, $kicker, $title, $tone, $inner) {
    $tones = array('good' => '#1B8A4C', 'info' => '#202844', 'warn' => '#B7791F', 'bad' => '#C62828');   // v6.8: info = storefront navy
    $c = isset($tones[$tone]) ? $tones[$tone] : '#202844';
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>' . vj_mail_e($title) . '</title></head>'
        . '<body style="margin:0;padding:0;background:#F2F3F6">'
        . '<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#F2F3F6">' . vj_mail_e($preheader) . str_repeat('&nbsp;&zwnj;', 40) . '</div>'
        . '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background:#F2F3F6"><tr><td align="center" style="padding:22px 12px">'
        . '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:560px">'
        . '<tr><td align="center" style="padding:6px 0 14px"><a href="' . VJ_MAIL_SHOP . '" style="text-decoration:none"><img src="' . VJ_MAIL_LOGO . '" width="180" alt="Visions Jersey" style="display:block;width:180px;height:auto;border:0"></a></td></tr>'
        . '<tr><td style="background:#ffffff;border-radius:18px;border-top:6px solid ' . $c . ';padding:26px 24px 22px;font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#14161F">'
        . '<div style="font-size:11.5px;font-weight:800;letter-spacing:1px;color:' . $c . ';text-transform:uppercase">' . $kicker . '</div>'
        . '<h1 style="font-size:24px;line-height:1.2;margin:8px 0 12px;font-weight:900;color:#14161F">' . $title . '</h1>'
        . $inner
        . '</td></tr>'
        . '<tr><td align="center" style="padding:16px 10px 0;font-family:Poppins,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:11.5px;line-height:1.6;color:#9A9CA6">'
        . 'Visions Jersey · Prepaid only · Free shipping all over India<br>'
        . 'Questions? Reply to this email or <a href="' . VJ_MAIL_SHOP . '/contact" style="color:#6B6E7A">chat with us</a> · <a href="' . VJ_MAIL_SHOP . '/account" style="color:#6B6E7A">My orders</a>'
        . '</td></tr></table></td></tr></table></body></html>';
}

function vj_mail_p($t, $muted = false) { return '<p style="font-size:' . ($muted ? '13px' : '15px') . ';line-height:1.6;margin:0 0 12px;color:' . ($muted ? '#6B6E7A' : '#14161F') . '">' . $t . '</p>'; }
function vj_mail_note($t, $tone = 'info') {
    $bg = array('good' => '#EAF7EF', 'info' => '#F4F4F6', 'warn' => '#FFF6E5', 'bad' => '#FDECEC');
    return '<div style="background:' . (isset($bg[$tone]) ? $bg[$tone] : '#F4F4F6') . ';border-radius:12px;padding:12px 14px;margin:14px 0;font-size:13.5px;line-height:1.55;color:#14161F">' . $t . '</div>';
}

function vj_mail_send($order, $to, $subject, $html, $tag) {
    if (!$to) return false;
    $from = get_option('vj_from_email', get_option('admin_email'));
    $headers = array('Content-Type: text/html; charset=UTF-8', 'From: Visions Jersey <' . $from . '>', 'Reply-To: Visions Jersey <' . $from . '>',
        'X-Entity-Ref-ID: vj-' . $order->get_id() . '-' . $tag . '-' . substr(md5($subject . time()), 0, 6));   // no List-Unsubscribe: these are order emails, not a newsletter (keeps them in Primary)
    if (!empty($GLOBALS['vj_mail_direct'])) return wp_mail($to, $subject, $html, $headers);   // the test command sends right now
    return vj_outbox_add($to, $subject, $html, $headers, $tag, $order->get_id());               // v6.2: through the outbox (daily quota + priority)
}

// =====================================================================
//  v6.2 — OUTBOX: daily sending quota + priority
//
//  Brevo's free plan sends 300 emails a day. Every customer email goes into
//  an outbox and is sent at once while there is room; when the day's quota
//  is used up, the rest wait and go out first thing after midnight, the
//  important ones first:
//      100 Payment received      90 Out for delivery / Delivery attempted
//       80 Shipped (tracking id) 70 Back with us (RTO)   60 Returning
//       50 Delivered             30 Payment pending      10 anything else
//  Emails that would be stale by then (payment pending, out for delivery)
//  are dropped instead of sent late. A failed send (Brevo not verified yet,
//  network) is retried every 10 minutes for up to 3 days — nothing is lost.
//      /wp-admin/?vj_mail_queue=1          what's waiting, what went today
//      /wp-admin/?vj_mail_quota=290        change the daily limit (default 290; Brevo free = 300)
// =====================================================================
function vj_outbox_table() { global $wpdb; return $wpdb->prefix . 'vj_outbox'; }
add_action('init', function () {
    if (get_option('vj_outbox_ver') === '1') return;
    global $wpdb; require_once ABSPATH . 'wp-admin/includes/upgrade.php';
    dbDelta("CREATE TABLE " . vj_outbox_table() . " (
        id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
        created datetime NOT NULL,
        priority smallint NOT NULL DEFAULT 10,
        to_email varchar(190) NOT NULL,
        subject text NOT NULL,
        html longtext NOT NULL,
        headers text NOT NULL,
        tag varchar(60) NOT NULL DEFAULT '',
        order_id bigint(20) unsigned NOT NULL DEFAULT 0,
        expires datetime NOT NULL,
        attempts smallint NOT NULL DEFAULT 0,
        next_try datetime NULL,
        sent_at datetime NULL,
        last_error text NULL,
        PRIMARY KEY  (id),
        KEY pending (sent_at, priority, id)
    ) " . $wpdb->get_charset_collate());
    update_option('vj_outbox_ver', '1', false);
});
function vj_mail_priority($tag) {
    $t = preg_replace('/-.*$/', '', (string) $tag);
    $p = array('processing' => 100, 'out' => 90, 'failed' => 90, 'refund' => 88, 'exchange' => 88, 'ticket' => 85, 'shipped' => 80, 'rto' => 70, 'returning' => 60, 'delivered' => 50, 'unpaid' => 30);   // v6.9: ticket = "we replied" · v7.1: refund / exchange
    return isset($p[$t]) ? $p[$t] : 10;
}
function vj_mail_ttl_hours($tag) {
    $t = preg_replace('/-.*$/', '', (string) $tag);
    if ($t === 'unpaid') return 20;          // pointless after a day
    if ($t === 'out') return 16;             // "arrives today" must not arrive tomorrow
    if ($t === 'ticket') return 24;
    return 72;
}
function vj_mail_today() {
    $d = get_option('vj_mail_day'); $today = gmdate('Y-m-d');   // Brevo counts days in UTC
    if (!is_array($d) || empty($d['day']) || $d['day'] !== $today) $d = array('day' => $today, 'n' => 0);
    return $d;
}
function vj_mail_month() {
    $m = get_option('vj_mail_month'); $cur = gmdate('Y-m');   // v6.8: Resend counts months in UTC
    if (!is_array($m) || empty($m['month']) || $m['month'] !== $cur) $m = array('month' => $cur, 'n' => 0);
    return $m;
}
add_action('wp_mail_succeeded', function () {
    $d = vj_mail_today(); $d['n']++; update_option('vj_mail_day', $d, false);
    $m = vj_mail_month(); $m['n']++; update_option('vj_mail_month', $m, false);
});   // every email WordPress sends counts (WooCommerce's too)
add_action('wp_mail_failed', function ($e) { update_option('vj_mail_last_err', gmdate('c') . ' ' . (is_wp_error($e) ? $e->get_error_message() : 'failed'), false); });
function vj_outbox_add($to, $subject, $html, $headers, $tag = '', $order_id = 0) {
    global $wpdb;
    $ok = $wpdb->insert(vj_outbox_table(), array(
        'created' => gmdate('Y-m-d H:i:s'), 'priority' => vj_mail_priority($tag), 'to_email' => $to, 'subject' => $subject, 'html' => $html,
        'headers' => wp_json_encode(is_array($headers) ? $headers : array($headers)), 'tag' => (string) $tag, 'order_id' => (int) $order_id,
        'expires' => gmdate('Y-m-d H:i:s', time() + vj_mail_ttl_hours($tag) * HOUR_IN_SECONDS)));
    if ($ok === false) return wp_mail($to, $subject, $html, $headers);   // table missing? send the old way rather than lose it
    vj_outbox_flush(5);
    return true;
}
function vj_outbox_flush($max = 40) {
    global $wpdb; $t = vj_outbox_table();
    if (get_option('vj_mail_hold')) return 0;    // /wp-admin/?vj_mail_hold=1 : keep everything in the outbox (e.g. until Brevo is verified); =0 releases it
    $quota = (int) get_option('vj_mail_quota', 290);
    $room = $quota - (int) vj_mail_today()['n'];
    $mq = (int) get_option('vj_mail_quota_month', 0);                       // v6.8: 0 = no monthly cap
    if ($mq > 0) $room = min($room, $mq - (int) vj_mail_month()['n']);
    if ($room <= 0) return 0;
    $now = gmdate('Y-m-d H:i:s');
    $rows = $wpdb->get_results($wpdb->prepare("SELECT * FROM $t WHERE sent_at IS NULL AND expires > %s AND (next_try IS NULL OR next_try <= %s) ORDER BY priority DESC, id ASC LIMIT %d", $now, $now, min($max, $room)));
    $sent = 0;
    foreach ((array) $rows as $r) {
        $headers = json_decode($r->headers, true); if (!is_array($headers)) $headers = array('Content-Type: text/html; charset=UTF-8');
        if (wp_mail($r->to_email, $r->subject, $r->html, $headers)) {
            $wpdb->update($t, array('sent_at' => gmdate('Y-m-d H:i:s')), array('id' => $r->id)); $sent++;
        } else {
            $wpdb->update($t, array('attempts' => $r->attempts + 1, 'next_try' => gmdate('Y-m-d H:i:s', time() + 10 * MINUTE_IN_SECONDS),
                'last_error' => (string) get_option('vj_mail_last_err', '')), array('id' => $r->id));
            break;   // sending is broken right now (not verified yet / limit hit) — try again in 10 minutes, don't hammer
        }
    }
    if (mt_rand(1, 50) === 1) $wpdb->query($wpdb->prepare("DELETE FROM $t WHERE (sent_at IS NOT NULL AND sent_at < %s) OR expires < %s", gmdate('Y-m-d H:i:s', time() - 7 * DAY_IN_SECONDS), gmdate('Y-m-d H:i:s', time() - 2 * DAY_IN_SECONDS)));
    return $sent;
}
add_action('vj_supa_tick', function () { vj_outbox_flush(40); });

// =====================================================================
//  v6.9 — TICKET CHAT: "we replied" email (19 Oct 2026)
//  Every minute: shop replies not yet announced → one short email per ticket
//  (at most one every 2 hours per ticket, so five quick replies = one email),
//  through the outbox at priority 85. The customer taps straight into the chat.
// =====================================================================
add_action('vj_supa_tick', function () {
    $raw = vj_supa_req('GET', 'vj_ticket_msgs?select=id,ref,created_at&sender=eq.shop&emailed=eq.false&order=id.asc&limit=40');
    if (!$raw || $raw === true) return;
    $rows = json_decode($raw, true); if (!is_array($rows) || !count($rows)) return;
    $by = array(); foreach ($rows as $r) { $by[$r['ref']][] = (int) $r['id']; }
    foreach ($by as $ref => $ids) {
        $tk = vj_supa_req('GET', 'vj_tickets?select=ref,name,email,category&ref=eq.' . rawurlencode($ref) . '&limit=1');
        $tk = ($tk && $tk !== true) ? json_decode($tk, true) : null; $tk = is_array($tk) && count($tk) ? $tk[0] : null;
        if ($tk && !empty($tk['email']) && is_email($tk['email']) && !get_transient('vj_chat_mail_' . $ref)) {
            $first = trim(explode(' ', (string) $tk['name'])[0]) ?: 'there';
            $url = VJ_MAIL_SHOP . '/ticket/' . rawurlencode($ref);
            $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ', we\'ve replied to your ticket <b>' . vj_mail_e($ref) . '</b>. Open the chat to read it and write back — it\'s the fastest way to reach us.')
                . vj_mail_btn($url, '💬 Open the chat')
                . vj_mail_p('Or copy this link: ' . vj_mail_e($url), true);
            $html = vj_mail_wrap('We replied to your ticket ' . $ref . ' — open the chat.', 'Ticket ' . $ref, 'We\'ve replied, ' . vj_mail_e($first), 'info', $inner);
            $from = get_option('vj_from_email', get_option('admin_email'));
            $headers = array('Content-Type: text/html; charset=UTF-8', 'From: Visions Jersey <' . $from . '>', 'Reply-To: Visions Jersey <' . $from . '>', 'X-Entity-Ref-ID: vj-ticket-' . $ref . '-' . substr(md5(implode(',', $ids)), 0, 6));
            vj_outbox_add($tk['email'], 'We replied to your ticket ' . $ref . ' · Visions Jersey', $html, $headers, 'ticket', 0);
            set_transient('vj_chat_mail_' . $ref, 1, 2 * HOUR_IN_SECONDS);
        }
        vj_supa_req('PATCH', 'vj_ticket_msgs?id=in.(' . implode(',', $ids) . ')', array('emailed' => true));   // announced (or no email on the ticket) — never again
    }
});
add_action('admin_init', function () {
    if (!current_user_can('manage_options')) return;
    if (isset($_GET['vj_mail_hold'])) {
        update_option('vj_mail_hold', $_GET['vj_mail_hold'] === '1' ? 1 : 0, false);
        wp_die(get_option('vj_mail_hold') ? 'Outbox ON HOLD — customer emails wait (important ones first) until you open /wp-admin/?vj_mail_hold=0' : 'Outbox released — sending now, important ones first.', 'VJ Email');
    }
    if (isset($_GET['vj_mail_quota'])) {
        $q = absint($_GET['vj_mail_quota']); if ($q < 10) wp_die('Usage: /wp-admin/?vj_mail_quota=290', 'VJ Email');
        update_option('vj_mail_quota', $q, false); wp_die('Daily limit set to ' . $q . ' emails.', 'VJ Email');
    }
    if (isset($_GET['vj_mail_queue'])) {
        global $wpdb; $t = vj_outbox_table(); $d = vj_mail_today();
        $wait = $wpdb->get_results("SELECT priority, tag, COUNT(*) n, MAX(attempts) a FROM $t WHERE sent_at IS NULL AND expires > UTC_TIMESTAMP() GROUP BY priority, tag ORDER BY priority DESC");
        $out = 'Sent today (UTC day ' . esc_html($d['day']) . '): <b>' . (int) $d['n'] . ' / ' . (int) get_option('vj_mail_quota', 290) . '</b><br>';
        $out .= 'Sent last 7 days: ' . (int) $wpdb->get_var("SELECT COUNT(*) FROM $t WHERE sent_at IS NOT NULL") . '<br><br><b>Waiting:</b><br>';
        if (!$wait) $out .= 'nothing — everything went out.<br>';
        foreach ($wait as $w) $out .= '• ' . (int) $w->n . ' × ' . esc_html($w->tag ?: 'other') . ' (priority ' . (int) $w->priority . ($w->a ? ', ' . (int) $w->a . ' failed attempts' : '') . ')<br>';
        $err = get_option('vj_mail_last_err', '');
        if ($err) $out .= '<br>Last sending error: ' . esc_html($err);
        wp_die($out, 'VJ Email');
    }
});

// ---------- the emails ----------

// =====================================================================
//  v7.1 — REFUND + EXCHANGE (branded, same design as Shipped / Delivered)
// =====================================================================
function vj_mail_refund($order, $amount, $reason) {
    $to = $order->get_billing_email(); if (!$to) return false;
    $first = $order->get_billing_first_name() ?: 'there';
    $amount = $amount > 0 ? $amount : (float) $order->get_total();
    $rows = array('Refund amount' => '₹' . number_format((float) $amount, 0), 'Goes back to' => 'Your bank / UPI', 'Usually arrives in' => '7–8 working days');
    if (trim((string) $reason) !== '') $rows = array('Reason' => '<span style="font-weight:600">' . vj_mail_e($reason) . '</span>') + $rows;
    $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ', we\'ve <b>initiated a refund of ₹' . number_format((float) $amount, 0) . '</b> for your order. We\'re really sorry it didn\'t work out this time.')
        . vj_mail_steps(1, false, array('Refund initiated', 'Processing', 'Credited'))
        . vj_mail_box($order, $rows, true)
        . vj_mail_btn(vj_mail_order_url($order), 'Track my refund')
        . vj_mail_note('<b>Not in your account after 8 working days?</b> <a href="' . esc_url(vj_mail_ticket_url($order, 'refund')) . '" style="color:#202844;font-weight:700">Raise a ticket</a> and a real person will sort it out right away.', 'info');
    return vj_mail_send($order, $to, 'Refund initiated · #' . $order->get_order_number() . ' · ₹' . number_format((float) $amount, 0),
        vj_mail_wrap('Your refund of ₹' . number_format((float) $amount, 0) . ' has been initiated.', 'Refund initiated', 'Your refund is on its way, ' . vj_mail_e($first), 'info', $inner), 'refund');
}

function vj_mail_exchange_offer($order, $token, $item, $size, $count) {
    $to = $order->get_billing_email(); if (!$to) return false;
    $first = $order->get_billing_first_name() ?: 'there';
    $url = VJ_MAIL_SHOP . '/exchange/' . rawurlencode($token);
    $what = $item ? '<b>' . vj_mail_e($item) . '</b>' . ($size ? ' (size ' . vj_mail_e($size) . ')' : '') : 'the jersey you ordered';
    $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ' 👋 We\'re really sorry — ' . $what . ' isn\'t available any more. Instead of a refund, you can <b>pick another jersey at no extra cost</b>. We\'ve hand-picked ' . ($count > 1 ? $count . ' kits' : 'some kits') . ' for you that are in stock right now.')
        . vj_mail_box($order, array('Extra to pay' => '₹0', 'Ships' => 'within 24–48 hrs of your pick'), true)
        . vj_mail_btn($url, 'Choose my new jersey')
        . vj_mail_p('Open the link, tap a jersey, pick your size and tap <b>Exchange for this</b>. That\'s it — your order updates by itself and you\'ll get the tracking number when it ships.', true)
        . vj_mail_note('Prefer your money back instead? <a href="' . esc_url(vj_mail_ticket_url($order, 'refund')) . '" style="color:#202844;font-weight:700">Tell us here</a> and we\'ll refund you.', 'info');
    return vj_mail_send($order, $to, 'Pick a new jersey · order #' . $order->get_order_number() . ' (no extra cost)',
        vj_mail_wrap('Your jersey is out of stock — choose another one at no extra cost.', 'Exchange', 'Choose your new jersey, ' . vj_mail_e($first), 'warn', $inner), 'exchange-offer');
}

function vj_mail_exchange_done($order, $from, $to_name, $size) {
    $to = $order->get_billing_email(); if (!$to) return false;
    $first = $order->get_billing_first_name() ?: 'there';
    $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ' 👋 Done! Your order now has <b>' . vj_mail_e($to_name) . '</b>' . ($size ? ' in size <b>' . vj_mail_e($size) . '</b>' : '') . ($from ? ' instead of ' . vj_mail_e($from) : '') . '. Nothing to pay — we\'re packing it now.')
        . vj_mail_steps(2, false, array('Exchange chosen', 'Packing', 'Shipped', 'Delivered'))
        . vj_mail_box($order, array(), true)
        . vj_mail_btn(vj_mail_order_url($order), 'View my order')
        . vj_mail_p('You\'ll get your India Post tracking number by email the moment it ships.', true);
    return vj_mail_send($order, $to, 'Exchange confirmed · #' . $order->get_order_number() . ' · ' . $to_name,
        vj_mail_wrap('Your new jersey is confirmed — packing now.', 'Exchange confirmed', 'Your new jersey is locked in 🎉', 'good', $inner), 'exchange-done');
}

// Replace one line of the order with the jersey the customer picked. The price
// they paid stays exactly the same (no money changes hands), the order note says
// what changed, the storefront's copy of the order and the dashboard mirror are
// updated, and the customer gets the "Exchange confirmed" email.
function vj_exchange_apply($order, $pp) {
    if ($order->get_meta('_vj_exchanged_' . sanitize_key(isset($pp['token']) ? $pp['token'] : ''))) return true;   // already done
    $prod = wc_get_product(absint($pp['product_id']));
    if (!$prod) return 'product ' . absint($pp['product_id']) . ' not found';
    $from = isset($pp['from_name']) ? strtolower(trim((string) $pp['from_name'])) : '';
    $old = null;
    foreach ($order->get_items() as $iid => $it) { if ($from !== '' && strtolower(trim($it->get_name())) === $from) { $old = $it; break; } }
    if (!$old) foreach ($order->get_items() as $iid => $it) { $old = $it; break; }
    if (!$old) return 'order has no items';
    $qty = max(1, (int) $old->get_quantity());
    $subtotal = (float) $old->get_subtotal(); $total = (float) $old->get_total();
    $oldName = $old->get_name(); $oldSize = '';
    foreach ($old->get_meta_data() as $m) { $d = $m->get_data(); if (isset($d['key']) && preg_match('/size/i', $d['key'])) { $oldSize = (string) $d['value']; break; } }
    $size = sanitize_text_field(isset($pp['size']) ? $pp['size'] : '');
    $order->remove_item($old->get_id());
    $nid = $order->add_product($prod, $qty, array('subtotal' => $subtotal, 'total' => $total));
    if (!$nid) return 'could not add the new jersey';
    $ni = $order->get_item($nid);
    if ($ni && $size) { $ni->add_meta_data('Sizes', $size, true); $ni->save(); }
    $tok = sanitize_key(isset($pp['token']) ? $pp['token'] : '');
    $order->update_meta_data('_vj_exchanged_' . $tok, time());
    $order->add_order_note('EXCHANGED by the customer: ' . $oldName . ($oldSize ? ' (' . $oldSize . ')' : '') . ' → ' . $prod->get_name() . ($size ? ' (' . $size . ')' : '') . ' — price unchanged.');
    $order->save();
    // the storefront's own copy (order page) shows the new jersey too
    $items = array();
    foreach ($order->get_items() as $it) {
        $sz = ''; foreach ($it->get_meta_data() as $m) { $d = $m->get_data(); if (isset($d['key']) && preg_match('/size/i', $d['key'])) { $sz = (string) $d['value']; break; } }
        $p = $it->get_product(); $img = '';
        if ($p) { $iid = $p->get_image_id() ?: ($p->get_parent_id() ? wc_get_product($p->get_parent_id())->get_image_id() : 0); if ($iid) $img = wp_get_attachment_image_url($iid, 'medium'); }
        $items[] = array('product_id' => (string) $it->get_product_id(), 'name' => $it->get_name(), 'size' => $sz, 'qty' => (int) $it->get_quantity(), 'price' => round((float) $it->get_total() / max(1, (int) $it->get_quantity()), 2), 'image' => $img);
    }
    vj_supa_req('PATCH', 'shop_orders?woo_order_id=eq.' . rawurlencode((string) $order->get_id()), array('items' => $items));
    vj_mail_exchange_done($order, $oldName, $prod->get_name(), $size);
    return true;
}

function vj_mail_processing($order, $to = '') {
    $to = $to ?: $order->get_billing_email(); if (!$to) return false;
    $first = $order->get_billing_first_name() ?: 'there';
    $paid = vj_pay_amount($order);
    $addr = $order->get_formatted_shipping_address() ?: $order->get_formatted_billing_address();
    $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ' 👋 Your payment is in and your order is confirmed. We\'re packing it now — it leaves within <b>24–48 hours</b> and reaches you in about <b>5–6 days</b> by India Post.')
        . vj_mail_steps(1)
        . vj_mail_box($order, array('Paid via UPI' => '₹' . number_format((float) $paid, 2), 'Shipping' => 'Free · India Post', 'Deliver to' => '<span style="font-weight:600;font-size:12.5px;line-height:1.4">' . wp_kses_post(str_replace('<br/>', '<br>', $addr)) . '</span>'))
        . vj_mail_btn(vj_mail_order_url($order), 'View my order')
        . vj_mail_p('You\'ll get your <b>tracking number</b> by email the moment it ships, and we\'ll keep you posted when it\'s out for delivery.', true)
        . vj_mail_note('<b>Sizing worry?</b> Our kits run slightly fitted. If you think you picked the wrong size, reply to this email within the hour and we\'ll change it before it\'s packed.', 'info');
    return vj_mail_send($order, $to, 'Order confirmed · #' . $order->get_order_number() . ' — thanks, ' . $first . '!', vj_mail_wrap('Payment received. Packing now, ships within 24-48 hours.', 'Payment received', 'Your jersey is confirmed 🎉', 'good', $inner), 'processing');
}

function vj_mail_shipped($order, $to = '', $reship = false) {
    $to = $to ?: $order->get_billing_email(); if (!$to) return false;
    $first = $order->get_billing_first_name() ?: 'there';
    $trk = vj_track_number($order);
    $inner = ($reship
            ? vj_mail_p('Hi ' . vj_mail_e($first) . ' 👋 Thank you for the reship payment — <b>your jersey went out again today</b> with a fresh India Post tracking number (below). Delivery usually takes <b>4–6 days</b>; we\'ll email you the day it\'s out for delivery.')
            : vj_mail_p('Hi ' . vj_mail_e($first) . ' 👋 Your parcel is with India Post and on its way. Delivery usually takes <b>4–6 days</b>; we\'ll email you the day it\'s out for delivery.'))
        . ($reship ? vj_mail_steps(3, false, array('Ordered', 'Returned', 'Reshipped', 'Out for delivery', 'Delivered')) : vj_mail_steps(3))
        . ($trk ? '<div style="text-align:center;border:2px dashed #D9DADF;border-radius:14px;padding:14px;margin:18px 0"><div style="font-size:11px;font-weight:800;letter-spacing:.8px;color:#9A9CA6">INDIA POST TRACKING NUMBER</div><div style="font-size:24px;font-weight:900;letter-spacing:1px;margin-top:4px">' . vj_mail_e($trk) . '</div></div>' : '')
        . vj_mail_box($order, array(), true)
        . vj_mail_btn(vj_mail_order_url($order), 'Track my order')
        . vj_mail_note('📱 <b>Keep your phone reachable.</b> The postman usually calls before delivering. If nobody answers, India Post tries again the next working day — and after 3 attempts the parcel comes back to us.', 'info');
    if ($reship) return vj_mail_send($order, $to, 'Reshipped · #' . $order->get_order_number() . ($trk ? ' · new tracking ' . $trk : ''), vj_mail_wrap('Your jersey is on its way again — new tracking number inside.', 'Reshipped', 'On its way again, ' . vj_mail_e($first) . ' 📦', 'info', $inner), 'shipped');
    return vj_mail_send($order, $to, 'Shipped · #' . $order->get_order_number() . ($trk ? ' · tracking ' . $trk : ''), vj_mail_wrap('Your jersey has shipped with India Post.', 'Shipped', 'It\'s on its way, ' . vj_mail_e($first) . ' 📦', 'info', $inner), 'shipped');
}

// $stage: out · failed · delivered · returning · rto     $ctx: attempts, desc, where, when, test
function vj_mail_track($order, $stage, $ctx = array(), $to = '') {
    $to = $to ?: $order->get_billing_email(); if (!$to) return false;
    $first = $order->get_billing_first_name() ?: 'there';
    $num = $order->get_order_number();
    $trk = vj_track_number($order);
    $attempts = isset($ctx['attempts']) ? (int) $ctx['attempts'] : 0;
    $where = !empty($ctx['where']) ? vj_mail_e($ctx['where']) : '';
    $desc = !empty($ctx['desc']) ? vj_mail_e($ctx['desc']) : '';
    $orderBtn = vj_mail_btn(vj_mail_order_url($order), 'View my order');
    $nth = array(1 => 'first', 2 => 'second', 3 => 'third', 4 => 'fourth');

    if ($stage === 'out') {
        $again = $attempts > 0;
        $subject = ($again ? 'Out for delivery again today' : 'Out for delivery today') . ' · #' . $num;
        $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ' 👋 The postman has your parcel <b>today</b>' . ($where ? ' (from ' . $where . ')' : '') . '. ' . ($again ? 'This is another attempt after the last one didn\'t go through — ' : '') . 'Please keep your phone on and reachable; India Post usually calls before coming.')
            . vj_mail_steps(4)
            . vj_mail_box($order, array('Tracking' => vj_mail_e($trk)), false)
            . $orderBtn
            . vj_mail_note('If you won\'t be home, tell a family member or neighbour — anyone at the address can receive it. ' . ($again ? '<b>After 3 failed attempts the parcel returns to us</b>, and reshipping costs ₹' . VJ_RESHIP_FEE . '.' : ''), $again ? 'warn' : 'good');
        return vj_mail_send($order, $to, $subject, vj_mail_wrap('Your jersey is out for delivery today — keep your phone reachable.', $again ? 'Delivery re-attempt' : 'Out for delivery', 'Your jersey arrives today 🚚', 'good', $inner), 'out-' . gmdate('Ymd'));
    }

    if ($stage === 'failed') {
        $n = max(1, $attempts);
        $left = max(0, 3 - $n);
        $subject = ($n >= 3 ? 'Delivery failed 3 times' : 'We couldn\'t deliver today (' . ($nth[$n] ?? $n . 'th') . ' attempt)') . ' · #' . $num;
        $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ', India Post tried to deliver your parcel today but couldn\'t hand it over' . ($desc ? ' — their note: <i>“' . $desc . '”</i>' : '') . '. This usually means nobody was available or the phone didn\'t connect.')
            . vj_mail_steps(4, true)
            . vj_mail_box($order, array('Tracking' => vj_mail_e($trk), 'Attempts so far' => $n . ' of 3' . ($where ? '<br><span style="font-weight:600;color:#6B6E7A">' . $where . '</span>' : '')), false)
            . ($left > 0
                ? vj_mail_note('<b>What happens next:</b> they try again on the next working day. Keep your phone on, or ask anyone at the address to receive it. ' . ($left === 1 ? '<b>This is the last attempt</b> — after it the parcel comes back to us and reshipping costs ₹' . VJ_RESHIP_FEE . '.' : 'You have ' . $left . ' attempts left.'), $left === 1 ? 'bad' : 'warn')
                : vj_mail_note('<b>All 3 attempts have failed</b>, so India Post will now send the parcel back to us. We\'ll email you the moment it arrives with how to get it reshipped (₹' . VJ_RESHIP_FEE . ').', 'bad'))
            . vj_mail_btn(vj_mail_wa($order, 'Hi, this is ' . $first . '. India Post couldn\'t deliver my order #' . $num . ' (' . $trk . '). Can you help?'), 'Something wrong? Chat with us', false)
            . vj_mail_p('Tip: you can also collect it yourself from your delivery post office with the tracking number above.', true);
        return vj_mail_send($order, $to, $subject, vj_mail_wrap('India Post couldn\'t deliver today — here\'s what to do.', 'Delivery attempted', 'We tried today, ' . vj_mail_e($first) . ' — one more go', 'warn', $inner), 'failed-' . $n);
    }

    if ($stage === 'delivered') {
        $ref = $order->get_meta('_vj_ref');
        $review = VJ_MAIL_SHOP . '/review?o=' . rawurlencode($ref ?: (string) $num) . '&n=' . rawurlencode($first);
        $subject = 'Delivered 🎉 How does it look, ' . $first . '?';
        $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ', your parcel was delivered today. We hope it fits like a glove and looks even better in person.')
            . vj_mail_steps(5)
            . vj_mail_box($order, array(), true)
            . '<div style="background:#14161F;border-radius:16px;padding:20px 18px;margin:18px 0;text-align:center">'
            . '<div style="font-size:18px;font-weight:900;color:#F0E8D5;margin-bottom:6px">Wear it. Snap it. Get featured. 📸</div>'
            . '<div style="font-size:13.5px;line-height:1.55;color:#C9C2B0;margin-bottom:14px">Send us a photo in your new kit with a line about it — the best ones go up on the shop\'s review wall for thousands to see.</div>'
            . vj_mail_btn($review, 'Share my photo', false) . '</div>';
        return vj_mail_send($order, $to, $subject, vj_mail_wrap('Delivered! Share a photo of your new jersey.', 'Delivered', 'Enjoy your jersey, ' . vj_mail_e($first) . ' 🎉', 'good', $inner), 'delivered');
    }

    if ($stage === 'returning') {
        $subject = 'Your parcel is coming back to us · #' . $num;
        $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ', India Post couldn\'t deliver your order after their attempts, so the parcel is now <b>travelling back to us</b>. Nothing is lost — your jersey is safe and it\'s still yours.')
            . vj_mail_steps(4, true, array('Ordered', 'Shipped', 'Delivery failed', 'Returning', 'Reship'))
            . vj_mail_box($order, array('Tracking' => vj_mail_e($trk)), false)
            . vj_mail_note('<b>What happens next:</b> the parcel takes a few days to reach us. The moment it\'s back, we\'ll email you a simple way to get it reshipped to you (a ₹' . VJ_RESHIP_FEE . ' shipping charge — we covered the first one). Nothing to do until then.', 'info')
            . vj_mail_btn(vj_mail_wa($order, 'Hi, this is ' . $first . '. My order #' . $num . ' is returning to you. I want it reshipped.'), 'Questions? Chat with us', false);
        return vj_mail_send($order, $to, $subject, vj_mail_wrap('India Post is returning your parcel to us — here is what happens next.', 'Returning to sender', 'Your parcel is on its way back to us', 'warn', $inner), 'returning');
    }

    if ($stage === 'rto') {
        $pay = get_option('vj_payinfo');
        $upi = !empty($pay['upi']) ? $pay['upi'] : 'iblamerishi@fam';
        $payee = get_option('vj_payee_name', 'Visions Jersey');
        $ref = $order->get_meta('_vj_ref') ?: (string) $num;
        $upiLink = 'upi://pay?pa=' . rawurlencode($upi) . '&pn=' . rawurlencode($payee) . '&am=' . VJ_RESHIP_FEE . '&cu=INR&tn=' . rawurlencode('Reship ' . $ref);
        $qr = 'https://api.qrserver.com/v1/create-qr-code/?size=360x360&margin=8&data=' . rawurlencode($upiLink);
        $wa = vj_mail_wa($order, 'Hi, this is ' . $first . '. I paid ₹' . VJ_RESHIP_FEE . ' to reship order #' . $num . ' (' . $trk . '). Here is my screenshot:');
        $n = max(1, min(3, $attempts ?: 3));
        $subject = 'Your parcel is back with us — let\'s get it to you · #' . $num;
        $inner = vj_mail_p('Hi ' . vj_mail_e($first) . ', your parcel has come back to our warehouse. India Post attempted delivery <b>' . $n . ' time' . ($n > 1 ? 's' : '') . '</b> at your address and couldn\'t hand it over' . ($desc ? ' (their last note: <i>“' . $desc . '”</i>)' : '') . '. We totally get it — a missed call, a busy day, a travel week. It happens.')
            . vj_mail_steps(4, true, array('Ordered', 'Shipped', $n . ' attempts', 'Back with us', 'Reship'))
            . vj_mail_box($order, array('Tracking (returned)' => vj_mail_e($trk)), true)
            . vj_mail_p('<b>Your jersey is still yours and ready to go out again.</b> We covered the first shipment; the return trip and the second one are charged by India Post, so a <b>₹' . VJ_RESHIP_FEE . ' reshipping fee</b> is all it takes to send it back on its way — same day, with a fresh tracking number.')
            . '<div style="text-align:center;border:1px solid #E6E7EB;border-radius:16px;padding:18px 14px;margin:16px 0">'
            . '<div style="font-size:11px;font-weight:800;letter-spacing:.8px;color:#9A9CA6">SCAN TO PAY · RESHIPPING</div>'
            . '<div style="font-size:34px;font-weight:900;margin:4px 0 8px">₹' . VJ_RESHIP_FEE . '</div>'
            . '<img src="' . esc_url($qr) . '" width="190" height="190" alt="UPI QR" style="display:block;margin:0 auto 8px;border-radius:10px">'
            . '<div style="font-size:12.5px;color:#6B6E7A">Any UPI app · or pay to <b style="color:#14161F">' . vj_mail_e($upi) . '</b></div></div>'
            . '<div style="font-size:13.5px;font-weight:800;text-align:center;margin:0 0 10px">Then tell us you have paid 👇</div>'
            . vj_mail_btn(vj_mail_ticket_url($order, 'delivery'), '💬 Open my ticket chat')
            . vj_mail_p('Write “paid ₹' . VJ_RESHIP_FEE . ' for reship” in the chat (with the UPI reference if you have it). We confirm within the hour and reship the same day. New address? Type it in the same chat.', true)
            . vj_mail_note('<b>Why the fee?</b> India Post charges us for the return and for the second delivery. ₹' . VJ_RESHIP_FEE . ' covers only the shipping — the jersey itself is already paid for and waiting for you.', 'info');
        return vj_mail_send($order, $to, $subject, vj_mail_wrap('Your parcel is back with us. ₹' . VJ_RESHIP_FEE . ' reship — scan, pay, tell us in the chat.', 'Back with us', 'Let\'s get your jersey to you, ' . vj_mail_e($first), 'bad', $inner), 'rto');
    }
    return false;
}

// =====================================================================
//  v7.3 — NO WORDPRESS SIGN-UPS, NO "ACCOUNT CREATED" EMAILS (22 Oct 2026)
//  Customers never need a WordPress account: they order on the shop and see
//  their orders with an emailed code (Supabase). Bots were signing up on the
//  WordPress "My account" page — every fake sign-up sent TWO emails through
//  Resend ("Your account has been created" + "[Visions Jersey] New User
//  Registration"), using the daily limit, and one was already marked as spam.
//  This turns sign-ups off and stops both emails, whatever the WordPress /
//  WooCommerce settings say. Admins can still add users in wp-admin → Users.
//  Undo: /wp-admin/?vj_signups=on   ·   back off: /wp-admin/?vj_signups=off
// =====================================================================
function vj_signups_blocked() { return get_option('vj_signups', 'off') !== 'on'; }

add_action('admin_init', function () {
    if (!current_user_can('manage_options') || !isset($_GET['vj_signups'])) return;
    $v = $_GET['vj_signups'] === 'on' ? 'on' : 'off';
    update_option('vj_signups', $v, false);
    wp_die($v === 'on' ? 'WordPress sign-ups are ON again (WordPress / WooCommerce settings decide).' : 'WordPress sign-ups are OFF ✓ — no new accounts, no "account created" / "new user registration" emails.', 'VJ sign-ups');
});

// 1 · the sign-up forms themselves
add_filter('pre_option_users_can_register', function ($v) { return vj_signups_blocked() ? '0' : $v; });
add_filter('pre_option_woocommerce_enable_myaccount_registration', function ($v) { return vj_signups_blocked() ? 'no' : $v; });
add_filter('pre_option_woocommerce_enable_signup_and_login_from_checkout', function ($v) { return vj_signups_blocked() ? 'no' : $v; });
add_filter('pre_option_woocommerce_enable_checkout_login_reminder', function ($v) { return vj_signups_blocked() ? 'no' : $v; });

// 2 · a bot posting straight to the sign-up address is refused too
add_filter('registration_errors', function ($errors) {
    if (vj_signups_blocked()) $errors->add('vj_closed', 'Sign-ups are closed. You can order and track your orders on shop.visionsjersey.com without an account.');
    return $errors;
}, 99);
add_filter('woocommerce_process_registration_errors', function ($errors) {
    if (vj_signups_blocked()) $errors->add('vj_closed', 'Sign-ups are closed. You can order and track your orders on shop.visionsjersey.com without an account.');
    return $errors;
}, 99);

// 3 · and even if an account is made some other way, neither email goes out
add_filter('woocommerce_email_enabled_customer_new_account', function ($on) { return vj_signups_blocked() ? false : $on; }, 99);
add_filter('wp_send_new_user_notification_to_admin', function ($on) { return vj_signups_blocked() ? false : $on; }, 99);
add_filter('wp_send_new_user_notification_to_user', function ($on) { return vj_signups_blocked() ? false : $on; }, 99);

// =====================================================================
//  v7.4.2 — GOOGLE: THE SHOP IS THE REAL PRODUCT PAGE (24 Oct 2026)
//  The same jerseys are on visionsjersey.com/product/… (WooCommerce) and on
//  shop.visionsjersey.com/jersey/… (where people actually order). Two copies
//  compete with each other on Google. Each WordPress product page now tells
//  Google, with a canonical link, that the shop page is the real one — so
//  Google sends searchers to the shop. Nothing changes for visitors.
//  Off: /wp-admin/?vj_canonical=off   ·   on again: /wp-admin/?vj_canonical=on
// =====================================================================
function vj_shop_canonical_on() { return get_option('vj_shop_canonical', 'on') === 'on'; }
function vj_shop_canonical_url() {
    if (!vj_shop_canonical_on() || !function_exists('is_singular') || !is_singular('product')) return '';
    $post = get_queried_object();
    if (!$post || empty($post->post_name) || $post->post_status !== 'publish') return '';
    return 'https://shop.visionsjersey.com/jersey/' . $post->post_name;
}
add_action('admin_init', function () {
    if (!current_user_can('manage_options') || !isset($_GET['vj_canonical'])) return;
    $v = $_GET['vj_canonical'] === 'off' ? 'off' : 'on';
    update_option('vj_shop_canonical', $v, false);
    wp_die($v === 'on' ? 'Product pages now point Google to shop.visionsjersey.com ✓' : 'Product pages point to themselves again (WordPress default).', 'VJ canonical');
});
// WordPress itself, and the common SEO plugins (Yoast, Rank Math, All in One SEO)
add_filter('get_canonical_url', function ($url) { $u = vj_shop_canonical_url(); return $u ? $u : $url; }, 99);
add_filter('wpseo_canonical', function ($url) { $u = vj_shop_canonical_url(); return $u ? $u : $url; }, 99);
add_filter('wpseo_opengraph_url', function ($url) { $u = vj_shop_canonical_url(); return $u ? $u : $url; }, 99);
add_filter('rank_math/frontend/canonical', function ($url) { $u = vj_shop_canonical_url(); return $u ? $u : $url; }, 99);
add_filter('aioseo_canonical_url', function ($url) { $u = vj_shop_canonical_url(); return $u ? $u : $url; }, 99);
