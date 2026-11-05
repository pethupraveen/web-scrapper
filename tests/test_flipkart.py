import json
import unittest

from webapp.scrapers import parse_flipkart_html

PID = "ACCH2Z7TQKMYGE3P"


def product_page(pid=PID, price=568, mrp=1799, listing=None, reviews=60):
    data = {"multiWidgetState": {
        "pageMeta": {"productId": pid},
        "pageDataResponse": {"pageContext": {"productId": pid, "fdpEventTracking": {
            "events": {"psi": {"fsn": pid,
                "pr": {"rating": 4.1, "ratingsCount": 695, "reviewsCount": reviews},
                "ppd": {"finalPrice": price, "mrp": mrp},
                "pls": listing if listing is not None else {"isAvailable": True},
            }}
        }}},
    }}
    return ('<title>Sprig Back Cover for Apple iPhone 13 - Sprig : Flipkart.com</title>'
            f'<script id="is_script">window.__INITIAL_STATE__ = {json.dumps(data)};</script>'
            '<div>Similar Products</div><div>₹999</div><div>5.0</div>')


class FlipkartParserTests(unittest.TestCase):
    def test_requested_product_fields_are_read_without_carousel_prices(self):
        row = parse_flipkart_html(PID, product_page())
        self.assertEqual(row["Status"], "Success")
        self.assertEqual(row["Price"], "₹568")
        self.assertEqual(row["MRP"], "₹1,799")
        self.assertEqual(row["Rating"], "4.1")
        self.assertEqual(row["RatingCount"], "695")
        self.assertEqual(row["ReviewCount"], "60")

    def test_unavailable_product_still_returns_its_details(self):
        row = parse_flipkart_html(PID, product_page(price=0, mrp=0, listing={"isAvailable": False}))
        self.assertEqual(row["Status"], "Success")
        self.assertIsNone(row["Price"])
        self.assertIsNone(row["MRP"])
        self.assertEqual(row["Stock"], "Unavailable")
        self.assertEqual(row["ReviewCount"], "60")

    def test_location_required_is_not_reported_as_out_of_stock(self):
        row = parse_flipkart_html(PID, product_page(listing={
            "isAvailable": False, "availabilityStatus": "OUT_OF_STOCK", "unserviceabilityReason": "NO_PINCODE"}))
        self.assertEqual(row["Stock"], "Location required")

    def test_mismatched_product_is_rejected(self):
        row = parse_flipkart_html(PID, product_page(pid="ACCGZER9NE9KRY4M"))
        self.assertNotEqual(row["Status"], "Success")
        self.assertIsNone(row["Name"])

    def test_generic_title_is_not_enough_to_claim_product_success(self):
        row = parse_flipkart_html(PID, '<title>Access Denied</title>')
        self.assertNotEqual(row["Status"], "Success")

    def test_json_ld_can_supply_the_matching_product_without_page_state(self):
        data = {"@type": "Product", "sku": PID, "name": "Sprig iPhone case",
                "offers": {"price": "568", "availability": "https://schema.org/InStock"},
                "aggregateRating": {"ratingValue": 4.1, "ratingCount": 695, "reviewCount": 0}}
        row = parse_flipkart_html(PID, f'<script type="application/ld+json">{json.dumps(data)}</script>')
        self.assertEqual(row["Status"], "Success")
        self.assertEqual(row["Price"], "₹568")
        self.assertEqual(row["ReviewCount"], "0")

    def test_unrated_product_has_no_fabricated_rating(self):
        data = {"@type": "Product", "sku": PID, "name": "Unrated case",
                "aggregateRating": {"ratingValue": 0, "ratingCount": 0, "reviewCount": 0}}
        row = parse_flipkart_html(PID, f'<script type="application/ld+json">{json.dumps(data)}</script>')
        self.assertIsNone(row["Rating"])
        self.assertEqual(row["RatingCount"], "0")
