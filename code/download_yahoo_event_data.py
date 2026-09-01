from __future__ import annotations

import csv
import json
from datetime import date, datetime, time, timedelta, timezone
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
    "^TNX": "TNX",
    "^TYX": "TYX",
}
MARKET_FIELDS = ["open", "high", "low", "close"]
FIELDNAMES = [
    "timestamp_utc",
    "timestamp_et",
    "session_date_et",
    "ticker",
    *MARKET_FIELDS,
]
PREVIOUS_LONG_FIELDNAMES = [*FIELDNAMES, "volume"]
EVENT_DIR = ROOT / "data" / "events" / EVENT_ID


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def unix_seconds(value: datetime) -> int:
    return int(value.timestamp())


def yahoo_url(ticker: str, query_start: datetime, query_end: datetime) -> str:
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


def fetch(ticker: str, query_start: datetime, query_end: datetime) -> dict[str, object]:
    request = Request(
        yahoo_url(ticker, query_start, query_end),
        headers={"User-Agent": "Mozilla/5.0 rate-event-study/2.0"},
    )
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if payload.get("chart", {}).get("error"):
        raise RuntimeError(f"Yahoo returned an error for {ticker}: {payload['chart']['error']}")
    if not payload.get("chart", {}).get("result"):
        raise RuntimeError(f"Yahoo returned no chart result for {ticker}")
    return payload


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp has no UTC offset: {value}")
    return parsed.astimezone(timezone.utc)


def format_market_value(value: object) -> str:
    if value is None:
        return ""
    number = float(value)
    return f"{number:.6f}"


def extract_payload_rows(
    payload: dict[str, object],
    ticker: str,
    query_start: datetime,
    query_end: datetime,
) -> tuple[dict[tuple[str, datetime], dict[str, str]], dict[str, object]]:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote_rows = result.get("indicators", {}).get("quote", [{}])[0]
    arrays = {field: quote_rows.get(field, []) for field in MARKET_FIELDS}
    volume_values = quote_rows.get("volume", [])
    rows: dict[tuple[str, datetime], dict[str, str]] = {}
    duplicate_minutes = 0
    duplicate_details: list[dict[str, object]] = []
    incomplete_ohlc_bars = 0
    volume_returned = 0
    volume_nonzero = 0

    for index, epoch in enumerate(timestamps):
        moment_et = datetime.fromtimestamp(epoch, timezone.utc).astimezone(ET)
        if moment_et.date() not in TRADING_DAYS:
            continue
        # Yahoo occasionally timestamps a one-minute bar at :59 seconds.
        moment_et = moment_et.replace(second=0, microsecond=0)
        if moment_et < query_start or moment_et >= query_end:
            continue
        moment_utc = moment_et.astimezone(timezone.utc)
        key = (ticker, moment_utc)
        values = {
            field: arrays[field][index] if index < len(arrays[field]) else None
            for field in MARKET_FIELDS
        }
        if all(values[field] is None for field in ("open", "high", "low", "close")):
            continue
        if any(values[field] is None for field in ("open", "high", "low", "close")):
            incomplete_ohlc_bars += 1
        volume_value = volume_values[index] if index < len(volume_values) else None
        if volume_value is not None:
            volume_returned += 1
            if float(volume_value) != 0:
                volume_nonzero += 1

        row = {
            "timestamp_utc": moment_utc.isoformat().replace("+00:00", "Z"),
            "timestamp_et": moment_et.isoformat(),
            "session_date_et": moment_et.date().isoformat(),
            "ticker": ticker,
            "open": format_market_value(values["open"]),
            "high": format_market_value(values["high"]),
            "low": format_market_value(values["low"]),
            "close": format_market_value(values["close"]),
        }
        if key in rows:
            duplicate_minutes += 1
            existing = rows[key]
            duplicate_details.append(
                {
                    "normalized_timestamp_et": row["timestamp_et"],
                    "retained_first": {field: existing[field] for field in MARKET_FIELDS},
                    "later_duplicate": {field: row[field] for field in MARKET_FIELDS},
                }
            )
            for field in MARKET_FIELDS:
                if existing[field] == "" and row[field] != "":
                    existing[field] = row[field]
            continue
        rows[key] = row

    metadata = result.get("meta", {})
    if volume_nonzero:
        raise ValueError(
            f"Yahoo returned {volume_nonzero} non-zero volume observations for {ticker}. "
            "Yield-index volume is intentionally not stored, so review this source change before proceeding."
        )
    stats = {
        "source_timestamps": len(timestamps),
        "bars_extracted": len(rows),
        "duplicate_minutes_in_response": duplicate_minutes,
        "duplicate_minute_details": duplicate_details,
        "incomplete_ohlc_bars": incomplete_ohlc_bars,
        "volume_values_returned": volume_returned,
        "volume_nonzero_returned": volume_nonzero,
        "exchange_timezone": metadata.get("exchangeTimezoneName"),
        "instrument_type": metadata.get("instrumentType"),
    }
    return rows, stats


