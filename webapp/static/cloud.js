// Hosted batches keep state in this browser and await every product request.
let cloudRun = null;
let cloudStop = false;
const HISTORY_KEY = 'web-scrapper-runs-v1';

function readCloudHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); }
  catch { return []; }
}

function saveCloudRun() {
  const history = readCloudHistory().filter(job => job.id !== cloudRun.id);
  history.unshift(cloudRun);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 20))); }
  catch { log('Browser storage is full or unavailable. Keep this tab open and download your results.'); }
}

function setupCloudUI() {
  document.querySelectorAll('input[name=headless]').forEach(input => {
    input.checked = true;
    input.disabled = true;
  });
  const amazonNote = $('#panel-amazon .note');
  amazonNote.textContent = 'Runs in the cloud using headless Chromium. Keep this tab open during a batch. Retailers may block cloud traffic; blocked pages are reported in the results.';
  $('footer p').textContent = 'Runs in the cloud. Results and the last 20 runs are saved in this browser. Keep the tab open while scraping and download results before clearing browser data.';
  ['csv', 'xlsx'].forEach(format => {
    $(`#dl-${format}`).addEventListener('click', event => {
      event.preventDefault();
      if (cloudRun?.rows.length) downloadCloud(cloudRun, format);
    });
  });
  const unfinished = readCloudHistory().filter(job => job.status === 'running');
  if (unfinished.length) {
    const history = readCloudHistory().map(job => job.status === 'running'
      ? { ...job, status: 'interrupted', error: 'The browser tab closed before the run finished.' } : job);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch { /* Downloads remain available. */ }
  }
}

async function cloudJson(response) {
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`The server returned HTTP ${response.status}. Please retry.`); }
  if (!response.ok) throw new Error(data.error || data.detail || `HTTP ${response.status}`);
  return data;
}

async function startCloud(form, stopSel) {
  if (cloudRun?.status === 'running') {
    alert('Stop the current run before starting another.');
    return;
  }
  const kind = form.id.replace('form-', '');
  const body = new FormData(form);
  body.set('kind', kind);
  const buttons = [...document.querySelectorAll('button[type=submit]')];
  buttons.forEach(button => { button.disabled = true; });
  let stop;
  try {
    const prepared = await cloudJson(await fetch('/api/cloud/prepare', { method: 'POST', body }));
    cloudRun = {
      id: crypto.randomUUID(), kind,
      label: `${prepared.values.length} ${kind === 'amazon' ? 'ASINs' : 'PIDs'}`,
      status: 'running', total: prepared.values.length, done: 0, succeeded: 0,
      rows: [], error: null, started_at: new Date().toISOString(),
    };
    cloudStop = false;
    activeJob = cloudRun.id;
    if (source) { source.close(); source = null; }
    const columns = COLUMNS[kind];
    $('#run').classList.remove('hidden');
    $('#run-title').textContent = `${KIND_LABELS[kind]} — ${cloudRun.label}`;
    $('#log').textContent = '';
    $('#body').innerHTML = '';
    $('#head').innerHTML = columns.map(column => `<th>${column}</th>`).join('');
    $('#bar').style.width = '0%';
    setStatus(cloudRun);
    stop = $(stopSel);
    stop.disabled = false;
    stop.onclick = () => {
      cloudStop = true;
      stop.disabled = true;
      log('Stop requested — finishing the current page.');
    };
    saveCloudRun();
    $('#run').scrollIntoView({ behavior: 'smooth', block: 'start' });
    const min = Math.max(0, Math.min(60, Number(body.get('min_delay')) || 0));
    const max = Math.max(min, Math.min(60, Number(body.get('max_delay')) || min));
    let preferBrowser = false;
    for (const [index, value] of prepared.values.entries()) {
      if (cloudStop) break;
      log(`[${index + 1}/${prepared.values.length}] ${value}`);
      let data;
      try {
        data = await requestCloudProduct(kind, value, preferBrowser);
      } catch (error) {
        log(`Could not fetch ${value}: ${error.message}. Continuing with the next product.`);
        data = { rows: [{ [kind === 'amazon' ? 'ASIN' : 'PID']: value,
          Status: 'Error: Request failed after retries; retry this product.',
          URL: kind === 'amazon' ? `https://www.amazon.in/dp/${value}`
            : `https://www.flipkart.com/product/p/itme?pid=${value}` }], logs: [] };
      }
      data.logs.forEach(log);
      if (kind === 'amazon' && data.method === 'browser') {
        const recovered = data.rows.some(row => row.Status === 'Success');
        if (recovered && !preferBrowser) {
          log('Using browser scraping for the remaining ASINs in this run.');
        }
        preferBrowser = recovered;
      }
      for (const row of data.rows) {
        cloudRun.rows.push(row);
        addRow(columns, row);
      }
      cloudRun.done = cloudRun.rows.length;
      cloudRun.succeeded = cloudRun.rows.filter(row => row.Status === 'Success').length;
      setStatus(cloudRun);
      $('#bar').style.width = `${Math.round((index + 1) / prepared.values.length * 100)}%`;
      saveCloudRun();
      if (index < prepared.values.length - 1 && !cloudStop) {
        const until = Date.now() + (min + Math.random() * (max - min)) * 1000;
        while (!cloudStop && Date.now() < until) await new Promise(resolve => setTimeout(resolve, 200));
      }
    }
    cloudRun.status = cloudStop ? 'stopped' : 'done';
  } catch (error) {
    if (cloudRun?.status === 'running') {
      cloudRun.status = 'error';
      cloudRun.error = error.message;
      log(error.message);
    } else alert(error.message);
  } finally {
    if (cloudRun) {
      cloudRun.finished_at = new Date().toISOString();
      setStatus(cloudRun);
      saveCloudRun();
      loadCloudHistory();
    }
    if (stop) stop.disabled = true;
    buttons.forEach(button => { button.disabled = false; });
  }
}

async function requestCloudProduct(kind, value, preferBrowser = false) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch('/api/cloud/scrape', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, value, prefer_browser: preferBrowser }),
      });
      if (attempt === 0 && [429, 502, 503, 504].includes(response.status)) {
        log(`Temporary server error for ${value}; retrying the request.`);
      } else return await cloudJson(response);
    } catch (error) {
      if (attempt || !(error instanceof TypeError)) throw error;
      log(`Connection interrupted for ${value}; retrying the request.`);
    }
    await new Promise(resolve => setTimeout(resolve, 2500));
  }
}

async function downloadCloud(job, format) {
  try {
    const response = await fetch(`/api/cloud/export/${format}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: job.kind, rows: job.rows }),
    });
    if (!response.ok) { await cloudJson(response); return; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = `${job.kind}_results_${job.id}.${format}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { alert(`Download failed: ${error.message}`); }
}

function loadCloudHistory() {
  const history = readCloudHistory().filter(job => KIND_LABELS[job.kind] && job.id !== activeJob);
  $('#history-panel').classList.toggle('hidden', history.length === 0);
  $('#history').replaceChildren();
  history.forEach(job => {
    const item = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = `${KIND_LABELS[job.kind]} · ${job.label} — ${job.status}, ${job.done} rows`;
    item.append(label);
    const downloads = document.createElement('span');
    ['xlsx', 'csv'].forEach(format => {
      const link = document.createElement('a');
      link.href = '#';
      link.textContent = ` ${format} `;
      link.addEventListener('click', event => { event.preventDefault(); downloadCloud(job, format); });
      downloads.append(link);
    });
    item.append(downloads);
    $('#history').append(item);
  });
}
