const meta = document.getElementById('todo-meta');
const count = document.getElementById('todo-count');
const container = document.getElementById('todo-items');

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

function itemCard(item) {
  const detailRows = Array.isArray(item.details) ? item.details : [];
  const detailHtml = detailRows.length
    ? `<ul>${detailRows.map((row) => `<li>${escapeHtml(row)}</li>`).join('')}</ul>`
    : '<p class="meta">No details yet.</p>';

  return `
    <article class="item" style="cursor:default;">
      <div class="row" style="justify-content:space-between; margin-bottom:6px;">
        <span class="badge">${escapeHtml(item.priority || 'P3')}</span>
        <span class="badge">${escapeHtml(item.status || 'planned')}</span>
      </div>
      <div class="title">${escapeHtml(item.title || 'Untitled Item')}</div>
      <div class="meta" style="margin-bottom:8px;">
        ${escapeHtml(item.category || 'general')} • ${escapeHtml(item.target_window || 'backlog')}
      </div>
      <div class="desc">${escapeHtml(item.summary || '')}</div>
      <div class="desc" style="margin-top:8px;">${detailHtml}</div>
    </article>
  `;
}

async function loadTodo() {
  const res = await fetch('/api/todo', { cache: 'no-store' });
  const payload = await res.json();
  const items = Array.isArray(payload.items) ? payload.items : [];
  items.sort((a, b) => priorityRank(a.priority) - priorityRank(b.priority));

  count.textContent = `${items.length} items`;
  meta.textContent = `${payload.title || 'Implementation To-Do'}${payload.updated_at ? ` • updated ${payload.updated_at}` : ''}`;

  container.innerHTML = items.length
    ? items.map(itemCard).join('')
    : '<p class="meta">No to-do items configured.</p>';
}

loadTodo().catch((err) => {
  meta.textContent = `Failed to load to-do list: ${String(err.message || err)}`;
});
