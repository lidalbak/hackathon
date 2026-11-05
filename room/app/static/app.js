const $ = (s) => document.querySelector(s);
let state = { tasks: [], transcript: [], drafts: [], recording: { active: false }, live: {} }, signature = '';
let live = { segments: [], partial: '', speaking: false, notice: null }, level = 0, remoteLevel = 0;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const time = (iso) => new Date(iso).toLocaleTimeString('nb-NO', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const clock = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function error(message) { const el = $('#error'); el.textContent = message; el.style.display = 'block'; clearTimeout(error.t); error.t = setTimeout(() => (el.style.display = 'none'), 7000); }

async function act(command, extra = {}) {
  try {
    const response = await fetch('/api/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command, ...extra }) });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    if (result.ignored) error('Ingen støttet produktforespørsel funnet. Prøv «Hvor mange nye kunder bruker produkt X siste 30 dager?»');
    await refresh();
    return result;
  } catch (e) { error(e.message); }
}

async function refresh() {
  try {
    const r = await fetch('/api/state');
    if (!r.ok) throw Error('Serveren svarer ikke');
    state = await r.json();
    state.fetchedAt = performance.now();
    if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) live = state.live || live;
    const next = JSON.stringify(state, (k, v) => (k === 'fetchedAt' || k === 'elapsed_s' ? undefined : v));
    if (next === signature) return renderLive();
    signature = next;
    render();
  } catch (e) { error('Mistet forbindelsen til serveren.'); }
}
let refreshTimer = null;
const refreshSoon = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 80); };

