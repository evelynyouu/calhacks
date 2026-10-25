// Charts: any <canvas data-chart="pie|fan|history|price" data-payload='json'>, including HTMX/OOB swaps.
// Inputs: [data-slider] keeps a range and a number box in sync; [data-paycheck] previews dollars.
// Greens, blacks and grays that stay distinguishable side by side in the fund doughnut.
const PALETTE = ['#0b7a3e', '#0b0f0d', '#8fd3a8', '#4a4f4c', '#0e9f4f', '#a3a8a5', '#075c2e', '#d5d8d6', '#3fbf74'];

// ---------- Markdown parser ----------
// Handles: **bold**, *italic*, `code`, - lists, 1. lists, paragraphs, line breaks.
function parseMarkdown(raw) {
  // Escape HTML
  let s = raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  // Inline: bold > italic > code  (order matters)
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(?<!\*)\*(?!\*)(?!\s)([\s\S]*?)(?<!\s)\*(?!\*)/g, '<em>$1</em>');
  s = s.replace(/`([^`\n]+)`/g, '<code class="bg-ink/[.07] px-1 rounded text-[.88em] font-mono">$1</code>');
  // Process lines to handle lists
  const lines = s.split('\n');
  const out = [];
  let inUl = false, inOl = false;
  for (const line of lines) {
    const ul = line.match(/^[ \t]*[-*]\s+(.+)/);
    const ol = line.match(/^[ \t]*\d+\.\s+(.+)/);
    if (ul) {
      if (!inUl) { if (inOl) { out.push('</ol>'); inOl = false; } out.push('<ul class="list-disc pl-4 space-y-0.5 my-1.5">'); inUl = true; }
      out.push(`<li>${ul[1]}</li>`);
    } else if (ol) {
      if (!inOl) { if (inUl) { out.push('</ul>'); inUl = false; } out.push('<ol class="list-decimal pl-4 space-y-0.5 my-1.5">'); inOl = true; }
      out.push(`<li>${ol[1]}</li>`);
    } else {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
      out.push(line);
    }
  }
  if (inUl) out.push('</ul>');
  if (inOl) out.push('</ol>');
  // Paragraph breaks (double newline) and soft line breaks
  let html = out.join('\n');
  html = html.replace(/\n{2,}/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');
  html = `<p>${html}</p>`.replace(/<p>(\s*<\/?(ul|ol|li|p)[^>]*>)/g, '$1').replace(/(<\/(?:ul|ol)>)\s*<\/p>/g, '$1');
  return html;
}

function renderMarkdown() {
  document.querySelectorAll('.ff-prose[data-md]').forEach(el => {
    if (!el.dataset.rendered) {
      el.innerHTML = parseMarkdown(el.dataset.md || '');
      el.dataset.rendered = '1';
    }
  });
}

// Fade-in animation for new chat messages
function animateNewMessages() {
  document.querySelectorAll('.ff-msg-in:not([data-anim])').forEach(el => {
    el.dataset.anim = '1';
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    requestAnimationFrame(() => {
      el.style.transition = 'opacity .22s ease, transform .22s ease';
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    });
  });
}

function money(v) { return '$' + Math.round(v).toLocaleString(); }

function makeChart(el) {
  if (Chart.getChart(el)) return;
  const d = JSON.parse(el.dataset.payload);
  if (el.dataset.chart === 'pie') {
    new Chart(el, {
      type: 'doughnut',
      data: { labels: d.labels, datasets: [{ data: d.values, backgroundColor: PALETTE, borderWidth: 2, borderColor: '#fff' }] },
      options: { cutout: '62%', plugins: { legend: { display: false },
                 tooltip: { callbacks: { label: c => `${c.label}: ${c.parsed}%` } } } },
    });
  } else if (el.dataset.chart === 'fan') {
    const labels = d.years.map(y => y === 0 ? 'Now' : `Yr ${y}`);
    new Chart(el, {
      type: 'line',
      data: { labels, datasets: [
        { label: 'Low (10th pct)', data: d.p10, borderColor: '#0b7a3e55', pointRadius: 0, borderWidth: 1 },
        { label: 'Typical', data: d.p50, borderColor: '#0b7a3e', pointRadius: 0, borderWidth: 2.5 },
        { label: 'High (90th pct)', data: d.p90, borderColor: '#0b7a3e55', backgroundColor: '#0b7a3e1f',
          pointRadius: 0, borderWidth: 1, fill: 0 },
      ] },
      options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.parsed.y)}` } } },
        scales: { y: { ticks: { callback: money } }, x: { grid: { display: false } } } },
    });
  } else if (el.dataset.chart === 'backtest') {
    makeBacktestChart(el, d);
  } else if (el.dataset.chart === 'price') {
    makePriceChart(el, d);
  } else if (el.dataset.chart === 'history') {
    const labels = d.labels.map(s => new Date(s + 'T00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
    new Chart(el, {
      type: 'line',
      data: { labels, datasets: [
        { label: 'Fund value', data: d.value, borderColor: '#0b7a3e', backgroundColor: '#0b7a3e1a', fill: true, pointRadius: 0, borderWidth: 2.5 },
        { label: 'You put in', data: d.contributed, borderColor: '#0b0f0d80', borderDash: [5, 4], pointRadius: 0, borderWidth: 1.5, stepped: true },
      ] },
      options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } },
                   tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.parsed.y)}` } } },
        scales: { y: { ticks: { callback: money } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } } } },
    });
  }
}

function makeBacktestChart(el, d) {
  const labels = d.labels.map(s => new Date(s + 'T00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
  new Chart(el, {
    type: 'line',
    data: { labels, datasets: [
      { label: 'Auto-trade',        data: d.auto,       borderColor: '#0b7a3e',   backgroundColor: '#0b7a3e18', fill: true,  pointRadius: 0, borderWidth: 2.5 },
      { label: 'Buy & hold',        data: d.hold,       borderColor: '#0b0f0d',   backgroundColor: 'transparent',            pointRadius: 0, borderWidth: 1.5 },
      { label: 'Benchmark',         data: d.benchmark,  borderColor: '#9ca3af',   backgroundColor: 'transparent',            pointRadius: 0, borderWidth: 1.5 },
      { label: 'SPY',               data: d.spy,        borderColor: '#d1d5db',   backgroundColor: 'transparent',            pointRadius: 0, borderWidth: 1,   borderDash: [4,3] },
      { label: 'You put in',        data: d.contributed,borderColor: '#0b0f0d55', backgroundColor: 'transparent',            pointRadius: 0, borderWidth: 1.5, borderDash: [5,4], stepped: true },
    ] },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.parsed.y)}` } },
      },
      scales: {
        y: { ticks: { callback: money } },
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
      },
    },
  });
}

