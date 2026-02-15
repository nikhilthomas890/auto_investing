const meta = document.getElementById('control-meta');
const configKey = document.getElementById('config-key');
const configValue = document.getElementById('config-value');
const configKeyMeta = document.getElementById('config-key-meta');
const overridesBody = document.getElementById('overrides-body');
const actionsList = document.getElementById('actions-list');
const resultsList = document.getElementById('results-list');

let configurableKeys = [];

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function compactJson(value) {
  if (Array.isArray(value) || (value && typeof value === 'object')) {
    return JSON.stringify(value);
  }
  return String(value ?? '');
}

function selectedConfigRow() {
  const key = configKey.value;
  return configurableKeys.find((row) => String(row.key) === String(key)) || null;
}

function updateConfigMeta() {
  const row = selectedConfigRow();
  if (!row) {
    configKeyMeta.textContent = 'No configurable key selected.';
    return;
  }
  const restartHint = row.restart_recommended ? 'Restart recommended after this change.' : 'Live-safe config update.';
  configKeyMeta.textContent = `${row.key} • type=${row.value_type} • ${restartHint}`;
  configValue.value = compactJson(row.current_value);
}

function coerceValue(raw, valueType) {
  const text = String(raw ?? '');
  const trimmed = text.trim();

  if (valueType === 'bool') {
    const lowered = trimmed.toLowerCase();
    if (['1', 'true', 'yes', 'y', 'on'].includes(lowered)) return true;
    if (['0', 'false', 'no', 'n', 'off'].includes(lowered)) return false;
    return text;
  }
  if (valueType === 'int') {
    const parsed = Number.parseInt(trimmed, 10);
    return Number.isNaN(parsed) ? text : parsed;
  }
  if (valueType === 'float') {
    const parsed = Number(trimmed);
    return Number.isNaN(parsed) ? text : parsed;
  }
  if (valueType === 'list') {
    if (trimmed.startsWith('[')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) return parsed;
      } catch (_) {
        // Fall through to string path for comma-separated parsing server side.
      }
    }
    return text;
  }
  return text;
}

async function postControlAction(actionType, payload) {
  const res = await fetch('/api/control/actions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action_type: actionType,
      payload: payload || {},
      apply_now: true,
      requested_by: 'dashboard_control_center',
    }),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    throw new Error(String(data.error || `Request failed (${res.status})`));
  }
  return data;
}

function cardBase(title, subtitle, body, badge) {
  return `
    <article class="item" style="cursor:default;">
      <div class="row" style="justify-content:space-between; margin-bottom:6px;">
        <span class="meta">${escapeHtml(subtitle)}</span>
        <span class="badge">${escapeHtml(badge)}</span>
      </div>
      <div class="title">${escapeHtml(title)}</div>
      <div class="desc">${body}</div>
    </article>
  `;
}

function actionCard(row) {
  const payload = compactJson(row.payload || {});
  return cardBase(
    row.action_type || 'action',
    row.timestamp || '',
    `<div class="meta">id: ${escapeHtml(row.action_id || '')}</div><div class="desc">payload: ${escapeHtml(payload)}</div>`,
    row.requested_by || 'dashboard',
  );
}

function resultCard(row) {
  const message = escapeHtml(row.message || '');
  const changes = compactJson(row.changes || []);
  return cardBase(
    row.action_type || 'result',
    row.timestamp || '',
    `<div class="desc">${message}</div><div class="meta">changes: ${escapeHtml(changes)}</div>`,
    row.status || 'unknown',
  );
}

async function loadConfigurableKeys() {
  const res = await fetch('/api/control/configurable', { cache: 'no-store' });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    throw new Error(String(data.error || `Failed to load configurable keys (${res.status})`));
  }
  configurableKeys = Array.isArray(data.keys) ? data.keys : [];
  configKey.innerHTML = configurableKeys.length
    ? configurableKeys
        .map((row) => `<option value="${escapeHtml(row.key)}">${escapeHtml(row.key)}</option>`)
        .join('')
    : '<option value="">No configurable keys</option>';
  updateConfigMeta();
}

