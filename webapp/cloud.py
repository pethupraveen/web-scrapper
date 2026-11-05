"""Stateless request-based scraping for Vercel; the browser owns batch state."""

import asyncio
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright

from . import scrapers
from .browser import launch_browser
from .jobs import Job

router = APIRouter(prefix="/api/cloud")
Kind = Literal["amazon", "flipkart_pid", "flipkart"]


def listing_url(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname in {"flipkart.com", "www.flipkart.com"}
                 and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, "Enter an https://www.flipkart.com/ listing URL.")
    return value


class ScrapeRequest(BaseModel):
    kind: Kind
    value: str = Field(min_length=1, max_length=4096)


class ExportRequest(BaseModel):
    kind: Kind
    rows: list[dict] = Field(max_length=10000)


@router.post("/scrape")
async def scrape(body: ScrapeRequest):
    logs = []
    rows = []

    async def progress(event, data):
        if event == "log":
            logs.append(data["message"])
        elif event == "row":
            rows.append(data["row"])

    if body.kind == "flipkart":
        listing_url(body.value)
    else:
        cleaner = scrapers.clean_asin if body.kind == "amazon" else scrapers.clean_pid
        if cleaner(body.value) != body.value:
            raise HTTPException(400, "Invalid product ID.")

    async def run():
        if body.kind == "flipkart":
            await scrapers.run_flipkart(body.value, progress, headless=True)
        else:
            async with async_playwright() as playwright:
                browser = await launch_browser(playwright, headless=True)
                try:
                    context = await browser.new_context(locale="en-IN", user_agent=scrapers.USER_AGENT)
                    page = await context.new_page()
                    scrape_product = (scrapers.scrape_amazon_product if body.kind == "amazon"
                                      else scrapers.scrape_flipkart_pid)
                    rows.append(await scrape_product(page, body.value))
                finally:
                    await browser.close()

    try:
        await asyncio.wait_for(run(), timeout=240)
    except scrapers.ListingFilterError as exc:
        raise HTTPException(422, str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(504, "This page took too long. Download completed rows and retry this item.")
    except Exception as exc:
        # Runtime failures are distinct from a retailer blocking a product page.
        print(f"Cloud browser failure: {exc}", flush=True)
        raise HTTPException(502, "The cloud browser could not finish this request. Please retry.")
    return {"rows": rows, "logs": logs}


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
