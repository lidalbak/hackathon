'use strict';
const $ = id => document.getElementById(id);
const base = location.pathname.startsWith('/hosting') ? 'hosting' : 'guest';
// Every conversation is shown from this page's side, like the two parties' own inboxes on Airbnb.
const me = base === 'hosting' ? 'host' : 'guest';
const FILTERS = [['all', 'All'], ['unread', 'Unread'], ['support', 'Support']];
const LOCALE = 'en-US';
const wide = () => matchMedia('(min-width: 1128px)').matches;
const twoPane = () => matchMedia('(min-width: 744px)').matches;

const state = { conversations: [], current: null, messages: [], seen: new Set(), filter: 'all', query: '', typing: null };
let typingTimer = null, sending = false, lastTypingSent = 0, live = false, pollTimer = null;

// ---- helpers ----------------------------------------------------------------
async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch('/api' + path, {
    method, credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {}, body: body && JSON.stringify(body),
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const linkify = text => String(text).split(/(https?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)])/g)
  .map((part, i) => i % 2 ? `<a href="${esc(part)}" target="_blank" rel="noopener noreferrer">${esc(part)}</a>` : esc(part)).join('');
const other = role => role === 'guest' ? 'host' : 'guest';
const contact = c => c[other(me)] || {};
const unread = c => c.unread_by?.[me] || 0;
const url = id => `/${base}/messages${id ? '/' + id : ''}`;
const idFromPath = () => Number(location.pathname.match(/\/messages\/(\d+)/)?.[1]) || null;
const lastAt = c => c.last_message?.created_at || c.created_at;

function avatar(person, size) {
  const initial = esc(([...(person?.name || '?')][0] || '?').toUpperCase());
  return person?.avatar
    ? `<img class="avatar s${size}" src="${esc(person.avatar)}" alt="" data-initial="${initial}">`
    : `<span class="avatar s${size}" aria-hidden="true">${initial}</span>`;
}

// ---- dates ------------------------------------------------------------------
const fmt = (d, o) => d.toLocaleString(LOCALE, o);
const time = d => fmt(d, { hour: 'numeric', minute: '2-digit' });
const parseDay = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const startOfDay = d => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const daysAgo = d => Math.round((startOfDay(new Date()) - startOfDay(d)) / 864e5);
const thisYear = d => d.getFullYear() === new Date().getFullYear();

function listDate(d) {
  const ago = daysAgo(d);
  if (ago <= 0) return time(d);
  if (ago === 1) return 'Yesterday';
  if (ago < 7) return fmt(d, { weekday: 'short' });
  return thisYear(d) ? fmt(d, { month: 'short', day: 'numeric' }) : fmt(d, { month: 'numeric', day: 'numeric', year: '2-digit' });
}

function dayLabel(d) {
  const ago = daysAgo(d);
  if (ago === 0) return 'Today';
  if (ago === 1) return 'Yesterday';
  return fmt(d, { weekday: 'short', month: 'short', day: 'numeric', ...(thisYear(d) ? {} : { year: 'numeric' }) });
}

function range(r = {}) {
  if (!r.check_in || !r.check_out) return '';
  const a = parseDay(r.check_in), b = parseDay(r.check_out);
  const sameMonth = a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear();
  const end = sameMonth ? b.getDate() : fmt(b, { month: 'short', day: 'numeric' });
  return `${fmt(a, { month: 'short', day: 'numeric' })} – ${end}${thisYear(b) ? '' : ', ' + b.getFullYear()}`;
}

function statusLabel(c) {
  const r = c.reservation || {}, host = me === 'host', today = startOfDay(new Date());
  switch (r.status) {
    case 'inquiry': return 'Inquiry';
    case 'pending': return host ? 'Request to book' : 'Request pending';
    case 'canceled': return 'Canceled';
    case 'completed': return host ? 'Completed' : 'Past trip';
    case 'confirmed':
      if (r.check_out && parseDay(r.check_out) < today) return host ? 'Completed' : 'Past trip';
      if (r.check_in && parseDay(r.check_in) <= today) return host ? 'Currently hosting' : 'Currently staying';
      return 'Confirmed';
  }
  return '';
}

