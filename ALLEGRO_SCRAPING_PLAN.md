# Plan: Scraping allegro.sk

Slovak branch of Allegro (largest Polish marketplace). Same Next.js stack and
anti-bot posture as allegro.pl, so the techniques from the Scrapfly and
Scrape.do guides transfer directly. A bare `curl` against `https://allegro.sk`
already returns HTTP 403, which confirms we need realistic browser traffic or a
scraping API — not plain `requests`.

This plan mirrors the shape of the existing Booking.com scraper in this repo
(`scraper.py` + `analyzer.py` + `main.py`) so a future implementation can reuse
the same CLI, output-path, and analyzer plumbing.

---

## 1. Scope & target data

Two entry points, chosen by the user at the CLI:

1. **Search / listing URL** — e.g.
   `https://allegro.sk/kategoria/telefony-a-prislusenstvo` or
   `https://allegro.sk/listing?string=iphone+15`.
   Output: list of offers with minimal fields + pagination.
2. **Single offer URL** — e.g.
   `https://allegro.sk/ponuka/<slug>-<offer-id>`.
   Output: full offer detail + seller info + reviews/questions if available.

### Fields per offer (listing row)
`id`, `title`, `url`, `price`, `currency`, `shipping_price`, `condition`
(new/used), `seller_name`, `seller_rating`, `image_url`, `badges` (Smart!,
Allegro Biznes, sponsored), `location`.

### Fields per offer (detail page)
Everything above plus: `description_html`, `parameters` (key/value spec table),
`images[]`, `available_quantity`, `delivery_options[]`, `return_policy`,
`category_path[]`, `rating_avg`, `rating_count`, `reviews[]` (author, date,
score, body), `questions[]` (Q/A pairs if present).

---

## 2. Site structure — what to actually parse

Allegro is a Next.js application. Every HTML response embeds a full JSON blob
in a `<script id="__NEXT_DATA__" type="application/json">…</script>` tag (and,
on some routes, additional `__listing_StoreState__` / `__offer_StoreState__`
scripts). Parsing that JSON is dramatically more stable than CSS selectors,
which Allegro rotates frequently (opaque hashed class names).

**Strategy: JSON-first, DOM-fallback.**

```python
import json, re
from selectolax.parser import HTMLParser

def extract_next_data(html: str) -> dict:
    tree = HTMLParser(html)
    node = tree.css_first('script#__NEXT_DATA__')
    if node:
        return json.loads(node.text())
    # Fallback: regex for inline state
    m = re.search(r'window\.__listing_StoreState__\s*=\s*(\{.*?\});', html, re.S)
    return json.loads(m.group(1)) if m else {}
```

Within `__NEXT_DATA__`, offers on a listing page live under roughly
`props.pageProps.*.items` (the exact path shifts, so navigate defensively and
log the keys encountered). Offer detail pages expose product, parameters, and
seller under `props.pageProps.offer` / `product`.

### Pagination

Listing URLs accept `?p=<N>` (1-indexed). `__NEXT_DATA__` also carries
`searchMeta.totalPages` and `totalCount`; read these to bound the loop instead
of relying on a "next" button. Allegro caps listings at ~60 pages even if
`totalCount` is higher — stop at `min(totalPages, 60)`.

---

## 3. Anti-bot plan

Allegro uses Cloudflare + their own bot-detection layer ("Allegro Pro"). Signs
we confirmed:

- Plain `curl` / `httpx` → 403.
- Requires realistic TLS fingerprint, JS challenge completion, and consistent
  cookies (`__cflb`, `__cf_bm`, `datadome`-like tokens).

Three viable tiers, in cost order:

### Tier A — Playwright (default, no paid services)
Re-use the pattern already in `scraper.py::_make_context`:

- Chromium, non-headless by default when debugging (`--no-headless` flag).
- Stealth tweaks already present: `--disable-blink-features=AutomationControlled`,
  override of `navigator.webdriver`. Add: randomized viewport, realistic
  `Accept-Language: sk-SK,sk;q=0.9,en;q=0.8`, `timezone_id="Europe/Bratislava"`,
  a real Chrome UA string matching the browser's major version.
