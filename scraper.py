"""
Booking.com scraper for Kyoto machiya/accommodation properties.
Uses Playwright for browser automation to handle JavaScript-heavy pages.
"""

import asyncio
import json
import re
import random
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext


# ──────────────────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PropertyInfo:
    name: str = ""
    address: str = ""
    rating: str = ""
    rating_count: str = ""
    price: str = ""
    url: str = ""
    description: str = ""
    category: str = ""


@dataclass
class Review:
    reviewer_name: str = ""
    reviewer_country: str = ""
    date: str = ""
    score: str = ""
    title: str = ""
    positive: str = ""
    negative: str = ""
    room_type: str = ""
    stay_type: str = ""  # e.g. "Solo traveller", "Couple"


@dataclass
class PropertyData:
    property: PropertyInfo = field(default_factory=PropertyInfo)
    reviews: list = field(default_factory=list)

    def to_dict(self):
        return {
            "property": asdict(self.property),
            "reviews": [asdict(r) if isinstance(r, Review) else r for r in self.reviews],
        }


# ──────────────────────────────────────────────────────────────────────────────
# Scraper
# ──────────────────────────────────────────────────────────────────────────────

class BookingScraper:
    BASE_URL = "https://www.booking.com"

    # Neutral check-in / check-out used only to get price visibility
    CHECKIN = "2026-05-01"
    CHECKOUT = "2026-05-02"

    def __init__(self, headless: bool = True, verbose: bool = False):
        self.headless = headless
        self.verbose = verbose

    # ── helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        if self.verbose:
            print(f"  [scraper] {msg}")

    async def _make_context(self, playwright) -> tuple:
        browser = await playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            timezone_id="Asia/Tokyo",
            extra_http_headers={
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        # Mask webdriver flag
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        return browser, context

    async def _dismiss_overlays(self, page: Page):
        """Close cookie banners, sign-in modals, and any other overlays."""
        selectors = [
            # Cookie consent
            'button#onetrust-accept-btn-handler',
            'button[data-gdpr-consent="accept"]',
            'button:has-text("Accept")',
            'button:has-text("I agree")',
            # Sign-in / register modal close button
            'button[aria-label="Dismiss sign-in info."]',
            'button[aria-label="Close"]',
            'button[data-testid="header-sign-in-button"] ~ * button',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click(timeout=1500)
                    await asyncio.sleep(0.4)
            except Exception:
                pass

    async def _safe_text(self, page: Page, *selectors: str) -> str:
        """Try multiple selectors and return the first non-empty text found."""
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    txt = await el.text_content(timeout=3000)
                    if txt and txt.strip():
                        return txt.strip()
            except Exception:
                pass
        return ""

    async def _human_delay(self, lo=0.8, hi=2.0):
        await asyncio.sleep(random.uniform(lo, hi))

    # ── public API ────────────────────────────────────────────────────────────

    async def search_properties(
        self,
        location: str,
        accommodation_type: str,
        max_results: int = 5,
    ) -> list[dict]:
        """
        Search booking.com for properties matching location + accommodation_type.
        Returns a list of dicts with {name, url, rating, price, address}.
        """
        query = f"{location} {accommodation_type}"
        search_url = (
            f"{self.BASE_URL}/searchresults.html"
            f"?ss={query.replace(' ', '+')}"
            f"&lang=en-gb"
            f"&checkin={self.CHECKIN}"
            f"&checkout={self.CHECKOUT}"
            f"&group_adults=2&no_rooms=1"
        )
        self._log(f"Searching: {search_url}")

        results = []
        async with async_playwright() as pw:
            browser, context = await self._make_context(pw)
            page = await context.new_page()
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=40000)
                await self._human_delay(2, 3)
                await self._dismiss_overlays(page)
                await self._human_delay(1, 1.5)

                # Wait for at least one property card
                try:
                    await page.wait_for_selector('[data-testid="property-card"]', timeout=15000)
                except Exception:
                    self._log("No property cards found — possibly blocked or no results")

                cards = page.locator('[data-testid="property-card"]')
                total = min(await cards.count(), max_results)
                self._log(f"Found {await cards.count()} results, reading {total}")

                for i in range(total):
                    card = cards.nth(i)
                    try:
                        name = await self._safe_text(
                            page if False else card,  # scope to card
                            '[data-testid="title"]',
                        )
                        # Scoped helper
                        async def card_text(*sels):
                            for s in sels:
                                try:
                                    el = card.locator(s).first
                                    if await el.count() > 0:
                                        t = await el.text_content(timeout=2000)
                                        if t and t.strip():
                                            return t.strip()
                                except Exception:
                                    pass
                            return ""

                        name = await card_text('[data-testid="title"]')
                        address = await card_text(
                            '[data-testid="address"]',
                            '.abf093bdfe',
                            '[class*="address"]',
                        )
                        rating = await card_text(
                            '[data-testid="review-score"] .ac4a7896c7',
                            '[data-testid="review-score"] div[class*="score"]',
                            '.b5cd09854e',
                        )
                        price = await card_text(
                            '[data-testid="price-and-discounted-price"]',
                            '[data-testid="price"]',
                            'span[class*="price"]',
                        )

                        # Get property URL
                        link_el = card.locator('a[data-testid="title-link"], h3 a').first
                        href = ""
                        try:
                            href = await link_el.get_attribute("href", timeout=2000) or ""
                        except Exception:
                            pass

                        if name:
                            results.append({
                                "name": name,
                                "url": href if href.startswith("http") else self.BASE_URL + href,
                                "rating": rating,
                                "price": price,
                                "address": address,
                            })
                    except Exception as e:
                        self._log(f"Error parsing card {i}: {e}")
            finally:
                await browser.close()

        return results

    async def get_property_details(self, url: str) -> PropertyData:
        """
        Scrape a single property page for basic info + all reviews.
        """
        # Ensure checkin params are present for price visibility
        if "checkin=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}checkin={self.CHECKIN}&checkout={self.CHECKOUT}&group_adults=2&no_rooms=1&lang=en-gb"

        self._log(f"Loading property page: {url}")

        data = PropertyData()
        async with async_playwright() as pw:
            browser, context = await self._make_context(pw)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                await self._human_delay(2, 3)
                await self._dismiss_overlays(page)
                await self._human_delay(1, 1.5)

                data.property = await self._extract_property_info(page, url)
                data.reviews = await self._extract_all_reviews(page, url)
            finally:
                await browser.close()

        return data

    # ── property info ─────────────────────────────────────────────────────────

    async def _extract_property_info(self, page: Page, url: str) -> PropertyInfo:
        info = PropertyInfo(url=url)

        info.name = await self._safe_text(
            page,
            'h2.pp-header__title',
            '[data-capla-component*="PropertyHeader"] h2',
            'h1[data-capla-component*="PropertyName"]',
            'h2[class*="pp-header"]',
            '#hp_hotel_name',
        )
        self._log(f"Property name: {info.name!r}")

        info.address = await self._safe_text(
            page,
            '.hp_address_subtitle',
            'span.hp_address_subtitle',
            '[data-testid="PropertySummaryAddressHighlight"] span',
            '[class*="address_clean"]',
            '[id="showMap2"] span',
        )

        info.rating = await self._safe_text(
            page,
            '[data-testid="review-score-right-component"] .ac4a7896c7',
            '.bui-review-score__badge',
            '[class*="review-score-badge"]',
            'div[class*="reviewBadge"] span',
        )

        info.rating_count = await self._safe_text(
            page,
            '[data-testid="review-score-right-component"] .b5cd09854e',
            '.bui-review-score__text',
            '[class*="review_score_count"]',
        )

        info.price = await self._safe_text(
            page,
            '[data-testid="price-and-discounted-price"]',
            '.hprt-price-price .bui-price-display__value',
            '[class*="finalPrice"]',
            'span[class*="price"]',
        )

        # Short description (first paragraph / tagline)
        info.description = await self._safe_text(
            page,
            '#property_description_content p:first-of-type',
            '.hp_desc_main_content p:first-of-type',
            '[class*="description"] p:first-of-type',
        )

        info.category = await self._safe_text(
            page,
            '.hp_desc_main_content .hp-badge-icon',
            '[class*="propertyTypeIcon"]',
            '.bh-quality-bar',
        )

        return info

    # ── reviews ───────────────────────────────────────────────────────────────

    def _parse_slug_and_cc(self, url: str) -> tuple[str, str]:
        """Extract hotel slug and country code from a booking.com hotel URL."""
        m = re.search(r'/hotel/([^/]+)/([^.?#]+)', url)
        if m:
            return m.group(2), m.group(1)  # slug, country_code
        return "", "jp"

    async def _extract_all_reviews(self, page: Page, prop_url: str) -> list[dict]:
        """
        Navigate to the booking.com review list page and scrape all reviews
        with pagination.
        """
        slug, cc = self._parse_slug_and_cc(prop_url)
        if not slug:
            self._log("Could not determine slug — skipping reviews")
            return []

        all_reviews: list[dict] = []
        offset = 0
        rows = 25
        max_reviews = 300  # safety cap

        self._log(f"Fetching reviews for slug={slug!r}, cc={cc!r}")

        while len(all_reviews) < max_reviews:
            review_url = (
                f"{self.BASE_URL}/reviewlist.html"
                f"?pagename={slug}"
                f"&cc1={cc}"
                f"&type=total"
                f"&lang=en-gb"
                f"&offset={offset}"
                f"&rows={rows}"
            )
            self._log(f"Review page offset={offset}: {review_url}")

            try:
                await page.goto(review_url, wait_until="domcontentloaded", timeout=30000)
                await self._human_delay(1.5, 2.5)
                await self._dismiss_overlays(page)
            except Exception as e:
                self._log(f"Failed to load review page: {e}")
                break

            page_reviews = await self._parse_review_page(page)
            if not page_reviews:
                self._log("No reviews found on this page — stopping pagination")
                break

            all_reviews.extend(page_reviews)
            self._log(f"  → collected {len(page_reviews)} reviews (total so far: {len(all_reviews)})")

            # Check for a "next page" button
            has_next = False
            try:
                next_btn = page.locator(
                    'a[aria-label="Next page"], '
                    'button[aria-label="Next page"], '
                    'li.bui-pagination__next-arrow a'
                ).first
                has_next = await next_btn.is_visible(timeout=2000)
            except Exception:
                pass

            if not has_next:
                break

            offset += rows
            await self._human_delay(1, 2)

        return all_reviews

    async def _parse_review_page(self, page: Page) -> list[dict]:
        """Parse all review blocks on the current review list page."""
        reviews = []

        # Try several possible container selectors (booking.com changes them)
        container_selectors = [
            '.review_list_block_container',
            '.c-review-block',
            '[data-testid="review-item"]',
            'li.review_list_block',
        ]

        container_sel = None
        for sel in container_selectors:
            try:
                count = await page.locator(sel).count()
                if count > 0:
                    container_sel = sel
                    self._log(f"Review container selector: {sel!r} ({count} items)")
                    break
            except Exception:
                pass

        if not container_sel:
            self._log("No review containers found on page")
            return []

        blocks = page.locator(container_sel)
        total = await blocks.count()

        for i in range(total):
            block = blocks.nth(i)
            review = await self._parse_single_review(block)
            if review:
                reviews.append(review)

        return reviews

    async def _parse_single_review(self, block) -> Optional[dict]:
        """Extract fields from a single review block."""

        async def t(*selectors):
            """Get cleaned text from the first matching selector within this block."""
            for sel in selectors:
                try:
                    el = block.locator(sel).first
                    if await el.count() > 0:
                        txt = await el.text_content(timeout=2000)
                        if txt and txt.strip():
                            return txt.strip()
                except Exception:
                    pass
            return ""

        name = await t(
            '.bui-avatar-block__title',
            '[class*="reviewer_name"]',
            '[class*="reviewerName"]',
            'span[class*="author"]',
        )
        country = await t(
            '.bui-avatar-block__subtitle',
            '[class*="reviewer_country"]',
            'span[class*="country"]',
        )
        date = await t(
            '.c-review-block__date',
            '[class*="review_date"]',
            'span[class*="reviewDate"]',
            'p[class*="date"]',
        )
        score = await t(
            '.bui-review-score__badge',
            '[class*="review-score-badge"]',
            'span[class*="reviewScore"]',
            'div[class*="scoreReview"]',
        )
        title = await t(
            '.c-review-block__title',
            '[class*="review_item_header_content"]',
            'p[class*="reviewTitle"]',
        )

        # Positive / negative text — booking.com wraps them in themed blocks
        positive = await t(
            # new markup
            '[data-review-positive="true"] .c-review__body',
            '[class*="c-review--positive"] .c-review__body',
            # older markup
            'p[class*="review_pos"]',
            '.review-positive .review_item_review_content',
            'span[class*="positive"]',
        )
        negative = await t(
            '[data-review-negative="true"] .c-review__body',
            '[class*="c-review--negative"] .c-review__body',
            'p[class*="review_neg"]',
            '.review-negative .review_item_review_content',
            'span[class*="negative"]',
        )

        # Fall back: grab all review body text
        if not positive and not negative:
            body = await t('.c-review__body', '[class*="review_item_review"]')
            positive = body  # treat as generic review text

        room_type = await t(
            '.c-review-block__room-info span',
            '[class*="room_info"]',
            '[class*="roomInfo"]',
        )
        stay_type = await t(
            '[class*="traveler_type"]',
            '[class*="travelerType"]',
            '.bui-list__item span[class*="type"]',
        )

        # Only include reviews that have some content
        if not any([positive, negative, title]):
            return None

        return asdict(Review(
            reviewer_name=name,
            reviewer_country=country,
            date=date,
            score=score,
            title=title,
            positive=positive,
            negative=negative,
            room_type=room_type,
            stay_type=stay_type,
        ))


# ──────────────────────────────────────────────────────────────────────────────
# Convenience helpers
# ──────────────────────────────────────────────────────────────────────────────

def save_results(data: dict, path: str):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved → {path}")


def load_results(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
