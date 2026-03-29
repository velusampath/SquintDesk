from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import time

from flask import Flask, jsonify, render_template_string, request
import pandas as pd

from main import load_config
from scanner.screener import StockScreener
from ustocks.backtest import run_backtest_counts_us, run_backtest_pnl_us
from ustocks.service import US_UNIVERSES, scan_us

# Local: config.yml. Production: set CONFIG_PATH or rely on bootstrap copying config.fno.quick.yml.
CONFIG_FILE = Path(os.environ.get("CONFIG_PATH") or "config.yml")

app = Flask(__name__)
UPLOADS_DIR = Path("uploads")
API_CACHE: dict[str, tuple[float, object]] = {}
SYMBOLS_CACHE_TTL_S = 300
BACKTEST_CACHE_TTL_S = 180
FNO_CACHE_TTL_S = 90
COMMON_NON_SYMBOL_WORDS = {
    "SCREENSHOT",
    "IMAGE",
    "IMG",
    "PHOTO",
    "CHART",
    "TRADINGVIEW",
    "NSE",
    "BSE",
    "PNG",
    "JPG",
    "JPEG",
    "WEBP",
    "GIF",
    "WHATSAPP",
    "CAMERA",
}


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

VIEW_COLUMNS = [
    "symbol",
    "open",
    "day_high",
    "close",
    "entry_price",
    "target_1",
    "target_2",
    "stop_loss",
    "support_20d",
    "bull_or_bearish",
]
RENAME_MAP = {
    "symbol": "Symbol",
    "open": "Open",
    "day_high": "High",
    "close": "Close",
    "entry_price": "Entry",
    "target_1": "T1",
    "target_2": "T2",
    "stop_loss": "S1",
    "support_20d": "S2",
    "bull_or_bearish": "bull_or_bearish",
}

THEME_HEAD_INIT = """
<script>
(function(){try{var k='nse-scanner-theme',t=localStorage.getItem(k);if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
</script>
"""

THEME_SCRIPT = """
<script>
(function(){
  var KEY='nse-scanner-theme';
  function apply(t,persist){
    document.documentElement.setAttribute('data-theme',t);
    if(persist){try{localStorage.setItem(KEY,t);}catch(e){}}
    var b=document.getElementById('theme-toggle');
    if(b)b.textContent=t==='dark'?'Light mode':'Dark mode';
  }
  document.addEventListener('DOMContentLoaded',function(){
    var t=document.documentElement.getAttribute('data-theme');
    if(t!=='dark'&&t!=='light')t='light';
    apply(t,false);
    var btn=document.getElementById('theme-toggle');
    if(btn)btn.addEventListener('click',function(){
      var next=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
      apply(next,true);
      if(typeof window.onNseThemeChange==='function')window.onNseThemeChange(next);
    });
  });
})();
</script>
"""

NSE_LOADING_CSS = """
    .nse-spinner {
      display: inline-block;
      width: 1.1em;
      height: 1.1em;
      border: 2px solid rgba(37, 99, 235, 0.22);
      border-top-color: #2563eb;
      border-radius: 50%;
      animation: nse-spin 0.72s linear infinite;
      vertical-align: -0.12em;
      flex-shrink: 0;
    }
    html[data-theme="dark"] .nse-spinner {
      border-color: rgba(96, 165, 250, 0.28);
      border-top-color: #60a5fa;
    }
    @keyframes nse-spin {
      to { transform: rotate(360deg); }
    }
    .nse-loading-block {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      width: 100%;
      min-height: 120px;
      padding: 24px 16px;
      box-sizing: border-box;
      color: var(--muted);
      font-size: 14px;
    }
    .nse-loading-block .nse-spinner {
      width: 1.35em;
      height: 1.35em;
      border-width: 2.5px;
    }
    .nse-loading-inline {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
    }
    .symbol-combo.is-loading::after,
    .screenshot-symbol-combo.is-loading::after {
      content: "";
      position: absolute;
      right: 10px;
      top: 50%;
      width: 1em;
      height: 1em;
      margin-top: -0.5em;
      border: 2px solid rgba(37, 99, 235, 0.22);
      border-top-color: #2563eb;
      border-radius: 50%;
      animation: nse-spin 0.72s linear infinite;
      pointer-events: none;
    }
    html[data-theme="dark"] .symbol-combo.is-loading::after,
    html[data-theme="dark"] .screenshot-symbol-combo.is-loading::after {
      border-color: rgba(96, 165, 250, 0.28);
      border-top-color: #60a5fa;
    }
"""

NSE_TABLE_PAGER_CSS = """
    .nse-table-pagewrap { width: 100%; }
    .nse-table-pager {
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--card-border);
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 10px;
    }
    .nse-pager-nav {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 10px 16px;
      width: 100%;
    }
    .nse-pager-btn {
      background: var(--btn-bg);
      color: var(--btn-color);
      border: 0;
      padding: 8px 14px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
    }
    .nse-pager-btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    .nse-pager-meta {
      font-size: 13px;
      color: var(--muted);
      text-align: right;
    }
    .nse-table-card-head {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px 16px;
      margin-bottom: 6px;
    }
    .nse-table-card-head h2 {
      margin: 0;
      flex: 1 1 auto;
      min-width: 0;
    }
    .nse-table-search-wrap {
      flex: 0 0 auto;
      margin-left: auto;
    }
    .nse-table-symbol-search {
      width: min(220px, 46vw);
      padding: 8px 12px;
      border-radius: 8px;
      border: 1px solid var(--select-border);
      background: var(--input-bg);
      color: var(--input-text);
      font-size: 14px;
      box-sizing: border-box;
    }
    .nse-table-symbol-search::placeholder { color: var(--muted); opacity: 0.85; }
    .nse-table-symbol-search:focus {
      outline: none;
      border-color: var(--btn-bg);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18);
    }
    html[data-theme="dark"] .nse-table-symbol-search:focus {
      box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.22);
    }
"""

NSE_TABLE_PAGER_JS = """
    function nseNormalizeSymbolText(s) {
      return (s || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    }
    function nseSymbolColumnIndex(table) {
      const ths = table.querySelectorAll("thead th");
      let idx = -1;
      ths.forEach((th, i) => {
        if (th.textContent.trim().toLowerCase() === "symbol") idx = i;
      });
      return idx >= 0 ? idx : 0;
    }
    function nseEnsureTableCardToolbar(card) {
      if (!card) return null;
      let head = card.querySelector(":scope > .nse-table-card-head");
      if (head) {
        return head.querySelector(".nse-table-symbol-search");
      }
      const h2 = card.querySelector(":scope > h2");
      if (!h2) return null;
      head = document.createElement("div");
      head.className = "nse-table-card-head";
      const searchWrap = document.createElement("div");
      searchWrap.className = "nse-table-search-wrap";
      const input = document.createElement("input");
      input.type = "search";
      input.className = "nse-table-symbol-search";
      input.placeholder = "Search symbol…";
      input.setAttribute("aria-label", "Filter rows by symbol");
      searchWrap.appendChild(input);
      h2.replaceWith(head);
      head.appendChild(h2);
      head.appendChild(searchWrap);
      return input;
    }
    function initSortablePaginatedTables() {
      const perPage = 10;
      document.querySelectorAll("main table.results").forEach((table) => {
        const tbody = table.tBodies[0];
        if (!tbody) return;

        let wrap = table.parentElement;
        if (!wrap || !wrap.classList.contains("nse-table-pagewrap")) {
          wrap = document.createElement("div");
          wrap.className = "nse-table-pagewrap";
          table.parentNode.insertBefore(wrap, table);
          wrap.appendChild(table);
          const pagerElNew = document.createElement("div");
          pagerElNew.className = "nse-table-pager";
          wrap.appendChild(pagerElNew);
        }

        const pagerEl = wrap.querySelector(".nse-table-pager");
        const card = table.closest(".card");
        const symColIdx = nseSymbolColumnIndex(table);
        const state = { page: 1, searchRaw: "" };

        function rowMatchesSearch(row) {
          const q = nseNormalizeSymbolText(state.searchRaw);
          if (!q) return true;
          const cell = row.cells[symColIdx];
          const cellQ = nseNormalizeSymbolText(cell ? cell.textContent : "");
          return cellQ.indexOf(q) !== -1;
        }

        function updatePagerUi() {
          const allRows = Array.from(tbody.rows);
          const filtered = allRows.filter(rowMatchesSearch);
          const n = filtered.length;
          const totalInTable = allRows.length;

          if (n === 0) {
            allRows.forEach((r) => {
              r.style.display = "none";
            });
            if (!pagerEl) return;
            pagerEl.innerHTML = "";
            pagerEl.style.display = totalInTable ? "" : "none";
            if (totalInTable) {
              const row = document.createElement("div");
              row.className = "nse-pager-nav";
              const msg = document.createElement("span");
              msg.className = "nse-pager-meta";
              msg.textContent = state.searchRaw.trim()
                ? "No symbols match your search."
                : "";
              row.appendChild(msg);
              pagerEl.appendChild(row);
            }
            return;
          }

          const totalPages = Math.max(1, Math.ceil(n / perPage));
          state.page = Math.min(Math.max(1, state.page), totalPages);
          const start = (state.page - 1) * perPage;
          const pageSlice = filtered.slice(start, start + perPage);
          allRows.forEach((row) => {
            row.style.display = pageSlice.indexOf(row) !== -1 ? "" : "none";
          });

          if (!pagerEl) return;
          pagerEl.innerHTML = "";
          pagerEl.style.display = "";

          const nav = document.createElement("div");
          nav.className = "nse-pager-nav";

          function addBtn(label, disabled, onClick) {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "nse-pager-btn";
            b.textContent = label;
            b.disabled = disabled;
            if (!disabled) b.addEventListener("click", onClick);
            nav.appendChild(b);
          }

          addBtn("Previous", state.page <= 1, () => {
            state.page -= 1;
            updatePagerUi();
          });

          const lab = document.createElement("span");
          lab.className = "nse-pager-meta";
          let meta =
            "Page " + state.page + " of " + totalPages + " · showing " + pageSlice.length + " of " + n + " rows";
          if (state.searchRaw.trim() && n < totalInTable) {
            meta += " (" + totalInTable + " total in table)";
          }
          lab.textContent = meta;
          nav.appendChild(lab);

          addBtn("Next", state.page >= totalPages, () => {
            state.page += 1;
            updatePagerUi();
          });

          pagerEl.appendChild(nav);
        }

        const headers = table.querySelectorAll("thead th");
        headers.forEach((header, colIndex) => {
          header.classList.add("sortable");
          header.dataset.sortDir = "none";
          header.addEventListener("click", () => {
            const rows = Array.from(tbody.rows);
            const nextDir = header.dataset.sortDir === "asc" ? "desc" : "asc";
            headers.forEach((h) => {
              h.dataset.sortDir = "none";
            });
            header.dataset.sortDir = nextDir;

            rows.sort((rowA, rowB) => {
              const textA = (rowA.cells[colIndex]?.textContent || "").trim();
              const textB = (rowB.cells[colIndex]?.textContent || "").trim();
              const numA = Number.parseFloat(textA.replace(/,/g, ""));
              const numB = Number.parseFloat(textB.replace(/,/g, ""));
              const bothNumeric = Number.isFinite(numA) && Number.isFinite(numB);
              if (bothNumeric) {
                return nextDir === "asc" ? numA - numB : numB - numA;
              }
              return nextDir === "asc"
                ? textA.localeCompare(textB, undefined, { sensitivity: "base" })
                : textB.localeCompare(textA, undefined, { sensitivity: "base" });
            });

            rows.forEach((row) => tbody.appendChild(row));
            state.page = 1;
            updatePagerUi();
          });
        });

        const searchInput = nseEnsureTableCardToolbar(card);
        if (searchInput) {
          searchInput.value = "";
          state.searchRaw = "";
          searchInput.oninput = function () {
            state.searchRaw = searchInput.value;
            state.page = 1;
            updatePagerUi();
          };
        }

        updatePagerUi();
      });
    }
"""

DEFAULT_DASHBOARD_FILTERS: dict[str, dict[str, object]] = {
    "swing": {
        "min_price": 100,
        "min_traded_value": 100_000_000,
        "min_volume_ratio": 1.5,
        "rsi_min": 45,
        "rsi_max": 75,
    },
    "long_term": {
        "min_price": 100,
        "min_traded_value": 100_000_000,
        "max_pe": 35,
        "min_roe": 0.15,
        "max_debt_to_equity": 80,
        "min_operating_cashflow": 1,
    },
    "fno": {
        "min_price": 100,
        "min_traded_value": 150_000_000,
        "min_atr": 5,
        "min_volume_ratio": 1.2,
        "min_oi_change_pct": 2.0,
        "require_oi_data": False,
    },
}


def _filters_for_dashboard() -> dict[str, dict[str, object]]:
    merged: dict[str, dict[str, object]] = {k: dict(v) for k, v in DEFAULT_DASHBOARD_FILTERS.items()}
    try:
        cfg = load_config(CONFIG_FILE)
        f = cfg.get("filters")
        if isinstance(f, dict):
            for cat, rules in f.items():
                if cat in merged and isinstance(rules, dict):
                    merged[cat].update(rules)
    except FileNotFoundError:
        pass
    return merged


DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
""" + THEME_HEAD_INIT + """
  <title>Dashboard · SquintDesk</title>
  <style>
    :root, html[data-theme="light"] {
      --page-bg: #f5f6f8;
      --text: #111827;
      --muted: #6b7280;
      --card-bg: #ffffff;
      --card-border: #e5e7eb;
      --link: #2563eb;
      --theme-toggle-bg: #e0f2fe;
      --theme-toggle-color: #1d4ed8;
      --nav-bg: #ffffff;
      --nav-border: #eceef2;
      --nav-shadow: 0 1px 0 rgba(15, 23, 42, 0.06);
      --accent-soft: #e8f1ff;
      --card-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 8px 28px rgba(15, 23, 42, 0.07);
    }
    html[data-theme="dark"] {
      --page-bg: #0f1419;
      --text: #e6edf3;
      --muted: #8b949e;
      --card-bg: #1a2332;
      --card-border: #30363d;
      --link: #60a5fa;
      --theme-toggle-bg: #1e3a5f;
      --theme-toggle-color: #93c5fd;
      --nav-bg: #161b22;
      --nav-border: #30363d;
      --nav-shadow: none;
      --accent-soft: #1f2937;
      --card-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
    }
    * { box-sizing: border-box; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      background: var(--page-bg);
      color: var(--text);
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }
    .site-topnav {
      background: var(--nav-bg);
      border-bottom: 1px solid var(--nav-border);
      box-shadow: var(--nav-shadow);
      position: sticky;
      top: 0;
      z-index: 300;
    }
    .site-topnav-inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 24px;
      min-height: 56px;
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .site-brand {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: var(--text);
      font-weight: 600;
      font-size: 1.05rem;
      letter-spacing: -0.02em;
      margin-right: 8px;
    }
    .site-brand-text { text-transform: none; }
    .site-brand-mark {
      width: 22px;
      height: 22px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 3px;
    }
    .site-brand-mark i { display: block; background: var(--text); border-radius: 2px; font-style: normal; }
    .site-topnav-nav { display: flex; align-items: center; gap: 2px; flex: 1; flex-wrap: wrap; }
    .site-topnav-divider { width: 1px; height: 20px; background: var(--card-border); margin: 0 8px; }
    .site-topnav-link {
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
      padding: 8px 14px;
      border-radius: 8px;
      transition: background 0.15s, color 0.15s;
    }
    .site-topnav-link:hover { color: var(--link); background: var(--page-bg); }
    .site-topnav-link.is-active { color: var(--link); background: var(--accent-soft); }
    .site-subnav { background: var(--nav-bg); border-bottom: 1px solid var(--nav-border); }
    .site-subnav-inner { max-width: 1200px; margin: 0 auto; padding: 8px 24px; display: flex; gap: 8px; flex-wrap: wrap; }
    .site-theme-btn { margin-left: auto; border: 1px solid rgba(37, 99, 235, 0.22); font-weight: 500; }
    html[data-theme="dark"] .site-theme-btn { border-color: rgba(96, 165, 250, 0.35); }
    .site-main { max-width: 1200px; margin: 0 auto; padding: 28px 24px 56px; }
    .site-hero { margin-bottom: 24px; }
    .site-hero h1 {
      font-size: 1.75rem;
      font-weight: 600;
      letter-spacing: -0.03em;
      margin: 0 0 8px;
      color: var(--text);
    }
    .site-hero .lead { font-size: 15px; max-width: 62ch; margin: 0; color: var(--muted); }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 22px 24px;
      margin-bottom: 18px;
      box-shadow: var(--card-shadow);
    }
    .card h2 { margin: 0 0 12px; font-size: 1.1rem; font-weight: 600; letter-spacing: -0.02em; }
    .muted { color: var(--muted); font-size: 14px; }
    .criteria-list { margin: 0; padding-left: 1.15rem; }
    .criteria-list li { margin-bottom: 8px; }
    .criteria-list li:last-child { margin-bottom: 0; }
    .theme-toggle {
      background: var(--theme-toggle-bg);
      color: var(--theme-toggle-color);
      padding: 8px 16px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 14px;
      border: 1px solid rgba(37, 99, 235, 0.22);
    }
    html[data-theme="dark"] .theme-toggle { border-color: rgba(96, 165, 250, 0.35); }
  </style>
