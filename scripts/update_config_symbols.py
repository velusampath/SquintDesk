from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from typing import Iterable

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yml"
CONFIG_EXAMPLE_PATH = PROJECT_ROOT / "config.example.yml"


def _safe_symbol(value: str) -> str:
    cleaned = value.strip().upper().replace(".NS", "").replace(".BO", "")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in ("-", "&"))
    return cleaned


def _read_csv_symbols(text: str, possible_columns: list[str]) -> list[str]:
    buffer = StringIO(text)
    reader = csv.DictReader(buffer)
    found: list[str] = []
    for row in reader:
        for col in possible_columns:
            val = row.get(col)
            if not val:
                continue
            symbol = _safe_symbol(val)
            if symbol:
                found.append(symbol)
                break
    return found


def fetch_nse_equity_symbols(session: requests.Session) -> list[str]:
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    res = session.get(url, timeout=60)
    res.raise_for_status()
    return _read_csv_symbols(res.text, ["SYMBOL"])


def fetch_bse_equity_symbols(session: requests.Session) -> list[str]:
    url = "https://api.bseindia.com/BseIndiaAPI/api/LitsOfScripCSVDownload/w"
    params = {"segment": "Equity", "status": "Active", "Group": "", "Scripcode": ""}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bseindia.com/corporates/List_Scrips.html",
    }
    res = session.get(url, params=params, headers=headers, timeout=90)
    res.raise_for_status()
    return _read_csv_symbols(res.text, ["Security Id", "SecurityId", "SCRIP_ID", "Scrip Id"])


def fetch_nse_fno_symbols(session: requests.Session) -> list[str]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/market-data/live-equity-market",
    }
    res = session.get("https://www.nseindia.com/api/underlying-information", headers=headers, timeout=60)
    res.raise_for_status()
    payload = res.json()
    underlying_list = payload.get("data", {}).get("UnderlyingList", [])
    symbols = [_safe_symbol(item.get("symbol", "")) for item in underlying_list]
    symbols = [s for s in symbols if s]
    if not symbols:
        raise RuntimeError("Unable to parse NSE F&O symbols from underlying-information API.")
    return symbols


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({v for v in values if v})


def update_config_file(config_path: Path, symbols: list[str], fno_symbols: list[str]) -> None:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["symbols"] = symbols
    data["fno_symbols"] = fno_symbols
    config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    nse_symbols = fetch_nse_equity_symbols(session)
    bse_symbols = fetch_bse_equity_symbols(session)
    fno_symbols = fetch_nse_fno_symbols(session)

    all_symbols = _unique_sorted([*nse_symbols, *bse_symbols])
    fno_symbols = _unique_sorted(fno_symbols)

    update_config_file(CONFIG_PATH, all_symbols, fno_symbols)
    update_config_file(CONFIG_EXAMPLE_PATH, all_symbols, fno_symbols)

    print(f"Updated {CONFIG_PATH.name} and {CONFIG_EXAMPLE_PATH.name}")
    print(f"Total symbols: {len(all_symbols)}")
    print(f"Total fno_symbols: {len(fno_symbols)}")


if __name__ == "__main__":
    main()

