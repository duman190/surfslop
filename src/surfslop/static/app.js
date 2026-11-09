/* surfslop web page. No framework, no inline styles or scripts (the CSP forbids them); text is
   always set as text, never as HTML. Everything it shows comes from /v1, the API agents use. */
'use strict';

const app = document.getElementById('app');
const foot = document.getElementById('foot');
const TZ = 'America/Los_Angeles';
/* One colour per source, always the same one. The six pass the dataviz validator on this page's
   card surface in dark mode (lightness band, colour-blind and normal-vision separation, contrast). */
const SRC = {
  'surfline': ['Surfline', '#3987e5'], 'surfforecast': ['surf-forecast', '#d95926'], 'nws': ['NWS', '#199e70'],
  'openmeteo-ncep': ['GFS-Wave', '#c98500'], 'openmeteo-ecmwf': ['ECMWF', '#d55181'], 'openmeteo-meteofrance': ['MFWAM', '#008300'],
};
const PERIODS = [['morning', '6-10am'], ['midday', '10am-2pm'], ['afternoon', '2-6pm']];
const state = { spots: null, overview: null, days: Number(localStorage.getItem('days')) || 3, stream: null, again: null, tries: 0 };

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v; else if (k.startsWith('on')) node.addEventListener(k.slice(2), v); else node.setAttribute(k, v);
  }
  node.append(...kids.flat().filter(k => k != null && k !== false));
  return node;
}
function svg(tag, attrs, ...kids) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) node.setAttribute(k, v);
  node.append(...kids.flat().filter(Boolean));
  return node;
}
async function get(url) {
  const r = await fetch(url);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error ? body.error.message : `the portal answered ${r.status}`);
  return body;
}
const fmt = (opts) => new Intl.DateTimeFormat('en-US', { timeZone: TZ, ...opts });
const hourFmt = fmt({ weekday: 'short', hour: 'numeric' });
const clockFmt = fmt({ weekday: 'short', hour: 'numeric', minute: '2-digit' });
function surfText(lo, hi) {
  if (lo == null || hi == null) return '–';
  if (hi < 0.75) return 'flat';
  const a = Math.max(Math.round(lo), 0);
  let b = Math.max(Math.round(hi), a + 1);
  if (b >= 12 && b - a < 3) b = a + 3; else if (b >= 7 && b - a < 2) b = a + 2;
  return `${a}-${b}`;
}
const windText = (w) => w ? `${w.speed} kt ${w.from} · ${w.type}` : 'no wind data';
const ago = (t) => { const m = Math.max(Math.round((Date.now() / 1000 - t) / 60), 0); return m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };
const pills = (day) => el('div', { class: 'pills' }, PERIODS.map(([p, hrs]) => {
  const q = (day.periods[p] || {}).quality || '';
  return el('span', { class: `pill ${q}`, title: `${p} (${hrs}): ${q || 'no data'}` });
}));
const legend = () => el('div', { class: 'legend' },
  ...['clean', 'fair', 'blown'].map(q => el('span', {}, el('span', { class: `pill ${q}` }), q)),
  el('span', {}, 'morning · midday · afternoon'), el('span', {}, 'heights: wave face, ft'));

/* ---------- navigation ---------- */
function go(url) { history.pushState(null, '', url); route(); }
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-nav]');
  if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); go(a.getAttribute('href')); }
});
window.addEventListener('popstate', route);
function route() {
  if (state.stream) { state.stream.close(); state.stream = null; }
  clearTimeout(state.again); state.tries = 0;
  const id = new URLSearchParams(location.search).get('spot');
  window.scrollTo(0, 0);
  (id ? spotPage(id) : home()).catch(fail);
}
function fail(e) { app.replaceChildren(el('div', { class: 'card err' }, `Couldn't load: ${e.message}`)); }

