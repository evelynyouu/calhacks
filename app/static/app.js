// Chart.js init for any <canvas data-chart="pie|fan" data-payload='json'>, including HTMX/OOB swaps.
const PALETTE = ['#25633f', '#c2603a', '#c99a1a', '#5b7fa6', '#8a6bb0', '#7a8f3a'];

function money(v) { return '$' + Math.round(v).toLocaleString(); }

function makeChart(el) {
  if (Chart.getChart(el)) return;
  const d = JSON.parse(el.dataset.payload);
  if (el.dataset.chart === 'pie') {
    new Chart(el, {
      type: 'doughnut',
      data: { labels: d.labels, datasets: [{ data: d.values, backgroundColor: PALETTE, borderWidth: 2, borderColor: '#fff' }] },
      options: { cutout: '62%', plugins: { legend: { position: 'bottom', labels: { boxWidth: 10 } },
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
  }
}

function initCharts() { document.querySelectorAll('canvas[data-chart]').forEach(makeChart); }

document.addEventListener('DOMContentLoaded', initCharts);
document.body.addEventListener('htmx:afterSettle', initCharts);
document.body.addEventListener('htmx:oobAfterSwap', () => setTimeout(initCharts, 0));

// Keep chat scrolled to the newest message
document.body.addEventListener('htmx:afterSwap', e => {
  if (e.detail.target.id === 'chat-messages') e.detail.target.scrollTop = e.detail.target.scrollHeight;
});
