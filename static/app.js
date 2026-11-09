'use strict';
/* Vendor onboarding UI. Plain JavaScript, no build step, no CDN: it works offline and deploys anywhere. */

// ------------------------------------------------------------------ helpers ---

// replaceChildren / append that accept nested arrays and skip null/false, like h() does
for (const m of ['replaceChildren', 'append']) {
  const orig = Element.prototype[m];
  Element.prototype[m] = function (...kids) { return orig.apply(this, kids.flat(Infinity).filter(k => k != null && k !== false)); };
}
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) { if (kid == null || kid === false) continue; el.append(kid.nodeType ? kid : document.createTextNode(String(kid))); }
  return el;
}
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
function toast(msg, err) {
  const t = h('div', { class: 'toast' + (err ? ' err' : '') }, msg);
  $('#toasts').append(t); setTimeout(() => t.remove(), err ? 7000 : 4200);
}
async function copy(text, label = 'Copied') {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch (_) { const ta = h('textarea', { style: 'position:fixed;left:-999px' }); ta.value = text; document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove(); toast(label); }
}
function fmt(ts) {
  if (!ts) return '';
  const d = new Date(ts); return isNaN(d) ? ts : d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}
const errorBox = e => h('div', { class: 'note err' }, h('b', {}, 'Something went wrong. '), e.message || String(e));
let cleanup = null;

const STATUS = {
  approved: ['Approved', 'ok'], awaiting_vendor: ['Waiting on vendor', 'wait'], awaiting_reviewer: ['Needs review', 'hold'],
  rejected_compliance: ['Rejected', 'bad'], rejected_closed: ['Closed: no response', 'bad'], rejected_manual: ['Rejected by reviewer', 'bad'],
  rejected: ['Rejected', 'bad'], pending: ['Pending', 'wait'], new: ['Not processed', 'mute'],
};
const pill = sub => { const [label, cls] = STATUS[sub] || [sub, 'mute']; return h('span', { class: 'pill ' + cls }, h('i'), label); };
const ACTION = { vendor: ['Asks the vendor', 'wait'], reviewer: ['Holds for a reviewer', 'hold'], reject: ['Rejects', 'bad'], none: ['Noted only', ''] };
const COUNTRY = { IN: 'India', DE: 'Germany', GB: 'United Kingdom', US: 'United States' };
let RULES = null;
async function rules() { if (!RULES) { const r = await api('/rulebook'); RULES = { map: Object.fromEntries(r.rules.map(x => [x.code, x])), raw: r }; } return RULES; }

