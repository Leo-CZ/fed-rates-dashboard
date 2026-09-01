# Data quality report

- Requested start date: `2019-01-01`
- First available FRED observation date: `2019-01-02`
- Latest available observation date: `2026-08-31`
- Missing values imputed: **No**
- Spreads and derived rates are calculated only when every required same-date input exists.

## Raw series coverage

| Series | Non-missing | Missing rows | First non-missing | Last non-missing |
|---|---:|---:|---|---|
| DGS2 | 1916 | 83 | 2019-01-02 | 2026-08-28 |
| DGS10 | 1916 | 83 | 2019-01-02 | 2026-08-28 |
| DGS30 | 1916 | 83 | 2019-01-02 | 2026-08-28 |
| T10YIE | 1917 | 82 | 2019-01-02 | 2026-08-31 |
| DFII10 | 1916 | 83 | 2019-01-02 | 2026-08-28 |
| DFII30 | 1916 | 83 | 2019-01-02 | 2026-08-28 |

## Release timing mismatch

The FRED series do not all end on the same date in this download:

- DGS2: `2026-08-28`
- DGS10: `2026-08-28`
- DGS30: `2026-08-28`
- T10YIE: `2026-08-31`
- DFII10: `2026-08-28`
- DFII30: `2026-08-28`

The consolidated file retains the later date and records unavailable inputs and calculations as `NA`; no value is carried forward.

## Calculation validation

The calculated implied real yields were compared with the independently downloaded FRED TIPS yields.

- 10Y maximum absolute difference versus DFII10: `0.0000` percentage points across `1916` matched dates.
- 30Y maximum absolute difference versus DFII30: `0.0000` percentage points across `1916` matched dates.
- Small 10Y differences can occur because FRED publishes each input rounded to two decimals.