/* ---------- home ---------- */
async function home() {
  document.title = 'surfslop · Bay Area surf';
  const table = el('div', {}, Array.from({ length: 7 }, () => el('div', { class: 'skeleton' })));
  const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Days shown' }, [3, 5, 7].map(n =>
    el('button', { 'aria-pressed': String(state.days === n), onclick: () => { state.days = n; localStorage.setItem('days', n); home(); } }, `${n} days`)));
  const aiBox = el('div', { class: 'card ai' });
  const all = el('div', { class: 'regions' });
  app.replaceChildren(
    el('h1', {}, 'Bay Area surf, Capitola to Ocean Beach'),
    el('p', { class: 'sub' }, 'Several forecasts, fused into one, for the week ahead.'),
    searchBox(),
    el('div', { class: 'bar' }, el('h2', {}, 'The week at your spots'), seg),
    el('div', { class: 'scroll' }, table), legend(),
    el('h2', {}, 'Where to go'), aiBox,
    el('h2', {}, 'All spots, south to north'), all);
  state.spots = state.spots || (await get('/v1/spots')).spots;
  const regions = new Map();
  for (const s of state.spots) regions.set(s.region, [...(regions.get(s.region) || []), s]);
  all.replaceChildren(...[...regions].map(([name, list]) => el('div', { class: 'card' }, el('h3', {}, name),
    el('div', { class: 'chips' }, list.map(s => el('a', { class: `chip${s.featured ? ' star' : ''}`, href: `/?spot=${s.id}`, 'data-nav': '' }, s.name))))));
  const fresh = !state.overview || Date.now() / 1000 - state.overview.generated > 600;
  if (fresh) state.overview = await get('/v1/overview?days=7');
  const ov = state.overview;
  const n = Math.min(state.days, ov.spots[0].days.length);
  const grid = el('div', { class: 'grid' });
  grid.style.gridTemplateColumns = `minmax(112px, 1.3fr) repeat(${n}, minmax(72px, 1fr))`;
  grid.append(el('div', { class: 'head' }, el('div', {}), ov.spots[0].days.slice(0, n).map(d =>
    el('div', {}, d.dow, el('div', { class: 'd' }, String(Number(d.date.slice(8))))))));
  for (const row of ov.spots) {
    grid.append(el('a', { class: 'row', href: `/?spot=${row.spot.id}`, 'data-nav': '' },
      el('div', { class: 'name' }, row.spot.name, el('small', {}, row.spot.region)),
      row.days.slice(0, n).map(d => el('div', { class: 'cell', title: `${d.label}: ${d.surf} ft, ${d.swell || ''}; confidence ${d.confidence}` },
        el('div', { class: 'ht' }, d.surf), pills(d)))));
  }
  table.replaceChildren(grid);
  footer(ov);
  streamAI(aiBox, '/v1/overview/ai', null, 'Local AI · from the fused numbers above');
}