</head>
<body>
  <header class="site-topnav">
    <div class="site-topnav-inner">
      <a href="/" class="site-brand">
        <span class="site-brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span class="site-brand-text">SquintDesk</span>
      </a>
      <nav class="site-topnav-nav" aria-label="Main navigation">
        <a href="/" class="site-topnav-link {% if top_nav == 'dashboard' %}is-active{% endif %}">Dashboard</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/scanner" class="site-topnav-link {% if top_nav == 'ind' %}is-active{% endif %}">IND Stocks</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/us/scanner" class="site-topnav-link {% if top_nav == 'us' %}is-active{% endif %}">US Stocks</a>
      </nav>
      <button type="button" class="theme-toggle site-theme-btn" id="theme-toggle" aria-label="Toggle light or dark theme">Dark mode</button>
    </div>
  </header>
  <main class="site-main">
    <div class="site-hero">
      <h1>Dashboard</h1>
      <p class="lead">How we pick stocks in this app: rule-based screening on daily NSE data (via Yahoo Finance). A symbol must pass <strong>all</strong> checks in a category to appear in that bucket.</p>
    </div>

    <div class="card">
      <h2>Swing</h2>
      <p class="muted" style="margin:0 0 12px;">Short-term momentum and breakout-style setups. Thresholds come from <code>config.yml</code> → <code>filters.swing</code> (same values the scanner uses).</p>
      <ul class="criteria-list">
        <li>Price: last daily close ≥ ₹{{ filters.swing.min_price }}.</li>
        <li>Liquidity: <strong>close × volume</strong> on that bar ≥ ₹{{ (filters.swing.min_traded_value / 10000000)|round(1) }} Cr (approximate day value; not exchange-reported turnover).</li>
        <li>Trend: close &gt; EMA(20) and EMA(20) &gt; EMA(50) on daily history (same period/interval as <code>market</code> in config).</li>
        <li>Volume: today’s volume ÷ 20-day average volume ≥ {{ filters.swing.min_volume_ratio }}.</li>
        <li>Breakout: today’s close is <strong>strictly above</strong> the highest daily high of the <strong>previous 20 completed sessions</strong> (resistance = rolling max of past highs; today’s high is not part of that window).</li>
        <li>RSI(14) between {{ filters.swing.rsi_min }} and {{ filters.swing.rsi_max }} (Wilder RSI on closes).</li>
      </ul>
    </div>

    <div class="card">
      <h2>Long term</h2>
      <p class="muted" style="margin:0 0 12px;">Fundamental quality filters. Thresholds from <code>config.yml</code> → <code>filters.long_term</code>. Fundamentals are read from Yahoo <code>ticker.info</code> (latest snapshot — not point-in-time for historical analysis).</p>
      <ul class="criteria-list">
        <li>Data: trailing PE, ROE, debt-to-equity, and operating cash flow must all be present; any missing → excluded.</li>
        <li>Price: close ≥ ₹{{ filters.long_term.min_price }}.</li>
        <li>Liquidity: close × volume ≥ ₹{{ (filters.long_term.min_traded_value / 10000000)|round(1) }} Cr (same convention as Swing).</li>
        <li>Valuation: PE &gt; 0 and PE ≤ {{ filters.long_term.max_pe }} (Yahoo: <code>trailingPE</code>, else <code>forwardPE</code>).</li>
        <li>ROE ≥ {{ (filters.long_term.min_roe * 100)|round(1) }}% — config <code>min_roe</code> is a <strong>decimal</strong> (e.g. 0.15) compared to Yahoo <code>returnOnEquity</code>.</li>
        <li>Debt-to-equity ≤ {{ filters.long_term.max_debt_to_equity }} (same numeric scale as Yahoo <code>debtToEquity</code>).</li>
        <li>Operating cash flow ≥ {{ filters.long_term.min_operating_cashflow }} (same units as Yahoo <code>operatingCashflow</code>).</li>
      </ul>
    </div>

    <div class="card">
      <h2>F&amp;O</h2>
      <p class="muted" style="margin:0 0 12px;">Only symbols in your <code>fno_symbols</code> list. Thresholds are read from <code>config.yml</code> → <code>filters.fno</code> (same values the scanner code uses).</p>
      <ul class="criteria-list">
        <li>Universe: symbol must be in the configured F&amp;O list.</li>
        <li>Price: close ≥ ₹{{ filters.fno.min_price }}.</li>
        <li>Liquidity: traded value ≥ ₹{{ (filters.fno.min_traded_value / 10000000)|round(1) }} Cr.</li>
        <li>Volatility: ATR(14) ≥ {{ filters.fno.min_atr }} (absolute ₹; not % of price).</li>
        <li>Volume: ratio vs 20-day average ≥ {{ filters.fno.min_volume_ratio }}.</li>
        <li>Volume activity: today’s volume &gt; its 20-day average volume (volume rise).</li>
        <li>Open interest <strong>gate</strong>: when OI % change is available, <strong>|OI % change| ≥ {{ filters.fno.min_oi_change_pct|default(1.5) }}%</strong> must pass. {% if filters.fno.require_oi_data %}If OI data is missing, the symbol <strong>does not</strong> qualify.{% else %}If OI is missing, this gate is skipped (symbol can still qualify on price/volume).{% endif %}</li>
        <li>Bullish / bearish <strong>label</strong> (must not be neutral): built from a score — EMA trend (close vs EMA20 vs EMA50), candle (close vs open), volume rise + move vs previous close, and OI: the <strong>same</strong> <code>min_oi_change_pct</code> ({{ filters.fno.min_oi_change_pct|default(1.5) }}%) is used to decide when OI adds to the score (positive vs negative OI change vs price). Final net score must lean by at least <strong>2 points</strong> toward bull or bear; otherwise the symbol is neutral and is <strong>not</strong> shown as F&amp;O.</li>
      </ul>
    </div>
  </main>
""" + THEME_SCRIPT + """
</body>
</html>
"""

HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
""" + THEME_HEAD_INIT + """
  <title>NSE Stock Scanner</title>
  <style>
    :root, html[data-theme="light"] {
      --page-bg: #f5f6f8;
      --text: #111827;
      --muted: #6b7280;
      --card-bg: #ffffff;
      --card-border: #e5e7eb;
      --table-border: #e5e7eb;
      --th-bg: #f9fafb;
      --btn-bg: #2563eb;
      --btn-color: #fff;
      --select-border: #d1d5db;
      --input-bg: #ffffff;
      --input-text: #111827;
      --input-border: #d1d5db;
      --link: #2563eb;
      --warn-text: #b91c1c;
      --warn-bg: #fef2f2;
      --warn-border: #fecaca;
      --theme-toggle-bg: #e0f2fe;
      --theme-toggle-color: #1d4ed8;
      --dd-hover: #eff6ff;
      --dd-border: #bfdbfe;
      --dd-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
      --nav-bg: #ffffff;
      --nav-border: #eceef2;
      --nav-shadow: 0 1px 0 rgba(15, 23, 42, 0.06);
      --accent-soft: #e8f1ff;
      --card-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 8px 28px rgba(15, 23, 42, 0.07);
    }
    html[data-theme="dark"] {
      --page-bg: #0f1419;
      --text: #e6edf3;
      --muted: #8b949e;
      --card-bg: #1a2332;
      --card-border: #30363d;
      --table-border: #3d4a5c;
      --th-bg: #252f3f;
      --btn-bg: #3b82f6;
      --btn-color: #fff;
      --select-border: #4a5568;
      --input-bg: #0d1117;
      --input-text: #e6edf3;
      --input-border: #30363d;
      --link: #60a5fa;
      --warn-text: #ffb1b1;
      --warn-bg: #2d1b1b;
      --warn-border: #4a2020;
      --theme-toggle-bg: #1e3a5f;
      --theme-toggle-color: #93c5fd;
      --dd-hover: #2a3447;
      --dd-border: #3d4f6e;
      --dd-shadow: 0 14px 36px rgba(0, 0, 0, 0.55);
      --nav-bg: #161b22;
      --nav-border: #30363d;
      --nav-shadow: none;
      --accent-soft: #1f2937;
      --card-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
    }
    * { box-sizing: border-box; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      background: var(--page-bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    .site-topnav {
      background: var(--nav-bg);
      border-bottom: 1px solid var(--nav-border);
      box-shadow: var(--nav-shadow);
      position: sticky;
      top: 0;
      z-index: 300;
    }
    .site-topnav-inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 24px;
      min-height: 56px;
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .site-brand {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: var(--text);
      font-weight: 600;
      font-size: 1.05rem;
      letter-spacing: -0.02em;
      margin-right: 8px;
    }
    .site-brand-text { text-transform: none; }
    .site-brand-mark {
      width: 22px;
      height: 22px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 3px;
    }
    .site-brand-mark i { display: block; background: var(--text); border-radius: 2px; font-style: normal; }
    .site-topnav-nav { display: flex; align-items: center; gap: 2px; flex: 1; flex-wrap: wrap; }
    .site-topnav-divider {
      width: 1px; height: 20px; background: var(--card-border); margin: 0 8px;
    }
    .site-topnav-link {
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
      padding: 8px 14px;
      border-radius: 8px;
      transition: background 0.15s, color 0.15s;
    }
    .site-topnav-link:hover { color: var(--link); background: var(--page-bg); }
    .site-topnav-link.is-active { color: var(--link); background: var(--accent-soft); }
    .site-subnav { background: var(--nav-bg); border-bottom: 1px solid var(--nav-border); }
    .site-subnav-inner { max-width: 1200px; margin: 0 auto; padding: 8px 24px; display: flex; gap: 8px; flex-wrap: wrap; }
    .site-theme-btn {
      margin-left: auto;
      border: 1px solid rgba(37, 99, 235, 0.22);
      font-weight: 500;
    }
    html[data-theme="dark"] .site-theme-btn { border-color: rgba(96, 165, 250, 0.35); }
    .site-main { max-width: 1200px; margin: 0 auto; padding: 28px 24px 56px; }
    .site-hero { margin-bottom: 28px; }
    .site-hero h1 {
      font-size: 1.75rem;
      font-weight: 600;
      letter-spacing: -0.03em;
      margin: 0 0 8px;
      color: var(--text);
    }
    .site-hero .lead { font-size: 15px; line-height: 1.55; max-width: 56ch; margin: 0; color: var(--muted); }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 22px 24px;
      margin-bottom: 20px;
      box-shadow: var(--card-shadow);
    }
    h2 { margin-top: 0; font-size: 1.15rem; font-weight: 600; letter-spacing: -0.02em; }
    .muted { color: var(--muted); }
    a { color: var(--link); }
    .theme-toggle {
      background: var(--theme-toggle-bg); color: var(--theme-toggle-color);
      padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px;
    }
    button:not(.theme-toggle) {
      background: var(--btn-bg); color: var(--btn-color); border: 0;
      padding: 10px 18px; border-radius: 8px; cursor: pointer; font-weight: 500;
    }
    select { padding: 8px; border-radius: 8px; border: 1px solid var(--select-border); margin-right: 8px; background: var(--input-bg); color: var(--input-text); }
    input[type="text"] { padding: 8px; border-radius: 8px; border: 1px solid var(--input-border); background: var(--input-bg); color: var(--input-text); }
    table { border-collapse: collapse; width: 100%; margin-top: 8px; font-size: 14px; }
    th, td { border: 1px solid var(--table-border); padding: 8px; text-align: left; }
    th { background: var(--th-bg); color: var(--text); }
    th.sortable { cursor: pointer; user-select: none; }
    .warn { color: var(--warn-text); background: var(--warn-bg); border: 1px solid var(--warn-border); padding: 8px; border-radius: 6px; margin-bottom: 6px; }
""" + NSE_LOADING_CSS + NSE_TABLE_PAGER_CSS + """
    .screenshot-analysis-card { overflow: visible; position: relative; z-index: 0; }
    .screenshot-form-row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px 14px;
      margin-bottom: 12px;
    }
    .screenshot-symbol-wrap {
      display: flex;
      flex-wrap: nowrap;
      align-items: center;
      gap: 10px 12px;
      margin-bottom: 0;
    }
    .screenshot-symbol-label { font-size: 14px; font-weight: 500; color: var(--text); white-space: nowrap; }
    .screenshot-symbol-combo { position: relative; display: inline-block; overflow: visible; }
    .screenshot-symbol-combo.is-open { z-index: 100; }
    .screenshot-sym-input {
      padding: 8px 12px;
      border-radius: 6px;
      border: 1px solid var(--input-border);
      background: var(--input-bg);
      color: var(--input-text);
      min-width: 200px;
      width: min(260px, 34vw);
      box-sizing: border-box;
      font-size: 14px;
    }
    .screenshot-sym-input:focus {
      outline: none;
      border-color: var(--btn-bg);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2);
    }
    .screenshot-symbol-combo.is-open .screenshot-sym-input {
      border-bottom-left-radius: 0;
      border-bottom-right-radius: 0;
      border-bottom-color: var(--dd-border);
      box-shadow: none;
    }
    .screenshot-symbol-dropdown {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      margin: 0;
      padding: 4px 0;
      list-style: none;
      max-height: 260px;
      overflow-y: auto;
      z-index: 40;
      background: var(--input-bg);
      color: var(--input-text);
      border: 1px solid var(--input-border);
      border-top: none;
      border-radius: 0 0 6px 6px;
      box-shadow: var(--dd-shadow);
    }
    .screenshot-symbol-dropdown li { padding: 9px 12px; font-size: 14px; cursor: pointer; line-height: 1.25; }
    .screenshot-symbol-dropdown li:hover,
    .screenshot-symbol-dropdown li.symbol-dd-active { background: var(--dd-hover); }
    .screenshot-symbol-dropdown .symbol-dd-empty { color: var(--muted); cursor: default; font-size: 13px; }
    .screenshot-symbol-dropdown .symbol-dd-empty:hover { background: transparent; }
    .screenshot-file-input {
      font-size: 14px;
      color: var(--text);
      max-width: min(100%, 360px);
      width: min(100%, 360px);
      padding: 7px 10px;
      border-radius: 8px;
      border: 1px solid var(--input-border);
      background: var(--input-bg);
      cursor: pointer;
    }
    .screenshot-file-input:hover {
      border-color: var(--btn-bg);
    }
    .screenshot-file-input:focus-visible {
      outline: none;
      border-color: var(--btn-bg);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18);
    }
    html[data-theme="dark"] .screenshot-file-input:focus-visible {
      box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.22);
    }
    .screenshot-file-input::file-selector-button {
      margin-right: 10px;
      border: 0;
      border-radius: 6px;
      padding: 7px 12px;
      font-size: 13px;
      font-weight: 600;
      background: var(--btn-bg);
      color: var(--btn-color);
      cursor: pointer;
      transition: filter 0.16s ease;
    }
    .screenshot-file-input::file-selector-button:hover {
      filter: brightness(0.96);
    }
  </style>
</head>
<body>
  <header class="site-topnav">
    <div class="site-topnav-inner">
      <a href="/" class="site-brand">
        <span class="site-brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span class="site-brand-text">SquintDesk</span>
      </a>
      <nav class="site-topnav-nav" aria-label="Main navigation">
        <a href="/" class="site-topnav-link {% if top_nav == 'dashboard' %}is-active{% endif %}">Dashboard</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/scanner" class="site-topnav-link {% if top_nav == 'ind' %}is-active{% endif %}">IND Stocks</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/us/scanner" class="site-topnav-link {% if top_nav == 'us' %}is-active{% endif %}">US Stocks</a>
      </nav>
      <button type="button" class="theme-toggle site-theme-btn" id="theme-toggle" aria-label="Toggle light or dark theme">Dark mode</button>
    </div>
  </header>
  <div class="site-subnav">
    <div class="site-subnav-inner">
      {% set na = nav_active|default('scanner') %}
      <a href="/scanner" class="site-topnav-link {% if na == 'scanner' %}is-active{% endif %}">Scanner</a>
      <a href="/fno-dashboard" class="site-topnav-link {% if na == 'fno' %}is-active{% endif %}">F&amp;O</a>
      <a href="/backtest" class="site-topnav-link {% if na == 'backtest' %}is-active{% endif %}">Backtest</a>
    </div>
  </div>
  <main class="site-main">
  <div class="site-hero">
    <h1>NSE Stock Scanner</h1>
    <p class="lead">Live market snapshot + screener with Entry, Stop Loss, Target 1 and Target 2.</p>
  </div>

  <div class="card">
    <form method="post" enctype="multipart/form-data">
      <input type="hidden" name="action" value="scan" />
      <label for="category">Category:</label>
      <select id="category" name="category">
        {% for c in categories %}
        <option value="{{ c }}" {% if c == selected_category %}selected{% endif %}>{{ c.replace('_', ' ')|title }}</option>
        {% endfor %}
      </select>
      <label for="sentiment_filter">Filter:</label>
      <select id="sentiment_filter" name="sentiment_filter">
        <option value="all" {% if selected_sentiment_filter == "all" %}selected{% endif %}>All</option>
        <option value="bull" {% if selected_sentiment_filter == "bull" %}selected{% endif %}>Bull</option>
        <option value="bear" {% if selected_sentiment_filter == "bear" %}selected{% endif %}>Bear</option>
      </select>
      <button type="submit">Run Scan</button>
    </form>
  </div>

  <div class="card screenshot-analysis-card">
    <h2>Screenshot Deep Analysis</h2>
    <form method="post" enctype="multipart/form-data">
      <input type="hidden" name="action" value="screenshot_analysis" />
      <div class="screenshot-form-row">
        <div class="screenshot-symbol-wrap">
          <label class="screenshot-symbol-label" for="analysis_symbol" id="analysis_symbol-label">Symbol (optional):</label>
          <div class="screenshot-symbol-combo" id="screenshot-symbol-combo">
            <input
              class="screenshot-sym-input"
              type="text"
              id="analysis_symbol"
              name="analysis_symbol"
              value="{{ selected_analysis_symbol }}"
              placeholder="Example: RELIANCE or SBIN"
              autocomplete="off"
              spellcheck="false"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded="false"
              aria-controls="screenshot-symbol-listbox"
              aria-labelledby="analysis_symbol-label"
            />
            <ul class="screenshot-symbol-dropdown" id="screenshot-symbol-listbox" role="listbox" hidden aria-label="Symbol suggestions"></ul>
          </div>
        </div>
        <input type="file" class="screenshot-file-input" name="chart_image" accept="image/*" required />
        <button type="submit">Analyze Screenshot</button>
      </div>
    </form>
    <p class="muted">Tip: leave symbol empty and app will auto-detect from filename/OCR text. You can also type any NSE symbol manually.</p>
  </div>

  {% if warnings %}
  <div class="card">
    <h2>Warnings</h2>
    {% for w in warnings %}
    <div class="warn">{{ w }}</div>
    {% endfor %}
  </div>
  {% endif %}

  {% if universe_table %}
    <div class="card">
      <h2>All Scanned Symbols (Live Snapshot)</h2>
      {{ universe_table | safe }}
    </div>
  {% endif %}

  {% if screenshot_table %}
    <div class="card">
      <h2>Screenshot Analysis Result</h2>
      {{ screenshot_table | safe }}
      {% if screenshot_notes %}
      <h3>Deep Analysis Notes</h3>
      <ul>
      {% for n in screenshot_notes %}
        <li>{{ n }}</li>
      {% endfor %}
      </ul>
      {% endif %}
    </div>
  {% endif %}

  {% if tables %}
    {% for name, table_html in tables.items() %}
    <div class="card">
      <h2>{{ name }}</h2>
      {% if table_html %}
        {{ table_html | safe }}
      {% else %}
        <p class="muted">No candidates matched filters.</p>
      {% endif %}
    </div>
    {% endfor %}
  {% endif %}
  </main>
  <script>
    function initScreenshotSymbolCombo() {
      const input = document.getElementById("analysis_symbol");
      const list = document.getElementById("screenshot-symbol-listbox");
      const combo = document.getElementById("screenshot-symbol-combo");
      if (!input || !list || !combo) return;

      let debounceTimer = null;
      let symFetchController = null;
      let symFetchGeneration = 0;
      let activeIndex = -1;
      let lastResults = [];

      function setOpen(open) {
        combo.classList.toggle("is-open", open);
        list.hidden = !open;
        input.setAttribute("aria-expanded", open ? "true" : "false");
      }

      function clearActive() {
        list.querySelectorAll("li[role='option']").forEach((el) => el.classList.remove("symbol-dd-active"));
        activeIndex = -1;
      }

      function applyActive() {
        const opts = list.querySelectorAll("li[role='option']");
        opts.forEach((el, i) => el.classList.toggle("symbol-dd-active", i === activeIndex));
        if (activeIndex >= 0 && opts[activeIndex]) opts[activeIndex].scrollIntoView({ block: "nearest" });
      }

      function pick(sym) {
        input.value = sym;
        setOpen(false);
        clearActive();
        input.focus();
      }

      function render(items, showEmptyHint) {
        lastResults = items;
        clearActive();
        list.innerHTML = "";
        if (items.length === 0) {
          if (showEmptyHint) {
            const li = document.createElement("li");
            li.className = "symbol-dd-empty";
            li.textContent = "No matches in your watchlist";
            list.appendChild(li);
            setOpen(true);
          } else {
            setOpen(false);
          }
          return;
        }
        items.forEach((sym, i) => {
          const li = document.createElement("li");
          li.setAttribute("role", "option");
          li.id = "shot-sym-opt-" + i;
          li.textContent = sym;
          li.addEventListener("mousedown", (e) => {
            e.preventDefault();
            pick(sym);
          });
          list.appendChild(li);
        });
        setOpen(true);
      }

      let symApiLoads = 0;
      function setComboLoading(on) {
        if (on) {
          symApiLoads += 1;
          combo.classList.add("is-loading");
        } else {
          symApiLoads = Math.max(0, symApiLoads - 1);
          if (symApiLoads === 0) combo.classList.remove("is-loading");
        }
      }

      async function fetchSymbols(q, signal) {
        setComboLoading(true);
        try {
          const res = await fetch("/api/symbols?q=" + encodeURIComponent(q), { signal });
          if (!res.ok) return [];
          const data = await res.json();
          return data.symbols || [];
        } finally {
          setComboLoading(false);
        }
      }

      function scheduleFetch(showEmptyHint) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(async () => {
          const q = input.value.trim();
          if (symFetchController) symFetchController.abort();
          symFetchController = new AbortController();
          const { signal } = symFetchController;
          const myGen = ++symFetchGeneration;
          try {
            const syms = await fetchSymbols(q, signal);
            if (myGen !== symFetchGeneration) return;
            render(syms, showEmptyHint && q.length > 0);
          } catch (err) {
            if (err && err.name === "AbortError") return;
            if (myGen !== symFetchGeneration) return;
            render([], showEmptyHint && q.length > 0);
          }
        }, 90);
      }

      input.addEventListener("input", () => scheduleFetch(true));
      input.addEventListener("focus", () => scheduleFetch(false));

      input.addEventListener("keydown", (e) => {
        const opts = () => list.querySelectorAll("li[role='option']");
        const n = opts().length;
        if (!list.hidden && n > 0) {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            activeIndex = activeIndex < n - 1 ? activeIndex + 1 : 0;
            applyActive();
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            activeIndex = activeIndex > 0 ? activeIndex - 1 : n - 1;
            applyActive();
            return;
          }
          if (e.key === "Enter") {
            if (activeIndex >= 0 && lastResults[activeIndex]) {
              e.preventDefault();
              pick(lastResults[activeIndex]);
            }
            return;
          }
        }
        if (e.key === "Escape") {
          setOpen(false);
          clearActive();
        }
      });

      document.addEventListener("click", (e) => {
        if (!combo.contains(e.target)) {
          setOpen(false);
          clearActive();
        }
      });
    }

""" + NSE_TABLE_PAGER_JS + """

    document.addEventListener("DOMContentLoaded", () => {
      initScreenshotSymbolCombo();
      initSortablePaginatedTables();
    });
  </script>
""" + THEME_SCRIPT + """
</body>
</html>
"""