// ------------------------------------------------------------------- shell ---
function updateRail(s) {
  const f = $('#railfoot'); if (!f) return;
  f.replaceChildren(
    h('span', { class: 'chip' + (s.llm_mode === 'mock' ? ' warn' : ''), title: s.llm_mode === 'mock' ? 'No API key: document reading is simulated offline' : 'Real model calls' },
      s.llm_mode === 'mock' ? 'Offline mock AI' : 'Live AI: ' + (s.provider || 'model')),
    s.clock_offset_days ? h('span', { class: 'chip', title: 'Demo clock has been moved forward' }, `Demo clock +${s.clock_offset_days} days`) : null);
}
function markNav(name) { document.querySelectorAll('[data-nav]').forEach(a => a.getAttribute('data-nav') === name ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')); }

// ------------------------------------------------------------------ router ---
const ROUTES = [
  [/^#\/?$/, dashboard, 'dashboard'], [/^#\/new$/, newSubmission, 'new'], [/^#\/run\/(.+)$/, runView, 'new'],
  [/^#\/vendor\/(.+)$/, vendorView, 'dashboard'], [/^#\/rules$/, rulesView, 'rules'], [/^#\/process$/, processView, 'process'],
  [/^#\/eval$/, evalView, 'eval'], [/^#\/outbox$/, outboxView, 'outbox'],
  [/^#\/assumptions$/, (v) => docView(v, 'assumptions'), 'assumptions'], [/^#\/design$/, (v) => docView(v, 'design'), 'process'],
];
async function route() {
  if (cleanup) { cleanup(); cleanup = null; }
  const view = $('#view');
  const portal = location.pathname.match(/^\/portal\/([^/]+)/);
  document.body.classList.toggle('portal', !!portal);
  if (portal) { document.title = 'Vendor registration'; return portalView(view, portal[1]); }
  document.title = 'Vendor onboarding';
  api('/stats').then(updateRail).catch(() => {});
  const hash = location.hash || '#/';
  for (const [re, fn, nav] of ROUTES) {
    const m = hash.match(re);
    if (m) {
      markNav(nav); view.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
      try { await fn(view, ...m.slice(1).map(decodeURIComponent)); } catch (e) { view.replaceChildren(errorBox(e)); }
      window.scrollTo(0, 0); return;
    }
  }
  view.replaceChildren(h('h1', {}, 'Page not found'), h('p', {}, h('a', { href: '#/' }, 'Back to vendors')));
}
window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

// --------------------------------------------------------------- dashboard ---
async function dashboard(view) {
  const [s, vendors] = await Promise.all([api('/stats'), api('/vendors')]);
  updateRail(s);
  const approved = vendors.filter(v => v.status === 'approved').length;
  const waiting = vendors.filter(v => v.substatus === 'awaiting_reviewer');
  let filter = 'all', q = '';

  const reset = h('button', { class: 'btn quiet', onclick: async (e) => {
    const b = e.currentTarget;
    if (!b.dataset.arm) { b.dataset.arm = '1'; b.textContent = 'Click again to wipe all demo data'; setTimeout(() => { delete b.dataset.arm; b.textContent = 'Reset demo data'; }, 4000); return; }
    try {
      let r;
      try { r = await api('/admin/reset', { method: 'POST' }); }
      catch (err) { if (err.status !== 403) throw err; const k = prompt('Admin key'); if (!k) return; r = await api('/admin/reset', { method: 'POST', headers: { 'X-Admin-Key': k } }); }
      toast(`Demo data reset: ${r.seeded_vendors} approved vendors restored`); route();
    } catch (err) { toast(err.message, true); }
  } }, 'Reset demo data');
  const advance = h('button', { class: 'btn', title: 'Pretend a week has passed: overdue vendors get reminders, and are closed after the last one', onclick: async (e) => {
    e.currentTarget.disabled = true;
    try {
      const r = await post('/admin/clock', { days: 7 });
      const rem = r.actions.filter(a => a.action === 'reminder').length, cl = r.actions.filter(a => a.action === 'closed').length;
      toast(`A week later (day +${r.clock_offset_days}): ${rem} reminder${rem === 1 ? '' : 's'} sent, ${cl} request${cl === 1 ? '' : 's'} closed`); route();
    } catch (err) { toast(err.message, true); e.currentTarget.disabled = false; }
  } }, 'Advance one week');

  const stat = (val, label, cls, extra) => h('div', { class: 'stat ' + (cls || '') }, h('b', {}, val), h('span', {}, label), extra);
  const strip = h('div', { class: 'strip' },
    stat(waiting.length, waiting.length === 1 ? 'vendor waiting on your decision' : 'vendors waiting on your decision', waiting.length ? 'attn' : ''),
    stat(s.awaiting_vendor, 'waiting on vendors', '', s.overdue ? h('div', { class: 'sm', style: 'color:var(--wait);font-weight:600' }, `${s.overdue} overdue`) : null),
    stat(approved, 'approved'),
    stat(s.processed ? s.handled_pct + '%' : '–', 'handled without a person', '',
      s.processed ? h('div', { class: 'sm faint', title: `Assumes ${s.manual_minutes_assumption} minutes per manual review` }, `about ${s.est_hours_saved} h saved`) : null),
    stat(s.runs ? s.avg_run_seconds + ' s' : '–', 'average time to decide', '', s.runs ? h('div', { class: 'sm faint' }, `${s.runs} run${s.runs === 1 ? '' : 's'}`) : null));

  const queue = h('section', { class: 'queue' }, h('h2', {}, 'Waiting on your decision'),
    waiting.length ? waiting.map(v => h('div', { class: 'qrow' },
      h('div', {}, h('a', { href: '#/vendor/' + v.vendor_id, style: 'font-weight:650' }, v.legal_name), h('div', { class: 'sm faint' }, v.vendor_id + ' · ' + (COUNTRY[v.country] || v.country))),
      h('div', { class: 'why' }, v.reason, h('div', { class: 'sm faint' }, 'Risk ' + v.risk_score + ' of 100')),
      h('a', { class: 'btn primary', href: '#/vendor/' + v.vendor_id }, 'Review')))
      : h('div', { class: 'empty' }, 'Nothing is waiting for you. Suspicious submissions will appear here with the reason and the safe next step.'));

  const tbody = h('tbody');
  const FILTERS = [['all', 'All'], ['awaiting_reviewer', 'Needs review'], ['awaiting_vendor', 'Waiting on vendor'], ['approved', 'Approved'], ['rejected', 'Rejected']];
  const tabs = h('div', { class: 'tabs', role: 'group', 'aria-label': 'Filter by status' });
  function draw() {
    tabs.replaceChildren(...FILTERS.map(([k, label]) => h('button', { 'aria-pressed': String(filter === k), onclick: () => { filter = k; draw(); } },
      label + ' ' + vendors.filter(v => k === 'all' || (k === 'rejected' ? v.status === 'rejected' : v.substatus === k)).length)));
    const rows = vendors.filter(v => (filter === 'all' || (filter === 'rejected' ? v.status === 'rejected' : v.substatus === filter))
      && (!q || (v.legal_name + v.vendor_id + (v.tax_id || '')).toLowerCase().includes(q)));
    tbody.replaceChildren(...(rows.length ? rows.map(v => h('tr', { class: 'click', tabindex: '0', onclick: () => location.hash = '#/vendor/' + v.vendor_id, onkeydown: e => { if (e.key === 'Enter') location.hash = '#/vendor/' + v.vendor_id; } },
      h('td', {}, h('div', { style: 'font-weight:600' }, v.legal_name), h('div', { class: 'id' }, v.vendor_id)),
      h('td', {}, COUNTRY[v.country] || v.country),
      h('td', {}, pill(v.substatus), v.overdue ? h('div', { class: 'sm', style: 'color:var(--wait);font-weight:600;margin-top:3px' }, 'Overdue') : null),
      h('td', {}, h('span', { class: 'risk ' + (v.risk_score >= 20 ? 'hi' : 'lo') }, h('span', { class: 'bar' }, h('b', { style: `width:${Math.min(100, v.risk_score)}%` })), v.risk_score)),
      h('td', { class: 'muted' }, v.reason),
      h('td', { class: 'faint sm' }, fmt(v.updated_at))))
      : [h('tr', {}, h('td', { colspan: 6, class: 'muted' }, 'No vendors match.'))]));
  }
  draw();
  const search = h('input', { class: 'search', type: 'search', placeholder: 'Search name, ID or tax ID', 'aria-label': 'Search vendors', oninput: e => { q = e.target.value.trim().toLowerCase(); draw(); } });

  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Vendors'), h('p', { class: 'sub' }, 'Every vendor, its status, and why.')),
      h('div', { class: 'actions' }, reset, advance, h('a', { class: 'btn primary', href: '#/new' }, 'New submission'))),
    strip, queue,
    h('h2', {}, 'All vendors'), h('div', { class: 'tools' }, tabs, search),
    h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Vendor', 'Country', 'Status', 'Risk', 'Reason', 'Updated'].map(x => h('th', {}, x)))), tbody)));
}

// ---------------------------------------------------------- new submission ---
const FIELD_GROUPS = [
  { title: 'Company', fields: [
    ['legal_name', 'Registered legal name', 'text'], ['trade_name', 'Trade name, if different', 'text'],
    ['entity_type', 'Legal form', 'select', [['pvt_ltd', 'Private limited company'], ['ltd', 'Limited company'], ['llp', 'LLP'], ['gmbh', 'GmbH'], ['llc', 'LLC'], ['partnership', 'Partnership'], ['sole_prop', 'Sole proprietor']]],
    ['country', 'Country', 'select', [['IN', 'India'], ['DE', 'Germany'], ['GB', 'United Kingdom'], ['US', 'United States']]],
    ['address', 'Registered address', 'text'], ['city', 'City', 'text'], ['postal_code', 'Postal or PIN code', 'text']] },
  { title: 'Tax registration', fields: [['tax_id', 'Tax ID', 'text', null, null, 'GSTIN or PAN (India), USt-IdNr (Germany), VAT number (UK), EIN (US)']] },
  { title: 'Contact', fields: [['contact_name', 'Contact person', 'text'], ['contact_email', 'Contact email', 'email'], ['contact_phone', 'Phone', 'text']] },
  { title: 'Bank details', fields: [
    ['bank_name', 'Bank name', 'text'], ['account_holder', 'Name on the account', 'text'],
    ['currency', 'Payment currency', 'select', [['INR', 'INR'], ['EUR', 'EUR'], ['GBP', 'GBP'], ['USD', 'USD']]],
    ['account_number', 'Account number', 'text', null, ['IN', 'GB', 'US']], ['ifsc', 'IFSC code', 'text', null, ['IN']],
    ['iban', 'IBAN', 'text', null, ['DE', 'GB']], ['sort_code', 'Sort code', 'text', null, ['GB']],
    ['routing_number', 'Routing number', 'text', null, ['US']], ['swift', 'SWIFT or BIC (optional)', 'text']] },
];
const DOC_SLOTS = [['tax_cert', 'Tax registration certificate', 'GST certificate, VAT certificate or W-9'], ['bank_proof', 'Bank letter or cancelled cheque', 'Issued in the last 90 days'], ['incorporation', 'Certificate of incorporation (optional)', '']];
const GROUP_LABEL = { happy: 'Clean vendors', edge1: 'Edge case 1: looks fine, is not', edge2: 'Edge case 2: looks wrong, is not', edge3: 'Edge case 3: wait and resume', edge4: 'Edge case 4: subtle contradictions', fixable: 'Vendor can fix it', reject: 'Compliance' };

function buildFields(prefix, values, onCountry) {
  const root = h('div'); const inputs = {};
  for (const g of FIELD_GROUPS) {
    const grid = h('div', { class: 'grid' });
    for (const [key, label, type, opts, only, hint] of g.fields) {
      const id = `${prefix}-${key}`;
      const input = type === 'select' ? h('select', { id, name: key }, opts.map(([v, l]) => h('option', { value: v }, l))) : h('input', { id, name: key, type, autocomplete: 'off' });
      inputs[key] = input; input.value = (values && values[key]) || (type === 'select' ? opts[0][0] : '');
      grid.append(h('div', { class: 'field', 'data-only': only ? only.join(',') : null }, h('label', { for: id }, label), input, hint ? h('span', { class: 'hint' }, hint) : null));
    }
    root.append(h('fieldset', {}, h('legend', {}, g.title), grid));
  }
  const apply = () => root.querySelectorAll('[data-only]').forEach(f => { f.hidden = !f.dataset.only.split(',').includes(inputs.country.value); });
  inputs.country.addEventListener('change', () => { apply(); onCountry && onCountry(); }); apply();
  return { root, inputs, apply, values: () => Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value])) };
}

async function newSubmission(view) {
  const samples = await api('/samples');
  const f = buildFields('f', { country: 'IN', entity_type: 'pvt_ltd', currency: 'INR' }, () => { const c = { IN: 'INR', DE: 'EUR', GB: 'GBP', US: 'USD' }[f.inputs.country.value]; f.inputs.currency.value = c; });
  const files = {}; const chips = {};
  const fileFields = DOC_SLOTS.map(([k, label, hint]) => {
    chips[k] = h('span', { class: 'filechip' });
    const el = h('input', { type: 'file', id: 'doc-' + k, accept: '.pdf,.png,.jpg,.jpeg,.webp', onchange: () => { chips[k].textContent = el.files[0] ? el.files[0].name : ''; } });
    files[k] = el;
    return h('div', { class: 'field' }, h('label', { for: 'doc-' + k }, label), el, hint ? h('span', { class: 'hint' }, hint) : null, chips[k]);
  });
  const sel = h('select', { id: 'sample' }, h('option', { value: '' }, 'Choose a sample vendor…'),
    Object.entries(GROUP_LABEL).map(([g, label]) => h('optgroup', { label }, samples.filter(s => s.group === g).map(s => h('option', { value: s.id }, s.title)))));
  async function loadSample() {
    const s = samples.find(x => x.id === sel.value); if (!s) return toast('Choose a sample first', true);
    for (const [k, el] of Object.entries(f.inputs)) el.value = s.form[k] || (el.tagName === 'SELECT' ? el.options[0].value : '');
    f.apply();
    for (const [k] of DOC_SLOTS) {
      files[k].value = ''; chips[k].textContent = '';
      try {
        const r = await fetch(`/api/sample-docs/${s.id}/${k}`); if (!r.ok) continue;
        const blob = await r.blob(); const ext = (r.headers.get('content-type') || '').includes('png') ? 'png' : 'pdf';
        const file = new File([blob], `${k}.${ext}`, { type: blob.type }); const dt = new DataTransfer(); dt.items.add(file); files[k].files = dt.files; chips[k].textContent = file.name;
      } catch (_) {}
    }
    toast('Sample loaded. Edit anything you like, then submit.');
  }
  const submit = h('button', { class: 'btn primary', type: 'submit' }, 'Submit and watch it run');
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault(); submit.disabled = true; submit.textContent = 'Submitting…';
    const fd = new FormData(); fd.append('form', JSON.stringify(f.values()));
    for (const [k, el] of Object.entries(files)) if (el.files[0]) fd.append(k, el.files[0]);
    try { const r = await api('/submissions', { method: 'POST', body: fd }); location.hash = '#/run/' + r.submission_id; }
    catch (err) { toast(err.message, true); submit.disabled = false; submit.textContent = 'Submit and watch it run'; }
  } },
    f.root, h('fieldset', {}, h('legend', {}, 'Documents'), h('div', { class: 'grid' }, fileFields), h('p', { class: 'hint faint sm', style: 'margin-top:8px' }, 'PDF or image, up to 6 MB each. Leave something out on purpose to see how the process asks for it.')),
    h('div', { class: 'actions' }, submit));
  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'New submission'), h('p', { class: 'sub' }, 'What a vendor sends us: a form and two documents.'))),
    h('div', { class: 'sample' }, h('div', { class: 'field' }, h('label', { for: 'sample' }, 'Try a prepared vendor'), sel),
      h('button', { class: 'btn', type: 'button', onclick: loadSample }, 'Fill the form and attach its documents'),
      h('button', { class: 'btn', type: 'button', onclick: async () => { if (!sel.value) return toast('Choose a sample first', true); try { const r = await api(`/samples/${sel.value}/submit`, { method: 'POST' }); location.hash = '#/run/' + r.submission_id; } catch (e) { toast(e.message, true); } } }, 'Run it now')),
    form);
}

