
import pandas as pd
import time
import random
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

INPUT_FILE = "input_asins.xlsx"
OUTPUT_FILE = "scraped_amazon_results.xlsx"


def clean_asin(value):
    asin = str(value).strip().upper()

    if "." in asin:
        asin = asin.split(".")[0]

    return asin


def scrape_product(page, asin):

    url = f"https://www.amazon.in/dp/{asin}"

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        time.sleep(random.uniform(2, 4))

        html = page.content()

        soup = BeautifulSoup(html, "html.parser")

        title = soup.select_one("#productTitle")

        if not title:
            return {
                "ASIN": asin,
                "Status": "Blocked/Not Found",
                "Title": None,
                "Price": None,
                "Rating": None,
                "ReviewCount": None,
                "Bullets": None,
            }

        title_text = title.get_text(strip=True)

        price = None

        price_selectors = [
            ".priceToPay .a-offscreen",
            ".a-price .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
        ]

        for selector in price_selectors:
            tag = soup.select_one(selector)

            if tag:
                price = tag.get_text(strip=True)
                break

        rating = None

        rating_tag = (
            soup.select_one("#acrPopover .a-icon-alt")
            or soup.select_one("span.a-icon-alt")
        )

        if rating_tag:
            rating = rating_tag.get_text(strip=True)

        review_count = None

        review_tag = (
            soup.select_one("#acrCustomerReviewText")
            or soup.select_one("#acrCustomerReviewLink")
        )

        if review_tag:
            review_count = review_tag.get_text(strip=True)

        bullets = []

        for li in soup.select("#feature-bullets li"):
            text = li.get_text(" ", strip=True)

            if text:
                bullets.append(text)

        return {
            "ASIN": asin,
            "Status": "Success",
            "Title": title_text,
            "Price": price,
            "Rating": rating,
            "ReviewCount": review_count,
            "Bullets": "; ".join(bullets),
        }

    except Exception as e:

        return {
            "ASIN": asin,
            "Status": f"Error: {str(e)}",
            "Title": None,
            "Price": None,
            "Rating": None,
            "ReviewCount": None,
            "Bullets": None,
        }


def main():

    df = pd.read_excel(INPUT_FILE)

    if "ASIN" not in df.columns:
        raise Exception("ASIN column not found")

    asins = (
        df["ASIN"]
        .dropna()
        .apply(clean_asin)
        .unique()
    )

    results = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=False
        )

        context = browser.new_context(
            locale="en-IN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            )
        )

        page = context.new_page()

        print(f"Total ASINs: {len(asins)}")

        for index, asin in enumerate(asins, start=1):

            print(f"[{index}/{len(asins)}] {asin}")

            result = scrape_product(
                page,
                asin
            )

            results.append(result)

            time.sleep(
                random.uniform(2, 5)
            )

        browser.close()

    pd.DataFrame(results).to_excel(
        OUTPUT_FILE,
        index=False
    )

    print(f"Saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()