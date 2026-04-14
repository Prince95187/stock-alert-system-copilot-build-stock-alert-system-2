/* ================================================================
   Stock Alert System — Frontend
   Handles: SSE price stream, rule CRUD, live ticker tape, toasts
   ================================================================ */

'use strict';

// ── State ──────────────────────────────────────────────────────
const state = {
  prices:    {},   // { AAPL: { price, timestamp } }
  prevPrices:{},   // for change calculation
  rules:     [],
  alerts:    [],
  connected: false,
};

// ── DOM refs (resolved after DOMContentLoaded) ─────────────────
let $ticker, $pricesGrid, $rulesList, $alertsFeed,
    $ruleCount, $alertCount, $trackedCount,
    $totalAlertsStat, $triggeredTodayStat, $tickersStat,
    $liveDot, $liveLabel, $toastContainer,
    $formTicker, $formType, $formCondition, $formThreshold,
    $formWindow, $formLabel, $formWindowRow, $submitBtn;

// ── Boot ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  resolveRefs();
  setupFormToggle();
  loadRules();
  loadAlerts();
  connectSSE();
});

function resolveRefs() {
  $ticker         = document.getElementById('ticker-tape-inner');
  $pricesGrid     = document.getElementById('prices-grid');
  $rulesList      = document.getElementById('rules-list');
  $alertsFeed     = document.getElementById('alerts-feed');
  $ruleCount      = document.getElementById('rule-count');
  $alertCount     = document.getElementById('alert-count');
  $trackedCount   = document.getElementById('tracked-count');
  $totalAlertsStat   = document.getElementById('stat-total-alerts');
  $triggeredTodayStat= document.getElementById('stat-triggered-today');
  $tickersStat    = document.getElementById('stat-tickers');
  $liveDot        = document.getElementById('live-dot');
  $liveLabel      = document.getElementById('live-label');
  $toastContainer = document.getElementById('toast-container');
  $formTicker     = document.getElementById('f-ticker');
  $formType       = document.getElementById('f-type');
  $formCondition  = document.getElementById('f-condition');
  $formThreshold  = document.getElementById('f-threshold');
  $formWindow     = document.getElementById('f-window');
  $formLabel      = document.getElementById('f-label');
  $formWindowRow  = document.getElementById('window-row');
  $submitBtn      = document.getElementById('submit-btn');

  document.getElementById('rule-form').addEventListener('submit', handleAddRule);
}

// ── SSE connection ──────────────────────────────────────────────
function connectSSE() {
  const sse = new EventSource('/sse/prices');

  sse.onopen = () => {
    setConnected(true);
  };

  sse.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }

    if (msg.type === 'prices') {
      handlePriceUpdate(msg.data);
    } else if (msg.type === 'alert') {
      handleIncomingAlert(msg.data);
    }
  };

  sse.onerror = () => {
    setConnected(false);
    // EventSource retries automatically — just update UI.
    setTimeout(() => {
      if (sse.readyState === EventSource.OPEN) setConnected(true);
    }, 3000);
  };
}

function setConnected(connected) {
  state.connected = connected;
  $liveDot.className  = 'live-dot' + (connected ? '' : ' dead');
  $liveLabel.textContent = connected ? 'Live' : 'Reconnecting…';
}

// ── Price updates ───────────────────────────────────────────────
function handlePriceUpdate(data) {
  // Save previous prices for change calculation.
  Object.assign(state.prevPrices, state.prices);
  Object.assign(state.prices, data);

  renderPricesGrid();
  renderTickerTape();
  updateStats();
}

function renderPricesGrid() {
  const tickers = Object.keys(state.prices);
  if (!tickers.length) return;

  // Build or update cards.
  tickers.forEach(ticker => {
    const price = state.prices[ticker];
    const prev  = state.prevPrices[ticker];
    const change = prev ? ((price - prev) / prev * 100) : 0;
    const dir    = change >= 0 ? 'pos' : 'neg';
    const arrow  = change >= 0 ? '▲' : '▼';
    const flash  = change >= 0.001 ? 'flash-up' : change <= -0.001 ? 'flash-down' : '';

    let card = document.getElementById(`pc-${ticker}`);
    if (!card) {
      card = document.createElement('div');
      card.id = `pc-${ticker}`;
      card.className = 'price-card';
      card.innerHTML = `
        <div class="pc-ticker">${ticker}</div>
        <div class="pc-price" id="pcp-${ticker}">$0.00</div>
        <div class="pc-change" id="pcc-${ticker}"></div>`;
      $pricesGrid.appendChild(card);
    }

    document.getElementById(`pcp-${ticker}`).textContent = `$${price.toFixed(2)}`;
    const changeEl = document.getElementById(`pcc-${ticker}`);
    changeEl.className = `pc-change ${dir}`;
    changeEl.textContent = `${arrow} ${Math.abs(change).toFixed(2)}%`;

    if (flash) {
      card.classList.remove('flash-up', 'flash-down');
      void card.offsetWidth; // reflow to restart animation
      card.classList.add(flash);
      setTimeout(() => card.classList.remove(flash), 600);
    }
  });

  $tickersStat.textContent = tickers.length;
}