function makePriceChart(el, d) {
  const color = d.up ? '#0b7a3e' : '#d93025';
  const long = d.labels.length > 120;
  const labels = d.labels.map(s => new Date(s + 'T00:00').toLocaleDateString(undefined, long ? { month: 'short', year: '2-digit' } : { month: 'short', day: 'numeric' }));
  const ctx = el.getContext('2d');
  const fill = ctx.createLinearGradient(0, 0, 0, el.clientHeight || 256);
  fill.addColorStop(0, color + '33');
  fill.addColorStop(1, color + '00');
  new Chart(el, {
    type: 'line',
    data: { labels, datasets: [{ data: d.values, borderColor: color, backgroundColor: fill, fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15 }] },
    options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => '$' + c.parsed.y.toFixed(2) } } },
      scales: { y: { position: 'right', ticks: { callback: v => '$' + v.toLocaleString() } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 6 } } } },
  });
}

function paintSwatches() {
  document.querySelectorAll('[data-swatch]').forEach(el => { el.style.background = PALETTE[+el.dataset.swatch % PALETTE.length]; });
}

function updatePreviews() {
  document.querySelectorAll('[data-paycheck]').forEach(p => {
    const box = p.closest('form').querySelector('[data-number]');
    const out = p.querySelector('[data-paycheck-out]');
    if (box && out) out.textContent = money(Math.floor(+p.dataset.paycheck * (+box.value || 0) / 100));
  });
}

function init() {
  document.querySelectorAll('canvas[data-chart]').forEach(makeChart);
  paintSwatches();
  updatePreviews();
  renderMarkdown();
  animateNewMessages();
}

// The box that scrolls the chat: partials/chat.html wraps #chat-messages in [data-chat-scroll] (column-reverse,
// so it stays pinned to the newest message by itself); the Portfolio tab's short feed scrolls #chat-messages itself.
function chatScroller() {
  const chat = document.getElementById('chat-messages');
  return chat && (chat.closest('[data-chat-scroll]') || chat);
}

// After a chat turn (or on page load): scroll to the newest message and focus the next question's input.
// A column-reverse box's bottom is scrollTop 0 and older messages are negative, so scrollHeight clamps to the end.
function followChat() {
  const box = chatScroller();
  if (box) box.scrollTop = box.scrollHeight;
  const first = document.querySelector('#composer [autofocus]');
  if (first) first.focus();
}

// Range <-> number box
document.addEventListener('input', e => {
  const wrap = e.target.closest('[data-slider]');
  if (wrap) {
    const range = wrap.querySelector('[data-range]'), box = wrap.querySelector('[data-number]');
    if (e.target === range) box.value = range.value;
    if (e.target === box && box.value !== '') range.value = Math.min(+box.max, Math.max(+box.min, +box.value));
    updatePreviews();
  }
  if (e.target.matches('[data-autogrow]')) {
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px';
  }
});

// Enter sends, Shift+Enter makes a new line
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && e.target.matches('textarea[data-autogrow]')) {
    e.preventDefault();
    if (e.target.value.trim()) e.target.form.requestSubmit();
  }
});

