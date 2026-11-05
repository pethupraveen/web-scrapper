"""Stateless request-based scraping for Vercel; the browser owns batch state."""

import asyncio
from contextlib import suppress
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
import httpx
from playwright.async_api import async_playwright

from . import scrapers
from .browser import launch_browser
from .jobs import Job

router = APIRouter(prefix="/api/cloud")
Kind = Literal["amazon", "flipkart_pid"]
_browser_slot = asyncio.Semaphore(1)
CLOUD_USER_AGENT = scrapers.USER_AGENT.replace("Chrome/125.0", "Chrome/153.0")


async def fetch_amazon_html(asin):
    """Amazon's server-rendered product HTML needs no browser or image decoding."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=8), follow_redirects=True, max_redirects=5,
        cookies={"i18n-prefs": "INR", "lc-acbin": "en_IN"},
        headers={"User-Agent": CLOUD_USER_AGENT, "Accept-Language": "en-IN,en;q=0.9",
                 "Accept": "text/html,application/xhtml+xml"},
    ) as client:
        response = await client.get(f"https://www.amazon.in/dp/{asin}")
        if response.status_code in (404, 410):
            return scrapers.blank_row(asin, "Not Found")
        if response.status_code != 200:
            return scrapers.blank_row(asin, f"Blocked by Amazon (HTTP {response.status_code})")
        return scrapers.parse_amazon_html(asin, response.text)


async def fetch_flipkart_html(pid):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(25, connect=8), follow_redirects=True, max_redirects=5,
        headers={"User-Agent": CLOUD_USER_AGENT, "Accept-Language": "en-IN,en;q=0.9",
                 "Accept": "text/html,application/xhtml+xml"},
    ) as client:
        response = await client.get(scrapers.FLIPKART_PID_URL.format(pid=pid))
        if response.status_code in (404, 410):
            return scrapers.blank_pid_row(pid, "Not Found")
        if response.status_code != 200:
            return scrapers.blank_pid_row(pid, f"Blocked by Flipkart (HTTP {response.status_code})")
        return scrapers.parse_flipkart_html(pid, response.text)


async def _lightweight_route(route):
    if route.request.resource_type in {"image", "media", "font"}:
        await route.abort()
    else:
        await route.continue_()


async def scrape_cloud_product(kind, value, logs, prefer_browser=False, outcome=None):
    """Bounded recovery per product; a failed browser never poisons the next item."""
    row = None
    best_row = None
    if outcome is not None:
        outcome["method"] = "html"
    if kind == "flipkart_pid":
        for attempt in range(2):
            try:
                row = await fetch_flipkart_html(value)
                if row["Status"] in {"Success", "Not Found"}:
                    return row
                logs.append(f"Flipkart HTML attempt {attempt + 1}: {row['Status']}.")
            except (httpx.HTTPError, OSError) as exc:
                logs.append(f"Flipkart HTML attempt {attempt + 1}: connection failed; retrying.")
                print(f"Flipkart HTML request failed for {value}: {exc}", flush=True)
            if attempt == 0:
                await asyncio.sleep(2)
    if kind == "amazon" and not prefer_browser:
        for attempt in range(2):
            try:
                row = await fetch_amazon_html(value)
                if row["Status"] == "Not Found":
                    return row
                if row["Status"] == "Success":
                    if row.get("Price"):
                        return row
                    best_row = row
                    logs.append("Product HTML found; checking the browser for its price.")
                    break
                logs.append(f"HTML attempt {attempt + 1}: {row['Status']}.")
                if row["Status"].startswith("Blocked"):
                    # An explicit block needs a browser, not another identical HTTP request.
                    print(f"Amazon HTML blocked for {value}: {row['Status']}; switching to browser", flush=True)
                    break
            except (httpx.HTTPError, OSError) as exc:
                logs.append(f"HTML attempt {attempt + 1}: connection failed; retrying.")
                print(f"Amazon HTML request failed for {value}: {exc}", flush=True)
            if attempt == 0:
                await asyncio.sleep(2)

    if outcome is not None:
        outcome["method"] = "browser"
    async with _browser_slot:
        for attempt in range(2):
            browser = None
            try:
                async with async_playwright() as playwright:
                    try:
                        browser = await launch_browser(playwright, headless=True)
                        context = await browser.new_context(
                            locale="en-IN", user_agent=CLOUD_USER_AGENT,
                            java_script_enabled=True, service_workers="block",
                            viewport={"width": 1280, "height": 720},
                        )
                        await context.route("**/*", _lightweight_route)
                        if kind == "amazon":
                            await context.add_cookies([
                                {"name": "i18n-prefs", "value": "INR", "domain": ".amazon.in", "path": "/"},
                                {"name": "lc-acbin", "value": "en_IN", "domain": ".amazon.in", "path": "/"},
                            ])
                        page = await context.new_page()
                        scrape_product = (scrapers.scrape_amazon_product if kind == "amazon"
                                          else scrapers.scrape_flipkart_pid)
                        row = await scrape_product(page, value)
                    finally:
                        if browser:
                            with suppress(Exception):
                                await browser.close()
                if row["Status"] == "Success":
                    if kind != "amazon" or row.get("Price"):
                        return row
                    best_row = row
                    logs.append(f"Browser attempt {attempt + 1} found the product without a price.")
                else:
                    logs.append(f"Browser attempt {attempt + 1} did not return product data.")
                    print(f"Cloud product {kind} {value} attempt {attempt + 1}: {row['Status']}", flush=True)
                if row["Status"] == "Not Found":
                    return row
            except Exception as exc:
                logs.append(f"Browser attempt {attempt + 1} failed; starting a fresh browser.")
                print(f"Cloud browser {kind} {value} attempt {attempt + 1}: {exc}", flush=True)
            if attempt == 0:
                await asyncio.sleep(3)

    blank = scrapers.blank_row if kind == "amazon" else scrapers.blank_pid_row
    if best_row:
        logs.append("Product found, but Amazon did not return a product price.")
        return best_row
    if row and row["Status"].startswith("Blocked"):
        return row
    return blank(value, "Error: Cloud page failed after retries; retry this product.")


class ScrapeRequest(BaseModel):
    kind: Kind
    value: str = Field(min_length=1, max_length=4096)
    prefer_browser: bool = False


class ExportRequest(BaseModel):
    kind: Kind
    rows: list[dict] = Field(max_length=10000)


@router.post("/scrape")
async def scrape(body: ScrapeRequest):
    logs = []
    rows = []
    outcome = {}

    cleaner = scrapers.clean_asin if body.kind == "amazon" else scrapers.clean_pid
    if cleaner(body.value) != body.value:
        raise HTTPException(400, "Invalid product ID.")

    async def run():
        rows.append(await scrape_cloud_product(body.kind, body.value, logs,
                                               prefer_browser=body.prefer_browser, outcome=outcome))

    try:
        await asyncio.wait_for(run(), timeout=240)
    except asyncio.TimeoutError:
        raise HTTPException(504, "This page took too long. Download completed rows and retry this item.")
    except Exception as exc:
        # Runtime failures are distinct from a retailer blocking a product page.
        print(f"Cloud browser failure: {exc}", flush=True)
        raise HTTPException(502, "The cloud browser could not finish this request. Please retry.")
    if rows and rows[0]["Status"] == "Success" and outcome.get("method") == "browser":
        # Recovered attempts are diagnostics, not failed product results.
        price_notes = [message for message in logs if "did not return a product price" in message]
        logs = ["Product scraped successfully using browser.", *price_notes]
    return {"rows": rows, "logs": logs, "method": outcome.get("method", "html")}


@router.post("/export/{format}")
async def export(format: Literal["csv", "xlsx"], body: ExportRequest):
    job = Job(body.kind, len(body.rows), "export")
    job.rows = [{column: row.get(column) for column in job.columns} for row in body.rows]
    content = job.to_csv() if format == "csv" else job.to_excel()
    media = "text/csv; charset=utf-8" if format == "csv" else (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{body.kind}_results.{format}"',
        "Cache-Control": "no-store",
    })
