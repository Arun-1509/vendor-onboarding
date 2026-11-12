'use strict';
/* Onboard: vendor verification console. Plain JavaScript, no build step. */

// =================================================================== helpers ===
for (const m of ['replaceChildren', 'append']) {
  const orig = Element.prototype[m];
  Element.prototype[m] = function (...kids) { return orig.apply(this, kids.flat(Infinity).filter(k => k != null && k !== false)); };
}
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = tag === 'svg' || tag.startsWith('svg:') ? document.createElementNS('http://www.w3.org/2000/svg', tag.replace('svg:', '')) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) { if (kid == null || kid === false) continue; el.append(kid.nodeType ? kid : document.createTextNode(String(kid))); }
  return el;
}
const s = (tag, attrs, ...kids) => h('svg:' + tag, attrs, ...kids);
async function api(path, opts = {}) {
  const r = await fetch('/api' + path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    const e = new Error(msg); e.status = r.status; throw e;
  }
  return r.json();
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
const sleep = ms => new Promise(r => setTimeout(r, ms));
function toast(msg, err) { const t = h('div', { class: 'toast' + (err ? ' err' : '') }, msg); $('#toasts').append(t); setTimeout(() => t.remove(), err ? 7000 : 4200); }
async function copy(text, label = 'Copied') { try { await navigator.clipboard.writeText(text); toast(label); } catch (_) { prompt('Copy this:', text); } }
function fmt(ts) { if (!ts) return ''; const d = new Date(ts); return isNaN(d) ? ts : d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); }
function ago(ts) {
  if (!ts) return ''; const sec = (Date.now() - new Date(ts)) / 1000;
  if (sec < 60) return 'just now'; if (sec < 3600) return Math.round(sec / 60) + ' min ago'; if (sec < 86400) return Math.round(sec / 3600) + ' h ago';
  return Math.round(sec / 86400) + ' d ago';
}
const pct = (x, d = 1) => (x * 100).toFixed(d).replace(/\.0+$/, '') + '%';
const mask = v => { v = String(v || ''); return v.length > 6 ? '•••• ' + v.slice(-4) : v; };
const errorBox = e => h('div', { class: 'note err' }, h('b', {}, 'Something went wrong. '), e.message || String(e));
const ls = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} } };
let cleanup = null;

// ================================================================= vocabulary ===
const STATUS = {
  approved: ['Approved', 'ok'], awaiting_vendor: ['Waiting on vendor', 'wait'], awaiting_reviewer: ['Needs review', 'hold'],
  awaiting_second_approval: ['Needs 2nd approval', 'hold'], rejected_compliance: ['Rejected: compliance', 'bad'],
  rejected_closed: ['Closed: no response', 'bad'], rejected_manual: ['Rejected by reviewer', 'bad'], rejected: ['Rejected', 'bad'],
  pending: ['Pending', 'wait'], new: ['Not processed', 'mute'],
};
const pill = (sub, lg) => { const [label, cls] = STATUS[sub] || [sub, 'mute']; return h('span', { class: 'pill ' + cls + (lg ? ' lg' : '') }, h('i'), label); };
const CLS = { approved: 'ok', awaiting_vendor: 'wait', awaiting_reviewer: 'hold', awaiting_second_approval: 'hold' };
const clsOf = sub => CLS[sub] || (String(sub).startsWith('rejected') ? 'bad' : 'mute');
const COUNTRY = { IN: 'India', DE: 'Germany', GB: 'United Kingdom', US: 'United States' };
const FLAG = { IN: '🇮🇳', DE: '🇩🇪', GB: '🇬🇧', US: '🇺🇸' };
const ACTION = { vendor: 'Asks vendor', reviewer: 'Holds for reviewer', reject: 'Rejects', none: 'Noted' };
const riskEl = r => h('span', { class: 'risk ' + (r >= 40 ? 'hi' : r >= 20 ? 'mid' : '') }, h('span', { class: 'bar' }, h('b', { style: `width:${Math.min(100, r)}%` })), r);
const STAGE_INFO = {
  intake: ['Intake', 'New vendor, or an update to one we already know? What changed?'],
  read_documents: ['Read documents', 'AI reads each file and must quote the text it used; code verifies the quote and inspects the raw file for edits.', true],
  completeness: ['Completeness', 'Required fields and documents for this country.'],
  validate: ['Format & check digits', 'Tax ID, IBAN / IFSC / routing / sort code, email, document dates.'],
  cross_check: ['Cross-reference', 'Form vs certificate vs bank letter vs bank codes vs registration state.'],
  name_judgment: ['Resolve names', 'Only ambiguous name pairs go to the AI judge, with a written reason.', true],
  risk_screening: ['Risk screening', 'Sanctions list, lookalike vendors, shared accounts, bank changes, prior rejections.'],
  decision: ['Decision', 'The rule table decides. No AI in this step.'],
  communicate: ['Communicate', 'Vendor email (only fixable items) or reviewer note (evidence + safe next step).', true],
};
const STAGES = Object.keys(STAGE_INFO);
let RULES = null;
async function rulebook() { if (!RULES) { const r = await api('/rulebook'); RULES = { map: Object.fromEntries(r.rules.map(x => [x.code, x])), raw: r }; } return RULES; }