BACKTEST_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
""" + THEME_HEAD_INIT + """
  <title>Screener backtest (6 months)</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root, html[data-theme="light"] {
      --bt-bg: #f5f6f8;
      --bt-text: #111827;
      --bt-muted: #6b7280;
      --muted: var(--bt-muted);
      --bt-strong: #111827;
      --bt-card: #ffffff;
      --bt-card-border: #e5e7eb;
      --bt-status: #2563eb;
      --bt-btn: #2563eb;
      --bt-btn-color: #fff;
      --bt-nav-pill: #edf2f7;
      --bt-nav-pill-color: #1e40af;
      --bt-input-bg: #fff;
      --bt-input-border: #d1d5db;
      --bt-input-text: #111827;
      --bt-placeholder: #9ca3af;
      --bt-warn-text: #b91c1c;
      --bt-warn-bg: #fef2f2;
      --bt-warn-border: #fecaca;
      --theme-toggle-bg: #e0f2fe;
      --theme-toggle-color: #1d4ed8;
      --bt-dd-hover: #eff6ff;
      --bt-dd-border: #bfdbfe;
      --bt-dd-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
      --bt-nav-bg: #ffffff;
      --bt-nav-border: #eceef2;
      --bt-nav-shadow: 0 1px 0 rgba(15, 23, 42, 0.06);
      --bt-accent-soft: #e8f1ff;
      --bt-card-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 8px 28px rgba(15, 23, 42, 0.07);
      --bt-panel-bg: #f9fafb;
    }
    html[data-theme="dark"] {
      --bt-bg: #0b0e14;
      --bt-text: #e6edf3;
      --bt-muted: #8b949e;
      --muted: var(--bt-muted);
      --bt-strong: #fff;
      --bt-card: #121826;
      --bt-card-border: #21262d;
      --bt-status: #79c0ff;
      --bt-btn: #3b82f6;
      --bt-btn-color: #fff;
      --bt-nav-pill: #2d333b;
      --bt-nav-pill-color: #e6edf3;
      --bt-input-bg: #1c2333;
      --bt-input-border: #30363d;
      --bt-input-text: #e6edf3;
      --bt-placeholder: #6e7681;
      --bt-warn-text: #ffb1b1;
      --bt-warn-bg: #2d1b1b;
      --bt-warn-border: #4a2020;
      --theme-toggle-bg: #1e3a5f;
      --theme-toggle-color: #93c5fd;
      --bt-dd-hover: #2a3447;
      --bt-dd-border: #3d4f6e;
      --bt-dd-shadow: 0 14px 36px rgba(0, 0, 0, 0.55);
      --bt-nav-bg: #161b22;
      --bt-nav-border: #30363d;
      --bt-nav-shadow: none;
      --bt-accent-soft: #1f2937;
      --bt-card-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
      --bt-panel-bg: #1a2332;
    }
    * { box-sizing: border-box; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      background: var(--bt-bg);
      color: var(--bt-text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    .site-topnav {
      background: var(--bt-nav-bg);
      border-bottom: 1px solid var(--bt-nav-border);
      box-shadow: var(--bt-nav-shadow);
      position: sticky;
      top: 0;
      z-index: 300;
    }
    .site-topnav-inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 24px;
      min-height: 56px;
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .site-brand {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: var(--bt-strong);
      font-weight: 600;
      font-size: 1.05rem;
      letter-spacing: -0.02em;
      margin-right: 8px;
    }
    .site-brand-text { text-transform: none; }
    .site-brand-mark {
      width: 22px;
      height: 22px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 3px;
    }
    .site-brand-mark i { display: block; background: var(--bt-strong); border-radius: 2px; font-style: normal; }
    .site-topnav-nav { display: flex; align-items: center; gap: 2px; flex: 1; flex-wrap: wrap; }
    .site-topnav-divider { width: 1px; height: 20px; background: var(--bt-card-border); margin: 0 8px; }
    .site-topnav-link {
      color: var(--bt-muted);
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
      padding: 8px 14px;
      border-radius: 8px;
      transition: background 0.15s, color 0.15s;
    }
    .site-topnav-link:hover { color: var(--bt-btn); background: var(--bt-bg); }
    .site-topnav-link.is-active { color: var(--bt-btn); background: var(--bt-accent-soft); }
    .site-subnav { background: var(--bt-nav-bg); border-bottom: 1px solid var(--bt-nav-border); }
    .site-subnav-inner { max-width: 1200px; margin: 0 auto; padding: 8px 24px; display: flex; gap: 8px; flex-wrap: wrap; }
    .site-theme-btn { margin-left: auto; border: 1px solid rgba(37, 99, 235, 0.22); font-weight: 500; }
    html[data-theme="dark"] .site-theme-btn { border-color: rgba(96, 165, 250, 0.35); }
    .site-main { max-width: 1200px; margin: 0 auto; padding: 28px 24px 56px; }
    .site-hero { margin-bottom: 24px; }
    .site-hero h1 {
      font-size: 1.75rem;
      font-weight: 600;
      letter-spacing: -0.03em;
      margin: 0 0 8px;
      color: var(--bt-strong);
    }
    .site-hero .lead, .muted.lead { color: var(--bt-muted); font-size: 15px; line-height: 1.55; max-width: 65ch; margin: 0; }
    .muted { color: var(--bt-muted); font-size: 14px; line-height: 1.5; }
    .muted strong { color: var(--bt-strong); }
    .row {
      display: flex;
      flex-wrap: nowrap;
      gap: 10px 14px;
      align-items: flex-end;
      margin: 0;
      padding: 4px 0;
      color: var(--bt-text);
      overflow: visible;
      position: relative;
      z-index: 1;
    }
    .row label {
      font-size: 13px;
      font-weight: 600;
      color: var(--bt-text);
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin: 0;
      min-width: 140px;
      flex: 0 0 auto;
    }
    .bt-controls-card {
      background: var(--bt-panel-bg);
      margin-bottom: 16px;
    }
    .card {
      background: var(--bt-card);
      border: 1px solid var(--bt-card-border);
      border-radius: 14px;
      padding: 20px 22px;
      margin-bottom: 20px;
      box-shadow: var(--bt-card-shadow);
    }
    .chart-wrap { position: relative; height: min(72vh, 560px); }
    .theme-toggle {
      background: var(--theme-toggle-bg);
      color: var(--theme-toggle-color);
      padding: 8px 16px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 14px;
    }
    button#run {
      background: var(--bt-btn);
      color: var(--bt-btn-color);
      border: 0;
      padding: 10px 18px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 500;
    }
    a.nav {
      background: var(--bt-nav-pill);
      color: var(--bt-nav-pill-color);
      border: 1px solid var(--bt-card-border);
      padding: 10px 16px;
      border-radius: 8px;
      cursor: pointer;
      text-decoration: none;
      font-size: 14px;
      display: inline-block;
      font-weight: 500;
    }
    select {
      background: var(--bt-input-bg);
      color: var(--bt-input-text);
      border: 1px solid var(--bt-input-border);
      padding: 8px 12px;
      border-radius: 8px;
    }
    .warn { color: var(--bt-warn-text); background: var(--bt-warn-bg); border: 1px solid var(--bt-warn-border); padding: 10px 14px; border-radius: 8px; margin: 0 0 12px; white-space: pre-wrap; }
""" + NSE_LOADING_CSS + """
    #status { margin: 0 0 8px; color: var(--bt-status); font-size: 14px; min-height: 1.25em; }
    #warnings { margin-bottom: 12px; }
    .sym-input {
      background: var(--bt-input-bg);
      color: var(--bt-input-text);
      border: 1px solid var(--bt-input-border);
      padding: 8px 12px;
      border-radius: 8px;
      min-width: 220px;
      font-size: 14px;
    }
    .sym-input::placeholder { color: var(--bt-placeholder); }
    .sym-input:focus {
      outline: none;
      border-color: var(--bt-btn);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2);
    }
    .symbol-search-wrap {
      display: flex;
      flex-wrap: nowrap;
      align-items: flex-end;
      gap: 8px;
      margin-left: 2px;
      flex: 1 1 auto;
      min-width: 280px;
    }
    .symbol-search-label {
      font-size: 13px;
      font-weight: 600;
      color: var(--bt-text);
      white-space: nowrap;
      margin-bottom: 8px;
    }
    .symbol-combo { position: relative; display: inline-block; vertical-align: middle; overflow: visible; }
    .symbol-combo.is-open { z-index: 200; }
    .symbol-combo .sym-input {
      min-width: 240px;
      width: 100%;
      border-radius: 6px;
      box-sizing: border-box;
    }
    #run {
      white-space: nowrap;
      margin-left: auto;
      align-self: flex-end;
      min-height: 40px;
      padding: 10px 18px;
    }
    @media (max-width: 980px) {
      .row { flex-wrap: wrap; }
      #run { margin-left: 0; }
    }
    .symbol-combo.is-open .sym-input {
      border-bottom-left-radius: 0;
      border-bottom-right-radius: 0;
      border-bottom-color: var(--bt-dd-border);
      box-shadow: none;
    }
    .symbol-dropdown {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      margin: 0;
      padding: 4px 0;
      list-style: none;
      max-height: 260px;
      overflow-y: auto;
      z-index: 40;
      background: var(--bt-input-bg);
      color: var(--bt-input-text);
      border: 1px solid var(--bt-input-border);
      border-top: none;
      border-radius: 0 0 6px 6px;
      box-shadow: var(--bt-dd-shadow);
    }
    .symbol-dropdown li {
      padding: 9px 12px;
      font-size: 14px;
      cursor: pointer;
      line-height: 1.25;
    }
    .symbol-dropdown li:hover,
    .symbol-dropdown li.symbol-dd-active {
      background: var(--bt-dd-hover);
    }
    .symbol-dropdown .symbol-dd-empty {
      color: var(--bt-muted);
      cursor: default;
      font-size: 13px;
    }
    .symbol-dropdown .symbol-dd-empty:hover { background: transparent; }
  </style>
