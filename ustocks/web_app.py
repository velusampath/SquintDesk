from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from flask import Flask, jsonify, render_template_string, request
import yfinance as yf

from .backtest import run_backtest_counts_us, run_backtest_pnl_us
from .service import scan_us

app = Flask(__name__)

API_CACHE: dict[str, tuple[float, object]] = {}


def _cache_get(key: str):
    item = API_CACHE.get(key)
    if not item:
        return None
    expires_at, value = item
    if time.time() > expires_at:
        API_CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: object, ttl_seconds: int) -> None:
    API_CACHE[key] = (time.time() + ttl_seconds, value)


def _us_fno_scan(sentiment: str = "all", universe: str = "mega") -> tuple[str, str, str, list[str]]:
    warnings: list[str] = []
    df, scan_w = scan_us(universe=universe)
    warnings.extend(scan_w)
    if df.empty:
        return "", "", "", warnings
    working = df.copy()
    working["Sentiment"] = working["Trend"].str.lower().map(
        {"bullish": "bullish", "bearish": "bearish"}
    ).fillna("neutral")
    if sentiment == "bull":
        working = working[working["Sentiment"] == "bullish"]
    elif sentiment == "bear":
        working = working[working["Sentiment"] == "bearish"]
    fno_cols = ["Symbol", "Open", "High", "Close", "Day %", "Trend"]
    fno = working[fno_cols] if not working.empty else working
    bear = working[working["Sentiment"] == "bearish"][fno_cols] if not working.empty else working
    universe_df = df[fno_cols]
    return (
        fno.to_html(index=False, classes="results") if not fno.empty else "",
        universe_df.to_html(index=False, classes="results") if not universe_df.empty else "",
        bear.to_html(index=False, classes="results") if not bear.empty else "",
        warnings,
    )