def load_existing_rows(
    csv_path: Path,
) -> tuple[
    dict[tuple[str, datetime], dict[str, str]],
    bool,
    dict[str, dict[str, object]],
]:
    if not csv_path.exists():
        return {}, False, {}

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        if fieldnames == PREVIOUS_LONG_FIELDNAMES:
            rows: dict[tuple[str, datetime], dict[str, str]] = {}
            for row_number, source_row in enumerate(reader, start=2):
                ticker = source_row["ticker"]
                if ticker not in TICKERS:
                    continue
                moment_utc = parse_utc(source_row["timestamp_utc"])
                key = (ticker, moment_utc)
                if key in rows:
                    raise ValueError(f"Duplicate ticker/timestamp in {csv_path} at row {row_number}")
                rows[key] = {field: source_row.get(field, "") for field in FIELDNAMES}
            return rows, True, {}
        if fieldnames != FIELDNAMES:
            raise ValueError(f"Unexpected columns in {csv_path}: {fieldnames}")

        rows: dict[tuple[str, datetime], dict[str, str]] = {}
        for row_number, source_row in enumerate(reader, start=2):
            ticker = source_row["ticker"]
            if ticker not in TICKERS:
                raise ValueError(f"Unknown ticker in {csv_path} at row {row_number}: {ticker}")
            moment_utc = parse_utc(source_row["timestamp_utc"])
            key = (ticker, moment_utc)
            if key in rows:
                raise ValueError(f"Duplicate ticker/timestamp in {csv_path} at row {row_number}")
            row = {field: source_row.get(field, "") for field in FIELDNAMES}
            rows[key] = row
    return rows, False, {}


def write_rows(
    csv_path: Path,
    rows: dict[tuple[str, datetime], dict[str, str]],
) -> None:
    temporary = csv_path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for _, row in sorted(rows.items(), key=lambda item: (item[0][1], item[0][0])):
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})
    temporary.replace(csv_path)