</head>
<body>
  <header class="site-topnav">
    <div class="site-topnav-inner">
      <a href="/" class="site-brand">
        <span class="site-brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span class="site-brand-text">SquintDesk</span>
      </a>
      <nav class="site-topnav-nav" aria-label="Main navigation">
        <a href="/" class="site-topnav-link {% if top_nav == 'dashboard' %}is-active{% endif %}">Dashboard</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/scanner" class="site-topnav-link {% if top_nav == 'ind' %}is-active{% endif %}">IND Stocks</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/us/scanner" class="site-topnav-link {% if top_nav == 'us' %}is-active{% endif %}">US Stocks</a>
      </nav>
      <button type="button" class="theme-toggle site-theme-btn" id="theme-toggle" aria-label="Toggle light or dark theme">Dark mode</button>
    </div>
  </header>
  <div class="site-subnav">
    <div class="site-subnav-inner">
      {% set na = nav_active|default('backtest') %}
      <a href="/scanner" class="site-topnav-link {% if na == 'scanner' %}is-active{% endif %}">Scanner</a>
      <a href="/fno-dashboard" class="site-topnav-link {% if na == 'fno' %}is-active{% endif %}">F&amp;O</a>
      <a href="/backtest" class="site-topnav-link {% if na == 'backtest' %}is-active{% endif %}">Backtest</a>
    </div>
  </div>
  <main class="site-main">
  <div class="site-hero">
    <h1>Strategy backtest · last 6 months</h1>
    <p class="muted lead">
      Each bar is one trading day. Stacks count <strong>distinct symbols</strong> that matched your screener that day,
      split so categories do not double-count (one symbol = one stack segment). Data: Yahoo Finance daily (EOD).
      Optional symbol filters to a <strong>single stock</strong> (universe and max setting are then ignored).
    </p>
  </div>

  <div class="card">
    <h2>How To Read This Chart</h2>
    <p class="muted" style="margin:0;">
      Use <strong>Signal count mode</strong> to see how many stocks triggered your rules each day (opportunity flow).
      Use <strong>P&amp;L mode</strong> to see a simplified equity curve (base 100) from historical bullish signals with
      fixed stop/target/max-hold assumptions.
    </p>
  </div>

  <div class="card bt-controls-card">
  <div class="row">
    <label>Universe:
      <select id="universe">
        <option value="fno" selected>F&amp;O list (recommended)</option>
        <option value="all">Full watchlist (capped)</option>
      </select>
    </label>
    <label>Max symbols:
      <select id="maxsym">
        <option value="60">60</option>
        <option value="120" selected>120</option>
        <option value="180">180</option>
      </select>
    </label>
    <label>Mode:
      <select id="btmode">
        <option value="count" selected>Signal count</option>
        <option value="pnl">P&amp;L backtest</option>
      </select>
    </label>
    <div class="symbol-search-wrap">
      <span class="symbol-search-label" id="onesymbol-label">Symbol (optional):</span>
      <div class="symbol-combo" id="symbol-combo">
        <input
          class="sym-input"
          type="text"
          id="onesymbol"
          placeholder="Example: RELIANCE or SBIN"
          autocomplete="off"
          spellcheck="false"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded="false"
          aria-controls="symbol-listbox"
          aria-labelledby="onesymbol-label"
        />
        <ul class="symbol-dropdown" id="symbol-listbox" role="listbox" hidden aria-label="Symbol suggestions"></ul>
      </div>
    </div>
    <button type="button" id="run">Run backtest</button>
  </div>
  </div>
  <div id="status"></div>
  <div id="warnings"></div>
  <div class="card" id="bt-results-card" style="display:none;">
    <div class="chart-wrap">
      <canvas id="btchart"></canvas>
    </div>
  </div>
  </main>

  <script>
    let chartInstance = null;

    function initSymbolCombo() {
      const input = document.getElementById("onesymbol");
      const list = document.getElementById("symbol-listbox");
      const combo = document.getElementById("symbol-combo");
      if (!input || !list || !combo) return;

      let debounceTimer = null;
      let symFetchController = null;
      let symFetchGeneration = 0;
      let activeIndex = -1;
      let lastResults = [];

      function setOpen(open) {
        combo.classList.toggle("is-open", open);
        list.hidden = !open;
        input.setAttribute("aria-expanded", open ? "true" : "false");
      }

      function clearActive() {
        list.querySelectorAll("li[role='option']").forEach((el) => el.classList.remove("symbol-dd-active"));
        activeIndex = -1;
      }

      function applyActive() {
        const opts = list.querySelectorAll("li[role='option']");
        opts.forEach((el, i) => el.classList.toggle("symbol-dd-active", i === activeIndex));
        if (activeIndex >= 0 && opts[activeIndex]) {
          opts[activeIndex].scrollIntoView({ block: "nearest" });
        }
      }

      function pick(sym) {
        input.value = sym;
        setOpen(false);
        clearActive();
        input.focus();
      }

      function render(items, showEmptyHint) {
        lastResults = items;
        clearActive();
        list.innerHTML = "";
        if (items.length === 0) {
          if (showEmptyHint) {
            const li = document.createElement("li");
            li.className = "symbol-dd-empty";
            li.textContent = "No matches in your watchlist";
            list.appendChild(li);
            setOpen(true);
          } else {
            setOpen(false);
          }
          return;
        }
        items.forEach((sym, i) => {
          const li = document.createElement("li");
          li.setAttribute("role", "option");
          li.id = "sym-opt-" + i;
          li.textContent = sym;
          li.addEventListener("mousedown", (e) => {
            e.preventDefault();
            pick(sym);
          });
          list.appendChild(li);
        });
        setOpen(true);
      }

      let symApiLoads = 0;
      function setComboLoading(on) {
        if (on) {
          symApiLoads += 1;
          combo.classList.add("is-loading");
        } else {
          symApiLoads = Math.max(0, symApiLoads - 1);
          if (symApiLoads === 0) combo.classList.remove("is-loading");
        }
      }

      async function fetchSymbols(q, signal) {
        setComboLoading(true);
        try {
          const res = await fetch("/api/symbols?q=" + encodeURIComponent(q), { signal });
          if (!res.ok) return [];
          const data = await res.json();
          return data.symbols || [];
        } finally {
          setComboLoading(false);
        }
      }

      function scheduleFetch(showEmptyHint) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(async () => {
          const q = input.value.trim();
          if (symFetchController) symFetchController.abort();
          symFetchController = new AbortController();
          const { signal } = symFetchController;
          const myGen = ++symFetchGeneration;
          try {
            const syms = await fetchSymbols(q, signal);
            if (myGen !== symFetchGeneration) return;
            render(syms, showEmptyHint && q.length > 0);
          } catch (err) {
            if (err && err.name === "AbortError") return;
            if (myGen !== symFetchGeneration) return;
            render([], showEmptyHint && q.length > 0);
          }
        }, 90);
      }

      input.addEventListener("input", () => scheduleFetch(true));
      input.addEventListener("focus", () => scheduleFetch(false));

      input.addEventListener("keydown", (e) => {
        const opts = () => list.querySelectorAll("li[role='option']");
        const n = opts().length;
        if (!list.hidden && n > 0) {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            activeIndex = activeIndex < n - 1 ? activeIndex + 1 : 0;
            applyActive();
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            activeIndex = activeIndex > 0 ? activeIndex - 1 : n - 1;
            applyActive();
            return;
          }
          if (e.key === "Enter") {
            if (activeIndex >= 0 && lastResults[activeIndex]) {
              e.preventDefault();
              pick(lastResults[activeIndex]);
            }
            return;
          }
        }
        if (e.key === "Escape") {
          setOpen(false);
          clearActive();
        }
      });

      document.addEventListener("click", (e) => {
        if (!combo.contains(e.target)) {
          setOpen(false);
          clearActive();
        }
      });
    }

    function backtestChartPalette() {
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      return {
        legend: dark ? "#c9d1d9" : "#374151",
        tick: dark ? "#8b949e" : "#4b5563",
        grid: dark ? "rgba(48, 54, 61, 0.6)" : "rgba(0, 0, 0, 0.08)",
        tooltipBg: dark ? "#1c2333" : "#ffffff",
        tooltipTitle: dark ? "#e6edf3" : "#111827",
        tooltipBody: dark ? "#c9d1d9" : "#4b5563",
        tooltipBorder: dark ? "#30363d" : "#d1d5db",
      };
    }

    function applyChartTheme(chart) {
      if (!chart || !chart.options) return;
      const c = backtestChartPalette();
      chart.options.plugins.legend.labels.color = c.legend;
      chart.options.plugins.tooltip.backgroundColor = c.tooltipBg;
      chart.options.plugins.tooltip.titleColor = c.tooltipTitle;
      chart.options.plugins.tooltip.bodyColor = c.tooltipBody;
      chart.options.plugins.tooltip.borderColor = c.tooltipBorder;
      chart.options.scales.x.ticks.color = c.tick;
      chart.options.scales.x.grid.color = c.grid;
      chart.options.scales.y.ticks.color = c.tick;
      chart.options.scales.y.grid.color = c.grid;
      chart.update();
    }

    window.onNseThemeChange = function () {
      applyChartTheme(chartInstance);
    };

    function statusLoading(msg) {
      const status = document.getElementById("status");
      status.innerHTML =
        '<span class="nse-loading-inline" role="status" aria-live="polite">' +
        '<span class="nse-spinner" aria-hidden="true"></span>' +
        "<span>" +
        msg +
        "</span></span>";
    }

    async function runBacktest() {
      const universe = document.getElementById("universe").value;
      const maxsym = document.getElementById("maxsym").value;
      const mode = document.getElementById("btmode").value;
      const oneSym = document.getElementById("onesymbol").value.trim();
      const symQs = oneSym ? `&symbol=${encodeURIComponent(oneSym)}` : "";
      const status = document.getElementById("status");
      const warningsEl = document.getElementById("warnings");
      const runBtn = document.getElementById("run");
      const resultsCard = document.getElementById("bt-results-card");
      warningsEl.innerHTML = "";
      if (runBtn) runBtn.disabled = true;
      if (resultsCard) resultsCard.style.display = "none";
      statusLoading("Loading history from Yahoo (may take a minute)…");
      try {
        const res = await fetch(`/api/backtest?mode=${encodeURIComponent(mode)}&universe=${encodeURIComponent(universe)}&max=${encodeURIComponent(maxsym)}${symQs}`);
        const payload = await res.json();
        if (!res.ok) {
          throw new Error(payload.error || res.statusText);
        }
        const { labels, datasets, meta } = payload;
        if (meta && meta.disclaimer) {
          const symNote = meta.symbol_filter ? `Single symbol: ${meta.symbol_filter} · ` : "";
          if ((meta.mode || mode) === "pnl") {
            status.textContent = `${symNote}${meta.symbol_count} symbols · trades: ${meta.trades || 0} · win rate: ${meta.win_rate_pct || 0}% · return: ${meta.cumulative_return_pct || 0}% · max DD: ${meta.max_drawdown_pct || 0}%`;
          } else {
            status.textContent = `${symNote}${meta.symbol_count} symbols · ${meta.period} · ${meta.disclaimer}`;
          }
        } else {
          status.textContent = "Done.";
        }
        if (payload.warnings && payload.warnings.length) {
          const div = document.createElement("div");
          div.className = "warn";
          div.textContent = payload.warnings.join("\\n");
          warningsEl.appendChild(div);
        }
        if (!labels || labels.length === 0) {
          if (chartInstance) chartInstance.destroy();
          chartInstance = null;
          status.textContent = "No chart data (check config symbols and warnings above).";
          if (resultsCard) resultsCard.style.display = "none";
          return;
        }
        const ctx = document.getElementById("btchart").getContext("2d");
        if (chartInstance) chartInstance.destroy();
        const pal = backtestChartPalette();
        const isPnl = (meta && meta.mode === "pnl") || mode === "pnl";
        chartInstance = new Chart(ctx, {
          type: isPnl ? "line" : "bar",
          data: { labels, datasets },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index" },
            plugins: {
              legend: {
                position: "top",
                labels: { color: pal.legend, boxWidth: 14, font: { size: 11 } },
              },
              tooltip: {
                backgroundColor: pal.tooltipBg,
                titleColor: pal.tooltipTitle,
                bodyColor: pal.tooltipBody,
                borderColor: pal.tooltipBorder,
                borderWidth: 1,
              },
            },
            scales: {
              x: {
                stacked: !isPnl,
                ticks: { color: pal.tick, maxRotation: 0, maxTicksLimit: 20 },
                grid: { color: pal.grid },
              },
              y: {
                stacked: !isPnl,
                beginAtZero: true,
                ticks: { color: pal.tick },
                grid: { color: pal.grid },
              },
            },
          },
        });
        if (resultsCard) resultsCard.style.display = "";
      } catch (e) {
        status.textContent = "";
        warningsEl.innerHTML = `<div class="warn">${e.message}</div>`;
        if (resultsCard) resultsCard.style.display = "none";
      } finally {
        if (runBtn) runBtn.disabled = false;
      }
    }

    document.getElementById("run").addEventListener("click", runBacktest);
    document.addEventListener("DOMContentLoaded", () => {
      initSymbolCombo();
    });
  </script>
