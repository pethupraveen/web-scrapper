# Web Scrapper

A small toolkit for pulling product data off Indian e-commerce sites.

**Live docs:** https://pethupraveen.github.io/web-scrapper/

## What's in here

| File | What it does |
| --- | --- |
| `Amazon_Updated_Script.py` | Reads ASINs from `input_asins.xlsx`, visits `amazon.in/dp/<ASIN>` with Playwright, and writes title / price / rating / review count / feature bullets to `scraped_amazon_results.xlsx`. |
| `scraper_New.py`, `scraper_updated.py` | Earlier iterations of the Amazon scraper. |
| `console_snippet.js` | Paste-into-DevTools script that scrapes Flipkart's "Compatible Mobiles" filter list and downloads it as CSV. Uses no Flipkart class names, so it survives their CSS-hash rotation. |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
playwright install chromium
```

## Running the Amazon scraper

1. Put an `input_asins.xlsx` next to the script with an `ASIN` column.
2. `python Amazon_Updated_Script.py`
3. Results land in `scraped_amazon_results.xlsx`.

The browser runs headful (`headless=False`) on purpose — it gets blocked far less
often — and sleeps 2–5s between products. Rows that get challenged come back with
`Status = "Blocked/Not Found"` rather than crashing the run.

## Running the Flipkart snippet

Open a Flipkart listing, open the **Compatible Mobiles** filter overlay, then paste
the contents of `console_snippet.js` into the DevTools console. It scrolls the list,
collects every model name, and downloads `compatible_mobiles.csv`.

## Note

Excel inputs and outputs are gitignored — this repo holds the code, not the data.
Scrape responsibly and within each site's terms of use.