// ---- state ------------------------------------------------------------------
function upsert(c) {
  const i = state.conversations.findIndex(x => x.id === c.id);
  if (i < 0) state.conversations.push(c); else state.conversations[i] = c;
  state.conversations.sort((a, b) => lastAt(b).localeCompare(lastAt(a)) || b.id - a.id);
  if (state.current?.id === c.id) state.current = c;
}

function visible() {
  const q = state.query.trim().toLowerCase();
  return state.conversations.filter(c => {
    if (state.filter === 'support') return false;
    if (state.filter === 'unread' && !unread(c)) return false;
    return !q || [contact(c).name, c.listing?.title, c.listing?.location, c.last_message?.text]
      .some(v => v && v.toLowerCase().includes(q));
  });
}

// ---- rendering --------------------------------------------------------------
function renderChrome() {
  document.body.classList.toggle('hosting', base === 'hosting');
  const sw = $('switch'), id = state.current?.id;
  sw.textContent = base === 'hosting' ? 'Switch to traveling' : 'Switch to hosting';
  sw.href = `/${base === 'hosting' ? 'guest' : 'hosting'}/messages${id ? '/' + id : ''}`;
  const self = state.conversations[0]?.[me];
  if (self) $('me-avatar').outerHTML = avatar(self, 32).replace('class="avatar', 'id="me-avatar" class="avatar');
}

function renderChips() {
  $('chips').innerHTML = FILTERS.map(([key, label]) =>
    `<button class="chip${state.filter === key ? ' active' : ''}" data-filter="${key}" role="tab" aria-selected="${state.filter === key}">${label}</button>`).join('');
}

function preview(c) {
  const m = c.last_message;
  if (!m) return 'No messages yet';
  const text = m.text.replace(/\s+/g, ' ');
  return m.sender === me ? `You: ${text}` : text;
}

function renderList() {
  const rows = visible();
  if (!rows.length) {
    const [title, body] = state.filter === 'support'
      ? ['No support messages', 'Messages from Airbnb Support will appear here.']
      : state.query ? ['No results', 'Try searching for a name, listing or place.']
        : ["You don't have any messages", 'When you receive a new message, it will appear here.'];
    $('threads').innerHTML = `<li class="list-empty"><h2>${title}</h2><p>${body}</p></li>`;
    return;
  }
  $('threads').innerHTML = rows.map(c => {
    const who = contact(c), when = c.last_message ? listDate(new Date(c.last_message.created_at)) : '';
    const sub = [statusLabel(c), range(c.reservation), c.listing?.location?.split(',')[0]].filter(Boolean).join(' · ');
    const cls = `thread-row${c.id === state.current?.id ? ' active' : ''}${unread(c) ? ' unread' : ''}`;
    return `<li><a class="${cls}" href="${url(c.id)}" data-id="${c.id}">${avatar(who, 48)}
      <span class="row-main">
        <span class="row-top"><span class="row-name">${esc(who.name)}</span><span class="row-date">${when}</span></span>
        <span class="row-preview">${esc(preview(c))}</span>
        <span class="row-sub">${esc(sub)}</span>
      </span></a></li>`;
  }).join('');
}

function renderThread() {
  const c = state.current;
  document.body.classList.toggle('has-thread', !!c);
  $('thread-head').hidden = $('composer').hidden = $('scroller').hidden = !c;
  $('thread-empty').hidden = !!c;
  renderChrome();
  if (!c) { $('details').innerHTML = ''; return; }
  $('thread-name').textContent = contact(c).name;
  $('thread-sub').textContent = [c.listing?.title, range(c.reservation)].filter(Boolean).join(' · ');
  $('details-toggle').textContent = document.body.classList.contains('show-details') ? 'Hide details' : 'Show details';
  renderMessages();
  renderDetails();
}