- On first visit to `allegro.sk`, handle the cookie-consent banner
  (`button[data-role="accept-consent"]`, text "Súhlasím" / "Akceptujem
  všetko") before navigating further.
- Persist `storage_state` to disk (`state.json`) so subsequent runs start with
  warmed cookies and don't re-solve the JS challenge.
- Between requests: `asyncio.sleep(random.uniform(1.5, 4.0))`. Cap concurrency
  to 1–2 pages; Allegro rate-limits aggressively.

### Tier B — `curl_cffi` with a browser TLS profile
Once we have valid cookies from Tier A's `storage_state`, many JSON endpoints
can be hit directly with `curl_cffi.requests.Session(impersonate="chrome124")`
carrying those cookies. This is 10–20× faster than Playwright for bulk listing
crawls. Fall back to Tier A whenever a 403/429 re-appears (cookies expired).

### Tier C — Paid unblocker (only if Tier A/B fails at scale)
Scrapfly `asp=true`, Scrape.do, ZenRows, or Bright Data Unlocker. Thin wrapper
around their API so the rest of the code doesn't care which backend it called.
Gate behind an env var `ALLEGRO_UNBLOCKER=scrapfly|scrapedo|none`.

---

## 4. Proposed module layout

Add alongside the existing Booking scraper — don't replace it.

```
allegro_scraper.py      # new, mirrors scraper.py shape
  class AllegroScraper:
      async def search(query_or_url, max_pages) -> ListingResult
      async def get_offer(url) -> OfferData
      # internal:
      _fetch_html(url)          # Playwright by default, curl_cffi when cookies valid
      _extract_next_data(html)
      _parse_listing(next_data)
      _parse_offer(next_data, html)
      _dismiss_consent(page)
allegro_analyzer.py     # optional; only if we actually want Claude analysis of offers
main.py                 # extend with subcommand: `python main.py allegro <url>`
```

Keep dataclasses symmetric with `PropertyInfo` / `Review`:
`Offer`, `OfferDetail`, `Seller`, `AllegroReview`, `ListingResult`.

Reuse `save_results` / `output_path` from the existing code.

---

## 5. Implementation checkpoints

Ship in this order; each step is independently useful.

1. **Spike — single offer page via Playwright.**
   Load one offer URL, dump `__NEXT_DATA__` to `debug/offer_raw.json`, confirm
   title/price/seller are present. Goal: prove the JSON path works for .sk.
2. **Parse offer JSON → `OfferDetail`.** Write a pure function `(dict) -> OfferDetail`
   with unit tests on the saved `debug/offer_raw.json` — no network in tests.
3. **Listing crawl with pagination.** Read `totalPages`, loop `?p=1..N`,
   dedupe by `offer.id`, respect the 60-page cap.
4. **Cookie/session reuse.** Save `storage_state` after the first successful
   load; load it on subsequent runs.
5. **curl_cffi fast path.** Try JSON-only requests using saved cookies; on
   401/403/429, drop back to Playwright and refresh `storage_state`.
6. **CLI wiring.** `python main.py allegro <url> [--max-pages N]`.
   Output files: `results/allegro_<slug>_<ts>_raw.json`.
7. **Optional analyzer.** If we want Claude summaries of reviews/Q&A per
   offer, mirror `ReviewAnalyzer` with an Allegro-specific prompt (buyer
   sentiment, common complaints, shipping issues, counterfeit concerns).

---

## 6. Risks & open questions

- **Selector drift is unavoidable** — that's exactly why we go JSON-first.
  Still, the JSON *shape* inside `__NEXT_DATA__` also changes between Allegro
  deploys. Log the top-level keys of `pageProps` on every run and fail loud
  when an expected path is missing.
- **ToS.** Allegro's terms prohibit automated access without agreement. This
  plan is for low-volume, personal research use. For anything commercial or
  high-volume, apply for their official Allegro REST API instead
  (`developer.allegro.pl`) — it covers most of the same data (offers, listings,
  categories) with OAuth auth and documented rate limits.
- **.sk vs .pl parity.** Structure is the same today, but Allegro has been
  rolling .sk out gradually. If a specific endpoint 404s on .sk, try the
  identical path on allegro.pl as a sanity check before assuming our parser is
  broken.
- **Captchas.** When Cloudflare throws a challenge page, Playwright usually
  solves it transparently; if not, the run aborts with a clear message
  instructing the user to re-run with `--no-headless` and solve once — the
  resulting `storage_state` then unblocks future headless runs for hours.

---

## 7. References

- Scrapfly — *How to Scrape Allegro.pl Without Getting Blocked*
  https://scrapfly.io/blog/posts/how-to-scrape-allegro
- Scrape.do — *Scraping Allegro: How to Extract Product Data Easily*
  https://scrape.do/blog/allegro-scraping/
- JWprogrammer/allegro-scraper (PHP, closed-source — useful only for the field
  list it exposes)
  https://github.com/JWprogrammer/allegro-scraper
- Allegro REST API (official, preferred for production use)
  https://developer.allegro.pl/documentation
