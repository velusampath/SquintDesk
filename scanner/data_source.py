from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import pandas as pd
import requests
import yfinance as yf


@dataclass
class SymbolData:
    symbol: str
    history: pd.DataFrame
    fundamentals: dict[str, Any]
    market_data: dict[str, Any]
    company_details: dict[str, Any]
    derivative_data: dict[str, Any]


class YFinanceDataSource:
    def __init__(self, period: str = "6mo", interval: str = "1d", retries: int = 2) -> None:
        self.period = period
        self.interval = interval
        self.retries = retries
        self.http = requests.Session()
        self.http.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.nseindia.com/",
            }
        )

    def _to_nse_ticker(self, symbol: str) -> str:
        if symbol.endswith(".NS"):
            return symbol
        return f"{symbol}.NS"

    def _warm_nse_session(self) -> None:
        try:
            self.http.get("https://www.nseindia.com/", timeout=20)
        except Exception:
            pass

    @staticmethod
    def _parse_derivative_oi(payload: dict[str, Any]) -> dict[str, Any]:
        # Prefer near-month stock futures record if present.
        stocks = payload.get("stocks", [])
        for item in stocks:
            metadata = item.get("metadata", {})
            inst = str(metadata.get("instrumentType", "")).upper()
            if inst not in {"FUTSTK", "FUTIDX"}:
                continue
            trade = item.get("marketDeptOrderBook", {}).get("tradeInfo", {})
            return {
                "oi": trade.get("openInterest"),
                "oi_change": trade.get("changeinOpenInterest"),
                "oi_change_pct": trade.get("pchangeinOpenInterest"),
            }

        # Fallback to top-level tradeInfo if futures rows are absent.
        trade = payload.get("marketDeptOrderBook", {}).get("tradeInfo", {})
        return {
            "oi": trade.get("openInterest"),
            "oi_change": trade.get("changeinOpenInterest"),
            "oi_change_pct": trade.get("pchangeinOpenInterest"),
        }

    def _fetch_derivative_data(self, symbol: str) -> dict[str, Any]:
        self._warm_nse_session()
        url = "https://www.nseindia.com/api/quote-derivative"
        try:
            resp = self.http.get(url, params={"symbol": symbol}, timeout=30)
            if resp.status_code != 200:
                return {"oi": None, "oi_change": None, "oi_change_pct": None}
            payload = resp.json()
            return self._parse_derivative_oi(payload)
        except Exception:
            return {"oi": None, "oi_change": None, "oi_change_pct": None}

    def fetch(self, symbol: str) -> SymbolData:
        ticker = yf.Ticker(self._to_nse_ticker(symbol))
        history = pd.DataFrame()
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                history = ticker.history(period=self.period, interval=self.interval, auto_adjust=False)
                if not history.empty:
                    break
            except Exception as exc:
                last_error = exc
            time.sleep(1 + attempt)

        if history.empty:
            if last_error is not None:
                raise ValueError(f"No price history for symbol: {symbol} ({last_error})")
            raise ValueError(f"No price history for symbol: {symbol}")

        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            info = {}

        fundamentals = {
            "pe": info.get("trailingPE") or info.get("forwardPE"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "operating_cashflow": info.get("operatingCashflow"),
        }
        market_data = {
            "live_price": info.get("regularMarketPrice"),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "prev_close": info.get("previousClose"),
        }
        company_details = {
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
        }
        derivative_data = self._fetch_derivative_data(symbol)
        return SymbolData(
            symbol=symbol,
            history=history,
            fundamentals=fundamentals,
            market_data=market_data,
            company_details=company_details,
            derivative_data=derivative_data,
        )

