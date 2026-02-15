function formatMoney(v) {
  const num = Number(v || 0);
  return `$${num.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

function row(cells) {
  return `<tr>${cells.map((c) => `<td>${c}</td>`).join('')}</tr>`;
}

async function refreshPortfolio() {
  const res = await fetch('/api/portfolio/latest', { cache: 'no-store' });
  const payload = await res.json();

  document.getElementById('last-updated').textContent = payload.timestamp || 'No snapshot yet';
  document.getElementById('equity').textContent = formatMoney(payload.account_equity);
  document.getElementById('cash').textContent = formatMoney(payload.cash);

  const calls = payload.open_calls || [];
  document.getElementById('calls-count').textContent = String(calls.length);
  document.getElementById('calls-body').innerHTML = calls.length
    ? calls.map((r) => row([r.symbol, r.quantity])).join('')
    : row(['No open calls', '-']);

  const equities = payload.equity_positions || [];
  document.getElementById('equity-body').innerHTML = equities.length
    ? equities.map((r) => row([r.symbol, r.quantity])).join('')
    : row(['No holdings', '-']);

  const trades = payload.recent_trades || [];
  document.getElementById('trades-body').innerHTML = trades.length
    ? trades.slice().reverse().slice(0, 50).map((r) => row([
        String(r.timestamp || ''),
        String(r.symbol || ''),
        String(r.instruction || ''),
        String(r.quantity || ''),
        String(r.reason || ''),
      ])).join('')
    : row(['No trade decisions logged', '-', '-', '-', '-']);
}

refreshPortfolio();
setInterval(refreshPortfolio, 15000);
