/* Flipkart "Compatible Mobiles" harvester, run via page.evaluate().

   Same approach as console_snippet.js: uses NO Flipkart class names, so it
   survives their CSS-hash rotation. Finds the list by locating the search
   input, then walking up to the ancestor that holds the checkboxes.
   Returns the sorted model names instead of downloading a CSV.            */

async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ---- 1. locate the list container -------------------------------------
  const input = [...document.querySelectorAll('input')].find(i =>
    (i.placeholder || '').toLowerCase().includes('compatible mobile'));

  if (!input) throw new Error('No "Search Compatible Mobile" input found — is the filter overlay open?');

  let root = null, n = input;
  while (n.parentElement) {
    n = n.parentElement;
    if (n.querySelectorAll('[title]').length > 5 ||
        n.querySelectorAll('input[type=checkbox]').length > 5) { root = n; break; }
  }
  if (!root) throw new Error('Found the input but not the list container.');

  // ---- 2. how to read names off whatever is currently mounted -----------
  const found = new Set();
  const NOISE = new Set(['CLEAR ALL', 'APPLY FILTERS', 'Close', 'close']);

  const grab = () => {
    let hits = 0;
    root.querySelectorAll('[title]').forEach(el => {
      const t = (el.getAttribute('title') || '').trim();
      if (t && t.length > 1 && !NOISE.has(t)) { found.add(t); hits++; }
    });
    if (hits) return;
    // fallback: no title attributes -> read the row text beside each checkbox
    root.querySelectorAll('input[type=checkbox]').forEach(cb => {
      let row = cb.closest('label') || cb.parentElement;
      for (let i = 0; i < 4 && row && !row.textContent.trim(); i++) row = row.parentElement;
      if (!row) return;
      const t = row.textContent.replace(/\s+/g, ' ').trim();
      if (t && t.length > 1 && !NOISE.has(t)) found.add(t);
    });
  };

  // ---- 3. find the horizontally scrolling element -----------------------
  let box = null, widest = 0;
  for (const el of [root, ...root.querySelectorAll('*')]) {
    const over = el.scrollWidth - el.clientWidth;
    if (over > 40 && over > widest) { box = el; widest = over; }
  }

  grab();

  if (box) {
    box.scrollLeft = 0;
    await sleep(300);
    for (let i = 0; i < 600; i++) {
      grab();
      box.scrollLeft += Math.max(box.clientWidth - 60, 200);
      await sleep(280);
      if (box.scrollLeft + box.clientWidth >= box.scrollWidth - 5) break;
    }
    grab();
  }

  return [...found].sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' }));
}