function renderTickerTape() {
  const tickers = Object.keys(state.prices);
  // Build two copies for seamless loop.
  const allTickers = [...tickers, ...tickers];
  $ticker.innerHTML = allTickers.map(ticker => {
    const price  = state.prices[ticker] ?? 0;
    const prev   = state.prevPrices[ticker] ?? price;
    const change = prev ? ((price - prev) / prev * 100) : 0;
    const cls    = change >= 0 ? 'up' : 'down';
    const arrow  = change >= 0 ? '▲' : '▼';
    return `
      <div class="ticker-item">
        <span class="ticker-symbol">${ticker}</span>
        <span class="ticker-price">$${price.toFixed(2)}</span>
        <span class="ticker-change ${cls}">${arrow} ${Math.abs(change).toFixed(2)}%</span>
      </div>`;
  }).join('');
}

// ── Incoming alert from SSE ─────────────────────────────────────
function handleIncomingAlert(alertData) {
  // Avoid duplicates if we just loaded via REST.
  if (state.alerts.some(a => a.id === alertData.id)) return;
  state.alerts.unshift(alertData);
  prependAlertCard(alertData, true);
  showAlertToast(alertData);
  updateStats();
}

// ── Load initial data ───────────────────────────────────────────
async function loadRules() {
  try {
    const res = await fetch('/api/rules');
    state.rules = await res.json();
    renderRules();
    updateStats();
  } catch (e) {
    console.error('Failed to load rules', e);
  }
}

async function loadAlerts() {
  try {
    const res = await fetch('/api/alerts?limit=30');
    state.alerts = await res.json();
    renderAlerts();
    updateStats();
  } catch (e) {
    console.error('Failed to load alerts', e);
  }
}

// ── Render rules ────────────────────────────────────────────────
function renderRules() {
  if (!state.rules.length) {
    $rulesList.innerHTML = emptyState('📋', 'No alert rules yet.<br>Add one below.');
    return;
  }
  $rulesList.innerHTML = '';
  state.rules.forEach(rule => $rulesList.appendChild(buildRuleCard(rule)));
  $ruleCount.textContent = state.rules.length;
}

function buildRuleCard(rule) {
  const isPct   = rule.rule_type === 'percentage_move';
  const isAbove = rule.condition === 'above';
  const iconCls = isPct ? 'pct' : isAbove ? 'above' : 'below';
  const icon    = isPct ? '📊' : isAbove ? '▲' : '▼';
  const badgeCls= isPct ? 'rb-pct' : isAbove ? 'rb-above' : 'rb-below';
  const badgeTxt= isPct ? 'PCT MOVE' : isAbove ? 'ABOVE' : 'BELOW';
  const meta    = isPct
    ? `±${rule.threshold}% in ${Math.round(rule.window_seconds / 60)}m`
    : `Threshold: $${Number(rule.threshold).toLocaleString('en-US', {minimumFractionDigits:2})}`;

  const card = document.createElement('div');
  card.className = 'rule-card';
  card.dataset.id = rule.id;
  card.innerHTML = `
    <div class="rule-icon ${iconCls}">${icon}</div>
    <div class="rule-body">
      <div class="rule-name">${escHtml(rule.label || rule.ticker)}</div>
      <div class="rule-meta">
        <span class="rule-badge ${badgeCls}">${badgeTxt}</span>
        <span>${escHtml(rule.ticker)}</span>
        <span>${escHtml(meta)}</span>
      </div>
    </div>
    <button class="rule-delete-btn" aria-label="Delete rule" data-id="${escHtml(rule.id)}">✕</button>`;

  card.querySelector('.rule-delete-btn').addEventListener('click', () => deleteRule(rule.id));
  return card;
}

// ── Render alerts ───────────────────────────────────────────────
function renderAlerts() {
  $alertsFeed.innerHTML = '';
  if (!state.alerts.length) {
    $alertsFeed.innerHTML = emptyState('🔔', 'No alerts triggered yet.');
    return;
  }
  state.alerts.forEach(a => $alertsFeed.appendChild(buildAlertCard(a)));
  $alertCount.textContent = state.alerts.length;
}

function prependAlertCard(alert, animate) {
  // Remove empty state if present.
  const empty = $alertsFeed.querySelector('.empty-state');
  if (empty) empty.remove();

  const card = buildAlertCard(alert, animate);
  $alertsFeed.prepend(card);
  $alertCount.textContent = state.alerts.length;
}