function searchBox() {
  const input = el('input', { type: 'search', placeholder: 'Search a spot: Steamer Lane, OB, Linda Mar…', 'aria-label': 'Search a surf spot', autocomplete: 'off', spellcheck: 'false' });
  const results = el('div', { class: 'results', hidden: '' });
  const norm = (s) => s.toLowerCase().replace(/['’]/g, '').replace(/ñ/g, 'n').replace(/[^a-z0-9]+/g, ' ').trim();
  let hits = [], on = 0;
  const draw = () => {
    results.hidden = !input.value.trim();
    results.replaceChildren(...(hits.length ? hits.map((s, i) => el('a', { href: `/?spot=${s.id}`, 'data-nav': '', class: i === on ? 'on' : '' },
      el('span', {}, s.name, s.aka ? el('span', { class: 'muted small' }, `  ${s.aka}`) : null), el('span', { class: 'muted small' }, s.region)))
      : [el('div', { class: 'none' }, 'No spot by that name between Capitola and Ocean Beach.')]));
  };
  input.addEventListener('input', () => {
    const q = norm(input.value);
    const rank = (s) => { const names = [s.name, ...s.aka.split(',')].map(norm).filter(Boolean);
      return names.includes(q) ? 0 : names.some(n => n.startsWith(q)) ? 1 : names.some(n => n.includes(q)) ? 2 : norm(s.region).includes(q) ? 3 : 9; };
    hits = (state.spots || []).map(s => [rank(s), s]).filter(([r]) => r < 9).sort((a, b) => a[0] - b[0] || b[1].featured - a[1].featured).slice(0, 8).map(([, s]) => s);
    on = 0; draw();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); on = (on + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % Math.max(hits.length, 1); draw(); }
    else if (e.key === 'Enter' && hits[on]) go(`/?spot=${hits[on].id}`);
    else if (e.key === 'Escape') { input.value = ''; draw(); }
  });
  const icon = svg('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'aria-hidden': 'true' },
    svg('circle', { cx: 11, cy: 11, r: 7 }), svg('path', { d: 'M20 20l-3.500-3.500' }));
  return el('div', { class: 'search' }, icon, input, results);
}

function footer(data) {
  const up = data.sources.filter(s => s.ok).map(s => s.label.replace('Open-Meteo · ', ''));
  const down = data.sources.filter(s => !s.ok).map(s => `${s.label} is unavailable`);
  const off = (data.off || []).map(s => `${s} is switched off`);
  foot.replaceChildren(`Fused from ${up.join(', ') || 'no sources'}. `, [...down, ...off].join('. '), el('br'),
    `Updated ${clockFmt.format(new Date(data.generated * 1000))} PT · tides NOAA CO-OPS · buoys NOAA NDBC · imagery USGS · a forecast, not a promise.`);
}

/* ---------- AI overview (server-sent events) ---------- */
function writeAI(box, words, head, typing) {
  const paras = words.split(/\n+/).filter(s => s.trim()).map((line, i, arr) => {
    const p = el('p', { class: typing && i === arr.length - 1 ? 'cursor' : '' });
    line.split(/\*\*(.+?)\*\*/).forEach((part, j) => p.append(j % 2 ? el('strong', {}, part) : part));
    return p;
  });
  box.replaceChildren(el('div', { class: 'who' }, el('span', {}, head)), ...paras);
}
function streamAI(box, url, ready, head) {
  if (ready) return writeAI(box, ready, head, false);
  let words = '';
  box.replaceChildren(el('div', { class: 'who' }, el('span', {}, head)), el('p', { class: 'muted cursor' }, 'Reading the forecasts '));
  const es = state.stream = new EventSource(url);
  es.addEventListener('text', (e) => { words += JSON.parse(e.data); writeAI(box, words, head, true); });
  es.addEventListener('done', () => { es.close(); if (words) writeAI(box, words, head, false); });
  es.addEventListener('unavailable', (e) => { es.close(); box.replaceChildren(el('p', { class: 'muted' }, `No written overview right now: ${JSON.parse(e.data)}.`)); });
  es.onerror = () => { es.close(); if (!words) box.replaceChildren(el('p', { class: 'muted' }, 'No written overview right now.')); else writeAI(box, words, head, false); };
}

/* ---------- spot page ---------- */
const unit = (b) => [Math.sin(b * Math.PI / 180), -Math.cos(b * Math.PI / 180)];
function arrow(tail, tip, color, w) {
  const dx = tip[0] - tail[0], dy = tip[1] - tail[1], len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len, head = w * 3.2;
  const neck = [tip[0] - ux * head, tip[1] - uy * head], k = head * 0.62;
  const shape = (extra, c) => [
    svg('line', { x1: tail[0], y1: tail[1], x2: neck[0], y2: neck[1], stroke: c, 'stroke-width': w + extra }),
    svg('polygon', { fill: c, points: `${tip[0] + ux * extra / 2},${tip[1] + uy * extra / 2} ${neck[0] - uy * (k + extra / 2)},${neck[1] + ux * (k + extra / 2)} ${neck[0] + uy * (k + extra / 2)},${neck[1] - ux * (k + extra / 2)}` })];
  return svg('g', {}, shape(4, '#000'), shape(0, color));
}
/* Same geometry as satellite.swell_arrow: the arrow sits out to sea in front of the spot. */
function overlay(spot, hour) {
  const g = svg('svg', { viewBox: '0 0 960 640', 'aria-hidden': 'true' });
  const colors = ['#40c4ff', '#bea0ff'], [fx, fy] = unit(spot.faces_deg);
  (hour.swells || []).forEach((s, i) => {
    if (s.dir == null) return;
    const ax = 480 + fx * (150 + i * 95), ay = 320 + fy * (150 + i * 95), [ux, uy] = unit(s.dir + 180), half = i ? 78 : 105;
    for (let k = 0; k < (i ? 0 : 3); k++) {
      const mx = ax - ux * (half - 14 - k * 42), my = ay - uy * (half - 14 - k * 42);
      g.append(svg('line', { x1: mx - uy * 58, y1: my + ux * 58, x2: mx + uy * 58, y2: my - ux * 58, stroke: colors[i], 'stroke-width': 3, opacity: .6 }));
    }
    g.append(arrow([ax - ux * half, ay - uy * half], [ax + ux * half, ay + uy * half], colors[i], i ? 6 : 9));
  });
  g.append(svg('circle', { cx: 480, cy: 320, r: 9, fill: '#fff', stroke: '#000', 'stroke-width': 3 }));
  if (hour.wind && hour.wind.dir != null) {
    const [ux, uy] = unit(hour.wind.dir + 180), wx = 886, wy = 566;
    g.append(svg('circle', { cx: wx, cy: wy, r: 52, fill: 'rgba(8,14,22,.75)', stroke: 'rgba(255,255,255,.35)', 'stroke-width': 2 }),
      svg('text', { x: wx, y: wy - 60, fill: '#fff', 'font-size': 16, 'text-anchor': 'middle' }, 'N'),
      arrow([wx - ux * 36, wy - uy * 36], [wx + ux * 36, wy + uy * 36], '#ffd60a', 6));
  }
  return g;
}

function chart(d, shown, sel, pick) {
  const W = 1000, H = 330, L = 34, R = 8, T = 10, B = 78, n = d.hours.length, tideH = 40;
  const x = (i) => L + i / (n - 1) * (W - L - R);
  let top = 2;
  d.hours.forEach(h => { if (h.surf_max > top) top = h.surf_max; });
  for (const id of shown) (d.source_hours[id] || []).forEach(v => { if (v > top) top = v; });
  const step = [1, 2, 5, 10, 20].find(s => top / s <= 6) || 20;
  top = Math.ceil(top * 1.08 / step) * step;
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Hourly wave height for the week: fused range and each source' });
  for (let i = 0; i < n; i += 24) {                               // nights and day labels
    g.append(svg('rect', { x: x(i), y: T, width: x(6) - x(0), height: H - T - B, fill: '#000', opacity: .22 }),
      svg('rect', { x: x(i + 18), y: T, width: x(Math.min(i + 24, n - 1)) - x(i + 18), height: H - T - B, fill: '#000', opacity: .22 }),
      svg('line', { x1: x(i), x2: x(i), y1: T, y2: H - 20, stroke: '#23314c' }),
      svg('text', { x: x(Math.min(i + 12, n - 1)), y: H - 6, 'text-anchor': 'middle' }, fmt({ weekday: 'short', day: 'numeric' }).format(new Date(d.hours[i].t * 1000))));
  }
  for (let v = 0; v <= top; v += step) g.append(svg('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: '#23314c' }),
    svg('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, v));
  g.append(svg('text', { x: L + 4, y: T + 12 }, 'ft'));
  const runs = (values) => { const out = []; let run = []; values.forEach((v, i) => { if (v == null) { if (run.length) out.push(run); run = []; } else run.push([i, v]); }); if (run.length) out.push(run); return out; };
  for (const run of runs(d.hours.map(h => h.surf_min == null ? null : [h.surf_min, h.surf_max]))) {   // the fused range
    const up = run.map(([i, v]) => `${x(i).toFixed(1)},${y(v[1]).toFixed(1)}`), down = run.map(([i, v]) => `${x(i).toFixed(1)},${y(v[0]).toFixed(1)}`).reverse();
    g.append(svg('polygon', { points: [...up, ...down].join(' '), fill: '#40c4ff', opacity: .3 }),
      svg('polyline', { points: run.map(([i, v]) => `${x(i).toFixed(1)},${y((v[0] + v[1]) / 2).toFixed(1)}`).join(' '), fill: 'none', stroke: '#e9eff8', 'stroke-width': 2.4, 'stroke-linejoin': 'round' }));
  }
  for (const id of shown) for (const run of runs(d.source_hours[id] || []))
    g.append(svg('polyline', { points: run.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' '), fill: 'none', stroke: SRC[id][1], 'stroke-width': 2, 'stroke-linejoin': 'round' }));
  const tides = d.hours.map(h => h.tide), lo = Math.min(...tides.filter(v => v != null)), hi = Math.max(...tides.filter(v => v != null));
  const ty = (v) => H - 22 - (v - lo) / ((hi - lo) || 1) * tideH;                                      // tide strip
  for (const run of runs(tides)) g.append(svg('polyline', { points: run.map(([i, v]) => `${x(i).toFixed(1)},${ty(v).toFixed(1)}`).join(' '), fill: 'none', stroke: '#8c9ab0', 'stroke-width': 1.5 }));
  g.append(svg('text', { x: L + 4, y: H - 22 - tideH - 3 }, `tide ${lo.toFixed(1)} to ${hi.toFixed(1)} ft`));
  const now = d.hours.findIndex(h => h.t + 3600 > Date.now() / 1000);
  if (now >= 0) g.append(svg('line', { x1: x(now), x2: x(now), y1: T, y2: H - 20, stroke: '#ffd60a', 'stroke-dasharray': '3 4' }));
  const mark = svg('line', { y1: T, y2: H - 20, stroke: '#40c4ff', 'stroke-width': 2 });
  g.append(mark);
  const place = (i) => { mark.setAttribute('x1', x(i)); mark.setAttribute('x2', x(i)); };
  place(sel);
  const at = (e) => { const r = g.getBoundingClientRect(); return Math.round(Math.min(Math.max(((e.clientX - r.left) / r.width * W - L) / (W - L - R), 0), 1) * (n - 1)); };
  g.addEventListener('pointerdown', (e) => { pick(at(e)); g.setPointerCapture(e.pointerId); });
  g.addEventListener('pointermove', (e) => { if (e.buttons) pick(at(e)); });
  return { node: g, place };
}

async function spotPage(id) {
  app.replaceChildren(el('a', { class: 'back', href: '/', 'data-nav': '' }, '‹ All spots'), el('div', { class: 'skeleton' }), el('div', { class: 'skeleton' }));
  const d = await get(`/v1/forecast/${encodeURIComponent(id)}`);
  const spot = d.spot, hours = d.hours, ids = d.sources.filter(s => s.ok).map(s => s.id).filter(i => SRC[i]);
  document.title = `${spot.name} · surfslop`;
  const nowI = Math.max(hours.findIndex(h => h.t + 3600 > Date.now() / 1000 && h.surf_min != null), 0);
  let sel = nowI, shown = new Set(ids), plot;

  const sat = el('div', { class: 'sat' }, el('img', { src: `/v1/satellite/${spot.id}.jpg`, alt: `Satellite view of ${spot.name}`, loading: 'lazy' }));
  const cap = el('div', { class: 'cap' });
  const out = el('output', {});
  const range = el('input', { type: 'range', min: 0, max: hours.length - 1, step: 1, 'aria-label': 'Forecast hour' });
  const dayStrip = el('div', { class: 'days' });
  const detail = el('div', { class: 'detail' });
  const plotBox = el('div', { class: 'scroll chart' });
  const dayButtons = d.days.map(day => el('button', { class: 'day', 'aria-pressed': 'false', onclick: () => setHour(hours.findIndex(h => h.time === `${day.date}T09:00`)) },
    el('div', { class: 'dow' }, day.dow), el('div', { class: 'dt' }, `${Number(day.date.slice(5, 7))}/${Number(day.date.slice(8))}`),
    el('div', { class: 'ht' }, day.surf), el('div', { class: 'stars', title: `${day.stars} of 5` }, '★'.repeat(day.stars), el('i', {}, '★'.repeat(5 - day.stars))),
    pills(day), el('div', { class: 'conf' }, el('span', { class: `dot ${day.confidence}` }), `${day.confidence} confidence`),
    day.caution === 'experts only' ? el('div', { class: 'warn' }, 'experts only') : null));
  dayStrip.append(...dayButtons);

  function setHour(i) {
    if (i < 0) return;
    sel = i; range.value = i;
    const h = hours[i], day = d.days.find(x => x.date === h.time.slice(0, 10));
    out.textContent = hourFmt.format(new Date(h.t * 1000)) + (i === nowI ? ' · now' : '');
    sat.querySelector('svg')?.remove(); sat.append(overlay(spot, h));
    cap.replaceChildren(el('span', {}, `${surfText(h.surf_min, h.surf_max)} ft · ${h.quality || 'no wind data'}`),
      ...(h.swells || []).map((s, k) => el('span', {}, `${k ? '2nd swell' : 'Swell'} ${s.height} ft @ ${s.period} s from ${s.from} ${s.dir}°`)),
      h.wind ? el('span', {}, `Wind ${windText(h.wind)}${h.wind.gust ? `, gusts ${h.wind.gust}` : ''}`) : null,
      h.tide != null ? el('span', {}, `Tide ${h.tide} ft`) : null);
    d.days.forEach((x, k) => dayButtons[k].setAttribute('aria-pressed', String(x === day)));
    if (plot) plot.place(i);
    if (day) detail.replaceChildren(
      ...PERIODS.map(([p, hrs]) => { const w = day.periods[p]; return el('div', {}, el('b', {}, `${p[0].toUpperCase()}${p.slice(1)} `), el('span', { class: 'muted' }, hrs), el('br'),
        w ? `${w.surf} ft · ${windText(w.wind)} ` : 'no data', w && w.quality ? el('span', { class: `badge ${w.quality}` }, w.quality) : null); }),
      el('div', {}, el('b', {}, 'Swell '), day.swell || 'n/a', day.caution ? el('span', { class: 'warn' }, ` · ${day.caution}`) : null, el('br'), el('b', {}, 'Best '), day.best || 'n/a', ' · ', el('span', { class: `badge ${day.confidence}` }, `${day.confidence} confidence`)),
      el('div', {}, el('b', {}, `Tides (${d.tide_station}) `), el('br'), day.tides.map(t => `${t.type} ${t.time} ${t.height} ft`).join(' · ') || 'n/a'));
  }
  range.addEventListener('input', () => setHour(Number(range.value)));
  const draw = () => { plot = chart(d, [...shown], sel, setHour); plotBox.replaceChildren(plot.node); };
  const keys = el('div', { class: 'keys' }, el('label', {}, el('i', {}), 'Fused range'), ids.map(i => {
    const box = el('input', { type: 'checkbox', checked: '' });
    box.addEventListener('change', () => { box.checked ? shown.add(i) : shown.delete(i); draw(); });
    const sw = el('i', {}); sw.style.background = SRC[i][1];
    return el('label', {}, box, sw, SRC[i][0]);
  }));
  keys.firstChild.firstChild.style.background = '#e9eff8';

  const n = d.now, buoys = d.buoys || [];
  const aiBox = el('div', { class: 'card ai' });
  const notes = d.days.flatMap(day => Object.entries(day.by_source).filter(([, v]) => v.note).map(([s, v]) => el('li', {}, `${day.label}, ${(SRC[s] || [s])[0]}: ${v.note}`)));
  app.replaceChildren(
    el('a', { class: 'back', href: '/', 'data-nav': '' }, '‹ All spots'),
    el('h1', {}, spot.name),
    el('p', { class: 'sub' }, [spot.region, spot.aka, spot.about].filter(Boolean).join(' · ')),
    el('div', { class: 'card now' },
      el('div', { class: 'stat' }, el('div', { class: 'k' }, 'Now'), el('div', { class: 'v big' }, n ? `${surfText(n.surf_min, n.surf_max)} ft ` : '–', n && n.quality ? el('span', { class: `badge ${n.quality}` }, n.quality) : null),
        el('div', { class: 'x' }, n ? `${n.n} forecasts agree within ±${Math.round((n.spread || 0) * 100)}%` : '')),
      el('div', { class: 'stat' }, el('div', { class: 'k' }, 'Swell'), el('div', { class: 'v' }, n && n.swells ? `${n.swells[0].height} ft @ ${n.swells[0].period} s` : '–'),
        el('div', { class: 'x' }, n && n.swells ? `from ${n.swells[0].from} (${n.swells[0].dir}°)` : '')),
      el('div', { class: 'stat' }, el('div', { class: 'k' }, 'Wind'), el('div', { class: 'v' }, n && n.wind ? `${n.wind.speed} kt ${n.wind.from}` : '–'), el('div', { class: 'x' }, n && n.wind ? n.wind.type : '')),
      el('div', { class: 'stat' }, el('div', { class: 'k' }, 'Tide'), el('div', { class: 'v' }, n && n.tide != null ? `${n.tide} ft` : '–'), el('div', { class: 'x' }, d.tide_station)),
      buoys.slice(0, 1).map(b => el('div', { class: 'stat' }, el('div', { class: 'k' }, 'Buoy, measured'), el('div', { class: 'v' }, `${b.height_ft} ft @ ${b.period_s} s`),
        el('div', { class: 'x' }, el('a', { href: b.url, rel: 'noreferrer', target: '_blank' }, b.name), ` · ${ago(b.t)}${b.water_f ? ` · water ${b.water_f}°F` : ''}`)))),
    el('h2', {}, 'The spot and the swell'), sat,
    el('div', { class: 'scrub' }, range, out, el('button', { onclick: () => setHour(nowI) }, 'Now')),
    el('h2', {}, 'The week'), el('div', { class: 'scroll' }, dayStrip), legend(), el('div', { class: 'card' }, detail),
    el('h2', {}, 'Hour by hour: the fused forecast and each source'), el('div', { class: 'card' }, keys, plotBox),
    el('h2', {}, 'Overview'), aiBox,
    el('h2', {}, 'What each source says'), el('div', { class: 'card scroll' }, sourceTable(d, ids)),
    el('h2', {}, 'Sources'), el('div', { class: 'card srcs' },
      d.sources.map(s => el('div', {}, el('span', { class: `dot ${s.ok ? 'high' : 'low'}` }), el('a', { href: s.url, rel: 'noreferrer', target: '_blank' }, s.label),
        el('span', { class: s.ok ? 'muted' : 'err' }, s.ok ? `through ${s.through} · read ${ago(s.fetched)}${s.scale ? ` · scaled ×${s.scale} to Surfline at this spot` : ''}` : s.error))),
      (d.off || []).map(s => el('div', {}, el('span', { class: 'dot' }), el('span', { class: 'muted' }, `${s} is switched off (surfslop source ${s} on)`))),
      notes.length ? el('ul', { class: 'notes' }, notes) : null));
  sat.append(cap, el('span', { class: 'credit' }, 'Imagery: USGS National Map'));
  draw(); setHour(nowI); footer(d);
  const written = d.overview_written ? ` · written ${clockFmt.format(new Date(d.overview_written * 1000))}` : '';
  streamAI(aiBox, `/v1/forecast/${spot.id}/ai`, d.overview, `Local AI${written} · from the numbers on this page${d.public ? '' : ' and the sources’ written forecasts'}`);
  // A source still being read in the background (Surfline, slowly): look again when it should be in.
  if (d.sources.some(s => s.pending) && state.tries < 2) {
    state.again = setTimeout(() => { state.tries += 1; if (state.stream) state.stream.close(); spotPage(id).catch(() => {}); }, 80000);
  }
}

function sourceTable(d, ids) {
  return el('table', {},
    el('thead', {}, el('tr', {}, el('th', {}, 'Day'), el('th', {}, 'Fused'), ids.map(i => el('th', {}, SRC[i][0])), el('th', {}, 'Agreement'))),
    el('tbody', {}, d.days.map(day => el('tr', {}, el('td', {}, day.label), el('td', { class: 'fused' }, `${day.surf} ft`),
      ids.map(i => { const v = day.by_source[i]; return el('td', {}, v ? v.surf : '–', v && v.rating ? el('small', {}, v.rating) : null); }),
      el('td', {}, el('span', { class: `badge ${day.confidence}` }, day.confidence))))));
}

route();
