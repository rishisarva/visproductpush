"""
Visions Jersey SEO builder — v2.0 (24 Oct 2026)

Runs inside vj_images.py on every hourly GitHub sync (after the product list and photos are ready).
Reads its settings from the shop's own SEO folder: shop.visionsjersey.com/seo/settings.json
(the keyword map from the research, the guides, which pages to push). No settings file = nothing
is rebuilt (the last pages stay online), so deleting the seo folder switches SEO off safely.

Builds on Cloudflare (vj-images.pages.dev):
  seo/jersey/<slug>.html   every jersey: keyword-style title ("Bellingham Real Madrid 2026-27 Home Jersey – Half
                           Sleeve Embroidery | ₹550"), description, specs table, FAQ, "Popular searches" links
                           with the exact keywords people type, Product data for Google (price, stock, shipping)
  seo/c/<slug>.html        every collection: clubs, players, types, price bands, club × type combos and a page for
                           every researched keyword with enough jerseys; sections for the smaller keywords
                           ("Real Madrid jersey full sleeve", "Ronaldo Real Madrid jersey" …), FAQ, related links
  seo/guide/<slug>.html    guides that answer questions (size chart, player vs fan version …) and link to the products
  seo/data/…               the same content as JSON — the shop's seo.js draws it while people browse
  seo/sitemap.xml          every page with its photos
  img/<jersey-name>-1.jpg  photo copies with the jersey's name in the file name (hard links, no extra space)
  feeds/google-products.xml  Google Shopping (Merchant Center) feed with the keyword-style titles
Your edits from the dashboard (Supabase table shop_seo) win over the automatic text.
"""
import datetime, html, json, os, re, shutil, statistics, urllib.request

SHOP = os.environ.get('VJ_SHOP', 'https://shop.visionsjersey.com').rstrip('/')
VERSION = 'v2.0'

# ---------------------------------------------------------------- what the names say
# Same rules as the shop's own club / player / type pages (index.html CLUB_DEFS / PLAYER_DEFS / TYPES).
I = re.I
CLUBS = [
    ('Real Madrid', r'real ?madrid'), ('Barcelona', r'barcelona|\bbar[cç]a\b'),
    ('Manchester United', r'man(chester)? ?(united|utd)\b'), ('Manchester City', r'man(chester)? ?city'),
    ('Liverpool', r'liverpool'), ('Arsenal', r'arsenal'), ('Chelsea', r'chelsea'), ('Tottenham', r'tottenham|\bspurs\b'),
    ('Newcastle', r'newcastle'), ('Aston Villa', r'aston ?villa'), ('AC Milan', r'\bac ?milan\b'),
    ('Inter Milan', r'inter ?milan|\binter(nazionale)?\b(?! ?miami)'), ('Juventus', r'juventus|\bjuve\b'),
    ('Napoli', r'napoli'), ('AS Roma', r'\bas ?roma\b|\broma\b'), ('Bayern Munich', r'bayern'),
    ('Borussia Dortmund', r'dortmund|\bbvb\b'), ('PSG', r'\bpsg\b|paris saint'), ('Atletico Madrid', r'atl[eé]tico'),
    ('Ajax', r'\bajax\b'), ('Benfica', r'benfica'), ('FC Porto', r'\bporto\b'), ('Celtic', r'\bceltic\b(?!s)'),
    ('Al Nassr', r'al ?nassr'), ('Al Hilal', r'al ?hilal'), ('Inter Miami', r'inter ?miami|\bmiami\b'),
    ('Santos', r'\bsantos\b'), ('Boca Juniors', r'\bboca\b'),
    ('Argentina', r'argentina'), ('Brazil', r'brazil|brasil'), ('Portugal', r'portugal'), ('France', r'\bfrance\b'),
    ('Germany', r'germany'), ('Spain', r'\bspain\b'), ('England', r'\bengland\b'), ('Italy', r'\bitaly\b'),
    ('Netherlands', r'netherlands|holland'), ('Croatia', r'croatia'), ('Belgium', r'belgium'), ('Japan', r'\bjapan\b'),
    ('Mexico', r'mexico'), ('Uruguay', r'uruguay'), ('Colombia', r'colombia'), ('India', r'\bindia\b'),
]
COUNTRIES = {'Argentina', 'Brazil', 'Portugal', 'France', 'Germany', 'Spain', 'England', 'Italy', 'Netherlands',
             'Croatia', 'Belgium', 'Japan', 'Mexico', 'Uruguay', 'Colombia', 'India'}
PLAYERS = [
    ('Messi', r'\bmessi\b'), ('Ronaldo', r'\b(ronaldo|cr7)\b'), ('Neymar', r'\bneym[ae]r\b'), ('Mbappe', r'\bmbapp?[eé]\b'),
    ('Yamal', r'\byamal\b'), ('Bellingham', r'\bbel+ingham\b'), ('Haaland', r'\bhaa?land\b'), ('Vinicius', r'\bvini(cius)?( jr)?\b'),
    ('Salah', r'\bsalah\b'), ('Beckham', r'\bbeckham\b'), ('Zidane', r'\bzidane\b'), ('Ronaldinho', r'\bronaldinho\b'),
    ('Kaka', r'\bkaka\b'), ('Rooney', r'\brooney\b'), ('Drogba', r'\bdrogba\b'), ('Henry', r'\bhenry\b'), ('Gerrard', r'\bgerrard\b'),
    ('Torres', r'\btorres\b'), ('Xavi', r'\bxavi\b'), ('Iniesta', r'\biniesta\b'), ('Pedri', r'\bpedri\b'), ('Raphinha', r'\braphinha\b'),
    ('Lewandowski', r'\blewandowski\b'), ('Modric', r'\bmodric\b'), ('Kroos', r'\bkroos\b'), ('Benzema', r'\bbenzema\b'),
    ('De Bruyne', r'\bde ?bruyne\b'), ('Foden', r'\bfoden\b'), ('Saka', r'\bsaka\b'), ('Odegaard', r'\bodegaard\b'), ('Rice', r'\brice\b'),
    ('Rashford', r'\brashford\b'), ('Bruno Fernandes', r'\bbruno\b|\bb\.? ?fern'), ('Palmer', r'\bpalmer\b'), ('Mac Allister', r'\bmac ?all?ister\b'),
    ('Szoboszlai', r'\bszobo'), ('Musiala', r'\bmusiala\b'), ('Kane', r'\bkane\b'), ('Olise', r'\bolise\b'), ('Griezmann', r'\bgriezmann\b'),
    ('Cubarsi', r'\bcubarsi\b'), ('Gavi', r'\bgavi\b'), ('Valverde', r'\bvalverde\b'), ('Dembele', r'\bdembel[eé]\b'), ('Gyokeres', r'\bgy[oö]keres\b'),
    ('Lautaro', r'\blautaro\b'), ('Yildiz', r'\byildiz\b'), ('Luis Diaz', r'\bluis ?diaz\b'), ('Van Dijk', r'\bvirgil\b|van ?dijk'),
    ('Maldini', r'\bmaldini\b'), ('Pirlo', r'\bpirlo\b'), ('Totti', r'\btotti\b'), ('Del Piero', r'\bdel ?piero\b'), ('Buffon', r'\bbuffon\b'),
    ('Maradona', r'\bmaradona\b'), ('Pele', r'\bpel[eé]\b'), ('Ozil', r'\b[oö]zil\b'), ('Fabregas', r'\bfabregas\b'), ('Owen', r'\bowen\b'),
    ('Coutinho', r'\bcoutinho\b'), ('Giggs', r'\bgiggs\b'), ('Cantona', r'\bcantona\b'), ('Pique', r'\bpiqu[eé]\b'), ('Puyol', r'\bpuyol\b'),
    ('Virat Kohli', r'\bvirat\b|\bkohli\b'), ('Rohit Sharma', r'\brohit\b'), ('Dhoni', r'\bdhoni\b'), ('Sanju Samson', r'\bsanju\b'),
]
NOT_SHORT = r'five ?sleeve|5 ?sleeve|full ?sleeve|long ?sleeve|sleeveless|polo|coller|collar|track|jacket|shorts?\b(?! ?sleeve)|hoodie|windbreaker'
TYPES = {
    # key: (regex, page slug, keyword, h1)
    'retro':       (r'retro|classic|anniversary|\b(19[6-9]\d|200\d|201[0-9])\b', 'retro-football-jerseys', 'Retro Football Jersey', 'Retro Football Jerseys'),
    'newseason':   (r'\b26[-/ ]?27\b|2026[-/ ]?(20)?27|\b2026\b(?![-/]2[0-5])|new season', 'new-season-2026-27-football-jerseys', '2026-27 Football Jersey', 'New Season 2026-27 Football Jerseys'),
    'embroidery':  (r'embro', 'embroidery-football-jerseys', 'Embroidery Football Jersey', 'Embroidery Football Jerseys'),
    'sublimation': (r'sublimat', 'sublimation-football-jerseys', 'Sublimation Football Jersey', 'Sublimation Football Jerseys'),
    'shortsleeve': (None, 'half-sleeve-football-jerseys', 'Half Sleeve Football Jersey', 'Half Sleeve Football Jerseys'),
    'fivesleeve':  (r'five ?sleeve|5 ?sleeve', 'five-sleeve-football-jerseys', 'Five Sleeve Football Jersey', 'Five Sleeve Football Jerseys'),
    'fullsleeve':  (r'full ?sleeve|long ?sleeve', 'full-sleeve-football-jerseys', 'Full Sleeve Football Jersey', 'Full Sleeve Football Jerseys'),
    'polo':        (r'\bpolo\b|coller|collar', 'collar-football-jerseys', 'Collar Football Jersey', 'Collar (Polo Neck) Football Jerseys'),
    'player':      (r'player ?(version|edition|issue)|imported|\bmaster\b', 'player-version-jerseys', 'Player Version Jersey', 'Player Version Jerseys'),
    'special':     (r'special|concept|limited', 'special-edition-football-jerseys', 'Special Edition Football Jersey', 'Special Edition Football Jerseys'),
    'premium':     (r'premium', 'premium-football-jerseys', 'Premium Football Jersey', 'Premium Quality Football Jerseys'),
    'national':    (r'world ?cup|portugal|brazil|argentina|france|spain|germany|england|italy|uruguay|netherlands|croatia|belgium|india|fifa|national|new zealand|australia|pakistan|south africa|sri lanka',
                    'national-team-football-jerseys', 'National Team Football Jersey', 'National Team Football Jerseys'),
}
# club × type combos worth their own page ("real madrid full sleeve jersey")
COMBO_TYPES = [('retro', 'retro', 'Retro'), ('fullsleeve', 'full-sleeve', 'Full Sleeve'), ('fivesleeve', 'five-sleeve', 'Five Sleeve'),
               ('newseason', '2026-27', '2026-27'), ('embroidery', 'embroidery', 'Embroidery'), ('player', 'player-version', 'Player Version')]
