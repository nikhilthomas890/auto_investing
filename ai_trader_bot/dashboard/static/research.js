const datePicker = document.getElementById('date-picker');
const itemsContainer = document.getElementById('items');
const countPill = document.getElementById('count-pill');
const modal = document.getElementById('detail-modal');
const modalTitle = document.getElementById('modal-title');
const modalSummary = document.getElementById('modal-summary');
const modalPoints = document.getElementById('modal-points');
const modalLink = document.getElementById('modal-link');

datePicker.value = new Date().toISOString().slice(0, 10);

document.getElementById('modal-close').addEventListener('click', () => modal.close());

function card(item) {
  const type = String(item.source_type || 'unknown');
  return `
    <article class="item" data-id="${item.item_id}">
      <div class="row" style="justify-content:space-between; margin-bottom:5px;">
        <span class="badge">${type}</span>
        <span class="meta">${item.symbol || ''}</span>
      </div>
      <div class="title">${item.title || '(untitled)'}</div>
      <div class="desc">${item.summary || item.description || ''}</div>
    </article>
  `;
}

let currentItems = [];

function showModal(item) {
  modalTitle.textContent = item.title || '(untitled)';
  modalSummary.textContent = item.summary || item.description || 'No summary available.';
  const points = Array.isArray(item.key_points) ? item.key_points : [];
  modalPoints.innerHTML = points.length
    ? points.map((p) => `<li>${p}</li>`).join('')
    : '<li>No key points generated for this item.</li>';
  modalLink.href = item.link || '#';
  modalLink.style.visibility = item.link ? 'visible' : 'hidden';
  modal.showModal();
}

async function loadResearch() {
  const date = datePicker.value;
  const res = await fetch(`/api/research?date=${encodeURIComponent(date)}`, { cache: 'no-store' });
  const payload = await res.json();
  currentItems = payload.items || [];

  countPill.textContent = `${payload.count || 0} items`;
  itemsContainer.innerHTML = currentItems.length ? currentItems.map(card).join('') : '<p class="meta">No research items for this date.</p>';

  itemsContainer.querySelectorAll('.item').forEach((el) => {
    el.addEventListener('click', () => {
      const id = el.getAttribute('data-id');
      const item = currentItems.find((r) => String(r.item_id) === String(id));
      if (item) showModal(item);
    });
  });
}

datePicker.addEventListener('change', loadResearch);
loadResearch();
