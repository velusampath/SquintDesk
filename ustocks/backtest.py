from __future__ import annotations

from datetime import date
import time
from typing import Any

import pandas as pd
import yfinance as yf

from .service import US_UNIVERSES


def _empty_count_payload() -> dict[str, Any]:
    return {
        "labels": [],
        "datasets": [],
        "meta": {
            "mode": "count",
            "period": "6mo",
            "universe": "",
            "symbol_count": 0,
            "symbol_filter": None,
            "disclaimer": "",
        },
    }


def _empty_pnl_payload() -> dict[str, Any]:
    return {
        "labels": [],
        "datasets": [],
        "meta": {
            "mode": "pnl",
            "period": "6mo",
            "universe": "",
            "symbol_count": 0,
            "symbol_filter": None,
            "trades": 0,
            "win_rate_pct": 0.0,
            "avg_trade_return_pct": 0.0,
            "cumulative_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "disclaimer": "",
        },
    }


def run_backtest_counts_us(
    *,
    universe: str = "mega",
    max_symbols: int = 120,
    period: str = "6mo",
    pause_sec: float = 0.03,
    symbol: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if symbol and symbol.strip():
        symbols = [symbol.strip().upper()]
        single = symbols[0]
    else:
        symbols = US_UNIVERSES.get(universe, US_UNIVERSES["mega"])[: max(1, min(max_symbols, 300))]
        single = None

    per_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=False)
            if hist.empty or len(hist) < 20:
                warnings.append(f"{sym}: insufficient history")
                continue
            per_symbol[sym] = hist
        except Exception as exc:
            warnings.append(f"{sym}: {exc}")
        time.sleep(pause_sec)

    if not per_symbol:
        warnings.append("No symbol history loaded; cannot backtest.")
        return _empty_count_payload(), warnings

    all_dates: set[date] = set()
    for df in per_symbol.values():
        all_dates.update(pd.Timestamp(ts).normalize().date() for ts in df.index)
    days = sorted(all_dates)

    labels = [d.isoformat() for d in days]
    up_counts: list[int] = []
    down_counts: list[int] = []
    flat_counts: list[int] = []
    for d in days:
        up = down = flat = 0
        for df in per_symbol.values():
            row = df.loc[df.index.normalize() == pd.Timestamp(d)]
            if row.empty:
                continue
            latest = row.iloc[-1]
            o = float(latest["Open"]) if pd.notna(latest["Open"]) else float(latest["Close"])
            c = float(latest["Close"])
            if c > o * 1.002:
                up += 1
            elif c < o * 0.998:
                down += 1
            else:
                flat += 1
        up_counts.append(up)
        down_counts.append(down)
        flat_counts.append(flat)

    payload: dict[str, Any] = {
        "labels": labels,
        "datasets": [
            {"label": "Bullish day", "data": up_counts, "backgroundColor": "#10b981", "borderWidth": 0},
            {"label": "Bearish day", "data": down_counts, "backgroundColor": "#ef4444", "borderWidth": 0},
            {"label": "Neutral day", "data": flat_counts, "backgroundColor": "#64748b", "borderWidth": 0},
        ],
        "meta": {
            "mode": "count",
            "period": period,
            "universe": universe if single is None else "single",
            "symbol_count": len(per_symbol),
            "symbol_filter": single,
            "disclaimer": "US EOD daily candles from Yahoo Finance. Count buckets are based on open-close move.",
        },
    }
    return payload, warnings


