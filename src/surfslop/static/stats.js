/* The public portal's stats page (tailnet only). Single-series charts in one hue: the title names
   the series, values wear text colours, every bar has a hover readout and a table underneath. */
'use strict';
const app = document.getElementById('app'), tip = document.getElementById('tip');
const HUE = '#3987e5';
function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (v == null || v === false) continue; if (k === 'class') node.className = v; else node.setAttribute(k, v); }
  node.append(...kids.flat().filter(k => k != null && k !== false));
  return node;
}
function svg(tag, attrs, ...kids) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) node.setAttribute(k, v);
  node.append(...kids.flat().filter(Boolean));
  return node;
}
const num = (n) => Number(n).toLocaleString('en-US');
function show(e, words) { tip.textContent = words; tip.hidden = false; tip.style.left = `${Math.min(e.clientX + 12, innerWidth - 180)}px`; tip.style.top = `${e.clientY - 34}px`; }
const tile = (k, v, x) => el('div', { class: 'card tile' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v), el('div', { class: 'x' }, x || ''));
const table = (head, rows) => el('details', {}, el('summary', {}, 'Table'), el('table', {},
  el('thead', {}, el('tr', {}, head.map(h => el('th', {}, h)))), el('tbody', {}, rows.map(r => el('tr', {}, r.map(c => el('td', {}, String(c))))))));

/* Vertical bars: [{label, value}], with a few x labels (indices in `marks`). */
function bars(points, marks, unit) {
  const W = 1000, H = 190, L = 40, R = 6, T = 10, B = 24, n = points.length;
  const top = Math.max(...points.map(p => p.value), 1);
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find(s => top / s <= 4) || 10000;
  const max = Math.ceil(top / step) * step, slot = (W - L - R) / n, y = (v) => T + (1 - v / max) * (H - T - B);
  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${unit}: ${n} bars, highest ${top}` });
  for (let v = 0; v <= max; v += step) g.append(svg('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: '#23314c' }),
    svg('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, num(v)));
  points.forEach((p, i) => {
    const x = L + i * slot + 1, w = Math.max(slot - 2, 1), h = (H - B) - y(p.value), r = Math.min(4, w / 2, h);
    if (p.value > 0) g.append(svg('path', { fill: HUE, d: `M${x},${H - B} v${-(h - r)} q0,${-r} ${r},${-r} h${w - 2 * r} q${r},0 ${r},${r} v${h - r} z` }));
    const hit = svg('rect', { class: 'hit', x: L + i * slot, y: T, width: slot, height: H - T - B });
    hit.addEventListener('pointermove', (e) => show(e, `${p.label}: ${num(p.value)} ${unit}`));
    hit.addEventListener('pointerleave', () => { tip.hidden = true; });
    g.append(hit);
  });
  for (const i of marks) g.append(svg('text', { x: L + (i + .5) * slot, y: H - 7, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }, points[i].label));
  return el('div', { class: 'plot' }, g);
}
function hbars(entries, limit) {
  if (!entries.length) return el('div', { class: 'empty' }, 'Nothing yet.');
  const top = entries[0][1], box = el('div', { class: 'hbars' });
  for (const [name, n] of entries.slice(0, limit)) {
    const bar = el('div', { class: 'bar' }); bar.style.width = `${Math.max(n / top * 100, 1)}%`;
    box.append(el('div', {}, name), el('div', {}, bar), el('div', { class: 'n' }, num(n)));
  }
  return box;
}

async function load() {
  const d = await (await fetch('/stats.json')).json();
  const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  document.getElementById('when').textContent = `updated ${clock(d.generated)}`;
  const minutes = d.minutes.map((v, i) => ({ label: clock(d.generated - (59 - i) * 60), value: v }));
  const byDate = Object.fromEntries(d.daily.map(x => [x.date, x]));
  const days = Array.from({ length: 30 }, (_, i) => {           // always thirty days, empty ones included
    const t = new Date(d.generated * 1000 - (29 - i) * 86400000);
    const date = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
    return { label: t.toLocaleDateString([], { month: 'short', day: 'numeric' }), value: (byDate[date] || {}).visitors || 0 };
  });
  const WHY = { slow_down: 'Too many requests', forbidden: 'Wrong host name', body_not_allowed: 'Request with a body', not_found: 'No such page',
    unknown_spot: 'Unknown spot', cross_site: 'From another site', busy: 'Portal busy', source_unavailable: 'A source was down', internal_error: 'Error here' };
  const title = (id) => id.split('-').map(w => w ? w[0].toUpperCase() + w.slice(1) : w).join(' ');
  const spots = Object.entries(d.month.spots).map(([k, n]) => [title(k), n]);
  const refused = Object.entries(d.month.refused).map(([k, n]) => [WHY[k] || k, n]), ms = Object.entries(d.today.ms);
  const ai = d.month.ai, last = days.length - 1;
  app.replaceChildren(
    el('div', { class: 'tiles' },
      tile('Visitors today', num(d.today.visitors), `${num(d.month.visitors)} in the last 30 days`),
      tile('Requests today', num(d.today.requests), `${num(d.month.requests)} in the last 30 days`),
      tile('Requests a minute', num(d.minutes[59]), `peak ${num(Math.max(...d.minutes))} in the last hour`),
      tile('Overviews written', num(ai.written), `${num(ai.cached)} served ready-made${ai.mean_seconds ? ` · ${ai.mean_seconds} s each` : ''} (30 days)`)),
    el('h2', {}, 'Requests a minute, last hour'),
    el('div', { class: 'card' }, bars(minutes, [0, 15, 30, 45, 59], 'requests'), table(['Minute', 'Requests'], minutes.filter(m => m.value).map(m => [m.label, m.value]))),
    el('h2', {}, 'Visitors a day, last 30 days'),
    el('div', { class: 'card' }, bars(days, [0, 10, 20, last], 'visitors'),
      table(['Day', 'Visitors', 'Requests'], d.daily.map(x => [x.date, x.visitors, x.requests]))),
    el('div', { class: 'two' },
      el('div', {}, el('h2', {}, 'Spots looked up, last 30 days'), el('div', { class: 'card' }, hbars(spots, 12))),
      el('div', {}, el('h2', {}, 'Refused, last 30 days'), el('div', { class: 'card' }, hbars(refused, 12)))),
    el('h2', {}, 'Time to answer, today'),
    el('div', { class: 'card scroll' }, ms.length ? el('table', {}, el('thead', {}, el('tr', {}, ['Kind', 'Answered', 'Mean', 'Slowest'].map(h => el('th', {}, h)))),
      el('tbody', {}, ms.map(([k, v]) => el('tr', {}, el('td', {}, k), el('td', {}, num(v.count)), el('td', {}, `${num(v.mean)} ms`), el('td', {}, `${num(v.worst)} ms`))))) : el('div', { class: 'empty' }, 'Nothing yet.')),
    el('p', { class: 'muted small' }, `A visitor is a network address, kept only as a keyed hash; an IPv6 /64 counts as one. Up since ${new Date(d.up_since * 1000).toLocaleString()}.`));
}
load().catch(e => { app.replaceChildren(el('div', { class: 'card err' }, `Couldn't load the stats: ${e.message}`)); });
setInterval(() => load().catch(() => {}), 30000);
