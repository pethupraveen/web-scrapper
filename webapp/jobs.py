"""In-memory job registry: one scrape run, its live event queue, and its rows."""

import asyncio
import io
import uuid
from datetime import datetime, timezone

import pandas as pd

from . import scrapers

JOBS = {}
MAX_JOBS = 50


class Job:
    def __init__(self, kind, total, label):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind                      # "amazon" | "flipkart"
        self.label = label
        self.total = total
        self.status = "queued"                # queued | running | done | error | stopped
        self.rows = []
        self.logs = []
        self.error = None
        self.started_at = datetime.now(timezone.utc)
        self.finished_at = None
        self._stop = False
        self._subscribers = []
        self.task = None

    # -- event fan-out ---------------------------------------------------
    def subscribe(self):
        queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue):
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def emit(self, event, data):
        if event == "log":
            self.logs.append(data["message"])
        elif event == "row":
            self.rows.append(data["row"])

        for queue in list(self._subscribers):
            queue.put_nowait({"event": event, "data": data})

    def request_stop(self):
        self._stop = True

    def should_stop(self):
        return self._stop

    # -- exports ---------------------------------------------------------
    @property
    def columns(self):
        return (
            scrapers.AMAZON_COLUMNS if self.kind == "amazon"
            else scrapers.FLIPKART_COLUMNS
        )

    def dataframe(self):
        if not self.rows:
            return pd.DataFrame(columns=self.columns)
        return pd.DataFrame(self.rows)[self.columns]

    def to_excel(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            self.dataframe().to_excel(writer, index=False, sheet_name="results")
        buffer.seek(0)
        return buffer.getvalue()

    def to_csv(self):
        return self.dataframe().to_csv(index=False).encode("utf-8-sig")

    def summary(self):
        succeeded = sum(1 for r in self.rows if r.get("Status") == "Success")
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "total": self.total,
            "done": len(self.rows),
            "succeeded": succeeded if self.kind == "amazon" else len(self.rows),
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def register(job):
    JOBS[job.id] = job

    # Keep the registry from growing without bound during a long session.
    if len(JOBS) > MAX_JOBS:
        oldest = sorted(JOBS.values(), key=lambda j: j.started_at)
        for stale in oldest[: len(JOBS) - MAX_JOBS]:
            if stale.status in {"done", "error", "stopped"}:
                JOBS.pop(stale.id, None)

    return job


async def run_job(job, **options):
    job.status = "running"
    await job.emit("status", job.summary())

    try:
        if job.kind == "amazon":
            await scrapers.run_amazon(
                options["asins"],
                job.emit,
                headless=options.get("headless", False),
                min_delay=options.get("min_delay", 2.0),
                max_delay=options.get("max_delay", 5.0),
                should_stop=job.should_stop,
            )
        else:
            await scrapers.run_flipkart(
                options["url"],
                job.emit,
                headless=options.get("headless", False),
                should_stop=job.should_stop,
            )

        job.status = "stopped" if job.should_stop() else "done"

    except asyncio.CancelledError:
        job.status = "stopped"
        raise

    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        await job.emit("log", {"message": f"Failed: {exc}"})

    finally:
        job.finished_at = datetime.now(timezone.utc)
        job.total = job.total or len(job.rows)
        await job.emit("status", job.summary())
        await job.emit("end", job.summary())
