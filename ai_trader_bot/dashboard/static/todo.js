const meta = document.getElementById('todo-meta');
const count = document.getElementById('todo-count');
const container = document.getElementById('todo-items');
const actionMeta = document.getElementById('todo-action-meta');
const titleInput = document.getElementById('todo-title');
const priorityInput = document.getElementById('todo-priority');
const categoryInput = document.getElementById('todo-category');
const targetWindowInput = document.getElementById('todo-target-window');
const estimateInput = document.getElementById('todo-estimate');
const summaryInput = document.getElementById('todo-summary');
const detailsInput = document.getElementById('todo-details');
const createBtn = document.getElementById('todo-create-btn');

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function priorityRank(value) {
  const key = String(value || '').toUpperCase().trim();
  if (key === 'P0') return 0;
  if (key === 'P1') return 1;
  if (key === 'P2') return 2;
  if (key === 'P3') return 3;
  return 99;
}

function statusRank(value) {
  const key = String(value || '').toLowerCase().trim();
  if (key === 'in_progress') return 0;
  if (key === 'planned') return 1;
  if (key === 'blocked') return 2;
  if (key === 'deferred') return 3;
  if (key === 'completed') return 4;
  return 99;
}

function idAttr(value) {
  return encodeURIComponent(String(value || ''));
}

function decodeIdAttr(value) {
  try {
    return decodeURIComponent(String(value || ''));
  } catch (_) {
    return String(value || '');
  }
}

function parseDetails(raw) {
  return String(raw || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

async function postTodoAction(payload) {
  const res = await fetch('/api/todo/items', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    throw new Error(String(data.error || `To-do update failed (${res.status})`));
  }
  return data;
}

function itemCard(item) {
  const detailRows = Array.isArray(item.details) ? item.details : [];
  const detailHtml = detailRows.length
    ? `<ul>${detailRows.map((row) => `<li>${escapeHtml(row)}</li>`).join('')}</ul>`
    : '<p class="meta">No details yet.</p>';
  const estimate = String(item.estimate_codex || '').trim();
  const estimateText = estimate ? ` • est ${escapeHtml(estimate)}` : '';
  const status = String(item.status || 'planned');
  const completed = status.toLowerCase() === 'completed';
  const completeButton = completed
    ? '<button class="btn" type="button" disabled>Completed</button>'
    : `<button class="btn btn-primary todo-action" type="button" data-action="complete" data-id="${idAttr(item.id)}">Mark Completed</button>`;

  return `
    <article class="item" style="cursor:default;" data-id="${idAttr(item.id)}">
      <div class="row" style="justify-content:space-between; margin-bottom:6px;">
        <span class="badge">${escapeHtml(item.priority || 'P3')}</span>
        <span class="badge">${escapeHtml(item.status || 'planned')}</span>
      </div>
      <div class="title">${escapeHtml(item.title || 'Untitled Item')}</div>
      <div class="meta" style="margin-bottom:8px;">
        ${escapeHtml(item.category || 'general')} • ${escapeHtml(item.target_window || 'backlog')}${estimateText}
      </div>
      <div class="desc">${escapeHtml(item.summary || '')}</div>
      <div class="desc" style="margin-top:8px;">${detailHtml}</div>
      <div class="row" style="margin-top:8px;">
        ${completeButton}
        <button class="btn todo-action" type="button" data-action="delete" data-id="${idAttr(item.id)}">Delete</button>
        <span class="meta">id: ${escapeHtml(item.id || '')}</span>
      </div>
    </article>
  `;
}

async function loadTodo() {
  const res = await fetch('/api/todo', { cache: 'no-store' });
  const payload = await res.json();
  const items = Array.isArray(payload.items) ? payload.items : [];
  items.sort((a, b) => {
    const byPriority = priorityRank(a.priority) - priorityRank(b.priority);
    if (byPriority !== 0) return byPriority;
    return statusRank(a.status) - statusRank(b.status);
  });

  count.textContent = `${items.length} items`;
  meta.textContent = `${payload.title || 'Implementation To-Do'}${payload.updated_at ? ` • updated ${payload.updated_at}` : ''}`;

  container.innerHTML = items.length
    ? items.map(itemCard).join('')
    : '<p class="meta">No to-do items configured.</p>';
}

createBtn.addEventListener('click', async () => {
  const title = titleInput.value.trim();
  if (!title) {
    actionMeta.textContent = 'Title is required.';
    return;
  }

  const item = {
    title: title,
    priority: priorityInput.value || 'P2',
    status: 'planned',
    category: categoryInput.value.trim() || 'general',
    target_window: targetWindowInput.value.trim() || 'backlog',
    estimate_codex: estimateInput.value.trim(),
    summary: summaryInput.value.trim(),
    details: parseDetails(detailsInput.value),
  };

  try {
    const response = await postTodoAction({ action: 'create', item: item });
    actionMeta.textContent = `Added to-do: ${String((response.item || {}).title || title)}`;
    titleInput.value = '';
    summaryInput.value = '';
    detailsInput.value = '';
    await loadTodo();
  } catch (err) {
    actionMeta.textContent = `Create failed: ${String(err.message || err)}`;
  }
});

container.addEventListener('click', async (event) => {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const button = target.closest('.todo-action');
  if (!button) {
    return;
  }

  const action = String(button.getAttribute('data-action') || '').trim().toLowerCase();
  const itemId = decodeIdAttr(button.getAttribute('data-id') || '');
  if (!itemId) {
    actionMeta.textContent = 'Missing to-do id.';
    return;
  }

  try {
    if (action === 'complete') {
      await postTodoAction({
        action: 'update',
        id: itemId,
        item: { status: 'completed' },
      });
      actionMeta.textContent = `Marked completed: ${itemId}`;
      await loadTodo();
      return;
    }

    if (action === 'delete') {
      await postTodoAction({
        action: 'delete',
        id: itemId,
      });
      actionMeta.textContent = `Deleted to-do: ${itemId}`;
      await loadTodo();
      return;
    }
  } catch (err) {
    actionMeta.textContent = `Action failed: ${String(err.message || err)}`;
  }
});

loadTodo().catch((err) => {
  meta.textContent = `Failed to load to-do list: ${String(err.message || err)}`;
  actionMeta.textContent = `Failed loading to-dos: ${String(err.message || err)}`;
});
