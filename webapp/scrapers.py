"""Scraping cores shared by the web tool.

Async Playwright versions of Amazon_Updated_Script.py and console_snippet.js.
Each scraper is a generator-ish coroutine that reports progress through a
callback so the web layer can stream it to the browser.
"""

import asyncio
import random
import re
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

AMAZON_COLUMNS = [
    "ASIN", "Status", "Title", "Price", "Rating", "ReviewCount", "Bullets", "URL",
]
FLIPKART_COLUMNS = ["model"]

PRICE_SELECTORS = [
    ".priceToPay .a-offscreen",
    ".a-price .a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
]

ASIN_RE = re.compile(r"\b([A-Z0-9]{10})\b")


def clean_asin(value):
    """Normalise one cell into an ASIN, pulling it out of a URL if needed."""
    asin = str(value).strip().upper()

    if not asin or asin == "NAN":
        return None

    if "." in asin and asin.replace(".", "").isdigit():
        asin = asin.split(".")[0]

    if "AMAZON." in asin or "/DP/" in asin:
        match = re.search(r"/(?:DP|GP/PRODUCT)/([A-Z0-9]{10})", asin)
        if match:
            return match.group(1)

    match = ASIN_RE.search(asin)

    return match.group(1) if match else None


def parse_asin_text(text):
    """Split a pasted blob (newlines, commas, spaces) into unique ASINs."""
    seen, asins = set(), []

    for token in re.split(r"[\s,;]+", text or ""):
        asin = clean_asin(token)

        if asin and asin not in seen:
            seen.add(asin)
            asins.append(asin)

    return asins


def blank_row(asin, status):
    return {
        "ASIN": asin,
        "Status": status,
        "Title": None,
        "Price": None,
        "Rating": None,
        "ReviewCount": None,
        "Bullets": None,
        "URL": f"https://www.amazon.in/dp/{asin}",
    }


async def scrape_amazon_product(page, asin):
    url = f"https://www.amazon.in/dp/{asin}"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(random.uniform(2, 4))

        soup = BeautifulSoup(await page.content(), "html.parser")
        title = soup.select_one("#productTitle")

        if not title:
            return blank_row(asin, "Blocked/Not Found")

        # An .a-offscreen node can be present but empty, so keep looking
        # until a selector actually yields text.
        price = None
        for selector in PRICE_SELECTORS:
            tag = soup.select_one(selector)
            if tag and tag.get_text(strip=True):
                price = tag.get_text(strip=True)
                break

        rating_tag = (
            soup.select_one("#acrPopover .a-icon-alt")
            or soup.select_one("span.a-icon-alt")
        )
        review_tag = (
            soup.select_one("#acrCustomerReviewText")
            or soup.select_one("#acrCustomerReviewLink")
        )

        bullets = [
            text
            for li in soup.select("#feature-bullets li")
            if (text := li.get_text(" ", strip=True))
        ]

        return {
            "ASIN": asin,
            "Status": "Success",
            "Title": title.get_text(strip=True),
            "Price": price,
            "Rating": rating_tag.get_text(strip=True) if rating_tag else None,
            "ReviewCount": review_tag.get_text(strip=True) if review_tag else None,
            "Bullets": "; ".join(bullets),
            "URL": url,
        }

    except Exception as exc:
        return blank_row(asin, f"Error: {exc}")


async def run_amazon(asins, on_progress, headless=False, min_delay=2.0, max_delay=5.0,
                     should_stop=None):
    """Visit each ASIN in one browser session, reporting rows as they land."""
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(locale="en-IN", user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            for index, asin in enumerate(asins, start=1):
                if should_stop and should_stop():
                    await on_progress("log", {"message": "Stopped by user."})
                    break

                await on_progress("log", {"message": f"[{index}/{len(asins)}] {asin}"})

                row = await scrape_amazon_product(page, asin)
                results.append(row)

                await on_progress("row", {"row": row, "done": index, "total": len(asins)})

                if index < len(asins):
                    await asyncio.sleep(random.uniform(min_delay, max_delay))

        finally:
            await context.close()
            await browser.close()

    return results


# The DevTools snippet, reused verbatim as an in-page evaluate. Returns the
# model names instead of downloading a CSV.
FLIPKART_COLLECT_JS = Path(__file__).with_name("flipkart_collect.js").read_text(
    encoding="utf-8"
)


async def run_flipkart(url, on_progress, headless=False, should_stop=None):
    """Open a Flipkart listing, expand the Compatible Mobiles filter, harvest it."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(locale="en-IN", user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            await on_progress("log", {"message": f"Opening {url}"})
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await asyncio.sleep(3)

            await on_progress("log", {"message": "Looking for the Compatible Mobiles filter…"})

            opened = await page.evaluate(
                """() => {
                  const hit = [...document.querySelectorAll('div,span,section')].find(el =>
                    el.children.length === 0 &&
                    /compatible mobile/i.test(el.textContent || ''));
                  if (hit) { hit.click(); return true; }
                  return false;
                }"""
            )

            if opened:
                await on_progress("log", {"message": "Filter opened, waiting for the overlay."})
                await asyncio.sleep(3)
            else:
                await on_progress("log", {
                    "message": "Could not click the filter automatically — "
                               "open it in the browser window, then wait.",
                })
                await asyncio.sleep(12)

            if should_stop and should_stop():
                return []

            await on_progress("log", {"message": "Scrolling the model list…"})
            models = await page.evaluate(FLIPKART_COLLECT_JS)

            await on_progress("log", {"message": f"Collected {len(models)} models."})

            rows = [{"model": m} for m in models]

            for row in rows:
                await on_progress("row", {"row": row, "done": len(rows), "total": len(rows)})

            return rows

        finally:
            await context.close()
            await browser.close()
