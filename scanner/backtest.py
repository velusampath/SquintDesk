from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import pandas as pd
import yfinance as yf

from .filters import is_fno_candidate, is_long_term_candidate, is_swing_candidate
from .models import ScanResult
from .screener import StockScreener, _atr, _rsi


def _normalize_symbol(symbol: str) -> str:
    raw = symbol.strip().upper().replace(".NS", "")
    raw = raw.split(":")[-1]
    return re.sub(r"[^A-Z0-9]", "", raw)


def _to_nse_ticker(symbol: str) -> str:
    raw = _normalize_symbol(symbol)
    return f"{raw}.NS"


def _fetch_fundamentals(symbol: str) -> dict[str, Any]:
    try:
        info = yf.Ticker(_to_nse_ticker(symbol)).info or {}
    except Exception:
        return {
            "pe": None,
            "roe": None,
            "debt_to_equity": None,
            "operating_cashflow": None,
        }
    return {
        "pe": info.get("trailingPE") or info.get("forwardPE"),
        "roe": info.get("returnOnEquity"),
        "debt_to_equity": info.get("debtToEquity"),
        "operating_cashflow": info.get("operatingCashflow"),
    }


def _enrich_history(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = out["Close"].ewm(span=20, adjust=False).mean()
    out["ema50"] = out["Close"].ewm(span=50, adjust=False).mean()
    out["rsi14"] = _rsi(out["Close"], 14)
    out["atr14"] = _atr(out, 14)
    out["avg_volume_20"] = out["Volume"].rolling(20).mean()
    out["support_20d"] = out["Low"].shift(1).rolling(20).min()
    out["resistance_20d"] = out["High"].shift(1).rolling(20).max()
    out["breakout_20d"] = out["Close"] > out["High"].shift(1).rolling(20).max()
    return out


def _scan_result_at_index(
    symbol: str,
    df: pd.DataFrame,
    idx: int,
    fundamentals: dict[str, Any],
) -> ScanResult | None:
    if idx < 0 or idx >= len(df) or idx < 50:
        return None
    latest = df.iloc[idx]
    if pd.isna(latest.get("Close")):
        return None

    open_price = float(latest["Open"]) if pd.notna(latest["Open"]) else float(latest["Close"])
    close = float(latest["Close"])
    volume = float(latest["Volume"]) if pd.notna(latest["Volume"]) else 0.0
    avg_volume = float(latest["avg_volume_20"]) if pd.notna(latest.get("avg_volume_20")) else 0.0
    atr14 = float(latest["atr14"]) if pd.notna(latest.get("atr14")) else 0.0
    ema20 = float(latest["ema20"]) if pd.notna(latest.get("ema20")) else close
    ema50 = float(latest["ema50"]) if pd.notna(latest.get("ema50")) else close
    resistance_20d = (
        float(latest["resistance_20d"]) if pd.notna(latest.get("resistance_20d")) else close
    )
    support_20d = float(latest["support_20d"]) if pd.notna(latest.get("support_20d")) else close

    prev_row = df.iloc[idx - 1]
    prev_close = float(prev_row["Close"]) if pd.notna(prev_row.get("Close")) else close
    day_high = float(latest["High"]) if pd.notna(latest.get("High")) else close
    day_low = float(latest["Low"]) if pd.notna(latest.get("Low")) else close

    setup, entry_price, stop_loss, target_1, target_2 = StockScreener._build_trade_levels(
        close=close,
        atr14=atr14,
        ema20=ema20,
        resistance_20d=resistance_20d,
    )

    rsi14 = float(latest["rsi14"]) if pd.notna(latest.get("rsi14")) else 0.0
    volume_rise = volume > avg_volume and avg_volume > 0
    oi_change_pct_val = None

    bull_score = 0
    bear_score = 0
    if close > ema20 > ema50:
        bull_score += 2
    elif close < ema20 < ema50:
        bear_score += 2

    if close > open_price:
        bull_score += 1
    elif close < open_price:
        bear_score += 1

    if volume_rise:
        if close >= prev_close:
            bull_score += 1
        else:
            bear_score += 1

    if bull_score - bear_score >= 2:
        bull_or_bearish = "bullish"
    elif bear_score - bull_score >= 2:
        bull_or_bearish = "bearish"
    else:
        bull_or_bearish = "neutral"

    traded_value = close * volume

    return ScanResult(
        symbol=symbol,
        open_price=open_price,
        close=close,
        live_price=close,
        day_high=day_high,
        day_low=day_low,
        prev_close=prev_close,
        market_cap=None,
        sector=None,
        industry=None,
        ema20=ema20,
        ema50=ema50,
        volume=volume,
        avg_volume_20=avg_volume,
        volume_ratio=(volume / avg_volume) if avg_volume > 0 else 0.0,
        traded_value=traded_value,
        rsi14=rsi14,
        atr14=atr14,
        support_20d=support_20d,
        resistance_20d=resistance_20d,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        oi=None,
        oi_change=None,
        oi_change_pct=oi_change_pct_val,
        volume_rise=volume_rise,
        bull_or_bearish=bull_or_bearish,
        setup=setup,
        breakout_20d=bool(latest["breakout_20d"]),
        pe=fundamentals.get("pe"),
        roe=fundamentals.get("roe"),
        debt_to_equity=fundamentals.get("debt_to_equity"),
        operating_cashflow=fundamentals.get("operating_cashflow"),
    )


def _bucket_exclusive(s: bool, lt: bool, fno: bool) -> int | None:
    if not s and not lt and not fno:
        return None
    if s and not lt and not fno:
        return 0
    if lt and not s and not fno:
        return 1
    if fno and not s and not lt:
        return 2
    if s and lt and not fno:
        return 3
    if s and fno and not lt:
        return 4
    return 5


def run_backtest_counts(
    config: dict[str, Any],
    *,
    universe: str = "fno",
    max_symbols: int = 120,
    period: str = "6mo",
    pause_sec: float = 0.05,
    symbol: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    For each trading day in the Yahoo history window, replay screener rules on EOD data.
    Returns Chart.js-friendly structure with exclusive stacked buckets per day.

    If ``symbol`` is set, only that NSE symbol is backtested (universe/max_symbols ignored).
    """
    warnings: list[str] = []
    rules = config["filters"]
    fno_set = set(config.get("fno_symbols") or [])

    single: str | None = None
    if symbol and symbol.strip():
        single = _normalize_symbol(symbol)
        if not single:
            warnings.append("Symbol filter was empty after cleaning; provide e.g. RELIANCE or SBIN.")
            return _empty_chart_payload(), warnings
        symbols = [single]
    elif universe.strip().lower() == "fno":
        symbols = [s for s in config.get("fno_symbols") or [] if s]
    else:
        symbols = [s for s in config.get("symbols") or [] if s]

    if not symbols:
        warnings.append("No symbols in config for selected universe.")
        return _empty_chart_payload(), warnings

    if single is None:
        symbols = symbols[: max(1, min(max_symbols, 250))]

    per_symbol: dict[str, tuple[pd.DataFrame, dict[str, Any], dict[date, int]]] = {}
    for sym in symbols:
        try:
            hist = yf.Ticker(_to_nse_ticker(sym)).history(
                period=period, interval="1d", auto_adjust=False
            )
            if hist.empty or len(hist) < 55:
                warnings.append(f"{sym}: insufficient history, skipped.")
                continue
            hist = _enrich_history(hist)
            fund = _fetch_fundamentals(sym)
            date_to_i: dict[date, int] = {}
            for i, ts in enumerate(hist.index):
                d = pd.Timestamp(ts).normalize().to_pydatetime().date()
                date_to_i[d] = i
            per_symbol[sym] = (hist, fund, date_to_i)
        except Exception as exc:
            warnings.append(f"{sym}: {exc}")
        time.sleep(pause_sec)

    if not per_symbol:
        warnings.append("No symbol history loaded; cannot backtest.")
        return _empty_chart_payload(), warnings

    all_dates: set[date] = set()
    for _, _, dmap in per_symbol.values():
        all_dates.update(dmap.keys())
    sorted_dates = sorted(all_dates)

    bucket_names = [
        "Swing only",
        "Long-term only",
        "F&O only",
        "Swing + Long-term",
        "Swing + F&O",
        "Mixed / other",
    ]
    counts_per_day: list[list[int]] = [[0] * 6 for _ in sorted_dates]

    for day_i, day in enumerate(sorted_dates):
        for sym, (hist, fund, dmap) in per_symbol.items():
            idx = dmap.get(day)
            if idx is None:
                continue
            row = _scan_result_at_index(sym, hist, idx, fund)
            if row is None:
                continue
            s = is_swing_candidate(row, rules["swing"])
            lt = is_long_term_candidate(row, rules["long_term"])
            fno = is_fno_candidate(row, rules["fno"], fno_set)
            b = _bucket_exclusive(s, lt, fno)
            if b is not None:
                counts_per_day[day_i][b] += 1

    labels = [d.isoformat() for d in sorted_dates]
    datasets = []
    colors = ["#ff5c8d", "#ffd166", "#06d6a0", "#118ab2", "#ff8c42", "#b967ff"]
    for b in range(6):
        datasets.append(
            {
                "label": bucket_names[b],
                "data": [counts_per_day[i][b] for i in range(len(sorted_dates))],
                "backgroundColor": colors[b],
                "borderWidth": 0,
            }
        )

    totals = [sum(counts_per_day[i]) for i in range(len(sorted_dates))]
    payload: dict[str, Any] = {
        "labels": labels,
        "datasets": datasets,
        "meta": {
            "period": period,
            "universe": universe if single is None else "single",
            "symbol_count": len(per_symbol),
            "symbol_filter": single,
            "bucket_legend": bucket_names,
            "disclaimer": (
                "End-of-day Yahoo data; indicators are causal (no lookahead). "
                "Long-term filters use latest fundamentals from Yahoo (not point-in-time). "
                "OI is unavailable historically—F&O rule uses the same logic as when OI data is missing."
            ),
            "daily_totals": totals,
        },
    }
    return payload, warnings


def run_backtest_pnl(
    config: dict[str, Any],
    *,
    universe: str = "fno",
    max_symbols: int = 120,
    period: str = "6mo",
    pause_sec: float = 0.05,
    symbol: str | None = None,
    hold_days: int = 5,
) -> tuple[dict[str, Any], list[str]]:
    """
    Simulated trade backtest with basic risk rules.
    Entry: next day open after bullish signal.
    Exit: target_1 hit, stop_loss hit, or max holding period close.
    """
    warnings: list[str] = []
    rules = config["filters"]
    fno_set = set(config.get("fno_symbols") or [])

    single: str | None = None
    if symbol and symbol.strip():
        single = _normalize_symbol(symbol)
        if not single:
            warnings.append("Symbol filter was empty after cleaning; provide e.g. RELIANCE or SBIN.")
            return _empty_pnl_payload(), warnings
        symbols = [single]
    elif universe.strip().lower() == "fno":
        symbols = [s for s in config.get("fno_symbols") or [] if s]
    else:
        symbols = [s for s in config.get("symbols") or [] if s]

    if not symbols:
        warnings.append("No symbols in config for selected universe.")
        return _empty_pnl_payload(), warnings

    if single is None:
        symbols = symbols[: max(1, min(max_symbols, 250))]

    per_symbol: dict[str, tuple[pd.DataFrame, dict[str, Any], dict[date, int]]] = {}
    for sym in symbols:
        try:
            hist = yf.Ticker(_to_nse_ticker(sym)).history(
                period=period, interval="1d", auto_adjust=False
            )
            if hist.empty or len(hist) < 55:
                warnings.append(f"{sym}: insufficient history, skipped.")
                continue
            hist = _enrich_history(hist)
            fund = _fetch_fundamentals(sym)
            date_to_i: dict[date, int] = {}
            for i, ts in enumerate(hist.index):
                d = pd.Timestamp(ts).normalize().to_pydatetime().date()
                date_to_i[d] = i
            per_symbol[sym] = (hist, fund, date_to_i)
        except Exception as exc:
            warnings.append(f"{sym}: {exc}")
        time.sleep(pause_sec)

    if not per_symbol:
        warnings.append("No symbol history loaded; cannot backtest.")
        return _empty_pnl_payload(), warnings

    daily_factors: dict[date, float] = {}
    trades: list[float] = []
    max_hold = max(1, hold_days)

    for sym, (hist, fund, _) in per_symbol.items():
        i = 50
        while i < len(hist) - 1:
            row = _scan_result_at_index(sym, hist, i, fund)
            if row is None:
                i += 1
                continue

            s = is_swing_candidate(row, rules["swing"])
            lt = is_long_term_candidate(row, rules["long_term"])
            fno = is_fno_candidate(row, rules["fno"], fno_set)
            is_signal = (s or lt or fno) and row.bull_or_bearish == "bullish"
            if not is_signal:
                i += 1
                continue

            entry_i = i + 1
            if entry_i >= len(hist):
                break
            entry_open = hist.iloc[entry_i].get("Open")
            entry_close = hist.iloc[entry_i].get("Close")
            if pd.isna(entry_open) and pd.isna(entry_close):
                i += 1
                continue
            entry_price = float(entry_open) if pd.notna(entry_open) else float(entry_close)
            if entry_price <= 0:
                i += 1
                continue

            stop = float(row.stop_loss)
            target = float(row.target_1)
            exit_i = min(len(hist) - 1, entry_i + max_hold)
            exit_price = float(hist.iloc[exit_i]["Close"])
            for j in range(entry_i, exit_i + 1):
                lo = hist.iloc[j].get("Low")
                hi = hist.iloc[j].get("High")
                if pd.notna(lo) and float(lo) <= stop:
                    exit_i = j
                    exit_price = stop
                    break
                if pd.notna(hi) and float(hi) >= target:
                    exit_i = j
                    exit_price = target
                    break

            trade_ret = (exit_price - entry_price) / entry_price
            trades.append(trade_ret)

            exit_day = pd.Timestamp(hist.index[exit_i]).normalize().to_pydatetime().date()
            daily_factors[exit_day] = daily_factors.get(exit_day, 1.0) * (1.0 + trade_ret)
            i = exit_i + 1

    if not daily_factors:
        warnings.append("No completed bullish trades for selected settings.")
        return _empty_pnl_payload(), warnings

    sorted_days = sorted(daily_factors.keys())
    labels = [d.isoformat() for d in sorted_days]
    equity: list[float] = []
    v = 100.0
    peak = v
    max_drawdown = 0.0
    for d in sorted_days:
        v *= daily_factors[d]
        equity.append(v)
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak
            max_drawdown = min(max_drawdown, dd)

    total_trades = len(trades)
    wins = sum(1 for r in trades if r > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades else 0.0
    avg_trade = (sum(trades) / total_trades * 100.0) if total_trades else 0.0
    cumulative = (equity[-1] / 100.0 - 1.0) * 100.0 if equity else 0.0

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
            "trades": total_trades,
            "win_rate_pct": round(win_rate, 2),
            "avg_trade_return_pct": round(avg_trade, 2),
            "cumulative_return_pct": round(cumulative, 2),
            "max_drawdown_pct": round(max_drawdown * 100.0, 2),
            "disclaimer": (
                "Simplified historical simulation (next-day entry, fixed stop/target/max-hold). "
                "Does not include slippage, costs, liquidity or point-in-time fundamentals."
            ),
        },
    }
    return payload, warnings


def _empty_chart_payload() -> dict[str, Any]:
    return {
        "labels": [],
        "datasets": [],
        "meta": {
            "period": "6mo",
            "universe": "",
            "symbol_count": 0,
            "bucket_legend": [],
            "disclaimer": "",
            "daily_totals": [],
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
