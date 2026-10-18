/* Financial outlook chart for the dashboard: cash flow and profit & loss views.
   Shared static file so it is cached by the browser instead of riding in every page. */
// Chart.js draws on a canvas, so CSS cannot recolour it. Read the active theme's
// tokens and push them into Chart.js defaults; callers re-render on theme change.
function inproficChartTheme() {
  const css = getComputedStyle(document.documentElement);
  const read = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
  const theme = {
    text: read('--ui-surface-text', '#182433'),
    muted: read('--ui-surface-muted-text', '#667382'),
    border: read('--ui-border', '#cbd5e1'),
    dark: document.documentElement.dataset.theme === 'dark'
  };
  if (typeof Chart !== 'undefined') {
    Chart.defaults.color = theme.muted;
    Chart.defaults.borderColor = theme.border;
  }
  return theme;
}

document.addEventListener('DOMContentLoaded', function () {
  const cashEl = document.getElementById('financial-data');
  const profitEl = document.getElementById('profit-data');
  const fiscalEl = document.getElementById('fiscal-data');
  const canvas = document.getElementById('financial-outlook-chart');
  const periodButtons = [...document.querySelectorAll('.financial-period-btn')];
  const modeButtons = [...document.querySelectorAll('.financial-mode-btn')];
  const caption = document.getElementById('financial-chart-caption');
  const symbol = canvas.dataset.currency || '';

  if (!cashEl || !canvas) { console.warn('Financial chart: required elements not found.'); return; }
  if (typeof Chart === 'undefined') { console.error('Financial chart: Chart.js is not loaded.'); return; }

  let cash = {}, profit = {}, fiscal = {};
  try {
    cash = JSON.parse(cashEl.textContent || '{}');
    profit = JSON.parse((profitEl && profitEl.textContent) || '{}');
    fiscal = JSON.parse((fiscalEl && fiscalEl.textContent) || '{}');
  } catch (error) { console.error('Financial chart: invalid JSON data.', error); return; }

  const GREEN = '#059669', ROSE = '#e11d48';
  const state = { mode: 'cash', period: 'week' };
  let chart = null;

  const periodText = {
    week: ['Daily', 'last 7 days'], month: ['Daily', 'current month'], quarter: ['Weekly', 'current quarter'],
    year: ['Monthly', 'current year'], fiscal: [fiscal.kind === 'Quarter' ? 'Weekly' : 'Monthly', fiscal.label || 'business period']
  };
  const modes = {
    cash: {
      inLabel: 'Sales', outLabel: 'Spend', netLabel: 'Net cash flow', inKey: 'sales', outKey: 'spend', netKey: 'revenue',
      title: 'Where the cash went',
      parts: [['Procurement', 'procurement', '#f59e0b'], ['Expenses', 'expenses', '#e11d48'], ['Payroll', 'payroll', '#7c3aed']],
      note: 'Cash view: money received from paid sales minus procurement, expenses and wages actually paid.'
    },
    profit: {
      inLabel: 'Revenue', outLabel: 'Total costs', netLabel: 'Net profit', inKey: 'sales', outKey: 'costs', netKey: 'profit',
      title: 'Where the revenue went',
      parts: [['Goods sold', 'cogs', '#f59e0b'], ['Expenses', 'expenses', '#e11d48'], ['Payroll', 'payroll', '#7c3aed'], ['Supplies', 'supplies', '#0ea5e9'], ['Write-offs', 'write_offs', '#64748b']],
      note: 'Profit view: paid sales minus goods sold, incurred expenses, payroll, supplies used and stock losses.'
    }
  };

  function money(value) {
    return Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function signed(value) { const n = Number(value || 0); return (n < 0 ? '−' : '') + symbol + money(Math.abs(n)); }
  function compact(value) {
    const n = Math.abs(Number(value || 0)), sign = Number(value) < 0 ? '−' : '';
    if (n >= 1e9) return sign + symbol + (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
    if (n >= 1e6) return sign + symbol + (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1e3) return sign + symbol + (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    return sign + symbol + Math.round(n);
  }
  function sum(rows, key) { return rows.reduce((total, row) => total + Number(row[key] || 0), 0); }
  function rowsFor() {
    const source = state.mode === 'profit' ? profit : cash;
    return Array.isArray(source[state.period]) ? source[state.period] : [];
  }
  function setActive(buttons, attr, value) {
    buttons.forEach(btn => {
      const active = btn.dataset[attr] === value;
      btn.classList.toggle('bg-[#8f172d]', active);
      btn.classList.toggle('text-white', active);
      btn.classList.toggle('text-stone-600', !active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }
  function renderSummary(rows, cfg) {
    const inTotal = sum(rows, cfg.inKey), outTotal = sum(rows, cfg.outKey), net = inTotal - outTotal;
    document.getElementById('financial-kpi-in-label').textContent = cfg.inLabel;
    document.getElementById('financial-kpi-out-label').textContent = cfg.outLabel;
    document.getElementById('financial-kpi-net-label').textContent = net < 0 && state.mode === 'profit' ? 'Net loss' : cfg.netLabel;
    document.getElementById('financial-kpi-in').textContent = signed(inTotal);
    document.getElementById('financial-kpi-out').textContent = signed(outTotal);
    const netEl = document.getElementById('financial-kpi-net');
    netEl.textContent = signed(net);
    netEl.classList.toggle('text-rose-700', net < 0);
    netEl.classList.toggle('text-emerald-700', net >= 0);
    document.getElementById('financial-kpi-net-card').classList.toggle('border-rose-300', net < 0);
    document.getElementById('financial-kpi-margin').textContent = inTotal > 0 ? `${(net / inTotal * 100).toFixed(1)}% of ${cfg.inLabel.toLowerCase()}` : '';
  }
  function renderBreakdown(rows, cfg) {
    const bar = document.getElementById('financial-breakdown-bar');
    const legend = document.getElementById('financial-breakdown-legend');
    document.getElementById('financial-breakdown-title').textContent = cfg.title;
    bar.replaceChildren(); legend.replaceChildren();
    const parts = cfg.parts.map(([label, key, color]) => ({ label, color, value: sum(rows, key) })).filter(part => part.value > 0);
    const total = parts.reduce((t, part) => t + part.value, 0);
    if (!total) { legend.textContent = 'No costs recorded in this period.'; legend.className = 'mt-2 text-[11px] text-stone-400'; return; }
    legend.className = 'mt-2 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-[11px]';
    parts.forEach(part => {
      const seg = document.createElement('span'); seg.style.width = `${part.value / total * 100}%`; seg.style.background = part.color; seg.title = `${part.label}: ${signed(part.value)}`; bar.append(seg);
      const item = document.createElement('div'); item.className = 'flex items-center justify-between gap-2';
      const name = document.createElement('span'); name.className = 'flex items-center gap-1.5 text-stone-500';
      const dot = document.createElement('span'); dot.className = 'h-2 w-2 rounded-full'; dot.style.background = part.color;
      name.append(dot, part.label);
      const value = document.createElement('span'); value.className = 'font-mono text-stone-700'; value.textContent = `${compact(part.value)} · ${(part.value / total * 100).toFixed(0)}%`;
      item.append(name, value); legend.append(item);
    });
  }

  function render() {
    const theme = inproficChartTheme(), INK = theme.text;
    const cfg = modes[state.mode], rows = rowsFor(), [grain, span] = periodText[state.period];
    if (chart) { chart.destroy(); chart = null; }
    setActive(periodButtons, 'period', state.period);
    setActive(modeButtons, 'mode', state.mode);
    caption.textContent = `${grain} ${cfg.inLabel.toLowerCase()}, ${cfg.outLabel.toLowerCase()} and ${cfg.netLabel.toLowerCase()} — ${span}`;
    document.getElementById('financial-fiscal-note').textContent = state.mode === 'profit' ? cfg.note : `${cfg.note} Business period: ${fiscal.label || ''}.`;
    renderSummary(rows, cfg);
    renderBreakdown(rows, cfg);
    if (!rows.length) { caption.textContent += ' — no data available'; canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height); return; }

    chart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: rows.map(row => row.label),
        datasets: [
          { label: cfg.inLabel, data: rows.map(row => Number(row[cfg.inKey] || 0)), backgroundColor: GREEN, borderRadius: 5, maxBarThickness: 22, order: 2 },
          { label: cfg.outLabel, data: rows.map(row => Number(row[cfg.outKey] || 0)), backgroundColor: ROSE, borderRadius: 5, maxBarThickness: 22, order: 2 },
          {
            label: cfg.netLabel, type: 'line', data: rows.map(row => Number(row[cfg.netKey] || 0)), order: 1,
            tension: 0.3, borderWidth: 3, borderColor: INK, backgroundColor: INK, pointRadius: 3, pointHoverRadius: 5,
            pointBackgroundColor: ctx => (ctx.raw < 0 ? ROSE : INK),
            segment: { borderColor: ctx => (ctx.p1.parsed.y < 0 ? ROSE : INK) }
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 }, color: theme.text } },
          tooltip: {
            backgroundColor: theme.dark ? '#0b1220' : '#182433', titleColor: '#ffffff', bodyColor: '#e8edf3', footerColor: '#cbd5e1',
            callbacks: {
              label: ctx => `${ctx.dataset.label}: ${signed(ctx.raw)}`,
              afterBody: items => {
                const row = rows[items[0].dataIndex], base = Number(row[cfg.inKey] || 0);
                return base > 0 ? [`Margin: ${(Number(row[cfg.netKey] || 0) / base * 100).toFixed(1)}%`] : [];
              }
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: ctx => (ctx.tick && ctx.tick.value === 0 ? theme.muted : theme.border), lineWidth: ctx => (ctx.tick && ctx.tick.value === 0 ? 1.5 : 1) },
            border: { color: theme.border },
            ticks: { callback: value => compact(value), font: { size: 10 }, color: theme.muted }
          },
          x: { grid: { display: false }, border: { color: theme.border }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: state.period === 'year' || state.period === 'fiscal' ? 12 : 10, font: { size: 10 }, color: theme.muted } }
        }
      }
    });
  }

  periodButtons.forEach(btn => btn.addEventListener('click', () => { state.period = btn.dataset.period; render(); }));
  modeButtons.forEach(btn => btn.addEventListener('click', () => { state.mode = btn.dataset.mode; render(); }));
  // Repaint when the user switches light/dark/system or the palette changes.
  new MutationObserver(() => render()).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
  matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => setTimeout(render, 50));
  render();
});
