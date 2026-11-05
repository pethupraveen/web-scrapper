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
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from .browser import CLOUD, launch_browser


class ListingFilterError(Exception):
    """The listing does not expose the filter needed for model collection."""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

AMAZON_COLUMNS = [
    "ASIN", "Status", "Title", "Price", "Rating", "ReviewCount", "Bullets", "URL",
]
FLIPKART_COLUMNS = ["model"]
FLIPKART_PID_COLUMNS = [
    "PID", "Status", "Name", "Rating", "RatingCount", "ReviewCount",
    "Price", "MRP", "Stock", "URL",
]

COLUMNS_BY_KIND = {
    "amazon": AMAZON_COLUMNS,
    "flipkart": FLIPKART_COLUMNS,
    "flipkart_pid": FLIPKART_PID_COLUMNS,
}

PRICE_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    "#buybox .a-price .a-offscreen",
    ".priceToPay .a-offscreen",
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


def parse_amazon_html(asin, html):
    """Read product fields only; carousel/navigation text is never a rating."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("#productTitle")
    if not title or not title.get_text(strip=True):
        if soup.select_one('#captchacharacters, form[action*="validateCaptcha"]') or any(
                marker in soup.get_text(" ", strip=True).lower()
                for marker in ("enter the characters you see below", "sorry, we just need to make sure",
                               "automated access to amazon")):
            return blank_row(asin, "Blocked by Amazon")
        return blank_row(asin, "Blocked/Not Found")

    product_id = soup.select_one('input#ASIN[value]')
    if product_id and product_id.get("value", "").upper() != asin:
        return blank_row(asin, "Error: Amazon returned a different product")

    price = None
    for selector in PRICE_SELECTORS:
        tag = soup.select_one(selector)
        if tag and tag.get_text(strip=True):
            price = tag.get_text(strip=True)
            break

    rating = None
    for tag in soup.select('#acrPopover .a-icon-alt, #acrPopover[title], '
                           '#averageCustomerReviews [data-hook="rating-out-of-text"], '
                           '#averageCustomerReviews .a-icon-alt'):
        text = tag.get("title") or tag.get_text(" ", strip=True)
        match = re.search(r"\b([0-5](?:[.,]\d+)?)\s*(?:out of|/)\s*5\b", text, re.I)
        if match and float(match.group(1).replace(",", ".")) <= 5:
            rating = f'{match.group(1).replace(",", ".")} out of 5 stars'
            break
    review_tag = soup.select_one("#acrCustomerReviewText")
    review_count = review_tag.get_text(strip=True) if review_tag else None
    if review_count and not re.search(r"\d", review_count):
        review_count = None
    bullets = [text for li in soup.select("#feature-bullets li")
               if (text := li.get_text(" ", strip=True))]
    return {"ASIN": asin, "Status": "Success", "Title": title.get_text(strip=True),
            "Price": price, "Rating": rating, "ReviewCount": review_count,
            "Bullets": "; ".join(bullets), "URL": f"https://www.amazon.in/dp/{asin}"}


async def scrape_amazon_product(page, asin):
    try:
        await page.goto(f"https://www.amazon.in/dp/{asin}", wait_until="domcontentloaded",
                        timeout=45000 if CLOUD else 60000)
        if CLOUD:
            try:
                await page.wait_for_selector("#productTitle", state="attached", timeout=8000)
            except PlaywrightTimeoutError:
                pass
        else:
            await asyncio.sleep(random.uniform(2, 4))
        return parse_amazon_html(asin, await page.content())
    except Exception as exc:
        return blank_row(asin, f"Error: {exc}")


async def run_amazon(asins, on_progress, headless=False, min_delay=2.0, max_delay=5.0,
                     should_stop=None):
    """Visit each ASIN in one browser session, reporting rows as they land."""
    results = []

    async with async_playwright() as p:
        browser = await launch_browser(p, headless=headless)
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

FLIPKART_PRICE_JS = Path(__file__).with_name("flipkart_price.js").read_text(
    encoding="utf-8"
)


async def run_flipkart(url, on_progress, headless=False, should_stop=None):
    """Open a Flipkart listing, expand the Compatible Mobiles filter, harvest it."""
    async with async_playwright() as p:
        browser = await launch_browser(p, headless=headless)
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
                if CLOUD:
                    raise ListingFilterError(
                        "This listing did not expose the Compatible Mobiles filter. "
                        "Try a category listing with that filter; Flipkart may also have blocked the page.")
                await on_progress("log", {
                    "message": "Could not click the filter automatically — "
                               "open it in the browser window, then wait.",
                })
                await asyncio.sleep(12)

            if should_stop and should_stop():
                return []

            await on_progress("log", {"message": "Scrolling the model list…"})
            try:
                models = await page.evaluate(FLIPKART_COLLECT_JS)
            except Exception as exc:
                if CLOUD and ('Search Compatible Mobile' in str(exc) or 'not the list container' in str(exc)):
                    raise ListingFilterError(
                        "The Compatible Mobiles filter could not be read. "
                        "Try a category listing with that filter, or retry the page.") from exc
                raise

            await on_progress("log", {"message": f"Collected {len(models)} models."})

            rows = [{"model": m} for m in models]

            for row in rows:
                await on_progress("row", {"row": row, "done": len(rows), "total": len(rows)})

            return rows

        finally:
            await context.close()
            await browser.close()


# ── Flipkart product pages, by PID / FSN ────────────────────────────────
#
# The URL matters: /product/p/itm (no trailing "e") serves a generic "Itm
# Store" listing that ignores the pid entirely and renders the same product
# for every id. /product/p/itme resolves the pid properly.
FLIPKART_PID_URL = "https://www.flipkart.com/product/p/itme?pid={pid}"

PID_RE = re.compile(r"\b([A-Z0-9]{16})\b")

# Resolved once the buy box has settled into a buyable or unbuyable state.
# Without this the price and stock fields get read mid-hydration.
BUY_BOX_READY_JS = """() => {
  const t = document.body ? document.body.innerText : '';
  return /Add to cart|Buy now|Notify Me|Currently unavailable|Sold Out/i.test(t);
}"""

# Everything above these headings belongs to the product; below them are the
# recommendation carousels, whose prices are for *other* products.
#
# Keep these headings specific. A loose word like "Recommended" also matches
# review text ("Not recommended at all") and truncates the page before the
# buy box ever appears.
CAROUSEL_RE = re.compile(
    r"^\s*(?:Similar Products|Products Related to this Item|"
    r"You might be interested in)\s*$",
    re.I | re.M,
)


def clean_pid(value):
    """Normalise a cell into a Flipkart PID, pulling it out of a URL if needed."""
    pid = str(value).strip().upper()

    if not pid or pid == "NAN":
        return None

    match = re.search(r"[?&]PID=([A-Z0-9]{16})", pid)
    if match:
        return match.group(1)

    match = PID_RE.search(pid)

    return match.group(1) if match else None


def parse_pid_text(text):
    seen, pids = set(), []

    for token in re.split(r"[\s,;]+", text or ""):
        pid = clean_pid(token)

        if pid and pid not in seen:
            seen.add(pid)
            pids.append(pid)

    return pids


def blank_pid_row(pid, status):
    row = {column: None for column in FLIPKART_PID_COLUMNS}
    row["PID"] = pid
    row["Status"] = status
    row["URL"] = FLIPKART_PID_URL.format(pid=pid)
    return row


def parse_flipkart_page(pid, title, body, prices=None):
    """Pull the product fields out of the rendered page text.

    Deliberately reads text rather than class names — Flipkart rotates CSS
    hashes, which is what broke the original scraper_updated.py.
    """
    name = re.sub(r"\s*[:|]\s*Flipkart\.com\s*$", "", title).strip()
    name = re.sub(r"\s*-\s*Buy\s.*$", "", name).strip()
    name = re.sub(r"\s*-\s*Sprig\s*$", "", name).strip() or None

    # A pid that doesn't resolve lands on the generic store page.
    if name and re.match(r"^Itm Store\b", name, re.I):
        return blank_pid_row(pid, "Not Found")

    head = CAROUSEL_RE.split(body)[0]

    rating_count = review_count = rating = None

    match = re.search(r"based on\s*([\d,]+)\s*ratings?", head, re.I)
    if match:
        rating_count = match.group(1)

    match = re.search(r"([\d,]+)\s*Reviews?\b", head, re.I)
    if match:
        review_count = match.group(1)

    match = re.search(r"(?m)^\s*([0-5](?:\.\d)?)\s*$", head)
    if match:
        rating = match.group(1)

    prices = prices or {}
    price = prices.get("price")
    mrp = prices.get("mrp")

    # Stock reads the whole page, not just the head: the buy buttons can render
    # below the fold in a sticky bar, and no carousel ever says "Add to cart",
    # so there is nothing here to confuse them with.
    #
    # Positive evidence only — defaulting to "In stock" mislabels pages whose
    # buy box never rendered a button at all.
    if re.search(r"\bAdd to cart\b|\bBuy now\b", body, re.I):
        stock = "In stock"
    elif re.search(r"\bNotify Me\b|Currently unavailable|Sold Out", body, re.I):
        stock = "Out of stock"
    else:
        stock = "Unknown"

    return {
        "PID": pid,
        "Status": "Success" if name else "Blocked/Not Found",
        "Name": name,
        "Rating": rating,
        "RatingCount": rating_count,
        "ReviewCount": review_count,
        "Price": price,
        "MRP": mrp,
        "Stock": stock,
        "URL": FLIPKART_PID_URL.format(pid=pid),
    }


async def scrape_flipkart_pid(page, pid):
    url = FLIPKART_PID_URL.format(pid=pid)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # The buy box hydrates client-side. Wait for it to declare itself one
        # way or the other, otherwise price and stock are read mid-render.
        try:
            await page.wait_for_function(BUY_BOX_READY_JS, timeout=15000)
        except Exception:
            pass

        await asyncio.sleep(random.uniform(0.6, 1.2))

        title = await page.title()
        body = await page.eval_on_selector("body", "e => e.innerText")
        prices = await page.evaluate(FLIPKART_PRICE_JS)

        return parse_flipkart_page(pid, title, body, prices)

    except Exception as exc:
        return blank_pid_row(pid, f"Error: {exc}")


async def run_flipkart_pids(pids, on_progress, headless=False, min_delay=2.0,
                            max_delay=5.0, should_stop=None):
    results = []

    async with async_playwright() as p:
        browser = await launch_browser(p, headless=headless)
        context = await browser.new_context(locale="en-IN", user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            for index, pid in enumerate(pids, start=1):
                if should_stop and should_stop():
                    await on_progress("log", {"message": "Stopped by user."})
                    break

                await on_progress("log", {"message": f"[{index}/{len(pids)}] {pid}"})

                row = await scrape_flipkart_pid(page, pid)
                results.append(row)

                await on_progress("row", {"row": row, "done": index, "total": len(pids)})

                if index < len(pids):
                    await asyncio.sleep(random.uniform(min_delay, max_delay))

        finally:
            await context.close()
            await browser.close()

    return results
