# Fed Rates Dashboard

The dashboard presents
- U.S. Treasury rate data for 2y/10y/30y from Federal Reserve Bank of St. Louis ([FRED](https://fred.stlouisfed.org/)), including
  - nominal rates
  - curve spreads
  - breakeven rates and implied real rates
- A possible major event like FOMC/Key Speeches/Pivot events
  - the minute level yield rates for 10y/30y from Yahoo Finance
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

Python 3.10 or newer is required. Use `python3` instead of `python` where needed. Install dependencies once:

```bash
python -m pip install -r requirements.txt
```

Update FRED data and rebuild the dashboard:

```bash
python ./code/refresh_fred_data.py
```

Update Yahoo event data and rebuild:

```bash
python ./code/download_yahoo_event_data.py
```

Update CME FedWatch and rebuild:

```bash
python ./code/update_cme_fedwatch.py
```

Rebuild the dashboard based on the refreshed data:

```bash
python ./code/build_rate_dashboard.py
```

The updater reads the public [CME FedWatch Tool](https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html), validates the complete probability table, converts CME's published timestamp to Eastern Time (`ET`), and saves one timestamped snapshot per ET data date. If that date already has a snapshot, it asks whether to overwrite it using `yes`, `no`, or `cancel`.

## Disclaimer

This repository is provided for educational, record-keeping, and research purposes only. It does not provide investment, trading, tax, legal, or financial advice and does not constitute a recommendation or solicitation to buy or sell any security, futures contract, or other financial instrument.
