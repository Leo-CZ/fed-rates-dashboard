# Fed Rates Dashboard

The dashboard presents
- U.S. Treasury rate data for 2y/10y/30y from Federal Reserve Bank of St. Louis ([FRED](https://fred.stlouisfed.org/)), including
  - nominal rates
  - curve speards
  - breakeven rates and implied real rates
- A possible major event like FOMC/Key Speeches/Pivot events
  - the minute level yield rates for 10y/30y from Yahop finance
- A snapshot of CME FedWatch probability table

## FRED download links
| Series | Description | Frequency | FRED page | CSV download from 2019-01-01 |
|---|---|---|---|---|
| DGS2 | 2-Year Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DGS2) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2&cosd=2019-01-01) |
| DGS10 | 10-Year Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DGS10) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2019-01-01) |
| DGS30 | 30-Year Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DGS30) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS30&cosd=2019-01-01) |
| T10YIE | 10-Year Breakeven Inflation Rate | Daily | [Series](https://fred.stlouisfed.org/series/T10YIE) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10YIE&cosd=2019-01-01) |
| DFII10 | 10-Year Inflation-Indexed Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DFII10) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10&cosd=2019-01-01) |
| DFII30 | 30-Year Inflation-Indexed Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DFII30) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII30&cosd=2019-01-01) |

## Policy-event and FedWatch sources

The CME probabilities are a point-in-time market snapshot and should not be treated as a Federal Reserve forecast.

## Yahoo Finance intraday event data

The first intraday event study covers Chair Kevin Warsh's Jackson Hole keynote at `2026-08-28 10:00 ET`. Its requested ±1-trading-day window is August 27, August 28, and August 31.

- Yield indices: `^TNX` (10Y) and `^TYX` (30Y).
- Frequency/value: one-minute Yahoo Finance open, high, low, and close observations, with both UTC and Eastern Time timestamps.
- Missing values: no interpolation or forward-fill.

## Calculations

- `2s10s = DGS10 - DGS2`
- `2s30s = DGS30 - DGS2`
- `10s30s = DGS30 - DGS10`
- `10Y implied real = DGS10 - T10YIE`
- `30Y breakeven = DGS30 - DFII30`

Every calculation uses same-date observations. If any required input is missing, the result is `NA`. No interpolation, forward-fill, or other imputation is performed.

Breakeven inflation includes inflation expectations plus liquidity and risk premia; it should not be interpreted as a pure inflation forecast.

## Interactive dashboard

Open [`rate_dashboard.html`](rate_dashboard.html) in a browser. The dashboard provides:

1. 2Y, 10Y, and 30Y nominal Treasury yields in one plot.
2. 2s10s, 2s30s, and 10s30s spreads.
3. 10Y/30Y breakeven inflation and 10Y/30Y implied real yields in one plot.
4. Custom start/end dates and zoom controls on the first control row, followed by shared quick-range buttons for 1 week, 1 month, 6 months, YTD, 1 year, 3 years, 5 years, and all data. The quick ranges are also available below each plot.
5. Button and mouse-wheel zooming plus horizontal mouse-drag panning, synchronized across all plots.
6. Toggleable series, cross-series hover values, and separately toggleable markers for major policy events, FOMC meetings, and minutes releases.
7. A CME FedWatch probability table whose title records the exact snapshot date and time.
8.  Separate, synchronized 10Y and 30Y yield-index candlestick plots, with open/high/low/close, a marker at the event timestamp, and selectable full-window, event-day, and immediate-reaction views.

## Updating the data

Run the commands below from the repository root. The scripts derive every data path relative to their own locations; no machine-specific folder configuration is required.

Requirements:

- Python 3.10 or newer.
- PowerShell 7 or newer for the FRED refresh script.
- The `curl` command-line application for bounded FRED downloads.
- Internet access for FRED and Yahoo updates.

Install the Python dependency once:

```powershell
python -m pip install -r requirements.txt
```

### 1. Update FRED Treasury and inflation data

```powershell
pwsh -File ./code/refresh_rate_data.ps1
python ./code/build_rate_dashboard.py
```

- On the first run, the refresh downloads DGS2, DGS10, DGS30, T10YIE, DFII10, and DFII30 beginning on `2019-01-01`.
- On later runs, each series is requested independently beginning one calendar day after its final stored row.

### 2. Update CME FedWatch probabilities

There is no credential-free supported CME API used by this repository. CME offers an official subscription [FedWatch API](https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html); this repository instead updates the snapshot from the public [CME FedWatch Tool](https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html) as follows:

```powershell
python ./code/update_cme_fedwatch.py `
  --input ./data/cme_fedwatch/fedwatch_export.csv `
  --current-target 350-375 `
  --snapshot-time "2026-08-31T06:53:08-05:00" `
  --acquisition-method browser_assisted
python ./code/build_rate_dashboard.py
```

### 3. Update Yahoo one-minute event data

```powershell
python ./code/download_yahoo_event_data.py
python ./code/build_rate_dashboard.py
```

The downloader retains UTC source time, supplies the corresponding Eastern Time timestamp and session date, and never fills missing minutes.

### Rebuild without downloading

If the raw inputs are already current, rebuild all derived files and the dashboard with:

```powershell
python ./code/build_rate_dashboard.py
```
## Disclaimer

This repository is provided for educational, record-keeping, and research purposes only. It does not provide investment, trading, tax, legal, or financial advice and does not constitute a recommendation or solicitation to buy or sell any security, futures contract, or other financial instrument.