// ===================================================================== tour ===
// The guide asks for a happy path plus 2-4 deliberate edge cases. These five are the ones to watch first.
const TOUR = [
  { id: 'H1_happy_india', name: 'Happy path', expect: 'approved', watch: 'Nine stages run in order. The AI reads both documents, code verifies every value against the file, and the vendor is approved with the reasons written out.' },
  { id: 'E1b_bank_change_request', name: 'Payment-fraud attempt', expect: 'awaiting_reviewer', watch: 'An approved vendor "updates" its bank account. Every field is valid, so only comparing with what is on file catches it. Held for two reviewers; the vendor is not told why.' },
  { id: 'E5a_edited_bank_letter', name: 'A document that lies', expect: 'awaiting_reviewer', watch: 'The bank letter looks perfect, but a new account number was pasted over the original. Look for the red box on the document.' },
  { id: 'tour_missing_doc', url: '/tour/missing-doc', reply: true, name: 'Missing document, then the reply', expect: 'awaiting_vendor', watch: 'A brand-new company forgets its bank letter. It gets a precise email saying what to send. Then press "Simulate the vendor replying": only the failed check is re-run and version 2 is approved.' },
  { id: 'E2c_variant_abbreviation_ai', name: 'Looks wrong, is fine', expect: 'approved', watch: '"Fab" on one document, "Fabrication" on another. The AI judge explains why they are the same company, and the rulebook accepts it. No false alarm.' },
];
const tourDone = () => { try { return JSON.parse(ls.get('tour', '[]')); } catch (_) { return []; } };
const tourMark = id => { const d = tourDone(); if (!d.includes(id)) { d.push(id); ls.set('tour', JSON.stringify(d)); } };
async function tourRun(i, btn) {
  if (btn) btn.disabled = true;
  try { const t = TOUR[i]; const r = await api(t.url || `/samples/${t.id}/submit`, { method: 'POST' }); tourMark(t.id);
    location.hash = `#/run/${r.submission_id}?tour=${i}`; }
  catch (e) { toast(e.message, true); if (btn) btn.disabled = false; }
}
function tourCard(compact) {
  const done = tourDone(), next = TOUR.findIndex(t => !done.includes(t.id));
  if (compact) return h('div', { class: 'hero', style: 'margin-bottom:16px' }, h('div', { style: 'flex:1;min-width:240px' },
    h('h2', {}, next === 0 ? 'New here? Take the 5-step tour' : next < 0 ? 'Tour complete' : `Tour: step ${next + 1} of ${TOUR.length} is next`),
    h('p', {}, next < 0 ? 'You have seen the happy path and four edge cases. Try an unseen vendor, or enter one by hand.' : 'One happy path and four edge cases, each run live through the real process. About two minutes.')),
    h('div', { class: 'steps-mini' }, TOUR.map((t, i) => h('i', { class: done.includes(t.id) ? 'on' : '', title: t.name }, done.includes(t.id) ? '✓' : String(i + 1)))),
    next < 0 ? h('a', { class: 'btn', href: '#/new' }, 'Try your own') : h('button', { class: 'btn', onclick: e => tourRun(next, e.currentTarget) }, next === 0 ? 'Start the tour' : 'Continue'));
  return h('div', { class: 'tour' }, TOUR.map((t, i) => h('div', { class: 'tstep' + (done.includes(t.id) ? ' done' : i === next ? ' next' : '') },
    h('span', { class: 'n' }, done.includes(t.id) ? '✓' : String(i + 1)),
    h('div', { class: 'tb' }, h('div', { class: 'row', style: 'gap:8px' }, h('b', {}, t.name), pill(t.expect)), h('p', {}, t.watch)),
    h('button', { class: 'btn sm ' + (i === next ? 'primary' : ''), onclick: e => tourRun(i, e.currentTarget) }, done.includes(t.id) ? 'Run again' : 'Run'))));
}
function applyTheme() { const t = ls.get('theme', ''); if (t) document.documentElement.setAttribute('data-theme', t); else document.documentElement.removeAttribute('data-theme'); }
applyTheme();
const isDark = () => (ls.get('theme', '') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark';

// ===================================================================== shell ===
let HEALTH = null;
async function updateRail() {
  try {
    const [st, q] = await Promise.all([api('/stats'), api('/review/queue')]);
    HEALTH = HEALTH || await api('/health');
    const b = $('#nb-review'); b.hidden = !q.length; b.textContent = q.length; b.classList.toggle('red', q.some(x => x.overdue));
    $('#railfoot').replaceChildren(
      h('span', { class: 'chip ' + (st.llm_mode === 'mock' ? 'mock' : 'live'), title: st.llm_mode === 'mock' ? 'No API key set: document reading is simulated offline' : 'Real model calls' },
        st.llm_mode === 'mock' ? 'Offline AI (simulated)' : 'Live AI · ' + st.provider),
      st.clock_offset_days ? h('span', { class: 'chip', title: 'Demo clock moved forward' }, `Demo clock +${st.clock_offset_days} days`) : null,
      h('button', { class: 'btn quiet sm', style: 'justify-content:flex-start;padding:0 4px', 'aria-label': 'Switch between light and dark', onclick: () => { ls.set('theme', isDark() ? 'light' : 'dark'); applyTheme(); updateRail(); } }, isDark() ? '☀ Light mode' : '☾ Dark mode'));
  } catch (_) {}
}
function markNav(name) { document.querySelectorAll('[data-nav]').forEach(a => a.getAttribute('data-nav') === name ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')); }
const hashQuery = () => new URLSearchParams((location.hash.split('?')[1]) || '');

const ROUTES = [
  [/^#\/?$/, dashboard, 'dashboard'], [/^#\/review$/, reviewView, 'review'], [/^#\/vendors$/, vendorsView, 'vendors'],
  [/^#\/vendor\/([^?]+)/, vendorView, 'vendors'], [/^#\/new$/, newView, 'new'], [/^#\/run\/([^?]+)/, runView, 'new'],
  [/^#\/outbox$/, outboxView, 'outbox'], [/^#\/quality$/, qualityView, 'quality'], [/^#\/rules$/, rulesView, 'rules'],
  [/^#\/process$/, processView, 'process'],
];
async function route() {
  if (cleanup) { cleanup(); cleanup = null; }
  const view = $('#view');
  const portal = location.pathname.match(/^\/portal\/([^/]+)/);
  document.body.classList.toggle('portal', !!portal);
  if (portal) { document.title = 'Vendor registration'; return portalView(view, portal[1]); }
  updateRail();
  const hash = location.hash || '#/';
  for (const [re, fn, nav] of ROUTES) {
    const m = hash.match(re);
    if (m) {
      markNav(nav); view.replaceChildren(h('div', { class: 'skeleton', style: 'width:40%;height:22px;margin:8px 0 20px' }), h('div', { class: 'skeleton', style: 'height:120px' }));
      try { await fn(view, ...m.slice(1).map(decodeURIComponent)); } catch (e) { console.error(e); view.replaceChildren(errorBox(e)); }
      window.scrollTo(0, 0); return;
    }
  }
  view.replaceChildren(h('h1', {}, 'Page not found'), h('p', {}, h('a', { href: '#/' }, 'Back to overview')));
}
window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

// ============================================================ small charts ===
function donut(parts, size = 150) {
  const total = parts.reduce((a, p) => a + p.v, 0) || 1, r = size / 2 - 12, c = 2 * Math.PI * r;
  let off = 0;
  const svg = s('svg', { width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: 'img', 'aria-label': parts.map(p => `${p.label} ${p.v}`).join(', ') },
    s('circle', { cx: size / 2, cy: size / 2, r, fill: 'none', stroke: 'var(--line)', 'stroke-width': 16 }),
    parts.filter(p => p.v).map(p => { const len = c * p.v / total; const el = s('circle', { cx: size / 2, cy: size / 2, r, fill: 'none', stroke: p.color, 'stroke-width': 16, 'stroke-dasharray': `${len - 2} ${c - len + 2}`, 'stroke-dashoffset': -off, transform: `rotate(-90 ${size / 2} ${size / 2})` }); off += len; return el; }),
    s('text', { x: '50%', y: '47%', 'text-anchor': 'middle', 'font-size': 26, 'font-weight': 700, fill: 'var(--ink)' }, String(parts.reduce((a, p) => a + p.v, 0))),
    s('text', { x: '50%', y: '62%', 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--muted)' }, 'decided'));
  return svg;
}
function gauge(v, limit = 20) {
  const w = 120, r = 46, cx = 60, cy = 56, a = Math.PI * Math.min(100, v) / 100;
  const x = cx - r * Math.cos(a), y = cy - r * Math.sin(a), col = v >= 40 ? 'var(--bad)' : v >= limit ? 'var(--wait)' : 'var(--ok)';
  const la = Math.PI * limit / 100;
  return s('svg', { width: w, height: 66, viewBox: '0 0 120 66', role: 'img', 'aria-label': `Risk ${v} of 100` },
    s('path', { d: `M14 56 A46 46 0 0 1 106 56`, fill: 'none', stroke: 'var(--line)', 'stroke-width': 10, 'stroke-linecap': 'round' }),
    v > 0 ? s('path', { d: `M14 56 A46 46 0 0 1 ${x.toFixed(1)} ${y.toFixed(1)}`, fill: 'none', stroke: col, 'stroke-width': 10, 'stroke-linecap': 'round' }) : null,
    s('line', { x1: cx - (r - 9) * Math.cos(la), y1: cy - (r - 9) * Math.sin(la), x2: cx - (r + 9) * Math.cos(la), y2: cy - (r + 9) * Math.sin(la), stroke: 'var(--ink-2)', 'stroke-width': 2 }),
    s('text', { x: 60, y: 52, 'text-anchor': 'middle', 'font-size': 20, 'font-weight': 700, fill: 'var(--ink)' }, String(v)));
}
function dayBars(byDay) {
  const days = Object.keys(byDay).sort().slice(-10);
  if (!days.length) return h('div', { class: 'empty' }, 'No runs yet.');
  const keys = [['approved', 'var(--ok)'], ['awaiting_vendor', 'var(--wait)'], ['awaiting_reviewer', 'var(--hold)'], ['rejected', 'var(--bad)']];
  const val = (d, k) => k === 'rejected' ? Object.entries(byDay[d]).filter(([x]) => x.startsWith('rejected')).reduce((a, [, n]) => a + n, 0) : (byDay[d][k] || 0);
  const max = Math.max(1, ...days.map(d => keys.reduce((a, [k]) => a + val(d, k), 0)));
  const W = 420, H = 150, bw = Math.min(34, (W - 20) / days.length - 8);
  return s('svg', { viewBox: `0 0 ${W} ${H + 22}`, width: '100%', role: 'img', 'aria-label': 'Runs per day by outcome' },
    days.map((d, i) => { let y = H; const x = 10 + i * ((W - 20) / days.length) + 4;
      return s('g', {}, keys.map(([k, col]) => { const v = val(d, k); const hgt = H * v / max; y -= hgt; return v ? s('rect', { x, y, width: bw, height: Math.max(0, hgt - 1), rx: 3, fill: col }, s('title', {}, `${d} · ${k.replace('_', ' ')}: ${v}`)) : null; }),
        s('text', { x: x + bw / 2, y: H + 15, 'text-anchor': 'middle', 'font-size': 10, fill: 'var(--muted)' }, d.slice(5))); }));
}

// ================================================================ dashboard ===
async function dashboard(view) {
  const [st, runs, queue, vendors] = await Promise.all([api('/stats'), api('/runs?limit=8'), api('/review/queue'), api('/vendors')]);
  const overdueV = vendors.filter(v => v.overdue);
  const kpi = (v, l, sub, color, cls) => h('div', { class: 'kpi ' + (cls || ''), style: color ? `--k:${color}` : '' }, h('div', { class: 'v' }, v), h('div', { class: 'l' }, l), sub ? h('div', { class: 's' }, sub) : null);
  const reset = h('button', { class: 'btn quiet sm', onclick: async (e) => {
    const b = e.currentTarget;
    if (!b.dataset.arm) { b.dataset.arm = '1'; b.textContent = 'Click again to wipe demo data'; setTimeout(() => { delete b.dataset.arm; b.textContent = 'Reset demo data'; }, 4000); return; }
    try { let r; try { r = await api('/admin/reset', { method: 'POST' }); } catch (err) { if (err.status !== 403) throw err; const k = prompt('Admin key'); if (!k) return; r = await api('/admin/reset', { method: 'POST', headers: { 'X-Admin-Key': k } }); }
      toast(`Reset: ${r.seeded_vendors} approved vendors restored`); route(); } catch (err) { toast(err.message, true); }
  } }, 'Reset demo data');
  const week = h('button', { class: 'btn sm', title: 'Pretend a week passed: overdue vendors get reminders, then are closed', onclick: async (e) => {
    e.currentTarget.disabled = true;
    try { const r = await post('/admin/clock', { days: 7 }); const rem = r.actions.filter(a => a.action === 'reminder').length, cl = r.actions.filter(a => a.action === 'closed').length;
      toast(`A week later: ${rem} reminder(s) sent, ${cl} request(s) closed`); route(); } catch (err) { toast(err.message, true); }
  } }, 'Advance one week');
  const by = st.by_substatus || {};
  const rejected = Object.entries(by).filter(([k]) => k.startsWith('rejected')).reduce((a, [, n]) => a + n, 0);
  const parts = [{ label: 'Approved', v: by.approved || 0, color: 'var(--ok)' }, { label: 'Waiting on vendor', v: by.awaiting_vendor || 0, color: 'var(--wait)' },
    { label: 'Needs review', v: (by.awaiting_reviewer || 0) + (by.awaiting_second_approval || 0), color: 'var(--hold)' }, { label: 'Rejected', v: rejected, color: 'var(--bad)' }];

  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Overview'), h('p', { class: 'sub' }, 'Every submission is read, cross-checked, screened and decided. People only see what needs a person.')),
      h('div', { class: 'actions' }, reset, week, h('a', { class: 'btn primary', href: '#/new' }, '+ New submission'))),
    tourCard(true),
    st.example_runs ? h('p', { class: 'xs muted', style: 'margin:-6px 0 12px' }, `Includes ${st.example_runs} example vendors that are generated and run automatically after each restart (the free host does not keep data), so this page is never empty. They are marked "example"; everything you run appears on top.`) : null,
    h('div', { class: 'kpis' },
      kpi(st.processed, 'Submissions decided', `${st.runs} run${st.runs === 1 ? '' : 's'} in total`, 'var(--accent)'),
      kpi(st.processed ? st.handled_pct + '%' : '–', 'Handled without a person', st.processed ? `≈ ${st.est_hours_saved} h of review saved` : 'approved, asked or rejected automatically', 'var(--ok)'),
      kpi(queue.length, 'In review queue', queue.some(q => q.overdue) ? `${queue.filter(q => q.overdue).length} past SLA` : (st.awaiting_second_approval ? `${st.awaiting_second_approval} need 2nd approval` : 'within SLA'), 'var(--hold)', queue.length ? 'attn' : ''),
      kpi(st.awaiting_vendor, 'Waiting on vendors', st.overdue ? `${st.overdue} overdue` : 'all within deadline', 'var(--wait)'),
      kpi(st.runs ? st.avg_run_seconds + ' s' : '–', 'Average time to decide', HEALTH && HEALTH.stage_pace_ms ? `incl. ${HEALTH.stage_pace_ms} ms/stage display pacing` : 'per submission', 'var(--info)')),
    h('div', { class: 'grid2', style: 'margin-top:16px' },
      h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Outcomes')),
        h('div', { class: 'row', style: 'gap:24px;align-items:center' }, donut(parts),
          h('div', { style: 'display:flex;flex-direction:column;gap:8px;flex:1' }, parts.map(p => h('div', { class: 'row', style: 'justify-content:space-between' },
            h('span', { class: 'row', style: 'gap:8px' }, h('i', { style: `width:10px;height:10px;border-radius:3px;background:${p.color};display:inline-block` }), p.label), h('b', {}, p.v)))))),
      h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Runs per day'), h('div', { class: 'legend' }, h('span', {}, h('i', { style: 'background:var(--ok)' }), 'approved'), h('span', {}, h('i', { style: 'background:var(--wait)' }), 'vendor'), h('span', {}, h('i', { style: 'background:var(--hold)' }), 'review'), h('span', {}, h('i', { style: 'background:var(--bad)' }), 'rejected'))), dayBars(st.runs_by_day || {}))),
    h('div', { class: 'grid2', style: 'margin-top:16px' },
      h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Needs a person'), h('a', { href: '#/review', class: 'sm' }, 'Open queue →')),
        queue.length || overdueV.length ? h('div', { style: 'display:flex;flex-direction:column;gap:8px' },
          queue.slice(0, 5).map(q => h('a', { class: 'case', href: '#/vendor/' + q.vendor_id, style: 'text-decoration:none;color:inherit' },
            h('div', { class: 'ct' }, h('b', {}, q.legal_name), h('small', {}, q.titles.join(' · ') || q.codes.join(', '))),
            q.four_eyes ? h('span', { class: 'tag', title: 'Needs two reviewers' }, '2 × eyes') : null,
            h('span', { class: 'xs ' + (q.overdue ? 'bad-t' : 'muted') }, q.overdue ? 'past SLA' : `${Math.max(0, Math.round(q.hours_left))} h left`), pill(q.substatus))),
          overdueV.map(v => h('a', { class: 'case', href: '#/vendor/' + v.vendor_id, style: 'text-decoration:none;color:inherit' }, h('div', { class: 'ct' }, h('b', {}, v.legal_name), h('small', {}, 'Vendor has not replied by the deadline')), pill('awaiting_vendor'))))
          : h('div', { class: 'empty' }, 'Nothing needs a person right now. Suspicious submissions appear here with the evidence and the safe next step.')),
      h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Recent runs'), h('a', { href: '#/vendors', class: 'sm' }, 'All vendors →')),
        runs.length ? h('div', { class: 'tablewrap', style: 'border:0' }, h('table', {}, h('tbody', {}, runs.map(r => h('tr', { class: 'click', onclick: () => location.hash = '#/run/' + r.submission_id },
          h('td', {}, h('b', {}, r.legal_name), h('div', { class: 'id' }, `${r.run_id} · v${r.version || 1} · ${(r.source || '').replace('synthetic:', 'synthetic · ').replace(/^example:.*/, 'example')}`)),
          h('td', {}, pill(r.substatus)), h('td', {}, riskEl(r.risk_score || 0)), h('td', { class: 'xs faint' }, ago(r.finished_at))))))) : h('div', { class: 'empty' }, 'No runs yet.'))));
}

// ============================================================== review queue ===
async function reviewView(view) {
  const q = await api('/review/queue');
  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Review queue'), h('p', { class: 'sub' }, `Fraud-shaped or ambiguous submissions. Ordered by deadline (${'48 h SLA'}) and risk. The vendor is told only that they are being reviewed.`))),
    q.length ? h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Vendor', 'Why it is here', 'Queue', 'Risk', 'SLA', 'Status', ''].map(x => h('th', {}, x)))),
      h('tbody', {}, q.map(i => h('tr', { class: 'click', onclick: () => location.hash = '#/vendor/' + i.vendor_id },
        h('td', {}, h('b', {}, i.legal_name), h('div', { class: 'id' }, `${i.vendor_id} · ${FLAG[i.country] || ''} ${COUNTRY[i.country] || i.country}`)),
        h('td', { style: 'max-width:340px' }, i.titles.map(t => h('div', { class: 'sm' }, '• ' + t))),
        h('td', {}, h('span', { class: 'who ' + (i.queue === 'compliance' ? 'compliance' : 'reviewer') }, i.queue)),
        h('td', {}, riskEl(i.risk_score)),
        h('td', { class: i.overdue ? 'bad-t' : '' }, i.overdue ? `${Math.abs(Math.round(i.hours_left))} h overdue` : `${Math.round(i.hours_left)} h left`, h('div', { class: 'xs faint' }, 'since ' + fmt(i.waiting_since))),
        h('td', {}, pill(i.substatus), i.four_eyes ? h('div', { class: 'xs muted', style: 'margin-top:4px' }, i.first_approver ? `1st approval: ${i.first_approver}` : 'Needs 2 reviewers') : null),
        h('td', {}, h('a', { class: 'btn sm primary', href: '#/vendor/' + i.vendor_id }, 'Review'))))))) :
      h('div', { class: 'empty' }, 'The queue is empty. Try the "Looks fine, isn\'t" or "Documents that lie" cases in the demo pack.'));
}

// ================================================================== vendors ===
async function vendorsView(view) {
  const vendors = await api('/vendors');
  let filter = 'all', q = '';
  const FILTERS = [['all', 'All'], ['awaiting_reviewer', 'Needs review'], ['awaiting_vendor', 'Waiting on vendor'], ['approved', 'Approved'], ['rejected', 'Rejected']];
  const match = (v, k) => k === 'all' || (k === 'rejected' ? v.status === 'rejected' : k === 'awaiting_reviewer' ? v.substatus.startsWith('awaiting_') && v.substatus !== 'awaiting_vendor' : v.substatus === k);
  const tabs = h('div', { class: 'tabs', role: 'group', 'aria-label': 'Filter' }), tbody = h('tbody');
  function draw() {
    tabs.replaceChildren(FILTERS.map(([k, l]) => h('button', { 'aria-pressed': String(filter === k), onclick: () => { filter = k; draw(); } }, `${l} ${vendors.filter(v => match(v, k)).length}`)));
    const rows = vendors.filter(v => match(v, filter) && (!q || (v.legal_name + v.vendor_id + (v.tax_id || '')).toLowerCase().includes(q)));
    tbody.replaceChildren(rows.length ? rows.map(v => h('tr', { class: 'click', tabindex: 0, onclick: () => location.hash = '#/vendor/' + v.vendor_id, onkeydown: e => { if (e.key === 'Enter') location.hash = '#/vendor/' + v.vendor_id; } },
      h('td', {}, h('b', {}, v.legal_name), h('div', { class: 'id' }, v.vendor_id + (v.version > 1 ? ` · v${v.version}` : ''))),
      h('td', {}, (FLAG[v.country] || '') + ' ' + (COUNTRY[v.country] || v.country)),
      h('td', {}, pill(v.substatus), v.overdue ? h('div', { class: 'xs bad-t', style: 'margin-top:3px' }, 'Overdue') : null),
      h('td', {}, riskEl(v.risk_score)), h('td', { class: 'muted sm', style: 'max-width:340px' }, v.reason), h('td', { class: 'xs faint' }, fmt(v.updated_at))))
      : h('tr', {}, h('td', { colspan: 6, class: 'muted' }, 'No vendors match.')));
  }
  draw();
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Vendors'), h('p', { class: 'sub' }, 'Every vendor, its status, and the reason in plain words.')), h('a', { class: 'btn primary', href: '#/new' }, '+ New submission')),
    h('div', { class: 'tools' }, tabs, h('input', { class: 'search', type: 'search', placeholder: 'Search name, ID or tax ID', 'aria-label': 'Search', oninput: e => { q = e.target.value.trim().toLowerCase(); draw(); } })),
    h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Vendor', 'Country', 'Status', 'Risk', 'Reason', 'Updated'].map(x => h('th', {}, x)))), tbody)));
}

// ========================================================= findings + emails ===
function findingEl(f) {
  return h('div', { class: 'finding ' + (f.action === 'none' ? '' : f.action) },
    h('div', { style: 'flex:1' }, h('div', { class: 'row', style: 'gap:8px' }, h('span', { class: 'ft' }, f.title), h('span', { class: 'code faint' }, f.code)),
      h('div', { class: 'fm' }, f.message)),
    h('span', { class: 'pts', title: ACTION[f.action] }, (f.points ? '+' + f.points : '') + (f.action !== 'none' ? ' · ' + ACTION[f.action] : '')));
}
function mailEl(m) {
  return h('div', { class: 'mail' }, h('div', { class: 'row', style: 'margin-bottom:6px' }, h('span', { class: 'who ' + (m.audience === 'vendor' ? 'vendor' : 'reviewer') }, m.audience === 'vendor' ? 'To vendor' : 'Internal'),
    h('span', { class: 'xs faint' }, m.to || m.to_addr || ''), m.fallback_reason ? h('span', { class: 'tag', title: m.fallback_reason }, 'safe template') : null),
    h('div', { class: 'subj' }, m.subject), h('pre', {}, m.body));
}
function actionsEl(list) {
  return h('ul', { class: 'actionlist' }, (list || []).map(a => h('li', {}, h('span', { class: 'who ' + a.who }, a.who), h('span', {}, a.action))));
}

// ================================================================ vendor 360 ===
async function vendorView(view, id) {
  const [d, queue, rb] = await Promise.all([api('/vendors/' + id), api('/review/queue'), rulebook()]);
  const v = d.vendor, qi = queue.find(x => x.vendor_id === id);
  const lastRun = [...d.runs].reverse().find(r => r.result);
  const res = lastRun ? lastRun.result : null;
  const subs = d.submissions;
  const latest = subs[subs.length - 1];
  const actor = ls.get('reviewer', '');

  // ---- review panel
  let panel = null;
  if (qi) {
    const name = h('input', { value: actor, placeholder: 'Your name', 'aria-label': 'Reviewer name', oninput: e => ls.set('reviewer', e.target.value) });
    const reason = h('textarea', { placeholder: 'Reason (required, kept in the audit trail)', 'aria-label': 'Reason' });
    const checks = qi.checklist.map(c => h('label', { class: 'check' }, h('input', { type: 'checkbox' }), h('span', {}, c)));
    const asks = h('textarea', { placeholder: 'One request per line, in neutral words (no mention of screening or fraud)', 'aria-label': 'Requests to the vendor' });
    const askBox = h('div', { hidden: true }, h('label', {}, 'What should the vendor send?'), asks, h('button', { class: 'btn sm primary', style: 'margin-top:8px', onclick: () => act('request_info') }, 'Send request to vendor'));
    async function act(action) {
      try {
        const r = await post(`/vendors/${id}/review`, { action, actor: name.value, reason: reason.value, checklist_done: checks.every(c => c.querySelector('input').checked),
          asks: asks.value.split('\n').map(x => x.trim()).filter(Boolean) });
        toast(r.note || `Done: ${STATUS[r.substatus] ? STATUS[r.substatus][0] : r.substatus}`); route();
      } catch (e) { toast(e.message, true); }
    }
    panel = h('div', { class: 'card', style: 'border-color:var(--hold-line)' },
      h('div', { class: 'card-h' }, h('h2', {}, 'Your decision'), h('span', { class: 'xs ' + (qi.overdue ? 'bad-t' : 'muted') }, qi.overdue ? 'Past the 48 h SLA' : `${Math.round(qi.hours_left)} h left in SLA`)),
      qi.substatus === 'awaiting_second_approval' ? h('div', { class: 'note warn', style: 'margin-bottom:10px' }, `First approval by ${qi.first_approver}. A different reviewer must approve (four-eyes rule).`) :
        qi.four_eyes ? h('div', { class: 'note info', style: 'margin-bottom:10px' }, 'High-risk case: approval needs the checklist and two different reviewers.') : null,
      checks.length ? h('div', {}, h('h3', {}, 'Verify before approving'), checks) : null,
      h('div', { class: 'grid2', style: 'gap:10px;margin:10px 0' }, h('div', {}, h('label', {}, 'Reviewer'), name), h('div', {})),
      h('label', {}, 'Reason'), reason,
      h('div', { class: 'row', style: 'margin-top:10px' }, h('button', { class: 'btn ok', onclick: () => act('approve') }, qi.substatus === 'awaiting_second_approval' ? 'Give 2nd approval' : 'Approve'),
        h('button', { class: 'btn bad', onclick: () => act('reject') }, 'Reject'),
        h('button', { class: 'btn', onclick: () => { askBox.hidden = !askBox.hidden; } }, 'Ask vendor instead…')), h('div', { style: 'margin-top:10px' }, askBox));
  }

  // ---- version diff
  const diffs = subs.map((sub, i) => {
    if (i === 0) return null;
    const a = subs[i - 1].form || {}, b = sub.form || {};
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].filter(k => (a[k] || '') !== (b[k] || ''));
    const docsNew = Object.keys(sub.docs || {}).filter(k => (subs[i - 1].docs || {})[k] !== sub.docs[k]);
    const sens = k => ['account_number', 'iban'].includes(k);
    return h('div', { class: 'diff', style: 'margin:6px 0 12px' }, h('b', {}, `v${sub.version}`), h('span', { class: 'faint xs' }, ` · ${fmt(sub.created_at)} · ${sub.source}`),
      keys.length ? keys.map(k => h('div', {}, h('span', { class: 'muted' }, k.replace(/_/g, ' ') + ': '), h('del', {}, sens(k) ? mask(a[k]) : (a[k] || '∅')), ' → ', h('ins', {}, sens(k) ? mask(b[k]) : (b[k] || '∅')))) : h('div', { class: 'muted' }, 'No form changes'),
      docsNew.length ? h('div', { class: 'muted' }, 'New documents: ' + docsNew.join(', ')) : null);
  }).filter(Boolean);

  const EV = { vendor_created: 'Vendor created', vendor_seeded: 'Existing vendor loaded', submission_received: 'Submission received', run_started: 'Run started', stage_completed: null,
    decision: 'Decision', email_drafted: 'Message drafted', reviewer_override: 'Reviewer override', review_action: 'Reviewer action', reminder_sent: 'Reminder sent', request_closed_no_response: 'Closed: no response' };
  const tl = d.audit.filter(a => EV[a.event] !== null).map(a => {
    const p = a.payload || {};
    const detail = a.event === 'decision' ? `${p.status} / ${p.substatus} · risk ${p.risk_score}` + (p.resolved && p.resolved.length ? ` · resolved ${p.resolved.join(', ')}` : '')
      : a.event === 'review_action' ? `${p.actor}: ${p.action} → ${p.to}. “${p.reason}”` : a.event === 'email_drafted' ? `${p.audience}: ${p.subject}`
      : a.event === 'submission_received' ? `version ${p.version} · ${(p.documents || []).join(', ')}` : a.event === 'reminder_sent' ? `reminder ${p.number}` : '';
    return h('li', {}, h('div', {}, h('b', {}, EV[a.event] || a.event), detail ? h('span', { class: 'muted' }, ' — ' + detail) : null), h('div', { class: 'when' }, fmt(a.at) + ' · #' + a.seq + ' · ' + a.hash.slice(0, 10)));
  });

  const lastRunAt = lastRun ? lastRun.finished_at : '';
  const humanActs = d.overrides.filter(o => o.at >= lastRunAt);
  const humanNote = humanActs.length ? h('div', { class: 'note ' + (v.status === 'approved' ? 'ok' : v.status === 'rejected' ? 'err' : 'warn'), style: 'margin-bottom:12px' },
    h('b', {}, 'After review: '), humanActs.map(o => `${o.actor} → ${(STATUS[o.new_status] || [o.new_status])[0]} (“${o.reason}”)`).join(' · ')) : null;
  const blocking = res ? res.findings.filter(f => f.action !== 'none') : [];
  const info = res ? res.findings.filter(f => f.action === 'none') : [];
  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {},
      h('div', { class: 'row', style: 'margin-bottom:6px' }, h('a', { href: '#/vendors', class: 'sm' }, '← Vendors'), h('span', { class: 'id' }, v.vendor_id), h('span', { class: 'tag' }, 'v' + v.version)),
      h('h1', {}, v.legal_name), h('p', { class: 'sub' }, `${FLAG[v.country] || ''} ${COUNTRY[v.country] || v.country} · tax ID ${v.tax_id || '—'} · updated ${fmt(v.updated_at)}`)),
      h('div', { class: 'actions' }, pill(v.substatus, true),
        h('button', { class: 'btn sm', onclick: () => copy(location.origin + v.portal_path, 'Vendor portal link copied') }, 'Copy vendor link'),
        h('a', { class: 'btn sm', href: v.portal_path, target: '_blank' }, 'Open vendor portal'),
        h('a', { class: 'btn sm', href: '/api/audit/export?vendor_id=' + id }, 'Export audit'),
        latest ? h('a', { class: 'btn sm', href: '#/run/' + latest.submission_id }, 'Replay latest run') : null)),
    h('div', { class: 'split' },
      h('div', {},
        panel,
        res ? h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Latest automated decision'), h('span', { class: 'xs faint' }, `${lastRun.run_id} · ${res.llm_mode} AI · ${res.seconds}s`)), humanNote,
          h('div', { class: 'gauge', style: 'margin-bottom:10px' }, gauge(res.risk_score, res.risk_limit), h('div', {}, h('p', { style: 'margin:0' }, res.explanation), h('p', { class: 'xs faint', style: 'margin:6px 0 0' }, res.rule_trace.join(' · ')))),
          h('h3', {}, 'Path to approval'), actionsEl(res.next_actions),
          res.resolved_since_last_run && res.resolved_since_last_run.length ? h('div', { class: 'note ok', style: 'margin-top:12px' }, 'Resolved since the previous version: ' + res.resolved_since_last_run.join(', ')) : null,
          blocking.length ? [h('h3', { style: 'margin-top:16px' }, 'What needs attention'), blocking.map(findingEl)] : null,
          info.length ? h('details', { style: 'margin-top:12px' }, h('summary', { class: 'sm', style: 'cursor:pointer;color:var(--accent)' }, `${info.length} observation(s) recorded, not blocking`), info.map(findingEl)) : null)
          : h('div', { class: 'card' }, h('p', { class: 'muted' }, 'Existing approved vendor (loaded from the vendor master). No run yet.')),
        d.emails.length ? h('div', { class: 'card' }, h('h2', {}, 'Messages'), h('div', { style: 'display:flex;flex-direction:column;gap:10px' }, [...d.emails].reverse().map(mailEl))) : null),
      h('div', {},
        latest && Object.keys(latest.docs || {}).length ? h('div', { class: 'card' }, h('h2', {}, 'Documents on file'),
          Object.keys(latest.docs).map(k => h('div', { class: 'case', style: 'margin-bottom:6px' }, h('div', { class: 'ct' }, k.replace('_', ' '), h('small', {}, 'version ' + latest.version)),
            h('a', { class: 'btn sm', href: `/api/submissions/${latest.submission_id}/doc/${k}`, target: '_blank' }, 'Open')))) : null,
        diffs.length ? h('div', { class: 'card' }, h('h2', {}, 'What changed between versions'), diffs) : null,
        h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Timeline & audit'), h('button', { class: 'btn sm', onclick: async () => { const r = await api('/audit/verify'); toast(r.ok ? `Audit chain intact: ${r.checked} events verified` : `Audit chain BROKEN at event ${r.broken_at_seq}`, !r.ok); } }, 'Verify chain')),
          h('ul', { class: 'timeline' }, tl.reverse())))));
}

