# Web Scrapper

A small toolkit for pulling product data off Indian e-commerce sites — now with a
local web tool that wraps both scrapers in a browser UI.

**Live docs:** https://pethupraveen.github.io/web-scrapper/

## The web tool

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
playwright install chromium

python -m webapp              # or: run_webtool.bat
```

Then open **http://127.0.0.1:8000**.

Two tabs:

- **Amazon ASINs** — paste ASINs (or `amazon.in/dp/…` links), or upload an
  `.xlsx`/`.csv`; the upload preview tells you how many unique ASINs it found before
  you commit to a run. Tune the delay range, cap the run with *Limit*, and watch rows
  land in the table live. **Stop** ends the run cleanly after the product in flight,
  and everything scraped so far is still downloadable.
- **Flipkart PIDs** — paste PIDs / FSN IDs (or product URLs), or upload a sheet with an
  `FSN_ID` column, and get name, rating, rating count, price, MRP and stock per product.
- **Flipkart models** — give a listing URL; the tool opens the *Compatible Mobiles*
  filter, scrolls the whole list, and returns every model name.

Results download as `.xlsx` or `.csv`. Past runs stay in **Earlier runs** and remain
downloadable until you restart the server.

### Notes

- Runs are **headful by default** — a Chromium window opens and Amazon blocks it far
  less often. Don't close that window mid-run. Tick *Headless browser* only if you
  know your runs are getting through.
- Start the server **without `--reload`**. Reload mode puts uvicorn on a
  `SelectorEventLoop`, which can't spawn the Playwright browser subprocess on Windows.
- Jobs live in memory. Restarting the server drops the history.

## What's in here

| File | What it does |
| --- | --- |
| `webapp/` | The web tool: FastAPI backend (`app.py`), job registry with SSE streaming (`jobs.py`), async scraping cores (`scrapers.py`), and the UI in `static/`. |
| `Amazon_Updated_Script.py` | CLI version: reads ASINs from `input_asins.xlsx`, visits `amazon.in/dp/<ASIN>` with Playwright, writes results to `scraped_amazon_results.xlsx`. |
| `scraper_New.py`, `scraper_updated.py` | The original `requests`-based Flipkart PID scrapers. Superseded by the **Flipkart PIDs** tab — see the note below before trusting their output. |
| `console_snippet.js` | The original paste-into-DevTools Flipkart script, kept for when you'd rather not run the server. `webapp/flipkart_collect.js` is the same logic, driven by Playwright. |

Both Flipkart scripts use **no Flipkart class names**, so they survive their CSS-hash
rotation — they find the list by locating the search input and walking up to the
ancestor that holds the checkboxes.

## Fields collected

| Mode | Columns |
| --- | --- |
| Amazon ASINs | `ASIN`, `Status`, `Title`, `Price`, `Rating`, `ReviewCount`, `Bullets`, `URL` |
| Flipkart PIDs | `PID`, `Status`, `Name`, `Rating`, `RatingCount`, `ReviewCount`, `Price`, `MRP`, `Stock`, `URL` |
| Flipkart models | `model` |

`Status` is `Success`, `Blocked/Not Found` when the page came back without a product
title, or `Error: …`. A blocked row never crashes the run.

## API

The UI is a thin client over a JSON API (interactive docs at `/api/docs`):

| Endpoint | Purpose |
| --- | --- |
| `POST /api/preview` | Count ASINs in an uploaded sheet |
| `POST /api/jobs/amazon` | Start an ASIN run (`asins`, `file`, `headless`, `min_delay`, `max_delay`, `limit`) |
| `POST /api/jobs/flipkart-pids` | Start a PID run (`pids`, `file`, `headless`, `min_delay`, `max_delay`, `limit`) |
| `POST /api/jobs/flipkart` | Start a model run (`url`, `headless`) |
| `GET /api/jobs/{id}/events` | Server-sent events: `log`, `row`, `status`, `end` |
| `POST /api/jobs/{id}/stop` | Ask a run to stop |
| `GET /api/jobs/{id}/download.xlsx` / `.csv` | Export results |

## About the old Flipkart PID scripts

`scraper_updated.py` and `scraper_New.py` produce unreliable data, and the copy of
`output_batch_1.xlsx` in this repo's history shows it — all 239 rows carry the same
product name and rating, and `Rating` equals `Reviews` on every row. Three separate
causes:

1. **The URL was wrong by one character.** They request `/product/p/itm?pid=…`, which
   serves a generic "Itm Store" listing that ignores the pid and renders the same
   product for every id. The working form is `/product/p/itme?pid=…`.
2. **Rating and reviews used the same selector** (`div.css-146c3p1`), so the two columns
   could never differ.
3. **They pin hashed class names** like `v1zwn21k v1zwn26 _1psv1zeb9`, which Flipkart
   rotates — the very thing `console_snippet.js` was written to avoid.

The **Flipkart PIDs** tab fixes all three: correct URL, a real browser so client-side
content renders, and text/layout-based extraction with no class names. It also waits for
the buy box to finish hydrating before reading price and stock, and scopes price to the
buy box so a "Similar Products" carousel price is never misread as this product's.

## Running the CLI script instead

1. Put an `input_asins.xlsx` next to the script with an `ASIN` column.
2. `python Amazon_Updated_Script.py`
3. Results land in `scraped_amazon_results.xlsx`.

## Note

Excel inputs and outputs are gitignored — this repo holds the code, not the data.
Scrape responsibly and within each site's terms of use.
