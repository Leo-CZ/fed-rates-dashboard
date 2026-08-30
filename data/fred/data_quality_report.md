# Data quality report

- Requested start date: `2019-01-01`
- First available FRED observation date: `2019-01-02`
- Latest available observation date: `2026-08-28`
- Missing values imputed: **No**
- Spreads and derived rates are calculated only when every required same-date input exists.

## Raw series coverage

| Series | Non-missing | Missing rows | First non-missing | Last non-missing |
|---|---:|---:|---|---|
| DGS2 | 1915 | 83 | 2019-01-02 | 2026-08-27 |
| DGS10 | 1915 | 83 | 2019-01-02 | 2026-08-27 |
| DGS30 | 1915 | 83 | 2019-01-02 | 2026-08-27 |
| T10YIE | 1916 | 82 | 2019-01-02 | 2026-08-28 |
| DFII10 | 1915 | 83 | 2019-01-02 | 2026-08-27 |
| DFII30 | 1915 | 83 | 2019-01-02 | 2026-08-27 |

## Release timing mismatch

The FRED series do not all end on the same date in this download:

- DGS2: `2026-08-27`
- DGS10: `2026-08-27`
- DGS30: `2026-08-27`
- T10YIE: `2026-08-28`
- DFII10: `2026-08-27`
- DFII30: `2026-08-27`

The consolidated file retains the later date and records unavailable inputs and calculations as `NA`; no value is carried forward.

## Calculation validation

The calculated implied real yields were compared with the independently downloaded FRED TIPS yields.

- 10Y maximum absolute difference versus DFII10: `0.0000` percentage points across `1915` matched dates.
- 30Y maximum absolute difference versus DFII30: `0.0000` percentage points across `1915` matched dates.
- Small 10Y differences can occur because FRED publishes each input rounded to two decimals.
