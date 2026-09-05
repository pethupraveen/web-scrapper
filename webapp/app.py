"""Web tool wrapping the Amazon ASIN scraper and the Flipkart model harvester.

Run it with:  python -m webapp        (or: uvicorn webapp.app:app --reload)
"""

import asyncio
import io
import json

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import scrapers
from .jobs import JOBS, Job, register, run_job

STATIC_DIR = Path(__file__).with_name("static")

app = FastAPI(title="Web Scrapper", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


def asins_from_upload(filename, raw):
    """Pull an ASIN column out of an uploaded .xlsx/.csv, or fall back to col 0."""
    buffer = io.BytesIO(raw)

    if filename.lower().endswith(".csv"):
        df = pd.read_csv(buffer, dtype=str)
    else:
        df = pd.read_excel(buffer, dtype=str)

    column = next(
        (c for c in df.columns if str(c).strip().lower() in {"asin", "asins", "sku"}),
        None,
    )

    if column is None:
        if df.empty or not len(df.columns):
            raise HTTPException(400, "That file had no usable columns.")
        column = df.columns[0]

    seen, asins = set(), []

    for value in df[column].dropna():
        asin = scrapers.clean_asin(value)
        if asin and asin not in seen:
            seen.add(asin)
            asins.append(asin)

    return asins


@app.post("/api/preview")
async def preview(file: UploadFile):
    """Parse an uploaded sheet so the UI can show the ASIN count before running."""
    raw = await file.read()

    try:
        asins = asins_from_upload(file.filename or "", raw)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not read that file: {exc}")

    return {"count": len(asins), "asins": asins}


@app.post("/api/jobs/amazon")
async def start_amazon(
    asins: str = Form(""),
    file: UploadFile | None = None,
    headless: bool = Form(False),
    min_delay: float = Form(2.0),
    max_delay: float = Form(5.0),
    limit: int = Form(0),
):
    collected = scrapers.parse_asin_text(asins)

    if file is not None and file.filename:
        raw = await file.read()
        try:
            collected += asins_from_upload(file.filename, raw)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"Could not read that file: {exc}")

    # De-dupe across the pasted list and the uploaded sheet, keeping order.
    seen, unique = set(), []
    for asin in collected:
        if asin not in seen:
            seen.add(asin)
            unique.append(asin)

    if not unique:
        raise HTTPException(400, "No valid ASINs found. Paste some, or upload a sheet with an ASIN column.")

    if limit > 0:
        unique = unique[:limit]

    if max_delay < min_delay:
        min_delay, max_delay = max_delay, min_delay

    job = register(Job("amazon", len(unique), f"{len(unique)} ASINs"))
    job.task = asyncio.create_task(
        run_job(
            job,
            asins=unique,
            headless=headless,
            min_delay=min_delay,
            max_delay=max_delay,
        )
    )

    return job.summary()


@app.post("/api/jobs/flipkart")
async def start_flipkart(url: str = Form(...), headless: bool = Form(False)):
    url = url.strip()

    if not url.startswith("http") or "flipkart." not in url:
        raise HTTPException(400, "Give a full Flipkart listing URL (https://www.flipkart.com/…).")

    job = register(Job("flipkart", 0, url))
    job.task = asyncio.create_task(run_job(job, url=url, headless=headless))

    return job.summary()


def get_job(job_id):
    job = JOBS.get(job_id)

    if job is None:
        raise HTTPException(404, "Unknown job — it may have expired when the server restarted.")

    return job


@app.get("/api/jobs")
async def list_jobs():
    ordered = sorted(JOBS.values(), key=lambda j: j.started_at, reverse=True)
    return [job.summary() for job in ordered]


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str):
    job = get_job(job_id)
    return {"summary": job.summary(), "columns": job.columns, "rows": job.rows, "logs": job.logs}


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: str):
    job = get_job(job_id)
    job.request_stop()
    return job.summary()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    """Server-sent events: replay what already happened, then stream the rest."""
    job = get_job(job_id)
    queue = job.subscribe()

    async def stream():
        try:
            for message in job.logs:
                yield sse("log", {"message": message})

            for row in job.rows:
                yield sse("row", {"row": row, "done": len(job.rows), "total": job.total})

            yield sse("status", job.summary())

            if job.status in {"done", "error", "stopped"}:
                yield sse("end", job.summary())
                return

            while True:
                if await request.is_disconnected():
                    break

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                yield sse(item["event"], item["data"])

                if item["event"] == "end":
                    break

        finally:
            job.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.get("/api/jobs/{job_id}/download.xlsx")
async def download_xlsx(job_id: str):
    job = get_job(job_id)
    name = f"{job.kind}_results_{job.id}.xlsx"

    return Response(
        job.to_excel(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/jobs/{job_id}/download.csv")
async def download_csv(job_id: str):
    job = get_job(job_id)
    name = f"{job.kind}_results_{job.id}.csv"

    return Response(
        job.to_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
