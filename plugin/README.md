# Visions Jersey Push — WordPress plugin

The WordPress/WooCommerce plugin that runs on **visionsjersey.com**.

- **File:** `visions-jersey-push/visions-jersey-push.php`
- **Current version:** 7.4.2
- **Author:** Visions Jersey

## What it does
- Sends Expo push notifications for new orders.
- Exposes a secret proxy endpoint `?vj_rest=1` (guarded by the `vj_email_secret`
  option set in WP admin → Settings → VJ Push) that runs WooCommerce REST calls
  from inside WordPress. The automation in this repo uses this "door" when the
  host firewall blocks GitHub's servers from reaching `/wp-json/`.

## How to deploy to WordPress (current manual steps)
1. Zip the `visions-jersey-push/` folder.
2. WP admin → Plugins → Add New → Upload Plugin → choose the zip → Replace current.

> Keep the version number in the plugin header bumped on each change so WordPress
> shows the update clearly.
