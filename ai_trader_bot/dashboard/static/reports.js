const tabs = [...document.querySelectorAll('.subtab')];
const container = document.getElementById('reports-list');
let activeType = 'daily';

function reportCard(row) {
  const title = row.subject || row.event || 'Report';
  const body = (row.body || '').replace(/\n/g, '<br/>');
  return `
    <article class="item" style="cursor:default;">
      <div class="title">${title}</div>
      <div class="meta" style="margin-bottom:7px;">${row.timestamp || ''}</div>
      <div class="desc">${body}</div>
    </article>
  `;
}

async function loadReports() {
  const res = await fetch(`/api/reports?type=${encodeURIComponent(activeType)}&limit=200`, { cache: 'no-store' });
  const payload = await res.json();
  const rows = payload.reports || [];

  container.innerHTML = rows.length
    ? rows.slice().reverse().map(reportCard).join('')
    : '<p class="meta">No reports for this category yet.</p>';
}

tabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    tabs.forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    activeType = tab.getAttribute('data-type') || 'daily';
    loadReports();
  });
});

loadReports();
setInterval(loadReports, 20000);
