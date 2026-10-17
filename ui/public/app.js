/* Scope -> Research -> Result flow. SSE with poll fallback. */
const $ = (id) => document.getElementById(id);
let currentJob = null, pollTimer = null, pollFailures = 0;

async function postJSON(url, body, timeoutMs = 180000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctl.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

function scopeError(msg) {
  $('scope-out').innerHTML = `<div class="alert alert-danger">${msg} — check API at <code>/health</code> and server logs.</div>`;
}

function show(step) {
  for (const s of ['scope', 'research', 'result']) {
    $('panel-' + s).classList.toggle('d-none', s !== step);
  }
  document.querySelectorAll('#steps .nav-link').forEach(b =>
    b.classList.toggle('active', b.dataset.step === step));
  if (step === 'research') $('progress').style.width = '30%';
  if (step === 'result') $('progress').style.width = '100%';
}
document.querySelectorAll('#steps .nav-link').forEach(b => b.onclick = () => show(b.dataset.step));

$('btn-scope').onclick = async () => {
  const query = $('query').value.trim();
  if (!query) return;
  const btn = $('btn-scope');
  btn.disabled = true;
  $('scope-out').innerHTML = '<div class="spinner-border"></div><div class="text-muted small mt-1">Contacting scope model — free-tier models can take 1–3 min…</div>';
  try {
    const j = await postJSON('/api/scope', { query });
    if (j.need_clarification) {
      $('scope-out').innerHTML = `<div class="alert alert-warning">${j.question}</div>`;
      $('brief-card').classList.add('d-none');
    } else {
      $('scope-out').innerHTML = `<div class="alert alert-info">${j.verification || 'Brief ready.'}</div>`;
      $('brief-text').textContent = j.research_brief || '';
      $('brief-card').classList.remove('d-none');
    }
  } catch (e) {
    scopeError(e.name === 'AbortError' ? 'Scope request timed out (3 min)' : `Scope request failed: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
};

$('btn-confirm-brief').onclick = async () => {
  const brief = $('brief-text').textContent;
  const r = await fetch('/api/research', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ research_brief: brief }) });
  const { jobId } = await r.json();
  currentJob = jobId;
  show('research');
  $('job-meta').textContent = 'job ' + jobId;
  startPoll(jobId);
};

function renderJob(j) {
  $('job-meta').textContent = `job ${j.jobId} — ${j.status} — iter ${j.iterations || 0}`;
  if (j.status === 'done') {
    clearInterval(pollTimer);
    $('report').innerHTML = window.marked ? marked.parse(j.final_report || '') : (j.final_report || '');
    $('report-raw').textContent = j.final_report || '';
    show('result');
  } else if (j.status === 'error') {
    clearInterval(pollTimer);
    $('job-meta').textContent += ' — ' + (j.error || 'error');
  }
}

function startPoll(jobId) {
  clearInterval(pollTimer);
  pollFailures = 0;
  const poll = async () => {
    try {
      const r = await fetch('/api/research/' + jobId);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      pollFailures = 0;
      renderJob(await r.json());
    } catch (e) {
      pollFailures++;
      $('job-meta').textContent = `job ${jobId} — poll failed (${pollFailures}): ${e.message}`;
      if (pollFailures >= 5) {
        clearInterval(pollTimer);
        $('job-meta').textContent += ' — stopped polling, is the API still up?';
      }
    }
  };
  pollTimer = setInterval(poll, 2000);
  poll();
}

$('btn-cancel').onclick = async () => {
  if (!currentJob) return;
  await fetch('/api/research/' + currentJob, { method: 'DELETE' });
  clearInterval(pollTimer);
};
$('btn-copy').onclick = () => navigator.clipboard.writeText($('report-raw').textContent || '');
$('btn-dl').onclick = () => {
  const blob = new Blob([$('report-raw').textContent || ''], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'report.md'; a.click();
};