MIN_PAGE, MIN_COMBO = 2, 4

# ---------------------------------------------------------------- small helpers
COLORS = ['black', 'white', 'blue', 'pink', 'red', 'green', 'yellow', 'purple', 'gold', 'grey', 'orange']
KIT_RX = [('home', r'\bhome\b'), ('away', r'\baway\b'), ('third', r'\bthird\b'), ('special edition', r'\bspecial\b'),
          ('training', r'\btraining\b|\btrack'), ('goalkeeper', r'goal ?keeper|\bgk\b')]
KEEP_UP = {'PSG', 'AC', 'FC', 'CF', 'CR7', 'R9', 'NBA', 'IPL', 'CSK', 'RCB', 'MI', 'KKR', 'SRH', 'USA', 'UK', 'UCL', 'II', 'III', 'PSV',
           'AS', 'BVB', 'LA', 'NY', 'RB', 'SC', 'UEFA', 'FIFA', 'WC', 'T20', 'ODI', 'GT', 'LSG', 'DC', 'PBKS', 'RR', 'XL', 'XXL', 'XS', 'S', 'M', 'L'}
SMALL = {'and', 'with', 'of', 'the', 'in', 'for', 'a', 'to', 'vs'}


def slugify(s):
    return re.sub(r'[^a-z0-9]+', '-', str(s).lower().replace('₹', '')).strip('-')


def is_type(key, name):
    rx = TYPES[key][0]
    if key == 'shortsleeve':
        return not re.search(NOT_SHORT, name, I)
    return bool(re.search(rx, name, I))


def tcase(name):
    out = []
    for i, w in enumerate(str(name).strip().split()):
        bare = re.sub(r'[^A-Za-z0-9]', '', w).upper()
        if bare in KEEP_UP:
            out.append(w.upper())
        elif re.match(r'^[\d(]', w):
            out.append(w)
        elif i and w.lower() in SMALL:
            out.append(w.lower())
        else:
            out.append('-'.join(x[:1].upper() + x[1:].lower() for x in w.split('-')))
    return ' '.join(out)


