// ============================================================
//  YOUR SETTINGS — edit this file only.
//  New index.html files from Claude never overwrite it.
// ============================================================
window.VJ_CONFIG = {
  SUPABASE_URL:  'https://amgiihalfdhogzjrxinu.supabase.co',
  // Supabase → Project Settings → API Keys → "anon public" (long eyJ… string)
  SUPABASE_ANON: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFtZ2lpaGFsZmRob2d6anJ4aW51Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODMwMTMwMDYsImV4cCI6MjA5ODU4OTAwNn0.9_Xgmdp79CtMgXUhL4W3PbC2ZXz_eSbJCItIUHZSrvw',
  PIXEL_ID: '1849071612747524',                  // Meta Events Manager → pixel ID
  GA4_ID:   'G-HCDG2MXYY0',                  // Google Analytics 4, e.g. 'G-XXXXXXX'
  UPI_ID:     'sarva-393@federal',
  PAYEE_NAME: 'Visions Jersey',  // shown in the customer's UPI app
  WHATSAPP:   '917434882164',    // country code, no +
  SHIPPING:    80,               // ₹ added to every order
  PAY_MINUTES: 10,                // payment countdown
  COUPONS: {
    VJ50: { off: 50, type: 'flat', label: '₹50 off' },
  },
  OFFER_CODE: 'VJ50',
  OFFER_TEXT: '₹50 OFF your order',
  // ---- Image compression ----
  // Product photos are resized + compressed + served as WebP before they reach
  // the shopper (roughly 400 KB down to 30 KB). Set to false to serve originals.
  IMG_CDN: true,
  // ---- Branding ----
  // Put your logo file in the same folder and name it here (e.g. 'logo.png').
  // Leave '' to keep the text wordmark.
  LOGO_IMAGE: 'logo.png',

  // Optional size-chart image (put it in the folder, e.g. 'sizechart.jpg').
  // Leave '' to show the built-in chest/length table instead.
  SIZE_CHART: 'sizechart.jpg',
  // ---- Hero background ----
  // Your image in the site folder (e.g. 'hero.jpg'). Leave '' to keep the
  // scrolling jersey strip.
  HERO_IMAGE: 'hero.jpg',
  // Black overlay strength: 0.4 = light, 0.6 = balanced, 0.75 = very dark
  HERO_DIM: 0.6,
  // Optional: your own QR image (e.g. 'qr.png'). Leave '' to auto-generate a
  // QR that already contains the exact amount + order reference (recommended).
  UPI_QR_IMAGE: '',
  // ---- Homepage ----
  // Club badges + curated rows. Names are matched against product titles, so
  // use the same spelling you use in WooCommerce.
  CLUBS: ['Real Madrid', 'Arsenal', 'Barcelona', 'AC Milan', 'Manchester City', 'Al Nassr'],
  // Banners. Add your own images (put them in the folder) for Ronaldo/Messi etc.
  // Leave the array empty to auto-generate banners from your product photos.
  BANNERS: [
    // { img: 'banner-ronaldo.jpg', title: 'Ronaldo Collection', sub: 'Al Nassr & Real Madrid kits', cat: '' },
    // { img: 'banner-messi.jpg',   title: 'Messi Classics',     sub: 'Barcelona & Argentina',      cat: '' },
  ],
};
