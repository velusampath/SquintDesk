# Python Stock Scanner (NSE)

Daily scanner for:
- Swing trading
- Long-term investing
- F&O watchlist
- Live market snapshot with trade plan columns (`Entry`, `Stop Loss`, `Target 1`, `Target 2`)

It pulls NSE symbols from Yahoo Finance (`.NS`) and applies configurable filters.

## 1) Setup

```bash
cd python-stock-scanner
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Quick Commands (Copy/Paste)

Run these from:

`D:\SeaBeast\seabeast-ui\python-stock-scanner`

Activate venv:

```powershell
.venv\Scripts\activate
```

Run CLI scanner:

```powershell
python main.py --config config.yml --category all
```

Run browser app:

```powershell
python web_app.py
```

Run market-specific web apps:

```powershell
python run_ind_web.py   # IND on localhost:5000
python run_us_web.py    # US on localhost:5001
```

Auto-reload is enabled for the web app. Save any file change and Flask will restart automatically.

Open in browser:

`http://localhost:5000`

Dedicated daily F&O page:

`http://localhost:5000/fno-dashboard`

If `python` is not in PATH, use:

```powershell
& "$env:LocalAppData\Programs\Python\Python312\python.exe" main.py --config config.yml --category all
& "$env:LocalAppData\Programs\Python\Python312\python.exe" web_app.py
```

## 2) Configure

```bash
copy config.example.yml config.yml
```

Edit `config.yml`:
- `symbols`: your full watchlist
- `fno_symbols`: F&O-eligible subset
- `filters`: strategy rules (swing/long_term/fno)

## 3) Run

Scan all categories:

```bash
python main.py --config config.yml --category all
```

Scan one category:

```bash
python main.py --config config.yml --category swing
python main.py --config config.yml --category long_term
python main.py --config config.yml --category fno
```

## 3A) Run in browser

Start web app:

```bash
python web_app.py
```

Or market-specific launchers:

```bash
python run_ind_web.py
python run_us_web.py
```

Open:

`http://localhost:5000`

From the page, choose category and click **Run Scan**.
UI now shows:
- All scanned symbols with live snapshot
- Setup type (`breakout_long` / `pullback_long`)
- Suggested `Entry`, `Stop Loss`, `Target 1`, `Target 2`
- Screenshot upload option with separate **Analyze Screenshot** button for deep single-symbol analysis
- Screenshot analysis auto-detects symbol from filename and OCR text (fallback to manual symbol input)
- F&O daily logic uses OI change + volume rise + trend and adds `bull_or_bearish` in table

Reports are saved under `reports/`:
- `swing_YYYY-MM-DD.csv`
- `long_term_YYYY-MM-DD.csv`
- `fno_YYYY-MM-DD.csv`
- `report_YYYY-MM-DD.md`

## 4) Suggested daily workflow

1. Update `symbols` watchlist weekly.
2. Run scan after market close.
3. Review `report_YYYY-MM-DD.md`.
4. Pick top candidates with best RR and structure.

## 5) Optional Windows Task Scheduler automation

Run daily at 6:30 PM:

```powershell
schtasks /Create /SC DAILY /TN "NSE Daily Scanner" /TR "cmd /c cd /d D:\SeaBeast\seabeast-ui\python-stock-scanner && .venv\Scripts\python.exe main.py --config config.yml --category all" /ST 18:30
```

## Notes

- This is a screening tool, not financial advice.
- Always validate setup on chart before placing trades.
- Data quality depends on Yahoo Finance availability.