""" + THEME_SCRIPT + """
</body>
</html>
"""

US_MARKET_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
""" + THEME_HEAD_INIT + """
  <title>SquintDesk · US Stocks</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root, html[data-theme="light"] {
      --page-bg: #f5f6f8;
      --text: #111827;
      --muted: #6b7280;
      --card-bg: #ffffff;
      --card-border: #e5e7eb;
      --table-border: #e5e7eb;
      --th-bg: #f9fafb;
      --btn-bg: #2563eb;
      --btn-color: #fff;
      --select-border: #d1d5db;
      --input-bg: #fff;
      --input-text: #111827;
      --link: #2563eb;
      --warn-text: #b91c1c;
      --warn-bg: #fef2f2;
      --warn-border: #fecaca;
      --theme-toggle-bg: #e0f2fe;
      --theme-toggle-color: #1d4ed8;
      --nav-bg: #ffffff;
      --nav-border: #eceef2;
      --nav-shadow: 0 1px 0 rgba(15, 23, 42, 0.06);
      --accent-soft: #e8f1ff;
      --card-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 8px 28px rgba(15, 23, 42, 0.07);
    }
    html[data-theme="dark"] {
      --page-bg: #0f1419;
      --text: #e6edf3;
      --muted: #8b949e;
      --card-bg: #1a2332;
      --card-border: #30363d;
      --table-border: #3d4a5c;
      --th-bg: #252f3f;
      --btn-bg: #3b82f6;
      --btn-color: #fff;
      --select-border: #4a5568;
      --input-bg: #0d1117;
      --input-text: #e6edf3;
      --link: #60a5fa;
      --warn-text: #ffb1b1;
      --warn-bg: #2d1b1b;
      --warn-border: #4a2020;
      --theme-toggle-bg: #1e3a5f;
      --theme-toggle-color: #93c5fd;
      --nav-bg: #161b22;
      --nav-border: #30363d;
      --nav-shadow: none;
      --accent-soft: #1f2937;
      --card-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
    }
    * { box-sizing: border-box; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      background: var(--page-bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    .site-topnav {
      background: var(--nav-bg);
      border-bottom: 1px solid var(--nav-border);
      box-shadow: var(--nav-shadow);
      position: sticky;
      top: 0;
      z-index: 300;
    }
    .site-topnav-inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 24px;
      min-height: 56px;
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .site-brand {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: var(--text);
      font-weight: 600;
      font-size: 1.05rem;
      letter-spacing: -0.02em;
      margin-right: 8px;
    }
    .site-brand-text { text-transform: none; }
    .site-brand-mark {
      width: 22px;
      height: 22px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 3px;
    }
    .site-brand-mark i {
      display: block;
      background: var(--text);
      border-radius: 2px;
      font-style: normal;
    }
    .site-topnav-nav { display: flex; align-items: center; gap: 2px; flex: 1; flex-wrap: wrap; }
    .site-topnav-divider { width: 1px; height: 20px; background: var(--card-border); margin: 0 8px; }
    .site-topnav-link {
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
      padding: 8px 14px;
      border-radius: 8px;
      transition: background 0.15s, color 0.15s;
    }
    .site-topnav-link:hover { color: var(--link); background: var(--page-bg); }
    .site-topnav-link.is-active { color: var(--link); background: var(--accent-soft); }
    .site-subnav { background: var(--nav-bg); border-bottom: 1px solid var(--nav-border); }
    .site-subnav-inner { max-width: 1200px; margin: 0 auto; padding: 8px 24px; display: flex; gap: 8px; flex-wrap: wrap; }
    .site-theme-btn {
      margin-left: auto;
      border: 1px solid rgba(37, 99, 235, 0.22);
      font-weight: 500;
    }
    html[data-theme="dark"] .site-theme-btn { border-color: rgba(96, 165, 250, 0.35); }
    .site-main { max-width: 1200px; margin: 0 auto; padding: 28px 24px 56px; }
    .site-hero { margin-bottom: 28px; }
    .site-hero h1 {
      font-size: 1.75rem;
      font-weight: 600;
      letter-spacing: -0.03em;
      margin: 0 0 8px;
      color: var(--text);
    }
    .site-hero .lead { font-size: 15px; line-height: 1.55; max-width: 56ch; margin: 0; color: var(--muted); }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      box-shadow: var(--card-shadow);
      padding: 16px;
      margin-top: 16px;
    }
    .card h2 { margin: 0 0 10px 0; font-size: 16px; }
    .row { display: flex; align-items: flex-end; gap: 10px 12px; flex-wrap: wrap; }
    label { display: inline-flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; }
    select, input, button {
      border: 1px solid var(--select-border);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 14px;
      background: var(--input-bg);
      color: var(--input-text);
    }
    .theme-toggle {
      background: var(--theme-toggle-bg);
      color: var(--theme-toggle-color);
      border: 1px solid rgba(37, 99, 235, 0.22);
      font-weight: 500;
      cursor: pointer;
      padding: 8px 14px;
    }
    button:not(.theme-toggle) { background: var(--btn-bg); border-color: var(--btn-bg); color: var(--btn-color); font-weight: 600; cursor: pointer; }
    .warn { color: var(--warn-text); background: var(--warn-bg); border: 1px solid var(--warn-border); border-radius: 10px; padding: 10px 12px; margin-top: 12px; font-size: 13px; }
    .muted { color: var(--muted); }
    table.results { width: 100%; border-collapse: collapse; font-size: 13px; border: 1px solid var(--table-border); border-radius: 10px; overflow: hidden; }
    table.results th, table.results td { border: 1px solid var(--table-border); padding: 8px 10px; text-align: left; white-space: nowrap; }
    table.results th { background: var(--th-bg); }
    .nse-no-record { color: var(--warn-text); text-align: center; font-weight: 600; padding: 18px 10px; }
    .screenshot-file-input {
      color: var(--text);
      max-width: min(100%, 360px);
      width: min(100%, 360px);
      padding: 7px 10px;
      border-radius: 8px;
      border: 1px solid var(--select-border);
      background: var(--input-bg);
      cursor: pointer;
    }
    .screenshot-file-input:hover { border-color: var(--btn-bg); }
    .screenshot-file-input::file-selector-button {
      margin-right: 10px;
      border: 0;
      border-radius: 6px;
      padding: 7px 12px;
      font-size: 13px;
      font-weight: 600;
      background: var(--btn-bg);
      color: var(--btn-color);
      cursor: pointer;
    }
    .screenshot-symbol-wrap { display: inline-flex; flex-direction: column; gap: 6px; min-width: 220px; }
    .screenshot-symbol-label { font-size: 14px; font-weight: 600; color: var(--text); line-height: 1.2; }
    .screenshot-symbol-combo { position: relative; display: inline-block; overflow: visible; }
    .screenshot-symbol-combo.is-open { z-index: 180; }
    .screenshot-symbol-combo .sym-input {
      width: min(280px, 56vw);
      min-width: 180px;
      border: 1px solid var(--select-border);
      border-radius: 10px;
      padding: 8px 10px;
      background: var(--input-bg);
      color: var(--input-text);
      font-size: 14px;
      line-height: 1.25;
      outline: none;
    }
    .screenshot-symbol-combo.is-open .sym-input { border-color: #93c5fd; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.16); }
""" + NSE_LOADING_CSS + """
""" + NSE_TABLE_PAGER_CSS + """
    .chart-wrap { position: relative; height: min(72vh, 560px); margin-top: 10px; }
    .symbol-search-wrap {
      display: inline-flex;
      flex-direction: column;
      gap: 6px;
      min-width: 200px;
      align-self: flex-end;
      margin-bottom: 0;
    }
    .symbol-search-label {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      line-height: 1.2;
    }
    .symbol-combo { position: relative; display: inline-block; vertical-align: middle; overflow: visible; }
    .symbol-combo.is-open { z-index: 200; }
    .symbol-combo .sym-input {
      width: min(260px, 52vw);
      min-width: 180px;
      border: 1px solid var(--select-border);
      border-radius: 10px;
      padding: 8px 10px;
      background: var(--input-bg);
      color: var(--input-text);
      font-size: 14px;
      line-height: 1.25;
      outline: none;
    }
    .symbol-combo .sym-input::placeholder { color: var(--muted); }
    .symbol-combo.is-open .sym-input { border-color: #93c5fd; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.16); }
    .symbol-dropdown {
      position: absolute;
      top: calc(100% + 6px);
      left: 0;
      right: 0;
      background: var(--card-bg);
      border: 1px solid var(--table-border);
      border-radius: 10px;
      box-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
      max-height: 260px;
      overflow: auto;
      list-style: none;
      margin: 0;
      padding: 4px 0;
    }
    .symbol-dropdown li { padding: 9px 12px; font-size: 14px; cursor: pointer; line-height: 1.25; }
    .symbol-dropdown li:hover,
    .symbol-dropdown li.symbol-dd-active { background: var(--accent-soft); }
    .symbol-dropdown .symbol-dd-empty { color: var(--muted); cursor: default; font-size: 13px; }
    .symbol-dropdown .symbol-dd-empty:hover { background: transparent; }
  </style>
</head>
<body>
  <header class="site-topnav">
    <div class="site-topnav-inner">
      <a href="/us/scanner" class="site-brand">
        <span class="site-brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span class="site-brand-text">SquintDesk</span>
      </a>
      <nav class="site-topnav-nav" aria-label="Main navigation">
        <a href="/" class="site-topnav-link {% if top_nav == 'dashboard' %}is-active{% endif %}">Dashboard</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/scanner" class="site-topnav-link {% if top_nav == 'ind' %}is-active{% endif %}">IND Stocks</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/us/scanner" class="site-topnav-link {% if top_nav == 'us' %}is-active{% endif %}">US Stocks</a>
      </nav>
      <button type="button" class="theme-toggle site-theme-btn" id="theme-toggle" aria-label="Toggle light or dark theme">Dark mode</button>
    </div>
  </header>
  <div class="site-subnav">
    <div class="site-subnav-inner">
      <a href="/us/scanner" class="site-topnav-link {% if tab == 'scanner' %}is-active{% endif %}">Scanner</a>
      <a href="/us/fno" class="site-topnav-link {% if tab == 'fno' %}is-active{% endif %}">F&amp;O</a>
      <a href="/us/backtest" class="site-topnav-link {% if tab == 'backtest' %}is-active{% endif %}">Backtest</a>
    </div>
  </div>
  <main class="site-main">
  {% if tab == 'scanner' %}
    <div class="site-hero">
      <h1>US Stock Scanner</h1>
      <p class="lead">Live market snapshot + screener with the same filter behavior as IND scanner.</p>
    </div>
    <div class="card">
      <div class="row">
        <label for="category">Category:
          <select id="category">
            <option value="mega">US Mega caps</option>
            <option value="tech">US Tech</option>
          </select>
        </label>
        <label for="sentiment_filter">Filter:
          <select id="sentiment_filter">
            <option value="all">All</option>
            <option value="bull">Bull</option>
            <option value="bear">Bear</option>
          </select>
        </label>
        <button id="run">Run Scan</button>
      </div>
    </div>
    <div class="card">
      <h2>All Scanned Symbols (Live Snapshot)</h2>
      <div id="table-host"><p class="muted">Click Run Scan.</p></div>
    </div>
    <div class="card screenshot-analysis-card">
      <h2>Screenshot Deep Analysis</h2>
      <div class="row">
        <div class="screenshot-symbol-wrap">
          <span class="screenshot-symbol-label" id="analysis_symbol-label">Symbol (optional):</span>
          <div class="screenshot-symbol-combo" id="screenshot-symbol-combo">
            <input type="text" class="sym-input" id="analysis_symbol" placeholder="Example: AAPL or MSFT" autocomplete="off" spellcheck="false" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="us-shot-symbol-listbox" aria-labelledby="analysis_symbol-label" />
            <ul class="symbol-dropdown" id="us-shot-symbol-listbox" role="listbox" hidden aria-label="US symbol suggestions"></ul>
          </div>
        </div>
        <input type="file" class="screenshot-file-input" id="chart_image" accept="image/*" />
        <button id="analyze-shot">Analyze Screenshot</button>
      </div>
      <p class="muted">Tip: leave symbol empty and app will auto-detect from filename/OCR text. You can also type any US symbol manually.</p>
      <div id="shot-warn-host"></div>
      <div id="shot-table-host"></div>
      <div id="shot-notes-host"></div>
    </div>
  {% elif tab == 'fno' %}
    <div class="card">
      <div class="row">
        <label for="sentiment_filter">Filter:
          <select id="sentiment_filter">
            <option value="choose">Choose</option>
            <option value="all">All</option>
            <option value="bull">Bull</option>
            <option value="bear">Bear</option>
          </select>
        </label>
        <label for="universe">Category:
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
    <div class="card" id="fno-picks-card" style="display:none;"><h2>US Picks</h2><div id="fno-host"></div></div>
    <div class="card" id="fno-universe-card" style="display:none;"><h2>US Universe Snapshot</h2><div id="uni-host"></div></div>
    <div class="card" id="fno-bear-card" style="display:none;"><h2>US Bear Watchlist</h2><div id="bear-host"></div></div>
  {% else %}
    <div class="card"><h2>How To Read This Chart</h2><p class="muted" style="margin:0;">Signal count shows daily setup flow. P&amp;L mode shows simplified equity curve (base 100).</p></div>
    <div class="card"><div class="row"><label>Universe<select id="universe"><option value="mega">US Mega caps</option><option value="tech">US Tech</option></select></label><label>Max symbols<select id="maxsym"><option value="60">60</option><option value="120" selected>120</option></select></label><label>Mode<select id="btmode"><option value="count">Signal count</option><option value="pnl">P&amp;L backtest</option></select></label><div class="symbol-search-wrap"><span class="symbol-search-label" id="onesymbol-label">Symbol (optional)</span><div class="symbol-combo" id="us-symbol-combo"><input class="sym-input" id="onesymbol" placeholder="Example: AAPL" autocomplete="off" spellcheck="false" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="us-symbol-listbox" aria-labelledby="onesymbol-label" /><ul class="symbol-dropdown" id="us-symbol-listbox" role="listbox" hidden aria-label="US symbol suggestions"></ul></div></div><button id="run">Run backtest</button></div></div>
    <div id="status" class="muted"></div><div id="warnings"></div>
    <div class="card" id="bt-results-card" style="display:none;"><div class="chart-wrap"><canvas id="btchart"></canvas></div></div>
  {% endif %}
  </main>
  {% if tab == 'scanner' %}
  <script>
    async function runScan() {
      const category = document.getElementById("category").value;
      const sentiment = document.getElementById("sentiment_filter").value;
      const host = document.getElementById("table-host");
      host.innerHTML = '<div class="nse-loading-block"><span class="nse-spinner" aria-hidden="true"></span><span>Fetching US data...</span></div>';
      try {
        const url = "/api/us/scanner?category=" + encodeURIComponent(category) + "&sentiment_filter=" + encodeURIComponent(sentiment);
        const res = await fetch(url);
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || res.statusText);
        host.innerHTML = payload.table_html || '<p class="nse-no-record">No record found for selected filter.</p>';
        if (payload.warnings && payload.warnings.length) {
          host.insertAdjacentHTML("afterbegin", '<div class="warn">' + payload.warnings.join(" | ") + "</div>");
        }
        initSortablePaginatedTables();
      } catch (e) {
        host.innerHTML = '<p class="warn">' + (e.message || String(e)) + "</p>";
      }
    }
    document.getElementById("run").addEventListener("click", runScan);
    async function runScreenshotAnalysis() {
      const symbol = (document.getElementById("analysis_symbol").value || "").trim();
      const fileInput = document.getElementById("chart_image");
      const warnHost = document.getElementById("shot-warn-host");
      const tableHost = document.getElementById("shot-table-host");
      const notesHost = document.getElementById("shot-notes-host");
      const category = document.getElementById("category").value;
      warnHost.innerHTML = "";
      notesHost.innerHTML = "";
      if (!fileInput.files || !fileInput.files.length) {
        warnHost.innerHTML = '<div class="warn">Please upload a screenshot image.</div>';
        return;
      }
      tableHost.innerHTML = '<div class="nse-loading-block"><span class="nse-spinner" aria-hidden="true"></span><span>Analyzing screenshot...</span></div>';
      const fd = new FormData();
      fd.append("analysis_symbol", symbol);
      fd.append("universe", category);
      fd.append("chart_image", fileInput.files[0]);
      try {
        const res = await fetch("/api/us/screenshot-analysis", { method: "POST", body: fd });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || res.statusText);
        if (payload.warnings && payload.warnings.length) {
          warnHost.innerHTML = payload.warnings.map(function (w) { return '<div class="warn">' + w + "</div>"; }).join("");
        }
        if (payload.detected_symbol) {
          document.getElementById("analysis_symbol").value = payload.detected_symbol;
        }
        tableHost.innerHTML = payload.table_html || '<p class="nse-no-record">No record found.</p>';
        if (payload.notes && payload.notes.length) {
          notesHost.innerHTML = "<h3>Deep Analysis Notes</h3><ul>" + payload.notes.map(function (n) { return "<li>" + n + "</li>"; }).join("") + "</ul>";
        }
      } catch (e) {
        tableHost.innerHTML = '<div class="warn">' + (e.message || String(e)) + "</div>";
      }
    }
    function initUsScreenshotSymbolCombo() {
      const input = document.getElementById("analysis_symbol");
      const list = document.getElementById("us-shot-symbol-listbox");
      const combo = document.getElementById("screenshot-symbol-combo");
      if (!input || !list || !combo) return;
      let debounceTimer = null;
      let fetchController = null;
      let fetchGeneration = 0;
      let activeIndex = -1;
      let lastResults = [];
      function setOpen(open) {
        combo.classList.toggle("is-open", open);
        list.hidden = !open;
        input.setAttribute("aria-expanded", open ? "true" : "false");
      }
      function clearActive() {
        list.querySelectorAll("li[role='option']").forEach((el) => el.classList.remove("symbol-dd-active"));
        activeIndex = -1;
      }
      function applyActive() {
        const opts = list.querySelectorAll("li[role='option']");
        opts.forEach((el, i) => el.classList.toggle("symbol-dd-active", i === activeIndex));
        if (activeIndex >= 0 && opts[activeIndex]) opts[activeIndex].scrollIntoView({ block: "nearest" });
      }
      function pick(sym) {
        input.value = sym;
        setOpen(false);
        clearActive();
        input.focus();
      }
      function render(items, showEmptyHint) {
        lastResults = items;
        clearActive();
        list.innerHTML = "";
        if (items.length === 0) {
          if (showEmptyHint) {
            const li = document.createElement("li");
            li.className = "symbol-dd-empty";
            li.textContent = "No matching symbols";
            list.appendChild(li);
            setOpen(true);
          } else {
            setOpen(false);
          }
          return;
        }
        items.forEach((sym, i) => {
          const li = document.createElement("li");
          li.setAttribute("role", "option");
          li.id = "us-shot-sym-opt-" + i;
          li.textContent = sym;
          li.addEventListener("mousedown", (e) => {
            e.preventDefault();
            pick(sym);
          });
          list.appendChild(li);
        });
        setOpen(true);
      }
      function scheduleFetch(showEmptyHint) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(async () => {
          const q = input.value.trim();
          if (fetchController) fetchController.abort();
          fetchController = new AbortController();
          const { signal } = fetchController;
          const myGen = ++fetchGeneration;
          try {
            const res = await fetch("/api/us/symbols?q=" + encodeURIComponent(q), { signal });
            if (!res.ok) throw new Error("Failed to load symbols");
            const data = await res.json();
            if (myGen !== fetchGeneration) return;
            render(data.symbols || [], showEmptyHint && q.length > 0);
          } catch (err) {
            if (err && err.name === "AbortError") return;
            if (myGen !== fetchGeneration) return;
            render([], showEmptyHint && q.length > 0);
          }
        }, 90);
      }
      input.addEventListener("input", () => scheduleFetch(true));
      input.addEventListener("focus", () => scheduleFetch(false));
      input.addEventListener("keydown", (e) => {
        const opts = () => list.querySelectorAll("li[role='option']");
        const n = opts().length;
        if (!list.hidden && n > 0) {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            activeIndex = activeIndex < n - 1 ? activeIndex + 1 : 0;
            applyActive();
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            activeIndex = activeIndex > 0 ? activeIndex - 1 : n - 1;
            applyActive();
            return;
          }
          if (e.key === "Enter" && activeIndex >= 0 && lastResults[activeIndex]) {
            e.preventDefault();
            pick(lastResults[activeIndex]);
            return;
          }
        }
        if (e.key === "Escape") {
          setOpen(false);
          clearActive();
        }
      });
      document.addEventListener("click", (e) => {
        if (!combo.contains(e.target)) {
          setOpen(false);
          clearActive();
        }
      });
    }
    initUsScreenshotSymbolCombo();
    document.getElementById("analyze-shot").addEventListener("click", runScreenshotAnalysis);
  </script>
  {% elif tab == 'fno' %}
  <script>
    function loadingHtml() {
      return '<div class="nse-loading-block"><span class="nse-spinner" aria-hidden="true"></span><span>Loading data...</span></div>';
    }
    function tableOrMsg(host, html, msg) {
      host.innerHTML = html && html.trim() ? html : '<p class="nse-no-record">' + msg + "</p>";
    }
    function setCardVisible(id, visible) {
      const el = document.getElementById(id);
      if (el) el.style.display = visible ? "" : "none";
    }
    async function loadFno() {
      const s = document.getElementById("sentiment_filter").value;
      const u = document.getElementById("universe").value;
      const f = document.getElementById("fno-host");
      const n = document.getElementById("uni-host");
      const b = document.getElementById("bear-host");
      const w = document.getElementById("warn-host");
      const showFno = s !== "bear" && s !== "choose";
      const showUni = s !== "bear" && s !== "choose";
      const showBear = s !== "bull" && s !== "choose";
      setCardVisible("fno-picks-card", showFno);
      setCardVisible("fno-universe-card", showUni);
      setCardVisible("fno-bear-card", showBear);
      w.innerHTML = "";
      document.getElementById("last-ref").textContent = "—";
      if (s === "choose") return;
      if (showFno) f.innerHTML = loadingHtml(); else f.innerHTML = "";
      if (showUni) n.innerHTML = loadingHtml(); else n.innerHTML = "";
      if (showBear) b.innerHTML = loadingHtml(); else b.innerHTML = "";
      try {
        const res = await fetch("/api/us/fno-dashboard?sentiment_filter=" + encodeURIComponent(s) + "&universe=" + encodeURIComponent(u));
        const p = await res.json();
        if (!res.ok) throw new Error(p.error || res.statusText);
        if (showFno) tableOrMsg(f, p.fno_table || "", "No record found for " + s.charAt(0).toUpperCase() + s.slice(1) + " filter.");
        if (showUni) tableOrMsg(n, p.universe_table || "", "No record found for " + s.charAt(0).toUpperCase() + s.slice(1) + " filter.");
        if (showBear) tableOrMsg(b, p.bear_table || "", "No record found for " + s.charAt(0).toUpperCase() + s.slice(1) + " filter.");
        if (p.warnings && p.warnings.length) w.innerHTML = p.warnings.map(function (x) { return '<div class="warn">' + x + "</div>"; }).join("");
        document.getElementById("last-ref").textContent = p.last_refreshed || "—";
        initSortablePaginatedTables();
      } catch (e) {
        const err = '<p class="warn">' + (e.message || String(e)) + "</p>";
        if (showFno) f.innerHTML = err;
        if (showUni) n.innerHTML = err;
        if (showBear) b.innerHTML = err;
      }
    }
    document.getElementById("refresh").addEventListener("click", loadFno);
    document.getElementById("sentiment_filter").addEventListener("change", loadFno);
    document.getElementById("universe").addEventListener("change", loadFno);
  </script>
  {% else %}
  <script>
    let chart = null;
    function initUsSymbolCombo() {
      const input = document.getElementById("onesymbol");
      const list = document.getElementById("us-symbol-listbox");
      const combo = document.getElementById("us-symbol-combo");
      if (!input || !list || !combo) return;
      let debounceTimer = null;
      let fetchController = null;
      let fetchGeneration = 0;
      let activeIndex = -1;
      let lastResults = [];
      function setOpen(open) {
        combo.classList.toggle("is-open", open);
        list.hidden = !open;
        input.setAttribute("aria-expanded", open ? "true" : "false");
      }
      function clearActive() {
        list.querySelectorAll("li[role='option']").forEach((el) => el.classList.remove("symbol-dd-active"));
        activeIndex = -1;
      }
      function applyActive() {
        const opts = list.querySelectorAll("li[role='option']");
        opts.forEach((el, i) => el.classList.toggle("symbol-dd-active", i === activeIndex));
        if (activeIndex >= 0 && opts[activeIndex]) opts[activeIndex].scrollIntoView({ block: "nearest" });
      }
      function pick(sym) {
        input.value = sym;
        setOpen(false);
        clearActive();
        input.focus();
      }
      function render(items, showEmptyHint) {
        lastResults = items;
        clearActive();
        list.innerHTML = "";
        if (items.length === 0) {
          if (showEmptyHint) {
            const li = document.createElement("li");
            li.className = "symbol-dd-empty";
            li.textContent = "No matching symbols";
            list.appendChild(li);
            setOpen(true);
          } else {
            setOpen(false);
          }
          return;
        }
        items.forEach((sym, i) => {
          const li = document.createElement("li");
          li.setAttribute("role", "option");
          li.id = "us-sym-opt-" + i;
          li.textContent = sym;
          li.addEventListener("mousedown", (e) => {
            e.preventDefault();
            pick(sym);
          });
          list.appendChild(li);
        });
        setOpen(true);
      }
      function scheduleFetch(showEmptyHint) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(async () => {
          const q = input.value.trim();
          if (fetchController) fetchController.abort();
          fetchController = new AbortController();
          const { signal } = fetchController;
          const myGen = ++fetchGeneration;
          try {
            const res = await fetch("/api/us/symbols?q=" + encodeURIComponent(q), { signal });
            if (!res.ok) throw new Error("Failed to load symbols");
            const data = await res.json();
            if (myGen !== fetchGeneration) return;
            render(data.symbols || [], showEmptyHint && q.length > 0);
          } catch (err) {
            if (err && err.name === "AbortError") return;
            if (myGen !== fetchGeneration) return;
            render([], showEmptyHint && q.length > 0);
          }
        }, 90);
      }
      input.addEventListener("input", () => scheduleFetch(true));
      input.addEventListener("focus", () => scheduleFetch(false));
      input.addEventListener("keydown", (e) => {
        const opts = () => list.querySelectorAll("li[role='option']");
        const n = opts().length;
        if (!list.hidden && n > 0) {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            activeIndex = activeIndex < n - 1 ? activeIndex + 1 : 0;
            applyActive();
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            activeIndex = activeIndex > 0 ? activeIndex - 1 : n - 1;
            applyActive();
            return;
          }
          if (e.key === "Enter" && activeIndex >= 0 && lastResults[activeIndex]) {
            e.preventDefault();
            pick(lastResults[activeIndex]);
            return;
          }
        }
        if (e.key === "Escape") {
          setOpen(false);
          clearActive();
        }
      });
      document.addEventListener("click", (e) => {
        if (!combo.contains(e.target)) {
          setOpen(false);
          clearActive();
        }
      });
    }
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
      status.innerHTML = '<div class="nse-loading-block"><span class="nse-spinner" aria-hidden="true"></span><span>Loading US backtest...</span></div>';
      try {
        const res = await fetch("/api/us/backtest?mode=" + encodeURIComponent(mode) + "&universe=" + encodeURIComponent(universe) + "&max=" + encodeURIComponent(max) + sym);
        const p = await res.json();
        if (!res.ok) throw new Error(p.error || res.statusText);
        const labels = p.labels || [];
        const datasets = p.datasets || [];
        const meta = p.meta || {};
        if (meta.mode === "pnl") status.textContent = "Trades: " + (meta.trades || 0) + " | Win rate: " + (meta.win_rate_pct || 0) + "% | Return: " + (meta.cumulative_return_pct || 0) + "%";
        else status.textContent = (meta.symbol_count || 0) + " symbols | " + (meta.period || "6mo");
        if (p.warnings && p.warnings.length) warnings.innerHTML = '<div class="warn">' + p.warnings.join(" | ") + "</div>";
        if (!labels.length) return;
        const ctx = document.getElementById("btchart").getContext("2d");
        if (chart) chart.destroy();
        chart = new Chart(ctx, { type: (meta.mode === "pnl") ? "line" : "bar", data: { labels: labels, datasets: datasets }, options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index" }, scales: { x: { stacked: meta.mode !== "pnl" }, y: { stacked: meta.mode !== "pnl", beginAtZero: true } } } });
        card.style.display = "";
      } catch (e) {
        status.textContent = "";
        warnings.innerHTML = '<div class="warn">' + (e.message || String(e)) + "</div>";
      }
    }
    initUsSymbolCombo();
    document.getElementById("run").addEventListener("click", runBt);
  </script>
  {% endif %}
  <script>
""" + NSE_TABLE_PAGER_JS + """
    document.addEventListener("DOMContentLoaded", function () {
      initSortablePaginatedTables();
    });
  </script>
""" + THEME_SCRIPT + """
</body>
</html>
"""

