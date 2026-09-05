/* Flipkart "Compatible Mobiles" -> CSV
   ------------------------------------
   1. Open the COMPATIBLE MOBILES filter so the full overlay is showing.
   2. Open DevTools (F12) -> Console.
   3. Paste this whole file, press Enter, wait for it to finish scrolling.

   Uses NO Flipkart class names, so it survives their CSS-hash rotation.
   It finds the list by locating the search input, then walking up to the
   ancestor that actually contains the checkboxes.                        */

(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ---- 1. locate the list container -------------------------------------
  const input = [...document.querySelectorAll('input')].find(i =>
    (i.placeholder || '').toLowerCase().includes('compatible mobile'));

  if (!input) {
    console.error('No "Search Compatible Mobile" input found. Is the filter open?');
    return;
  }

  let root = null, n = input;
  while (n.parentElement) {
    n = n.parentElement;
    if (n.querySelectorAll('[title]').length > 5 ||
        n.querySelectorAll('input[type=checkbox]').length > 5) { root = n; break; }
  }
  if (!root) { console.error('Found the input but not the list container.'); return; }

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
  console.log(`before scrolling: ${found.size}`);

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
  } else {
    console.warn('No horizontal scroller found - list may already be fully mounted.');
  }

  // ---- 4. sort, report, download ----------------------------------------
  const models = [...found].sort((a, b) =>
    a.localeCompare(b, 'en', { sensitivity: 'base' }));

  console.log(`TOTAL: ${models.length} models`);
  console.table(models.slice(0, 10));
  window.__models = models;   // inspect in console if you want

  const csv = 'model\n' + models.map(m => `"${m.replace(/"/g, '""')}"`).join('\n');
  const url = URL.createObjectURL(new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = 'compatible_mobiles.csv'; a.click();
  URL.revokeObjectURL(url);
})();