// ----------------------------------------------------- shared result pieces ---
const STAGES = [
  ['intake', 'Intake', 'Records the submission and what changed since last time.'],
  ['read_documents', 'Read documents', 'The AI reads each PDF or image and must quote the text it read. Code checks the quote is really on the page.'],
  ['completeness', 'Completeness check', 'Are all the required fields and documents here for this country?'],
  ['validate', 'Format and checksum validation', 'Tax ID, IBAN, IFSC, routing number: format and check digits. Plain code, no AI.'],
  ['cross_check', 'Cross-reference fields', 'Form against documents against each other: names, IDs, country, bank, legal form.'],
  ['name_judgment', 'Resolve ambiguous names', 'Only unclear name differences go to the AI, with a recorded explanation.'],
  ['risk_screening', 'Risk screening', 'Sanctions list, lookalike vendors, shared bank accounts, bank-change requests.'],
  ['decision', 'Decision', 'The rulebook turns findings into a status. No AI involved.'],
  ['communicate', 'Communicate', 'Drafts what the vendor needs to do, or a private note for the reviewer.'],
];
const ORDER = { reject: 0, reviewer: 1, vendor: 2, none: 3 };

function findingsTable(findings, R) {
  const fs = [...findings].sort((a, b) => (ORDER[a.action] - ORDER[b.action]) || (b.points - a.points));
  if (!fs.length) return h('div', { class: 'empty' }, 'No findings: every check passed.');
  return h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['What we found', 'Evidence', 'Effect', 'Risk'].map(x => h('th', {}, x)))),
    h('tbody', {}, fs.map(f => { const [al, cls] = ACTION[f.action] || ['', '']; const why = (R.map[f.code] || {}).why;
      return h('tr', {}, h('td', { style: 'min-width:200px' }, h('div', { style: 'font-weight:600' }, f.title), h('div', { class: 'id' }, f.code), why ? h('div', { class: 'sm faint', style: 'margin-top:2px' }, why) : null),
        h('td', { style: 'min-width:240px' }, f.message), h('td', {}, h('span', { class: 'tag ' + cls }, al)), h('td', {}, f.points ? '+' + f.points : '0')); }))));
}
function emailCard(m) {
  return h('div', { class: 'mail' }, h('header', {}, h('div', {}, h('b', {}, m.subject), h('div', { class: 'sm faint' }, `To ${m.to_addr || m.to}  ·  ${m.audience === 'reviewer' ? 'Internal note, the vendor never sees this' : 'Goes to the vendor'}`)),
    h('div', { class: 'actions' }, h('button', { class: 'btn quiet', onclick: () => copy(m.subject + '\n\n' + m.body, 'Email copied') }, 'Copy'),
      m.id ? h('button', { class: 'btn quiet', title: 'Sends to your own demo inbox, never to the vendor', onclick: async (e) => { e.currentTarget.disabled = true; try { const r = await api(`/emails/${m.id}/send`, { method: 'POST' }); toast('Sent to ' + r.sent_to); } catch (err) { toast(err.message, true); } e.currentTarget.disabled = false; } }, 'Send to my test inbox') : null)),
    h('pre', {}, m.body));
}
function docsPanel(documents) {
  const L = { tax_cert: 'Tax registration certificate', bank_proof: 'Bank letter', incorporation: 'Incorporation certificate' };
  const ks = Object.keys(documents || {}); if (!ks.length) return h('div', { class: 'empty' }, 'No documents were read.');
  return h('div', { class: 'grid two' }, ks.map(k => { const d = documents[k]; const f = d.fields || {};
    return h('div', { class: 'doc' }, h('h3', {}, L[k] || k), h('div', { class: 'sm faint', style: 'margin:2px 0 8px' }, `Read as: ${d.detected_as || 'unknown'}  ·  ${d.grounding || ''}`),
      Object.keys(f).length ? h('dl', { class: 'kvgrid' }, Object.entries(f).map(([a, b]) => [h('dt', {}, a.replace(/_/g, ' ')), h('dd', {}, b)])) : h('span', { class: 'muted' }, 'No fields could be used.')); }));
}
async function renderResult(run, box, vendor) {
  const R = await rules(); const r = run.result; const [label, cls] = STATUS[r.substatus] || [r.status, 'mute'];
  const lim = r.risk_limit;
  const meter = h('div', { 'aria-label': `Risk ${r.risk_score} of 100, auto-approve below ${lim}` }, h('div', { class: 'meter' }, h('b', { style: `width:${Math.min(100, r.risk_score)}%;background:${r.risk_score >= lim ? 'var(--hold)' : 'var(--ok)'}` }), h('u', { style: `left:${lim}%`, title: 'Auto-approve limit' })),
    h('div', { class: 'sm muted' }, `Risk ${r.risk_score} of 100. The vendor can be auto-approved only below ${lim}.`));
  const link = vendor && vendor.portal_path && r.substatus === 'awaiting_vendor' ? (location.origin + vendor.portal_path) : null;
  box.replaceChildren(
    h('div', { class: 'verdict ' + cls }, h('div', { class: 'big' }, label), h('p', { class: 'why' }, r.explanation), meter,
      r.resolved_since_last_run && r.resolved_since_last_run.length ? h('div', { style: 'margin-top:12px' }, h('div', { class: 'sm muted', style: 'margin-bottom:4px' }, 'Cleared by this resubmission'), h('div', { class: 'chips' }, r.resolved_since_last_run.map(c => h('span', { class: 'tag ok' }, c.replace(':', ' · '))))) : null,
      h('div', { class: 'actions', style: 'margin-top:14px' }, h('a', { class: 'btn primary', href: '#/vendor/' + run.vendor_id }, 'Open vendor'), h('span', { class: 'sm faint', style: 'align-self:center' }, `${r.llm_mode === 'mock' ? 'Offline mock AI' : 'Live AI'}  ·  ${r.seconds} s`))),
    link ? h('section', { class: 'sec' }, h('h2', {}, 'What the vendor receives'), h('p', { class: 'muted' }, 'The email below links to this private page, where the vendor can upload what is missing. Opening it is how you play the vendor in a demo.'),
      h('div', { class: 'link' }, h('code', {}, link), h('button', { class: 'btn quiet', onclick: () => copy(link, 'Link copied') }, 'Copy'), h('a', { class: 'btn quiet', href: vendor.portal_path, target: '_blank', rel: 'noopener' }, 'Open as the vendor'))) : null,
    h('section', { class: 'sec' }, h('h2', {}, 'Why this decision'), h('p', { class: 'muted' }, 'Rule that fired: ' + (r.rule_trace || []).join(' ')), findingsTable(r.findings, R)),
    h('section', { class: 'sec' }, h('h2', {}, 'What the AI read from the documents'), docsPanel(r.documents)),
    h('section', { class: 'sec' }, h('h2', {}, 'Messages drafted'), (run.emails || []).length ? run.emails.map(emailCard) : h('div', { class: 'empty' }, 'No message is needed for this outcome.')));
}