FNO_DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
""" + THEME_HEAD_INIT + """
  <title>Daily F&O Dashboard</title>
  <style>
    :root, html[data-theme="light"] {
      --page-bg: #f5f6f8;
      --text: #111827;
      --muted: #6b7280;
      --card-bg: #ffffff;
      --card-border: #e5e7eb;
      --table-border: #e5e7eb;
      --th-bg: #f9fafb;
      --btn-bg: #2563eb;
      --btn-color: #fff;
      --select-border: #d1d5db;
      --input-bg: #fff;
      --input-text: #111827;
      --link: #2563eb;
      --warn-text: #b91c1c;
      --warn-bg: #fef2f2;
      --warn-border: #fecaca;
      --theme-toggle-bg: #e0f2fe;
      --theme-toggle-color: #1d4ed8;
      --nav-bg: #ffffff;
      --nav-border: #eceef2;
      --nav-shadow: 0 1px 0 rgba(15, 23, 42, 0.06);
      --accent-soft: #e8f1ff;
      --card-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 8px 28px rgba(15, 23, 42, 0.07);
      --panel-bg: #f9fafb;
    }
    html[data-theme="dark"] {
      --page-bg: #0f1419;
      --text: #e6edf3;
      --muted: #8b949e;
      --card-bg: #1a2332;
      --card-border: #30363d;
      --table-border: #3d4a5c;
      --th-bg: #252f3f;
      --btn-bg: #3b82f6;
      --btn-color: #fff;
      --select-border: #4a5568;
      --input-bg: #0d1117;
      --input-text: #e6edf3;
      --link: #60a5fa;
      --warn-text: #ffb1b1;
      --warn-bg: #2d1b1b;
      --warn-border: #4a2020;
      --theme-toggle-bg: #1e3a5f;
      --theme-toggle-color: #93c5fd;
      --nav-bg: #161b22;
      --nav-border: #30363d;
      --nav-shadow: none;
      --accent-soft: #1f2937;
      --card-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
      --panel-bg: #252f3f;
    }
    * { box-sizing: border-box; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      background: var(--page-bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    .site-topnav {
      background: var(--nav-bg);
      border-bottom: 1px solid var(--nav-border);
      box-shadow: var(--nav-shadow);
      position: sticky;
      top: 0;
      z-index: 300;
    }
    .site-topnav-inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 24px;
      min-height: 56px;
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .site-brand {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: var(--text);
      font-weight: 600;
      font-size: 1.05rem;
      letter-spacing: -0.02em;
      margin-right: 8px;
    }
    .site-brand-text { text-transform: none; }
    .site-brand-mark {
      width: 22px;
      height: 22px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 3px;
    }
    .site-brand-mark i { display: block; background: var(--text); border-radius: 2px; font-style: normal; }
    .site-topnav-nav { display: flex; align-items: center; gap: 2px; flex: 1; flex-wrap: wrap; }
    .site-topnav-divider { width: 1px; height: 20px; background: var(--card-border); margin: 0 8px; }
    .site-topnav-link {
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
      padding: 8px 14px;
      border-radius: 8px;
      transition: background 0.15s, color 0.15s;
    }
    .site-topnav-link:hover { color: var(--link); background: var(--page-bg); }
    .site-topnav-link.is-active { color: var(--link); background: var(--accent-soft); }
    .site-subnav { background: var(--nav-bg); border-bottom: 1px solid var(--nav-border); }
    .site-subnav-inner { max-width: 1200px; margin: 0 auto; padding: 8px 24px; display: flex; gap: 8px; flex-wrap: wrap; }
    .site-theme-btn { margin-left: auto; border: 1px solid rgba(37, 99, 235, 0.22); font-weight: 500; }
    html[data-theme="dark"] .site-theme-btn { border-color: rgba(96, 165, 250, 0.35); }
    .site-main { max-width: 1200px; margin: 0 auto; padding: 28px 24px 56px; }
    .site-hero { margin-bottom: 28px; }
    .site-hero h1 {
      font-size: 1.75rem;
      font-weight: 600;
      letter-spacing: -0.03em;
      margin: 0 0 8px;
    }
    .site-hero .lead { font-size: 15px; line-height: 1.55; max-width: 56ch; margin: 0; color: var(--muted); }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 22px 24px;
      margin-bottom: 20px;
      box-shadow: var(--card-shadow);
    }
    h2 { margin-top: 0; font-size: 1.15rem; font-weight: 600; letter-spacing: -0.02em; }
    .muted { color: var(--muted); }
    a { color: var(--link); }
    .theme-toggle {
      background: var(--theme-toggle-bg);
      color: var(--theme-toggle-color);
      padding: 8px 16px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 14px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px 14px;
      align-items: center;
      padding: 16px 18px;
      background: var(--panel-bg);
      border-radius: 12px;
      border: 1px solid var(--card-border);
    }
    button:not(.theme-toggle), a.button-link {
      background: var(--btn-bg);
      color: var(--btn-color);
      border: 0;
      padding: 10px 18px;
      border-radius: 8px;
      cursor: pointer;
      text-decoration: none;
      display: inline-block;
      font-weight: 500;
    }
    a.button-link.secondary {
      background: var(--input-bg);
      color: var(--link);
      border: 1px solid var(--card-border);
    }
    select { padding: 8px 12px; border-radius: 8px; border: 1px solid var(--select-border); background: var(--input-bg); color: var(--input-text); }
    table { border-collapse: collapse; width: 100%; margin-top: 8px; font-size: 14px; }
    th, td { border: 1px solid var(--table-border); padding: 8px; text-align: left; }
    th { background: var(--th-bg); color: var(--text); }
    th.sortable { cursor: pointer; user-select: none; }
    .warn { color: var(--warn-text); background: var(--warn-bg); border: 1px solid var(--warn-border); padding: 8px; border-radius: 6px; margin-bottom: 6px; }
    .nse-no-record {
      min-height: 120px;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--warn-text);
      font-weight: 600;
      text-align: center;
      padding: 18px 16px;
      box-sizing: border-box;
    }