US_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SquintDesk · US Stocks</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    body { font-family: "Segoe UI", Arial, sans-serif; margin: 0; background: #f5f6f8; color: #111827; }
    .top { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 10px 16px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .brand { font-weight: 700; margin-right: 10px; }
    .pill { text-decoration: none; color: #334155; padding: 7px 12px; border-radius: 8px; border: 1px solid #e5e7eb; background: #fff; font-size: 14px; }
    .pill.active { background: #e8f1ff; color: #1d4ed8; border-color: #bfdbfe; }
    .sep { width: 1px; height: 20px; background: #e5e7eb; }
    .container { max-width: 1180px; margin: 16px auto; padding: 0 16px; }
    .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 14px; }
    .row { display: flex; align-items: end; gap: 10px 12px; flex-wrap: wrap; }
    label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; }
    select, input, button { border: 1px solid #d1d5db; border-radius: 8px; padding: 8px 10px; font-size: 14px; }
    button { background: #2563eb; color: #fff; border-color: #2563eb; font-weight: 600; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { border: 1px solid #e5e7eb; padding: 8px; text-align: left; }
    th { background: #f8fafc; }
    .muted { color: #64748b; }
    .warn { color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; padding: 8px; border-radius: 8px; margin-bottom: 6px; }
    .loading { display: flex; gap: 8px; align-items: center; justify-content: center; min-height: 100px; color: #64748b; }
    .spinner { width: 1em; height: 1em; border: 2px solid #bfdbfe; border-top-color: #2563eb; border-radius: 50%; animation: sp .7s linear infinite; }
    .chart-wrap { position: relative; height: min(72vh, 560px); }
    @keyframes sp { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="top">
    <span class="brand">SquintDesk</span>
    <a class="pill" href="/">IND Stocks</a>
    <a class="pill active" href="/us/scanner">US Stocks</a>
    <span class="sep"></span>
    <a class="pill {% if tab=='scanner' %}active{% endif %}" href="/us/scanner">Scanner</a>
    <a class="pill {% if tab=='fno' %}active{% endif %}" href="/us/fno">F&O</a>
    <a class="pill {% if tab=='backtest' %}active{% endif %}" href="/us/backtest">Backtest</a>
  </div>
  <div class="container">
    {% if tab == 'scanner' %}
      <div class="card">
        <div class="row">
          <label>Universe
            <select id="universe">
              <option value="mega">US Mega caps</option>
              <option value="tech">US Tech</option>
            </select>
          </label>
          <button id="run">Run US scan</button>
          <span class="muted" id="status"></span>
        </div>
      </div>
      <div class="card" id="table-host"><p class="muted">Click Run US scan.</p></div>
    {% elif tab == 'fno' %}
      <div class="card">
        <div class="row">
          <label>Filter
            <select id="sentiment_filter">
              <option value="all">All</option>
              <option value="bull">Bull</option>
              <option value="bear">Bear</option>
            </select>
          </label>
          <label>Universe
            <select id="universe">
              <option value="mega">US Mega caps</option>
              <option value="tech">US Tech</option>
            </select>
          </label>
          <button id="refresh">Refresh Data</button>
          <span class="muted">Last refreshed: <span id="last-ref">—</span></span>
        </div>
      </div>
      <div id="warn-host"></div>
      <div class="card"><h2>US Picks</h2><div id="fno-host"></div></div>
      <div class="card"><h2>US Universe Snapshot</h2><div id="uni-host"></div></div>
      <div class="card"><h2>US Bear Watchlist</h2><div id="bear-host"></div></div>
    {% else %}
      <div class="card">
        <h2>How To Read This Chart</h2>
        <p class="muted" style="margin:0;">Signal count shows daily setup flow. P&L mode shows simplified equity curve (base 100).</p>
      </div>
      <div class="card">
        <div class="row">
          <label>Universe
            <select id="universe">
              <option value="mega">US Mega caps</option>
              <option value="tech">US Tech</option>
            </select>
          </label>
          <label>Max symbols
            <select id="maxsym"><option value="60">60</option><option value="120" selected>120</option></select>
          </label>
          <label>Mode
            <select id="btmode"><option value="count">Signal count</option><option value="pnl">P&L backtest</option></select>
          </label>
          <label>Symbol (optional)
            <input id="onesymbol" placeholder="Example: AAPL" />
          </label>
          <button id="run">Run backtest</button>
        </div>
      </div>
      <div id="status" class="muted"></div>
      <div id="warnings"></div>
      <div class="card" id="bt-results-card" style="display:none;">
        <div class="chart-wrap"><canvas id="btchart"></canvas></div>
      </div>
    {% endif %}
  </div>

  {% if tab == 'scanner' %}
  <script>
    async function runScan() {
      const u = document.getElementById("universe").value;
      const host = document.getElementById("table-host");
      const status = document.getElementById("status");
      status.textContent = "Loading...";
      host.innerHTML = '<div class="loading"><span class="spinner"></span><span>Fetching US data...</span></div>';
      try {
        const res = await fetch("/us/api/scanner?universe=" + encodeURIComponent(u));
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || res.statusText);
        host.innerHTML = payload.table_html || '<p class="muted">No records found.</p>';
        status.textContent = payload.warnings && payload.warnings.length ? payload.warnings.join(" | ") : "Done";
      } catch (e) {
        status.textContent = "";
        host.innerHTML = '<p class="warn">' + (e.message || String(e)) + '</p>';
      }
    }
    document.getElementById("run").addEventListener("click", runScan);
  </script>
  {% elif tab == 'fno' %}
  <script>
    function loadingHtml() {
      return '<div class="loading"><span class="spinner"></span><span>Loading data...</span></div>';
    }
    function tableOrMsg(host, html) {
      host.innerHTML = html && html.trim() ? html : '<p class="muted">No records found.</p>';
    }
    async function loadFno() {
      const s = document.getElementById("sentiment_filter").value;
      const u = document.getElementById("universe").value;
      const f = document.getElementById("fno-host");
      const n = document.getElementById("uni-host");
      const b = document.getElementById("bear-host");
      const w = document.getElementById("warn-host");
      f.innerHTML = n.innerHTML = b.innerHTML = loadingHtml();
      w.innerHTML = "";
      try {
        const res = await fetch("/us/api/fno-dashboard?sentiment_filter=" + encodeURIComponent(s) + "&universe=" + encodeURIComponent(u));
        const p = await res.json();
        if (!res.ok) throw new Error(p.error || res.statusText);
        tableOrMsg(f, p.fno_table || "");
        tableOrMsg(n, p.universe_table || "");
        tableOrMsg(b, p.bear_table || "");
        if (p.warnings && p.warnings.length) {
          w.innerHTML = p.warnings.map(x => '<div class="warn">' + x + '</div>').join("");
        }
        document.getElementById("last-ref").textContent = p.last_refreshed || "—";
      } catch (e) {
        f.innerHTML = n.innerHTML = b.innerHTML = '<p class="warn">' + (e.message || String(e)) + '</p>';
      }
    }
    document.getElementById("refresh").addEventListener("click", loadFno);
    document.getElementById("sentiment_filter").addEventListener("change", loadFno);
    document.getElementById("universe").addEventListener("change", loadFno);
    loadFno();
  </script>
  {% else %}
  <script>
    let chart = null;
    async function runBt() {
      const universe = document.getElementById("universe").value;
      const max = document.getElementById("maxsym").value;
      const mode = document.getElementById("btmode").value;
      const one = document.getElementById("onesymbol").value.trim();
      const sym = one ? "&symbol=" + encodeURIComponent(one) : "";
      const status = document.getElementById("status");
      const warnings = document.getElementById("warnings");
      const card = document.getElementById("bt-results-card");
      warnings.innerHTML = "";
      card.style.display = "none";
      status.innerHTML = '<div class="loading"><span class="spinner"></span><span>Loading US backtest...</span></div>';
      try {
        const res = await fetch("/us/api/backtest?mode=" + encodeURIComponent(mode) + "&universe=" + encodeURIComponent(universe) + "&max=" + encodeURIComponent(max) + sym);
        const p = await res.json();
        if (!res.ok) throw new Error(p.error || res.statusText);
        const labels = p.labels || [];
        const datasets = p.datasets || [];
        const meta = p.meta || {};
        if (meta.mode === "pnl") {
          status.textContent = "Trades: " + (meta.trades || 0) + " | Win rate: " + (meta.win_rate_pct || 0) + "% | Return: " + (meta.cumulative_return_pct || 0) + "%";
        } else {
          status.textContent = (meta.symbol_count || 0) + " symbols | " + (meta.period || "6mo");
        }
        if (p.warnings && p.warnings.length) warnings.innerHTML = '<div class="warn">' + p.warnings.join(" | ") + '</div>';
        if (!labels.length) return;
        const ctx = document.getElementById("btchart").getContext("2d");
        if (chart) chart.destroy();
        chart = new Chart(ctx, {
          type: (meta.mode === "pnl") ? "line" : "bar",
          data: { labels, datasets },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index" },
            scales: {
              x: { stacked: meta.mode !== "pnl" },
              y: { stacked: meta.mode !== "pnl", beginAtZero: true },
            },
          },
        });
        card.style.display = "";
      } catch (e) {
        status.textContent = "";
        warnings.innerHTML = '<div class="warn">' + (e.message || String(e)) + '</div>';
      }
    }
    document.getElementById("run").addEventListener("click", runBt);
  </script>
  {% endif %}
</body>
</html>
"""


@app.route("/us/scanner")
def us_scanner() -> str:
    return render_template_string(US_HTML, tab="scanner")


@app.route("/us/fno")
def us_fno() -> str:
    return render_template_string(US_HTML, tab="fno")


@app.route("/us/backtest")
def us_backtest() -> str:
    return render_template_string(US_HTML, tab="backtest")


@app.route("/us/api/scanner")
def us_api_scanner():
    universe = (request.args.get("universe") or "mega").strip().lower()
    cache_key = f"us:scan:{universe}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    df, warnings = scan_us(universe=universe)
    table_html = df.to_html(index=False, classes="results") if not df.empty else ""
    payload = {"table_html": table_html, "warnings": warnings}
    _cache_set(cache_key, payload, 90)
    return jsonify(payload)


@app.route("/us/api/fno-dashboard")
def us_api_fno_dashboard():
    sentiment = (request.args.get("sentiment_filter") or "all").strip().lower()
    if sentiment not in {"all", "bull", "bear"}:
        sentiment = "all"
    universe = (request.args.get("universe") or "mega").strip().lower()
    cache_key = f"us:fno:{sentiment}:{universe}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    fno, uni, bear, warnings = _us_fno_scan(sentiment=sentiment, universe=universe)
    payload = {
        "fno_table": fno,
        "universe_table": uni,
        "bear_table": bear,
        "warnings": warnings,
        "last_refreshed": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache_set(cache_key, payload, 90)
    return jsonify(payload)


@app.route("/us/api/backtest")
def us_api_backtest():
    mode = (request.args.get("mode") or "count").strip().lower()
    if mode not in {"count", "pnl"}:
        mode = "count"
    universe = (request.args.get("universe") or "mega").strip().lower()
    try:
        max_sym = int(request.args.get("max", "120"))
    except ValueError:
        max_sym = 120
    max_sym = max(10, min(max_sym, 250))
    symbol = (request.args.get("symbol") or "").strip() or None
    cache_key = f"us:bt:{mode}:{universe}:{max_sym}:{symbol or ''}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    try:
        if mode == "pnl":
            payload, warnings = run_backtest_pnl_us(universe=universe, max_symbols=max_sym, symbol=symbol)
        else:
            payload, warnings = run_backtest_counts_us(universe=universe, max_symbols=max_sym, symbol=symbol)
        payload["warnings"] = warnings
        _cache_set(cache_key, payload, 180)
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc), "labels": [], "datasets": [], "warnings": []}), 500