async function loadOverrides() {
  const res = await fetch('/api/control/overrides', { cache: 'no-store' });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    overridesBody.innerHTML = '<tr><td colspan="2">Control disabled or unavailable.</td></tr>';
    return;
  }
  const rows = Object.entries(data.overrides || {});
  overridesBody.innerHTML = rows.length
    ? rows
        .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
        .map(([key, value]) => `<tr><td>${escapeHtml(key)}</td><td>${escapeHtml(compactJson(value))}</td></tr>`)
        .join('')
    : '<tr><td colspan="2">No runtime overrides applied.</td></tr>';
}

async function loadActionsAndResults() {
  const [actionsRes, resultsRes] = await Promise.all([
    fetch('/api/control/actions?limit=120', { cache: 'no-store' }),
    fetch('/api/control/results?limit=120', { cache: 'no-store' }),
  ]);
  const actionsPayload = await actionsRes.json();
  const resultsPayload = await resultsRes.json();

  const actions = Array.isArray(actionsPayload.actions) ? actionsPayload.actions.slice().reverse() : [];
  const results = Array.isArray(resultsPayload.results) ? resultsPayload.results.slice().reverse() : [];

  actionsList.innerHTML = actions.length ? actions.map(actionCard).join('') : '<p class="meta">No actions submitted.</p>';
  resultsList.innerHTML = results.length ? results.map(resultCard).join('') : '<p class="meta">No action results yet.</p>';
}

async function refreshAll() {
  try {
    await Promise.all([loadOverrides(), loadActionsAndResults()]);
  } catch (err) {
    meta.textContent = `Failed refreshing control center: ${String(err.message || err)}`;
  }
}

document.getElementById('set-config-btn').addEventListener('click', async () => {
  try {
    const row = selectedConfigRow();
    if (!row || !row.key) {
      throw new Error('Select a config key first.');
    }
    const value = coerceValue(configValue.value, String(row.value_type || 'str'));
    const response = await postControlAction('set_config', { key: row.key, value });
    const result = response.result || {};
    meta.textContent = result.message || 'Config action submitted.';
    await Promise.all([loadConfigurableKeys(), refreshAll()]);
  } catch (err) {
    meta.textContent = `Set config failed: ${String(err.message || err)}`;
  }
});

document.getElementById('restart-btn').addEventListener('click', async () => {
  try {
    const response = await postControlAction('restart_runtime', {});
    meta.textContent = (response.result && response.result.message) || 'Restart request submitted.';
    await refreshAll();
  } catch (err) {
    meta.textContent = `Restart request failed: ${String(err.message || err)}`;
  }
});

document.getElementById('redeploy-btn').addEventListener('click', async () => {
  try {
    const response = await postControlAction('redeploy_code', {});
    meta.textContent = (response.result && response.result.message) || 'Redeploy request submitted.';
    await refreshAll();
  } catch (err) {
    meta.textContent = `Redeploy request failed: ${String(err.message || err)}`;
  }
});

document.getElementById('model-request-btn').addEventListener('click', async () => {
  const modelName = document.getElementById('model-name').value.trim();
  const rationale = document.getElementById('model-rationale').value.trim();
  const targetQuarter = document.getElementById('model-quarter').value.trim();
  if (!modelName) {
    meta.textContent = 'Model name is required.';
    return;
  }
  try {
    const response = await postControlAction('new_model_request', {
      model_name: modelName,
      rationale: rationale,
      target_quarter: targetQuarter,
    });
    meta.textContent = (response.result && response.result.message) || 'Model request submitted.';
    document.getElementById('model-name').value = '';
    document.getElementById('model-rationale').value = '';
    document.getElementById('model-quarter').value = '';
    await refreshAll();
  } catch (err) {
    meta.textContent = `Model request failed: ${String(err.message || err)}`;
  }
});

configKey.addEventListener('change', updateConfigMeta);

loadConfigurableKeys()
  .then(refreshAll)
  .catch((err) => {
    meta.textContent = `Failed loading control center: ${String(err.message || err)}`;
  });

setInterval(refreshAll, 15000);