// =========================================================== new submission ===
const PACKS = {
  happy: ['Happy path', 'Clean vendors in four countries. Expect: approved, with every check explained.', '✅'],
  edge1: ['Looks fine, isn\'t', 'Lookalike names, a bank-change request, a shared bank account. Expect: held for a reviewer, vendor not told why.', '🕵️'],
  edge5: ['Documents that lie', 'A bank letter with a pasted-over account number; a valid GSTIN that belongs to someone else. Expect: held for review.', '🧾'],
  edge2: ['Looks wrong, isn\'t', '"Pvt Ltd" vs "Private Limited", trade names, abbreviations. Expect: approved, with the variant recorded.', '🤝'],
  edge3: ['Wait and resume', 'Something missing → vendor asked → vendor replies → re-checked. Expect: approved on version 2.', '🔁'],
  edge4: ['Subtle contradictions', 'Each field is valid on its own; only cross-referencing exposes it. Expect: held for review.', '🧩'],
  fixable: ['Vendor can fix it', 'Typos, wrong / stale / blurry documents. Expect: a precise, friendly request to the vendor.', '✉️'],
  reject: ['Compliance', 'Sanctions-list match (auto-reject) vs near-match (a human decides).', '⛔'],
};
const FIELD_GROUPS = [
  ['Company', [['legal_name', 'Registered legal name'], ['trade_name', 'Trade name (if different)'], ['entity_type', 'Legal form', [['pvt_ltd', 'Private limited'], ['ltd', 'Limited'], ['llp', 'LLP'], ['gmbh', 'GmbH'], ['ag', 'AG'], ['plc', 'PLC'], ['llc', 'LLC'], ['inc', 'Inc'], ['partnership', 'Partnership'], ['sole_prop', 'Sole proprietor']]],
    ['country', 'Country', [['IN', 'India'], ['DE', 'Germany'], ['GB', 'United Kingdom'], ['US', 'United States']]], ['address', 'Registered address'], ['city', 'City'], ['postal_code', 'Postal / PIN code']]],
  ['Tax & contact', [['tax_id', 'Tax ID (GSTIN / USt-IdNr / VAT / EIN)'], ['contact_name', 'Contact person'], ['contact_email', 'Contact email']]],
  ['Bank', [['bank_name', 'Bank name'], ['account_holder', 'Name on the account'], ['currency', 'Currency', [['INR', 'INR'], ['EUR', 'EUR'], ['GBP', 'GBP'], ['USD', 'USD']]],
    ['account_number', 'Account number'], ['ifsc', 'IFSC (India)'], ['iban', 'IBAN (DE / UK)'], ['sort_code', 'Sort code (UK)'], ['routing_number', 'Routing number (US)']]],
];
function formFields(values = {}, prefix = 'f') {
  const inputs = {};
  const root = FIELD_GROUPS.map(([title, fields]) => h('fieldset', {}, h('legend', {}, title), h('div', { class: 'fgrid' }, fields.map(([k, label, opts]) => {
    const el = opts ? h('select', { id: `${prefix}-${k}` }, opts.map(([v, l]) => h('option', { value: v }, l))) : h('input', { id: `${prefix}-${k}`, autocomplete: 'off' });
    el.value = values[k] || (opts ? opts[0][0] : ''); inputs[k] = el;
    return h('div', { class: 'field' }, h('label', { for: `${prefix}-${k}` }, label), el);
  }))));
  return { root, values: () => Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value.trim()])) };
}
const DOC_SLOTS = [['tax_cert', 'Tax registration certificate', 'GST certificate, VAT certificate or W-9'], ['bank_proof', 'Bank letter or cancelled cheque', 'Issued in the last 90 days'], ['incorporation', 'Certificate of incorporation', 'Optional']];