// ---------------------------------------------------------------- live socket
let liveSocket = null;
function connectLive() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/live`);
  liveSocket = ws;
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'state') refreshSoon();
    else if (msg.type === 'live') { live = msg; renderLive(); }
    else if (msg.type === 'level') { remoteLevel = Math.max(0, Math.min(1, (msg.db + 60) / 50)); live.speaking = msg.speaking; renderDevice(); }
  };
  ws.onclose = () => { liveSocket = null; setTimeout(connectLive, 1500); };
}

// ---------------------------------------------------------------- microphone
const mic = new RoomMic({
  onState: (s, detail) => {
    if (s === 'idle' && detail && detail.reason) refreshSoon();
    renderRecorder();
  },
  onLevel: (v) => { level = Math.min(1, v * 4); renderMeter(); },
  onError: (m) => error(m),
});

$('#rec').onclick = async () => {
  const rec = state.recording || {};
  if (mic.active) return mic.stop().then(refresh);
  if (rec.active) return act('stop_recording', { reason: rec.kind === 'simulated' ? 'Demomøtet ble stoppet.' : 'Stoppet fra en annen skjerm.' });
  try { await mic.start(); await refresh(); } catch (e) { error(e.message); renderRecorder(); }
};
$('#demoMeeting').onclick = () => act('start_demo');
window.addEventListener('pagehide', () => { if (mic.active) mic.stop(); });

// ---------------------------------------------------------------- rendering
const names = { code: 'Kode & definisjoner', data: 'Database', presentation: 'Presentasjon' };
const statuses = { queued: 'I KØ', working: 'UNDERSØKER', clarify: 'TRENGER AVKLARING', ready: 'RESULTAT KLART', cancelled: 'AVBRUTT', error: 'FEIL' };
const sources = { 'browser-mic': 'MIKROFON', mic: 'MIKROFON', text: 'TEKST', simulated: 'SIMULERT', 'atech-mic': 'ATECH', 'wav-file': 'LYDFIL' };
const outcomes = {
  pending: ['TOLKER …', 'pending'], ignored: ['INGEN HANDLING', 'muted'], task: ['NY OPPGAVE', 'new'], followup: ['OPPFØLGING', 'new'],
  clarify: ['TRENGER AVKLARING', 'warn'], clarify_answer: ['AVKLARING BESVART', 'new'], duplicate: ['GJENTAKELSE · SLÅTT SAMMEN', 'muted'],
  amend: ['RETTELSE', 'warn'], cancel: ['AVBRUTT', 'warn'], draft: ['GITHUB-UTKAST', 'new'],
};

function render() {
  $('#mode').textContent = state.mode;
  $('#taskCount').textContent = state.tasks.length + ' OPPGAVER';
  renderTranscript();
  renderTasks();
  renderDashboards();
  $('#draftSection').hidden = !state.drafts.length;
  $('#drafts').innerHTML = state.drafts.map((d) => `<article class="draft"><h3>${esc(d.title)}</h3><pre>${esc(d.body)}</pre><button class="secondary" data-action="download" data-id="${d.task_id}">Last ned issue.md</button><p class="hint">Utkast til gjennomgang. GitHub er ikke koblet til.</p></article>`).join('');
  renderRecorder();
  renderLive();
}

function renderTranscript() {
  const el = $('#transcript');
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.innerHTML = state.transcript.length ? state.transcript.map((t) => {
    const [label, cls] = outcomes[t.outcome?.kind] || outcomes.ignored;
    const why = [t.outcome?.reason, t.outcome?.change].filter(Boolean).join(' · ');
    return `<div class="utterance"><div class="utt-meta"><time>${esc(time(t.at))}</time><span class="src">${sources[t.source] || esc(t.source)}</span>${t.segments > 1 ? `<span class="src">${t.segments} SEGMENTER</span>` : ''}<span class="outcome ${cls}" title="${esc(why)}">${label}</span></div>${esc(t.text)}${t.outcome?.change ? `<div class="utt-note">${esc(t.outcome.change)}</div>` : ''}</div>`;
  }).join('') : '<p class="empty">Start opptak, spill av demomøtet eller skriv et spørsmål.</p>';
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function renderLive() {
  const rec = state.recording || {};
  const text = (live.segments || []).join(' ');
  const show = rec.active && (text || live.partial || live.speaking);
  $('#live').hidden = !show;
  $('#liveFinal').textContent = text;
  $('#livePartial').textContent = live.partial || (live.speaking ? '…' : '');
  renderDevice();
}

function renderTasks() {
  $('#tasks').innerHTML = state.tasks.length ? [...state.tasks].reverse().map((t) => {
    const meta = [t.product ? `Produkt ${t.product}` : 'Produkt ?', t.days ? `${t.days} dager` : 'periode ?',
      t.interpreter === 'OpenAI' ? 'tolket av modell' : 'regelbasert tolkning', sources[t.source] ? 'fra ' + sources[t.source].toLowerCase() : ''].filter(Boolean).join(' · ');
    const notes = (t.notes || []).map((n) => `<li>${esc(n)}</li>`).join('');
    const history = (t.history || []).map((h) => `<li><time>${esc(time(h.at))}</time> ${esc(h.text)}${h.turn ? ` <q>${esc(h.turn)}</q>` : ''}</li>`).join('');
    const clarify = t.status === 'clarify' ? `<p class="hint">Svar muntlig, for eksempel «produkt Y» eller «siste 30 dager», eller velg her.</p><select aria-label="Produkt" id="product-${t.id}">${['X', 'Y', 'Z'].map((p) => `<option ${t.product === p ? 'selected' : ''}>${p}</option>`).join('')}</select><select aria-label="Periode" id="days-${t.id}">${[7, 30, 90].map((d) => `<option value="${d}" ${t.days === d ? 'selected' : ''}>Siste ${d} dager</option>`).join('')}</select><button class="primary" data-action="clarify" data-id="${t.id}">Start undersøkelse</button>` : '';
    return `<article class="task ${t.status}"><div class="task-top"><span class="tag">${statuses[t.status]}</span>${t.mentions > 1 ? `<span class="mentions">NEVNT ${t.mentions} GANGER</span>` : ''}</div><h3>${esc(t.question)}</h3><p class="task-meta">${esc(meta)}</p>${notes ? `<ul class="notes">${notes}</ul>` : ''}<div class="agents">${Object.entries(t.agents).map(([key, value]) => `<span class="agent ${value}">${value === 'done' ? '✓' : value === 'working' ? '◌' : '○'} ${names[key]}</span>`).join('')}</div>${clarify}${t.error ? `<p>${esc(t.error)}</p>` : ''}${t.cancel_reason ? `<p class="hint">${esc(t.cancel_reason)}</p>` : ''}<div class="task-actions">${t.status === 'ready' ? `<button class="primary" data-action="show" data-id="${t.id}">${t.shown ? 'Vis igjen' : 'Vis på skjermen ↗'}</button><button class="secondary" data-action="speak" data-id="${t.id}">Les opp</button>` : ''}${['clarify', 'working', 'queued'].includes(t.status) ? `<button class="secondary" data-action="cancel" data-id="${t.id}">Avbryt</button>` : ''}</div>${history ? `<details class="history"><summary>Historikk (${t.history.length})</summary><ul>${history}</ul></details>` : ''}</article>`;
  }).join('') : '<p class="empty">Ingen oppgaver ennå. Room venter på et spørsmål.</p>';
}

function renderDashboards() {
  const shown = state.tasks.filter((t) => t.shown && t.status === 'ready');
  $('#dashboards').innerHTML = shown.length ? shown.map((t) => {
    const r = t.result, p = r.total ? Math.round(100 * r.active / r.total) : 0;
    const notes = (t.notes || []).map((n) => `<li>${esc(n)}</li>`).join('');
    const timing = t.timings ? `SQL kjørte på ${t.timings.sql_ms} ms · kildefil lest på ${t.timings.code_ms} ms.` : '';
    return `<article class="dashboard"><span class="tag">DEMODATA · PRODUKT ${r.product} · SISTE ${r.days} DAGER</span><h3>${esc(t.summary)}</h3><div class="metrics"><div class="metric"><strong>${r.active}</strong><span>bruker produktet</span></div><div class="metric"><strong>${r.total}</strong><span>nye kunder totalt</span></div><div class="metric"><strong>${p}%</strong><span>andel med bruk</span></div></div><div class="bar" role="img" aria-label="${p} prosent bruker produktet"><span style="width:${p}%"></span></div><p class="legend">Grønn: minst én brukshendelse · Grå: ingen registrert bruk (${r.inactive})</p><p class="hint">Nye eksterne kunder opprettet fra ${esc(r.cutoff)}, med bruk i samme periode. Tall viser sammenheng, ikke årsak. Data er syntetiske og ble opprettet lokalt.</p>${notes ? `<div class="uncertainty"><strong>Tolkning og usikkerhet</strong><ul>${notes}</ul></div>` : ''}<p class="question">Spørsmål fra møtet: «${esc(t.question)}»</p><details><summary>Se SQL, parametere og kildekode</summary><pre>${esc(r.sql)}\n\nParametere: ${esc(JSON.stringify(r.parameters))}\n${esc(timing)}</pre><pre>${esc(t.evidence.path)}\n${esc(t.evidence.source)}</pre></details><button class="secondary" data-action="draft" data-id="${t.id}">Lag GitHub-utkast</button></article>`;
  }).join('') : '<div class="result-placeholder">Når et svar er klart, lyser boksen.<br><strong>Trykk for å gi innsikten plass.</strong></div>';
}

function renderRecorder() {
  const rec = state.recording || {}, stt = state.stt || {}, keep = state.retention || {};
  const mine = mic.active, busy = rec.active && !mine;
  const button = $('#rec');
  button.classList.toggle('on', mine || busy);
  button.disabled = mic.state === 'starting' || mic.state === 'stopping' || (!mine && !busy && !stt.ready);
  button.textContent = mic.state === 'starting' ? 'Starter mikrofon …' : mic.state === 'stopping' ? 'Fullfører siste setning …'
    : mine ? '■ Stopp opptak' : busy ? (rec.kind === 'simulated' ? '■ Stopp demomøtet' : '■ Stopp opptaket') : '● Start opptak';
  $('#demoMeeting').hidden = mine || busy;
  $('#recPill').hidden = !rec.active;
  $('#recPill').firstChild.textContent = rec.kind === 'simulated' ? '▶ DEMOMØTE ' : '● OPPTAK ';
  $('#micState').textContent = rec.active ? (rec.kind === 'simulated' ? '▶ SIMULERT MØTE' : mine ? '● MIKROFON PÅ' : '● OPPTAK PÅ ANNEN SKJERM') : 'MIKROFON AV';
  $('#micState').classList.toggle('on', !!rec.active);
  let status;
  if (mic.state === 'stopping' || rec.state === 'stopping') status = 'Stopper. Venter på siste segment fra transkripsjonen …';
  else if (rec.active) status = `${rec.kind === 'simulated' ? 'Spiller av manus' : 'Lytter'} · ${esc(rec.provider?.label || '')} · ${rec.segments || 0} segmenter`;
  else if (!stt.ready) status = `Opptak utilgjengelig: ${esc(stt.note || 'ingen leverandør')}`;
  else status = `Klar · ${esc(stt.label)}${stt.transcribes ? '' : ' (bare mikrofontest)'}`;
  if (!rec.active && rec.last_stop) status += ` · Sist: ${esc(rec.last_stop.reason)}`;
  if (live.notice && rec.active) status += ` · ${esc(live.notice)}`;
  $('#recStatus').innerHTML = status;
  const audio = rec.kind === 'simulated' ? 'ingen lyd (manus)' : keep.audio === 'disk' ? 'lagres lokalt som WAV (ROOM_STORE_AUDIO=1)' : 'lagres ikke';
  $('#privacy').innerHTML = `<b>Lyd:</b> ${esc(stt.destination || '–')}, ${audio}. <b>Transkripsjon:</b> ${keep.transcript === 'disk' ? 'lagres i data/transcript.json' : 'bare i minnet til serveren stoppes'}. Start bare når alle i rommet vet det.`;
  document.title = rec.active ? `● ${rec.kind === 'simulated' ? 'Demo' : 'Opptak'} · Room` : 'Room · Møtets nysgjerrige medspiller';
  renderMeter();
  renderDevice();
}

function renderMeter() {
  const v = mic.active ? level : (state.recording?.active ? remoteLevel : 0);
  $('#meterFill').style.width = Math.round(v * 100) + '%';
  if (state.recording?.active) renderDevice();
}

function renderDevice() {
  const tasks = state.tasks || [], rec = state.recording || {};
  const pending = tasks.find((t) => t.status === 'clarify'), working = tasks.find((t) => t.status === 'working' || t.status === 'queued');
  const ready = [...tasks].reverse().find((t) => t.status === 'ready' && !t.shown);
  const listening = rec.active;
  const status = pending ? 'clarify' : working ? 'working' : ready ? 'ready' : listening ? 'listening' : '';
  const leds = $('#leds');
  leds.className = 'leds ' + status;
  const v = mic.active ? level : remoteLevel;
  [...leds.children].forEach((i, n) => i.classList.toggle('lit', status === 'listening' && n < Math.round(v * 9)));
  $('#deviceState').textContent = pending ? 'AVKLARING' : working ? 'ARBEIDER' : ready ? 'KLART' : listening ? (rec.kind === 'simulated' ? 'DEMO' : 'LYTTER') : 'STANDBY';
  const lastWords = (live.partial || (live.segments || []).slice(-1)[0] || '').split(' ').slice(-6).join(' ');
  $('#lcd').textContent = pending ? (!pending.product ? 'Hvilket produkt? Si «produkt X, Y eller Z».' : 'Hvilken periode? 7, 30 eller 90 dager?')
    : working ? 'Kode + data undersøkes …' : ready ? 'Svar klart. Trykk for å vise.'
    : listening ? (lastWords ? `«${lastWords}»` : 'Lytter til møtet …') : 'Klar for neste spørsmål';
}

// ---------------------------------------------------------------- inputs
$('#ask').onsubmit = async (e) => {
  e.preventDefault();
  const text = $('#question').value.trim();
  if (!text) return;
  const button = e.submitter; button.disabled = true;
  try { const r = await act('submit', { text }); if (r && !r.ignored) $('#question').value = ''; } finally { button.disabled = false; }
};
$('#demo').onclick = () => { $('#question').value = 'Hvor mange nye kunder bruker produkt X siste 30 dager?'; $('#ask').requestSubmit($('#ask button[type=submit]')); };
$('#clearTranscript').onclick = () => { if (confirm('Slette hele transkripsjonen? Oppgaver og resultater beholdes.')) act('clear_transcript'); };
$('#physical').onclick = () => {
  const t = [...state.tasks].reverse().find((t) => t.status === 'ready' && !t.shown);
  if (t) act('show', { id: t.id }).then(() => $('#results').scrollIntoView({ behavior: 'smooth' }));
  else error('Ingen nye resultater klare.');
};
document.addEventListener('click', async (e) => {
  const b = e.target.closest('[data-action]');
  if (!b) return;
  const { action, id } = b.dataset;
  if (action === 'clarify') return act(action, { id, product: $('#product-' + id).value, days: Number($('#days-' + id).value) });
  if (action === 'speak') {
    if (!('speechSynthesis' in window)) return error('Opplesing støttes ikke.');
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(state.tasks.find((t) => t.id === id).summary);
    u.lang = 'nb-NO';
    // Room must not transcribe its own voice: pause audio while reading aloud.
    mic.paused = true;
    u.onend = u.onerror = () => { mic.paused = false; };
    return speechSynthesis.speak(u);
  }
  if (action === 'download') {
    const d = state.drafts.find((d) => d.task_id === id), url = URL.createObjectURL(new Blob(['# ' + d.title + '\n\n' + d.body], { type: 'text/markdown' })), a = document.createElement('a');
    a.href = url; a.download = 'issue.md'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    return;
  }
  await act(action, { id });
  if (action === 'show') $('#results').scrollIntoView({ behavior: 'smooth' });
});

setInterval(() => {
  const rec = state.recording;
  if (rec && rec.active && state.fetchedAt) $('#recTime').textContent = clock(rec.elapsed_s + (performance.now() - state.fetchedAt) / 1000);
}, 250);
refresh();
connectLive();
setInterval(refresh, 4000);
