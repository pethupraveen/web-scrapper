"""Web tool for Amazon ASIN and Flipkart PID product scraping.

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
from .browser import CLOUD
from .cloud import router as cloud_router
from .jobs import JOBS, Job, register, run_job

STATIC_DIR = Path(__file__).with_name("static")

app = FastAPI(title="Web Scrapper", docs_url="/api/docs", redoc_url=None)
app.include_router(cloud_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/runtime.js", include_in_schema=False)
async def runtime_config():
    return Response(f"window.CLOUD_MODE = {str(CLOUD).lower()};",
                    media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def cloud_jobs_guard(request: Request, call_next):
    if CLOUD and request.url.path.startswith("/api/jobs"):
        return JSONResponse({"error": "Hosted runs use /api/cloud endpoints; batch history is saved in your browser."},
                            status_code=409)
    return await call_next(request)


# Column names each id type answers to, and the cleaner that validates it.
ID_KINDS = {
    "amazon": ({"asin", "asins", "sku"}, scrapers.clean_asin),
    "flipkart_pid": ({"fsn_id", "fsn", "pid", "pids", "product_id"}, scrapers.clean_pid),
}


def ids_from_upload(filename, raw, kind="amazon"):
    """Pull the id column out of an uploaded .xlsx/.csv, or fall back to col 0."""
    names, clean = ID_KINDS[kind]
    buffer = io.BytesIO(raw)

    if filename.lower().endswith(".csv"):
        df = pd.read_csv(buffer, dtype=str)
    else:
        df = pd.read_excel(buffer, dtype=str)

    column = next(
        (c for c in df.columns if str(c).strip().lower() in names),
        None,
    )

    if column is None:
        if df.empty or not len(df.columns):
            raise HTTPException(400, "That file had no usable columns.")
        column = df.columns[0]

    seen, ids = set(), []

    for value in df[column].dropna():
        found = clean(value)
        if found and found not in seen:
            seen.add(found)
            ids.append(found)

    return ids


@app.post("/api/preview")
async def preview(file: UploadFile, kind: str = Form("amazon")):
    """Parse an uploaded sheet so the UI can show the id count before running."""
    if kind not in ID_KINDS:
        raise HTTPException(400, f"Unknown id kind: {kind}")

    raw = await file.read()

    try:
        ids = ids_from_upload(file.filename or "", raw, kind)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not read that file: {exc}")

    return {"count": len(ids), "ids": ids}


async def collect_ids(pasted, file, kind, parse_text):
    """Merge a pasted blob and an uploaded sheet into one ordered, de-duped list."""
    collected = parse_text(pasted)

    if file is not None and file.filename:
        raw = await file.read()
        try:
            collected += ids_from_upload(file.filename, raw, kind)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"Could not read that file: {exc}")

    seen, unique = set(), []

    for found in collected:
        if found not in seen:
            seen.add(found)
            unique.append(found)

    return unique


@app.post("/api/cloud/prepare")
async def prepare_cloud(
    kind: str = Form(...), asins: str = Form(""), pids: str = Form(""),
    file: UploadFile | None = None, limit: int = Form(0, ge=0),
):
    if kind not in ID_KINDS:
        raise HTTPException(400, "Unknown scraper type.")
    pasted, parser = ((asins, scrapers.parse_asin_text) if kind == "amazon"
                      else (pids, scrapers.parse_pid_text))
    ids = await collect_ids(pasted, file, kind, parser)
    if not ids:
        raise HTTPException(400, "No valid product IDs found.")
    ids = ids[:limit] if limit else ids
    if len(ids) > 10000:
        raise HTTPException(400, "Use at most 10,000 products per run.")
    return {"values": ids}


@app.post("/api/jobs/amazon")
async def start_amazon(
    asins: str = Form(""),
    file: UploadFile | None = None,
    headless: bool = Form(False),
    min_delay: float = Form(2.0),
    max_delay: float = Form(5.0),
    limit: int = Form(0),
):
    unique = await collect_ids(asins, file, "amazon", scrapers.parse_asin_text)

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


@app.post("/api/jobs/flipkart-pids")
async def start_flipkart_pids(
    pids: str = Form(""),
    file: UploadFile | None = None,
    headless: bool = Form(False),
    min_delay: float = Form(2.0),
    max_delay: float = Form(5.0),
    limit: int = Form(0),
):
    unique = await collect_ids(pids, file, "flipkart_pid", scrapers.parse_pid_text)

    if not unique:
        raise HTTPException(
            400,
            "No valid PIDs found. Paste some, or upload a sheet with an FSN_ID column.",
        )

    if limit > 0:
        unique = unique[:limit]

    if max_delay < min_delay:
        min_delay, max_delay = max_delay, min_delay

    job = register(Job("flipkart_pid", len(unique), f"{len(unique)} PIDs"))
    job.task = asyncio.create_task(
        run_job(
            job,
            pids=unique,
            headless=headless,
            min_delay=min_delay,
            max_delay=max_delay,
        )
    )

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
