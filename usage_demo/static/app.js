const $ = (id) => document.getElementById(id);
const canonical = (value) => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v) ? Object.keys(v).sort().reduce((a, k) => (a[k] = v[k], a), {}) : v);
const bytes = (value) => Uint8Array.from(atob(value), c => c.charCodeAt(0));
const escape = (value) => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let key, keyId, selectedTier = 'Free', identity, projectId, lastEnvelope, verified = 0, activeSession;
const seen = new Set();
const projector = location.pathname === '/live';
if (projector) {
  document.body.classList.add('projector');
  $('headline').textContent = 'Watch the room leave a trace.';
  $('subtitle').textContent = 'Every row is a real API request. This browser checks its signature before marking it verified.';
  $('view-link').textContent = 'Join the experiment'; $('view-link').href = '/';
  $('join-code').hidden = false;
  $('qr-code').src = `/demo/qr.svg?origin=${encodeURIComponent(location.origin)}`;
  $('join-url').textContent = location.host; $('join-url').href = location.origin;
  if (['localhost', '127.0.0.1'].includes(location.hostname)) $('phone-note').textContent = 'Local preview. Phones need the public HTTPS URL.';
}

async function api(path, body, authenticated = false, method) {
  const headers = {'Content-Type':'application/json'};
  if (authenticated && identity) headers.Authorization = `Bearer ${identity.token}`;
  const response = await fetch(path, {method: method || (body ? 'POST' : 'GET'), headers, body: body ? JSON.stringify(body) : undefined});
  let result; try { result = await response.json(); } catch { result = {}; }
  return {response, result};
}

async function verifyEnvelope(envelope) {
  if (!key || !envelope || envelope.key_id !== keyId) return false;
  try { return await crypto.subtle.verify('Ed25519', key, bytes(envelope.signature), new TextEncoder().encode(canonical(envelope.payload))); }
  catch { return false; }
}

async function initialize() {
  try {
    if (!crypto.subtle) throw Error('Open this page over HTTPS or localhost to verify signatures.');
    const {response, result} = await api('/demo/state');
    if (!response.ok) throw Error('The public demo is unavailable.');
    const der = bytes(result.public_key_pem.replace(/-----[^-]+-----/g, '').replace(/\s/g, ''));
    key = await crypto.subtle.importKey('spki', der, 'Ed25519', false, ['verify']);
    keyId = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', der))).map(b => b.toString(16).padStart(2,'0')).join('');
    if (!await verifyEnvelope(result.state)) throw Error('The initial state signature is invalid.');
    await updateState(result.state);
    $('trust-title').textContent = 'Signatures checked in this browser';
    connect();
    if (!projector) {
      try { identity = JSON.parse(sessionStorage.getItem('proofroom-identity')); } catch { identity = null; }
      if (identity) {
        const {response} = await api('/v1/me', null, true);
        if (response.ok) { selectTier(identity.tier); await prepareWorkspace(); showIdentity(); }
        else { identity = null; sessionStorage.removeItem('proofroom-identity'); }
      }
    }
  } catch (error) {
    $('trust-title').textContent = 'Verification unavailable';
    $('trust-description').textContent = error.message;
    $('connection').textContent = 'Not verified'; $('connection').className = 'connection error';
  }
}

function connect() {
  const events = new EventSource('/demo/events');
  let queue = Promise.resolve();
  events.onopen = () => { $('connection').textContent = 'Live'; $('connection').className = 'connection connected'; };
  events.onerror = () => { $('connection').textContent = 'Reconnecting'; $('connection').className = 'connection error'; };
  events.addEventListener('record', (event) => {
    queue = queue.then(() => addRecord(JSON.parse(event.data))).catch(showStreamError);
  });
  events.addEventListener('state', (event) => {
    queue = queue.then(() => updateState(JSON.parse(event.data))).catch(showStreamError);
  });
}

function showStreamError(error) {
  $('connection').textContent = 'Verification failed'; $('connection').className = 'connection error';
  $('evidence-result').className = 'evidence-result bad'; $('evidence-result').textContent = error.message;
}

