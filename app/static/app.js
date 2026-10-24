// Charts: any <canvas data-chart="pie|fan|history|price" data-payload='json'>, including HTMX/OOB swaps.
// Inputs: [data-slider] keeps a range and a number box in sync; [data-paycheck] previews dollars.
const PALETTE = ['#25633f', '#5b7fa6', '#c99a1a', '#c2603a', '#8a6bb0', '#7a8f3a', '#3f8f8a', '#b0577a', '#6b6b6b'];

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
        { label: 'Low (10th pct)', data: d.p10, borderColor: '#2f7a4f55', pointRadius: 0, borderWidth: 1 },
        { label: 'Typical', data: d.p50, borderColor: '#25633f', pointRadius: 0, borderWidth: 2.5 },
        { label: 'High (90th pct)', data: d.p90, borderColor: '#2f7a4f55', backgroundColor: '#2f7a4f1f',
          pointRadius: 0, borderWidth: 1, fill: 0 },
      ] },
      options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.parsed.y)}` } } },
        scales: { y: { ticks: { callback: money } }, x: { grid: { display: false } } } },
    });
  } else if (el.dataset.chart === 'price') {
    makePriceChart(el, d);
  } else if (el.dataset.chart === 'history') {
    const labels = d.labels.map(s => new Date(s + 'T00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
    new Chart(el, {
      type: 'line',
      data: { labels, datasets: [
        { label: 'Fund value', data: d.value, borderColor: '#25633f', backgroundColor: '#2f7a4f1a', fill: true, pointRadius: 0, borderWidth: 2.5 },
        { label: 'You put in', data: d.contributed, borderColor: '#1b242066', borderDash: [5, 4], pointRadius: 0, borderWidth: 1.5, stepped: true },
      ] },
      options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } },
                   tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.parsed.y)}` } } },
        scales: { y: { ticks: { callback: money } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } } } },
    });
  }
}

function makePriceChart(el, d) {
  const color = d.up ? '#2f7a4f' : '#c2603a';
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
}

// After a chat turn (or on page load): scroll to the newest message and focus the next question's input
function followChat() {
  const chat = document.getElementById('chat-messages');
  if (chat) chat.scrollTop = chat.scrollHeight;
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
  const chat = document.getElementById('chat-messages');
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
  box.value = '';
  box.style.height = 'auto';
});

document.addEventListener('DOMContentLoaded', () => { init(); followChat(); });
document.body.addEventListener('htmx:afterSettle', e => {
  setTimeout(init, 0);
  if (e.detail.target.id !== 'tape') setTimeout(followChat, 0);   // the tape refreshes every minute; don't steal focus
});