def money(n):
    n = int(round(float(n or 0)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        s = re.sub(r'(\d)(?=(\d\d)+$)', r'\1,', head) + ',' + tail
    return '₹' + s


def e(s):
    return html.escape(str(s if s is not None else ''), quote=True)


def is_note(d):
    return bool(re.search(r'^\s*note\b|ai[\s-]*generated', str(d), I))


def hide_detail(d):
    return bool(re.match(r'^\s*(b2b|b2c|wholesale|bulk( order)?s?|reseller|dropship(ping)?)\s*$', str(d), I))


def vol_num(v):
    m = re.search(r'([\d,.]+)\s*([kK])?', str(v or ''))
    if not m:
        return 0
    n = float(m.group(1).replace(',', '')) * (1000 if m.group(2) else 1)
    return int(n / 2) if str(v).strip().startswith('<') else int(n)


def fit_title(t, brand):
    suf = f' | {brand}'
    return t[:-len(suf)] if len(t) > 65 and t.endswith(suf) else t


def fit_desc(d, n=160):
    d = re.sub(r'\s+', ' ', d).strip()
    while len(d) > n:
        cut = max(d.rfind('. ', 0, n), d.rfind(', ', 0, n))
        if cut < 60:
            return d[:n - 1].rsplit(' ', 1)[0] + '…'
        d = d[:cut].rstrip(',') + '.'
    return d


def join_words(xs):
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ', '.join(xs[:-1]) + ' and ' + xs[-1] if xs else ''


# ---------------------------------------------------------------- what a jersey is (from its name)
def season_of(n):
    m = re.search(r'\b((?:19|20)\d\d)\s*[-/]\s*((?:19|20)?\d\d)\b', n)
    if m:
        a, b = m.group(1), m.group(2)[-2:]
        return f'{a}-{b}'
    m = re.search(r'(?<![\d/])(\d\d)\s*/\s*(\d\d)(?![\d/])', n)
    if m and int(m.group(2)) == (int(m.group(1)) + 1) % 100:
        return f'20{m.group(1)}-{m.group(2)}' if int(m.group(1)) < 50 else f'19{m.group(1)}-{m.group(2)}'
    m = re.search(r'\b((?:19|20)\d\d)\b', n)
    return m.group(1) if m else ''


def facets(p):
    n = str(p.get('name', ''))
    f = {'name': n}
    f['clubs'] = [c for c, rx in CLUBS if re.search(rx, n, I)]
    f['players'] = [x for x, rx in PLAYERS if re.search(rx, n, I)]
    f['types'] = {k for k in TYPES if k != 'national' and is_type(k, n)}
    f['season'] = season_of(n)
    f['years'] = re.findall(r'\b(?:19|20)\d\d\b', n)
    f['kit'] = next((k for k, rx in KIT_RX if re.search(rx, n, I)), '')
    f['sleeve'] = ('Five Sleeve' if 'fivesleeve' in f['types'] else 'Full Sleeve' if 'fullsleeve' in f['types']
                   else 'Sleeveless' if re.search(r'sleeveless', n, I) else 'Half Sleeve')
    f['finish'] = 'Embroidery' if 'embroidery' in f['types'] else 'Sublimation' if 'sublimation' in f['types'] else ''
    f['collar'] = 'polo' in f['types']
    f['colors'] = [c for c in COLORS if re.search(r'\b' + c + r'\b', n, I)]
    f['price'] = float(p.get('price') or 0)
    return f


LEFTOVER_DROP = (r'\b(jersey|jerseys|kit|kits|half|five|full|long|sleeves?|fivesleeve|embroidery|embrodery|embroidered|sublimation|'
                 r'collar|coller|polo|round|neck|premium|quality|version|player|edition|issue|imported|master|retro|home|away|third|'
                 r'special|training|new|season|v\d+|with|and|the|of|fan|men|mens|loose|fit|regular|shirt|tshirt|t-shirt|football|soccer|'
                 r'world ?cup|wc|anniversary|classic|limited|concept|zip|dot|net)\b')


def seo_name(p, f=None):
    """'BELLINGHAM REAL MADRID 2026-27 HOME KIT EMBROIDERY HALF SLEEVE JERSEY' → 'Bellingham Real Madrid 2026-27 Home Jersey'.
    The order people type into Google: player, club, season, kit, 'Jersey'."""
    f = f or facets(p)
    n = f['name']
    if not f['clubs'] and not f['players']:
        nm = tcase(re.sub(r'\bV\d+\b', '', n, flags=I)).strip()
        return nm if re.search(r'jersey|kit|shirt', nm, I) else nm + ' Jersey'
    # words of the name that are not club / player / season / kit / sleeve — "Zoro Edition", "Last Match", "Dragon"
    rest = n
    for c, rx in CLUBS:
        rest = re.sub(rx, ' ', rest, flags=I)
    for x, rx in PLAYERS:
        rest = re.sub(rx, ' ', rest, flags=I)
    rest = re.sub(r'\b(?:19|20)?\d\d\s*[-/]\s*(?:19|20)?\d\d\b|\b(?:19|20)\d\d\b', ' ', rest)
    rest = re.sub(LEFTOVER_DROP, ' ', rest, flags=I)
    extra = [w for w in rest.split() if len(w) > 1 and not re.match(r'^[\W\d]+$', w)][:3]
    if re.search(r'\bedition\b', n, I) and extra:
        extra.append('Edition')
    parts = []
    if f['players']:
        parts.append(f['players'][0])
    if f['clubs']:
        parts.append(f['clubs'][0])
    if f['season']:
        parts.append(f['season'])
    elif 'newseason' in f['types']:
        parts.append('2026-27')
    if extra:
        parts.append(tcase(' '.join(extra)))
    kit = f['kit']
    if kit:
        parts.append(kit.title())
    if 'retro' in f['types'] and not (f['season'] and f['season'] >= '2020'):
        parts.append('Retro')
    parts.append('Jersey')
    nm = ' '.join(parts)
    if 'player' in f['types']:
        nm += ' (Player Version)'
    return re.sub(r'\s+', ' ', nm).strip()


def product_specs(p, f, buy2):
    sizes = [s.get('size') for s in (p.get('sizes') or []) if s.get('in_stock')]
    details = [d for d in (p.get('details') or [])[:12] if not is_note(d) and not hide_detail(d)]
    fabric = next((d for d in details if re.search(r'fabric|dot ?net|polyester|dri|mesh|material', str(d), I)), '')
    fit = next((d for d in details if re.search(r'\bfit\b', str(d), I)), '')
    rows = []
    if f['clubs']:
        rows.append(('Team', f['clubs'][0]))
    if f['players']:
        rows.append(('Player', f['players'][0]))
    if f['season']:
        rows.append(('Season', f['season']))
    if f['kit']:
        rows.append(('Kit', f['kit'].title()))
    rows.append(('Sleeve', f['sleeve']))
    rows.append(('Neck', 'Collar (polo)' if f['collar'] else 'Round neck'))
    if f['finish']:
        rows.append(('Badge & logos', 'Embroidered (stitched)' if f['finish'] == 'Embroidery' else 'Sublimation (printed into the fabric)'))
    rows.append(('Version', 'Player version' if 'player' in f['types'] else 'Retro' if 'retro' in f['types'] else 'Fan version'))
    if fit:
        rows.append(('Fit', str(fit)))
    if fabric:
        rows.append(('Fabric', str(fabric)))
    rows.append(('Sizes in stock', ', '.join(sizes) if sizes else 'Out of stock right now'))
    rows.append(('Delivery', 'Free shipping in India · dispatched in 24-48 hours'))
    if buy2:
        rows.append(('Offer', f'Buy any 2 jerseys, get ₹{buy2} off'))
    return rows


def about_text(p, f, name, buy2):
    bits = [{'Embroidery': 'embroidered', 'Sublimation': 'sublimation-printed'}.get(f['finish'], ''), f['sleeve'].lower(), 'collar' if f['collar'] else '']
    desc = ' '.join(b for b in bits if b)
    kind = 'player version' if 'player' in f['types'] else 'retro' if 'retro' in f['types'] else ''
    s1 = f'The {name} is {"an" if desc[:1] in "aeiou" and desc else "a"} {desc} {kind + " " if kind else ""}football jersey'
    if f['clubs'] and f['kit']:
        s1 += f' in {f["clubs"][0]}\'s {f["kit"]} colours'
    s1 += '.'
    s2 = ''
    if f['finish'] == 'Embroidery':
        s2 = ' The club badge and logos are stitched on, so they stay sharp wash after wash.'
    elif f['finish'] == 'Sublimation':
        s2 = ' The design is printed into the fabric, so it stays light and the colours do not crack or peel.'
    if f['collar']:
        s2 += ' The collar gives it a smart look for everyday wear.'
    if f['sleeve'] == 'Five Sleeve':
        s2 += ' Five sleeve means longer, looser sleeves that reach the elbow and a relaxed fit.'
    sizes = [s.get('size') for s in (p.get('sizes') or []) if s.get('in_stock')]
    s3 = (f' Sizes in stock right now: {", ".join(sizes)} — our jerseys run slightly fitted, so pick one size up from your usual.' if sizes
          else ' It is out of stock right now — check back soon.')
    s4 = ' Free shipping anywhere in India, dispatched within 24-48 hours with a tracking ID.'
    if buy2:
        s4 += f' Buy any 2 jerseys and ₹{buy2} comes off automatically.'
    return (s1 + s2 + s3 + s4).strip()


# ---------------------------------------------------------------- keywords → rules → pages
def kw_rule(k, buy2):
    s = ' ' + str(k).lower() + ' '
    # "india" is the market, not the team — unless the search is about India's own jersey
    if not re.match(r'^\s*(india|indian)\b', s.strip()):
        s = re.sub(r'\b(in )?india\b', ' ', s)
    r = {}
    for c, rx in CLUBS:
        if re.search(rx, s, I):
            r['club'] = c
            break
    if re.search(r'\bfc barcelona\b|\bbarca\b', s):
        r['club'] = 'Barcelona'
    for x, rx in PLAYERS:
        if re.search(rx, s, I):
            r['player'] = x
            break
    t = []
    for key, rx in (('fullsleeve', r'full ?sleeve|long ?sleeve'), ('fivesleeve', r'five ?sleeve'), ('retro', r'\bretro\b|\bvintage\b'),
                    ('embroidery', r'embroid'), ('sublimation', r'sublimat'), ('player', r'player version'), ('polo', r'\bcollar\b|\bpolo\b')):
        if re.search(rx, s):
            t.append(key)
    if t:
        r['types'] = t
    if re.search(r'\b2026\b|\b26 ?[/-] ?27\b|\bnew season\b', s):
        r['newseason'] = True
    yr = [y for y in re.findall(r'\b(?:19|20)\d\d\b', s) if y != '2026']
    if yr:
        r['year'] = yr[0]
        r.pop('newseason', None)
    for c in COLORS:
        if re.search(r'\b' + c + r'\b', s):
            r['color'] = c
    for k2, rx in KIT_RX[:3]:
        if re.search(rx, s):
            r['kit'] = k2
    m = re.search(r'\bunder (?:₹|rs\.? ?)?(\d+)', s)
    if m:
        lim = int(m.group(1))
        r['max'] = lim + buy2 / 2 - 0.01 if buy2 else lim     # under ₹500 = under ₹500 each when you buy 2
    return r


def rule_key(r):
    return json.dumps({k: r[k] for k in sorted(r)}, sort_keys=True)


def match(r, f):
    if r.get('club') and r['club'] not in f['clubs']:
        return False
    if r.get('player') and r['player'] not in f['players']:
        return False
    for t in r.get('types', []):
        if t not in f['types']:
            return False
    if r.get('newseason') and 'newseason' not in f['types']:
        return False
    if r.get('year') and r['year'] not in f['years'] and not f['season'].startswith(r['year']):
        return False
    if r.get('color') and r['color'] not in f['colors']:
        return False
    if r.get('kit') and r['kit'] != f['kit']:
        return False
    if r.get('max') and not (0 < f['price'] <= float(r['max'])):
        return False
    if r.get('min') and f['price'] < float(r['min']):
        return False
    q = (r.get('q') or '').strip().lower()
    if q:
        n = f['name'].lower()
        if not any(all(w in n for w in a.split()) for a in [x.strip() for x in q.split(',') if x.strip()]):
            return False
    if r.get('ids'):
        return False   # handled by the caller
    return True


def sort_for_page(prods):
    return sorted(prods, key=lambda p: (not p.get('in_stock', True), -(p.get('sold_30d') or 0)))


# ---------------------------------------------------------------- the page list
class Page(dict):
    pass


def build_pages(feed, F, S, buy2, overrides, say):
    auto = S.get('auto') or {}
    min_page, min_combo, kw_min = int(auto.get('min_page', 2)), int(auto.get('min_combo', 4)), int(auto.get('keyword_page_min', 3))
    pages = {}

    def prods_for(r):
        if r.get('ids'):
            ids = [str(x) for x in r['ids']]
            by = {str(p['id']): p for p in feed}
            return [by[i] for i in ids if i in by]
        return [p for p in feed if match(r, F[str(p['id'])])]

    def add(slug, kind, kw, h1, r, prods=None, **x):
        prods = prods if prods is not None else prods_for(r)
        if not prods or slug in pages:
            return pages.get(slug)
        pg = Page(slug=slug, kind=kind, kw=kw, h1=h1, rule=r, prods=prods, kws=[], **x)
        pages[slug] = pg
        return pg

    add('football-jerseys-india', 'hub', 'Football Jersey', 'Football Jerseys in India', {}, list(feed))
    add('football-jerseys-under-500', 'price', 'Football Jersey Under ₹500', 'Football Jerseys Under ₹500 (Buy 2)',
        {'max': 500 + buy2 / 2 - 0.01 if buy2 else 499}, buy2note=bool(buy2))
    add('football-jerseys-under-600', 'price', 'Football Jersey Under ₹600', 'Football Jerseys Under ₹600', {'max': 600})
    if auto.get('types', True):
        for key, (rx, slug, kw, h1) in TYPES.items():
            r = {'types': [key]} if key != 'national' else {'q': 'argentina, brazil, portugal, france, germany, spain, england, italy, netherlands, croatia, belgium, japan, uruguay, mexico, colombia, world cup'}
            if key == 'newseason':
                r = {'newseason': True}
            prods = [p for p in feed if is_type(key, p['name'])]
            if len(prods) >= min_page:
                add(slug, 'type', kw, h1, r, prods, type=key)
    for club, rx in CLUBS:
        if not auto.get('clubs', True):
            break
        r = {'club': club}
        prods = prods_for(r)
        if len(prods) < min_page:
            continue
        add(f'{slugify(club)}-jersey', 'club', f'{club} Jersey', f'{club} Jerseys', r, prods, club=club)
        if auto.get('combos', True):
            for key, part, label in COMBO_TYPES:
                r2 = dict(r, **({'newseason': True} if key == 'newseason' else {'types': [key]}))
                sub = prods_for(r2)
                if min_combo <= len(sub) < len(prods):
                    add(f'{slugify(club)}-{part}-jersey', 'combo', f'{club} {label} Jersey', f'{club} {label} Jerseys', r2, sub, club=club, type=key)
    if auto.get('players', True):
        for player, rx in PLAYERS:
            r = {'player': player}
            prods = prods_for(r)
            if len(prods) >= min_page:
                add(f'{slugify(player)}-jersey', 'player', f'{player} Jersey', f'{player} Jerseys', r, prods, player=player)

    for s_ in (S.get('boost') or []):     # pages the settings file pushes (the dashboard can switch each off)
        if s_ in pages:
            pages[s_]['boost'] = True
    # dashboard: your own keyword pages + edits to automatic pages
    for o in overrides:
        if (o.get('kind') or 'collection') != 'collection':
            continue
        slug = slugify(o.get('slug') or '')
        rule = o.get('rule') or {}
        if isinstance(rule, str):
            try:
                rule = json.loads(rule)
            except Exception:
                rule = {}
        pg = pages.get(slug)
        if pg is None and slug:
            h = (o.get('h1') or slug.replace('-', ' ').title()).strip()
            prods = prods_for(rule) if rule else []
            pg = add(slug, 'custom', re.sub(r'(?i)jerseys\b', 'Jersey', h), h, rule, prods) if prods else None
        elif pg is not None and rule:
            prods = prods_for(rule)
            if prods:
                pg['prods'], pg['rule'] = prods, rule
        if pg is None:
            continue
        pg['edited'] = True
        pg['active'] = o.get('active') is not False
        if o.get('boost') is not None:
            pg['boost'] = bool(o.get('boost'))
        for k in ('title', 'description', 'h1', 'intro'):
            if (o.get(k) or '').strip():
                pg['o_' + k] = o[k].strip()

    # the keyword map: every researched keyword finds its page (or gets one)
    by_rule = {}
    for pg in pages.values():
        by_rule.setdefault(rule_key(pg['rule']), pg)
    kws = [dict(k) for k in (S.get('keywords') or [])]
    for o in overrides:
        if o.get('kind') == 'keyword' and (o.get('h1') or '').strip():
            r = o.get('rule') or {}
            kws.append({'kw': o['h1'].strip().lower(), 'vol': r.get('vol', ''), 'kd': r.get('kd', ''), 'page': r.get('page', ''), 'mine': True})
    kw_out = []
    for k in kws:
        text = str(k.get('kw') or '').strip().lower()
        if not text:
            continue
        rec = {'kw': text, 'vol': k.get('vol', ''), 'n': vol_num(k.get('vol')), 'kd': k.get('kd', ''), 'note': k.get('note', ''), 'mine': bool(k.get('mine'))}
        target = (k.get('page') or '').strip()
        if target.startswith('guide/'):
            rec.update(page=target, how='guide')
            kw_out.append(rec)
            continue
        r = kw_rule(text, buy2)
        m = prods_for(r)
        rec['matches'] = len(m)
        pg = pages.get(target) if target else None
        if pg is None and target and m:
            pg = add(target, 'keyword', tcase(text), tcase(re.sub(r'\bjersey\b', 'jerseys', text)), r, m)
        if pg is None:
            pg = by_rule.get(rule_key(r))
        if pg is None and m and len(m) >= kw_min and rec['n'] >= 1000:
            pg = add(slugify(text), 'keyword', tcase(text), tcase(re.sub(r'\bjersey\b', 'jerseys', text)), r, m)
            if pg:
                by_rule[rule_key(r)] = pg
        how = 'page'
        if pg is None and m:
            parent = (pages.get(f'{slugify(r["club"])}-jersey') if r.get('club') else None) or \
                     (pages.get(f'{slugify(r["player"])}-jersey') if r.get('player') else None) or \
                     next((pages[s] for key, (rx, s, kw0, h0) in TYPES.items() for t in r.get('types', []) if key == t and s in pages), None)
            if parent is not None:
                pg, how = parent, 'section'
        if pg is None:
            rec.update(page='', how='no jerseys for it yet' if not m else 'no page')
            kw_out.append(rec)
            continue
        same = rule_key(r) == rule_key(pg['rule']) or (target == pg['slug'] and how == 'page')
        pg['kws'].append(dict(rec, rule=r, same=same))
        rec.update(page=pg['slug'], how='main keyword' if same else how if how == 'section' else 'section')
        kw_out.append(rec)
    return {s: p for s, p in pages.items() if p.get('active', True)}, kw_out


def page_text(pg, F, buy2, brand):
    """Title, description, intro, sections and FAQ for one collection page."""
    prods = sort_for_page(pg['prods'])
    n = len(prods)
    prices = sorted(F[str(p['id'])]['price'] for p in prods if F[str(p['id'])]['price'] > 0) or [0]
    lo, hi, mid = prices[0], prices[-1], statistics.median(prices)
    mains = sorted([k for k in pg['kws'] if k.get('same')], key=lambda k: -k['n'])
    primary = tcase(mains[0]['kw']) if mains else pg['kw']
    primary = re.sub(r'\bJerseys\b', 'Jersey', primary)
    pg['primary'] = primary
    plural = re.sub(r'\bJersey\b', 'Jerseys', primary, count=1) if 'Jerseys' not in primary else primary
    proper = pg['kind'] in ('club', 'player', 'combo', 'custom', 'keyword')
    low = (lambda t: re.sub(r'\b(Jerseys?|Retro|Full Sleeve|Five Sleeve|Embroidery|Player Version|Football|Black|White|Away|Home)\b',
                            lambda m: m.group(0).lower(), t)) if proper else str.lower
    pl = low(plural)
    fs = [F[str(p['id'])] for p in prods]
    kits = [k for k in ('home', 'away', 'third', 'special edition') if any(f['kit'] == k for f in fs)]
    sleeves = [s for s in ('Half Sleeve', 'Five Sleeve', 'Full Sleeve') if any(f['sleeve'] == s for f in fs)]
    retro = sorted({f['season'] for f in fs if 'retro' in f['types'] and f['season']})
    new = any('newseason' in f['types'] for f in fs)

    # sections: the page's other keywords first ("Real Madrid jersey full sleeve", "Ronaldo Real Madrid jersey" …)
    secs, used = [], set()
    cands = sorted([k for k in pg['kws'] if not k.get('same')], key=lambda k: -k['n'])
    auto_secs = []
    if pg['kind'] in ('club', 'player', 'keyword', 'custom') and n >= 6:
        base = pg['rule']
        for key, part, label in [('newseason', '', '2026-27'), ('retro', '', 'Retro'), ('fullsleeve', '', 'Full Sleeve'),
                                 ('fivesleeve', '', 'Five Sleeve'), ('player', '', 'Player Version'), ('embroidery', '', 'Embroidery')]:
            r = dict(base, **({'newseason': True} if key == 'newseason' else {'types': list(base.get('types', [])) + [key]}))
            auto_secs.append({'kw': f'{pg["kw"].replace(" Jersey", "")} {label} jersey', 'rule': r, 'n': 0})
    for k in cands + auto_secs:
        ids = [str(p['id']) for p in prods if str(p['id']) not in used and match(k['rule'], F[str(p['id'])])]
        if len(ids) < (1 if k in cands else 2) or len(ids) == n or len(secs) >= 6:
            continue
        h2 = tcase(re.sub(r'\bjersey\b', 'jerseys', k['kw']))
        if any(s['h2'].lower() == h2.lower() for s in secs):
            continue
        used.update(ids)
        secs.append({'h2': h2, 'id': slugify(k['kw']), 'ids': ids, 'kw': k['kw']})
    rest = [str(p['id']) for p in prods if str(p['id']) not in used]
    if secs and rest:
        secs.append({'h2': f'More {pg["h1"]}', 'id': 'more', 'ids': rest, 'kw': ''})
    if not secs:
        secs = [{'h2': '', 'id': 'all', 'ids': [str(p['id']) for p in prods], 'kw': ''}]
    pg['sections'] = secs

    rng = f'from {money(lo)}' + (f' to {money(hi)}' if hi > lo else '')
    others = [low(tcase(s['kw'])) for s in secs if s['kw']][:3]
    if pg['slug'] == 'football-jerseys-under-500':
        each = money((2 * lo - buy2) / 2)
        title = f'Football Jerseys Under ₹500 – {each} Each When You Buy 2 | {brand}'
        desc = (f'Football jerseys under ₹500 in India: add any 2 to the cart and ₹{buy2} comes off by itself — 2 × {money(lo)} = '
                f'{money(2 * lo - buy2)}, {each} each. {n} designs, free shipping, dispatch in 24-48 hours.')
        intro = (f'Every jersey here costs under ₹500 each when you buy any two: add 2 to your cart and ₹{buy2} comes off automatically, '
                 f'no code needed — 2 × {money(lo)} = {money(2 * lo - buy2)}, which is {each} per jersey. {n} designs to mix and match: '
                 f'clubs, countries, retro classics and new-season kits in {join_words([s.lower() for s in sleeves])}. '
                 'Free shipping to every pin code in India and dispatch within 24-48 hours.')
    else:
        title = (f'{primary} – {n} Designs {rng.split(" to ")[0]}, Free Shipping | {brand}' if pg['kind'] != 'hub'
                 else f'Football Jerseys India – Retro, Club & 2026-27 Kits {rng.split(" to ")[0]} | {brand}')
        what = []
        if kits:
            what.append(f'{join_words(kits)} kits' + (' from the 2026-27 season' if new else ''))
        if retro and pg.get('type') != 'newseason':
            what.append('retro classics from ' + join_words(retro[:3]))
        desc = (f'Buy {pl if pg["kind"] != "hub" else "football jerseys"} online in India: {n} designs {rng}'
                + (f' — {join_words(what)}' if what else '') + f'. Free shipping, dispatch in 24-48 hrs'
                + (f', buy any 2 get ₹{buy2} off.' if buy2 else '.'))
        intro = f'Shop {n} {pl if pg["kind"] != "hub" else "football jerseys"} online in India, priced {rng}.'
        if what:
            intro += f' The range covers {join_words(what)}' + (f', in {join_words([s.lower() for s in sleeves])}' if sleeves else '') + '.'
        if others:
            intro += f' Looking for {"an" if others[0][:1].lower() in "aeiou" else "a"} {join_words(others).replace(" and ", " or ")}? Each has its own section below.'
        intro += ' Every kit is checked before it is packed, ships free anywhere in India and is dispatched within 24-48 hours.'
        if buy2:
            intro += f' Buy any 2 and ₹{buy2} comes off automatically.'
    kwl = low(primary)
    q1 = f'How much do football jerseys cost at {brand}?' if pg['kind'] in ('price', 'hub') else \
        f'What is the price of {"an" if kwl[:1].lower() in "aeiou" else "a"} {kwl} at {brand}?'
    a1 = (f'Every jersey on this page costs {money(lo)}–{money(hi)}. Add any 2 to your cart and ₹{buy2} comes off automatically, so each one works out under ₹500 — two {money(lo)} jerseys cost {money(2 * lo - buy2)}.'
          if pg['slug'] == 'football-jerseys-under-500' and buy2 else None)
    faq = [(q1, a1) if a1 else (q1, f'{pl[:1].upper() + pl[1:]} at {brand} start at {money(lo)}' + (f', and most cost about {money(mid)}' if mid > lo else '') + '.'
            + (f' Buy any 2 and ₹{buy2} comes off automatically, so two cost {money(2 * lo - buy2)}.' if buy2 else '')),
           (f'Do you deliver {pl} all over India?',
            'Yes. Shipping is free to every pin code in India. Orders are dispatched within 24-48 hours and usually arrive in 5-6 days, with a tracking ID by SMS and email.'),
           ('Which size should I order?',
            'Our jerseys run slightly fitted, so order one size larger than your usual — normally M, choose L. Sizes go from XS to 3XL; see the size chart guide for chest measurements.')]
    pg['title'] = pg.get('o_title') or fit_title(title, brand)
    pg['description'] = pg.get('o_description') or fit_desc(desc)
    pg['h1'] = pg.get('o_h1') or pg['h1']
    pg['intro'] = pg.get('o_intro') or intro
    pg['faq'] = faq
    pg['min'] = lo
    return pg


GUIDE_FOR = {'fivesleeve': 'what-is-a-five-sleeve-jersey', 'player': 'player-version-vs-fan-version-jersey',
             'embroidery': 'embroidery-vs-sublimation-jersey', 'sublimation': 'embroidery-vs-sublimation-jersey', 'retro': 'best-retro-football-jerseys'}


def related_pages(pg, pages):
    out = []
    if pg.get('club'):
        out += [x for x in pages.values() if x.get('club') == pg['club'] and x is not pg]
        out += [x for x in pages.values() if x['kind'] in ('player', 'keyword') and any(pg['club'] in F_CACHE.get(str(p['id']), {}).get('clubs', []) for p in x['prods'][:3])][:4]
    if pg.get('player'):
        ids = {str(p['id']) for p in pg['prods']}
        out += [x for x in pages.values() if x['kind'] == 'club' and ids & {str(p['id']) for p in x['prods']}]
    if pg.get('type'):
        out += [x for x in pages.values() if x['kind'] == 'combo' and x.get('type') == pg['type']][:6]
    out += [x for x in pages.values() if x.get('boost')]
    out += sorted([x for x in pages.values() if x['kind'] == 'club'], key=lambda x: -len(x['prods']))[:8]
    out += sorted([x for x in pages.values() if x['kind'] == 'player'], key=lambda x: -len(x['prods']))[:6]
    seen, res = {pg['slug']}, []
    for x in out:
        if x['slug'] not in seen:
            seen.add(x['slug'])
            res.append(x)
    return res[:18]


F_CACHE = {}


# ---------------------------------------------------------------- guides (markdown-lite)
def render_guide(g, pages, feed, F, kw_links, guides_by):
    """## heading, - bullet, **bold**, [[link slug|text]], [[table]]..[[/table]], [[products type=retro n=6]].
    The first mention of any researched keyword links to its page (internal links)."""
    used, out, grids = set(), [], {}
    lines = (g.get('body') or '').replace('\r', '').split('\n')
    i = 0

    def inline(t):
        t = e(t)
        t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)

        def lk(m):
            target, text = m.group(1).strip(), (m.group(2) or '').strip()
            if target.startswith('guide/'):
                gs = target[6:]
                text = text or (guides_by.get(gs, {}).get('h1') or gs)
                used.add(target)
                return f'<a href="/guide/{e(gs)}">{text}</a>'
            pg = pages.get(target)
            if not pg:
                return text or target
            used.add(target)
            return f'<a href="/c/{e(target)}">{text or e(pg["h1"])}</a>'
        t = re.sub(r'\[\[link ([^|\]]+)(?:\|([^\]]+))?\]\]', lk, t)
        # automatic internal links: the first mention of a keyword that has a page
        for phrase, href in kw_links:
            if href in used or href.endswith('/' + g['slug']):
                continue
            m = re.search(r'(?<![\w>/-])(' + re.escape(phrase) + r')(?![\w<])', t, I)
            if m and not re.search(r'<a [^>]*>[^<]*$', t[:m.start()]):
                t = t[:m.start()] + f'<a href="{e(href)}">{m.group(1)}</a>' + t[m.end():]
                used.add(href)
        return t
    para = []

    def flush():
        if para:
            out.append('<p>' + inline(' '.join(para)) + '</p>')
            para.clear()
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            flush()
        elif ln.startswith('## '):
            flush()
            out.append(f'<h2 id="{slugify(ln[3:])}">{inline(ln[3:])}</h2>')
        elif ln.startswith('- '):
            flush()
            items = []
            while i < len(lines) and lines[i].startswith('- '):
                items.append('<li>' + inline(lines[i][2:]) + '</li>')
                i += 1
            out.append('<ul>' + ''.join(items) + '</ul>')
            continue
        elif ln.strip() == '[[table]]':
            flush()
            rows = []
            i += 1
            while i < len(lines) and lines[i].strip() != '[[/table]]':
                if lines[i].strip():
                    rows.append([c.strip() for c in lines[i].split('|')])
                i += 1
            if rows:
                out.append('<div class="seo-tbl"><table><thead><tr>' + ''.join(f'<th>{e(c)}</th>' for c in rows[0]) + '</tr></thead><tbody>'
                           + ''.join('<tr>' + ''.join(f'<td>{e(c)}</td>' for c in r) + '</tr>' for r in rows[1:]) + '</tbody></table></div>')
        elif re.match(r'^\[\[products\b', ln.strip()):
            flush()
            args = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', ln.strip()[10:-2]))
            args = {k: v.strip('"') for k, v in args.items()}
            r = {}
            if args.get('type') in TYPES:
                r = {'newseason': True} if args['type'] == 'newseason' else {'types': [args['type']]}
            if args.get('q'):
                r['q'] = args['q']
            if args.get('club'):
                r['club'] = args['club']
            if args.get('max'):
                r['max'] = float(args['max'])
            ps = sort_for_page([p for p in feed if match(r, F[str(p['id'])])] if r else list(feed))
            ids = [str(p['id']) for p in ps if p.get('in_stock', True)][:int(args.get('n', 6) or 6)]
            key = f'g{len(grids)}'
            grids[key] = ids
            out.append(f'<div class="seo-pgrid grid" data-grid="{key}"></div>')
        else:
            para.append(ln.strip())
        i += 1
    flush()
    return '\n'.join(out), grids


# ---------------------------------------------------------------- html pieces
CHIP = ('display:inline-block;margin:0 6px 8px 0;padding:7px 12px;border:1px solid var(--line);border-radius:99px;'
        'font-size:12.5px;font-weight:700;color:var(--ink);text-decoration:none')


def chip_links(items):
    return ''.join(f'<a href="{e(h)}" style="{CHIP}">{e(a)}</a>' for a, h in items)


def card(p, img, alt, note=''):
    price = float(p.get('price') or 0)
    return (f'<a class="card" href="/jersey/{e(p.get("slug") or p["id"])}" data-id="{e(p["id"])}">'
            f'<img loading="lazy" src="{e(img)}" alt="{e(alt)}" width="400" height="460" decoding="async">'
            f'<div class="b"><div class="nm">{e(p["name"])}</div><div class="pr">{money(price)}'
            f'<span style="font-size:11px;color:var(--mut);text-decoration:line-through;font-weight:600;margin-left:5px">{money(round(price * 1.6))}</span>'
            f'<span style="font-size:10px;font-weight:800;color:var(--red);margin-left:4px">38% OFF</span></div>{note}</div></a>')


def ld(obj):
    return '<script type="application/ld+json" data-seo="1">' + json.dumps(obj, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + '</script>'


def head_block(title, desc, url, image, brand, extra_ld=(), og_type='website'):
    t = (f'<title>{e(title)}</title>\n<meta name="description" content="{e(desc)}">\n<link rel="canonical" href="{e(url)}">\n'
         f'<meta property="og:site_name" content="{e(brand)}"><meta property="og:type" content="{og_type}">'
         f'<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">'
         f'<meta property="og:url" content="{e(url)}"><meta property="og:locale" content="en_IN">'
         + (f'<meta property="og:image" content="{e(image)}"><meta name="twitter:image" content="{e(image)}">' if image else '')
         + f'<meta name="twitter:card" content="summary_large_image">\n'
         '<style>html.lp #lskel{display:none!important}</style><noscript><style>#home{display:none}</style></noscript>\n')
    return t + '\n'.join(ld(x) for x in extra_ld)


def make_page(tpl, head, body, foot_html):
    out = tpl
    a, b = out.find('<!--seo:head-->'), out.find('<!--/seo:head-->')
    if a != -1 and b > a:
        out = out[:a] + '<!--seo:head-->\n' + head + '\n' + out[b:]
    else:
        out = re.sub(r'<meta name="description"[^>]*>\s*', '', out, count=1)
        out = re.sub(r'<title>.*?</title>', lambda m: head, out, count=1, flags=re.S)
    if foot_html:
        out = out.replace('<div class="ftrust">', foot_html + '\n    <div class="ftrust">', 1)
    out = re.sub(r'<div id="pView"[^>]*>\s*</div>', lambda m: '<div id="pView" style="display:block">' + body + '</div>', out, count=1)
    return out


def foot_block(links, guides):
    """Popular searches + guides in the footer: links Google follows from every page."""
    return ('<div class="fpop" id="seoFoot"><div class="fh">Popular searches</div><div class="fpop-l">'
            + ''.join(f'<a href="{e(h)}">{e(a)}</a>' for a, h in links) + '</div>'
            + (('<div class="fh" style="margin-top:12px">Guides</div><div class="fpop-l">'
                + ''.join(f'<a href="/guide/{e(g["slug"])}">{e(g["h1"])}</a>' for g in guides) + '</div>') if guides else '')
            + '</div>')


# ---------------------------------------------------------------- photos (+ copies with the jersey's name in the file name)
def make_photos(feed, F, photo_map, public, site, names):
    """Per product: [{'lg', 'jpg', 'sm'}] — Cloudflare copies; plus img/<jersey-name>-<n>.jpg/.webp
    (same bytes, a file name Google Images can read). Copies are hard links, so they cost no space."""
    img_dir = os.path.join(site, 'img')
    os.makedirs(img_dir, exist_ok=True)
    keep, out, taken = set(), {}, {}
    for p in feed:
        pid = str(p['id'])
        base = slugify(names[pid])[:70] or pid
        if taken.get(base, pid) != pid:
            base = f'{base}-{pid}'
        taken[base] = pid
        lst = []
        for i, u in enumerate([u for u in (p.get('images') or []) if u and not str(u).startswith('data:')]):
            h = photo_map.get(pid, {}).get(str(i))
            if h:
                b = f'{pid}-{i}-{h}'
                rec = {'lg': f'{public}/p/{b}-lg.webp', 'jpg': f'{public}/p/{b}-m34.jpg', 'sm': f'{public}/p/{b}-sm.webp'}
                for ext, src in (('jpg', f'{b}-m34.jpg'), ('webp', f'{b}-lg.webp')):
                    s_path, nm = os.path.join(site, 'p', src), f'{base}-{i + 1}.{ext}'
                    d_path = os.path.join(img_dir, nm)
                    if os.path.exists(s_path):
                        try:
                            if not os.path.exists(d_path) or os.stat(d_path).st_ino != os.stat(s_path).st_ino:
                                if os.path.exists(d_path):
                                    os.remove(d_path)
                                try:
                                    os.link(s_path, d_path)
                                except OSError:
                                    shutil.copyfile(s_path, d_path)
                            keep.add(nm)
                            rec['n' + ext] = f'{public}/img/{nm}'
                        except OSError:
                            pass
                lst.append(rec)
            elif str(u).startswith('http'):
                lst.append({'lg': u, 'jpg': u, 'sm': u})
        out[pid] = lst
    for f in os.listdir(img_dir):
        if f not in keep:
            try:
                os.remove(os.path.join(img_dir, f))
            except OSError:
                pass
    return out


# ---------------------------------------------------------------- main
UA = {'User-Agent': 'Mozilla/5.0 VisionsJerseySEO/2.0'}
BOOT = ('<!doctype html><meta charset="utf-8"><title>Visions Jersey</title><script>'
        'fetch("/",{cache:"no-store"}).then(function(r){return r.text()}).then(function(h){document.open();document.write(h);document.close()})'
        '.catch(function(){location.replace("/")})</script>')


def fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def load_overrides(supa, key, say):
    if not (supa and key):
        return []
    try:
        req = urllib.request.Request(f'{supa}/rest/v1/shop_seo?select=*', headers={'apikey': key, 'Authorization': f'Bearer {key}', **UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read())
        say(f'SEO: {len(rows)} change(s) from the dashboard')
        return rows if isinstance(rows, list) else []
    except Exception as ex:
        say(f'SEO: dashboard changes not read ({type(ex).__name__}) — using the settings file only')
        return []


def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def build(site, feed, photo_map, public, say=print, supa=None, key=None, tpl=None, settings=None):
    """Returns the number of pages written (0 = nothing changed, the last pages stay online)."""
    def keep_last(msg):
        say(msg)
        nf = os.path.join(site, 'seo', '404.html')
        if not os.path.exists(nf):
            os.makedirs(os.path.dirname(nf), exist_ok=True)
            with open(nf, 'w', encoding='utf-8') as f:
                f.write(BOOT)
        return 0
    try:
        S = settings if settings is not None else json.loads(fetch(SHOP + '/seo/settings.json'))
    except Exception as ex:
        return keep_last(f'SEO: no settings at {SHOP}/seo/settings.json ({type(ex).__name__}) — SEO folder not uploaded? Keeping the last pages')
    if not S.get('on', True):
        return keep_last('SEO: switched off in seo/settings.json ("on": false) — nothing rebuilt')
    brand = S.get('brand') or 'Visions Jersey'
    try:
        tpl = tpl or fetch(SHOP + '/')
    except Exception as ex:
        return keep_last(f'SEO: could not read {SHOP} ({type(ex).__name__}) — keeping the last pages')
    if 'id="pView"' not in tpl or '<title>' not in tpl or len(tpl) < 50000:
        return keep_last('SEO: the shop page did not look right — keeping the last pages')
    buy2 = 100
    try:
        m = re.search(r'BUY2_OFF\s*:\s*(\d+)', fetch(SHOP + '/config.js'))
        if m:
            buy2 = int(m.group(1))
    except Exception:
        pass
    overrides = load_overrides(supa, key, say)
    feed = [p for p in feed if p.get('id') and p.get('name')]
    F = {str(p['id']): facets(p) for p in feed}
    F_CACHE.clear()
    F_CACHE.update(F)
    names = {pid: seo_name(p, F[str(p['id'])]) for p in feed for pid in [str(p['id'])]}
    PH = make_photos(feed, F, photo_map, public, site, names)
    pages, kw_out = build_pages(feed, F, S, buy2, overrides, say)
    for pg in pages.values():
        page_text(pg, F, buy2, brand)
    today = datetime.date.today()
    org = {'@type': 'Organization', '@id': SHOP + '/#org', 'name': brand, 'url': SHOP + '/', 'logo': SHOP + '/logo.png'}
    pov = {slugify(o.get('slug')): o for o in overrides if o.get('kind') == 'product'}

    # guides: from the settings file, plus the ones you write in the dashboard
    guides = {g['slug']: dict(g) for g in (S.get('guides') or []) if g.get('slug')}
    for o in overrides:
        if o.get('kind') == 'guide' and o.get('slug'):
            g = guides.get(o['slug'], {'slug': o['slug']})
            for k_src, k_dst in (('title', 'title'), ('description', 'description'), ('h1', 'h1'), ('intro', 'body')):
                if (o.get(k_src) or '').strip():
                    g[k_dst] = o[k_src]
            g['active'] = o.get('active') is not False
            guides[o['slug']] = g
    guides = {s: g for s, g in guides.items() if g.get('active', True) and g.get('body') and g.get('h1')}
    kw_links = sorted({(k['kw'], ('/' + k['page']) if k['page'].startswith('guide/') else '/c/' + k['page'])
                       for k in kw_out if k.get('page') and len(k['kw'].split()) >= 2 and k['how'] in ('main keyword', 'page', 'guide')},
                      key=lambda x: -len(x[0]))

    def img_of(p, i=0, kind='sm'):
        lst = PH.get(str(p['id'])) or []
        if not lst:
            return ''
        r = lst[min(i, len(lst) - 1)]
        return r.get('nwebp' if kind == 'lg' else 'njpg' if kind == 'jpg' else 'sm') or r[kind if kind != 'jpg' else 'jpg']

    # footer: boosted pages first, then the biggest clubs and players
    foot = [p for p in pages.values() if p.get('boost')] + sorted([p for p in pages.values() if p['kind'] in ('club', 'player', 'keyword')],
                                                                  key=lambda p: -len(p['prods']))
    seen, flinks = set(), []
    for p in foot:
        if p['slug'] not in seen and len(flinks) < 24:
            seen.add(p['slug'])
            flinks.append((p.get('primary') or p['kw'], '/c/' + p['slug']))
    glist = [{'slug': s, 'h1': g['h1']} for s, g in guides.items()]
    fblock = foot_block(flinks, glist)

    tmp = os.path.join(site, 'seo.new')
    shutil.rmtree(tmp, ignore_errors=True)
    for d in ('jersey', 'c', 'guide', 'data/p', 'data/c', 'data/g'):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    urls, plist = [], []

    # ---- jersey pages
    boosted = [p for p in pages.values() if p.get('boost') and p['rule'] and not p['rule'].get('ids')]
    kw_for_products = [k for k in kw_out if k.get('page') and not k['page'].startswith('guide/') and k['page'] in pages]
    by_id = {str(p['id']): p for p in feed}
    n_prod = 0
    for p in feed:
        slug = str(p.get('slug') or '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,200}', slug):
            continue
        pid = str(p['id'])
        f = F[pid]
        nm = names[pid]
        price = f['price']
        ov = pov.get(slug, {})
        sizes = [s.get('size') for s in (p.get('sizes') or []) if s.get('in_stock')]
        in_stock = bool(p.get('in_stock', True)) and (bool(sizes) or not p.get('sizes'))
        sub = ' '.join(x for x in [f['sleeve'], f['finish']] if x)
        title = (ov.get('title') or '').strip() or fit_title(f'{nm} – {sub} | {money(price)} | {brand}', brand)
        desc = (ov.get('description') or '').strip() or fit_desc(
            f'Buy the {nm} online in India for {money(price)}: {sub.lower()}'
            + (f', {"collar" if f["collar"] else "round neck"}' ) + '. '
            + (f'Sizes {", ".join(sizes)} in stock. ' if sizes else '')
            + 'Free shipping, dispatch in 24-48 hrs' + (f', buy any 2 get ₹{buy2} off.' if buy2 else '.'))
        crumb = None
        for cand in ([f'{slugify(f["clubs"][0])}-jersey'] if f['clubs'] else []) + ([f'{slugify(f["players"][0])}-jersey'] if f['players'] else []):
            if cand in pages:
                crumb = pages[cand]
                break
        if crumb is None:
            crumb = next((pages[TYPES[t][1]] for t in ('retro', 'newseason', 'player', 'fivesleeve', 'fullsleeve') if t in f['types'] and TYPES[t][1] in pages), None)
        # "Popular searches" on the product page: researched keywords this jersey answers, linked to their page
        links, seen_h = [], set()
        for bp in boosted:            # pages you push get a link from every jersey they list
            if bp['kind'] != 'hub' and match(bp['rule'], f) and len(links) < 3:
                seen_h.add('/c/' + bp['slug'])
                links.append(((bp.get('primary') or bp['kw']).lower(), '/c/' + bp['slug']))
        for k in sorted(kw_for_products, key=lambda k: -k['n']):
            r = kw_rule(k['kw'], buy2)
            if not r or not match(r, f):
                continue
            href = '/c/' + k['page'] + ('' if k['how'] in ('main keyword', 'page') else '#' + slugify(k['kw']))
            if href in seen_h:
                continue
            seen_h.add(href)
            links.append((k['kw'], href))
            if len(links) >= 8:
                break
        guide = next((GUIDE_FOR[t] for t in ('fivesleeve', 'player', 'embroidery', 'sublimation', 'retro') if t in f['types'] and GUIDE_FOR[t] in guides), None)
        specs = product_specs(p, f, buy2)
        about = about_text(p, f, nm, buy2)
        faq = [(f'Is the {nm} a player version or a fan version?',
                'It is the player version: slim fit, light fabric, cut like the shirt worn on the pitch — size up if you are between sizes.'
                if 'player' in f['types'] else
                'It is a fan version: a regular, comfortable fit made for everyday wear. Order one size larger than your usual T-shirt.'),
               (f'Which sizes are available for the {nm}?',
                (f'In stock right now: {", ".join(sizes)}. ' if sizes else 'It is out of stock right now. ')
                + 'Our jerseys run slightly fitted — between two sizes, choose the bigger one. The size chart guide has chest and length in inches.')]
        pdata = {'id': pid, 'title': title, 'description': desc, 'name': nm, 'specs': specs, 'about': about, 'faq': faq,
                 'crumb': {'slug': crumb['slug'], 'h1': crumb['h1']} if crumb else None,
                 'links': [{'a': a, 'href': h} for a, h in links],
                 'guide': {'slug': guide, 'h1': guides[guide]['h1']} if guide else None,
                 'size_guide': 'football-jersey-size-chart-india' if 'football-jersey-size-chart-india' in guides else None,
                 'alt': nm, 'img': img_of(p, 0, 'jpg')}
        wjson(os.path.join(tmp, 'data', 'p', slug + '.json'), pdata)
        url = f'{SHOP}/jersey/{slug}'
        imgs = [img_of(p, i, 'jpg') for i in range(len(PH.get(pid) or []))][:6]
        prod_ld = {'@context': 'https://schema.org', '@type': 'Product', 'name': nm, 'sku': pid, 'url': url, 'image': imgs,
                   'description': about, 'category': 'Football Jerseys', 'brand': {'@type': 'Brand', 'name': brand},
                   'additionalProperty': [{'@type': 'PropertyValue', 'name': k2, 'value': v2} for k2, v2 in specs[:8]],
                   'offers': {'@type': 'Offer', 'url': url, 'priceCurrency': 'INR', 'price': f'{price:.2f}',
                              'priceValidUntil': (today + datetime.timedelta(days=30)).isoformat(),
                              'availability': 'https://schema.org/' + ('InStock' if in_stock else 'OutOfStock'),
                              'itemCondition': 'https://schema.org/NewCondition', 'seller': org,
                              'shippingDetails': {'@type': 'OfferShippingDetails',
                                                  'shippingRate': {'@type': 'MonetaryAmount', 'value': '0', 'currency': 'INR'},
                                                  'shippingDestination': {'@type': 'DefinedRegion', 'addressCountry': 'IN'},
                                                  'deliveryTime': {'@type': 'ShippingDeliveryTime',
                                                                   'handlingTime': {'@type': 'QuantitativeValue', 'minValue': 1, 'maxValue': 2, 'unitCode': 'DAY'},
                                                                   'transitTime': {'@type': 'QuantitativeValue', 'minValue': 4, 'maxValue': 8, 'unitCode': 'DAY'}}}}}
        crumbs = [{'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': SHOP + '/'}]
        if crumb:
            crumbs.append({'@type': 'ListItem', 'position': 2, 'name': crumb['h1'], 'item': f'{SHOP}/c/{crumb["slug"]}'})
        crumbs.append({'@type': 'ListItem', 'position': len(crumbs) + 1, 'name': nm})
        head = head_block(title, desc, url, img_of(p, 0, 'jpg'), brand,
                          [prod_ld, {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': crumbs}], 'product')
        head += f'<meta property="product:price:amount" content="{price:.2f}"><meta property="product:price:currency" content="INR">'
        rel = [r for r in sort_for_page(crumb['prods'] if crumb else feed) if str(r['id']) != pid][:12]
        body = product_html(p, f, pdata, PH.get(pid) or [], rel, img_of, names, buy2)
        with open(os.path.join(tmp, 'jersey', slug + '.html'), 'w', encoding='utf-8') as fh:
            fh.write(make_page(tpl, head, body, fblock))
        n_prod += 1
        urls.append((url, str(p.get('updated_at') or '')[:10] or today.isoformat(), imgs[:6], 0.8))
        plist.append({'kind': 'product', 'slug': slug, 'id': pid, 'name': p['name'], 'seo_name': nm, 'title': title, 'description': desc,
                      'edited': bool(ov), 'in_stock': in_stock, 'crumb': crumb['slug'] if crumb else '', 'links': len(links)})

    # ---- collection pages
    for pg in pages.values():
        slug = pg['slug']
        url = f'{SHOP}/c/{slug}'
        prods = sort_for_page(pg['prods'])
        rel = related_pages(pg, pages)
        pguides = [g for g in ([GUIDE_FOR.get(pg.get('type'))] + (['football-jersey-size-chart-india'] if pg['kind'] in ('club', 'player') else []))
                   if g and g in guides]
        cdata = {'slug': slug, 'kind': pg['kind'], 'title': pg['title'], 'description': pg['description'], 'h1': pg['h1'], 'intro': pg['intro'],
                 'sections': pg['sections'], 'faq': pg['faq'], 'buy2note': bool(pg.get('buy2note')),
                 'rel': [{'slug': x['slug'], 'a': x.get('primary') or x['kw']} for x in rel],
                 'guides': [{'slug': g, 'h1': guides[g]['h1']} for g in pguides]}
        wjson(os.path.join(tmp, 'data', 'c', slug + '.json'), cdata)
        first = img_of(prods[0], 0, 'jpg') if prods else ''
        item_ld = {'@context': 'https://schema.org', '@type': 'CollectionPage', 'name': pg['h1'], 'url': url, 'description': pg['description'],
                   'isPartOf': {'@type': 'WebSite', 'name': brand, 'url': SHOP + '/'},
                   'mainEntity': {'@type': 'ItemList', 'numberOfItems': len(prods),
                                  'itemListElement': [{'@type': 'ListItem', 'position': i + 1, 'url': f'{SHOP}/jersey/{p.get("slug") or p["id"]}',
                                                       'name': names[str(p['id'])]} for i, p in enumerate(prods[:30])]}}
        crumbs = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': SHOP + '/'}, {'@type': 'ListItem', 'position': 2, 'name': pg['h1']}]}
        head = head_block(pg['title'], pg['description'], url, first, brand, [item_ld, crumbs])
        body = collection_html(cdata, by_id, img_of, names, buy2)
        with open(os.path.join(tmp, 'c', slug + '.html'), 'w', encoding='utf-8') as fh:
            fh.write(make_page(tpl, head, body, fblock))
        lastmod = max([str(p.get('updated_at') or '')[:10] for p in prods] + ['']) or today.isoformat()
        urls.append((url, lastmod, [], 0.9 if pg['kind'] in ('club', 'hub', 'price', 'type', 'player') else 0.7))

    # ---- guides
    guides_by = guides
    for s, g in guides.items():
        html_body, grids = render_guide(g, pages, feed, F, kw_links, guides_by)
        url = f'{SHOP}/guide/{s}'
        rel = [{'slug': x, 'h1': guides[x]['h1']} for x in guides if x != s][:5]
        gdata = {'slug': s, 'title': g.get('title') or g['h1'], 'description': g.get('description') or '', 'h1': g['h1'],
                 'html': html_body, 'grids': grids, 'rel': rel}
        wjson(os.path.join(tmp, 'data', 'g', s + '.json'), gdata)
        art = {'@context': 'https://schema.org', '@type': 'Article', 'headline': g['h1'], 'description': gdata['description'], 'url': url,
               'author': org, 'publisher': org, 'dateModified': today.isoformat(), 'inLanguage': 'en-IN'}
        crumbs = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': SHOP + '/'}, {'@type': 'ListItem', 'position': 2, 'name': g['h1']}]}
        head = head_block(fit_title(gdata['title'], brand), fit_desc(gdata['description']), url, '', brand, [art, crumbs], 'article')
        body = guide_html(gdata, by_id, img_of, names)
        with open(os.path.join(tmp, 'guide', s + '.html'), 'w', encoding='utf-8') as fh:
            fh.write(make_page(tpl, head, body, fblock))
        urls.append((url, today.isoformat(), [], 0.6))

    with open(os.path.join(tmp, '404.html'), 'w', encoding='utf-8') as fh:
        fh.write(tpl)
    home = S.get('home') or {}
    wjson(os.path.join(tmp, 'data', 'index.json'), {'home': home, 'foot': [{'a': a, 'href': h} for a, h in flinks], 'guides': glist,
                                                    'pages': {s: p['h1'] for s, p in pages.items()}})
    kstat = {}
    for k in kw_out:
        kstat[k['kw']] = k
    wjson(os.path.join(tmp, 'data', 'pages.json'), {
        'built': datetime.datetime.utcnow().isoformat(timespec='minutes') + 'Z', 'version': VERSION, 'buy2': buy2,
        'pages': [{'kind': 'collection', 'type': p['kind'], 'slug': p['slug'], 'h1': p['h1'], 'title': p['title'], 'description': p['description'],
                   'intro': p['intro'], 'n': len(p['prods']), 'min': p['min'], 'edited': bool(p.get('edited')), 'boost': bool(p.get('boost')),
                   'primary': p.get('primary'), 'keywords': [k['kw'] for k in p['kws']], 'sections': [s['h2'] for s in p['sections'] if s['h2']]}
                  for p in pages.values()] + plist,
        'keywords': list(kstat.values()),
        'guides': [{'slug': s, 'h1': g['h1'], 'title': g.get('title'), 'description': g.get('description'), 'body': g.get('body'),
                    'keywords': g.get('keywords') or []} for s, g in guides.items()]})

    static = [(SHOP + '/', today.isoformat(), [], 1.0), (SHOP + '/shop', today.isoformat(), [], 0.9),
              (SHOP + '/reviews', today.isoformat(), [], 0.5), (SHOP + '/help', today.isoformat(), [], 0.4)]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">']
    for u, lm, imgs, pr in static + urls:
        sm.append(f'<url><loc>{e(u)}</loc><lastmod>{e(lm)}</lastmod><priority>{pr:.1f}</priority>'
                  + ''.join(f'<image:image><image:loc>{e(i)}</image:loc></image:image>' for i in imgs if i) + '</url>')
    sm.append('</urlset>')
    with open(os.path.join(tmp, 'sitemap.xml'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(sm) + '\n')

    n_feed = google_feed(feed, F, names, PH, img_of, pages, os.path.join(site, 'feeds', 'google-products.xml'), brand)
    final = os.path.join(site, 'seo')
    shutil.rmtree(final, ignore_errors=True)
    os.replace(tmp, final)
    n_kw_page = sum(1 for k in kw_out if k.get('page'))
    say(f'SEO pages: {n_prod} jerseys + {len(pages)} collections + {len(guides)} guides · keywords with a page: {n_kw_page}/{len(kw_out)} '
        f'· sitemap {len(urls) + len(static)} links · Google Shopping feed {n_feed} products')
    return n_prod + len(pages)


def product_html(p, f, d, photos_, rel, img_of, names, buy2):
    lazy, first = 'loading="lazy"', 'fetchpriority="high"'
    gal = ''.join(f'<img src="{e(x.get("nwebp") or x["lg"])}" alt="{e(d["alt"])}{"" if not i else " – photo " + str(i + 1)}" width="700" height="770" '
                  f'{lazy if i else first} decoding="async">' for i, x in enumerate(photos_ or [{'lg': ''}]))
    price = f['price']
    sizes = p.get('sizes') or []
    cr = d.get('crumb')
    return (f'<div style="padding:9px 14px"><a href="/shop" style="font-size:13px;font-weight:700;color:var(--mut);text-decoration:none">← Back to shop</a></div>'
            f'<div class="gal" id="gal">{gal}</div><div class="pinfo" style="padding:0 14px 14px">'
            f'<nav class="crumbs seo-crumb"><a href="/">Home</a> › ' + (f'<a href="/c/{e(cr["slug"])}">{e(cr["h1"])}</a> › ' if cr else '') + f'{e(d["name"])}</nav>'
            f'<div class="cat-lbl">JERSEY</div><h1 style="font-size:19px;font-weight:800;line-height:1.3;margin:4px 0 8px">{e(p["name"])}</h1>'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px"><span style="font-size:25px;font-weight:900">{money(price)}</span>'
            f'<span style="font-size:14px;color:var(--mut);text-decoration:line-through">{money(round(price * 1.6))}</span></div>'
            + (f'<div class="buy2tag">🎁 <b>Buy any 2 jerseys, get ₹{buy2} off</b> — applied automatically at checkout</div>' if buy2 else '')
            + (f'<div style="font-weight:700;font-size:13px;margin:10px 0 7px">Sizes</div><div id="szs">'
               + ''.join(f'<span class="sz {"" if s.get("in_stock") else "out"}">{e(s.get("size"))}</span>' for s in sizes) + '</div>' if sizes else '')
            + '</div>' + seo_product_block(d) + related_block(cr, rel, img_of, names))


def seo_product_block(d):
    """The same block seo.js draws on the live page (keep them in step)."""
    specs = ''.join(f'<tr><th>{e(k)}</th><td>{e(v)}</td></tr>' for k, v in d['specs'])
    faq = ''.join(f'<details class="acc" name="pfaq"><summary>{e(q)}</summary><div>{e(a)}</div></details>' for q, a in d['faq'])
    links = ''.join(f'<a href="{e(l["href"])}" class="seo-chip">{e(l["a"])}</a>' for l in d['links'])
    g = d.get('guide')
    sg = d.get('size_guide')
    return (f'<section class="seo-pblock" id="seoP">'
            f'<h2>About this jersey</h2><p class="seo-about">{e(d["about"])}</p>'
            f'<h2>Jersey details</h2><div class="seo-tbl"><table>{specs}</table></div>'
            + (f'<p class="seo-gl">' + (f'<a href="/guide/{e(sg)}">Size chart</a>' if sg else '')
               + (f' · <a href="/guide/{e(g["slug"])}">{e(g["h1"])}</a>' if g else '') + '</p>' if (g or sg) else '')
            + f'<h2>Questions</h2><div class="accs">{faq}</div>'
            + (f'<h2>Popular searches</h2><div class="seo-links">{links}</div>' if links else '')
            + '</section>')


def related_block(cr, rel, img_of, names):
    if not rel:
        return ''
    return (f'<div class="prelated" style="border-top:1px solid var(--line);margin-top:22px;padding:20px 14px 0">'
            f'<h2 style="font-weight:900;font-size:15px;margin:0 0 12px">More {e(cr["h1"]) if cr else "jerseys"}</h2>'
            f'<div class="grid">{"".join(card(r, img_of(r), names[str(r["id"])]) for r in rel)}</div></div>')


def collection_html(c, by_id, img_of, names, buy2):
    secs = ''
    nav = ''
    if len(c['sections']) > 1:
        nav = '<div class="seo-links seo-jump">' + ''.join(f'<a href="#{e(s["id"])}" class="seo-chip">{e(s["h2"])} ({len(s["ids"])})</a>' for s in c['sections']) + '</div>'
    for s in c['sections']:
        cards = []
        for pid in s['ids'][:60]:
            p = by_id.get(pid)
            if p:
                note = (f'<div class="mut" style="font-size:11px;margin-top:2px">{money((2 * float(p.get("price") or 0) - buy2) / 2)} each in any 2</div>'
                        if c.get('buy2note') and buy2 else '')
                cards.append(card(p, img_of(p), names[pid], note))
        secs += (f'<section class="seo-sec" id="{e(s["id"])}">' + (f'<h2>{e(s["h2"])}</h2>' if s['h2'] else '')
                 + f'<div class="grid">{"".join(cards)}</div></section>')
    faq = ''.join(f'<details class="acc" name="cfaq"><summary>{e(q)}</summary><div>{e(a)}</div></details>' for q, a in c['faq'])
    rel = ''.join(f'<a href="/c/{e(x["slug"])}" class="seo-chip">{e(x["a"])}</a>' for x in c['rel'])
    gl = ''.join(f'<a href="/guide/{e(g["slug"])}" class="seo-chip">{e(g["h1"])}</a>' for g in c['guides'])
    return (f'<div class="wrap seo-c" data-seo="{e(c["slug"])}"><nav class="crumbs seo-crumb"><a href="/">Home</a> › <a href="/shop">All jerseys</a> › {e(c["h1"])}</nav>'
            f'<h1 class="seo-h1">{e(c["h1"])}</h1><p class="mut seo-intro">{e(c["intro"])}</p>{nav}{secs}'
            f'<section class="seo-pblock"><h2>Questions</h2><div class="accs">{faq}</div>'
            + (f'<h2>Guides</h2><div class="seo-links">{gl}</div>' if gl else '')
            + (f'<h2>More to explore</h2><div class="seo-links">{rel}</div>' if rel else '') + '</section></div>')


def guide_html(g, by_id, img_of, names):
    body = g['html']
    for k, ids in g['grids'].items():
        cards = ''.join(card(by_id[i], img_of(by_id[i]), names[i]) for i in ids if i in by_id)
        body = body.replace(f'<div class="seo-pgrid grid" data-grid="{k}"></div>', f'<div class="seo-pgrid grid" data-grid="{k}">{cards}</div>')
    rel = ''.join(f'<a href="/guide/{e(x["slug"])}" class="seo-chip">{e(x["h1"])}</a>' for x in g['rel'])
    return (f'<article class="wrap seo-g" data-seo="{e(g["slug"])}"><nav class="crumbs seo-crumb"><a href="/">Home</a> › Guides › {e(g["h1"])}</nav>'
            f'<h1 class="seo-h1">{e(g["h1"])}</h1><div class="seo-gbody">{body}</div>'
            + (f'<section class="seo-pblock"><h2>More guides</h2><div class="seo-links">{rel}</div></section>' if rel else '') + '</article>')


def google_feed(feed, F, names, PH, img_of, pages, out_path, brand):
    rows = []
    for p in feed:
        pid = str(p['id'])
        lst = PH.get(pid) or []
        if not lst or not F[pid]['price']:
            continue
        f = F[pid]
        title = f'{names[pid]} – {f["sleeve"]}' + (f' {f["finish"]}' if f['finish'] else '')
        ptype = ' > '.join(['Football Jerseys'] + ([f['clubs'][0]] if f['clubs'] else []))
        x = [f'<g:id>{e(pid)}</g:id>', f'<g:title>{e(title[:150])}</g:title>',
             f'<g:description>{e(about_text(p, f, names[pid], 0)[:4900])}</g:description>',
             f'<g:link>{e(SHOP)}/jersey/{e(p.get("slug") or pid)}</g:link>', f'<g:image_link>{e(img_of(p, 0, "jpg"))}</g:image_link>']
        x += [f'<g:additional_image_link>{e(img_of(p, i, "jpg"))}</g:additional_image_link>' for i in range(1, min(len(lst), 10))]
        ok = p.get('in_stock', True) and any(s.get('in_stock') for s in (p.get('sizes') or [{'in_stock': True}]))
        x += [f'<g:availability>{"in_stock" if ok else "out_of_stock"}</g:availability>', f'<g:price>{f["price"]:.2f} INR</g:price>',
              '<g:condition>new</g:condition>', f'<g:brand>{e(brand)}</g:brand>', '<g:identifier_exists>no</g:identifier_exists>',
              '<g:google_product_category>Apparel &amp; Accessories &gt; Clothing &gt; Activewear</g:google_product_category>',
              f'<g:product_type>{e(ptype)}</g:product_type>', '<g:gender>unisex</g:gender>', '<g:age_group>adult</g:age_group>',
              '<g:shipping><g:country>IN</g:country><g:price>0.00 INR</g:price></g:shipping>']
        rows.append('<item>' + ''.join(x) + '</item>')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>'
                 f'<title>{e(brand)}</title><link>{SHOP}</link><description>{e(brand)} — football jerseys, shipped free across India</description>\n'
                 + '\n'.join(rows) + '\n</channel></rss>\n')
    return len(rows)


if __name__ == '__main__':      # local test: python vj_seo.py <feed.json> <spa.html> <settings.json> <out dir>
    import sys
    fd, tp, st, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    build(out, json.load(open(fd)), {}, 'https://vj-images.pages.dev', print, tpl=open(tp, encoding='utf-8').read(),
          settings=json.load(open(st, encoding='utf-8')))
