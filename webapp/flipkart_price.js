/* Buy-box price extractor, run via page.evaluate().

   Flipkart rotates CSS hashes, so this keys off layout instead of class
   names. The product's own price renders large (≈27px) and high on the page;
   the "Similar Products" carousel prices are small (14–16px) and far below.
   Taking the first "₹…" in the page text picks up a carousel price whenever
   the buy box is empty, which is exactly the bug this avoids.               */

() => {
  const MONEY = /^\u20b9\s?[\d,]+$/;
  const found = [];

  document.querySelectorAll('*').forEach(el => {
    if (el.children.length) return;                    // leaf nodes only
    const text = (el.innerText || '').trim();
    if (!MONEY.test(text)) return;

    const style = getComputedStyle(el);
    found.push({
      text,
      size: parseFloat(style.fontSize) || 0,
      struck: style.textDecorationLine.includes('line-through'),
      y: el.getBoundingClientRect().top + window.scrollY,
    });
  });

  if (!found.length) return { price: null, mrp: null };

  // The buy-box price is the largest one on the page. A carousel price never
  // reaches 20px, so anything smaller means this product has no price shown
  // (out of stock, or seller not listing one).
  const biggest = found.reduce((a, b) => (b.size > a.size ? b : a));
  if (biggest.size < 20) return { price: null, mrp: null };

  // The struck-through MRP sits on the same line as the price.
  const mrp = found.find(f => f.struck && Math.abs(f.y - biggest.y) < 60);

  return { price: biggest.text, mrp: mrp ? mrp.text : null };
}
