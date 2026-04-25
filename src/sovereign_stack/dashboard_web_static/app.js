// Sovereign Stack web dashboard — vanilla DOM, no framework.
//
// Polls /snapshot.json every POLL_MS. Updates four panels in place:
//   - status line (overall dot + text + updated-at timestamp)
//   - services list (per-endpoint pill + meta)
//   - indicators (honks, halts, listener stale, decisions)
//   - activity feed (rendered from connectivity events; reserved for SSE)
//
// Why polling not SSE by default: SSE works (the server exposes /events),
// but polling is more predictable across proxies/networks and the dashboard
// data changes slowly (every few seconds is plenty). Wire SSE in if you
// have a need for sub-second push.

const POLL_MS = 3000;

const STATUS_DOT = {
  ok: 'dot-ok',
  degraded: 'dot-degraded',
  down: 'dot-down',
  stale: 'dot-stale',
  unknown: 'dot-unknown',
};

const PILL_CLASS = {
  ok: 'pill-ok',
  degraded: 'pill-degraded',
  stale: 'pill-stale',
  down: 'pill-down',
  unknown: 'pill-unknown',
};

const $ = (id) => document.getElementById(id);

function fmtAge(seconds) {
  if (seconds == null) return null;
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function fmtClock(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtRelTime(isoOrEpoch) {
  if (isoOrEpoch == null) return '';
  let t;
  if (typeof isoOrEpoch === 'number') {
    t = isoOrEpoch * (isoOrEpoch < 1e12 ? 1000 : 1);
  } else {
    t = Date.parse(isoOrEpoch);
    if (isNaN(t)) return '';
  }
  const diff = Math.max(0, (Date.now() - t) / 1000);
  if (diff < 60)    return `${Math.round(diff)}s ago`;
  if (diff < 3600)  return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function setOverall(snapshot) {
  const overall = snapshot.connectivity?.overall || 'unknown';
  const dot = $('overall-dot');
  dot.className = 'dot ' + (STATUS_DOT[overall] || 'dot-unknown');
  $('overall-text').textContent = overall;
  $('updated-at').textContent = fmtClock(snapshot.timestamp);
}

function renderServices(snapshot) {
  const ul = $('services');
  const counts = $('counts');
  ul.innerHTML = '';
  counts.innerHTML = '';

  const c = snapshot.connectivity?.counts || {};
  for (const [k, v] of Object.entries(c)) {
    const span = document.createElement('span');
    span.innerHTML = `${k}: <strong>${v}</strong>`;
    counts.appendChild(span);
  }

  const endpoints = snapshot.connectivity?.endpoints || [];
  for (const ep of endpoints) {
    const li = document.createElement('li');
    li.className = 'service';

    const name = document.createElement('span');
    name.className = 'service-name';
    name.textContent = ep.name;

    const pill = document.createElement('span');
    pill.className = `pill ${PILL_CLASS[ep.status] || 'pill-unknown'}`;
    pill.textContent = ep.status || 'unknown';

    const meta = document.createElement('span');
    meta.className = 'service-meta';
    const parts = [];
    if (ep.pid) parts.push(`pid ${ep.pid}`);
    if (ep.http_status != null) parts.push(`http ${ep.http_status}`);
    const age = fmtAge(ep.log_age_seconds);
    if (age) parts.push(`age ${age}`);
    meta.textContent = parts.join('  ·  ') || '—';

    li.append(name, pill, meta);
    ul.appendChild(li);
  }
}

function renderIndicators(snapshot) {
  const ul = $('indicators');
  ul.innerHTML = '';
  const items = [];
  if (snapshot.unacked_honks > 0) {
    items.push({
      cls: 'indicator-warn', icon: '⚠',
      html: `<strong>${snapshot.unacked_honks}</strong> unacked honk${snapshot.unacked_honks === 1 ? '' : 's'}`,
    });
  }
  if (snapshot.halts_count > 0) {
    items.push({
      cls: 'indicator-down', icon: '⛔',
      html: `<strong>${snapshot.halts_count}</strong> halt note${snapshot.halts_count === 1 ? '' : 's'}`,
    });
  }
  if (snapshot.decisions_count > 0) {
    items.push({
      cls: 'indicator-info', icon: '📋',
      html: `<strong>${snapshot.decisions_count}</strong> metabolize decision${snapshot.decisions_count === 1 ? '' : 's'}`,
    });
  }
  if (snapshot.listener_stale) {
    items.push({
      cls: 'indicator-warn', icon: '⏰',
      html: 'listener <strong>stale</strong>',
    });
  }
  if (items.length === 0) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = 'No indicators.';
    ul.appendChild(li);
    return;
  }
  for (const it of items) {
    const li = document.createElement('li');
    li.className = `indicator ${it.cls}`;
    li.innerHTML = `<span class="indicator-icon">${it.icon}</span><span>${it.html}</span>`;
    ul.appendChild(li);
  }
}

function renderFeed(snapshot) {
  // Snapshot endpoint doesn't currently include the activity feed
  // (the feed is per-process state on the server). Render a friendly
  // placeholder if empty; future iteration can wire the SSE stream.
  const ol = $('feed');
  const meta = $('feed-meta');
  const events = snapshot.feed || [];
  ol.innerHTML = '';
  if (events.length === 0) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = 'Watching… (filesystem watcher just started; events appear as the chronicle, daemons, and Nape produce them)';
    ol.appendChild(li);
    meta.textContent = 'idle';
    return;
  }
  meta.textContent = `live · ${events.length} event${events.length === 1 ? '' : 's'}`;
  for (const ev of events) {
    const li = document.createElement('li');
    li.className = `feed-item cat-${(ev.category || '').toLowerCase()}`;
    const t = document.createElement('span');
    t.className = 'feed-time';
    t.textContent = ev.time;
    const c = document.createElement('span');
    c.className = 'feed-cat';
    c.textContent = ev.category;
    const m = document.createElement('span');
    m.className = 'feed-msg';
    m.textContent = ev.message;
    li.append(t, c, m);
    ol.appendChild(li);
  }
}

function renderLatest(snapshot) {
  const container = $('latest');
  container.innerHTML = '';
  const latest = snapshot.latest || {};
  const order = [
    ['insight',     'Insight',     l => l.domain,        l => l.preview],
    ['handoff',     'Handoff',     l => l.thread || l.source_instance, l => l.preview],
    ['open_thread', 'Open thread', l => l.domain,        l => l.preview],
    ['learning',    'Learning',    l => l.applies_to,    l => l.preview],
    ['decision',    'Decision',    l => l.filename,      l => l.preview],
    ['halt',        'Halt',        l => l.filename,      l => l.preview],
    ['honk',        'Honk',        l => `${l.level || ''} ${l.pattern || ''}`.trim(), l => l.preview],
  ];

  for (const [key, label, domainFn, previewFn] of order) {
    const entry = latest[key];
    const card = document.createElement('div');
    card.className = `latest-item latest-${key === 'open_thread' ? 'thread' : key}`;

    const head = document.createElement('div');
    head.className = 'latest-head';
    const type = document.createElement('span');
    type.className = 'latest-type';
    type.textContent = label;
    const when = document.createElement('span');
    when.className = 'latest-when';
    when.textContent = entry ? fmtRelTime(entry.timestamp) : '';
    head.append(type, when);

    card.appendChild(head);

    if (!entry) {
      const empty = document.createElement('div');
      empty.className = 'latest-empty';
      empty.textContent = 'no entries yet';
      card.appendChild(empty);
    } else {
      const dom = document.createElement('div');
      dom.className = 'latest-domain';
      dom.textContent = domainFn(entry) || '';
      const preview = document.createElement('div');
      preview.className = 'latest-preview';
      preview.textContent = previewFn(entry) || '(empty)';
      if (dom.textContent) card.appendChild(dom);
      card.appendChild(preview);
    }

    container.appendChild(card);
  }
}

async function poll() {
  try {
    const r = await fetch('/snapshot.json', { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const snapshot = await r.json();
    setOverall(snapshot);
    renderServices(snapshot);
    renderIndicators(snapshot);
    renderFeed(snapshot);
    renderLatest(snapshot);
    $('poll-status').textContent = `· last poll OK ${fmtClock(Date.now() / 1000)}`;
  } catch (err) {
    $('poll-status').textContent = `· poll error: ${err.message}`;
    $('overall-dot').className = 'dot dot-unknown';
    $('overall-text').textContent = 'unreachable';
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

poll();