function renderMessages(forceScroll = false) {
  const c = state.current, sc = $('scroller'), mine = me;
  const nearBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 160;
  const spoken = state.messages.filter(m => m.sender !== 'system');
  const receiptFor = spoken.length && spoken[spoken.length - 1].sender === mine ? spoken[spoken.length - 1] : null;
  let html = '', lastDay = '', group = null;

  const groupHtml = g => {
    const isMine = g.sender === mine, person = c[g.sender] || {};
    const meta = isMine ? `<div class="meta">${g.first ? time(g.first) : ''}</div>`
      : `<div class="meta"><b>${esc(person.name)}</b>${g.first ? time(g.first) : ''}</div>`;
    const bubbles = g.typing ? '<div class="bubble typing-bubble new" aria-label="Typing"><i></i><i></i><i></i></div>'
      : g.items.map(m => `<div class="bubble${state.seen.has(m.id) ? '' : ' new'}" data-id="${m.id}">${linkify(m.text)}</div>`).join('');
    const receipt = receiptFor && g.items.includes(receiptFor)
      ? `<div class="receipt">${(c.read?.[other(mine)] || 0) >= receiptFor.id ? 'Read' : 'Sent'}</div>` : '';
    return `<div class="group${isMine ? ' mine' : ''}">${isMine ? '' : avatar(person, 32)}<div class="stack">${g.typing ? '' : meta}${bubbles}${receipt}</div></div>`;
  };
  const flush = () => { if (group) html += groupHtml(group); group = null; };

  for (const m of state.messages) {
    const d = new Date(m.created_at), day = d.toDateString();
    if (day !== lastDay) { flush(); html += `<div class="day">${dayLabel(d)}</div>`; lastDay = day; }
    if (m.sender === 'system') {
      flush();
      html += `<div class="event"><b>${linkify(m.text)}</b><span>${time(d)}</span></div>`;
      continue;
    }
    if (!group || group.sender !== m.sender || d - group.last > 10 * 60e3) {
      flush();
      group = { sender: m.sender, first: d, last: d, items: [] };
    }
    group.items.push(m);
    group.last = d;
  }
  flush();
  if (state.typing) html += groupHtml({ sender: state.typing, typing: true, items: [] });
  $('messages').innerHTML = html;
  state.messages.forEach(m => state.seen.add(m.id));
  if (forceScroll || nearBottom) sc.scrollTop = sc.scrollHeight;
}

function renderDetails() {
  const c = state.current, r = c.reservation || {}, l = c.listing || {}, who = contact(c), host = me === 'host';
  const day = s => s ? fmt(parseDay(s), { weekday: 'short', month: 'short', day: 'numeric' }) : '—';
  const guests = r.guests ? `${r.guests} guest${r.guests > 1 ? 's' : ''}` : '—';
  const row = (k, v) => `<div class="detail-row"><span>${k}</span><span>${esc(v)}</span></div>`;
  const status = statusLabel(c);
  const request = host && r.status === 'pending'
    ? '<div class="actions"><button class="btn-primary" data-act="accept">Accept</button><button class="btn-secondary" data-act="decline">Decline</button></div>' : '';
  $('details').innerHTML = `
    <div class="details-head"><h2>${host ? 'Reservation' : 'Trip details'}</h2>
      <button class="icon-btn" data-act="close" aria-label="Close"><svg class="icon"><use href="#i-close"/></svg></button></div>
    <div class="details-body">
      ${l.photo ? `<img class="hero" src="${esc(l.photo)}" alt="">` : '<div class="hero"></div>'}
      <h3>${esc(l.title || 'Listing')}</h3>
      <p class="muted">${esc([l.type, l.location].filter(Boolean).join(' · '))}</p>
      ${status ? `<span class="status ${esc(r.status)}">${esc(status)}</span>` : ''}
      ${request}
      <div class="divider"></div>
      <div class="person">${avatar(who, 56)}<div>
        <b>${host ? esc(who.name) : `Hosted by ${esc(who.name)}`}</b>
        <p class="muted">${host ? guests : who.superhost ? 'Superhost' : 'Host'}</p></div></div>
      <div class="divider"></div>
      <div class="facts">
        <div><div class="label">Check-in</div><div class="value">${day(r.check_in)}</div></div>
        <div><div class="label">Checkout</div><div class="value">${day(r.check_out)}</div></div>
        <div class="wide"><div class="label">Guests</div><div class="value">${guests}</div></div>
      </div>
      <div style="margin-top:12px">${r.code ? row('Confirmation code', r.code) : ''}${r.total ? row(host ? 'Payout' : 'Total cost', r.total) : ''}</div>
      <div class="actions"><button class="btn-secondary" data-act="listing">${host ? 'View listing' : 'Show listing'}</button></div>
    </div>`;
}

// ---- actions ----------------------------------------------------------------
async function loadList() {
  state.conversations = [];
  (await api('/conversations')).conversations.forEach(upsert);
  renderList();
  renderChrome();
}

