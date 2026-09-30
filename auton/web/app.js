'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const CSRF_KEY = 'auton.csrf';
  const REFRESH_MS = 5000;
  const MAX_DISPLAY_CHARS = 200000;
  let identity = null, csrf = '', jobs = [], endpoints = [], health = null;
  let selected = null, refreshing = false, refreshAgain = false, submitting = false, generation = 0;
  function savedCSRF() { try { return localStorage.getItem(CSRF_KEY) || csrf; } catch (_) { return csrf; } }
  function saveCSRF(value) { csrf = value; try { value ? localStorage.setItem(CSRF_KEY, value) : localStorage.removeItem(CSRF_KEY); } catch (_) {} }
  function notice(message, tone = 'error') {
    $('notice').dataset.tone = tone;
    $('notice').textContent = message; $('notice').hidden = !message;
  }
  function signedOut(message = '') {
    generation++; identity = null; selected = null; jobs = []; endpoints = []; health = null;
    $('console').hidden = true; $('login-panel').hidden = false; $('logout').hidden = true;
    $('principal').textContent = ''; $('jobs').replaceChildren(); $('endpoints').replaceChildren();
    $('stdout').textContent = ''; $('stderr').textContent = ''; $('detail').hidden = true;
    $('run-dialog').close(); $('password').value = ''; saveCSRF(''); notice(message);
  }
  async function request(path, {method = 'GET', body, login = false} = {}) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const headers = {Accept: 'application/json'};
      if (method !== 'GET') { headers['Content-Type'] = 'application/json'; if (!login) headers['X-CSRF-Token'] = savedCSRF(); }
      const response = await fetch(path, {method, headers, credentials: 'same-origin', cache: 'no-store',
        redirect: 'error', signal: controller.signal, body: body === undefined ? undefined : JSON.stringify(body)});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('The daemon returned an unreadable response.'); }
      if (!response.ok) {
        const error = new Error(response.status === 401 ? 'Sign in again: your session is missing, expired or revoked.' :
          response.status === 403 ? 'Your account is not allowed to perform this action.' :
          response.status === 503 ? 'The daemon is unavailable or is refusing this action.' : 'Request rejected (' + response.status + ').');
        error.status = response.status;
        error.notAdmitted = response.headers.get('X-Auton-Admission') === 'not-admitted';
        if (response.status === 401 && !login) signedOut(error.message);
        throw error;
      }
      return data;
    } finally { clearTimeout(timer); }
  }
  function session(data) {
    identity = data; generation++; $('login-panel').hidden = true; $('console').hidden = false;
    $('logout').hidden = false; $('principal').textContent = data.principal;
    $('release').textContent = 'v' + data.version; $('release').hidden = false;
    $('origin').textContent = location.origin; notice('');
    $('maintenance-controls').hidden = !(data.scopes.includes('maintenance') && data.maintenance_operator);
  }
  function state(job) {
    if (job.storage_error) return ['Storage error', 'unknown'];
    if (job.execution_uncertain) return ['Unknown outcome', 'unknown'];
    if (job.outcome === 'job.interrupted') return ['Interrupted', 'failed'];
    if (job.status === 'processing') return ['Running', 'running'];
    if (job.status === 'new') return ['Queued', 'queued'];
    return job.return_code === 0 ? ['Completed', 'completed'] : ['Failed', 'failed'];
  }
  function duration(job) {
    if (job.started_at == null) return '—';
    const seconds = Math.max(0, (job.ended_at == null ? Date.now() / 1000 : job.ended_at) - job.started_at);
    return seconds < 60 ? seconds.toFixed(1) + 's' : Math.floor(seconds / 60) + 'm ' + Math.floor(seconds % 60) + 's';
  }
  function renderJobs() {
    const search = $('search').value.toLowerCase(), filter = $('status-filter').value;
    const rows = jobs.filter(j => (!filter || j.status === filter) && (j.uid + ' ' + j.endpoint).toLowerCase().includes(search));
    rows.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
    $('jobs').replaceChildren(); $('job-count').textContent = String(jobs.length);
    for (const job of rows) {
      const row = document.createElement('tr'), cell = document.createElement('td'), button = document.createElement('button');
      button.textContent = job.uid.slice(job.endpoint.length + 1); button.title = 'Inspect ' + job.uid;
      button.addEventListener('click', () => { selected = job; inspect().catch(error => notice(error.message)); });
      const endpoint = document.createElement('small'); endpoint.textContent = job.endpoint; cell.append(button, endpoint); row.append(cell);
      const statusCell = document.createElement('td'), badge = document.createElement('span'), [label, style] = state(job);
      badge.className = 'badge ' + style; badge.textContent = label; statusCell.append(badge); row.append(statusCell);
      for (const value of [duration(job), job.execution_uncertain ? 'Unknown' : job.return_code == null ? '—' : String(job.return_code)]) {
        const td = document.createElement('td'); td.textContent = value; row.append(td);
      }
      $('jobs').append(row);
    }
    $('empty').hidden = rows.length > 0;
    $('empty').textContent = jobs.length ? 'No jobs match your filters.' : 'No jobs yet. Run an allowed endpoint to get started.';
  }
  function render() {
    const maintenance = health.maintenance.enabled, available = health.status === 'ok';
    $('health-value').textContent = !available ? 'Degraded' : maintenance ? 'Maintenance' : 'Ready';
    $('health-note').textContent = !available ? 'Inspect daemon storage' : maintenance ? 'New jobs are paused' : 'Accepting jobs';
    for (const [key, status] of [['running', 'processing'], ['queued', 'new'], ['complete', 'complete']])
      $(key + '-value').textContent = String(jobs.filter(j => j.status === status).length);
    $('maintenance-banner').hidden = !maintenance;
    $('maintenance-banner').textContent = 'Maintenance is enabled. ' + (health.maintenance.reason || 'New jobs are refused; queued jobs wait.');
    $('maintenance').textContent = maintenance ? 'Disable maintenance' : 'Enable maintenance';
    $('new-job').disabled = !identity.scopes.includes('run') || !health.accepting_jobs || endpoints.length === 0 || submitting;
    $('endpoints').replaceChildren(); $('run-endpoint').replaceChildren();
    const oldEndpoint = selectedRunEndpoint;
    for (const endpoint of endpoints) {
      const item = document.createElement('li'); item.textContent = endpoint.name + (endpoint.description ? ' — ' + endpoint.description : ''); $('endpoints').append(item);
      const option = document.createElement('option'); option.value = endpoint.name; option.textContent = item.textContent; $('run-endpoint').append(option);
    }
    if (endpoints.some(e => e.name === oldEndpoint)) $('run-endpoint').value = oldEndpoint;
    $('endpoint-count').textContent = String(endpoints.length);
    if (!endpoints.length) { const item = document.createElement('li'); item.textContent = 'No permitted endpoints'; $('endpoints').append(item); }
    renderJobs();
  }
  let selectedRunEndpoint = '';
  async function refresh() {
    if (!identity) return;
    if (refreshing) { refreshAgain = true; return; }
    if (!identity.scopes.includes('read')) { notice('The console requires read permission. Ask your administrator to add the read scope.'); return; }
    refreshing = true; $('refresh').disabled = true; const current = generation;
    try {
      const [nextHealth, nextJobs, nextEndpoints] = await Promise.all([request('/health'), request('/jobs'), request('/endpoints')]);
      if (current !== generation) return;
      health = nextHealth; jobs = nextJobs.jobs; endpoints = nextEndpoints.endpoints; render();
      $('updated').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
      if (selected) await inspect();
    } catch (error) {
      if (current === generation) { notice(error.message); $('health-value').textContent = 'Unavailable'; $('new-job').disabled = true; }
    } finally {
      refreshing = false; $('refresh').disabled = false;
      if (refreshAgain) { refreshAgain = false; queueMicrotask(refresh); }
    }
  }
  function output(parts) {
    const value = (parts || []).join('');
    return value.length > MAX_DISPLAY_CHARS ? value.slice(0, MAX_DISPLAY_CHARS) + '\n[Display truncated. Use the API or CLI for retained output.]' : value || '(no output)';
  }
  async function inspect() {
    if (!selected) return;
    const job = selected, current = generation;
    const data = await request('/jobs/' + encodeURIComponent(job.endpoint) + '/' + encodeURIComponent(job.uid.slice(job.endpoint.length + 1)));
    if (current !== generation || selected?.uid !== job.uid) return;
    $('detail').hidden = false; $('detail-title').textContent = data.uid;
    $('detail-meta').textContent = state(data)[0] + ' · ' + duration(data) + ' · Return code ' + (data.return_code ?? 'pending');
    $('stdout').textContent = output(data.stream); $('stderr').textContent = output(data.errors);
    $('detail-warning').hidden = !data.outcome?.startsWith('job.interrupted');
    $('detail-warning').textContent = data.execution_uncertain ? 'The daemon restarted. The actual execution outcome is unknown. Verify the effects before running again.' : 'The daemon restarted before this job launched. It was not executed or replayed.';
  }
  $('login-form').addEventListener('submit', async event => {
    event.preventDefault(); $('login-button').disabled = true; notice('');
    try {
      const result = await request('/ui/auth/login', {method: 'POST', login: true, body: {principal: $('username').value, password: $('password').value}});
      saveCSRF(result.csrf); session(result); await refresh();
    } catch (error) { notice(error.status === 401 ? 'Sign-in failed. Check your credentials or wait before trying again.' : error.message); }
    finally { $('password').value = ''; $('login-button').disabled = false; }
  });
  $('logout').addEventListener('click', async () => {
    $('logout').disabled = true;
    try { await request('/ui/auth/logout', {method: 'POST', body: {}}); signedOut(); }
    catch (error) { notice(error.message); }
    finally { $('logout').disabled = false; }
  });
  $('refresh').addEventListener('click', refresh);
  $('search').addEventListener('input', renderJobs); $('status-filter').addEventListener('change', renderJobs);
  $('close-detail').addEventListener('click', () => { selected = null; $('detail').hidden = true; $('stdout').textContent = ''; $('stderr').textContent = ''; });
  $('new-job').addEventListener('click', () => {
    $('run-confirm').checked = false; $('run-args').value = ''; $('run-preview').textContent = 'Daemon: ' + location.origin;
    $('run-dialog').showModal();
  });
  $('run-endpoint').addEventListener('change', () => { selectedRunEndpoint = $('run-endpoint').value; $('run-confirm').checked = false; });
  $('run-args').addEventListener('input', () => { $('run-confirm').checked = false; });
  $('cancel-run').addEventListener('click', () => $('run-dialog').close());
  $('run-dialog').addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
  $('run-form').addEventListener('submit', async event => {
    event.preventDefault(); if (submitting || !$('run-confirm').checked) return;
    const endpoint = $('run-endpoint').value, args = $('run-args').value ? $('run-args').value.split('\n') : [];
    if (args.length > 64) { $('run-preview').textContent = 'At most 64 arguments are allowed.'; return; }
    const id = crypto.randomUUID(); submitting = true; $('submit-run').disabled = true; $('cancel-run').disabled = true;
    try {
      await request('/run/' + encodeURIComponent(endpoint) + '/' + id, {method: 'POST', body: {args}});
      selected = {endpoint, uid: endpoint + ':' + id}; $('run-dialog').close(); notice('Job submitted: ' + id, 'info'); await refresh();
    } catch (error) {
      $('run-dialog').close();
      const refused = [400, 401, 403, 404, 413].includes(error.status) || error.notAdmitted;
      notice(refused ? error.message : 'Submission outcome unknown for ' + endpoint + ':' + id + '. It was not retried. Refresh the job list and verify before running again.', refused ? 'error' : 'warning');
      if (!refused && identity) { selected = null; await refresh(); }
    } finally { submitting = false; $('submit-run').disabled = false; $('cancel-run').disabled = false; if (identity && health) render(); }
  });
  $('maintenance').addEventListener('click', async () => {
    const enabled = !health?.maintenance.enabled;
    if (!confirm((enabled ? 'Enable' : 'Disable') + ' maintenance on ' + location.origin + '?')) return;
    $('maintenance').disabled = true;
    try { await request('/maintenance', {method: 'POST', body: {enabled, reason: enabled ? 'Operator maintenance' : ''}}); notice(''); await refresh(); }
    catch (error) { notice(error.status ? error.message : 'Maintenance response unavailable. Refresh to inspect the current state; the request was not retried.'); }
    finally { $('maintenance').disabled = false; }
  });
  setInterval(() => { if (!document.hidden && $('auto-refresh').checked) refresh(); }, REFRESH_MS);
  (async () => {
    if (!savedCSRF()) return;
    try { session(await request('/ui/auth/session')); await refresh(); }
    catch (error) { signedOut(error.status === 401 ? '' : error.message); }
  })();
})();
