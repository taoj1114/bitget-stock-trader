"""交易仪表盘 Web 界面 — 实时版"""

import asyncio, json, os, sqlite3
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()
BASE = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
STATE = os.path.join(BASE, "data", "trader_state.json")
DB = os.path.join(BASE, "data", "trades.db")

HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>AI Trader</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px monospace;background:#0d1117;color:#c9d1d9;padding:20px}
h1{color:#58a6ff;margin-bottom:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:16px;margin-bottom:16px}
.card h2{color:#58a6ff;margin-bottom:12px;font-size:16px}
.row{display:flex;gap:16px;flex-wrap:wrap}
.stat{flex:1;min-width:160px;background:#0d1117;padding:12px;border-radius:4px;text-align:center}
.stat .val{font-size:28px;font-weight:bold;margin:4px 0}
.stat .lbl{color:#8b949e;font-size:11px}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}
table{width:100%;border-collapse:collapse;margin-top:8px}
th,td{padding:8px 12px;text-align:right;border-bottom:1px solid #30363d}
th{color:#8b949e;font-weight:normal;font-size:11px}
td:first-child,th:first-child{text-align:left}
tr:hover{background:#1c2128}
#status{text-align:center;color:#484f58;margin-top:20px;font-size:11px}
</style></head>
<body>
<h1>🤖 AI 交易仪表盘</h1>
<div class="row" id="stats"></div>
<div class="card"><h2>📊 持仓</h2>
<table id="pos"><thead><tr><th>品种</th><th>方向</th><th>数量</th><th>入场价</th><th>当前价</th><th>入场时间</th><th>杠杆</th><th>浮盈</th></tr></thead><tbody></tbody></table>
</div>
<div class="card"><h2>📜 交易历史</h2>
<table id="trades"><thead><tr><th>时间</th><th>品种</th><th>方向</th><th>价格</th><th>数量</th><th>盈亏</th><th>原因</th></tr></thead><tbody></tbody></table>
</div>
<div id="status">加载中...</div>
<script>
async function refresh() {
  try {
    let r = await fetch('/api/live');
    let d = await r.json();
    // Stats
    let pnl = d.equity - d.balance;
    let pc = pnl >= 0 ? 'green' : 'red';
    document.getElementById('stats').innerHTML =
      `<div class="stat"><div class="lbl">净值</div><div class="val">$${d.equity.toLocaleString()}</div></div>` +
      `<div class="stat"><div class="lbl">余额</div><div class="val">$${d.balance.toLocaleString()}</div></div>` +
      `<div class="stat"><div class="lbl">浮盈</div><div class="val ${pc}">${pnl >= 0 ? '+' : ''}$${pnl.toLocaleString()}</div></div>` +
      `<div class="stat"><div class="lbl">持仓</div><div class="val">${d.positions.length}</div></div>` +
      `<div class="stat"><div class="lbl">保证金</div><div class="val">$${d.margin.toLocaleString()}</div></div>`;

    // Positions
    let ph = '';
    for (let p of d.positions) {
      let c = p.pnl >= 0 ? 'green' : 'red';
      ph += `<tr><td>${p.symbol}</td><td>${p.side}</td><td>${p.qty.toFixed(1)}</td>` +
            `<td>$${p.entry.toFixed(2)}</td><td>$${p.mark.toFixed(2)}</td>` +
            `<td>${p.opened||'-'}</td><td>${p.lev}x</td>` +
            `<td class="${c}">${p.pnl >= 0 ? '+' : ''}$${p.pnl.toFixed(1)}</td></tr>`;
    }
    document.querySelector('#pos tbody').innerHTML = ph;

    // Trades
    let th = '';
    for (let t of d.trades) {
      let c = t.pnl >= 0 ? 'green' : 'red';
      th += `<tr><td>${t.time}</td><td>${t.symbol}</td><td>${t.side}</td>` +
            `<td>$${t.price.toFixed(2)}</td><td>${t.qty.toFixed(1)}</td>` +
            `<td class="${c}">${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(1)}</td>` +
            `<td>${t.reason}</td></tr>`;
    }
    document.querySelector('#trades tbody').innerHTML = th;

    document.getElementById('status').innerHTML = '✅ 实时 | ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('status').innerHTML = '⚠️ ' + e.message;
  }
}
refresh();
setInterval(refresh, 10000);
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.get("/api/live")
async def api_live():
    # 读 state
    try:
        with open(STATE) as f:
            s = json.load(f)
    except Exception:
        return {"equity": 0, "balance": 0, "margin": 0, "positions": [], "trades": []}

    # 获取实时报价
    quotes = {}
    try:
        from src.datasources.bitget.market import BitgetMarketSource
        m = BitgetMarketSource()
        symbols = [p["symbol"] for p in s.get("positions", [])]
        tasks = [m.get_quote(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, q in zip(symbols, results):
            if q and not isinstance(q, Exception) and q.mark_price > 0:
                quotes[sym] = q.mark_price
        await m.close()
    except Exception:
        pass

    # 持仓
    positions = []
    for p in s.get("positions", []):
        entry = p.get("entry_price", 0)
        mark = quotes.get(p["symbol"], p.get("mark_price", entry))
        pnl = (mark - entry) * p["quantity"] if p["side"] == "LONG" else (entry - mark) * p["quantity"]
        positions.append({
            "symbol": p["symbol"], "side": p["side"],
            "qty": p["quantity"], "entry": entry, "mark": mark,
            "pnl": round(pnl, 1),
            "opened": (p.get("opened_at", "")[:16] or "-"),
            "lev": p.get("leverage", 5),
        })

    # 净值 = 余额 + 持仓总浮盈
    balance = s.get("current_balance", 0)
    total_pnl = sum(pp["pnl"] for pp in positions)
    equity = balance + total_pnl

    # 交易历史
    trades = []
    if os.path.exists(DB):
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE type='CLOSE' ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()
        conn.close()
        for t in rows:
            trades.append({
                "time": (t["timestamp"] or "")[:16],
                "symbol": t["symbol"], "side": t["side"],
                "price": t["price"] or 0, "qty": t["quantity"] or 0,
                "pnl": t["pnl"] or 0, "reason": (t["reason"] or "")[:20],
            })

    return {
        "equity": round(equity, 0),
        "balance": balance,
        "margin": s.get("used_margin", 0),
        "positions": positions,
        "trades": trades,
    }


def main():
    port = int(os.environ.get("DASHBOARD_PORT", "8081"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
