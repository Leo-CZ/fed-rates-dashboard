# Fed Rates Dashboard

This folder contains reproducible U.S. Treasury rate data and an interactive rate-analysis dashboard. All Treasury, breakeven, and real-yield inputs come from the Federal Reserve Bank of St. Louis FRED service. The requested download start date is `2019-01-01`. FRED's first available observation in this window may be later because January 1 is a market holiday.

## Folder layout

- `data/fred`: raw FRED downloads plus consolidated daily outputs and quality metadata.
- `data/cme_fedwatch`: current and timestamped CME FedWatch snapshots.
- `data/events`: the shared event calendar and one self-contained folder per intraday event study.
- `code`: FRED/Yahoo refresh tools, the CME snapshot importer, the dashboard builder, and vendored dependencies.
- `rate_dashboard.html`: generated dashboard kept at the project root for direct use.

## FRED download links

| Series | Description | Frequency | FRED page | CSV download from 2019-01-01 |
|---|---|---|---|---|
| DGS2 | 2-Year Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DGS2) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2&cosd=2019-01-01) |
| DGS10 | 10-Year Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DGS10) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2019-01-01) |
| DGS30 | 30-Year Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DGS30) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS30&cosd=2019-01-01) |
| T10YIE | 10-Year Breakeven Inflation Rate | Daily | [Series](https://fred.stlouisfed.org/series/T10YIE) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10YIE&cosd=2019-01-01) |
| DFII10 | 10-Year Inflation-Indexed Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DFII10) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10&cosd=2019-01-01) |
| DFII30 | 30-Year Inflation-Indexed Treasury Constant Maturity Rate | Daily | [Series](https://fred.stlouisfed.org/series/DFII30) | [CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII30&cosd=2019-01-01) |

The raw, unmodified downloads are stored in [`data/fred/raw`](data/fred/raw). Missing FRED observations remain blank in the raw files.

## Policy-event and FedWatch sources

- FOMC meeting dates and minutes-release dates: [Federal Reserve meeting calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm), plus the Fed's [2019](https://www.federalreserve.gov/monetarypolicy/fomchistorical2019.htm) and [2020](https://www.federalreserve.gov/monetarypolicy/fomchistorical2020.htm) archives.
- CME rate-path probabilities: [CME FedWatch Tool](https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html).

The current CME capture is stored in [`data/cme_fedwatch/cme_fedwatch_snapshot.csv`](data/cme_fedwatch/cme_fedwatch_snapshot.csv), with snapshot metadata in [`data/cme_fedwatch/cme_fedwatch_snapshot.json`](data/cme_fedwatch/cme_fedwatch_snapshot.json). The timestamped source capture is retained separately as [`data/cme_fedwatch/cme_fedwatch_snapshot_20260830_114317_CT.csv`](data/cme_fedwatch/cme_fedwatch_snapshot_20260830_114317_CT.csv). These probabilities are a point-in-time market snapshot and should not be treated as a Federal Reserve forecast. Blank CME source cells remain blank and display as a dash in the dashboard.

The FRED refresh is automated. CME FedWatch is updated through a validated manual CSV import because CME's official [FedWatch API](https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html) is a subscription product; this repository does not scrape an undocumented private endpoint. If a new CME snapshot is not imported, the dashboard deliberately retains and labels the older snapshot rather than presenting it as current.

## Yahoo Finance intraday event data

The first intraday event study covers Chair Kevin Warsh's Jackson Hole keynote at `2026-08-28 10:00 ET`. Its requested ±1-trading-day window is August 27, August 28, and August 31. The source snapshot is [`data/events/warsh_jackson_hole_20260828/event_intraday_yahoo.csv`](data/events/warsh_jackson_hole_20260828/event_intraday_yahoo.csv), metadata and completeness status are in [`event_intraday_yahoo_metadata.json`](data/events/warsh_jackson_hole_20260828/event_intraday_yahoo_metadata.json), and the unmodified Yahoo responses are archived under [`data/events/warsh_jackson_hole_20260828/raw`](data/events/warsh_jackson_hole_20260828/raw).

- Futures prices: `ZT=F` (2Y), `ZN=F` (10Y), and `ZB=F` (30Y).
- Yield indices: `^TNX` (10Y) and `^TYX` (30Y).
- Frequency/value: one-minute Yahoo Finance `close` observations, converted to Eastern Time for analysis.
- Missing values: no interpolation or forward-fill. The yield indices have shorter sessions than the futures.

The futures are price proxies, not cash Treasury yields, and therefore are not substituted into the daily 2s10s/2s30s/10s30s calculations. Yahoo's chart endpoint is an undocumented public interface and may change or restrict its minute-history window; the archived raw responses preserve the exact source snapshot used by the dashboard.

## Calculations

- `2s10s = DGS10 - DGS2`
- `2s30s = DGS30 - DGS2`
- `10s30s = DGS30 - DGS10`
- `10Y implied real = DGS10 - T10YIE`
- `30Y breakeven = DGS30 - DFII30`
- `30Y implied real = DGS30 - 30Y breakeven`

Every calculation uses same-date observations. If any required input is missing, the result is `NA`. No interpolation, forward-fill, or other imputation is performed. The consolidated dataset is [`data/fred/rates_daily.csv`](data/fred/rates_daily.csv).

FRED series can update on different schedules. In the current download, T10YIE extends through `2026-08-28`, while the nominal Treasury and TIPS series extend through `2026-08-27`. The August 28 row is retained with `NA` for unavailable values and calculations. See [`data/fred/data_quality_report.md`](data/fred/data_quality_report.md) for the current coverage of every series.

The daily 30-year breakeven series is calculated because FRED's directly published [T30YIEM](https://fred.stlouisfed.org/series/T30YIEM) series is monthly. Using DGS30 and DFII30 avoids mixing daily and monthly observations.

Breakeven inflation includes inflation expectations plus liquidity and risk premia; it should not be interpreted as a pure inflation forecast.

## Interactive dashboard

Open [`rate_dashboard.html`](rate_dashboard.html) in a browser. The dashboard provides:

1. 2Y, 10Y, and 30Y nominal Treasury yields in one plot.
2. 2s10s, 2s30s, and 10s30s spreads in one plot.
3. 10Y/30Y breakeven inflation and 10Y/30Y implied real yields in one plot.
4. Custom start/end dates and zoom controls on the first control row, followed by shared quick-range buttons for 1 week, 1 month, 6 months, YTD, 1 year, 3 years, 5 years, and all data. The quick ranges are also available below each plot.
5. Button and mouse-wheel zooming plus horizontal mouse-drag panning, synchronized across all plots.
6. Y-axes that tightly rescale to the selected dates and enabled series without forcing zero into the domain.
7. Toggleable series, cross-series hover values, and separately toggleable markers for major policy events, FOMC meetings, and minutes releases.
8. A CME FedWatch probability table whose title records the exact snapshot date and time.
9. Full calendar-date x-axis labels for windows up to roughly one year. When the selected range reaches the latest observation, the x-axis extends seven calendar days beyond it as an empty outlook margin; no values are imputed or plotted in that margin.
10. Two synchronized intraday event plots: 2Y/10Y/30Y Treasury futures prices and Yahoo's 10Y/30Y yield indices, with a marker at the event timestamp and selectable full-window, event-day, and immediate-reaction views.

Event dates, types, and official sources are stored in [`data/events/events.csv`](data/events/events.csv). Two-day FOMC meetings use the decision/end date; the March 2 and March 15, 2020 emergency meetings are included from the Fed's archive. Future meeting dates are included from the official calendar, but future minutes markers are not added until the Federal Reserve publishes their actual release dates. For Jackson Hole, the marker uses the date of the sitting Fed Chair's remarks rather than the symposium start date.

## Updating the data

Run the commands below from the repository root. The scripts derive every data path relative to their own locations; no machine-specific folder configuration is required.

Requirements:

- Python 3.10 or newer.
- PowerShell 7 or newer for the FRED refresh script.
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

The refresh downloads DGS2, DGS10, DGS30, T10YIE, DFII10, and DFII30 beginning on `2019-01-01`. Each response is downloaded to a temporary file, retried after transient failures, and checked for the expected FRED CSV header before replacing the prior raw file. The build regenerates the consolidated daily data, metadata, quality report, and dashboard.

After rebuilding, review [`data/fred/data_quality_report.md`](data/fred/data_quality_report.md). It records coverage and release-timing differences without modifying or filling the data.

### 2. Update CME FedWatch probabilities

There is no credential-free supported CME API used by this repository. CME offers an official subscription [FedWatch API](https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html); this repository instead updates the snapshot from the public [CME FedWatch Tool](https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html) as follows:

1. Record the table's displayed update time in Central Time and the current federal-funds target range.
2. Copy or export the probability table to a UTF-8 CSV. Its first column must be `Meeting Date`; subsequent columns must be target ranges such as `350-375`. Probability cells may contain numbers with or without `%` signs.
3. Import and validate the file:

```powershell
python ./code/update_cme_fedwatch.py `
  --input ./fedwatch_export.csv `
  --current-target 350-375 `
  --snapshot-time "2026-08-30T11:43:17-05:00"
python ./code/build_rate_dashboard.py
```

Replace the example target and timestamp with the values shown by CME. The importer rejects malformed dates, invalid target ranges, probabilities outside 0–100%, duplicate meeting dates, and rows that do not sum to approximately 100%. A validated timestamped archive is created automatically, while `cme_fedwatch_snapshot.csv` and its metadata JSON become the current snapshot.

### 3. Update Yahoo one-minute event data

```powershell
python ./code/download_yahoo_event_data.py
python ./code/build_rate_dashboard.py
```

The downloader refreshes the configured `warsh_jackson_hole_20260828` event folder, archives the unmodified Yahoo responses, and regenerates the normalized one-minute CSV and completeness metadata. Rerun it after the next requested trading day closes to fill a previously pending session. Yahoo limits the availability of one-minute history, so event data should be captured promptly.

Yahoo's yield indices have shorter sessions than Treasury futures. The downloader retains the source timestamps, converts the analysis timestamps to Eastern Time, and never fills missing minutes.

### Rebuild without downloading

If the raw inputs are already current, rebuild all derived files and the dashboard with:

```powershell
python ./code/build_rate_dashboard.py
```

## Disclaimer

This repository is provided for educational, record-keeping, and research purposes only. It does not provide investment, trading, tax, legal, or financial advice and does not constitute a recommendation or solicitation to buy or sell any security, futures contract, or other financial instrument.

Market data may be delayed, incomplete, revised, unavailable, or inaccurate. FRED observations are daily reference series; Yahoo Finance data use a public interface that may change without notice; Treasury futures prices are not cash Treasury yields; and CME FedWatch probabilities are estimates derived from Fed Funds futures rather than Federal Reserve forecasts. Users should verify material figures against the original sources and comply with each data provider's applicable terms and licensing requirements. No warranty is made regarding accuracy, completeness, availability, or fitness for any purpose, and users assume all responsibility for decisions made using these files.