async function updateState(envelope) {
  if (!await verifyEnvelope(envelope) || envelope.payload.kind !== 'public-demo-state-v1') throw Error('State signature verification failed.');
  const state = envelope.payload;
  document.querySelector('.mode').textContent = state.key_source === 'local-development' ? 'Local development' : 'Attestation pending';
  if (activeSession && state.session_id !== activeSession) { location.reload(); return; }
  activeSession = state.session_id;
  $('request-count').textContent = state.seq.toLocaleString();
  $('scope-count').textContent = state.active_scopes.toLocaleString();
  $('session').textContent = `Session ${state.session_id.slice(0,8)}`;
  for (const tier of ['Free','Pro','Enterprise']) {
    const count = state.tier_counts[tier] || 0, name = tier.toLowerCase();
    $(name + '-count').textContent = count.toLocaleString();
    document.querySelector('.' + name + '-meter').style.width = `${state.seq ? 100 * count / state.seq : 0}%`;
  }
}

async function addRecord(envelope) {
  const record = envelope.payload;
  const valid = await verifyEnvelope(envelope) && record.kind === 'usage-record-v1' && record.session_id === activeSession;
  if (!valid) throw Error('A request signature failed verification.');
  const id = `${record.session_id}/${record.seq}`;
  if (seen.has(id)) return;
  seen.add(id); verified++; $('verified-count').textContent = verified.toLocaleString();
  lastEnvelope = envelope; $('tamper').disabled = false;
  const row = document.createElement('button');
  row.className = 'stream-row' + (identity && record.user_id === identity.user_id ? ' mine' : '');
  row.innerHTML = `<span class="sequence">#${record.seq.toLocaleString()}</span><span class="request-detail"><strong>${escape(record.tier || 'Unauthenticated')}</strong><small>${escape(record.method)} ${escape(record.route)}</small></span><span class="http-status ${record.status >= 400 ? 'error' : ''}">${record.status} <small>${Math.round(record.elapsed_us / 1000)}ms</small></span><span class="verified">✓ Signature valid</span>`;
  row.onclick = () => {
    $('evidence-result').className = 'evidence-result good';
    $('evidence-result').innerHTML = `<h3>Request #${record.seq}: signature verified</h3>The signed record includes its predecessor’s hash, request scope, route, outcome, and trace ID. Hardware attestation is pending.<pre>${escape(JSON.stringify(envelope,null,2))}</pre>`;
  };
  const stream = $('stream'); stream.querySelector('.empty')?.remove(); stream.prepend(row);
  while (stream.children.length > 30) stream.lastElementChild.remove();
}

function selectTier(tier) {
  selectedTier = tier;
  document.querySelectorAll('.tier').forEach(button => {
    const active = button.dataset.tier === tier;
    button.classList.toggle('selected', active); button.setAttribute('aria-pressed', active);
  });
  $('join').textContent = `Join as ${tier}`;
}
document.querySelectorAll('.tier').forEach(button => button.onclick = () => selectTier(button.dataset.tier));

async function prepareWorkspace() {
  let {response, result} = await api('/v1/projects', null, true);
  if (!response.ok) throw Error('Could not load your workspace. Join again to retry.');
  if (result.length) { projectId = result[0].id; return; }
  ({response, result} = await api('/v1/projects', {name:'Live demo workspace'}, true));
  if (!response.ok) throw Error('Could not create your workspace. Try joining again.');
  projectId = result.id;
}

function showIdentity() {
  $('join').hidden = true; document.querySelector('.tiers').hidden = true;
  $('identity').hidden = false; $('identity-tier').textContent = `${identity.tier} participant`;
  $('identity-id').textContent = identity.user_id;
  $('units').max = identity.tier === 'Free' ? 2 : 10;
  $('units').value = 1; $('unit-value').textContent = '1';
  $('run-job').disabled = false; $('browse').disabled = false;
  $('job-result').className = 'job-result'; $('job-result').textContent = 'Your workspace is ready. Run a job and look for your highlighted requests.';
}