function buildAlertCard(alert, animate = false) {
  const isPct   = alert.rule_type === 'percentage_move';
  const isAbove = alert.condition === 'above';
  const pipCls  = isPct ? 'pct' : isAbove ? 'above' : 'below';
  const priceCls= isPct ? 'apt-pct' : isAbove ? 'apt-above' : 'apt-below';
  const direction = isAbove ? 'above' : 'below';
  const time    = new Date(alert.fired_at * 1000).toLocaleTimeString();

  const card = document.createElement('div');
  card.className = 'alert-item' + (animate ? '' : '');
  card.style.animationDelay = animate ? '0ms' : 'none';
  card.innerHTML = `
    <div class="alert-pip ${pipCls}"></div>
    <div class="alert-content">
      <div class="alert-title">${escHtml(alert.rule_label)}</div>
      <div class="alert-detail">
        <span>${escHtml(alert.ticker)}</span>
        <span>${direction} $${Number(alert.threshold).toFixed(2)}</span>
        <span>${time}</span>
      </div>
    </div>
    <span class="alert-price-tag ${priceCls}">$${Number(alert.trigger_price).toFixed(2)}</span>`;
  return card;
}

// ── Form handling ───────────────────────────────────────────────
function setupFormToggle() {
  // Show / hide window field based on rule type.
  document.getElementById('f-type').addEventListener('change', function() {
    const isPct = this.value === 'percentage_move';
    $formWindowRow.style.display = isPct ? '' : 'none';

    // Adjust condition options for PCT move rules.
    const condSel = document.getElementById('f-condition');
    if (isPct) {
      condSel.options[0].text = 'Moves ≥ threshold (above)';
      condSel.options[1].text = 'Moves < threshold (below)';
    } else {
      condSel.options[0].text = 'Rises above';
      condSel.options[1].text = 'Falls below';
    }
  });
}

async function handleAddRule(evt) {
  evt.preventDefault();
  $submitBtn.disabled = true;
  $submitBtn.textContent = 'Adding…';

  const isPct = $formType.value === 'percentage_move';
  const payload = {
    ticker:       $formTicker.value.trim().toUpperCase(),
    rule_type:    $formType.value,
    condition:    $formCondition.value,
    threshold:    parseFloat($formThreshold.value),
    label:        $formLabel.value.trim() || null,
  };
  if (isPct) payload.window_seconds = parseInt($formWindow.value, 10) * 60;

  try {
    const res = await fetch('/api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail ?? 'Server error');
    }
    const rule = await res.json();
    state.rules.push(rule);

    // Remove empty state, prepend card.
    const empty = $rulesList.querySelector('.empty-state');
    if (empty) empty.remove();
    const card = buildRuleCard(rule);
    $rulesList.prepend(card);
    $ruleCount.textContent = state.rules.length;
    updateStats();

    // Reset form.
    document.getElementById('rule-form').reset();
    $formWindowRow.style.display = 'none';
    showToast(`✓ Alert rule added for ${rule.ticker}`);
  } catch (e) {
    showToast(`✗ ${e.message}`, 'error');
  } finally {
    $submitBtn.disabled = false;
    $submitBtn.textContent = 'Add Alert Rule';
  }
}

async function deleteRule(ruleId) {
  try {
    const res = await fetch(`/api/rules/${ruleId}`, { method: 'DELETE' });
    if (!res.ok && res.status !== 204) throw new Error('Delete failed');

    state.rules = state.rules.filter(r => r.id !== ruleId);
    const card = $rulesList.querySelector(`[data-id="${ruleId}"]`);
    if (card) {
      card.style.transition = 'opacity .2s, transform .2s';
      card.style.opacity = '0';
      card.style.transform = 'translateX(-20px)';
      setTimeout(() => {
        card.remove();
        if (!state.rules.length) {
          $rulesList.innerHTML = emptyState('📋', 'No alert rules yet.<br>Add one below.');
        }
        $ruleCount.textContent = state.rules.length;
        updateStats();
      }, 200);
    }
    showToast('Rule deleted.');
  } catch (e) {
    showToast(`✗ ${e.message}`, 'error');
  }
}

// ── Stats update ────────────────────────────────────────────────
function updateStats() {
  if ($totalAlertsStat) $totalAlertsStat.textContent = state.rules.length;

  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayTs = todayStart.getTime() / 1000;
  const todayCount = state.alerts.filter(a => a.fired_at >= todayTs).length;
  if ($triggeredTodayStat) $triggeredTodayStat.textContent = todayCount;

  const trackedCount = Object.keys(state.prices).length;
  if ($tickersStat) $tickersStat.textContent = trackedCount;
}

// ── Toast ────────────────────────────────────────────────────────
function showToast(message, type = 'info') {
  const t = document.createElement('div');
  t.className = 'toast' + (type === 'alert' ? ' toast-alert' : '');
  t.textContent = message;
  $toastContainer.appendChild(t);
  setTimeout(() => {
    t.classList.add('removing');
    t.addEventListener('animationend', () => t.remove());
  }, 3500);
}

function showAlertToast(alert) {
  const direction = alert.condition === 'above' ? '▲ above' : '▼ below';
  showToast(
    `🚨 ${alert.ticker} ${direction} $${Number(alert.threshold).toFixed(2)} @ $${Number(alert.trigger_price).toFixed(2)}`,
    'alert'
  );
}

// ── Utility ──────────────────────────────────────────────────────
function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function emptyState(icon, text) {
  return `<div class="empty-state"><div class="empty-icon">${icon}</div><div class="empty-text">${text}</div></div>`;
}
