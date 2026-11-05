"""Run with: python -m unittest discover -s tests (requires httpx)."""

import io
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import pandas as pd

from webapp.app import app


class CloudApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_prepare_merges_upload_and_paste_and_applies_limit(self):
        response = await self.client.post("/api/cloud/prepare", data={
            "kind": "amazon", "asins": "B0CHX1W1XY B0CHX1W1XY", "limit": "2",
        }, files={"file": ("asins.csv", b"ASIN\nB0CHX1W1XY\nB09G9BL5CD\nB0BDHWDR12\n", "text/csv")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["values"], ["B0CHX1W1XY", "B09G9BL5CD"])

    async def test_removed_models_finder_is_not_available(self):
        response = await self.client.post("/api/cloud/prepare", data={"kind": "flipkart", "url": "https://www.flipkart.com/search?q=cover"})
        self.assertEqual(response.status_code, 400)
        response = await self.client.post("/api/cloud/scrape", json={"kind": "flipkart", "value": "https://www.flipkart.com/search?q=cover"})
        self.assertEqual(response.status_code, 422)

    async def test_product_request_waits_for_scrape_and_closes_browser(self):
        browser = AsyncMock()
        product = {"ASIN": "B0CHX1W1XY", "Status": "Success", "Title": "Example", "Price": "₹499"}
        with patch("webapp.cloud.launch_browser", AsyncMock(return_value=browser)), \
                patch("webapp.cloud.fetch_amazon_html", AsyncMock(return_value={"Status": "Blocked by Amazon"})), \
                patch("webapp.cloud.asyncio.sleep", AsyncMock()), \
                patch("webapp.cloud.scrapers.scrape_amazon_product", AsyncMock(return_value=product)):
            response = await self.client.post("/api/cloud/scrape", json={
                "kind": "amazon", "value": "B0CHX1W1XY",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], [product])
        browser.close.assert_awaited_once()

    async def test_cloud_avoids_in_memory_job_api(self):
        with patch("webapp.app.CLOUD", True):
            response = await self.client.get("/api/runtime.js")
            self.assertIn("true", response.text)
            response = await self.client.post("/api/jobs/amazon", data={"asins": "B0CHX1W1XY"})
            self.assertEqual(response.status_code, 409)

    async def test_html_product_does_not_launch_chromium(self):
        product = {"ASIN": "B0GL2ZGQLX", "Status": "Success", "Title": "Example", "Price": "₹499"}
        with patch("webapp.cloud.fetch_amazon_html", AsyncMock(return_value=product)), \
                patch("webapp.cloud.launch_browser", AsyncMock()) as launch:
            response = await self.client.post("/api/cloud/scrape", json={"kind": "amazon", "value": "B0GL2ZGQLX"})
        self.assertEqual(response.json()["rows"], [product])
        launch.assert_not_awaited()

    async def test_browser_preference_uses_the_local_scraping_function_without_html_attempts(self):
        browser = AsyncMock()
        product = {"ASIN": "B0GL2ZGQLX", "Status": "Success", "Title": "Example", "Price": "₹499"}
        with patch("webapp.cloud.fetch_amazon_html", AsyncMock()) as html, \
                patch("webapp.cloud.launch_browser", AsyncMock(return_value=browser)), \
                patch("webapp.cloud.scrapers.scrape_amazon_product", AsyncMock(return_value=product)) as scraper:
            response = await self.client.post("/api/cloud/scrape", json={
                "kind": "amazon", "value": "B0GL2ZGQLX", "prefer_browser": True})
        html.assert_not_awaited()
        scraper.assert_awaited_once()
        self.assertTrue(browser.new_context.call_args.kwargs["java_script_enabled"])
        self.assertEqual(response.json()["method"], "browser")
        self.assertEqual(response.json()["rows"], [product])

    async def test_html_503_switches_to_browser_once_and_reports_recovery(self):
        product = {"ASIN": "B0GL2ZGQLX", "Status": "Success", "Title": "Recovered", "Price": "₹499"}
        with patch("webapp.cloud.fetch_amazon_html", AsyncMock(return_value={"Status": "Blocked by Amazon (HTTP 503)"})) as html, \
                patch("webapp.cloud.launch_browser", AsyncMock(return_value=AsyncMock())), \
                patch("webapp.cloud.scrapers.scrape_amazon_product", AsyncMock(return_value=product)):
            response = await self.client.post("/api/cloud/scrape", json={"kind": "amazon", "value": "B0GL2ZGQLX"})
        html.assert_awaited_once()
        self.assertEqual(response.json()["method"], "browser")
        self.assertEqual(response.json()["logs"], ["Product scraped successfully using browser."])

    async def test_flipkart_without_active_offer_does_not_need_a_browser(self):
        product = {"PID": "ACCH2Z7TQKMYGE3P", "Status": "Success", "Name": "iPhone cover",
                   "Price": None, "Stock": "Unavailable", "RatingCount": "695", "ReviewCount": "60"}
        with patch("webapp.cloud.fetch_flipkart_html", AsyncMock(return_value=product)), \
                patch("webapp.cloud.launch_browser", AsyncMock()) as launch:
            response = await self.client.post("/api/cloud/scrape", json={"kind": "flipkart_pid", "value": product["PID"]})
        self.assertEqual(response.json()["rows"], [product])
        launch.assert_not_awaited()

    async def test_crashed_browser_is_closed_and_replaced(self):
        browsers = [AsyncMock(), AsyncMock()]
        product = {"ASIN": "B0GL2ZGQLX", "Status": "Success", "Title": "Recovered", "Price": "₹499"}
        with patch("webapp.cloud.fetch_amazon_html", AsyncMock(return_value={"Status": "Blocked by Amazon"})), \
                patch("webapp.cloud.asyncio.sleep", AsyncMock()), \
                patch("webapp.cloud.launch_browser", AsyncMock(side_effect=browsers)) as launch, \
                patch("webapp.cloud.scrapers.scrape_amazon_product", AsyncMock(side_effect=[
                    {"Status": "Error: Page.goto: Target page, context or browser has been closed"}, product])):
            response = await self.client.post("/api/cloud/scrape", json={"kind": "amazon", "value": "B0GL2ZGQLX"})
        self.assertEqual(response.json()["rows"], [product])
        self.assertEqual(launch.await_count, 2)
        for browser in browsers:
            browser.close.assert_awaited_once()
        self.assertTrue(response.json()["logs"])

    async def test_browser_failure_preserves_product_html(self):
        product = {"ASIN": "B0GL2ZGQLX", "Status": "Success", "Title": "Product without a listed price", "Price": None}
        with patch("webapp.cloud.fetch_amazon_html", AsyncMock(return_value=product)), \
                patch("webapp.cloud.asyncio.sleep", AsyncMock()), \
                patch("webapp.cloud.launch_browser", AsyncMock(side_effect=RuntimeError("Browser crashed"))):
            response = await self.client.post("/api/cloud/scrape", json={"kind": "amazon", "value": "B0GL2ZGQLX"})
        self.assertEqual(response.json()["rows"], [product])
        self.assertIn("did not return a product price", response.json()["logs"][-1])

    async def test_exports_work_without_server_job_history(self):
        row = {"ASIN": "B0CHX1W1XY", "Status": "Success", "Title": "Example ₹"}
        for format in ("csv", "xlsx"):
            response = await self.client.post(f"/api/cloud/export/{format}", json={
                "kind": "amazon", "rows": [row],
            })
            self.assertEqual(response.status_code, 200)
            raw = io.BytesIO(response.content)
            frame = pd.read_csv(raw) if format == "csv" else pd.read_excel(raw)
            self.assertEqual(frame.iloc[0]["Title"], row["Title"])


if __name__ == "__main__":
    unittest.main()