async function newView(view) {
  const [samples, scenarios] = await Promise.all([api('/samples'), api('/synthetic/scenarios')]);
  const groups = {}; samples.forEach(c => (groups[c.group] = groups[c.group] || []).push(c));
  const run = async (btn, url, extra = '') => {
    btn.disabled = true;
    try { const r = await api(url, { method: 'POST' }); location.hash = `#/run/${r.submission_id}?${extra}`; } catch (e) { toast(e.message, true); btn.disabled = false; }
  };
  const sel = h('select', { 'aria-label': 'Scenario' }, h('option', { value: 'random' }, '🎲 Random scenario'), scenarios.map(x => h('option', { value: x.id }, x.title)));
  const surprise = h('button', { class: 'btn', onclick: async e => {
    e.currentTarget.disabled = true;
    try { const r = await api('/synthetic/submit?scenario=' + sel.value, { method: 'POST' });
      location.hash = `#/run/${r.submission_id}?exp=${r.expected}&sc=${encodeURIComponent(r.title + (r.note ? ' (' + r.note + ')' : ''))}`; } catch (err) { toast(err.message, true); e.currentTarget.disabled = false; }
  } }, 'Generate & run');
  const f = formFields({ country: 'IN', currency: 'INR' }, 'n');
  const files = {};
  const slots = DOC_SLOTS.map(([k, label, hint]) => { const el = h('input', { type: 'file', id: 'n-' + k, accept: '.pdf,.png,.jpg,.jpeg,.webp' }); files[k] = el; return h('div', { class: 'field' }, h('label', { for: 'n-' + k }, label), el, h('span', { class: 'hint' }, hint)); });
  const submit = h('button', { class: 'btn primary', type: 'submit' }, 'Submit & run');
  const manual = h('form', { onsubmit: async e => {
    e.preventDefault(); submit.disabled = true;
    const fd = new FormData(); fd.append('form', JSON.stringify(f.values())); for (const [k, el] of Object.entries(files)) if (el.files[0]) fd.append(k, el.files[0]);
    try { const r = await api('/submissions', { method: 'POST', body: fd }); location.hash = '#/run/' + r.submission_id; } catch (err) { toast(err.message, true); submit.disabled = false; }
  } }, f.root, h('fieldset', {}, h('legend', {}, 'Documents'), h('div', { class: 'fgrid' }, slots)), submit);

  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'New submission'), h('p', { class: 'sub' }, 'Take the tour, generate a vendor the system has never seen, or enter one by hand.')),
      h('div', { class: 'actions' }, h('a', { class: 'btn sm', href: '#/new', onclick: e => { e.preventDefault(); const d = $('#manual'); d.open = true; d.scrollIntoView({ behavior: 'smooth' }); } }, 'Enter my own vendor ↓'))),
    h('h2', { style: 'margin:0 0 4px' }, 'Start here: the happy path and four edge cases'),
    h('p', { class: 'muted sm', style: 'margin:0 0 12px' }, 'Each one runs live through the real process. The label shows the outcome to expect.'),
    tourCard(false),
    h('div', { class: 'hero', style: 'margin-top:22px' }, h('div', { style: 'flex:1;min-width:260px' }, h('h2', {}, 'Surprise me: an unseen synthetic vendor'),
      h('p', {}, 'Generates a brand-new company with valid IDs and freshly rendered documents, runs it, and shows the ground-truth label next to the decision.')),
      h('div', { class: 'row' }, sel, surprise)),
    h('h2', { style: 'margin:26px 0 4px' }, 'All prepared cases'),
    h('p', { class: 'muted sm', style: 'margin:0 0 12px' }, `${samples.length} cases in ${Object.keys(groups).length} groups, for going deeper after the tour.`),
    h('div', { class: 'packs' }, Object.entries(PACKS).filter(([g]) => groups[g]).map(([g, [title, desc, icon]]) => h('div', { class: 'pack' },
      h('h3', {}, h('span', {}, icon), title), h('p', { class: 'desc' }, desc),
      groups[g].map(c => h('div', { class: 'case' }, h('div', { class: 'ct' }, c.title, h('small', {}, `${c.id} · ${FLAG[c.form.country] || ''} ${c.form.legal_name}`)),
        h('button', { class: 'btn sm primary', onclick: e => run(e.currentTarget, `/samples/${c.id}/submit`, c.has_follow_up ? `sample=${c.id}` : '') }, 'Run')))))),
    h('details', { class: 'card', id: 'manual', style: 'margin-top:20px' }, h('summary', { style: 'cursor:pointer;font-weight:650' }, 'Enter a vendor manually (with your own PDFs or photos)'),
      h('p', { class: 'muted sm', style: 'margin:10px 0 0' }, 'Anything works, including a blank form, the wrong file or a made-up company: the process says exactly what is missing or wrong. Files: PDF or image, up to 6 MB.'),
      h('div', { style: 'margin-top:14px' }, manual)));
}