async function open(id, push = true) {
  if (push) history.pushState({}, '', url(id));
  let data;
  try {
    data = await api(`/conversations/${id}`);
  } catch {
    history.replaceState({}, '', url());
    state.current = null; state.messages = [];
    renderList(); renderThread();
    return;
  }
  const { messages, ...c } = data;
  state.current = c; state.messages = messages; state.typing = null;
  state.seen = new Set(messages.map(m => m.id));
  upsert(c);
  renderList(); renderThread(); renderMessages(true);
  markRead();
  if (twoPane()) $('input').focus({ preventScroll: true });
}

function close(push = true) {
  if (push) history.pushState({}, '', url());
  state.current = null; state.messages = [];
  renderList(); renderThread();
}

async function markRead() {
  const c = state.current;
  if (!c || !unread(c) || document.hidden) return;
  try {
    upsert(await api(`/conversations/${c.id}/read`, { method: 'POST', body: { by: me } }));
    renderList();
  } catch { /* the next event or reload catches up */ }
}

async function resync() {
  await loadList();
  const id = state.current?.id;
  if (id) await open(id, false);
}

async function reservationAction(act) {
  const c = state.current;
  const code = 'HM' + Array.from(crypto.getRandomValues(new Uint8Array(8)), b => 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'[b % 32]).join('');
  const [patch, text] = act === 'accept'
    ? [{ reservation: { status: 'confirmed', code: c.reservation?.code || code } }, 'Reservation confirmed']
    : [{ reservation: { status: 'canceled' } }, 'Request declined'];
  upsert(await api(`/conversations/${c.id}`, { method: 'PATCH', body: patch }));
  await api(`/conversations/${c.id}/messages`, { method: 'POST', body: { sender: 'system', text, source: 'ui' } });
  renderThread(); renderList();
}

function setTyping(sender) {
  clearTimeout(typingTimer);
  state.typing = sender;
  if (sender) typingTimer = setTimeout(() => setTyping(null), 8000);
  if (state.current) renderMessages();
}

function notifyTyping(typing) {
  const c = state.current;
  if (!c || (typing && Date.now() - lastTypingSent < 3000) || (!typing && !lastTypingSent)) return;
  lastTypingSent = typing ? Date.now() : 0;
  api(`/conversations/${c.id}/typing`, { method: 'POST', body: { sender: me, typing } }).catch(() => {});
}

// ---- live updates -----------------------------------------------------------
// Server-sent events when they get through; polling when a proxy buffers them (e.g. Cloudflare quick tunnels).
const startPolling = () => { if (!pollTimer) pollTimer = setInterval(poll, 2000); };
const stopPolling = () => { clearInterval(pollTimer); pollTimer = null; };
const checkLive = () => setTimeout(() => { if (!live) startPolling(); }, 4000);

async function poll() {
  try {
    const { conversations } = await api('/conversations');
    const before = JSON.stringify(state.conversations), prev = state.current;
    state.conversations = [];
    conversations.forEach(upsert);
    const changed = before !== JSON.stringify(state.conversations), c = state.current;
    if (prev && !conversations.some(x => x.id === prev.id)) return close(false);
    if (c && c.created_at !== prev.created_at) return open(c.id, false);  // demo was reset
    if (c) {
      const last = state.messages[state.messages.length - 1]?.id || 0;
      const { messages } = await api(`/conversations/${c.id}/messages?after=${last}`);
      const typing = c.typing && c.typing !== me ? c.typing : null;
      messages.forEach(m => { if (!state.messages.some(x => x.id === m.id)) state.messages.push(m); });
      if (messages.length || typing !== state.typing || changed) {
        state.typing = typing;
        renderThread();
        if (messages.some(m => m.sender !== me)) markRead();
      }
    }
    if (changed) renderList();
  } catch { /* try again on the next tick */ }
}

function connect() {
  const events = new EventSource('/api/events');
  let dropped = false;
  const on = (type, fn) => events.addEventListener(type, e => fn(JSON.parse(e.data)));
  events.onerror = () => { dropped = true; live = false; checkLive(); };
  events.onopen = () => { if (dropped) { dropped = false; resync(); } };
  on('hello', () => { live = true; stopPolling(); });
  checkLive();

  on('message.created', ev => {
    upsert(ev.conversation);
    if (state.current?.id === ev.conversation_id) {
      if (!state.messages.some(m => m.id === ev.message.id)) state.messages.push(ev.message);
      if (state.typing === ev.message.sender) { clearTimeout(typingTimer); state.typing = null; }
      renderThread();
      if (ev.message.sender !== me) markRead();
    }
    renderList();
  });
  for (const type of ['conversation.created', 'conversation.updated', 'conversation.read']) {
    on(type, ev => {
      upsert(ev.conversation);
      if (state.current?.id === ev.conversation.id) renderThread();
      renderList();
    });
  }
  on('conversation.deleted', ev => {
    state.conversations = state.conversations.filter(c => c.id !== ev.conversation_id);
    if (state.current?.id === ev.conversation_id) close(); else renderList();
  });
  on('message.deleted', ev => {
    if (state.current?.id === ev.conversation_id) state.messages = state.messages.filter(m => m.id !== ev.message_id);
    resync();
  });
  on('typing', ev => {
    if (state.current?.id === ev.conversation_id && ev.sender !== me) setTyping(ev.typing ? ev.sender : null);
  });
  on('reset', async () => {
    state.current = null;
    await loadList();
    const first = visible()[0];
    if (first && twoPane()) { history.replaceState({}, '', url(first.id)); await open(first.id, false); } else close(false);
  });
}

// ---- wiring -----------------------------------------------------------------
const input = $('input'), send = $('send');
const autosize = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; };
const updateSend = () => { send.disabled = sending || !input.value.trim(); };