// ---------------------------------------------------------------- run view ---
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function runView(view, sid) {
  const R = await rules();
  const rows = {}; const tape = h('ol', { class: 'tape', 'aria-label': 'Stages of the run' });
  STAGES.forEach(([key, title, desc], i) => {
    const node = h('div', { class: 'node' }, String(i + 1)); const head = h('div', { class: 'h' }); const ms = h('div', { class: 'ms' }); const extra = h('div');
    const li = h('li', { class: 'step', 'data-s': 'pending' }, node, h('div', { class: 'sbody' }, h('div', { class: 't' }, title), h('div', { class: 'd' }, desc), head, extra), ms);
    rows[key] = { li, node, head, ms, extra, n: i + 1 }; tape.append(li);
  });
  const sub = h('p', { class: 'sub' }, 'Starting…'); const final = h('div');
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Live run'), sub), h('div', { class: 'actions' }, h('a', { class: 'btn', href: '#/new' }, 'New submission'))), tape, final);

  function setStep(key, state, headline, ms, findings, details) {
    const r = rows[key]; if (!r) return;
    r.li.dataset.s = state; r.node.textContent = state === 'pass' ? '✓' : state === 'flag' ? '!' : state === 'error' ? '×' : String(r.n);
    r.head.textContent = headline || ''; r.ms.textContent = ms != null && state !== 'running' ? ms + ' ms' : '';
    r.extra.replaceChildren();
    if (findings && findings.length) r.extra.append(h('ul', { class: 'flist' }, findings.map(f => h('li', { class: f.action }, h('b', {}, f.title + ': '), f.message))));
    const d = details && Object.fromEntries(Object.entries(details).filter(([k]) => k !== 'new_findings' && k !== 'trace'));
    if (d && Object.keys(d).length) r.extra.append(h('details', {}, h('summary', {}, 'Technical detail'), h('pre', { class: 'kv' }, JSON.stringify(d, null, 2))));
  }
  async function finish() {
    const st = await api(`/submissions/${sid}/run`);
    if (!st.run) return;
    const v = await api('/vendors/' + st.run.vendor_id).catch(() => null);
    sub.textContent = `${v ? v.vendor.legal_name + '  ·  ' : ''}${st.run.vendor_id}  ·  run ${st.run.run_id}`;
    await renderResult(st.run, final, v && v.vendor);
  }
  const done = await api(`/submissions/${sid}/run`);
  if (done.run) {                      // already finished (page reload / opened from history): show instantly
    const byCode = {}; (done.run.result.findings || []).forEach(f => (byCode[f.code] = byCode[f.code] || []).push(f));
    done.run.steps.forEach(s => setStep(s.stage, s.status, s.headline, s.ms, ((s.details || {}).new_findings || []).flatMap(c => byCode[c] || []), s.details));
    return finish();
  }
  const es = new EventSource(`/api/submissions/${sid}/events`); cleanup = () => es.close();
  const queue = []; let busy = false;
  async function pump() {                       // pace the updates so a human can follow along
    if (busy) return; busy = true;
    while (queue.length) { const [kind, d] = queue.shift();
      if (kind === 'stage_start') { setStep(d.stage, 'running', 'Working…'); await sleep(260); }
      else if (kind === 'stage_end') { setStep(d.stage, d.status, d.headline, d.ms, d.findings, d.details); await sleep(380); }
      else if (kind === 'run_finished') { await finish(); }
      else if (kind === 'run_failed') { final.replaceChildren(errorBox(new Error(d.error))); } }
    busy = false;
  }
  for (const k of ['run_started', 'stage_start', 'stage_end', 'run_finished', 'run_failed'])
    es.addEventListener(k, e => { const d = JSON.parse(e.data); if (k === 'run_started') { sub.textContent = `${d.vendor_id}  ·  version ${d.version}  ·  ${d.llm_mode === 'mock' ? 'offline mock AI' : 'live AI'}`; return; } queue.push([k, d]); pump(); });
  es.addEventListener('end', () => es.close());
  es.onerror = () => { es.close(); if (!queue.length && !busy) finish().catch(() => {}); };
}

