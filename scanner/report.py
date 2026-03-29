from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import ScanResult


def _table(rows: list[ScanResult]) -> str:
    if not rows:
        return "_No candidates matched filters._\n"

    header = "| Symbol | Live | Setup | Entry | SL | T1 | T2 | RSI14 | ATR14 | Vol Ratio |"
    sep = "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"| {r.symbol} | {r.live_price:.2f} | {r.setup} | {r.entry_price:.2f} | {r.stop_loss:.2f} | "
            f"{r.target_1:.2f} | {r.target_2:.2f} | {r.rsi14:.2f} | {r.atr14:.2f} | {r.volume_ratio:.2f} |"
        )
    return "\n".join(lines) + "\n"


def build_markdown(scan_data: dict[str, list[ScanResult]]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    chunks = [f"# Daily Stock Scan Report\n\nGenerated: {now}\n"]

    chunks.append("## Swing Candidates\n")
    chunks.append(_table(scan_data["swing"]))

    chunks.append("## Long-Term Candidates\n")
    chunks.append(_table(scan_data["long_term"]))

    chunks.append("## F&O Candidates\n")
    chunks.append(_table(scan_data["fno"]))

    return "\n".join(chunks)


def save_markdown(content: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"report_{datetime.now().strftime('%Y-%m-%d')}.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path