input.addEventListener('input', () => {
  autosize(); updateSend();
  notifyTyping(!!input.value.trim());
});
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('composer').requestSubmit(); }
});
input.addEventListener('blur', () => notifyTyping(false));

$('composer').addEventListener('submit', async e => {
  e.preventDefault();
  const c = state.current, text = input.value.trim();
  if (!c || !text || sending) return;
  sending = true; updateSend();
  $('compose-error').hidden = true;
  try {
    const m = await api(`/conversations/${c.id}/messages`, { method: 'POST', body: { sender: me, text, source: 'ui' } });
    lastTypingSent = 0;
    input.value = ''; autosize();
    if (!state.messages.some(x => x.id === m.id)) state.messages.push(m);
    upsert({ ...state.current, last_message: m });
    renderMessages(true); renderList();
  } catch (err) {
    $('compose-error').textContent = `Message not sent: ${err.message}`;
    $('compose-error').hidden = false;
  } finally {
    sending = false; updateSend();
  }
});

$('threads').addEventListener('click', e => {
  const row = e.target.closest('a.thread-row');
  if (!row || e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  open(Number(row.dataset.id));
});
$('chips').addEventListener('click', e => {
  const chip = e.target.closest('[data-filter]');
  if (!chip) return;
  state.filter = chip.dataset.filter;
  renderChips(); renderList();
});
$('search-toggle').addEventListener('click', () => {
  const row = $('search-row');
  row.hidden = !row.hidden;
  if (row.hidden) { state.query = $('search').value = ''; renderList(); } else $('search').focus();
});
$('search').addEventListener('input', e => { state.query = e.target.value; renderList(); });
$('back').addEventListener('click', () => close());
$('details-toggle').addEventListener('click', () => {
  document.body.classList.toggle('show-details');
  renderThread();
});
$('details').addEventListener('click', e => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'close') { document.body.classList.remove('show-details'); renderThread(); }
  if (act === 'accept' || act === 'decline') reservationAction(act).catch(err => alert(err.message));
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !wide() && document.body.classList.contains('show-details')) {
    document.body.classList.remove('show-details'); renderThread();
  }
});
document.addEventListener('error', e => {
  const img = e.target;
  if (img.tagName !== 'IMG') return;
  const stand = document.createElement(img.classList.contains('avatar') ? 'span' : 'div');
  stand.className = img.className;
  stand.textContent = img.dataset.initial || '';
  img.replaceWith(stand);
}, true);
document.addEventListener('visibilitychange', markRead);
window.addEventListener('popstate', () => { const id = idFromPath(); id ? open(id, false) : close(false); });

(async function boot() {
  if (wide()) document.body.classList.add('show-details');
  renderChips();
  await loadList();
  const id = idFromPath(), first = visible()[0];
  if (id) await open(id, false);
  else if (first && twoPane()) { history.replaceState({}, '', url(first.id)); await open(first.id, false); }
  else renderThread();
  connect();
})();