""" + NSE_LOADING_CSS + NSE_TABLE_PAGER_CSS + """
    button:not(.theme-toggle):disabled { opacity: 0.65; cursor: not-allowed; }
  </style>
</head>
<body>
  <header class="site-topnav">
    <div class="site-topnav-inner">
      <a href="/" class="site-brand">
        <span class="site-brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span class="site-brand-text">SquintDesk</span>
      </a>
      <nav class="site-topnav-nav" aria-label="Main navigation">
        <a href="/" class="site-topnav-link {% if top_nav == 'dashboard' %}is-active{% endif %}">Dashboard</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/scanner" class="site-topnav-link {% if top_nav == 'ind' %}is-active{% endif %}">IND Stocks</a>
        <span class="site-topnav-divider" aria-hidden="true"></span>
        <a href="/us/scanner" class="site-topnav-link {% if top_nav == 'us' %}is-active{% endif %}">US Stocks</a>
      </nav>
      <button type="button" class="theme-toggle site-theme-btn" id="theme-toggle" aria-label="Toggle light or dark theme">Dark mode</button>
    </div>
  </header>
  <div class="site-subnav">
    <div class="site-subnav-inner">
      {% set na = nav_active|default('fno') %}
      <a href="/scanner" class="site-topnav-link {% if na == 'scanner' %}is-active{% endif %}">Scanner</a>
      <a href="/fno-dashboard" class="site-topnav-link {% if na == 'fno' %}is-active{% endif %}">F&amp;O</a>
      <a href="/backtest" class="site-topnav-link {% if na == 'backtest' %}is-active{% endif %}">Backtest</a>
    </div>
  </div>
  <main class="site-main">
  <div class="site-hero">
    <h1>Daily F&amp;O Dashboard</h1>
    <p class="lead">Real-time F&amp;O universe and picks. Refresh whenever you need the latest snapshot.</p>
  </div>

  <div class="card">
    <div class="actions">
      <label for="sentiment_filter">Filter:</label>
      <select id="sentiment_filter" name="sentiment_filter" aria-label="Sentiment filter">
        <option value="choose" {% if selected_sentiment_filter == "choose" %}selected{% endif %}>Choose</option>
        <option value="all" {% if selected_sentiment_filter == "all" %}selected{% endif %}>All</option>
        <option value="bull" {% if selected_sentiment_filter == "bull" %}selected{% endif %}>Bull</option>
        <option value="bear" {% if selected_sentiment_filter == "bear" %}selected{% endif %}>Bear</option>
      </select>
      <button type="button" id="fno-refresh">Refresh Data</button>
      <span class="muted">Last refreshed: <span id="fno-last-refreshed">—</span></span>
      <span class="muted" id="fno-snapshot-wrap" hidden>| Source: <span id="fno-snapshot-name"></span></span>
    </div>
  </div>

  <div class="card" id="warnings-card" hidden>
    <h2>Warnings</h2>
    <div id="warnings-list"></div>
  </div>

  <div class="card" id="fno-picks-card">
    <h2>F&O Picks</h2>
    <div id="fno-table-host"></div>
  </div>

  <div class="card" id="fno-universe-card">
    <h2>F&O Universe Snapshot</h2>
    <div id="universe-table-host"></div>
  </div>

  <div class="card" id="fno-bear-card">
    <h2>Bear Watchlist (Separate Table)</h2>
    <div id="bear-table-host"></div>
    <div id="bear-notes-host"></div>
  </div>
  </main>
  <script>
    function fnoLoadingHtml() {
      return (
        '<div class="nse-loading-block" role="status" aria-live="polite">' +
        '<span class="nse-spinner" aria-hidden="true"></span>' +
        "<span>Loading data…</span></div>"
      );
    }

    function renderWarnings(list) {
      const card = document.getElementById("warnings-card");
      const listEl = document.getElementById("warnings-list");
      if (!card || !listEl) return;
      listEl.innerHTML = "";
      if (!list || !list.length) {
        card.hidden = true;
        return;
      }
      card.hidden = false;
      list.forEach((w) => {
        const d = document.createElement("div");
        d.className = "warn";
        d.textContent = w;
        listEl.appendChild(d);
      });
    }

    function renderTableHost(host, html, emptyMsg, opts) {
      if (!host) return;
      if (html && html.trim()) {
        host.innerHTML = html;
      } else {
        const red = opts && opts.red;
        if (red) {
          host.innerHTML = '<div class="nse-no-record">' + emptyMsg + "</div>";
        } else {
          host.innerHTML = '<p class="muted">' + emptyMsg + "</p>";
        }
      }
    }

    function renderBearNotes(notes) {
      const host = document.getElementById("bear-notes-host");
      if (!host) return;
      host.innerHTML = "";
      if (!notes || !notes.length) return;
      const h3 = document.createElement("h3");
      h3.textContent = "Bear Scenario Analysis";
      const ul = document.createElement("ul");
      notes.forEach((n) => {
        const li = document.createElement("li");
        li.textContent = n;
        ul.appendChild(li);
      });
      host.appendChild(h3);
      host.appendChild(ul);
    }

    function setCardVisible(cardId, show) {
      const el = document.getElementById(cardId);
      if (!el) return;
      el.style.display = show ? "" : "none";
    }

    let fnoLoadSeq = 0;
    async function loadFnoDashboard() {
      const seq = ++fnoLoadSeq;
      const sel = document.getElementById("sentiment_filter");
      const btn = document.getElementById("fno-refresh");
      const filter = (sel && sel.value) || "choose";
      const isChoose = filter === "choose";
      const showFno = filter === "all" || filter === "bull";
      const showUni = filter === "all" || filter === "bull";
      const showBear = filter === "all" || filter === "bear";
      setCardVisible("fno-picks-card", showFno);
      setCardVisible("fno-universe-card", showUni);
      setCardVisible("fno-bear-card", showBear);
      const fnoH = document.getElementById("fno-table-host");
      const uniH = document.getElementById("universe-table-host");
      const bearH = document.getElementById("bear-table-host");
      const notesH = document.getElementById("bear-notes-host");
      if (btn) btn.disabled = true;
      renderWarnings([]);
      if (notesH) notesH.innerHTML = "";
      if (isChoose) {
        if (fnoH) fnoH.innerHTML = "";
        if (uniH) uniH.innerHTML = "";
        if (bearH) bearH.innerHTML = "";
        document.getElementById("fno-last-refreshed").textContent = "—";
        document.getElementById("fno-snapshot-wrap").hidden = true;
        if (btn) btn.disabled = false;
        return;
      }
      if (fnoH) fnoH.innerHTML = fnoLoadingHtml();
      if (uniH) uniH.innerHTML = fnoLoadingHtml();
      if (bearH) bearH.innerHTML = fnoLoadingHtml();

      try {
        const res = await fetch("/api/fno-dashboard?sentiment_filter=" + encodeURIComponent(filter));
        const payload = await res.json().catch(() => ({}));
        if (seq !== fnoLoadSeq) return;
        if (!res.ok) {
          const err = payload.error || res.statusText || "Request failed";
          renderWarnings([err].concat(payload.warnings || []));
          renderTableHost(fnoH, "", "No record found", { red: true });
          renderTableHost(uniH, "", "No record found", { red: true });
          renderTableHost(bearH, "", "No record found", { red: true });
          if (notesH) notesH.innerHTML = "";
          document.getElementById("fno-last-refreshed").textContent = "—";
          document.getElementById("fno-snapshot-wrap").hidden = true;
          return;
        }
        renderWarnings(payload.warnings || []);
        renderTableHost(
          fnoH,
          showFno ? payload.fno_table || "" : "",
          filter === "bull" ? "No record found for Bull filter." : "No record found.",
          { red: !showFno }
        );
        renderTableHost(
          uniH,
          showUni ? payload.universe_table || "" : "",
          filter === "bull" ? "No record found for Bull filter." : "No record found.",
          { red: !showUni }
        );
        renderTableHost(
          bearH,
          showBear ? payload.bear_table || "" : "",
          filter === "bear" ? "No record found for Bear filter." : "No record found.",
          { red: !showBear }
        );
        if (showBear) {
          renderBearNotes(payload.bear_notes || []);
        } else if (notesH) {
          notesH.innerHTML = "";
        }
        document.getElementById("fno-last-refreshed").textContent = payload.last_refreshed || "—";
        const snapW = document.getElementById("fno-snapshot-wrap");
        const snapN = document.getElementById("fno-snapshot-name");
        if (payload.snapshot_name && snapW && snapN) {
          snapN.textContent = payload.snapshot_name;
          snapW.hidden = false;
        } else if (snapW) {
          snapW.hidden = true;
        }
      } catch (e) {
        if (seq !== fnoLoadSeq) return;
        renderWarnings([e.message || String(e)]);
        renderTableHost(fnoH, "", "No record found", { red: true });
        renderTableHost(uniH, "", "No record found", { red: true });
        renderTableHost(bearH, "", "No record found", { red: true });
        if (notesH) notesH.innerHTML = "";
        document.getElementById("fno-last-refreshed").textContent = "—";
        document.getElementById("fno-snapshot-wrap").hidden = true;
      } finally {
        if (seq === fnoLoadSeq && btn) btn.disabled = false;
        if (seq === fnoLoadSeq) initSortablePaginatedTables();
      }
    }

""" + NSE_TABLE_PAGER_JS + """

    document.addEventListener("DOMContentLoaded", () => {
      const sel = document.getElementById("sentiment_filter");
      const btn = document.getElementById("fno-refresh");
      if (btn) btn.addEventListener("click", loadFnoDashboard);
      if (sel) sel.addEventListener("change", loadFnoDashboard);
      setCardVisible("fno-picks-card", false);
      setCardVisible("fno-universe-card", false);
      setCardVisible("fno-bear-card", false);
      document.getElementById("fno-last-refreshed").textContent = "—";
      document.getElementById("fno-snapshot-wrap").hidden = true;
    });
  </script>
