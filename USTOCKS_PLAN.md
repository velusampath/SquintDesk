# Market Split Plan (IND + US)

## Current state
- India logic is centralized in `web_app.py` + `scanner/*`.
- `main.py` remains unchanged and still points to existing scanner flow.

## Target structure
- `indstocks/` for India-specific app modules.
- `ustocks/` for US-specific app modules.
- Keep shared primitives reusable where possible.

## Implemented in phase 1
- Added `indstocks/web_app.py` as India app entrypoint wrapper.
- Added `ustocks/web_app.py` scaffold routes:
  - `/us/scanner`
  - `/us/fno`
  - `/us/backtest`
  - `/us/api/scanner`
- Added `ustocks/service.py` to scan US symbols via Yahoo.
- Added `run_us_web.py` to run US app on port `5001`.

## Implemented in phase 2
- Upgraded `ustocks/web_app.py` to full working modules:
  - US Scanner page + `/us/api/scanner`
  - US F&O page + `/us/api/fno-dashboard` (all/bull/bear)
  - US Backtest page + `/us/api/backtest` (count + pnl modes)
- Added `ustocks/backtest.py`:
  - `run_backtest_counts_us`
  - `run_backtest_pnl_us`
- Added in-memory API cache for US routes.
- Added `run_ind_web.py` (India launcher) and kept `main.py` unchanged.

## Next implementation phase (phase 3)
1. Extract shared HTML/CSS helpers to a common module.
2. Move India scanner/backtest engines into `indstocks/scanner/`.
3. Add top-level market tabs in India app (`IND Stocks` / `US Stocks`) using one host.
4. Add separate config files:
   - `indstocks/config_ind.yml`
   - `ustocks/config_us.yml`
5. Add a single launcher that can serve both markets under one host.

