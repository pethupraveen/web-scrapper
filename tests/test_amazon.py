import unittest

from webapp.scrapers import parse_amazon_html


class AmazonParserTests(unittest.TestCase):
    def test_navigation_and_recommendation_stars_are_not_product_ratings(self):
        row = parse_amazon_html("B0GL2ZGQLX", '''
            <span id="productTitle">Screen protector</span>
            <span class="a-icon-alt">Previous page</span>
            <div id="recommendations"><span class="a-icon-alt">4.9 out of 5 stars</span></div>
            <span class="a-price"><span class="a-offscreen">₹999</span></span>
        ''')
        self.assertEqual(row["Status"], "Success")
        self.assertIsNone(row["Rating"])
        self.assertIsNone(row["Price"])

    def test_product_rating_price_and_review_count(self):
        row = parse_amazon_html("B0GL2ZGQLX", '''
            <span id="productTitle">Screen protector</span>
            <input id="ASIN" value="B0GL2ZGQLX">
            <div id="acrPopover"><span class="a-icon-alt">4.2 out of 5 stars</span></div>
            <span id="acrCustomerReviewText">123 ratings</span>
            <div id="corePriceDisplay_desktop_feature_div">
              <span class="a-price"><span class="a-offscreen">₹499.00</span></span>
            </div>
        ''')
        self.assertEqual(row["Rating"], "4.2 out of 5 stars")
        self.assertEqual(row["Price"], "₹499.00")
        self.assertEqual(row["ReviewCount"], "123 ratings")

    def test_captcha_is_not_a_product(self):
        row = parse_amazon_html("B0GL2ZGQLX", '<form action="/errors/validateCaptcha"><input id="captchacharacters"></form>')
        self.assertEqual(row["Status"], "Blocked by Amazon")

    def test_a_different_asin_is_never_reported_as_success(self):
        row = parse_amazon_html("B0GL2ZGQLX", '<span id="productTitle">Other</span><input id="ASIN" value="B0GJZPRXF7">')
        self.assertTrue(row["Status"].startswith("Error:"))