// Free chat: show the user's message right away, then Claude's reply arrives
document.body.addEventListener('htmx:beforeRequest', e => {
  const form = e.detail.elt;
  if (!form.matches || !form.matches('form[data-optimistic]')) return;
  const box = form.querySelector('textarea');
  const row = document.createElement('div');
  row.className = 'flex justify-end';
  const p = document.createElement('p');
  p.className = 'max-w-[85%] rounded-2xl rounded-br-md bg-ink/[.06] px-4 py-2.5 text-[.95rem] leading-relaxed whitespace-pre-line';
  p.textContent = box.value.trim();
  row.appendChild(p);
  document.getElementById('chat-messages').appendChild(row);
  const scroller = chatScroller();
  scroller.scrollTop = scroller.scrollHeight;
  box.value = '';
  box.style.height = 'auto';
});

document.addEventListener('DOMContentLoaded', () => { init(); followChat(); });
document.body.addEventListener('htmx:afterSettle', e => {
  setTimeout(init, 0);
  if (e.detail.target.id !== 'tape') setTimeout(followChat, 0);   // the tape refreshes every minute; don't steal focus
});

// ---------- Left sidebar (partials/nav.html, nav.css) ----------
// Desktop: three states, remembered in localStorage (if allowed) and applied before paint by base.html:
// 'open', 'collapsed' (icons with tooltips) and 'hidden' (the header's Show button brings it back).
// Mobile: the header's Menu button opens it as a drawer. Escape, the backdrop or a link closes it, and focus
// goes back to the Menu button. Listeners are delegated, so they keep working after htmx swaps #nav.
const NAV_KEY = 'ff-nav';
const mobileNav = () => window.matchMedia('(max-width: 767.98px)').matches;

function setNavState(state) {
  const html = document.documentElement;
  html.classList.toggle('nav-collapsed', state === 'collapsed');
  html.classList.toggle('nav-hidden', state === 'hidden');
  try { localStorage.setItem(NAV_KEY, state); } catch (err) { /* private mode: not remembered */ }
  syncNav();
}

function syncNav() {
  const collapsed = document.documentElement.classList.contains('nav-collapsed');
  const hidden = document.documentElement.classList.contains('nav-hidden');
  document.querySelectorAll('[data-nav-show], [data-nav-hide]').forEach(b => b.setAttribute('aria-expanded', String(!hidden)));
  document.querySelectorAll('[data-nav-collapse]').forEach(b => {
    b.setAttribute('aria-expanded', String(!collapsed));
    b.title = collapsed ? 'Expand the sidebar' : 'Collapse the sidebar to icons';
    const t = b.querySelector('[data-nav-collapse-text]');
    if (t) t.textContent = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
  });
  const open = document.documentElement.classList.contains('nav-open');
  document.querySelectorAll('[data-nav-toggle]').forEach(b => b.setAttribute('aria-expanded', String(open)));
}

function openNav() {
  document.documentElement.classList.add('nav-open');
  syncNav();
  const first = document.querySelector('#nav [aria-current="page"]') || document.querySelector('#nav a');
  if (first) setTimeout(() => first.focus(), 0);
}

function closeNav(restoreFocus) {
  if (!document.documentElement.classList.contains('nav-open')) return;
  document.documentElement.classList.remove('nav-open');
  syncNav();
  const btn = document.querySelector('[data-nav-toggle]');
  if (restoreFocus && btn) btn.focus();
}

document.addEventListener('click', e => {
  if (e.target.closest('[data-nav-toggle]')) {
    document.documentElement.classList.contains('nav-open') ? closeNav(true) : openNav();
  } else if (e.target.closest('[data-nav-close]')) {
    closeNav(true);
  } else if (e.target.closest('[data-nav-collapse]')) {
    setNavState(document.documentElement.classList.contains('nav-collapsed') ? 'open' : 'collapsed');
  } else if (e.target.closest('[data-nav-hide]')) {
    setNavState('hidden');
    const show = document.querySelector('[data-nav-show]');   // focus would otherwise be lost with the sidebar
    if (show) show.focus();
  } else if (e.target.closest('[data-nav-show]')) {
    setNavState('open');
    const hide = document.querySelector('[data-nav-hide]');
    if (hide) hide.focus();
  } else if (e.target.closest('#nav a')) {
    closeNav(false);
  }
});

document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || !document.documentElement.classList.contains('nav-open')) return;
  e.preventDefault();
  closeNav(true);
});

// Keep Tab inside the open drawer: from the last link forward goes back to the Menu button, and vice versa.
document.addEventListener('keydown', e => {
  if (e.key !== 'Tab' || !mobileNav() || !document.documentElement.classList.contains('nav-open')) return;
  const btn = document.querySelector('[data-nav-toggle]');
  const links = [...document.querySelectorAll('#nav a')];
  if (!btn || !links.length) return;
  const first = links[0], last = links[links.length - 1];
  if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); btn.focus(); }
  else if (e.shiftKey && document.activeElement === btn) { e.preventDefault(); last.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); btn.focus(); }
  else if (!e.shiftKey && document.activeElement === btn) { e.preventDefault(); first.focus(); }
});

window.addEventListener('resize', () => { if (!mobileNav()) closeNav(false); });
document.addEventListener('DOMContentLoaded', syncNav);
document.body.addEventListener('htmx:afterSettle', syncNav);