// -------------------------------------------------------------- vendor view ---
async function vendorView(view, id) {
  const [d, R, samples] = await Promise.all([api('/vendors/' + id), rules(), api('/samples')]);
  const v = d.vendor; const done = d.runs.filter(r => r.status !== 'running'); const last = done[done.length - 1];
  let tab = 'decision'; const body = h('div');
  const portalUrl = location.origin + v.portal_path;
  const sample = samples.find(s => s.has_follow_up && (s.form.tax_id || '').replace(/[\s-]/g, '').toUpperCase() === (v.tax_id || '').replace(/[\s-]/g, '').toUpperCase());

  const TABS = [['decision', 'Decision'], ['history', 'History'], ['emails', `Messages (${d.emails.length})`], ['audit', `Audit trail (${d.audit.length})`]];
  const tabs = h('div', { class: 'tabs', role: 'group' });
  async function draw() {
    tabs.replaceChildren(...TABS.map(([k, l]) => h('button', { 'aria-pressed': String(tab === k), onclick: () => { tab = k; draw(); } }, l)));
    if (tab === 'decision') {
      if (!last) return body.replaceChildren(h('div', { class: 'empty' }, 'This is an existing approved vendor. It has not been through the pipeline here, so there is no run to show.'));
      const r = last.result; body.replaceChildren(h('p', { class: 'why', style: 'font-size:16px;max-width:70ch' }, r.explanation),
        h('section', { class: 'sec' }, h('h2', {}, 'Findings'), findingsTable(r.findings, R)),
        h('section', { class: 'sec' }, h('h2', {}, 'What the AI read from the documents'), docsPanel(r.documents)));
    } else if (tab === 'history') {
      body.replaceChildren(h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Version', 'Received', 'Source', 'Run', 'Result', 'Risk'].map(x => h('th', {}, x)))),
        h('tbody', {}, d.submissions.map(s => { const run = d.runs.find(r => r.submission_id === s.submission_id);
          return h('tr', { class: run ? 'click' : '', onclick: () => run && (location.hash = '#/run/' + s.submission_id) }, h('td', {}, 'v' + s.version), h('td', {}, fmt(s.created_at)), h('td', {}, s.source), h('td', { class: 'id' }, run ? run.run_id : 'not run'),
            h('td', {}, run ? pill(run.substatus) : '–'), h('td', {}, run ? run.risk_score : '–')); })))),
        d.overrides.length ? h('section', { class: 'sec' }, h('h2', {}, 'Decisions made by people and by the waiting loop'), h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['When', 'Who', 'Change', 'Reason'].map(x => h('th', {}, x)))),
          h('tbody', {}, d.overrides.map(o => h('tr', {}, h('td', {}, fmt(o.at)), h('td', {}, o.actor), h('td', {}, `${o.old_status} → ${o.new_status}`), h('td', {}, o.reason))))))) : null);
    } else if (tab === 'emails') {
      body.replaceChildren(d.emails.length ? d.emails.map(emailCard) : h('div', { class: 'empty' }, 'No messages yet.'));
    } else {
      const verify = h('button', { class: 'btn', onclick: async () => { const r = await api('/audit/verify'); toast(r.ok ? `Audit chain intact: ${r.checked} events verified` : `Chain BROKEN at event ${r.broken_at_seq}`, !r.ok); } }, 'Verify the whole chain');
      body.replaceChildren(h('p', { class: 'muted' }, 'Every event is chained to the one before it with a hash, so rewriting or deleting history is detectable. This replaces “whatever is in someone’s inbox”.'), h('div', { style: 'margin-bottom:10px' }, verify),
        h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['#', 'When', 'Event', 'Detail', 'Hash'].map(x => h('th', {}, x)))),
          h('tbody', {}, d.audit.map(a => h('tr', {}, h('td', { class: 'id' }, a.seq), h('td', {}, fmt(a.at)), h('td', {}, a.event.replace(/_/g, ' ')), h('td', { class: 'sm muted' }, JSON.stringify(a.payload).slice(0, 150)), h('td', { class: 'id' }, a.hash.slice(0, 10))))))));
    }
  }
  // reviewer override
  const status = h('select', { 'aria-label': 'New status' }, h('option', { value: 'approved' }, 'Approve'), h('option', { value: 'rejected' }, 'Reject'), h('option', { value: 'pending' }, 'Keep on hold'));
  const reason = h('textarea', { placeholder: 'Why? For example: “Called the number on file for V-0001; they confirmed the new account.”', 'aria-label': 'Reason' });
  const decide = h('button', { class: 'btn primary', onclick: async (e) => { e.currentTarget.disabled = true;
    try { await post(`/vendors/${id}/override`, { status: status.value, reason: reason.value, actor: 'reviewer' }); toast('Decision recorded in the audit trail'); route(); }
    catch (err) { toast(err.message, true); e.currentTarget.disabled = false; } } }, 'Record decision');

  view.replaceChildren(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, v.legal_name), h('p', { class: 'sub' }, `${v.vendor_id}  ·  ${COUNTRY[v.country] || v.country}${v.tax_id ? '  ·  ' + v.tax_id : ''}`)),
      h('div', { class: 'actions' }, pill(v.substatus), h('span', { class: 'tag' }, 'Risk ' + v.risk_score))),
    v.substatus === 'awaiting_vendor' ? h('div', { class: 'note' }, h('b', {}, 'Waiting on the vendor. '), v.due_at ? `Reply due ${fmt(v.due_at)}${v.reminders_sent ? `, ${v.reminders_sent} reminder${v.reminders_sent > 1 ? 's' : ''} sent` : ''}.` : '',
      h('div', { class: 'link', style: 'margin-top:8px;background:transparent' }, h('code', {}, portalUrl), h('button', { class: 'btn quiet', onclick: () => copy(portalUrl, 'Link copied') }, 'Copy'), h('a', { class: 'btn quiet', href: v.portal_path, target: '_blank', rel: 'noopener' }, 'Open as the vendor'),
        sample ? h('button', { class: 'btn quiet', title: 'Demo shortcut: submits the prepared reply to this request', onclick: async () => { try { const r = await api(`/samples/${sample.id}/submit?follow_up=true&vendor_id=${v.vendor_id}`, { method: 'POST' }); location.hash = '#/run/' + r.submission_id; } catch (e) { toast(e.message, true); } } }, 'Demo: vendor sends the missing document') : null)) : null,
    tabs, h('div', { style: 'margin-top:16px' }, body),
    v.status !== 'new' ? h('section', { class: 'sec' }, h('h2', {}, 'Make a decision yourself'), h('p', { class: 'muted' }, 'Overrides need a reason and are written to the audit trail.'),
      h('div', { class: 'panel' }, h('div', { class: 'grid two' }, h('div', { class: 'field' }, h('label', {}, 'Decision'), status), h('div', { class: 'field' }, h('label', {}, 'Reason (required)'), reason)), h('div', { style: 'margin-top:12px' }, decide))) : null);
  draw();
}