// ======================================================= document rendering ===
// pdf.js renders the page in every browser and gives text positions, so we can put the
// evidence ON the document: green = value the AI extracted and code verified in the file,
// red = the same field printed twice with different values (a value pasted over another).
let PDFJS = null;
async function loadPdfjs() {
  if (PDFJS) return PDFJS;
  await new Promise((res, rej) => { const sc = document.createElement('script'); sc.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js'; sc.onload = res; sc.onerror = rej; document.head.append(sc); });
  window.pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  return (PDFJS = window.pdfjsLib);
}
const alnum = x => String(x || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
async function renderDoc(box, url, fieldValues) {
  box.replaceChildren(h('div', { class: 'skeleton', style: 'margin:16px;height:300px' }));
  let type = '';
  try { type = (await fetch(url, { method: 'HEAD' })).headers.get('content-type') || ''; } catch (_) {}
  if (type.startsWith('image/')) { box.replaceChildren(h('img', { src: url, alt: 'document' })); return { conflicts: 0 }; }
  try {
    const lib = await loadPdfjs();
    const pdf = await lib.getDocument(url).promise, page = await pdf.getPage(1);
    // zoom to where the text actually is, so the evidence is readable in a narrow panel
    const base = page.getViewport({ scale: 1 }), tc0 = await page.getTextContent();
    let minX = base.width, maxX = 0, minY = base.height;
    tc0.items.forEach(i => { if (!i.str.trim()) return; const t = lib.Util.transform(base.transform, i.transform); minX = Math.min(minX, t[4]); maxX = Math.max(maxX, t[4] + i.width); minY = Math.min(minY, t[5] - 20); });
    const fit = (box.clientWidth - 2) / base.width, zoom = maxX > minX ? (box.clientWidth - 24) / (maxX - minX + 30) : fit;
    const scale = Math.min(2.4, Math.max(fit, zoom)), vp = page.getViewport({ scale });
    const dpr = window.devicePixelRatio || 1;
    const canvas = h('canvas', { width: Math.floor(vp.width * dpr), height: Math.floor(vp.height * dpr), style: `width:${vp.width}px;height:${vp.height}px` });
    const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
    await page.render({ canvasContext: ctx, viewport: vp }).promise;
    const tc = await page.getTextContent();
    const items = tc.items.filter(i => i.str && i.str.trim()).map(i => {
      const t = lib.Util.transform(vp.transform, i.transform), fh = Math.hypot(t[2], t[3]);
      return { str: i.str, x: t[4], y: t[5] - fh, w: i.width * scale, h: fh * 1.3 };
    });
    const byLabel = {};
    items.forEach(it => { const m = it.str.match(/^([^:]{2,40}):\s*(.+)$/); if (m) { const k = m[1].trim().toLowerCase(); (byLabel[k] = byLabel[k] || new Set()).add(alnum(m[2])); it.label = k; } });
    const vals = Object.values(fieldValues || {}).map(alnum).filter(v => v.length >= 3);
    const wrap = h('div', { class: 'pdfwrap', style: `width:${vp.width}px;height:${vp.height}px` }, canvas);
    let conflicts = 0;
    for (const it of items) {
      const bad = it.label && byLabel[it.label].size > 1;
      const ok = !bad && vals.some(v => alnum(it.str).includes(v));
      if (bad) conflicts++;
      if (bad || ok) wrap.append(h('div', { class: 'hl ' + (bad ? 'bad' : 'ok'), title: bad ? `"${it.label}" appears more than once with different values: one is hidden under the visible page` : 'Extracted and verified in the file text',
        style: `left:${it.x - 3}px;top:${it.y - 2}px;width:${it.w + 6}px;height:${it.h + 2}px` }));
    }
    box.replaceChildren(wrap);
    box.scrollLeft = Math.max(0, (minX - 14) * scale); box.scrollTop = Math.max(0, (minY - 14) * scale);
    return { conflicts };
  } catch (e) {
    box.replaceChildren(h('iframe', { src: url, title: 'document' }));
    return { conflicts: 0 };
  }
}

// ================================================================= live run ===
function stageDetails(name, d) {
  if (!d || !Object.keys(d).length) return null;
  const kv = obj => h('dl', { class: 'kv' }, Object.entries(obj).filter(([, v]) => v !== '' && v != null).map(([k, v]) => [h('dt', {}, k.replace(/_/g, ' ')), h('dd', {}, Array.isArray(v) ? (v.length ? v.map(x => Array.isArray(x) ? x.join(' ↔ ') : x).join(', ') : '—') : typeof v === 'object' ? JSON.stringify(v) : String(v))]));
  if (name === 'read_documents' && d.documents) return Object.entries(d.documents).map(([k, doc]) => h('div', { style: 'margin-top:8px' },
    h('div', { class: 'row sm' }, h('b', {}, k.replace('_', ' ')), h('span', { class: 'tag' }, 'looks like: ' + (doc.detected_as || '?')), h('span', { class: 'xs muted' }, doc.grounding)),
    h('table', { class: 'mini' }, h('tbody', {}, Object.entries(doc.fields || {}).map(([fk, fv]) => h('tr', {}, h('td', { class: 'muted' }, fk.replace(/_/g, ' ')), h('td', { class: 'mono' }, ['account_number', 'iban'].includes(fk) ? mask(fv) : fv),
      h('td', { class: 'ok-t xs' }, doc.grounding && doc.grounding.startsWith('verified') ? '✓ found in file' : '')))))));
  if (name === 'validate' && d.checks) return h('table', { class: 'mini' }, h('tbody', {}, d.checks.map(c => h('tr', {}, h('td', {}, c.field.replace(/_/g, ' ')), h('td', { class: c.ok ? 'ok-t' : 'bad-t' }, c.ok ? '✓ valid' : '✕ ' + (c.detail || 'invalid'))))));
  if (name === 'name_judgment' && d.judgments) return d.judgments.map(j => h('div', { class: 'note', style: 'margin-top:6px' }, h('b', {}, `“${j.a}” vs “${j.b}”`), h('div', { class: 'sm' }, `AI: ${j.same_entity} (${pct(j.confidence || 0, 0)}) → ${j.accepted ? 'accepted' : 'not accepted'}`), h('div', { class: 'sm muted' }, j.reasoning)));
  if (name === 'decision') return kv({ rule: (d.rule_trace || []).join(' · '), risk: `${d.risk_score} (auto-approve below ${d.risk_limit})`, driving: d.driving_codes });
  if (name === 'communicate' && d.emails) return d.emails.map(m => h('div', { class: 'sm', style: 'margin-top:4px' }, h('span', { class: 'who ' + (m.audience === 'vendor' ? 'vendor' : 'reviewer') }, m.audience), ' ', m.subject));
  if (name === 'cross_check') return kv({ 'country signals': d.signals ? Object.entries(d.signals).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v || '—'}`).join(' · ') : '', 'legal name used': d.authoritative_legal_name, 'account holder': d.account_holder_used, 'sent to AI judge': d.grey_zone_pairs });
  return kv(d);
}

async function runView(view, sid) {
  const qp = hashQuery(), exp = qp.get('exp'), sc = qp.get('sc'), sample = qp.get('sample');
  const ti = qp.get('tour') == null ? -1 : +qp.get('tour'), tstep = TOUR[ti];
  const tourNext = h('span', { class: 'row' });
  const tourStrip = tstep ? h('div', { class: 'tourstrip' }, h('span', { class: 'n' }, String(ti + 1)), h('div', { style: 'flex:1;min-width:220px' },
    h('b', {}, `Tour ${ti + 1} of ${TOUR.length}: ${tstep.name}`), h('div', { class: 'sm' }, tstep.watch)), tourNext) : null;
  const info = await api(`/submissions/${sid}/run`);
  const sub = info.submission || {};
  const els = {}, bar = h('b'), started = Date.now();
  let done = 0, findings = {}, mode = HEALTH ? HEALTH.llm_mode : '';
  const list = h('ol', { class: 'stages' }, STAGES.map((k, i) => {
    const [title, what, ai] = STAGE_INFO[k];
    const dot = h('span', { class: 'dot' }, String(i + 1)), hl = h('div', { class: 'hl' }), ms = h('span', { class: 'ms' }), body = h('div');
    const li = h('li', { class: 'stage pending' }, dot, h('div', { class: 'sbox' }, h('div', { class: 't' }, title, ai ? h('span', { class: 'tag', title: 'Uses the AI model' }, 'AI') : null, ms), h('div', { class: 'what' }, what), hl, body));
    els[k] = { li, dot, hl, ms, body }; return li;
  }));
  const decisionBox = h('div'), docsBox = h('div'), banner = h('div');
  const docTabs = (sub.documents || []);
  function showDocs(fields) {
    if (!docTabs.length) { docsBox.replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'Documents'), h('div', { class: 'empty' }, 'No documents were attached.'))); return; }
    let cur = docTabs.includes('bank_proof') ? 'bank_proof' : docTabs[0];
    const tabs = h('div', { class: 'tabs' }), frame = h('div', { class: 'docview' }), table = h('div'), legend = h('div', { class: 'legend', style: 'margin-top:8px' });
    function draw() {
      tabs.replaceChildren(docTabs.map(k => h('button', { 'aria-pressed': String(k === cur), onclick: () => { cur = k; draw(); } }, k.replace('_', ' '))));
      const url = `/api/submissions/${sid}/doc/${cur}`;
      const fl = fields && fields[cur];
      renderDoc(frame, url, fl ? fl.fields : null).then(r => legend.replaceChildren(
        h('span', {}, h('i', { style: 'background:rgba(16,185,129,.5)' }), 'extracted & verified in the file'),
        r.conflicts ? h('span', { class: 'bad-t' }, h('i', { style: 'background:rgba(239,68,68,.7)' }), 'same field printed twice with different values') : null));
      table.replaceChildren(fl ? h('div', { style: 'margin-top:10px' }, h('div', { class: 'row xs muted', style: 'margin-bottom:4px' }, h('span', { class: 'tag' }, 'read as: ' + (fl.detected_as || '?')), fl.grounding),
        h('table', { class: 'mini' }, h('tbody', {}, Object.entries(fl.fields || {}).map(([k, v]) => h('tr', {}, h('td', { class: 'muted' }, k.replace(/_/g, ' ')), h('td', { class: 'mono' }, ['account_number', 'iban'].includes(k) ? mask(v) : v)))))) : null);
    }
    draw();
    docsBox.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Documents'), tabs), frame, legend, table));
  }
  function setStage(k, st, ev) {
    const e = els[k]; if (!e) return;
    e.li.className = 'stage ' + st;
    if (st === 'pass' || st === 'flag' || st === 'error') e.dot.textContent = st === 'pass' ? '✓' : st === 'flag' ? '!' : '✕';
    if (ev) {
      e.hl.textContent = ev.headline || ''; e.ms.textContent = ev.ms != null ? ev.ms + ' ms' : '';
      const fs = (ev.findings || []).filter(f => f.action !== 'none' || f.points);
      const det = stageDetails(k, ev.details);
      e.body.replaceChildren(fs.map(findingEl), det ? h('details', {}, h('summary', {}, 'Show evidence'), det) : null);
    }
  }
  async function finish() {
    const r = await api(`/submissions/${sid}/run`);
    if (!r.run) return;
    const res = r.run.result, cls = clsOf(res.substatus);
    bar.style.width = '100%';
    showDocs(res.documents);
    if (exp) {
      const ok = (exp.startsWith('rejected') && res.status === 'rejected') || exp === res.substatus;
      banner.replaceChildren(h('div', { class: 'labelcmp ' + (ok ? 'match' : 'miss') }, h('b', {}, ok ? '✓ Matches the ground-truth label' : '✕ Differs from the label'),
        h('span', {}, `expected: ${(STATUS[exp] || [exp])[0]} · decided: ${(STATUS[res.substatus] || [res.substatus])[0]}`)));
    }
    const emails = r.run.emails || [];
    if (tstep) {
      const isReplyCase = !!tstep.reply, waitReply = isReplyCase && res.substatus === 'awaiting_vendor';
      const want = isReplyCase && sub.version > 1 ? 'approved' : tstep.expect;
      tourNext.replaceChildren(want === res.substatus ? h('span', { class: 'sm', style: 'font-weight:650' }, '✓ as expected') : null,
        waitReply ? h('span', { class: 'sm' }, 'Now simulate the reply, below →') : ti + 1 < TOUR.length ? h('button', { class: 'btn sm primary', onclick: e => tourRun(ti + 1, e.currentTarget) }, `Next: ${TOUR[ti + 1].name} →`) : h('a', { class: 'btn sm primary', href: '#/' }, 'Finish: see the dashboard →'));
    }
    const tourReply = tstep && tstep.reply;
    const follow = (sample || tourReply) && res.substatus === 'awaiting_vendor' ? h('button', { class: 'btn primary', onclick: async e => {
      e.currentTarget.disabled = true;
      try { const x = await api(tourReply ? `/tour/missing-doc/reply?vendor_id=${sub.vendor_id}` : `/samples/${sample}/submit?follow_up=true&vendor_id=${sub.vendor_id}`, { method: 'POST' }); location.hash = '#/run/' + x.submission_id + (tstep ? '?tour=' + ti : ''); } catch (err) { toast(err.message, true); }
    } }, '↩ Simulate the vendor replying') : null;
    decisionBox.replaceChildren(h('div', { class: 'decision ' + cls },
      h('div', { class: 'row', style: 'justify-content:space-between;margin-bottom:10px' }, pill(res.substatus, true), h('span', { class: 'xs faint' }, `${r.run.run_id} · ${res.seconds}s · ${res.llm_mode} AI`)),
      h('div', { class: 'gauge' }, gauge(res.risk_score, res.risk_limit), h('p', { style: 'margin:0' }, res.explanation)),
      res.ai_backup && res.ai_backup.length ? h('div', { class: 'note warn', style: 'margin-top:10px' }, h('b', {}, 'AI model unavailable for this run. '), `The built-in reader read the text of: ${res.ai_backup.join(', ')}. Every value was still verified against the file, and the same rules decided. Photos and scans are never read this way; they wait for a person.`) : null,
      res.resolved_since_last_run && res.resolved_since_last_run.length ? h('div', { class: 'note ok', style: 'margin-top:10px' }, 'Resolved since the last version: ' + res.resolved_since_last_run.join(', ')) : null,
      h('h3', { style: 'margin-top:14px' }, 'Path to approval'), actionsEl(res.next_actions),
      emails.length ? [h('h3', { style: 'margin-top:14px' }, 'Drafted message'), emails.map(mailEl)] : null,
      h('div', { class: 'row', style: 'margin-top:14px' }, follow, h('a', { class: 'btn', href: '#/vendor/' + r.run.vendor_id }, 'Open vendor'), h('a', { class: 'btn quiet', href: '#/new' }, 'Run another'))));
    updateRail();
  }
  view.replaceChildren(
    h('div', { class: 'runhead' }, h('div', {}, h('div', { class: 'row', style: 'margin-bottom:6px' }, h('a', { href: '#/new', class: 'sm' }, '← New submission'), h('span', { class: 'id' }, sid), /^example/.test(sub.source || '') ? h('span', { class: 'tag' }, 'example') : null, sub.version > 1 ? h('span', { class: 'tag' }, 'version ' + sub.version) : null),
      h('h1', {}, sub.legal_name || 'Submission'), sc ? h('p', { class: 'sub muted', style: 'margin-top:4px' }, 'Unseen synthetic vendor · ' + sc) : null),
      h('div', { class: 'actions' }, HEALTH ? h('span', { class: 'chip ' + (HEALTH.llm_mode === 'mock' ? 'mock' : 'live') }, HEALTH.llm_mode === 'mock' ? `Offline AI · paced ${HEALTH.stage_pace_ms} ms/stage` : 'Live AI · ' + HEALTH.provider) : null)),
    tourStrip, h('div', { class: 'progress' }, bar), banner,
    h('div', { class: 'split' }, h('div', {}, list), h('div', { class: 'sticky' }, decisionBox, h('div', { style: 'height:16px' }), docsBox)));
  showDocs(null);

  if (info.run && !info.live) {               // finished earlier: replay instantly
    const res = info.run.result || {};
    const byCode = {}; (res.findings || []).forEach(f => (byCode[f.code] = byCode[f.code] || []).push(f));
    for (const st of info.run.steps || []) {
      const det = st.details || {}; const codes = det.new_findings || [];
      const fs = codes.map(c => (byCode[c] || []).shift()).filter(Boolean);
      setStage(st.stage, st.status, { headline: st.headline, ms: st.ms, details: det, findings: fs });
    }
    await finish(); return;
  }
  decisionBox.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'row' }, h('span', { class: 'dot', style: 'position:static;border-color:var(--accent)' }, '…'), h('div', {}, h('b', {}, 'Running'), h('div', { class: 'sm muted' }, 'Each stage appears as it executes. The decision lands here.')))));
  const es = new EventSource(`/api/submissions/${sid}/events`);
  cleanup = () => es.close();
  es.addEventListener('run_started', e => { const d = JSON.parse(e.data); mode = d.llm_mode; });
  es.addEventListener('stage_start', e => { const d = JSON.parse(e.data); setStage(d.stage, 'running'); els[d.stage] && els[d.stage].li.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); });
  es.addEventListener('stage_end', e => { const d = JSON.parse(e.data); done++; bar.style.width = (done / STAGES.length * 92) + '%'; setStage(d.stage, d.status, d);
    if (d.stage === 'read_documents' && d.details && d.details.documents) showDocs(d.details.documents); });
  es.addEventListener('run_failed', e => { decisionBox.replaceChildren(errorBox(new Error(JSON.parse(e.data).error))); });
  es.addEventListener('end', () => { es.close(); finish(); });
  es.addEventListener('error', () => { es.close(); setTimeout(finish, 600); });
}

// =================================================================== outbox ===
async function outboxView(view) {
  const mails = await api('/emails');
  let aud = 'all';
  const tabs = h('div', { class: 'tabs' }), list = h('div', { style: 'display:flex;flex-direction:column;gap:12px' });
  function draw() {
    tabs.replaceChildren([['all', 'All'], ['vendor', 'To vendors'], ['reviewer', 'Internal']].map(([k, l]) => h('button', { 'aria-pressed': String(aud === k), onclick: () => { aud = k; draw(); } }, l)));
    const rows = mails.filter(m => aud === 'all' || m.audience === aud);
    list.replaceChildren(rows.length ? rows.map(m => h('div', {}, h('p', { class: 'xs muted', style: 'margin:0 0 4px' }, h('a', { href: '#/vendor/' + m.vendor_id }, m.legal_name || m.vendor_id), ` · ${m.kind} · ${fmt(m.created_at)} · ${m.status}`), mailEl(m))) : h('div', { class: 'empty' }, 'No messages yet.'));
  }
  draw();
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Outbox'), h('p', { class: 'sub' }, 'Every message the process wrote. Vendor emails are scanned by code for internal terms before they could go out; nothing is sent to real vendors from the demo.')), tabs), list);
}


// --------------------------------------------------- robustness benchmarks (blind + diverse documents) ---
async function benchCard(host) {
  let b; try { b = await api('/benchmark'); } catch (e) { host.replaceChildren(); return; }
  const sets = b.sets || {}; const live = (b.live && b.live.sets) || {};
  const NAME = { blind: 'Blind test 1', diverse: 'Messy documents', holdout: 'Blind test 2 (ChatGPT)', fresh: 'Blind test 3' };
  const WHO = { blind: 'Cases and answers by an independent author who never saw the code', diverse: 'Tables, letters, stamps, German, phone photos, pasted-over values',
    holdout: 'Written by ChatGPT from a description of the process only', fresh: 'A new author, told to avoid everything already tested' };
  const acc = m => m ? pct(m.decision_accuracy) : '–', uns = m => m ? String(m.errors.unsafe_approvals + m.errors.missed_rejects) : '–';
  const liveCell = k => live[k] ? (live[k].answered_accuracy != null ? pct(live[k].answered_accuracy) : acc(live[k].metrics)) : 'not run';
  const rows = Object.entries(sets).map(([k, v]) => h('tr', {}, h('td', {}, h('b', {}, NAME[k] || k), h('div', { class: 'xs muted', style: 'font-weight:400' }, WHO[k] || '')),
    h('td', { class: 'num' }, v.n), h('td', { class: 'num', style: 'font-weight:700' }, acc(v.first_look)), h('td', { class: 'num' }, acc(v.after_fixes)),
    h('td', { class: 'num' + (live[k] ? '' : ' faint') }, liveCell(k)),
    h('td', { class: 'num' }, h('span', { class: +uns(v.first_look) ? 'bad-t' : '' }, uns(v.first_look)), ' → ', h('span', { class: 'ok-t' }, uns(v.after_fixes)))));
  const reading = Object.values(live).map(x => x.reading).filter(Boolean);
  const rd = reading.length ? reading.reduce((a, r) => ({ c: a.c + r.fields_correct, n: a.n + r.fields_checked }), { c: 0, n: 0 }) : null;
  const status = h('div', { class: 'sm muted', style: 'margin-top:8px' });
  const drawJob = j => { if (!j || j.state === 'idle') { status.replaceChildren(); return; }
    const ok = (j.recent || []).filter(r => r.ok).length;
    status.replaceChildren(h('div', { class: 'note ' + (j.state === 'error' ? 'warn' : 'ok') }, j.state === 'running' ? `Live benchmark running: ${j.set}, ${j.done}/${j.total} vendors (${ok} correct so far, ${Math.round(j.seconds / 60)} min)` :
      j.state === 'done' ? `Live benchmark finished: ${ok}/${j.done} correct. Reload to see the table.` : `Live benchmark stopped: ${j.error}`)); };
  const runBtn = h('button', { class: 'btn sm', onclick: async e => {
    const k = prompt('Admin key (the live run uses about 3 model calls per vendor, ~25 minutes)'); if (!k) return;
    try { await api('/admin/benchmark', { method: 'POST', headers: { 'X-Admin-Key': k } }); toast('Live benchmark started'); } catch (err) { toast(err.message, true); return; }
    e.currentTarget.disabled = true;
    for (let i = 0; i < 2000; i++) { await sleep(5000); const x = await api('/benchmark'); drawJob(x.job); if (x.job.state !== 'running') break; }
    e.currentTarget.disabled = false; } }, 'Run live benchmark');
  const held = Object.entries(live).flatMap(([k, v]) => (v.failures || []).filter(f => (f.codes || []).includes('STAGE_ERROR')).map(f => [k, f.id]));
  const retryBtn = held.length ? h('button', { class: 'btn sm', onclick: async e => {
    const k = prompt(`Admin key. Reruns only the ${held.length} vendors the AI did not answer last time, one every 30 s (~${Math.ceil(held.length / 2)} min). Stops by itself if the AI quota is used up.`); if (!k) return;
    const q = `?sets=${[...new Set(held.map(x => x[0]))].join(',')}&ids=${held.map(x => x[1]).join(',')}&pace=30`;
    try { await api('/admin/benchmark' + q, { method: 'POST', headers: { 'X-Admin-Key': k } }); toast('Rerun started'); } catch (err) { toast(err.message, true); return; }
    const btn = e.currentTarget; btn.disabled = true;
    for (let i = 0; i < 2000; i++) { await sleep(5000); const x = await api('/benchmark'); drawJob(x.job); if (x.job.state !== 'running') break; }
    btn.disabled = false; } }, `Rerun ${held.length} unanswered`) : null;
  drawJob(b.job);
  const sum = t => h('summary', { style: 'cursor:pointer;font-weight:650;font-size:13px;color:var(--accent)' }, t);
  host.replaceChildren(h('div', { class: 'card' },
    h('div', { class: 'card-h' }, h('h2', {}, 'The hard tests: cases the rules were never written for')),
    h('p', { class: 'sm muted', style: 'margin:0 0 10px' }, 'I wrote both the rules and the 1,900 test vendors above, so that test cannot show my blind spots. These can. "First try" is the score before I changed anything, and it is the number I quote.'),
    h('div', { class: 'tablewrap', style: 'border:0' }, h('table', { class: 'cmp' }, h('thead', {}, h('tr', {}, ['Test', 'Cases', 'First try', 'After fixes', 'With live Gemini', 'Wrongly approved: first try → now'].map((x, i) => h('th', { class: i ? 'num' : '' }, x)))), h('tbody', {}, rows))),
    status,
    h('details', { style: 'margin-top:12px' }, sum('How to read this table'),
      h('ul', { class: 'sm muted', style: 'margin:8px 0 0;padding-left:18px' },
        h('li', {}, '"First try" and "After fixes" give the rules a perfect reading of each document, so they measure the rules alone.'),
        h('li', {}, '"With live Gemini" runs the real files through the deployed AI.' + (rd ? ` It read ${rd.c} of ${rd.n} printed fields exactly (${pct(rd.c / rd.n)}).` : '') + ' One vendor ran out of free AI quota and was held for a person, so it is left out. The run found one more wrong approval (a bank statement accepted as a bank letter), which is now double-checked in code.'),
        h('li', {}, '"Wrongly approved" counts vendors approved that should not have been, plus sanctioned parties not rejected.'),
        h('li', {}, 'Where "After fixes" is below 100%, the remaining cases are policy differences with the author, sent to a person here (the safe direction).'),
        h('li', {}, 'After every round of fixes the rules are probed on freshly generated vendors (132 of 133) to check they were not fitted to the test cases.')),
      h('div', { class: 'row', style: 'margin-top:10px' }, h('span', { class: 'xs muted' }, 'Admin:'), runBtn, retryBtn)),
    h('details', { style: 'margin-top:8px' }, h('summary', { style: 'cursor:pointer;font-weight:650;font-size:13px;color:var(--accent)' }, `What each test found, and how it was fixed (${(b.findings || []).length})`),
      h('table', { class: 'mini', style: 'margin-top:8px' }, h('tbody', {}, (b.findings || []).map(f => h('tr', {}, h('td', { class: 'xs muted', style: 'white-space:nowrap;vertical-align:top' }, NAME[f.set] || f.set),
        h('td', { class: 'sm' }, f.found, h('div', { class: 'xs', style: 'color:var(--ok, #10b981);margin-top:2px' }, '→ ' + f.fix)))))))));
}

// ============================================================ model quality ===
async function qualityView(view) {
  let q;
  try { q = await api('/quality'); } catch (e) { view.replaceChildren(h('h1', {}, 'Model quality'), errorBox(e)); return; }
  const S = q.splits, test = S.test, stress = S.stress;
  const tv = test.first_look || test.v2, tb = test.baseline;
  const big = (label, v, b, fmtf, good) => h('div', { class: 'kpi' }, h('div', { class: 'l', style: 'margin:0 0 6px' }, label), h('div', { class: 'bigstat' }, fmtf(v)),
    h('div', { class: 's' }, 'my first version: ', h('b', {}, fmtf(b)), ' ', h('span', { class: 'delta up' }, good)));
  const metricRows = [['Decision accuracy', m => pct(m.decision_accuracy)], ['Right decision for the right reason', m => pct(m.strict_accuracy)], ['Macro F1 (4 outcomes)', m => m.macro_f1.toFixed(3)],
    ['Unsafe approvals (should not have been approved)', m => m.errors.unsafe_approvals], ['Disclosure leaks (fraud case emailed to vendor)', m => m.errors.disclosure_leaks],
    ['Missed sanctions rejects', m => m.errors.missed_rejects], ['False holds (clean vendor not approved)', m => m.errors.false_holds], ['Straight-through rate (clean vendors)', m => pct(m.straight_through_rate)]];
  const cmp = (name, sp) => h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, `${name} · ${sp.v2 ? sp.v2.n : ''} vendors`), h('span', { class: 'xs muted' }, sp.description)),
    h('div', { class: 'tablewrap', style: 'border:0' }, h('table', { class: 'cmp' }, h('thead', {}, h('tr', {}, h('th', {}, 'Metric'), h('th', { class: 'num' }, 'Original'), sp.first_look ? h('th', { class: 'num' }, 'v2 first look') : null, h('th', { class: 'num' }, 'v2'))),
      h('tbody', {}, metricRows.map(([l, f]) => h('tr', {}, h('td', {}, l), h('td', { class: 'num b' }, sp.baseline ? f(sp.baseline) : '–'), sp.first_look ? h('td', { class: 'num v' }, f(sp.first_look)) : null, h('td', { class: 'num v' }, sp.v2 ? f(sp.v2) : '–')))))));
  const CL = ['approved', 'awaiting_vendor', 'awaiting_reviewer', 'rejected'], CLN = ['Approve', 'Ask vendor', 'Review', 'Reject'];
  const heat = (m, title) => { const max = Math.max(...CL.map(e => Math.max(...CL.map(g => m.confusion[e][g])))); return h('div', {}, h('h3', {}, title),
    h('table', { class: 'heat' }, h('tr', {}, h('th', {}, 'label ↓ / decided →'), CLN.map(x => h('th', {}, x))), CL.map((e, i) => h('tr', {}, h('th', { style: 'text-align:right' }, CLN[i]), CL.map(g => { const n = m.confusion[e][g]; const diag = e === g;
      const a = n ? 0.15 + 0.75 * n / max : 0; return h('td', {}, h('div', { style: `background:${n ? (diag ? `rgba(16,185,129,${a})` : `rgba(239,68,68,${Math.max(.35, a)})`) : 'var(--panel-2)'};color:${n && (a > .5 || !diag) ? '#fff' : 'var(--ink-2)'}`, title: `${CLN[i]} → ${CLN[CL.indexOf(g)]}: ${n}` }, n)); }))))); };
  const bars = sp => { const keys = Object.keys(sp.v2.per_scenario); return h('div', {}, keys.map(k => { const v = sp.v2.per_scenario[k], b = sp.baseline && sp.baseline.per_scenario[k];
    const vb = v.strict_ok / v.n, bb = b ? b.strict_ok / b.n : 0; return h('div', { class: 'hbar' }, h('span', { class: 'muted', title: k }, k.replace(/^x_/, '').replace(/_/g, ' ')),
      h('div', { class: 'track' }, h('b', { class: 'base', style: `width:${bb * 100}%` }), h('b', { class: 'v2', style: `width:${vb * 100}%` })), h('span', { class: 'num xs' }, pct(vb, 0))); })); };
  const benchHost = h('div'); benchCard(benchHost);
  const spotOut = h('div');
  const spotBtn = h('button', { class: 'btn primary', onclick: async e => {
    e.currentTarget.disabled = true; spotOut.replaceChildren(h('div', { class: 'skeleton', style: 'height:60px' }));
    try { const r = await api('/quality/spotcheck?n=4', { method: 'POST' });
      spotOut.replaceChildren(h('div', { class: 'note ' + (r.correct === r.n ? 'ok' : 'warn'), style: 'margin:10px 0' }, `${r.correct} of ${r.n} fresh vendors decided as labelled (seed ${r.seed}).`),
        h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['', 'Scenario', 'Expected', 'Decided', 'Findings'].map(x => h('th', {}, x)))),
          h('tbody', {}, r.rows.map(x => h('tr', {}, h('td', { class: x.decision_ok ? 'ok-t' : 'bad-t' }, x.decision_ok ? '✓' : '✕'), h('td', { style: 'max-width:360px' }, x.title, h('div', { class: 'id' }, x.country)),
            h('td', {}, pill(x.expected === 'rejected' ? 'rejected_compliance' : x.expected)), h('td', {}, pill(x.got === 'rejected' ? 'rejected_compliance' : x.got)), h('td', { class: 'xs muted' }, x.codes.join(', ') || '—')))))));
    } catch (err) { spotOut.replaceChildren(errorBox(err)); } e.currentTarget.disabled = false;
  } }, 'Spot-check 4 fresh vendors now');

  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Model quality'), h('p', { class: 'sub' }, 'Measured, not claimed. The top row is my own test data (the easy number). The table below it is the hard one: cases written by independent authors, scored before any fix.'))),
    h('div', { class: 'kpis' },
      big('Accuracy on my own held-out test data', tv.decision_accuracy, tb.decision_accuracy, x => pct(x), ''),
      big('Wrongly approved (same data)', tv.errors.unsafe_approvals, tb.errors.unsafe_approvals, x => String(x), ''),
      big('Fraud cases that tipped off the vendor', tv.errors.disclosure_leaks, tb.errors.disclosure_leaks, x => String(x), ''),
      big('Accuracy on variations I had not planned for', (stress.first_look || stress.v2).decision_accuracy, stress.baseline.decision_accuracy, x => pct(x), '')),
    benchHost,
    h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Validate on vendors generated right now'), spotBtn),
      h('p', { class: 'muted sm', style: 'margin:0' }, 'Each click generates fresh companies (random names, valid IDs, newly rendered PDFs and scans) across 25 scenarios, runs them, and scores them against their labels.'), spotOut),
    h('div', { class: 'grid2' }, h('div', { class: 'card' }, h('h2', {}, 'Where decisions land (held-out set)'), h('div', { class: 'row', style: 'gap:24px;align-items:flex-start' }, heat(tb, 'Original engine'), heat(test.v2, 'v2'))),
      h('div', { class: 'card' }, h('h2', {}, 'How the errors are weighted'), h('table', { class: 'mini' }, h('tbody', {},
        [['Unsafe approval', '50', 'Fraud or compliance loss'], ['Missed sanctions reject', '50', 'Legal exposure'], ['Disclosure leak', '10', 'Tells a fraudster which detail to forge'], ['Over-escalation', '2', 'Reviewer time'], ['False hold', '1', 'Vendor friction']].map(r => h('tr', {}, h('td', {}, h('b', {}, r[0])), h('td', { class: 'num mono' }, '×' + r[1]), h('td', { class: 'muted' }, r[2]))))),
        h('p', { class: 'sm muted', style: 'margin-top:10px' }, `Weighted error cost on the held-out set: ${tb.weighted_cost} → ${tv.weighted_cost}.`))),
    h('details', { class: 'card' }, h('summary', { style: 'cursor:pointer;font-weight:650' }, 'Full tables: every metric and every scenario for the 1,900 vendors'), h('div', { style: 'margin-top:14px' },
    cmp('Held-out test set', test), cmp('Stress set', stress), cmp('Tuning set', S.dev),
    h('div', { class: 'grid2', style: 'margin-top:16px' }, h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Stress set by scenario'), h('div', { class: 'legend' }, h('span', {}, h('i', { style: 'background:var(--faint)' }), 'original'), h('span', {}, h('i', { style: 'background:var(--accent)' }), 'v2'))), bars(stress)),
      h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h2', {}, 'Held-out set by scenario'), h('div', { class: 'legend' }, h('span', {}, h('i', { style: 'background:var(--faint)' }), 'original'), h('span', {}, h('i', { style: 'background:var(--accent)' }), 'v2'))), bars(test))))),
    h('div', { class: 'card' }, h('h2', {}, 'How to read this honestly'), h('ul', { style: 'margin:0;padding-left:18px' }, q.notes.concat([
      'The same person wrote the generator and the rules, so the stress set was written before measuring, to probe what the rules were not designed for. Its first-look score is reported, not just the final one.',
      'Real-world inputs will be messier than synthetic ones. The next step is a pilot on a few hundred historic submissions with known outcomes.']).map(n => h('li', { class: 'sm', style: 'margin:4px 0' }, n)))));
}

// ================================================================= rulebook ===
async function rulesView(view) {
  const { raw } = await rulebook(); const groups = {};
  raw.rules.forEach(r => (groups[r.category] = groups[r.category] || []).push(r));
  const NAMES = { completeness: 'Completeness', format: 'Format & check digits', consistency: 'Consistency across sources', fraud: 'Fraud & document integrity', compliance: 'Compliance', lifecycle: 'Lifecycle', system: 'System' };
  const cls = { vendor: 'wait', reviewer: 'hold', reject: 'bad', none: 'mute' };
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Rulebook'), h('p', { class: 'sub' }, 'Every decision traces back to a line here. A procurement lead can read it and change it. The AI cannot.'))),
    h('div', { class: 'card' }, h('h2', {}, 'Thresholds'), h('dl', { class: 'kv' }, Object.entries(raw.thresholds).map(([k, v]) => [h('dt', {}, k), h('dd', { class: 'mono' }, String(v))])),
      h('p', { class: 'sm muted', style: 'margin:10px 0 0' }, `Precedence: reject > hold for reviewer > ask vendor > risk limit > approve. Auto-approve only when risk is below ${raw.auto_approve_below_risk}.`)),
    Object.entries(NAMES).filter(([k]) => groups[k]).map(([k, n]) => h('div', { class: 'card' }, h('h2', {}, n), h('div', { class: 'tablewrap', style: 'border:0' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Rule', 'Why it exists', 'Effect', 'Risk'].map(x => h('th', {}, x)))),
      h('tbody', {}, groups[k].map(r => h('tr', {}, h('td', { style: 'min-width:240px' }, h('b', {}, r.title), h('div', { class: 'id' }, r.code)), h('td', { class: 'muted sm' }, r.why),
        h('td', {}, h('span', { class: 'pill ' + cls[r.action] }, h('i'), ACTION[r.action])), h('td', { class: 'num mono' }, '+' + r.points)))))))));
}

// ============================================================== how it works ===
function processView(view) {
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'How it works'), h('p', { class: 'sub' }, 'AI reads and explains. Code decides. People handle what is genuinely ambiguous.'))),
    h('div', { class: 'card' }, h('h2', {}, 'The nine stages (highlighted = uses the AI)'), h('div', { class: 'flow' }, STAGES.map(k => h('div', { class: STAGE_INFO[k][2] ? 'ai' : '' }, h('b', {}, STAGE_INFO[k][0]), STAGE_INFO[k][1])))),
    h('div', { class: 'grid2' },
      h('div', { class: 'card' }, h('h2', {}, 'Which outcome wins'), [['bad', 'Rejected', 'Exact sanctions-list match, or no reply after two reminders. The vendor is never told about screening.'],
        ['hold', 'Needs review', 'Fraud-shaped, not proven: lookalike, shared or changed bank account, a document edited after issue, IDs that belong to someone else. Bank changes and similar need two reviewers.'],
        ['wait', 'Waiting on vendor', 'Fixable: missing, blurry, wrong or stale documents, typos. The vendor gets a numbered list and a private link.'],
        ['ok', 'Approved', 'Nothing blocking and risk under 20. Harmless variants are recorded, not punished.']].map(([c, t, d]) => h('div', { class: 'row', style: 'align-items:flex-start;margin-bottom:10px;flex-wrap:nowrap' }, h('span', { class: 'pill ' + c, style: 'min-width:130px' }, h('i'), t), h('span', { class: 'sm muted' }, d)))),
      h('div', { class: 'card' }, h('h2', {}, 'Built-in safety'), h('ul', { style: 'margin:0;padding-left:18px' }, [
        'The AI must quote what it read; code verifies the quote is in the file.',
        'The raw PDF is inspected for edits: a pasted-over value leaves the original underneath.',
        'Only an exact sanctions match auto-rejects; near-matches go to a person.',
        'Any failed step or AI outage → a person. Never a guess, never an approval.',
        'Vendor emails are scanned by code for screening / fraud language.',
        'Four-eyes approval for bank changes, shared accounts, lookalikes and forged documents.',
        'A hash-chained audit trail: rewriting history breaks the chain.'].map(t => h('li', { class: 'sm', style: 'margin:5px 0' }, t))))),
    h('div', { class: 'card' }, h('h2', {}, 'Who sees what'), h('div', { class: 'tablewrap', style: 'border:0' }, h('table', {}, h('thead', {}, h('tr', {}, ['', 'Vendor', 'Reviewer'].map(x => h('th', {}, x)))), h('tbody', {},
      [['Missing / wrong documents', 'Told exactly what to send', 'Sees it too'], ['Fraud-shaped signals', 'Sees only "being reviewed"', 'Evidence + checklist + safe next step'], ['Sanctions match', 'Nothing', 'Escalated to compliance'], ['Risk score', 'Never', 'With every rule behind it']].map(r => h('tr', {}, r.map((c, i) => h('td', { style: i ? '' : 'font-weight:600' }, c)))))))));
}

// ============================================================== vendor portal ===
async function portalView(view, token) {
  let state;
  try { state = await api('/portal/' + token); }
  catch (e) { return view.replaceChildren(h('h1', {}, 'This link is not valid'), h('p', { class: 'muted' }, 'Please use the link in the email you received, or contact the procurement team.')); }
  const wrap = h('div', { style: 'padding-top:24px' }); view.replaceChildren(wrap);
  const header = () => [h('div', { class: 'brand', style: 'padding:0 0 18px' }, h('span', { class: 'logo' }), h('span', {}, h('b', {}, 'Supplier registration'), h('small', {}, 'Procurement team'))),
    h('h1', {}, state.company), h('p', { class: 'muted' }, 'Reference ' + state.reference)];
  function show() {
    if (state.state === 'checking') { wrap.replaceChildren(header(), h('div', { class: 'card', style: 'margin-top:16px' }, h('div', { class: 'skeleton', style: 'width:60%;margin-bottom:10px' }), state.message)); return; }
    if (state.state !== 'open') { wrap.replaceChildren(header(), h('div', { class: 'card', style: 'margin-top:16px' }, h('h2', {}, { approved: '✓ You are approved', review: 'We are reviewing your details', closed: 'This request is closed' }[state.state]), h('p', {}, state.message))); return; }
    const f = formFields(state.form, 'p'); const files = {};
    const slots = DOC_SLOTS.map(([k, label, hint]) => { const el = h('input', { type: 'file', id: 'p-' + k, accept: '.pdf,.png,.jpg,.jpeg,.webp' }); files[k] = el;
      return h('div', { class: 'field' }, h('label', { for: 'p-' + k }, label), el, h('span', { class: 'hint' }, state.documents_on_file.includes(k) ? 'Already on file. Upload only to replace it.' : hint)); });
    const btn = h('button', { class: 'btn primary', type: 'submit' }, 'Send to procurement');
    const form = h('form', { onsubmit: async e => { e.preventDefault(); btn.disabled = true;
      const fd = new FormData(); fd.append('form', JSON.stringify(f.values())); for (const [k, el] of Object.entries(files)) if (el.files[0]) fd.append(k, el.files[0]);
      try { await api(`/portal/${token}/respond`, { method: 'POST', body: fd }); } catch (err) { toast(err.message, true); btn.disabled = false; return; }
      state = { ...state, state: 'checking', message: 'Thanks. We are checking what you sent.' }; show();
      for (let i = 0; i < 120; i++) { await sleep(800); const x = await api('/portal/' + token); if (x.state !== 'checking') { state = x; show(); return; } } } },
      h('div', { class: 'card' }, h('h2', {}, 'Upload what we asked for'), h('div', { class: 'fgrid' }, slots)),
      h('details', { class: 'card' }, h('summary', { style: 'cursor:pointer;font-weight:600' }, 'Check or correct your details'), h('div', { style: 'margin-top:12px' }, f.root)),
      h('div', { style: 'margin-top:16px' }, btn));
    wrap.replaceChildren(header(), h('div', { class: 'card', style: 'margin:16px 0' }, h('p', { style: 'font-weight:650' }, state.message),
      state.asks.length ? h('ol', { style: 'margin:0;padding-left:20px' }, state.asks.map(a => h('li', { style: 'margin:6px 0' }, a))) : null,
      state.days_left != null ? h('p', { class: 'sm muted', style: 'margin:10px 0 0' }, state.days_left === 0 ? 'Please reply today.' : `Please reply within ${state.days_left} day${state.days_left === 1 ? '' : 's'}.`) : null), form);
  }
  show();
}