def run_backtest_pnl_us(
    *,
    universe: str = "mega",
    max_symbols: int = 120,
    period: str = "6mo",
    pause_sec: float = 0.03,
    symbol: str | None = None,
    hold_days: int = 5,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if symbol and symbol.strip():
        symbols = [symbol.strip().upper()]
        single = symbols[0]
    else:
        symbols = US_UNIVERSES.get(universe, US_UNIVERSES["mega"])[: max(1, min(max_symbols, 300))]
        single = None

    per_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=False)
            if hist.empty or len(hist) < 30:
                warnings.append(f"{sym}: insufficient history")
                continue
            hist = hist.copy()
            hist["ema20"] = hist["Close"].ewm(span=20, adjust=False).mean()
            per_symbol[sym] = hist
        except Exception as exc:
            warnings.append(f"{sym}: {exc}")
        time.sleep(pause_sec)

    if not per_symbol:
        warnings.append("No symbol history loaded; cannot backtest.")
        return _empty_pnl_payload(), warnings

    daily_factors: dict[date, float] = {}
    trades: list[float] = []
    max_hold = max(1, hold_days)

    for _, hist in per_symbol.items():
        i = 20
        while i < len(hist) - 1:
            c = float(hist.iloc[i]["Close"])
            ema = float(hist.iloc[i]["ema20"]) if pd.notna(hist.iloc[i]["ema20"]) else c
            if c <= ema:
                i += 1
                continue

            entry_i = i + 1
            if entry_i >= len(hist):
                break
            entry = float(hist.iloc[entry_i]["Open"]) if pd.notna(hist.iloc[entry_i]["Open"]) else float(hist.iloc[entry_i]["Close"])
            if entry <= 0:
                i += 1
                continue
            stop = entry * 0.97
            target = entry * 1.05
            exit_i = min(len(hist) - 1, entry_i + max_hold)
            exit_price = float(hist.iloc[exit_i]["Close"])
            for j in range(entry_i, exit_i + 1):
                lo = float(hist.iloc[j]["Low"]) if pd.notna(hist.iloc[j]["Low"]) else exit_price
                hi = float(hist.iloc[j]["High"]) if pd.notna(hist.iloc[j]["High"]) else exit_price
                if lo <= stop:
                    exit_i = j
                    exit_price = stop
                    break
                if hi >= target:
                    exit_i = j
                    exit_price = target
                    break

            ret = (exit_price - entry) / entry
            trades.append(ret)
            d = pd.Timestamp(hist.index[exit_i]).normalize().date()
            daily_factors[d] = daily_factors.get(d, 1.0) * (1.0 + ret)
            i = exit_i + 1

    if not daily_factors:
        warnings.append("No completed trades for selected settings.")
        return _empty_pnl_payload(), warnings

    days = sorted(daily_factors.keys())
    labels = [d.isoformat() for d in days]
    equity: list[float] = []
    v = 100.0
    peak = v
    max_dd = 0.0
    for d in days:
        v *= daily_factors[d]
        equity.append(v)
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak)

    trades_n = len(trades)
    win_rate = (sum(1 for r in trades if r > 0) / trades_n * 100.0) if trades_n else 0.0
    avg_trade = (sum(trades) / trades_n * 100.0) if trades_n else 0.0
    cum = (equity[-1] / 100.0 - 1.0) * 100.0 if equity else 0.0

    payload: dict[str, Any] = {
        "labels": labels,
        "datasets": [
            {
                "label": "Equity Curve (Base 100)",
                "data": [round(x, 2) for x in equity],
                "borderColor": "#3b82f6",
                "backgroundColor": "rgba(59,130,246,0.14)",
                "pointRadius": 0,
                "fill": True,
                "tension": 0.2,
            }
        ],
        "meta": {
            "mode": "pnl",
            "period": period,
            "universe": universe if single is None else "single",
            "symbol_count": len(per_symbol),
            "symbol_filter": single,
            "trades": trades_n,
            "win_rate_pct": round(win_rate, 2),
            "avg_trade_return_pct": round(avg_trade, 2),
            "cumulative_return_pct": round(cum, 2),
            "max_drawdown_pct": round(max_dd * 100.0, 2),
            "disclaimer": "Simplified US simulation (next-day entry, 3% stop, 5% target, max-hold).",
        },
    }
    return payload, warnings

