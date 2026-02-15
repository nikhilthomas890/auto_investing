const pane = document.getElementById('log-pane');
const meta = document.getElementById('log-meta');

async function loadLogs() {
  const res = await fetch('/api/system-logs?limit=800', { cache: 'no-store' });
  const payload = await res.json();
  const lines = payload.lines || [];
  pane.textContent = lines.join('\n');
  pane.scrollTop = pane.scrollHeight;
  meta.textContent = `${payload.path || ''} • ${payload.count || 0} lines`;
}

document.getElementById('refresh-btn').addEventListener('click', loadLogs);
loadLogs();
setInterval(loadLogs, 12000);