""" + THEME_SCRIPT + """
</body>
</html>
"""


def _to_table(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    df = df[[column for column in VIEW_COLUMNS if column in df.columns]]
    df = df.rename(columns=RENAME_MAP)
    return df.to_html(index=False, classes="results")


def _filter_by_sentiment(df: pd.DataFrame, sentiment_filter: str) -> pd.DataFrame:
    if df.empty or "bull_or_bearish" not in df.columns:
        return df
    if sentiment_filter == "bull":
        return df[df["bull_or_bearish"] == "bullish"]
    if sentiment_filter == "bear":
        return df[df["bull_or_bearish"] == "bearish"]
    return df


def run_scan(category: str, sentiment_filter: str = "all") -> tuple[dict[str, str], str, list[str]]:
    config = load_config(CONFIG_FILE)
    screener = StockScreener(config)
    results = screener.run(category=category)

    categories = ["swing", "long_term", "fno"] if category == "all" else [category]
    tables: dict[str, str] = {}
    for key in categories:
        df = screener.to_dataframe(results[key])
        df = _filter_by_sentiment(df, sentiment_filter)
        tables[key] = _to_table(df)

    universe_df = screener.to_dataframe(screener.last_scanned)
    universe_df = _filter_by_sentiment(universe_df, sentiment_filter)
    universe_table = _to_table(universe_df)
    return tables, universe_table, screener.warnings


def run_fno_dashboard_scan(sentiment_filter: str = "all") -> tuple[str, str, list[str], pd.DataFrame]:
    config = load_config(CONFIG_FILE)
    screener = StockScreener(config)
    results = screener.run(category="fno")
    raw_df = screener.to_dataframe(results.get("fno", []))

    filtered_df = _filter_by_sentiment(raw_df, sentiment_filter)
    fno_table = _to_table(filtered_df)

    universe_df = screener.to_dataframe(screener.last_scanned)
    universe_df = _filter_by_sentiment(universe_df, sentiment_filter)
    universe_table = _to_table(universe_df)
    return fno_table, universe_table, screener.warnings, raw_df


def load_latest_fno_snapshot(sentiment_filter: str = "all") -> tuple[str, str]:
    reports_dir = Path("reports")
    if not reports_dir.exists():
        return "", ""

    fno_files = sorted(reports_dir.glob("fno_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not fno_files:
        return "", ""

    latest = fno_files[0]
    try:
        df = pd.read_csv(latest)
        df = _filter_by_sentiment(df, sentiment_filter)
        return _to_table(df), latest.name
    except Exception:
        return "", ""


def build_bear_watchlist(df: pd.DataFrame) -> tuple[str, list[str]]:
    if df.empty or "bull_or_bearish" not in df.columns:
        return "", []

    bear_df = df[df["bull_or_bearish"] == "bearish"].copy()
    if bear_df.empty:
        return "", []

    if "atr14" in bear_df.columns:
        bear_df["trigger"] = bear_df["support_20d"]
        bear_df["target_a"] = bear_df["support_20d"] - (0.8 * bear_df["atr14"])
        bear_df["target_b"] = bear_df["support_20d"] - (1.5 * bear_df["atr14"])
        bear_df["invalidation"] = bear_df["day_high"]
    else:
        bear_df["trigger"] = bear_df["support_20d"]
        bear_df["target_a"] = bear_df["support_20d"]
        bear_df["target_b"] = bear_df["support_20d"]
        bear_df["invalidation"] = bear_df["day_high"]

    show_cols = [
        "symbol",
        "close",
        "trigger",
        "target_a",
        "target_b",
        "invalidation",
        "volume_ratio",
        "oi_change_pct",
        "bull_or_bearish",
    ]
    show_cols = [c for c in show_cols if c in bear_df.columns]
    bear_table_df = bear_df[show_cols].rename(
        columns={
            "symbol": "Symbol",
            "close": "Close",
            "trigger": "Bear Trigger",
            "target_a": "Target A",
            "target_b": "Target B",
            "invalidation": "Invalidation",
            "volume_ratio": "Volume Ratio",
            "oi_change_pct": "OI Change %",
            "bull_or_bearish": "Sentiment",
        }
    )

    notes: list[str] = []
    top = bear_df.head(8)
    for _, row in top.iterrows():
        symbol = row.get("symbol", "")
        close = float(row.get("close", 0))
        trigger = float(row.get("trigger", close))
        target_a = float(row.get("target_a", trigger))
        target_b = float(row.get("target_b", target_a))
        invalidation = float(row.get("invalidation", close))
        volume_ratio = float(row.get("volume_ratio", 0)) if pd.notna(row.get("volume_ratio")) else 0.0
        oi_pct = row.get("oi_change_pct")
        oi_text = f"{float(oi_pct):.2f}%" if pd.notna(oi_pct) else "N/A"
        notes.append(
            f"{symbol}: Primary scenario -> breakdown below {trigger:.2f} can move to {target_a:.2f} then {target_b:.2f}; "
            f"Invalidation above {invalidation:.2f}. Volume ratio {volume_ratio:.2f}, OI change {oi_text}."
        )
        notes.append(
            f"{symbol}: Alternate scenario -> if price holds above {trigger:.2f} and reclaims intraday highs, bearish momentum may fail; "
            f"wait for fresh confirmation before entry."
        )

    return bear_table_df.to_html(index=False, classes="results"), notes


def _normalize_symbol(symbol: str) -> str:
    raw = symbol.strip().upper().replace(".NS", "")
    raw = raw.split(":")[-1]
    return re.sub(r"[^A-Z0-9]", "", raw)


def _config_symbol_universe() -> list[str]:
    """Deduplicated sorted symbols from config.yml (watchlist + F&O)."""
    try:
        config = load_config(CONFIG_FILE)
    except FileNotFoundError:
        return []
    seen: dict[str, None] = {}
    for key in ("symbols", "fno_symbols"):
        for raw in config.get(key) or []:
            sym = _normalize_symbol(str(raw))
            if sym:
                seen[sym] = None
    return sorted(seen.keys())


def _extract_symbol_from_filename(filename: str) -> str:
    upper_name = filename.upper()
    candidates = re.findall(r"[A-Z0-9]{2,20}", upper_name)
    # Prefer tokens that look like stock symbols and avoid generic words.
    for token in candidates:
        if token in COMMON_NON_SYMBOL_WORDS:
            continue
        if re.search(r"\d{4,}", token):
            continue
        return token
    return ""


def _extract_symbol_from_ocr(image_path: Path, watchlist_symbols: list[str]) -> str:
    try:
        from PIL import Image  # type: ignore
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception:
        return ""

    try:
        image = Image.open(image_path)
        width, height = image.size
        # Symbols are commonly near top-left in chart screenshots.
        crop = image.crop((0, 0, int(width * 0.45), int(height * 0.2)))
        ocr_engine = RapidOCR()
        ocr_result, _ = ocr_engine(crop)
    except Exception:
        return ""

    if not ocr_result:
        return ""

    watchlist_set = {s.upper() for s in watchlist_symbols}
    raw_text = " ".join(item[1] for item in ocr_result if len(item) > 1)
    tokens = re.findall(r"[A-Z0-9:.]{2,20}", raw_text.upper())
    normalized_tokens = [_normalize_symbol(token) for token in tokens]

    # First preference: a known watchlist symbol found in OCR text.
    for token in normalized_tokens:
        if token in watchlist_set:
            return token

    # Second preference: first token that looks like a real symbol.
    for token in normalized_tokens:
        if not token or token in COMMON_NON_SYMBOL_WORDS:
            continue
        if re.search(r"\d{5,}", token):
            continue
        return token
    return ""


def run_screenshot_analysis(upload_name: str, upload_bytes: bytes, selected_symbol: str) -> tuple[str, list[str], list[str], str]:
    config = load_config(CONFIG_FILE)
    warnings: list[str] = []
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload_name).name or "chart_upload.png"
    save_path = UPLOADS_DIR / safe_name
    save_path.write_bytes(upload_bytes)

    symbol = _normalize_symbol(selected_symbol or "")
    if not symbol:
        symbol = _normalize_symbol(_extract_symbol_from_filename(safe_name))
    if not symbol:
        symbol = _normalize_symbol(_extract_symbol_from_ocr(save_path, config["symbols"]))
        if not symbol:
            warnings.append("Could not auto-detect symbol from screenshot. Type symbol manually (example: SBIN).")
            return "", [], warnings, ""

    screener = StockScreener(config)
    row = screener.analyze_symbol(symbol)
    df = screener.to_dataframe([row])
    table = _to_table(df)

    risk = max(0.01, row.entry_price - row.stop_loss)
    rr_t1 = (row.target_1 - row.entry_price) / risk
    rr_t2 = (row.target_2 - row.entry_price) / risk
    atr_pct = (row.atr14 / row.close) * 100 if row.close > 0 else 0.0

    notes = [
        f"Detected symbol: {symbol}. Uploaded screenshot saved at {save_path}.",
        f"Setup: {row.setup}. Entry at {row.entry_price:.2f}, Stop Loss at {row.stop_loss:.2f}.",
        f"Risk/Reward: T1 ~ {rr_t1:.2f}R, T2 ~ {rr_t2:.2f}R.",
        f"Volatility: ATR14 is {row.atr14:.2f} ({atr_pct:.2f}% of close).",
        (
            "Trend bias: bullish"
            if row.close > row.ema20 > row.ema50
            else "Trend bias: neutral/weak; wait for confirmation."
        ),
    ]
    return table, notes, warnings + screener.warnings, symbol


def run_screenshot_analysis_us(
    upload_name: str, upload_bytes: bytes, selected_symbol: str, universe: str
) -> tuple[str, list[str], list[str], str]:
    warnings: list[str] = []
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload_name).name or "chart_upload.png"
    save_path = UPLOADS_DIR / safe_name
    save_path.write_bytes(upload_bytes)

    us_symbols = sorted({sym.upper() for values in US_UNIVERSES.values() for sym in values})
    symbol = _normalize_symbol(selected_symbol or "")
    if not symbol:
        symbol = _normalize_symbol(_extract_symbol_from_filename(safe_name))
    if not symbol:
        symbol = _normalize_symbol(_extract_symbol_from_ocr(save_path, us_symbols))
    if not symbol:
        warnings.append("Could not auto-detect symbol from screenshot. Type symbol manually (example: AAPL).")
        return "", [], warnings, ""

    df, scan_warnings = scan_us(universe=universe)
    warnings.extend(scan_warnings)
    if df.empty:
        warnings.append("No US market data available right now.")
        return "", [], warnings, symbol

    row_df = df[df["Symbol"].astype(str).str.upper() == symbol]
    if row_df.empty:
        warnings.append(f"{symbol} is not in the selected category list.")
        return "", [], warnings, symbol

    row = row_df.iloc[0]
    table = row_df.to_html(index=False, classes="results")
    move = "bullish" if str(row.get("Trend", "")).lower() == "bullish" else "bearish"
    notes = [
        f"Detected symbol: {symbol}. Uploaded screenshot saved at {save_path}.",
        f"Latest US snapshot: Open {float(row['Open']):.2f}, High {float(row['High']):.2f}, Close {float(row['Close']):.2f}.",
        f"Day move: {float(row['Day %']):.2f}% with {move} intraday trend.",
        "Tip: use Backtest for historical curve; scanner snapshot is current-day oriented.",
    ]
    return table, notes, warnings, symbol


@app.route("/")
def dashboard() -> str:
    return render_template_string(
        DASHBOARD_HTML,
        top_nav="dashboard",
        filters=_filters_for_dashboard(),
    )


@app.route("/scanner", methods=["GET", "POST"])
def scanner() -> str:
    selected = "fno"
    selected_sentiment_filter = "all"
    selected_analysis_symbol = ""
    tables: dict[str, str] = {}
    universe_table = ""
    screenshot_table = ""
    screenshot_notes: list[str] = []
    warnings: list[str] = []
    config = load_config(CONFIG_FILE)

    if request.method == "POST":
        action = request.form.get("action", "scan")
        if action == "screenshot_analysis":
            selected_analysis_symbol = request.form.get("analysis_symbol", "").strip().upper()
            upload = request.files.get("chart_image")
            if upload is None or not upload.filename:
                warnings.append("Please upload a screenshot image.")
            else:
                screenshot_table, screenshot_notes, shot_warnings, detected_symbol = run_screenshot_analysis(
                    upload.filename,
                    upload.read(),
                    selected_analysis_symbol,
                )
                warnings.extend(shot_warnings)
                if detected_symbol:
                    selected_analysis_symbol = detected_symbol
        else:
            selected = request.form.get("category", "all")
            selected_sentiment_filter = request.form.get("sentiment_filter", "all").strip().lower()
            if selected_sentiment_filter not in {"all", "bull", "bear"}:
                selected_sentiment_filter = "all"
            tables, universe_table, warnings = run_scan(selected, sentiment_filter=selected_sentiment_filter)

    return render_template_string(
        HTML,
        categories=["all", "swing", "long_term", "fno"],
        top_nav="ind",
        nav_active="scanner",
        selected_category=selected,
        selected_sentiment_filter=selected_sentiment_filter,
        selected_analysis_symbol=selected_analysis_symbol,
        tables=tables,
        universe_table=universe_table,
        screenshot_table=screenshot_table,
        screenshot_notes=screenshot_notes,
        warnings=warnings,
    )


@app.route("/fno-dashboard", methods=["GET"])
def fno_dashboard() -> str:
    selected_sentiment_filter = request.args.get("sentiment_filter", "choose").strip().lower()
    if selected_sentiment_filter not in {"choose", "all", "bull", "bear"}:
        selected_sentiment_filter = "choose"

    return render_template_string(
        FNO_DASHBOARD_HTML,
        top_nav="ind",
        nav_active="fno",
        selected_sentiment_filter=selected_sentiment_filter,
    )


@app.route("/api/fno-dashboard")
def api_fno_dashboard():
    selected_sentiment_filter = request.args.get("sentiment_filter", "all").strip().lower()
    if selected_sentiment_filter not in {"all", "bull", "bear"}:
        selected_sentiment_filter = "all"
    cache_key = f"fno:{selected_sentiment_filter}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        fno_table, universe_table, warnings, raw_fno_df = run_fno_dashboard_scan(
            sentiment_filter=selected_sentiment_filter
        )
        if not universe_table:
            universe_table = fno_table
        bear_table, bear_notes = build_bear_watchlist(raw_fno_df)
        last_refreshed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "fno_table": fno_table,
            "universe_table": universe_table,
            "bear_table": bear_table,
            "bear_notes": bear_notes,
            "warnings": warnings,
            "last_refreshed": last_refreshed,
            "snapshot_name": "Fresh real-time scan",
        }
        _cache_set(cache_key, payload, FNO_CACHE_TTL_S)
        return jsonify(payload)
    except Exception as exc:
        return (
            jsonify(
                {
                    "error": str(exc),
                    "fno_table": "",
                    "universe_table": "",
                    "bear_table": "",
                    "bear_notes": [],
                    "warnings": [str(exc)],
                    "last_refreshed": "",
                    "snapshot_name": "",
                }
            ),
            500,
        )


@app.route("/backtest")
def backtest_page() -> str:
    return render_template_string(BACKTEST_HTML, top_nav="ind", nav_active="backtest")


@app.route("/us/scanner")
def us_scanner_page() -> str:
    return render_template_string(US_MARKET_HTML, top_nav="us", tab="scanner")


@app.route("/us/fno")
def us_fno_page() -> str:
    return render_template_string(US_MARKET_HTML, top_nav="us", tab="fno")


@app.route("/us/backtest")
def us_backtest_page() -> str:
    return render_template_string(US_MARKET_HTML, top_nav="us", tab="backtest")


@app.route("/api/symbols")
def api_symbols():
    """Filter symbols for backtest combobox (prefix-first, then contains)."""
    raw_q = (request.args.get("q") or "").strip()
    q = _normalize_symbol(raw_q)
    cache_key = f"symbols:{q}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    all_syms = _config_symbol_universe()
    if not q:
        payload = {"symbols": all_syms[:120]}
        _cache_set(cache_key, payload, SYMBOLS_CACHE_TTL_S)
        return jsonify(payload)
    pref = [s for s in all_syms if s.startswith(q)]
    rest = [s for s in all_syms if not s.startswith(q) and q in s]
    payload = {"symbols": (pref + rest)[:80]}
    _cache_set(cache_key, payload, SYMBOLS_CACHE_TTL_S)
    return jsonify(payload)


@app.route("/api/backtest")
def api_backtest():
    mode = request.args.get("mode", "count").strip().lower()
    if mode not in {"count", "pnl"}:
        mode = "count"
    universe = request.args.get("universe", "fno").strip().lower()
    if universe not in {"fno", "all"}:
        universe = "fno"
    try:
        max_sym = int(request.args.get("max", "120"))
    except ValueError:
        max_sym = 120
    max_sym = max(10, min(max_sym, 200))
    one_symbol = (request.args.get("symbol") or "").strip() or None
    cache_key = f"backtest:{mode}:{universe}:{max_sym}:{one_symbol or ''}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        config = load_config(CONFIG_FILE)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc), "labels": [], "datasets": [], "warnings": []}), 400

    try:
        from scanner.backtest import run_backtest_counts, run_backtest_pnl

        if mode == "pnl":
            payload, warnings = run_backtest_pnl(
                config,
                universe=universe,
                max_symbols=max_sym,
                period="6mo",
                symbol=one_symbol,
            )
        else:
            payload, warnings = run_backtest_counts(
                config,
                universe=universe,
                max_symbols=max_sym,
                period="6mo",
                symbol=one_symbol,
            )
    except Exception as exc:
        return jsonify({"error": str(exc), "labels": [], "datasets": [], "warnings": []}), 500

    payload["warnings"] = warnings
    _cache_set(cache_key, payload, BACKTEST_CACHE_TTL_S)
    return jsonify(payload)


def _us_fno_scan(sentiment: str = "all", universe: str = "mega") -> tuple[str, str, str, list[str]]:
    warnings: list[str] = []
    df, scan_w = scan_us(universe=universe)
    warnings.extend(scan_w)
    if df.empty:
        return "", "", "", warnings
    working = df.copy()
    working["Sentiment"] = working["Trend"].str.lower().map({"bullish": "bullish", "bearish": "bearish"}).fillna("neutral")
    if sentiment == "bull":
        working = working[working["Sentiment"] == "bullish"]
    elif sentiment == "bear":
        working = working[working["Sentiment"] == "bearish"]
    cols = ["Symbol", "Open", "High", "Close", "Day %", "Trend"]
    fno = working[cols] if not working.empty else working
    bear = working[working["Sentiment"] == "bearish"][cols] if not working.empty else working
    universe_df = df[cols]
    return (
        fno.to_html(index=False, classes="results") if not fno.empty else "",
        universe_df.to_html(index=False, classes="results") if not universe_df.empty else "",
        bear.to_html(index=False, classes="results") if not bear.empty else "",
        warnings,
    )


@app.route("/api/us/scanner")
def api_us_scanner():
    universe = (request.args.get("category") or request.args.get("universe") or "mega").strip().lower()
    sentiment = (request.args.get("sentiment_filter") or "all").strip().lower()
    if sentiment not in {"all", "bull", "bear"}:
        sentiment = "all"
    cache_key = f"us:scan:{universe}:{sentiment}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    df, warnings = scan_us(universe=universe)
    if not df.empty and "Trend" in df.columns and sentiment != "all":
        if sentiment == "bull":
            df = df[df["Trend"].astype(str).str.lower() == "bullish"]
        elif sentiment == "bear":
            df = df[df["Trend"].astype(str).str.lower() == "bearish"]
    payload = {"table_html": df.to_html(index=False, classes="results") if not df.empty else "", "warnings": warnings}
    _cache_set(cache_key, payload, 90)
    return jsonify(payload)


@app.route("/api/us/fno-dashboard")
def api_us_fno_dashboard():
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


@app.route("/api/us/backtest")
def api_us_backtest():
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


@app.route("/api/us/screenshot-analysis", methods=["POST"])
def api_us_screenshot_analysis():
    selected_symbol = (request.form.get("analysis_symbol") or "").strip().upper()
    universe = (request.form.get("universe") or "mega").strip().lower()
    if universe not in US_UNIVERSES:
        universe = "mega"
    upload = request.files.get("chart_image")
    if upload is None or not upload.filename:
        return jsonify({"error": "Please upload a screenshot image.", "table_html": "", "notes": [], "warnings": []}), 400
    try:
        table_html, notes, warnings, detected_symbol = run_screenshot_analysis_us(
            upload.filename,
            upload.read(),
            selected_symbol,
            universe,
        )
        return jsonify(
            {
                "table_html": table_html,
                "notes": notes,
                "warnings": warnings,
                "detected_symbol": detected_symbol,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "table_html": "", "notes": [], "warnings": []}), 500


@app.route("/api/us/symbols")
def api_us_symbols():
    raw_q = (request.args.get("q") or "").strip().upper()
    cache_key = f"us:symbols:{raw_q}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    all_syms = sorted({sym for values in US_UNIVERSES.values() for sym in values})
    if not raw_q:
        payload = {"symbols": all_syms[:120]}
        _cache_set(cache_key, payload, SYMBOLS_CACHE_TTL_S)
        return jsonify(payload)
    pref = [s for s in all_syms if s.startswith(raw_q)]
    rest = [s for s in all_syms if not s.startswith(raw_q) and raw_q in s]
    payload = {"symbols": (pref + rest)[:80]}
    _cache_set(cache_key, payload, SYMBOLS_CACHE_TTL_S)
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=True, use_reloader=True)