// -------------------------------------------------- the vendor-facing page ---
async function portalView(view, token) {
  let state;
  try { state = await api('/portal/' + token); }
  catch (e) { return view.replaceChildren(h('h1', {}, 'This link is not valid'), h('p', { class: 'muted' }, 'It may have been mistyped. Please use the link in the email you received, or contact the procurement team.')); }
  const wrap = h('div'); view.replaceChildren(wrap);
  const header = () => [h('p', { class: 'sm faint', style: 'margin-bottom:6px' }, 'Procurement team  ·  Vendor registration'), h('h1', {}, state.company), h('p', { class: 'sub muted' }, 'Reference ' + state.reference)];
  function show() {
    if (state.state === 'checking') { wrap.replaceChildren(...header(), h('div', { class: 'note' }, state.message)); return; }
    if (state.state !== 'open') { wrap.replaceChildren(...header(), h('div', { class: 'panel', style: 'margin-top:18px' }, h('h2', {}, { approved: 'You are approved', review: 'We are reviewing your details', closed: 'This request is closed' }[state.state]), h('p', { style: 'margin-top:8px' }, state.message))); return; }
    const f = buildFields('p', state.form);
    const files = {}; const slots = DOC_SLOTS.map(([k, label, hint]) => { const el = h('input', { type: 'file', id: 'p-doc-' + k, accept: '.pdf,.png,.jpg,.jpeg,.webp' }); files[k] = el;
      return h('div', { class: 'field' }, h('label', { for: 'p-doc-' + k }, label), el, hint ? h('span', { class: 'hint' }, hint) : null, state.documents_on_file.includes(k) ? h('span', { class: 'hint' }, 'We already have one on file. Upload only to replace it.') : null); });
    const btn = h('button', { class: 'btn primary', type: 'submit' }, 'Send to procurement');
    const form = h('form', { onsubmit: async (e) => { e.preventDefault(); btn.disabled = true;
      const fd = new FormData(); fd.append('form', JSON.stringify(f.values())); for (const [k, el] of Object.entries(files)) if (el.files[0]) fd.append(k, el.files[0]);
      try { await api(`/portal/${token}/respond`, { method: 'POST', body: fd }); } catch (err) { toast(err.message, true); btn.disabled = false; return; }
      state = { ...state, state: 'checking', message: 'Thanks. We are checking what you sent.' }; show();
      for (let i = 0; i < 90; i++) { await sleep(700); const s = await api('/portal/' + token); if (s.state !== 'checking') { state = s; show(); return; } } } },
      h('fieldset', {}, h('legend', {}, 'Upload what we asked for'), h('div', { class: 'grid' }, slots)),
      h('details', { style: 'margin-bottom:20px' }, h('summary', { style: 'cursor:pointer;color:var(--brand);font-weight:600' }, 'Check or correct your details'), h('div', { style: 'margin-top:12px' }, f.root)), btn);
    wrap.replaceChildren(...header(),
      h('div', { class: 'panel', style: 'margin:18px 0 22px' }, h('p', { style: 'font-weight:650;margin-bottom:6px' }, state.message), state.asks.length ? h('ol', { style: 'margin:0;padding-left:20px' }, state.asks.map(a => h('li', { style: 'margin:6px 0' }, a))) : null,
        state.days_left != null ? h('p', { class: 'sm muted', style: 'margin:10px 0 0' }, state.days_left === 0 ? 'Please reply today.' : `Please reply within ${state.days_left} day${state.days_left === 1 ? '' : 's'}.`) : null), form);
  }
  show();
}

