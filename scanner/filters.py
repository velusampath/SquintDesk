from __future__ import annotations

from typing import Any

from .models import ScanResult

# Shared with StockScreener sentiment scoring — keep defaults identical.
DEFAULT_MIN_OI_CHANGE_PCT = 1.5


def is_swing_candidate(row: ScanResult, rules: dict[str, Any]) -> bool:
    return (
        row.close >= rules["min_price"]
        and row.traded_value >= rules["min_traded_value"]
        and row.close > row.ema20
        and row.ema20 > row.ema50
        and row.volume_ratio >= rules["min_volume_ratio"]
        and row.breakout_20d
        and rules["rsi_min"] <= row.rsi14 <= rules["rsi_max"]
    )


def is_long_term_candidate(row: ScanResult, rules: dict[str, Any]) -> bool:
    if row.pe is None or row.roe is None or row.debt_to_equity is None or row.operating_cashflow is None:
        return False

    return (
        row.close >= rules["min_price"]
        and row.traded_value >= rules["min_traded_value"]
        and row.pe > 0
        and row.pe <= rules["max_pe"]
        and row.roe >= rules["min_roe"]
        and row.debt_to_equity <= rules["max_debt_to_equity"]
        and row.operating_cashflow >= rules["min_operating_cashflow"]
    )


def is_fno_candidate(row: ScanResult, rules: dict[str, Any], fno_symbol_set: set[str]) -> bool:
    require_oi_data = rules.get("require_oi_data", False)
    min_oi_change_pct = float(rules.get("min_oi_change_pct", DEFAULT_MIN_OI_CHANGE_PCT))
    oi_present = row.oi_change_pct is not None
    oi_ok = oi_present and abs(row.oi_change_pct or 0) >= min_oi_change_pct
    if not require_oi_data:
        oi_ok = oi_ok or not oi_present

    return (
        row.symbol in fno_symbol_set
        and row.close >= rules["min_price"]
        and row.traded_value >= rules["min_traded_value"]
        and row.atr14 >= rules["min_atr"]
        and row.volume_ratio >= rules["min_volume_ratio"]
        and row.volume_rise
        and oi_ok
        and row.bull_or_bearish != "neutral"
    )