def append_rows(csv_path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def append_debug_record(path: Path, record: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")


def remove_zero_volume_from_retained_raw_snapshots(raw_dir: Path) -> int:
    sanitized = 0
    for path in (raw_dir / "index_TNX.json", raw_dir / "index_TYX.json"):
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for result in payload.get("chart", {}).get("result", []) or []:
            for quote_rows in result.get("indicators", {}).get("quote", []) or []:
                volume_values = quote_rows.get("volume", [])
                if any(value not in (None, 0, 0.0) for value in volume_values):
                    raise ValueError(
                        f"Retained yield snapshot contains non-zero volume and requires review: {path}"
                    )
                if "volume" in quote_rows:
                    del quote_rows["volume"]
                    changed = True
        if changed:
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8"
            )
            temporary.replace(path)
            sanitized += 1
    return sanitized


def build() -> None:
    raw_dir = EVENT_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_snapshots_sanitized = remove_zero_volume_from_retained_raw_snapshots(raw_dir)
    csv_path = EVENT_DIR / "event_intraday_yahoo.csv"
    metadata_path = EVENT_DIR / "event_intraday_yahoo_metadata.json"
    debug_log_path = EVENT_DIR / "event_intraday_yahoo_updates.jsonl"
    previous_metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    )
    previous_coverage = previous_metadata.get("coverage", {})
    rows, migrated_previous_schema, migration_stats = load_existing_rows(csv_path)
    existing_keys = set(rows)
    run_time_utc = utc_now()
    event_start = datetime.combine(TRADING_DAYS[0], time.min, tzinfo=ET)
    event_end = datetime.combine(TRADING_DAYS[-1], time.max, tzinfo=ET)
    query_cutoff = min(run_time_utc.astimezone(ET), event_end)
    coverage: dict[str, dict[str, object]] = {}
    new_keys: set[tuple[str, datetime]] = set()
    filled_existing_fields = 0
    conflicts_preserved = 0
    requests_made = 0

    for ticker in TICKERS:
        ticker_times = [moment for stored_ticker, moment in rows if stored_ticker == ticker]
        previous_last = max(ticker_times) if ticker_times else None
        query_start = (
            (previous_last + timedelta(minutes=1)).astimezone(ET)
            if previous_last is not None
            else event_start
        )
        previous_ticker_coverage = previous_coverage.get(ticker, {})
        source_stats = migration_stats.get(ticker, {})
        request_stats: dict[str, object] = {
            "source_timestamps": 0,
            "bars_extracted": 0,
            "duplicate_minutes_in_response": 0,
            "incomplete_ohlc_bars": 0,
            "volume_values_returned": 0,
            "volume_nonzero_returned": 0,
            "exchange_timezone": source_stats.get(
                "exchange_timezone", previous_ticker_coverage.get("exchange_timezone")
            ),
            "instrument_type": source_stats.get(
                "instrument_type", previous_ticker_coverage.get("instrument_type")
            ),
        }
        ticker_added = 0
        ticker_filled = 0
        ticker_conflicts = 0

        if query_start < query_cutoff:
            payload = fetch(ticker, query_start, query_cutoff)
            requests_made += 1
            incoming_rows, request_stats = extract_payload_rows(
                payload, ticker, query_start, query_cutoff
            )
            for key, incoming in incoming_rows.items():
                if key not in rows:
                    rows[key] = incoming
                    new_keys.add(key)
                    ticker_added += 1
                    continue
                stored = rows[key]
                for field in MARKET_FIELDS:
                    if stored[field] == "" and incoming[field] != "":
                        stored[field] = incoming[field]
                        filled_existing_fields += 1
                        ticker_filled += 1
                    elif (
                        stored[field] != ""
                        and incoming[field] != ""
                        and abs(float(stored[field]) - float(incoming[field])) > 0.0000005
                    ):
                        conflicts_preserved += 1
                        ticker_conflicts += 1

        ticker_rows = [row for (stored_ticker, _), row in rows.items() if stored_ticker == ticker]
        available_dates = sorted({row["session_date_et"] for row in ticker_rows})
        coverage[ticker] = {
            "query_start_et": query_start.isoformat(),
            "query_end_et": query_cutoff.isoformat() if query_start < query_cutoff else None,
            "request_made": query_start < query_cutoff,
            "bars_added": ticker_added,
            "existing_blank_fields_filled": ticker_filled,
            "conflicts_preserved": ticker_conflicts,
            "bars_retained": len(ticker_rows),
            "available_trading_dates": available_dates,
            **request_stats,
            "volume_storage": "not stored because Yahoo returned only zeros",
        }

    if migrated_previous_schema or not csv_path.exists() or filled_existing_fields:
        write_rows(csv_path, rows)
        storage_action = "rewrote atomically for schema migration or blank-field completion"
    elif new_keys:
        new_rows = [rows[key] for key in sorted(new_keys, key=lambda key: (key[1], key[0]))]
        append_rows(csv_path, new_rows)
        storage_action = "appended new ticker-minute rows"
    else:
        storage_action = "left canonical CSV unchanged"

    yield_common_dates = set.intersection(
        *(set(coverage[ticker]["available_trading_dates"]) for ticker in sorted(TICKERS))
    )
    last_source_download = (
        run_time_utc.isoformat(timespec="seconds")
        if requests_made
        else previous_metadata.get("downloaded_utc", previous_metadata.get("update_attempted_utc"))
        or run_time_utc.isoformat(timespec="seconds")
    )
    metadata = {
        "event_id": EVENT_ID,
        "event_label": EVENT_LABEL,
        "event_timestamp_et": EVENT_TIME.isoformat(),
        "event_source": "https://www.kansascityfed.org/newsroom/2026-news-releases/kansas-city-fed-to-host-annual-jackson-hole-economic-policy-symposium-2026/",
        "requested_trading_dates": [day.isoformat() for day in TRADING_DAYS],
        "complete_across_yield_tickers": all(
            day.isoformat() in yield_common_dates for day in TRADING_DAYS
        ),
        "common_available_trading_dates": sorted(yield_common_dates),
        "downloaded_utc": last_source_download,
        "update_attempted_utc": run_time_utc.isoformat(timespec="seconds"),
        "source": "Yahoo Finance chart endpoint",
        "source_url_template": "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        "interval": "1m",
        "schema": "Long-form ticker-minute OHLC for ^TNX and ^TYX",
        "timezone_for_analysis": "America/New_York",
        "update_mode": "Incremental by ticker from the final stored observation",
        "previous_long_csv_migrated": migrated_previous_schema,
        "existing_nonblank_values_replaced": False,
        "missing_values_imputed": False,
        "rows_before_update": len(existing_keys),
        "rows_after_update": len(rows),
        "new_bars_added": len(new_keys),
        "existing_blank_fields_filled": filled_existing_fields,
        "conflicts_preserved": conflicts_preserved,
        "requests_made": requests_made,
        "storage_action": storage_action,
        "debug_log": debug_log_path.name,
        "retained_raw_snapshots_sanitized_this_run": raw_snapshots_sanitized,
        "coverage": coverage,
        "notes": [
            "Each CSV row is uniquely identified by ticker and UTC timestamp.",
            "Only Yahoo's ^TNX and ^TYX yield indices are retained.",
            "Open, high, low, and close values are retained.",
            "Yahoo returned only zero volume for these indices, so volume is not stored.",
            "Existing nonblank market fields are immutable; source conflicts are counted but not applied.",
            "No interpolation or forward-fill is performed.",
            "No new full-response archives are created.",
        ],
    }
    temporary_metadata = metadata_path.with_suffix(".json.tmp")
    temporary_metadata.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary_metadata.replace(metadata_path)

    debug_record = {
        "update_attempted_utc": metadata["update_attempted_utc"],
        "event_id": EVENT_ID,
        "interval": "1m",
        "storage_action": storage_action,
        "previous_long_csv_migrated": migrated_previous_schema,
        "new_bars_added": len(new_keys),
        "existing_blank_fields_filled": filled_existing_fields,
        "conflicts_preserved": conflicts_preserved,
        "retained_raw_snapshots_sanitized_this_run": raw_snapshots_sanitized,
        "coverage": coverage,
    }
    append_debug_record(debug_log_path, debug_record)

    print(f"Yahoo update: {storage_action}")
    print(f"Canonical rows: {len(rows):,}; new bars: {len(new_keys):,}")
    for ticker, details in coverage.items():
        print(
            f"{ticker}: added {details['bars_added']:,}; retained {details['bars_retained']:,}; "
            f"OHLC incomplete {details['incomplete_ohlc_bars']:,}; "
            f"volume nonzero {details['volume_nonzero_returned']:,}"
        )
    if not metadata["complete_across_yield_tickers"]:
        missing = sorted(set(day.isoformat() for day in TRADING_DAYS) - yield_common_dates)
        print(f"Yield event window incomplete; missing common dates: {', '.join(missing)}")


if __name__ == "__main__":
    build()
