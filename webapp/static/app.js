const $ = sel => document.querySelector(sel);

let activeJob = null;
let source = null;

// ── tabs ────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === tab));
    $('#panel-amazon').classList.toggle('hidden', tab.dataset.tab !== 'amazon');
    $('#panel-flipkart').classList.toggle('hidden', tab.dataset.tab !== 'flipkart');
  });
});

// ── file preview ────────────────────────────────────────────────────────
$('#form-amazon').file.addEventListener('change', async event => {
  const file = event.target.files[0];
  const hint = $('#file-hint');

  if (!file) { hint.textContent = 'Looks for an ASIN column; falls back to the first column.'; return; }

  hint.textContent = 'Reading…';
  const body = new FormData();
  body.append('file', file);

  try {
    const res = await fetch('/api/preview', { method: 'POST', body });
    const data = await res.json();
    hint.textContent = res.ok
      ? `${data.count} unique ASIN${data.count === 1 ? '' : 's'} found in ${file.name}.`
      : data.error;
  } catch (err) {
    hint.textContent = `Could not read that file: ${err.message}`;
  }
});

// ── starting a run ──────────────────────────────────────────────────────
$('#form-amazon').addEventListener('submit', e => start(e, '/api/jobs/amazon', '#stop-amazon'));
$('#form-flipkart').addEventListener('submit', e => start(e, '/api/jobs/flipkart', '#stop-flipkart'));

async function start(event, endpoint, stopSel) {
  event.preventDefault();

  const form = event.target;
  const body = new FormData(form);

  // Unchecked checkboxes are simply absent; FastAPI wants an explicit value.
  body.set('headless', form.headless.checked ? 'true' : 'false');

  const submit = form.querySelector('button[type=submit]');
  submit.disabled = true;

  try {
    const res = await fetch(endpoint, { method: 'POST', body });
    const summary = await res.json();

    if (!res.ok) { alert(summary.error || 'Could not start the run.'); return; }

    attach(summary, stopSel);
  } catch (err) {
    alert(`Could not start the run: ${err.message}`);
  } finally {
    submit.disabled = false;
  }
}

// ── live run view ───────────────────────────────────────────────────────
function attach(summary, stopSel) {
  activeJob = summary.id;

  if (source) source.close();

  $('#run').classList.remove('hidden');
  $('#run-title').textContent =
    summary.kind === 'amazon' ? `Amazon — ${summary.label}` : 'Flipkart — compatible models';
  $('#log').textContent = '';
  $('#body').innerHTML = '';
  $('#bar').style.width = '0%';

  const columns = summary.kind === 'amazon'
    ? ['ASIN', 'Status', 'Title', 'Price', 'Rating', 'ReviewCount', 'Bullets']
    : ['model'];

  $('#head').innerHTML = columns.map(c => `<th>${c}</th>`).join('');

  $('#dl-xlsx').href = `/api/jobs/${summary.id}/download.xlsx`;
  $('#dl-csv').href = `/api/jobs/${summary.id}/download.csv`;
  setDownloads(false);

  const stop = $(stopSel);
  stop.disabled = false;
  stop.onclick = () => {
    stop.disabled = true;
    fetch(`/api/jobs/${summary.id}/stop`, { method: 'POST' });
    log('Stop requested — finishing the product in flight…');
  };

  setStatus(summary);
  $('#run').scrollIntoView({ behavior: 'smooth', block: 'start' });

  source = new EventSource(`/api/jobs/${summary.id}/events`);

  source.addEventListener('log', e => log(JSON.parse(e.data).message));
  source.addEventListener('status', e => setStatus(JSON.parse(e.data)));
  source.addEventListener('row', e => {
    const { row, done, total } = JSON.parse(e.data);
    addRow(columns, row);
    if (total) $('#bar').style.width = `${Math.round((done / total) * 100)}%`;
  });

  source.addEventListener('end', e => {
    const final = JSON.parse(e.data);
    setStatus(final);
    setDownloads(final.done > 0);
    $('#bar').style.width = '100%';
    stop.disabled = true;
    source.close();
    source = null;
    loadHistory();
  });

  source.onerror = () => {
    if (source && source.readyState === EventSource.CLOSED) {
      log('Lost the connection to the server — reload to reattach.');
    }
  };
}

function setDownloads(enabled) {
  ['#dl-xlsx', '#dl-csv'].forEach(sel => {
    $(sel).setAttribute('aria-disabled', enabled ? 'false' : 'true');
  });
}

function setStatus(summary) {
  const pill = $('#conn');
  pill.textContent = summary.status;
  pill.className = `pill ${summary.status}`;

  const parts = [`${summary.done}${summary.total ? ` / ${summary.total}` : ''} rows`];

  if (summary.kind === 'amazon') parts.push(`${summary.succeeded} succeeded`);
  if (summary.error) parts.push(summary.error);

  $('#run-sub').textContent = parts.join(' · ');
  setDownloads(summary.done > 0);
}

function log(message) {
  const box = $('#log');
  box.textContent += `${message}\n`;
  box.scrollTop = box.scrollHeight;
}

function addRow(columns, row) {
  const tr = document.createElement('tr');

  columns.forEach(column => {
    const td = document.createElement('td');
    const value = row[column];

    if (column === 'Status') {
      td.className = /^Success/.test(value) ? 'st-success'
        : /^Error/.test(value) ? 'st-error' : 'st-blocked';
      td.textContent = value ?? '';
    } else if (column === 'ASIN' && row.URL) {
      const a = document.createElement('a');
      a.href = row.URL; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = value ?? '';
      td.append(a);
    } else {
      td.className = 'clip';
      td.title = value ?? '';
      td.textContent = value ?? '';
    }

    tr.append(td);
  });

  $('#body').append(tr);
}

// ── history ─────────────────────────────────────────────────────────────
async function loadHistory() {
  let jobs = [];

  try {
    jobs = await (await fetch('/api/jobs')).json();
  } catch { return; }

  const past = jobs.filter(j => j.id !== activeJob);
  $('#history-panel').classList.toggle('hidden', past.length === 0);

  $('#history').innerHTML = past.map(job => `
    <li>
      <span>${job.kind === 'amazon' ? 'Amazon' : 'Flipkart'} · ${escapeHtml(job.label)}
        <span class="muted">— ${job.status}, ${job.done} rows</span></span>
      <span>
        <a href="/api/jobs/${job.id}/download.xlsx">xlsx</a> ·
        <a href="/api/jobs/${job.id}/download.csv">csv</a>
      </span>
    </li>`).join('');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

loadHistory();
