"""
probe_site.py — looks at how a tracking website fetches its data, so an adapter can be written.
Run from GitHub: Actions → "Probe a tracking site" → Run workflow (site URL + one tracking number).
Prints: forms, scripts, API-looking URLs, captcha signs, and what a few likely result URLs answer.
"""
import re, sys, json, os
import requests
from urllib.parse import urljoin, urlparse

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
S = requests.Session(); S.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})
out = []
def say(x=""): print(x); out.append(x)

def get(url, **kw):
    try:
        r = S.get(url, timeout=30, allow_redirects=True, **kw); return r.status_code, r.text, r.headers.get("content-type", "")
    except Exception as e:  # noqa
        return 0, f"ERR {e}", ""

def main(url, number):
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    code, html, ct = get(url)
    say(f"# {url}\nstatus {code} · {ct} · {len(html)} bytes · final cookies: {list(S.cookies.keys())[:6]}")
    say("\n## forms"); 
    for m in re.finditer(r"<form[^>]*>(.*?)</form>", html, re.S | re.I):
        tag = re.search(r"<form[^>]*>", m.group(0), re.I).group(0)
        inputs = re.findall(r"<(?:input|select|button)[^>]*>", m.group(1), re.I)
        say(tag); [say("   " + i[:200]) for i in inputs[:12]]
    say("\n## captcha signs: " + ", ".join(sorted(set(re.findall(r"(?i)recaptcha|hcaptcha|turnstile|grecaptcha|captcha", html)))) or "none in HTML")
    say("\n## the form's own JavaScript (onFormSubmit / location changes)")
    for m in re.finditer(r"<script(?![^>]*src)[^>]*>(.*?)</script>", html, re.S | re.I):
        js = m.group(1)
        for k in re.finditer(r"onFormSubmit|location\.href|window\.location|location\.assign|location\.replace|\.submit\(", js):
            a, b = max(0, k.start() - 200), min(len(js), k.end() + 900); say("   …" + re.sub(r"\s+", " ", js[a:b]) + "…")
        for k in re.findall(r"""["'`](/[A-Za-z0-9_\-/]{3,80})["'`]""", js):
            say("   path string: " + k)
    say("\n## inline script snippets with fetch/axios/XMLHttpRequest/api")
    for m in re.finditer(r"<script(?![^>]*src)[^>]*>(.*?)</script>", html, re.S | re.I):
        js = m.group(1)
        for k in re.finditer(r"fetch\(|axios\.|XMLHttpRequest|/api/|graphql", js):
            a, b = max(0, k.start() - 150), min(len(js), k.end() + 250); say("   …" + js[a:b].replace("\n", " ") + "…")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    say(f"\n## scripts ({len(scripts)})")
    for s_ in scripts[:25]:
        say("   " + s_)
    say("\n## inside scripts: api-looking strings")
    seen = 0
    for s_ in scripts[:20]:
        c2, js, _ = get(urljoin(base, s_))
        if c2 != 200: continue
        for k in re.finditer(r"""["'`](/[a-zA-Z0-9_\-/{}.$]*(?:api|track|awb|consign|shipment)[a-zA-Z0-9_\-/{}.$?=&]*)["'`]""", js):
            a, b = max(0, k.start() - 120), min(len(js), k.end() + 160)
            say(f"   [{s_.rsplit('/',1)[-1][:40]}] " + js[a:b].replace("\n", " ")); seen += 1
            if seen > 40: break
        if seen > 40: break
    say("\n## the page the form goes to")
    found = re.search(r'encodeURI\("([^"]+)"\s*\+\s*"/"\s*\+\s*"([^"]+)"', html)
    result_url = f"{base}{found.group(1)}/{found.group(2)}/{number}" if found else f"{base}/track-and-trace/india-post-domestic/{number}"
    code, txt, ct = get(result_url, headers={"Referer": url})
    say(f"   {code} {result_url} · {ct[:30]} · {len(txt)} bytes · number present: {number in txt}")
    text = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", txt, flags=re.S))
    i = text.find(number); say("   visible text around the number: …" + text[max(0, i - 400): i + 1800] + "…" if i >= 0 else "   visible text: " + text[:1500])
    for m in re.finditer(r"<script(?![^>]*src)[^>]*>(.*?)</script>", txt, re.S | re.I):
        js = m.group(1)
        if re.search(r"fetch\(|\$\.(ajax|get|post|getJSON)\(|XMLHttpRequest|axios|/api/|ajax", js):
            say("   result-page script: " + re.sub(r"\s+", " ", js)[:2500])
    for s_ in re.findall(r'<script[^>]+src="([^"]+)"', txt):
        say("   result-page script src: " + s_)
    for m in re.finditer(r"<(?:table|ul|ol)[^>]*>(.*?)</(?:table|ul|ol)>", txt, re.S | re.I):
        inner = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", m.group(1)))
        if re.search(r"(?i)booked|received|dispatch|deliver|bagged", inner): say("   result-page table/list: " + inner[:1500])
    say("\n## likely result URLs")
    slug = urlparse(url).path.strip("/")
    cands = [f"{base}/{slug}?awb={number}", f"{base}/{slug}?tracking={number}", f"{base}/{slug}?number={number}", f"{base}/{slug}/{number}",
             f"{base}/track-and-trace/india-post/{number}", f"{base}/track/{number}", f"{base}/tracking/{number}", f"{base}/india-post/{number}",
             f"{base}/api/track?awb={number}&courier=india-post", f"{base}/api/track/{number}", f"{base}/api/tracking?number={number}"]
    for c in cands:
        code, txt, ct = get(c, headers={"Referer": url})
        hit = number in txt; words = sorted(set(re.findall(r"(?i)delivered|booked|dispatched|out for delivery|item received|bagged", txt)))[:6]
        body = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", txt, flags=re.S))[:300]
        say(f"   {code} {c}\n      number present: {hit} · words: {words} · {ct[:30]}\n      {body}")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("```\n" + "\n".join(out) + "\n```\n")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2].strip().upper())
