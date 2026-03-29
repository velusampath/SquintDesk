from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data_source import YFinanceDataSource
from .filters import DEFAULT_MIN_OI_CHANGE_PCT, is_fno_candidate, is_long_term_candidate, is_swing_candidate
from .models import ScanResult


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift(1)).abs()
    low_close = (df["Low"] - df["Close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


class StockScreener:
    def __init__(self, config: dict[str, Any]) -> None:
        market = config.get("market", {})
        self.symbols = config["symbols"]
        self.rules = config["filters"]
        self.fno_symbols = set(config.get("fno_symbols", []))
        self.data_source = YFinanceDataSource(
            period=market.get("history_period", "6mo"),
            interval=market.get("interval", "1d"),
        )
        self.warnings: list[str] = []
        self.last_scanned: list[ScanResult] = []

    @staticmethod
    def _build_trade_levels(close: float, atr14: float, ema20: float, resistance_20d: float) -> tuple[str, float, float, float, float]:
        # Use breakout or pullback style levels based on whether price is near 20D resistance.
        if close >= resistance_20d * 0.995:
            setup = "breakout_long"
            entry = max(close, resistance_20d * 1.002)
            stop = max(0.01, min(ema20, entry - (1.2 * atr14)))
        else:
            setup = "pullback_long"
            entry = max(close, ema20 * 1.003)
            stop = max(0.01, entry - (1.0 * atr14))

        risk = max(0.01, entry - stop)
        target_1 = entry + (1.5 * risk)
        target_2 = entry + (2.5 * risk)
        return setup, entry, stop, target_1, target_2

    def _build_result(self, symbol: str) -> ScanResult:
        symbol_data = self.data_source.fetch(symbol)
        df = symbol_data.history.copy()

        df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["rsi14"] = _rsi(df["Close"], 14)
        df["atr14"] = _atr(df, 14)
        df["avg_volume_20"] = df["Volume"].rolling(20).mean()
        df["support_20d"] = df["Low"].shift(1).rolling(20).min()
        df["resistance_20d"] = df["High"].shift(1).rolling(20).max()
        df["breakout_20d"] = df["Close"] > df["High"].shift(1).rolling(20).max()

        latest = df.iloc[-1]
        open_price = float(latest["Open"]) if pd.notna(latest["Open"]) else float(latest["Close"])
        close = float(latest["Close"])
        volume = float(latest["Volume"])
        avg_volume = float(latest["avg_volume_20"]) if pd.notna(latest["avg_volume_20"]) else 0.0
        atr14 = float(latest["atr14"]) if pd.notna(latest["atr14"]) else 0.0
        ema20 = float(latest["ema20"])
        resistance_20d = float(latest["resistance_20d"]) if pd.notna(latest["resistance_20d"]) else close
        support_20d = float(latest["support_20d"]) if pd.notna(latest["support_20d"]) else close

        setup, entry_price, stop_loss, target_1, target_2 = self._build_trade_levels(
            close=close,
            atr14=atr14,
            ema20=ema20,
            resistance_20d=resistance_20d,
        )
        live_price = symbol_data.market_data.get("live_price")
        day_high = symbol_data.market_data.get("day_high")
        day_low = symbol_data.market_data.get("day_low")
        prev_close = symbol_data.market_data.get("prev_close")
        oi = symbol_data.derivative_data.get("oi")
        oi_change = symbol_data.derivative_data.get("oi_change")
        oi_change_pct = symbol_data.derivative_data.get("oi_change_pct")
        oi_change_pct_val = float(oi_change_pct) if oi_change_pct is not None else None
        volume_rise = volume > avg_volume and avg_volume > 0

        bull_score = 0
        bear_score = 0
        if close > ema20 > float(latest["ema50"]):
            bull_score += 2
        elif close < ema20 < float(latest["ema50"]):
            bear_score += 2

        if close > open_price:
            bull_score += 1
        elif close < open_price:
            bear_score += 1

        if volume_rise:
            if close >= (float(prev_close) if prev_close is not None else close):
                bull_score += 1
            else:
                bear_score += 1

        # Same threshold as is_fno_candidate (filters.fno.min_oi_change_pct).
        fno_rules = self.rules.get("fno") or {}
        oi_pct_th = float(fno_rules.get("min_oi_change_pct", DEFAULT_MIN_OI_CHANGE_PCT))

        if oi_change_pct_val is not None:
            if oi_change_pct_val > oi_pct_th and close > (float(prev_close) if prev_close is not None else close):
                bull_score += 2
            elif oi_change_pct_val > oi_pct_th and close < (float(prev_close) if prev_close is not None else close):
                bear_score += 2
            elif oi_change_pct_val < -oi_pct_th and close > (float(prev_close) if prev_close is not None else close):
                bull_score += 1
            elif oi_change_pct_val < -oi_pct_th and close < (float(prev_close) if prev_close is not None else close):
                bear_score += 1

        if bull_score - bear_score >= 2:
            bull_or_bearish = "bullish"
        elif bear_score - bull_score >= 2:
            bull_or_bearish = "bearish"
        else:
            bull_or_bearish = "neutral"

        return ScanResult(
            symbol=symbol,
            open_price=open_price,
            close=close,
            live_price=float(live_price) if live_price is not None else close,
            day_high=float(day_high) if day_high is not None else close,
            day_low=float(day_low) if day_low is not None else close,
            prev_close=float(prev_close) if prev_close is not None else close,
            market_cap=symbol_data.company_details.get("market_cap"),
            sector=symbol_data.company_details.get("sector"),
            industry=symbol_data.company_details.get("industry"),
            ema20=ema20,
            ema50=float(latest["ema50"]),
            volume=volume,
            avg_volume_20=avg_volume,
            volume_ratio=(volume / avg_volume) if avg_volume > 0 else 0.0,
            traded_value=close * volume,
            rsi14=float(latest["rsi14"]) if pd.notna(latest["rsi14"]) else 0.0,
            atr14=atr14,
            support_20d=support_20d,
            resistance_20d=resistance_20d,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            oi=float(oi) if oi is not None else None,
            oi_change=float(oi_change) if oi_change is not None else None,
            oi_change_pct=oi_change_pct_val,
            volume_rise=volume_rise,
            bull_or_bearish=bull_or_bearish,
            setup=setup,
            breakout_20d=bool(latest["breakout_20d"]),
            pe=symbol_data.fundamentals.get("pe"),
            roe=symbol_data.fundamentals.get("roe"),
            debt_to_equity=symbol_data.fundamentals.get("debt_to_equity"),
            operating_cashflow=symbol_data.fundamentals.get("operating_cashflow"),
        )

    def run(self, category: str = "all") -> dict[str, list[ScanResult]]:
        self.warnings = []
        self.last_scanned = []
        scan_symbols = self.fno_symbols if category == "fno" else self.symbols
        results: list[ScanResult] = []
        for symbol in scan_symbols:
            try:
                results.append(self._build_result(symbol))
            except Exception as exc:
                warning = f"skipped {symbol}: {exc}"
                self.warnings.append(warning)
                print(f"[WARN] {warning}")

        self.last_scanned = results
        output = {"swing": [], "long_term": [], "fno": []}
        for row in results:
            if category in ("all", "swing") and is_swing_candidate(row, self.rules["swing"]):
                output["swing"].append(row)
            if category in ("all", "long_term") and is_long_term_candidate(row, self.rules["long_term"]):
                output["long_term"].append(row)
            if category in ("all", "fno") and is_fno_candidate(row, self.rules["fno"], self.fno_symbols):
                output["fno"].append(row)

        for key in output:
            output[key].sort(key=lambda x: x.volume_ratio, reverse=True)
        return output

    def analyze_symbol(self, symbol: str) -> ScanResult:
        """Return a full analysis row for one symbol without category filtering."""
        return self._build_result(symbol)

    @staticmethod
    def to_dataframe(rows: list[ScanResult]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([row.as_dict() for row in rows])

    @staticmethod
    def save_csv(scan_data: dict[str, list[ScanResult]], output_dir: Path) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        file_map: dict[str, Path] = {}
        stamp = datetime.now().strftime("%Y-%m-%d")
        for category, rows in scan_data.items():
            path = output_dir / f"{category}_{stamp}.csv"
            StockScreener.to_dataframe(rows).to_csv(path, index=False)
            file_map[category] = path
        return file_map

