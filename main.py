from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from scanner.report import build_markdown, save_markdown
from scanner.screener import StockScreener


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily NSE stock scanner for swing/long-term/F&O.")
    parser.add_argument(
        "--config",
        default="config.yml",
        help="Path to YAML config file. Default: config.yml",
    )
    parser.add_argument(
        "--category",
        choices=["all", "swing", "long_term", "fno"],
        default="all",
        help="Scan one category or all.",
    )
    parser.add_argument(
        "--output",
        default="reports",
        help="Directory to save CSV and markdown reports. Default: reports",
    )
    return parser.parse_args()


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.yml to config.yml and update symbols."
        )
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    screener = StockScreener(config)
    results = screener.run(category=args.category)

    output_dir = Path(args.output)
    csv_map = screener.save_csv(results, output_dir)
    md_text = build_markdown(results)
    md_path = save_markdown(md_text, output_dir)

    print("Scan completed.")
    for category, csv_path in csv_map.items():
        print(f"- {category}: {csv_path}")
    print(f"- report: {md_path}")


if __name__ == "__main__":
    main()

