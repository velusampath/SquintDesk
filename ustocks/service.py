from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf


US_UNIVERSES: dict[str, list[str]] = {
    "mega": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "GOOGL",
        "TSLA",
        "AVGO",
        "JPM",
        "NFLX",
        "AMD",
        "PLTR",
    ],
    "tech": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "GOOGL",
        "TSLA",
        "NFLX",
        "AMD",
        "ORCL",
        "CRM",
        "INTC",
    ],
}


def scan_us(universe: str = "mega") -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    symbols = US_UNIVERSES.get(universe, US_UNIVERSES["mega"])
    rows: list[dict[str, Any]] = []
    for sym in symbols:
        try:
            df = yf.Ticker(sym).history(period="6mo", interval="1d", auto_adjust=False)
            if df.empty:
                warnings.append(f"{sym}: no history")
                continue
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            close = float(latest["Close"])
            open_price = float(latest["Open"])
            high = float(latest["High"])
            day_change_pct = ((close - float(prev["Close"])) / float(prev["Close"])) * 100 if float(prev["Close"]) else 0.0
            trend = "Bullish" if close >= open_price else "Bearish"
            rows.append(
                {
                    "Symbol": sym,
                    "Open": round(open_price, 2),
                    "High": round(high, 2),
                    "Close": round(close, 2),
                    "Day %": round(day_change_pct, 2),
                    "Trend": trend,
                }
            )
        except Exception as exc:
            warnings.append(f"{sym}: {exc}")
    return pd.DataFrame(rows), warnings