$('join').onclick = async () => {
  $('join').disabled = true; $('job-result').textContent = 'Creating your anonymous workspace…';
  try {
    const {response, result} = await api('/demo/join', {tier:selectedTier});
    if (!response.ok) throw Error('Could not join. Try again.');
    identity = result; sessionStorage.setItem('proofroom-identity', JSON.stringify(identity));
    await prepareWorkspace(); showIdentity();
  } catch (error) { $('job-result').className = 'job-result error'; $('job-result').textContent = error.message; }
  finally { $('join').disabled = false; }
};
$('change-tier').onclick = () => {
  identity = null; projectId = null; sessionStorage.removeItem('proofroom-identity');
  $('identity').hidden = true; $('join').hidden = false; document.querySelector('.tiers').hidden = false;
  $('run-job').disabled = true; $('browse').disabled = true;
  $('job-result').textContent = 'Choose a tier to start a new anonymous scope.';
};
$('units').oninput = () => $('unit-value').textContent = $('units').value;
$('run-job').onclick = async () => {
  $('run-job').disabled = true; $('run-job').textContent = 'Running…';
  try {
    const {response, result} = await api('/v1/jobs', {project_id:projectId, units:Number($('units').value)}, true);
    const seq = response.headers.get('x-usage-seq');
    $('job-result').className = `job-result ${response.ok ? 'success' : 'error'}`;
    $('job-result').textContent = response.ok ? `Report complete. Request #${seq} is in the signed journal.` : `${result.detail || 'Request failed'} (${response.status}). This outcome is also recorded as request #${seq || '—'}.`;
  } catch { $('job-result').className = 'job-result error'; $('job-result').textContent = 'Connection lost. Check the live status, then retry.'; }
  finally { $('run-job').disabled = false; $('run-job').textContent = 'Run a job'; }
};
$('browse').onclick = async () => {
  try {
    const {response} = await api('/v1/projects', null, true);
    $('job-result').className = 'job-result';
    $('job-result').textContent = `Workspace request #${response.headers.get('x-usage-seq') || '—'} returned HTTP ${response.status}.`;
  } catch { $('job-result').textContent = 'Could not reach the workspace. Retry when connected.'; }
};

$('tamper').onclick = async () => {
  if (!lastEnvelope) return;
  const altered = structuredClone(lastEnvelope); altered.payload.status = altered.payload.status === 200 ? 503 : 200;
  const accepted = await verifyEnvelope(altered);
  $('evidence-result').className = `evidence-result ${accepted ? 'good' : 'bad'}`;
  $('evidence-result').innerHTML = `<h3>${accepted ? 'Unexpected: altered record accepted' : 'Altered record rejected'}</h3>Changed the HTTP status in a copy of request #${lastEnvelope.payload.seq}, keeping its original signature. Browser verification returned <strong>${accepted}</strong>. The journal itself was not modified.<pre>Original status: ${lastEnvelope.payload.status}\nAltered status:  ${altered.payload.status}\nSignature:       unchanged</pre>`;
};
$('sql-query').onclick = async () => {
  $('sql-query').disabled = true; $('evidence-result').className = 'evidence-result';
  $('evidence-result').textContent = 'Running the fixed SQL query over a committed journal snapshot…';
  try {
    const nonce = crypto.randomUUID();
    const {response, result} = await api('/demo/query', {query_id:'usage_by_tier', nonce});
    if (!response.ok) throw Error(result.detail || 'Could not obtain a SQL receipt.');
    if (!await verifyEnvelope(result) || !await verifyEnvelope(result.payload.checkpoint) || result.payload.nonce !== nonce || result.payload.query_id !== 'usage_by_tier' || result.payload.kind !== 'usage-sql-receipt-v1') throw Error('SQL receipt verification failed.');
    const payload = result.payload;
    $('evidence-result').className = 'evidence-result good';
    const rows = payload.result.map(r => `<tr><td>${escape(r.tier)}</td><td>${r.requests}</td><td>${r.active_users}</td><td>${r.successful_requests}</td><td>${r.server_errors}</td></tr>`).join('');
    $('evidence-result').innerHTML = `<h3>SQL receipt signature verified</h3>Bound to ${payload.checkpoint.payload.record_count.toLocaleString()} requests and this browser’s fresh nonce. Cohorts need at least 3 distinct scopes. Execution is locally signed; hardware attestation is pending.${rows ? `<table class="sql-table"><thead><tr><th>Tier</th><th>Requests</th><th>Scopes</th><th>2xx</th><th>5xx</th></tr></thead><tbody>${rows}</tbody></table>` : '<p>No tier has three active scopes yet. Have three visitors join the same tier, then retry.</p>'}<details><summary>Inspect the signed SQL receipt</summary><pre>${escape(JSON.stringify(result,null,2))}</pre></details>`;
  } catch (error) { $('evidence-result').className = 'evidence-result bad'; $('evidence-result').textContent = error.message; }
  finally { $('sql-query').disabled = false; }
};
initialize();
