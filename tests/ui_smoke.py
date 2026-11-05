"""Exercise the deployed UI using two fixture rows and real prepare/export APIs."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def product(route):
            body = route.request.post_data_json
            await route.fulfill(json={"rows": [{"ASIN": body["value"], "Status": "Success",
                "Title": "UI verification fixture", "Price": "₹100", "Rating": "4",
                "ReviewCount": "10", "Bullets": "Example", "URL": "https://www.amazon.in/"}], "logs": []})

        await page.route("**/api/cloud/scrape", product)
        await page.goto("https://web-scrapper-umber.vercel.app", wait_until="networkidle")
        assert await page.locator("#form-amazon input[name=headless]").is_disabled()
        await page.locator("textarea[name=asins]").fill("B0CHX1W1XY B09G9BL5CD")
        await page.locator("#form-amazon input[name=min_delay]").fill("0")
        await page.locator("#form-amazon input[name=max_delay]").fill("0")
        await page.locator("#form-amazon button[type=submit]").click()
        await page.wait_for_function("document.querySelector('#conn').textContent === 'done'")
        assert await page.locator("#body tr").count() == 2
        for format in ("csv", "xlsx"):
            async with page.expect_download() as info:
                await page.locator(f"#dl-{format}").click()
            download = await info.value
            assert await download.failure() is None
            print(f"UI download verified: {format}")
        await page.reload(wait_until="networkidle")
        assert await page.locator("#history li").count() == 1
        history = json.loads(await page.evaluate("localStorage.getItem('web-scrapper-runs-v1')"))
        assert history[0]["done"] == 2
        assert not errors, errors
        await page.screenshot(path=str(Path('.vercel') / 'ui-smoke.png'), full_page=True)
        print("UI batch, history persistence, CSV, Excel and JavaScript checks passed")
        await browser.close()


asyncio.run(main())
