from __future__ import annotations

import csv
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent
EVENT_ID = "warsh_jackson_hole_20260828"
EVENT_LABEL = "Chair Kevin Warsh — Jackson Hole keynote"
ET = ZoneInfo("America/New_York")
EVENT_TIME = datetime(2026, 8, 28, 10, 0, tzinfo=ET)
TRADING_DAYS = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31)]
TICKERS = {
    "ZT=F": "ZT_F",
    "ZN=F": "ZN_F",
    "ZB=F": "ZB_F",
    "^TNX": "TNX",
    "^TYX": "TYX",
}
OUTPUT_COLUMNS = ["ZT_F", "ZN_F", "ZB_F", "TNX", "TYX"]
EVENT_DIR = ROOT / "data" / "events" / EVENT_ID


def unix_seconds(value: datetime) -> int:
    return int(value.timestamp())


def yahoo_url(ticker: str) -> str:
    query_start = datetime.combine(TRADING_DAYS[0], time.min, tzinfo=ET)
    query_end = datetime.combine(TRADING_DAYS[-1], time.max, tzinfo=ET)
    params = urlencode(
        {
            "period1": unix_seconds(query_start),
            "period2": unix_seconds(query_end),
            "interval": "1m",
            "events": "history",
            "includePrePost": "true",
        }
    )
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}?{params}"


def fetch(ticker: str) -> dict[str, object]:
    request = Request(
        yahoo_url(ticker),
        headers={"User-Agent": "Mozilla/5.0 rate-event-study/1.0"},
    )
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if payload.get("chart", {}).get("error"):
        raise RuntimeError(f"Yahoo returned an error for {ticker}: {payload['chart']['error']}")
    if not payload.get("chart", {}).get("result"):
        raise RuntimeError(f"Yahoo returned no chart result for {ticker}")
    return payload


def build() -> None:
    raw_dir = EVENT_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: dict[datetime, dict[str, object]] = {}
    coverage: dict[str, dict[str, object]] = {}

    for ticker, column in TICKERS.items():
        payload = fetch(ticker)
        safe_name = ticker.replace("^", "index_").replace("=", "_")
        (raw_dir / f"{safe_name}.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
        result = payload["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quote_rows = result.get("indicators", {}).get("quote", [{}])[0]
        closes = quote_rows.get("close", [])
        kept = 0
        available_dates: set[str] = set()

        for epoch, close_value in zip(timestamps, closes):
            if close_value is None:
                continue
            moment_et = datetime.fromtimestamp(epoch, timezone.utc).astimezone(ET)
            if moment_et.date() not in TRADING_DAYS:
                continue
            # Yahoo occasionally timestamps a one-minute bar at :59 seconds.
            moment_et = moment_et.replace(second=0, microsecond=0)
            moment_utc = moment_et.astimezone(timezone.utc)
            row = rows.setdefault(
                moment_utc,
                {
                    "timestamp_utc": moment_utc.isoformat().replace("+00:00", "Z"),
                    "timestamp_et": moment_et.isoformat(),
                    "session_date_et": moment_et.date().isoformat(),
                },
            )
            row[column] = round(float(close_value), 6)
            kept += 1
            available_dates.add(moment_et.date().isoformat())

        coverage[ticker] = {
            "column": column,
            "source_bars_with_close": kept,
            "bars_retained": 0,
            "available_trading_dates": sorted(available_dates),
            "exchange_timezone": result.get("meta", {}).get("exchangeTimezoneName"),
            "instrument_type": result.get("meta", {}).get("instrumentType"),
        }

    # Recount the actual saved minute rows after :59 timestamps have been floored
    # and any resulting same-minute duplicates have been resolved.
    for ticker, column in TICKERS.items():
        retained_rows = [row for row in rows.values() if column in row]
        coverage[ticker]["bars_retained"] = len(retained_rows)
        coverage[ticker]["available_trading_dates"] = sorted(
            {str(row["session_date_et"]) for row in retained_rows}
        )

    csv_path = EVENT_DIR / "event_intraday_yahoo.csv"
    fieldnames = ["timestamp_utc", "timestamp_et", "session_date_et", *OUTPUT_COLUMNS]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for _, row in sorted(rows.items()):
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    completed_days = set.intersection(
        *(set(details["available_trading_dates"]) for details in coverage.values())
    )
    metadata = {
        "event_id": EVENT_ID,
        "event_label": EVENT_LABEL,
        "event_timestamp_et": EVENT_TIME.isoformat(),
        "event_source": "https://www.kansascityfed.org/newsroom/2026-news-releases/kansas-city-fed-to-host-annual-jackson-hole-economic-policy-symposium-2026/",
        "requested_trading_dates": [day.isoformat() for day in TRADING_DAYS],
        "complete_across_all_tickers": all(day.isoformat() in completed_days for day in TRADING_DAYS),
        "common_available_trading_dates": sorted(completed_days),
        "downloaded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance chart endpoint",
        "source_url_template": "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        "interval": "1m",
        "value_field": "close",
        "timezone_for_analysis": "America/New_York",
        "missing_values_imputed": False,
        "coverage": coverage,
        "notes": [
            "ZT=F, ZN=F, and ZB=F are futures prices, not cash Treasury yields.",
            "^TNX and ^TYX are Yahoo Finance 10-year and 30-year yield indices.",
            "Minute bars are retained only for the previous trading day, event day, and next trading day.",
            "No interpolation or forward-fill is performed across instruments or sessions.",
        ],
    }
    (EVENT_DIR / "event_intraday_yahoo_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    print(f"Wrote {csv_path.name} with {len(rows):,} union timestamps")
    for ticker, details in coverage.items():
        print(
            f"{ticker}: {details['bars_retained']:,} bars on "
            f"{', '.join(details['available_trading_dates']) or 'no requested dates'}"
        )
    if not metadata["complete_across_all_tickers"]:
        missing = sorted(set(day.isoformat() for day in TRADING_DAYS) - completed_days)
        print(f"Event window incomplete; common data still missing for: {', '.join(missing)}")


if __name__ == "__main__":
    build()
