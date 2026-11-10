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

/* The hourly panels for one day, stacked on one time axis: surf (fused range and each source),
   wind, tide, swell, wave energy, consistency, weather. Drawn at the page's real width, so the text
   stays readable on a phone. Everything fused is one hue (FUSED); only the surf panel's source
   lines carry their own colours. One crosshair runs through all panels; the readout above shows
   the selected hour, and a table of the day sits underneath. */
const FUSED = '#40c4ff', GRID = '#23314c', INK = '#e9eff8', MUTE = '#8c9ab0';
const STRIP = { clean: '#34c759', fair: '#ffcc00', blown: '#ff9500' };
function glyph(x, y, bearing, color, size) {                      // a small arrow pointing along `bearing`
  const k = size / 5;
  return svg('path', { d: `M0,${-5 * k} L${3 * k},${2.5 * k} L0,${k} L${-3 * k},${2.5 * k} Z`, fill: color, transform: `translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${bearing})` });
}
function skyIcon(x, y, w) {                                       // sun, moon, cloud, rain… about 14 px
  const g = svg('g', { transform: `translate(${x.toFixed(1)},${y})` }), sky = w.sky;
  const cloud = (dx, dy, c) => svg('path', { d: `M${dx - 6},${dy + 3} a3.2,3.2 0 0 1 1.200,-6.200 a4.200,4.200 0 0 1 8,1 a2.700,2.700 0 0 1 -0.500,5.200 z`, fill: c });
  const orb = (dx, dy, r) => w.night ? svg('path', { d: `M${dx + 1},${dy - r} a${r},${r} 0 1 0 ${r - 1},${r + 1} a${r * .8},${r * .8} 0 0 1 ${-(r - 1)},${-(r + 1)} z`, fill: '#c8d2e0' })
    : svg('circle', { cx: dx, cy: dy, r, fill: '#ffd60a' });
  if (sky === 'clear' || sky === 'mostly clear') g.append(orb(0, 0, 4.5));
  else if (sky === 'partly cloudy') g.append(orb(-2.5, -2, 3.6), cloud(1.500, 1, MUTE));
  else if (sky === 'fog') [-3, 0, 3].forEach(dy => g.append(svg('line', { x1: -6, x2: 6, y1: dy, y2: dy, stroke: MUTE, 'stroke-width': 1.6, 'stroke-linecap': 'round' })));
  else {
    g.append(cloud(0, -1, MUTE));
    if (sky === 'drizzle' || sky === 'rain' || sky === 'showers' || sky === 'thunder')
      [-3, 0.500, 4].forEach(dx => g.append(svg('line', { x1: dx, x2: dx - 1.200, y1: 4, y2: 7.500, stroke: FUSED, 'stroke-width': 1.5, 'stroke-linecap': 'round' })));
    if (sky === 'thunder') g.append(svg('path', { d: 'M1,2 l-3,4 h2.500 l-1.500,3.500 l4,-5 h-2.500 z', fill: '#ffd60a' }));
    if (sky === 'snow') [-3, 0.500, 4].forEach(dx => g.append(svg('circle', { cx: dx, cy: 6, r: 1.100, fill: INK })));
  }
  return g;
}
/* `index` lists the day's hours as positions in d.hours; `sel` and `pick` use those positions too.
   A panel is drawn only if the day has data for it, and the ones below move up. */