// ----------------------------------------------------------- how it works ---
async function rulesView(view) {
  const { raw } = await rules(); const groups = {};
  raw.rules.forEach(r => (groups[r.category] = groups[r.category] || []).push(r));
  const NAMES = { completeness: 'Completeness', format: 'Format and check digits', consistency: 'Consistency across sources', compliance: 'Compliance', fraud: 'Fraud signals', lifecycle: 'Lifecycle', system: 'System' };
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Rulebook'), h('p', { class: 'sub' }, 'Every decision traces back to a line here. A procurement lead can read it, challenge it and change it. The AI cannot.'))),
    h('div', { class: 'panel', style: 'margin-bottom:26px' }, h('h2', {}, 'Thresholds'), h('dl', { class: 'kvgrid', style: 'margin-top:10px' }, Object.entries(raw.thresholds).map(([k, v]) => [h('dt', {}, k), h('dd', { class: 'mono' }, String(v))])),
      h('p', { class: 'muted sm', style: 'margin-top:10px' }, `Risk points add up across findings. A vendor can be approved automatically only when the total is below ${raw.auto_approve_below_risk}.`)),
    Object.entries(NAMES).filter(([k]) => groups[k]).map(([k, name]) => h('section', { class: 'sec' }, h('h2', {}, name),
      h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Rule', 'Why it exists', 'Effect', 'Risk'].map(x => h('th', {}, x)))),
        h('tbody', {}, groups[k].map(r => h('tr', {}, h('td', {}, h('div', { style: 'font-weight:600' }, r.title), h('div', { class: 'id' }, r.code)), h('td', { class: 'muted' }, r.why), h('td', {}, h('span', { class: 'tag ' + (ACTION[r.action] || ['', ''])[1] }, (ACTION[r.action] || [r.action])[0])), h('td', {}, '+' + r.points)))))))));
}
function processView(view) {
  const flow = STAGES.map(([, t, d]) => h('div', {}, h('b', {}, t), h('p', {}, d)));
  const rung = (cls, t, d) => h('div', { class: 'rung ' + cls }, h('b', {}, t), h('span', { class: 'muted' }, d));
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'How a submission is decided'), h('p', { class: 'sub' }, 'The AI reads and explains. The rulebook decides.'))),
    h('section', {}, h('h2', { style: 'margin-bottom:10px' }, 'The nine stages'), h('div', { class: 'flow' }, flow)),
    h('section', { class: 'sec' }, h('h2', {}, 'Which outcome wins when several apply'), h('div', { class: 'ladder' },
      rung('bad', 'Rejected', 'A sanctions match, or the vendor never answered after the reminders. Nothing about a sanctions match is ever told to the vendor.'),
      rung('hold', 'Needs review', 'Looks like fraud but is not proof: a lookalike of an existing vendor, a bank account that already belongs to someone else, a bank-change request, contradictions between ID, country and bank. A person decides, and the vendor is not told.'),
      rung('wait', 'Waiting on vendor', 'Anything the vendor can simply fix: a missing, blurry, wrong or stale document, a typo in an ID, a name that needs explaining. They receive a precise numbered list and a private link to reply.'),
      rung('ok', 'Approved', 'Nothing blocking, and the risk score is below the limit. Harmless variants such as “Pvt Ltd” and “Private Limited” are noticed and recorded, not punished.'))),
    h('section', { class: 'sec' }, h('h2', {}, 'Who sees what'), h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['', 'The vendor', 'The reviewer'].map(x => h('th', {}, x)))), h('tbody', {},
      [['Missing or wrong documents', 'Told exactly what to send', 'Sees it too'], ['Fraud-shaped signals', 'Sees only “being reviewed”', 'Sees evidence and the safe next step (call the number already on file)'], ['Sanctions match', 'Gets nothing', 'Sees the match and escalates to compliance'], ['Risk score', 'Never shown', 'Shown with every rule behind it']].map(r => h('tr', {}, r.map((c, i) => h('td', { style: i === 0 ? 'font-weight:600' : '' }, c)))))))),
    h('section', { class: 'sec' }, h('h2', {}, 'Built-in safety'), h('ul', { style: 'max-width:72ch' },
      ['The AI must quote the text it read; code checks the quote is really in the document, so invented values are caught.', 'If any step fails or the AI is unavailable, the vendor goes to a person. It never guesses and never approves.', 'Every event is chained with a hash in the audit trail, so history cannot be quietly rewritten.', 'Emails drafted by the AI are scanned by code for fraud or screening language before a vendor could see them.'].map(t => h('li', { style: 'margin:6px 0' }, t)))),
    h('p', {}, h('a', { href: '#/design' }, 'Read the full design notes')));
}
async function evalView(view) {
  const s0 = await api('/eval/status'); const stats = await api('/stats');
  const out = h('div'); const btn = h('button', { class: 'btn primary' }, 'Run all cases');
  function paint(s) {
    btn.disabled = s.state === 'running';
    const failed = s.results.filter(r => !r.passed);
    out.replaceChildren(
      s.total ? h('div', { class: 'strip' }, h('div', { class: 'stat' + (failed.length ? '' : ' ') }, h('b', { style: `color:${failed.length ? 'var(--bad)' : 'var(--ok)'}` }, `${s.passed} of ${s.done}`), h('span', {}, 'cases behaved as expected')),
        h('div', { class: 'stat' }, h('b', {}, s.done + ' / ' + s.total), h('span', {}, s.state === 'running' ? 'running…' : 'run')), h('div', { class: 'stat' }, h('b', {}, s.llm_mode === 'mock' ? 'Offline mock' : 'Live AI'), h('span', {}, 'AI mode')), h('div', { class: 'stat' }, h('b', {}, s.seconds + ' s'), h('span', {}, 'elapsed'))) : null,
      s.results.length ? h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['', 'Case', 'Expected', 'Got', 'Findings'].map(x => h('th', {}, x)))), h('tbody', {}, s.results.map(r => { const last = r.steps[r.steps.length - 1]; const ex = last.expect;
        return h('tr', {}, h('td', {}, h('span', { class: 'tag ' + (r.passed ? 'ok' : 'bad') }, r.passed ? 'Pass' : 'Fail')), h('td', { style: 'min-width:260px' }, h('div', { style: 'font-weight:600' }, r.title), h('div', { class: 'id' }, r.id), r.steps.some(x => x.fails.length) ? h('div', { class: 'sm', style: 'color:var(--bad)' }, r.steps.flatMap(x => x.fails).join('; ')) : null),
          h('td', {}, ex.status + (ex.substatus ? ' / ' + ex.substatus.replace('_', ' ') : '')), h('td', {}, last.status + ' / ' + last.substatus.replace('_', ' ')), h('td', { class: 'sm muted', style: 'max-width:300px' }, last.codes.join(', ') || 'none')); })))) : h('div', { class: 'empty' }, 'Not run yet. Each case is a vendor with a known right answer, run through the real pipeline in a fresh database.'));
  }
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Evaluation'), h('p', { class: 'sub' }, 'Labelled vendors with a known right answer, run through the real pipeline. If a change breaks a decision, it shows here.')), h('div', { class: 'actions' }, btn)),
    stats.llm_mode === 'live' ? h('div', { class: 'note' }, 'Live mode: running every case calls the real model and uses API credit.') : null, out);
  paint(s0);
  let timer; const poll = async () => { const s = await api('/eval/status'); paint(s); if (s.state === 'running') timer = setTimeout(poll, 800); };
  btn.onclick = async () => { try { await api('/eval/run?confirm_live=' + (stats.llm_mode === 'live'), { method: 'POST' }); poll(); } catch (e) { toast(e.message, true); } };
  cleanup = () => clearTimeout(timer); if (s0.state === 'running') poll();
}
async function outboxView(view) {
  const mails = await api('/emails');
  view.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Outbox'), h('p', { class: 'sub' }, 'Every message the process wrote. The demo never emails a real vendor: sending goes only to your own test inbox.'))),
    mails.length ? mails.map(m => h('div', {}, h('p', { class: 'sm muted', style: 'margin:14px 0 4px' }, h('a', { href: '#/vendor/' + m.vendor_id }, m.legal_name || m.vendor_id), `  ·  ${fmt(m.created_at)}  ·  ${m.status.replace(/_/g, ' ')}`), emailCard(m))) : h('div', { class: 'empty' }, 'No messages yet. Submit a vendor and the process will draft one.'));
}
function inline(t) { return t.split(/(\*\*[^*]+\*\*|`[^`]+`)/).map(p => p.startsWith('**') ? h('b', {}, p.slice(2, -2)) : p.startsWith('`') ? h('code', {}, p.slice(1, -1)) : p); }
function md(text) {
  const out = h('div', { class: 'md' }); const L = text.split('\n'); let i = 0;
  while (i < L.length) {
    const l = L[i];
    if (/^```/.test(l)) { const buf = []; i++; while (i < L.length && !/^```/.test(L[i])) buf.push(L[i++]); i++; out.append(h('pre', {}, buf.join('\n'))); }
    else if (/^#{1,3} /.test(l)) { const n = l.match(/^#+/)[0].length; out.append(h('h' + n, {}, inline(l.replace(/^#+ /, '')))); i++; }
    else if (/^\|/.test(l)) { const rows = []; while (i < L.length && /^\|/.test(L[i])) rows.push(L[i++]); const cells = r => r.replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      const body = rows.filter(r => !/^\|[\s:-]+\|/.test(r) || /[A-Za-z]/.test(r)).map(cells); out.append(h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, body[0].map(c => h('th', {}, inline(c))))), h('tbody', {}, body.slice(1).map(r => h('tr', {}, r.map(c => h('td', {}, inline(c))))))))); }
    else if (/^\s*([-*]|\d+\.) /.test(l)) { const ord = /^\s*\d+\./.test(l); const items = []; while (i < L.length && /^\s*([-*]|\d+\.) /.test(L[i])) items.push(L[i++].replace(/^\s*([-*]|\d+\.) /, '')); out.append(h(ord ? 'ol' : 'ul', {}, items.map(x => h('li', {}, inline(x))))); }
    else if (/^> /.test(l)) { const buf = []; while (i < L.length && /^> ?/.test(L[i])) buf.push(L[i++].replace(/^> ?/, '')); out.append(h('blockquote', {}, inline(buf.join(' ')))); }
    else if (!l.trim()) i++;
    else { const buf = []; while (i < L.length && L[i].trim() && !/^(#{1,3} |\||```|> |\s*([-*]|\d+\.) )/.test(L[i])) buf.push(L[i++]); out.append(h('p', {}, inline(buf.join(' ')))); }
  }
  return out;
}
async function docView(view, name) {
  const r = await fetch('/api/docs/' + name); if (!r.ok) throw new Error('Document not found');
  view.replaceChildren(md(await r.text()));
}
