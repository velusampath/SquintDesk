from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScanResult:
    symbol: str
    open_price: float
    close: float
    live_price: float
    day_high: float
    day_low: float
    prev_close: float
    market_cap: float | None
    sector: str | None
    industry: str | None
    ema20: float
    ema50: float
    volume: float
    avg_volume_20: float
    volume_ratio: float
    traded_value: float
    rsi14: float
    atr14: float
    support_20d: float
    resistance_20d: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    oi: float | None
    oi_change: float | None
    oi_change_pct: float | None
    volume_rise: bool
    bull_or_bearish: str
    setup: str
    breakout_20d: bool
    pe: float | None = None
    roe: float | None = None
    debt_to_equity: float | None = None
    operating_cashflow: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open": round(self.open_price, 2),
            "close": round(self.close, 2),
            "live_price": round(self.live_price, 2),
            "day_high": round(self.day_high, 2),
            "day_low": round(self.day_low, 2),
            "prev_close": round(self.prev_close, 2),
            "market_cap": round(self.market_cap, 2) if self.market_cap is not None else None,
            "sector": self.sector,
            "industry": self.industry,
            "ema20": round(self.ema20, 2),
            "ema50": round(self.ema50, 2),
            "volume": int(self.volume),
            "avg_volume_20": int(self.avg_volume_20),
            "volume_ratio": round(self.volume_ratio, 2),
            "traded_value": round(self.traded_value, 2),
            "rsi14": round(self.rsi14, 2),
            "atr14": round(self.atr14, 2),
            "support_20d": round(self.support_20d, 2),
            "resistance_20d": round(self.resistance_20d, 2),
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "target_1": round(self.target_1, 2),
            "target_2": round(self.target_2, 2),
            "oi": round(self.oi, 2) if self.oi is not None else None,
            "oi_change": round(self.oi_change, 2) if self.oi_change is not None else None,
            "oi_change_pct": round(self.oi_change_pct, 2) if self.oi_change_pct is not None else None,
            "volume_rise": self.volume_rise,
            "bull_or_bearish": self.bull_or_bearish,
            "setup": self.setup,
            "breakout_20d": self.breakout_20d,
            "pe": round(self.pe, 2) if self.pe is not None else None,
            "roe": round(self.roe, 2) if self.roe is not None else None,
            "debt_to_equity": round(self.debt_to_equity, 2) if self.debt_to_equity is not None else None,
            "operating_cashflow": (
                round(self.operating_cashflow, 2) if self.operating_cashflow is not None else None
            ),
        }