function chart(d, shown, index, sel, pick, width) {
  const W = Math.max(Math.round(width), 300), L = 38, R = 8, n = index.length, H = index.map(i => d.hours[i]);
  const slot = (W - L - R) / n, x = (k) => L + (k + .5) * slot, t0 = H[0].t;
  const xt = (t) => x(Math.min(Math.max((t - t0) / 3600, -.5), n - .5));
  const wide = slot >= 22;                                        // room for a number on every third hour
  const g = svg('svg', { role: 'img', 'aria-label': 'The day hour by hour, from the fused forecast' });
  const runs = (values) => { const out = []; let run = []; values.forEach((v, k) => { if (v == null) { if (run.length) out.push(run); run = []; } else run.push([k, v]); }); if (run.length) out.push(run); return out; };
  const line = (values, y, attrs) => runs(values).map(run => svg('polyline', { points: run.map(([k, v]) => `${x(k).toFixed(1)},${y(v).toFixed(1)}`).join(' '), fill: 'none', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', ...attrs }));
  const nice = (top, max) => { const step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000].find(s => top / s <= max) || 10000; return [step, Math.ceil(top * 1.06 / step) * step]; };
  const day = d.days.find(x => x.date === H[0].time.slice(0, 10)) || {};
  const rise = day.sun ? xt(day.sun.sunrise_t) : x(6), set = day.sun ? xt(day.sun.sunset_t) : x(18);
  let cursor = 12;                                                // where the next panel starts
  /* One panel: title, grid with labelled ticks, night shading; returns its y scale. */
  function panel(height, title, max, ticks, below = 14, fixed = false) {
    const top = cursor, y0 = top + 18, y1 = top + height, [step, cap] = !max ? [null, 1] : fixed ? [max / ticks, max] : nice(max, ticks);
    cursor = y1 + below;
    const y = (v) => y1 - v / cap * (y1 - y0);
    g.append(svg('rect', { x: L, y: y0, width: Math.max(rise - L, 0), height: y1 - y0, fill: '#000', opacity: .22 }),
      svg('rect', { x: set, y: y0, width: Math.max(W - R - set, 0), height: y1 - y0, fill: '#000', opacity: .22 }));
    if (step) for (let v = 0; v <= cap; v += step) g.append(svg('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: GRID }),
      svg('text', { x: L - 5, y: y(v) + 4, 'text-anchor': 'end' }, v));
    g.append(svg('text', { x: L, y: top + 11, class: 'pt' }, W < 480 ? title.split(':')[0] : title));   // the short name on a phone
    return { y, y0, y1 };
  }
  const max = (f) => Math.max(1, ...H.map(f).filter(v => v != null));
  const any = (f) => H.some(h => f(h) != null);
  const hourLabel = (h) => { const hr = Number(h.time.slice(11, 13)); return hr === 0 ? '12a' : hr === 12 ? '12p' : hr < 12 ? `${hr}a` : `${hr - 12}p`; };

  /* surface strip, then surf */
  H.forEach((h, k) => { if (h.quality) g.append(svg('rect', { x: L + k * slot, y: 2, width: slot + .3, height: 6, fill: STRIP[h.quality] })); });
  let top = max(h => h.surf_max);
  for (const id of shown) index.forEach(i => { const v = (d.source_hours[id] || [])[i]; if (v > top) top = v; });
  const surf = panel(196, 'Surf (ft): fused range, and each source', top, 5, 18);
  for (const run of runs(H.map(h => h.surf_min == null ? null : [h.surf_min, h.surf_max]))) {
    const up = run.map(([k, v]) => `${x(k).toFixed(1)},${surf.y(v[1]).toFixed(1)}`), down = run.map(([k, v]) => `${x(k).toFixed(1)},${surf.y(v[0]).toFixed(1)}`).reverse();
    g.append(svg('polygon', { points: [...up, ...down].join(' '), fill: FUSED, opacity: .3 }));
  }
  for (const id of shown) g.append(...line(index.map(i => (d.source_hours[id] || [])[i]), surf.y, { stroke: SRC[id][1] }));
  g.append(...line(H.map(h => h.surf_min == null ? null : (h.surf_min + h.surf_max) / 2), surf.y, { stroke: INK, 'stroke-width': 2.6 }));

  if (any(h => h.wind)) {                                         /* wind: a bar an hour, gusts as ticks, where it blows underneath */
    const wind = panel(92, 'Wind (kt): speed, gusts as ticks, and where it blows', max(h => h.wind && Math.max(h.wind.speed, h.wind.gust || 0)), 3, 28);
    const bw = Math.max(slot - 2, 2);
    H.forEach((h, k) => {
      if (!h.wind) return;
      const yv = wind.y(h.wind.speed), hgt = wind.y1 - yv, r = Math.min(3, bw / 2, hgt);
      if (hgt > .3) g.append(svg('path', { fill: FUSED, d: `M${(x(k) - bw / 2).toFixed(1)},${wind.y1} v${-(hgt - r)} q0,${-r} ${r},${-r} h${(bw - 2 * r).toFixed(1)} q${r},0 ${r},${r} v${hgt - r} z` }));
      if (h.wind.gust > h.wind.speed) g.append(svg('line', { x1: x(k) - bw / 2, x2: x(k) + bw / 2, y1: wind.y(h.wind.gust), y2: wind.y(h.wind.gust), stroke: MUTE, 'stroke-width': 1.5 }));
      if (h.wind.dir != null && (slot >= 11 || k % 2 === 0)) g.append(glyph(x(k), wind.y1 + 12, h.wind.dir + 180, INK, slot >= 16 ? 6 : 4.5));
      if (wide && k % 3 === 0) g.append(svg('text', { x: x(k), y: yv - 4, 'text-anchor': 'middle' }, h.wind.speed));
    });
  }
  if (any(h => h.tide)) {                                         /* tide: the curve, with each high and low marked */
    const tides = H.map(h => h.tide), known = tides.filter(v => v != null), lo = Math.min(0, ...known), hi = Math.max(1, ...known);
    const tide = panel(86, 'Tide (ft): highs and lows', null, 0, 16);
    const ty = (v) => tide.y1 - 14 - (v - lo) / (hi - lo) * (tide.y1 - tide.y0 - 30);
    g.append(svg('line', { x1: L, x2: W - R, y1: ty(0), y2: ty(0), stroke: GRID }), svg('text', { x: L - 5, y: ty(0) + 4, 'text-anchor': 'end' }, '0'), ...line(tides, ty, { stroke: FUSED }));
    (day.tides || []).forEach(e => { const px = Math.min(Math.max(xt(e.t), L + 26), W - R - 26);
      g.append(svg('circle', { cx: xt(e.t), cy: ty(e.height), r: 3, fill: INK }),
        svg('text', { x: px, y: ty(e.height) + (e.type === 'high' ? -7 : 15), 'text-anchor': 'middle' }, W >= 520 ? `${e.time} · ${e.height.toFixed(1)}` : e.height.toFixed(1))); });
  }
  if (any(h => h.swells)) {                                       /* swell at sea: the main train and, dashed, the second */
    const swell = panel(98, 'Swell at sea (ft): main train, second dashed', max(h => h.swells && h.swells[0].height), 4, 16);
    g.append(...line(H.map(h => h.swells && h.swells[1] ? h.swells[1].height : null), swell.y, { stroke: FUSED, 'stroke-dasharray': '5 4', opacity: .6 }),
      ...line(H.map(h => h.swells ? h.swells[0].height : null), swell.y, { stroke: FUSED }));
    H.forEach((h, k) => { if (k % 3 === 1 && h.swells) { g.append(glyph(x(k), swell.y(h.swells[0].height) - 10, h.swells[0].dir + 180, INK, 6));
      if (h.swells[1]) g.append(glyph(x(k), swell.y(h.swells[1].height) - 9, h.swells[1].dir + 180, MUTE, 4.5)); } });
  }
  if (any(h => h.energy)) {                                       /* wave energy at the break (the open-sea figure is in the readout and table) */
    const energy = panel(90, 'Wave energy at the break (kJ per wave)', max(h => h.energy && h.energy.nearshore), 3, 16);
    g.append(...line(H.map(h => h.energy ? h.energy.nearshore : null), energy.y, { stroke: FUSED }));
  }
  if (any(h => h.consistency)) {                                  /* consistency, 0 to 100 */
    const steady = panel(76, 'Consistency (0-100): how steadily waves arrive, our estimate', 100, 2, 16, true);
    g.append(...line(H.map(h => h.consistency), steady.y, { stroke: FUSED }));
  }
  if (any(h => h.weather)) {                                      /* weather: sky and air temperature every three hours */
    const wx = panel(46, 'Weather (°F)', null, 0, 10);
    H.forEach((h, k) => { if (k % 3 === 0 && h.weather) g.append(skyIcon(x(k), wx.y0 + 10, h.weather), svg('text', { x: x(k), y: wx.y1 + 1, 'text-anchor': 'middle' }, h.weather.temp_f)); });
  }

  const bottom = cursor, total = bottom + 22;
  g.setAttribute('viewBox', `0 0 ${W} ${total}`); g.setAttribute('width', W); g.setAttribute('height', total);
  H.forEach((h, k) => { if (k % 3 === 0) g.append(svg('line', { x1: L + k * slot, x2: L + k * slot, y1: 12, y2: bottom, stroke: GRID, opacity: k ? .7 : 1 }),
    svg('text', { x: x(k), y: total - 6, 'text-anchor': 'middle' }, hourLabel(h))); });
  const now = H.findIndex(h => h.t <= Date.now() / 1000 && h.t + 3600 > Date.now() / 1000);
  if (now >= 0) g.append(svg('line', { x1: x(now), x2: x(now), y1: 12, y2: bottom, stroke: '#ffd60a', 'stroke-dasharray': '3 4' }));
  const mark = svg('line', { y1: 0, y2: bottom, stroke: INK, 'stroke-width': 1.5, opacity: .85 });
  g.append(mark);
  const place = (i) => { const k = index.indexOf(i); mark.setAttribute('visibility', k < 0 ? 'hidden' : 'visible'); if (k >= 0) { mark.setAttribute('x1', x(k)); mark.setAttribute('x2', x(k)); } };
  place(sel);
  const at = (e) => { const r = g.getBoundingClientRect(); return index[Math.min(Math.max(Math.floor(((e.clientX - r.left) / r.width * W - L) / slot), 0), n - 1)]; };
  g.addEventListener('pointerdown', (e) => { pick(at(e)); if (e.pointerType !== 'touch') g.setPointerCapture(e.pointerId); });
  g.addEventListener('pointermove', (e) => { if (e.pointerType === 'mouse' || e.buttons) pick(at(e)); });
  return { node: g, place };
}

async function spotPage(id) {
  app.replaceChildren(el('a', { class: 'back', href: '/', 'data-nav': '' }, '‹ All spots'), el('div', { class: 'skeleton' }), el('div', { class: 'skeleton' }));
  const d = await get(`/v1/forecast/${encodeURIComponent(id)}`);
  const spot = d.spot, hours = d.hours, ids = d.sources.filter(s => s.ok).map(s => s.id).filter(i => SRC[i]);
  document.title = `${spot.name} · surfslop`;
  const nowI = Math.max(hours.findIndex(h => h.t + 3600 > Date.now() / 1000 && h.surf_min != null), 0);
  let sel = nowI, shown = new Set(ids), plot, plotDay = null;
  const dayOf = (i) => hours[i].time.slice(0, 10);

  const sat = el('div', { class: 'sat' }, el('img', { src: `/v1/satellite/${spot.id}.jpg`, alt: `Satellite view of ${spot.name}`, loading: 'lazy' }));
  const cap = el('div', { class: 'cap' });
  const out = el('output', {});
  const range = el('input', { type: 'range', min: 0, max: hours.length - 1, step: 1, 'aria-label': 'Forecast hour' });
  const dayStrip = el('div', { class: 'days' });
  const detail = el('div', { class: 'detail' });
  const plotBox = el('div', { class: 'chart' });
  const readout = el('div', { class: 'readout' }), hourTable = el('details', {}), dayName = el('span', {});
  const step = (by) => { const k = d.days.findIndex(x => x.date === dayOf(sel)) + by; if (d.days[k]) setHour(hours.findIndex(h => h.time === `${d.days[k].date}T09:00`)); };
  const dayNav = el('div', { class: 'daynav' }, el('button', { onclick: () => step(-1), 'aria-label': 'Previous day' }, '‹'), dayName,
    el('button', { onclick: () => step(1), 'aria-label': 'Next day' }, '›'));
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
    if (plot && plotDay !== dayOf(i)) draw(); else if (plot) plot.place(i);
    dayName.textContent = day ? day.label : dayOf(i);
    const s0 = (h.swells || [])[0];
    readout.replaceChildren(el('b', {}, hourFmt.format(new Date(h.t * 1000))),
      ...[`${surfText(h.surf_min, h.surf_max)} ft`, s0 && `swell ${s0.height} ft @ ${s0.period} s ${s0.from}`, h.wind && `wind ${h.wind.speed} kt ${h.wind.from}, ${h.wind.type}`,
        h.tide != null && `tide ${h.tide} ft`, h.energy && `energy ${h.energy.nearshore.toLocaleString('en-US')} kJ at the break (${h.energy.offshore.toLocaleString('en-US')} at sea)`, h.consistency != null && `consistency ${h.consistency}`,
        h.weather && `${h.weather.temp_f}°F ${h.weather.sky}`].filter(Boolean).map(t => el('span', {}, t)));
    const rows = hours.filter(x => x.time.slice(0, 10) === h.time.slice(0, 10) && Number(x.time.slice(11, 13)) % 3 === 0);
    const cols = [['Time', x => fmt({ hour: 'numeric' }).format(new Date(x.t * 1000)), () => true],
      ['Surf (ft)', x => surfText(x.surf_min, x.surf_max), x => x.surf_min != null],
      ['Swell at sea', x => `${x.swells[0].height} ft @ ${x.swells[0].period} s ${x.swells[0].from}`, x => x.swells],
      ['Wind', x => `${x.wind.speed} kt ${x.wind.from}`, x => x.wind], ['Tide (ft)', x => x.tide, x => x.tide != null],
      ['Energy at the break (kJ)', x => x.energy.nearshore.toLocaleString('en-US'), x => x.energy],
      ['Energy at sea (kJ)', x => x.energy.offshore.toLocaleString('en-US'), x => x.energy],
      ['Consistency', x => x.consistency, x => x.consistency != null],
      ['Weather', x => `${x.weather.temp_f}°F ${x.weather.sky}`, x => x.weather]].filter(c => rows.some(c[2]));
    hourTable.replaceChildren(el('summary', {}, `Table: ${day ? day.label : h.time.slice(0, 10)}, every three hours`), el('div', { class: 'scroll' }, el('table', {},
      el('thead', {}, el('tr', {}, cols.map(c => el('th', {}, c[0])))),
      el('tbody', {}, rows.map(x => el('tr', {}, cols.map(c => el('td', {}, c[2](x) ? String(c[1](x)) : '–'))))))));
    if (day) detail.replaceChildren(
      ...PERIODS.map(([p, hrs]) => { const w = day.periods[p]; return el('div', {}, el('b', {}, `${p[0].toUpperCase()}${p.slice(1)} `), el('span', { class: 'muted' }, hrs), el('br'),
        w ? `${w.surf} ft · ${windText(w.wind)} ` : 'no data', w && w.quality ? el('span', { class: `badge ${w.quality}` }, w.quality) : null); }),
      el('div', {}, el('b', {}, 'Swell '), day.swell || 'n/a', day.caution ? el('span', { class: 'warn' }, ` · ${day.caution}`) : null, el('br'), el('b', {}, 'Best '), day.best || 'n/a', ' · ', el('span', { class: `badge ${day.confidence}` }, `${day.confidence} confidence`)),
      el('div', {}, el('b', {}, `Tides (${d.tide_station}) `), el('br'), day.tides.map(t => `${t.type} ${t.time} ${t.height} ft`).join(' · ') || 'n/a'),
      el('div', {}, el('b', {}, 'Sun and weather '), el('br'), [day.sun && `sunrise ${day.sun.sunrise}, sunset ${day.sun.sunset}`,
        day.weather && `${day.weather.low}-${day.weather.high}°F, ${day.weather.sky}`].filter(Boolean).join(' · ') || 'n/a'),
      el('div', {}, el('b', {}, 'Energy and consistency '), el('br'), [day.energy != null && `${day.energy.toLocaleString('en-US')} kJ at the break`,
        day.consistency != null && `consistency ${day.consistency} of 100`].filter(Boolean).join(' · ') || 'n/a'));
  }
  range.addEventListener('input', () => setHour(Number(range.value)));
  const draw = () => {                                            // the panels for the selected hour's day
    plotDay = dayOf(sel);
    plot = chart(d, [...shown], hours.map((h, i) => i).filter(i => dayOf(i) === plotDay), sel, setHour, plotBox.clientWidth || 700);
    plotBox.replaceChildren(plot.node);
  };
  window.onresize = () => { if (plot && document.body.contains(plotBox)) draw(); };
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
    el('h2', {}, 'Hour by hour, from the fused forecast'), el('div', { class: 'card' }, dayNav, readout, keys, plotBox, hourTable),
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
