from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd


CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent
DATA_DIR = ROOT / "data"
FRED_DIR = DATA_DIR / "fred"
RAW = FRED_DIR / "raw"
CME_DIR = DATA_DIR / "cme_fedwatch"
SNAPSHOT_PATTERN = re.compile(r"^cme_fedwatch_snapshot_(\d{8})_(\d{6})_ET\.csv$")
EVENTS_DIR = DATA_DIR / "events"
INTRADAY_EVENT_DIR = EVENTS_DIR / "warsh_jackson_hole_20260828"
START_DATE = pd.Timestamp("2019-01-01")
SERIES = ["DGS2", "DGS10", "DGS30", "T10YIE", "DFII10", "DFII30"]


def read_series(series_id: str) -> pd.DataFrame:
    path = RAW / f"{series_id}.csv"
    frame = pd.read_csv(path, na_values=[".", "NA", ""], keep_default_na=True)
    if list(frame.columns) != ["observation_date", series_id]:
        raise ValueError(f"Unexpected columns in {path}: {list(frame.columns)}")
    frame["observation_date"] = pd.to_datetime(frame["observation_date"], errors="raise")
    frame[series_id] = pd.to_numeric(frame[series_id], errors="coerce")
    return frame.loc[frame["observation_date"] >= START_DATE].set_index("observation_date")


def load_events() -> list[dict[str, str]]:
    with (EVENTS_DIR / "events.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_cme_snapshot() -> tuple[list[dict[str, str]], dict[str, object]]:
    snapshots = [
        path
        for path in CME_DIR.iterdir()
        if path.is_file() and SNAPSHOT_PATTERN.fullmatch(path.name)
    ]
    if not snapshots:
        raise FileNotFoundError(f"No timestamped CME FedWatch snapshots found in {CME_DIR}")
    snapshot_path = max(snapshots, key=lambda path: path.name)
    metadata_path = snapshot_path.with_suffix(".json")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"CME snapshot metadata not found: {metadata_path}")
    with snapshot_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["snapshot_file"] = snapshot_path.name
    return rows, metadata


def load_intraday_event() -> tuple[list[dict[str, object]], dict[str, object]]:
    metadata_path = INTRADAY_EVENT_DIR / "event_intraday_yahoo_metadata.json"
    data_path = INTRADAY_EVENT_DIR / "event_intraday_yahoo.csv"
    if not metadata_path.exists() or not data_path.exists():
        return [], {}

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected_columns = [
        "timestamp_utc",
        "timestamp_et",
        "session_date_et",
        "ticker",
        "open",
        "high",
        "low",
        "close",
    ]
    numeric_columns = ["open", "high", "low", "close"]
    rows: list[dict[str, object]] = []
    with data_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_columns:
            raise ValueError(
                f"Unexpected Yahoo event columns in {data_path}: {reader.fieldnames}. "
                "Run the Yahoo event-data updater to migrate the yield-only OHLC schema."
            )
        for source_row in reader:
            row: dict[str, object] = {
                "timestamp_utc": source_row["timestamp_utc"],
                "timestamp_et": source_row["timestamp_et"],
                "session_date_et": source_row["session_date_et"],
                "ticker": source_row["ticker"],
            }
            for column in numeric_columns:
                value = source_row.get(column, "")
                row[column] = round(float(value), 6) if value not in (None, "") else None
            rows.append(row)
    return rows, metadata


def make_cme_table(rows: list[dict[str, str]], metadata: dict[str, object]) -> str:
    if not rows:
        return '<p class="error">No CME FedWatch snapshot rows are available.</p>'

    headers = list(rows[0])
    current_target = str(metadata["current_target_bps"])
    header_cells = ['<th scope="col">Meeting date</th>']
    for target in headers[1:]:
        class_name = ' class="current-target"' if target == current_target else ""
        header_cells.append(f'<th scope="col"{class_name}>{escape(target)} bps</th>')

    body_rows: list[str] = []
    for row in rows:
        numeric = {
            key: float(value)
            for key, value in row.items()
            if key != headers[0] and value not in (None, "")
        }
        row_max = max(numeric.values()) if numeric else None
        cells = [f'<th scope="row">{escape(row[headers[0]])}</th>']
        for target in headers[1:]:
            value = row.get(target, "")
            classes: list[str] = []
            if target == current_target:
                classes.append("current-target")
            if value not in (None, "") and row_max is not None and float(value) == row_max:
                classes.append("row-maximum")
            class_attr = f' class="{" ".join(classes)}"' if classes else ""
            display = f"{escape(value)}%" if value not in (None, "") else '<span title="CME source cell was blank">—</span>'
            cells.append(f"<td{class_attr}>{display}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<div class="table-scroll"><table class="fedwatch-table">'
        f'<thead><tr>{"".join(header_cells)}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def safe_float(value: object) -> float | None:
    if pd.isna(value):
        return None
    return round(float(value), 4)


def build() -> None:
    frames = [read_series(series_id) for series_id in SERIES]
    data = pd.concat(frames, axis=1, join="outer").sort_index()

    # Curve spreads follow the conventional long-maturity minus short-maturity sign.
    data["SPREAD_2Y10Y"] = data["DGS10"] - data["DGS2"]
    data["SPREAD_2Y30Y"] = data["DGS30"] - data["DGS2"]
    data["SPREAD_10Y30Y"] = data["DGS30"] - data["DGS10"]

    # 10Y breakeven is the official daily FRED series. The implied real yield is calculated.
    data["BE10"] = data["T10YIE"]
    data["REAL10_IMPLIED"] = data["DGS10"] - data["BE10"]

    # No official daily 30Y breakeven series is published. Calculate it from matched daily FRED series.
    data["BE30_CALC"] = data["DGS30"] - data["DFII30"]
    data["REAL30_IMPLIED"] = data["DGS30"] - data["BE30_CALC"]

    # Independent validation columns; these are not substitutions or imputations.
    data["REAL10_VALIDATION_DIFF"] = data["REAL10_IMPLIED"] - data["DFII10"]
    data["REAL30_VALIDATION_DIFF"] = data["REAL30_IMPLIED"] - data["DFII30"]

    output_columns = [
        "DGS2",
        "DGS10",
        "DGS30",
        "SPREAD_2Y10Y",
        "SPREAD_2Y30Y",
        "SPREAD_10Y30Y",
        "BE10",
        "REAL10_IMPLIED",
        "BE30_CALC",
        "REAL30_IMPLIED",
        "DFII10",
        "DFII30",
        "REAL10_VALIDATION_DIFF",
        "REAL30_VALIDATION_DIFF",
    ]
    output = data[output_columns]
    output.to_csv(FRED_DIR / "rates_daily.csv", index_label="date", na_rep="NA", float_format="%.4f")

    records: list[dict[str, object]] = []
    for date, row in output.iterrows():
        record: dict[str, object] = {"date": date.strftime("%Y-%m-%d")}
        for column in output_columns:
            record[column] = safe_float(row[column])
        records.append(record)

    events = load_events()
    cme_rows, cme_metadata = load_cme_snapshot()
    intraday_rows, intraday_metadata = load_intraday_event()
    metadata = {
        "requested_start_date": START_DATE.strftime("%Y-%m-%d"),
        "first_available_date": data.index.min().strftime("%Y-%m-%d"),
        "last_available_date": data.index.max().strftime("%Y-%m-%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_series": SERIES,
        "missing_values_imputed": False,
        "cme_fedwatch_snapshot": cme_metadata,
    }
    (FRED_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    quality_lines = [
        "# Data quality report",
        "",
        f"- Requested start date: `{metadata['requested_start_date']}`",
        f"- First available FRED observation date: `{metadata['first_available_date']}`",
        f"- Latest available observation date: `{metadata['last_available_date']}`",
        "- Missing values imputed: **No**",
        "- Spreads and derived rates are calculated only when every required same-date input exists.",
        "",
        "## Raw series coverage",
        "",
        "| Series | Non-missing | Missing rows | First non-missing | Last non-missing |",
        "|---|---:|---:|---|---|",
    ]
    for series_id in SERIES:
        series = data[series_id]
        valid = series.dropna()
        quality_lines.append(
            f"| {series_id} | {series.notna().sum()} | {series.isna().sum()} | "
            f"{valid.index.min():%Y-%m-%d} | {valid.index.max():%Y-%m-%d} |"
        )

    latest_dates = {series_id: data[series_id].dropna().index.max() for series_id in SERIES}
    if len(set(latest_dates.values())) > 1:
        quality_lines.extend(
            [
                "",
                "## Release timing mismatch",
                "",
                "The FRED series do not all end on the same date in this download:",
                "",
            ]
        )
        for series_id, latest_date in latest_dates.items():
            quality_lines.append(f"- {series_id}: `{latest_date:%Y-%m-%d}`")
        quality_lines.extend(
            [
                "",
                "The consolidated file retains the later date and records unavailable inputs and calculations as `NA`; no value is carried forward.",
            ]
        )

    diff10 = data["REAL10_VALIDATION_DIFF"].dropna().abs()
    diff30 = data["REAL30_VALIDATION_DIFF"].dropna().abs()
    quality_lines.extend(
        [
            "",
            "## Calculation validation",
            "",
            "The calculated implied real yields were compared with the independently downloaded FRED TIPS yields.",
            "",
            f"- 10Y maximum absolute difference versus DFII10: `{diff10.max():.4f}` percentage points "
            f"across `{len(diff10)}` matched dates.",
            f"- 30Y maximum absolute difference versus DFII30: `{diff30.max():.4f}` percentage points "
            f"across `{len(diff30)}` matched dates.",
            "- Small 10Y differences can occur because FRED publishes each input rounded to two decimals.",
            "",
        ]
    )
    (FRED_DIR / "data_quality_report.md").write_text("\n".join(quality_lines), encoding="utf-8")

    html = make_html(
        records,
        events,
        metadata,
        cme_rows,
        cme_metadata,
        intraday_rows,
        intraday_metadata,
    )
    (ROOT / "rate_dashboard.html").write_text(html, encoding="utf-8")


def make_html(
    records: list[dict[str, object]],
    events: list[dict[str, str]],
    metadata: dict[str, object],
    cme_rows: list[dict[str, str]],
    cme_metadata: dict[str, object],
    intraday_rows: list[dict[str, object]],
    intraday_metadata: dict[str, object],
) -> str:
    data_json = json.dumps(records, separators=(",", ":"), allow_nan=False)
    events_json = json.dumps(events, separators=(",", ":"), allow_nan=False)
    metadata_json = json.dumps(metadata, separators=(",", ":"), allow_nan=False)
    intraday_json = json.dumps(intraday_rows, separators=(",", ":"), allow_nan=False)
    intraday_metadata_json = json.dumps(intraday_metadata, separators=(",", ":"), allow_nan=False)
    fedwatch_table = make_cme_table(cme_rows, cme_metadata)
    snapshot_date = datetime.strptime(str(cme_metadata["snapshot_date"]), "%Y-%m-%d").strftime("%B %d, %Y")
    snapshot_title = (
        f"{snapshot_date}, {cme_metadata['snapshot_time']} {cme_metadata['timezone']}"
    )
    intraday_downloaded = "not downloaded"
    intraday_available = "No Yahoo intraday observations are available."
    intraday_status_class = "error"
    if intraday_metadata:
        downloaded = datetime.fromisoformat(str(intraday_metadata["downloaded_utc"]).replace("Z", "+00:00"))
        intraday_downloaded = downloaded.strftime("%B %d, %Y at %H:%M UTC")
        available_dates = intraday_metadata.get("common_available_trading_dates", [])
        requested_dates = intraday_metadata.get("requested_trading_dates", [])
        missing_dates = sorted(set(requested_dates) - set(available_dates))
        intraday_available = f"Available trading dates: {', '.join(available_dates) or 'none'}."
        if missing_dates:
            intraday_available += f" Pending: {', '.join(missing_dates)}."
            intraday_status_class = "status-warning"
        else:
            intraday_status_class = "status-complete"
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>U.S. Rate Analysis</title>
  <script src="code/vendor/d3.v7.9.0.min.js"></script>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: light-dark(#f7f8fa, #0e1117);
      --fg: light-dark(#17202a, #edf2f7);
      --muted: light-dark(#5d6772, #a6b0bb);
      --grid: light-dark(#d7dde4, #34404c);
      --frame: light-dark(#aeb8c2, #56616c);
      --popover: light-dark(#ffffff, #171d24);
      --popover-fg: light-dark(#17202a, #edf2f7);
      --series-1: light-dark(#1769aa, #62aeea);
      --series-2: light-dark(#c45b12, #ff9a56);
      --series-3: light-dark(#25805a, #66c79b);
      --series-4: light-dark(#7a4fb3, #b899e4);
      --series-5: light-dark(#b43858, #ec7796);
      --series-6: light-dark(#6d6f73, #b6bbc2);
      --event: light-dark(#7f2d2d, #e78b8b);
      --event-fomc: light-dark(#6a4c93, #bd9bea);
      --event-minutes: light-dark(#68737e, #9da8b3);
      --highlight: light-dark(#fff1bd, #493d18);
      --candle-up: light-dark(#18794e, #55c792);
      --candle-down: light-dark(#b4233c, #f06f83);
      --candle-doji: light-dark(#58636f, #aab4bf);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--fg); font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    main {{ width: min(1180px, 100%); margin: 0 auto; padding: 24px 18px 40px; }}
    h1 {{ margin: 0 0 6px; font-size: clamp(1.45rem, 3vw, 2.1rem); font-weight: 500; }}
    h2 {{ margin: 30px 0 4px; font-size: 1.08rem; font-weight: 500; }}
    h3 {{ margin: 22px 0 4px; font-size: 1rem; font-weight: 500; }}
    p {{ margin: 4px 0; color: var(--muted); }}
    .controls {{ display: grid; gap: 10px; margin: 20px 0 8px; }}
    .window-controls {{ display: flex; flex-wrap: wrap; align-items: end; gap: 10px 14px; }}
    .controls label {{ display: grid; gap: 4px; color: var(--muted); font-size: .85rem; }}
    input, button {{ font: inherit; color: var(--fg); background: transparent; border: 1px solid var(--frame); border-radius: 5px; padding: 7px 10px; }}
    button {{ cursor: pointer; }}
    button:hover {{ background: color-mix(in srgb, var(--fg) 8%, transparent); }}
    button:focus-visible, input:focus-visible {{ outline: 2px solid var(--series-1); outline-offset: 2px; }}
    .quick-ranges {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .chart-range-controls {{ align-items: center; margin: -4px 0 18px; color: var(--muted); font-size: .84rem; }}
    .chart-range-controls button {{ padding: 5px 9px; }}
    .event-controls {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; margin: 4px 0 6px; color: var(--muted); font-size: .86rem; }}
    .event-controls button {{ border: 0; padding: 4px 0; display: inline-flex; align-items: center; gap: 7px; }}
    .event-controls button[aria-pressed="false"] {{ opacity: .42; text-decoration: line-through; }}
    .event-swatch {{ width: 18px; height: 0; display: inline-block; border-top: 2px dashed var(--event); }}
    .event-swatch.fomc {{ border-color: var(--event-fomc); border-top-style: solid; }}
    .event-swatch.minutes {{ border-color: var(--event-minutes); border-top-style: dotted; }}
    .control-note {{ font-size: .84rem; margin-bottom: 14px; }}
    .chart-shell {{ position: relative; width: 100%; min-height: 430px; }}
    .intraday-controls {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 12px 0 8px; }}
    .intraday-controls span {{ color: var(--muted); font-size: .86rem; margin-right: 4px; }}
    .intraday-controls button[aria-pressed="true"] {{ background: color-mix(in srgb, var(--series-1) 12%, transparent); border-color: var(--series-1); }}
    .intraday-shell {{ min-height: 430px; }}
    .status-warning {{ color: var(--series-2); font-size: .86rem; }}
    .status-complete {{ color: var(--series-3); font-size: .86rem; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 6px 16px; min-height: 30px; align-items: center; }}
    .legend button {{ border: 0; padding: 4px 0; display: inline-flex; align-items: center; gap: 7px; }}
    .legend button[aria-pressed="false"] {{ opacity: .45; text-decoration: line-through; }}
    .swatch {{ width: 18px; height: 3px; display: inline-block; }}
    svg {{ width: 100%; height: 390px; overflow: visible; }}
    .axis text, .axis-title, .event-label {{ fill: var(--fg); font-size: 12px; }}
    .axis path, .axis line {{ stroke: var(--frame); }}
    .grid line {{ stroke: var(--grid); stroke-opacity: .55; }}
    .grid path {{ display: none; }}
    .plot-frame {{ fill: none; stroke: var(--frame); }}
    .series-line {{ fill: none; stroke-width: 2; }}
    .zero-line {{ stroke: var(--frame); stroke-dasharray: 4 4; }}
    .event-line {{ stroke: var(--event); stroke-width: 1.35; stroke-dasharray: 4 3; }}
    .event-line.event-fomc {{ stroke: var(--event-fomc); stroke-width: 1; stroke-dasharray: none; }}
    .event-line.event-minutes {{ stroke: var(--event-minutes); stroke-width: 1; stroke-dasharray: 1.5 3; }}
    .event-hit {{ stroke: transparent; stroke-width: 12; cursor: help; }}
    .event-label {{ fill: var(--event); font-size: 11px; }}
    .intraday-event-line {{ stroke: var(--event); stroke-width: 1.6; stroke-dasharray: 5 3; }}
    .intraday-event-label {{ fill: var(--event); font-size: 11px; }}
    .candle-wick {{ stroke-width: 1; shape-rendering: crispEdges; }}
    .candle-body {{ stroke-width: 1; shape-rendering: crispEdges; }}
    .candle-up {{ fill: var(--candle-up); stroke: var(--candle-up); }}
    .candle-down {{ fill: var(--candle-down); stroke: var(--candle-down); }}
    .candle-doji {{ fill: var(--candle-doji); stroke: var(--candle-doji); }}
    .candle-key {{ display: flex; gap: 16px; margin: 8px 0 2px; color: var(--muted); font-size: .84rem; }}
    .candle-key span {{ display: inline-flex; align-items: center; gap: 6px; }}
    .candle-key i {{ width: 11px; height: 11px; display: inline-block; }}
    .hover-guide {{ stroke: var(--fg); stroke-width: 1; stroke-opacity: .45; pointer-events: none; }}
    .hover-dot {{ stroke: var(--bg); stroke-width: 1.5; pointer-events: none; }}
    .overlay {{ fill: transparent; cursor: grab; touch-action: pan-y; }}
    body.plot-dragging, body.plot-dragging .overlay {{ cursor: grabbing; user-select: none; }}
    .tooltip {{ position: absolute; pointer-events: none; display: none; z-index: 3; padding: 9px 11px; border: 1px solid var(--frame); border-radius: 5px; background: var(--popover); color: var(--popover-fg); font-size: 12px; line-height: 1.45; max-width: 260px; }}
    .tooltip strong {{ font-weight: 500; }}
    .tooltip-row {{ display: grid; grid-template-columns: 10px 1fr auto; gap: 7px; align-items: center; }}
    .tooltip-dot {{ width: 8px; height: 8px; border-radius: 50%; }}
    .tooltip-events {{ margin-top: 7px; padding-top: 6px; border-top: 1px solid var(--grid); }}
    .tooltip-event {{ margin-top: 3px; }}
    .table-scroll {{ overflow-x: auto; margin-top: 12px; border: 1px solid var(--frame); border-radius: 5px; }}
    table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--grid); text-align: right; white-space: nowrap; font-size: .84rem; }}
    th:first-child {{ position: sticky; left: 0; z-index: 1; background: var(--bg); text-align: left; }}
    thead th {{ background: color-mix(in srgb, var(--fg) 7%, var(--bg)); font-weight: 500; }}
    tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
    .current-target {{ background: color-mix(in srgb, var(--series-1) 10%, transparent); }}
    .row-maximum {{ background: var(--highlight); font-weight: 600; }}
    .fedwatch-note {{ font-size: .84rem; }}
    footer {{ margin-top: 30px; padding-top: 14px; border-top: 1px solid var(--grid); font-size: .85rem; color: var(--muted); }}
    footer a {{ color: var(--series-1); }}
    .error {{ color: var(--event); font-weight: 500; }}
    @media (max-width: 620px) {{
      main {{ padding-inline: 10px; }}
      .chart-shell {{ min-height: 400px; }}
      svg {{ height: 360px; }}
      .event-label {{ display: none; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>U.S. Rate Analysis</h1>
  <p>Daily FRED observations from 2019-01-01; missing values remain missing.</p>

  <section class="controls" aria-label="Shared date range controls">
    <div class="window-controls" aria-label="Selected time window">
      <label>Start <input id="start-date" type="date"></label>
      <label>End <input id="end-date" type="date"></label>
      <button id="apply-range" type="button">Apply</button>
      <button id="zoom-in" type="button" aria-label="Zoom in on selected date range">Zoom in</button>
      <button id="zoom-out" type="button" aria-label="Zoom out from selected date range">Zoom out</button>
      <span id="range-error" class="error" role="alert"></span>
    </div>
    <div class="quick-ranges" aria-label="Quick ranges">
      <button type="button" data-range="1w">1 week</button>
      <button type="button" data-range="1m">1 month</button>
      <button type="button" data-range="6m">6 months</button>
      <button type="button" data-range="ytd">YTD</button>
      <button type="button" data-range="1y">1 year</button>
      <button type="button" data-range="3y">3 years</button>
      <button type="button" data-range="5y">5 years</button>
      <button type="button" data-range="all">All</button>
    </div>
  </section>

  <div class="event-controls" aria-label="Event marker controls">
    <span>Events:</span>
    <button type="button" data-event-type="major" aria-pressed="true"><span class="event-swatch"></span>Major policy / Jackson Hole</button>
    <button type="button" data-event-type="fomc" aria-pressed="true"><span class="event-swatch fomc"></span>FOMC meetings</button>
    <button type="button" data-event-type="minutes" aria-pressed="true"><span class="event-swatch minutes"></span>Minutes releases</button>
  </div>
  <p class="control-note">Use the buttons or mouse wheel over any plot to zoom. Drag a plot horizontally to pan. The shared date range and every y-axis remain synchronized.</p>

  <section>
    <h2>Nominal Treasury yields</h2>
    <p>2-year, 10-year, and 30-year constant-maturity yields.</p>
    <div id="legend-nominal" class="legend" aria-label="Nominal yield series"></div>
    <div id="chart-nominal" class="chart-shell" role="img" aria-label="Interactive chart of 2-year, 10-year, and 30-year Treasury yields"><div class="tooltip" role="tooltip"></div></div>
    <div class="quick-ranges chart-range-controls" aria-label="Nominal yield time range">
      <span>Range:</span><button type="button" data-range="1w">1 week</button><button type="button" data-range="1m">1 month</button><button type="button" data-range="6m">6 months</button><button type="button" data-range="ytd">YTD</button><button type="button" data-range="1y">1 year</button><button type="button" data-range="3y">3 years</button><button type="button" data-range="5y">5 years</button><button type="button" data-range="all">All</button>
    </div>
  </section>

  <section>
    <h2>Treasury curve spreads</h2>
    <p>Longer-maturity yield minus shorter-maturity yield. Values are percentage points.</p>
    <div id="legend-spreads" class="legend" aria-label="Treasury spread series"></div>
    <div id="chart-spreads" class="chart-shell" role="img" aria-label="Interactive chart of 2s10s, 2s30s, and 10s30s Treasury spreads"><div class="tooltip" role="tooltip"></div></div>
    <div class="quick-ranges chart-range-controls" aria-label="Treasury spread time range">
      <span>Range:</span><button type="button" data-range="1w">1 week</button><button type="button" data-range="1m">1 month</button><button type="button" data-range="6m">6 months</button><button type="button" data-range="ytd">YTD</button><button type="button" data-range="1y">1 year</button><button type="button" data-range="3y">3 years</button><button type="button" data-range="5y">5 years</button><button type="button" data-range="all">All</button>
    </div>
  </section>

  <section>
    <h2>Breakeven inflation and implied real yields</h2>
    <p>10Y breakeven is FRED T10YIE. Daily 30Y breakeven is calculated as DGS30 minus DFII30.</p>
    <div id="legend-components" class="legend" aria-label="Breakeven and real-yield series"></div>
    <div id="chart-components" class="chart-shell" role="img" aria-label="Interactive chart of 10-year and 30-year breakeven inflation and implied real yields"><div class="tooltip" role="tooltip"></div></div>
    <div class="quick-ranges chart-range-controls" aria-label="Breakeven and real-yield time range">
      <span>Range:</span><button type="button" data-range="1w">1 week</button><button type="button" data-range="1m">1 month</button><button type="button" data-range="6m">6 months</button><button type="button" data-range="ytd">YTD</button><button type="button" data-range="1y">1 year</button><button type="button" data-range="3y">3 years</button><button type="button" data-range="5y">5 years</button><button type="button" data-range="all">All</button>
    </div>
  </section>

  <section id="intraday-event-study">
    <h2>Intraday event study — Warsh at Jackson Hole</h2>
    <p>One-minute Yahoo Finance OHLC yields around Chair Kevin Warsh’s August 28, 2026 keynote at 10:00 a.m. ET. Candle bodies show open-to-close and wicks show low-to-high. The requested window is the previous trading day, event day, and next trading day.</p>
    <p class="{intraday_status_class}">{escape(intraday_available)} Snapshot downloaded {escape(intraday_downloaded)}.</p>
    <div class="intraday-controls" aria-label="Intraday event range">
      <span>View:</span>
      <button type="button" data-intraday-range="full">Full available window</button>
      <button type="button" data-intraday-range="day" aria-pressed="true">Event day</button>
      <button type="button" data-intraday-range="reaction">−60 / +120 minutes</button>
      <button id="intraday-zoom-in" type="button">Zoom in</button>
      <button id="intraday-zoom-out" type="button">Zoom out</button>
    </div>

    <div class="candle-key" aria-label="Candlestick direction key"><span><i class="candle-up"></i>Close &gt; open</span><span><i class="candle-down"></i>Close &lt; open</span><span><i class="candle-doji"></i>Close = open</span></div>

    <h3>10Y Treasury yield — ^TNX</h3>
    <p>One-minute OHLC candlesticks. Yahoo reports only zero volume for this yield index, so volume is intentionally omitted.</p>
    <div id="chart-intraday-10y" class="chart-shell intraday-shell" role="img" aria-label="Interactive one-minute candlestick chart of the 10-year Treasury yield"><div class="tooltip" role="tooltip"></div></div>

    <h3>30Y Treasury yield — ^TYX</h3>
    <p>One-minute OHLC candlesticks. Yahoo reports only zero volume for this yield index, so volume is intentionally omitted.</p>
    <div id="chart-intraday-30y" class="chart-shell intraday-shell" role="img" aria-label="Interactive one-minute candlestick chart of the 30-year Treasury yield"><div class="tooltip" role="tooltip"></div></div>
  </section>

  <section>
    <h2>CME FedWatch probabilities — snapshot {snapshot_title}</h2>
    <p>Market-implied probabilities by FOMC meeting and target range. Current target: {escape(str(cme_metadata['current_target_bps']))} bps. Verify this snapshot at the <a href="{escape(str(cme_metadata['source']))}">CME FedWatch Tool</a>.</p>
    {fedwatch_table}
    <p class="fedwatch-note">Probabilities are a point-in-time CME snapshot, not a forecast by the Federal Reserve. A dash means the CME source cell was blank; it has not been imputed.</p>
  </section>

  <footer>
    FRED data through <span id="data-through"></span>. Intraday event data are from Yahoo Finance; FOMC event dates are from the Federal Reserve; FedWatch is from CME. Source definitions and reproducible links are in <a href="README.md">README.md</a>.
  </footer>
</main>

<script>
const RAW_DATA = {data_json};
const EVENTS = {events_json};
const META = {metadata_json};
const RAW_INTRADAY_DATA = {intraday_json};
const INTRADAY_META = {intraday_metadata_json};

if (typeof d3 === 'undefined') {{
  document.getElementById('range-error').textContent = 'D3 library was not loaded. Run the documented refresh/build steps.';
  throw new Error('D3 unavailable');
}}

const parseDate = d3.timeParse('%Y-%m-%d');
const formatDate = d3.timeFormat('%Y-%m-%d');
const DATA = RAW_DATA.map(d => ({{...d, date: parseDate(d.date)}}));
const parsedEvents = EVENTS.map(e => ({{...e, date: parseDate(e.date)}}));
const EVENT_TYPES = {{
  major: {{label:'Major event', priority:3}},
  fomc: {{label:'FOMC meeting', priority:2}},
  minutes: {{label:'Minutes release', priority:1}}
}};
const hiddenEventTypes = new Set();
const minDate = d3.min(DATA, d => d.date);
const maxDataDate = d3.max(DATA, d => d.date);
const maxViewDate = d3.timeDay.offset(maxDataDate, 7);
let selectedStart = minDate;
let selectedEnd = maxDataDate;

const charts = [
  {{
    id: 'chart-nominal', legend: 'legend-nominal', includeZero: false,
    series: [
      {{key:'DGS2', label:'2Y nominal', color:'var(--series-1)'}},
      {{key:'DGS10', label:'10Y nominal', color:'var(--series-2)'}},
      {{key:'DGS30', label:'30Y nominal', color:'var(--series-3)'}}
    ]
  }},
  {{
    id: 'chart-spreads', legend: 'legend-spreads', includeZero: false,
    series: [
      {{key:'SPREAD_2Y10Y', label:'10Y − 2Y', color:'var(--series-1)'}},
      {{key:'SPREAD_2Y30Y', label:'30Y − 2Y', color:'var(--series-2)'}},
      {{key:'SPREAD_10Y30Y', label:'30Y − 10Y', color:'var(--series-3)'}}
    ]
  }},
  {{
    id: 'chart-components', legend: 'legend-components', includeZero: false,
    series: [
      {{key:'BE10', label:'10Y breakeven', color:'var(--series-1)'}},
      {{key:'REAL10_IMPLIED', label:'10Y implied real', color:'var(--series-2)'}},
      {{key:'BE30_CALC', label:'30Y breakeven', color:'var(--series-3)'}},
      {{key:'REAL30_IMPLIED', label:'30Y implied real', color:'var(--series-4)'}}
    ]
  }}
];

for (const chart of charts) {{
  chart.hidden = new Set();
  const legend = d3.select('#' + chart.legend);
  for (const s of chart.series) {{
    const button = legend.append('button').attr('type','button').attr('aria-pressed','true');
    button.append('span').attr('class','swatch').style('background',s.color);
    button.append('span').text(s.label);
    button.on('click', () => {{
      if (chart.hidden.has(s.key)) chart.hidden.delete(s.key); else chart.hidden.add(s.key);
      button.attr('aria-pressed', chart.hidden.has(s.key) ? 'false' : 'true');
      drawChart(chart);
    }});
  }}
}}

const startInput = document.getElementById('start-date');
const endInput = document.getElementById('end-date');
startInput.min = formatDate(minDate); startInput.max = formatDate(maxDataDate); startInput.value = formatDate(minDate);
endInput.min = formatDate(minDate); endInput.max = formatDate(maxDataDate); endInput.value = formatDate(maxDataDate);
document.getElementById('data-through').textContent = META.last_available_date;

function setRange(start, end) {{
  const error = document.getElementById('range-error');
  if (!start || !end || start > end || end < minDate || start > maxDataDate) {{
    error.textContent = 'Choose a valid date range within the available data.';
    return;
  }}
  error.textContent = '';
  const dayStart = d3.timeDay.floor(start);
  const dayEnd = d3.timeDay.floor(end);
  selectedStart = dayStart < minDate ? minDate : dayStart;
  selectedEnd = dayEnd > maxDataDate ? maxDataDate : dayEnd;
  startInput.value = formatDate(selectedStart);
  endInput.value = formatDate(selectedEnd);
  drawAll();
}}

document.getElementById('apply-range').addEventListener('click', () => setRange(parseDate(startInput.value), parseDate(endInput.value)));
document.querySelectorAll('[data-range]').forEach(button => button.addEventListener('click', () => {{
  const code = button.dataset.range;
  if (code === 'all') return setRange(minDate, maxDataDate);
  const end = maxDataDate;
  let start;
  if (code === '1w') start = d3.timeDay.offset(end, -7);
  else if (code === '1m') start = d3.timeMonth.offset(end, -1);
  else if (code === '6m') start = d3.timeMonth.offset(end, -6);
  else if (code === 'ytd') start = new Date(end.getFullYear(), 0, 1);
  else start = d3.timeYear.offset(end, -Number.parseInt(code));
  setRange(start, end);
}}));

const DAY_MS = 24 * 60 * 60 * 1000;
let dragActive = false;
let dragPointerId = null;
let dragStartClientX = 0;
let dragStartRange = null;
let pendingPanRange = null;

function shiftedRange(start, end, shiftMs) {{
  let startMs = start.getTime() + shiftMs;
  let endMs = end.getTime() + shiftMs;
  if (startMs < minDate.getTime()) {{ endMs += minDate.getTime() - startMs; startMs = minDate.getTime(); }}
  if (endMs > maxDataDate.getTime()) {{ startMs -= endMs - maxDataDate.getTime(); endMs = maxDataDate.getTime(); }}
  return [new Date(Math.max(minDate.getTime(), startMs)), new Date(Math.min(maxDataDate.getTime(), endMs))];
}}

function zoomAround(anchor, factor) {{
  const fullSpan = maxDataDate - minDate;
  const currentSpan = Math.max(DAY_MS, selectedEnd - selectedStart);
  const nextSpan = Math.max(7 * DAY_MS, Math.min(fullSpan, currentSpan * factor));
  const ratio = Math.max(0, Math.min(1, (anchor - selectedStart) / currentSpan));
  let startMs = anchor.getTime() - nextSpan * ratio;
  let endMs = startMs + nextSpan;
  if (startMs < minDate.getTime()) {{ endMs += minDate.getTime() - startMs; startMs = minDate.getTime(); }}
  if (endMs > maxDataDate.getTime()) {{ startMs -= endMs - maxDataDate.getTime(); endMs = maxDataDate.getTime(); }}
  setRange(new Date(Math.max(minDate.getTime(), startMs)), new Date(Math.min(maxDataDate.getTime(), endMs)));
}}

document.getElementById('zoom-in').addEventListener('click', () => zoomAround(new Date((selectedStart.getTime() + selectedEnd.getTime()) / 2), 0.5));
document.getElementById('zoom-out').addEventListener('click', () => zoomAround(new Date((selectedStart.getTime() + selectedEnd.getTime()) / 2), 2));
document.querySelectorAll('[data-event-type]').forEach(button => button.addEventListener('click', () => {{
  const eventType = button.dataset.eventType;
  if (hiddenEventTypes.has(eventType)) hiddenEventTypes.delete(eventType); else hiddenEventTypes.add(eventType);
  button.setAttribute('aria-pressed', hiddenEventTypes.has(eventType) ? 'false' : 'true');
  drawAll();
}}));

function finite(value) {{ return typeof value === 'number' && Number.isFinite(value); }}

function adaptiveYDomain(values, includeZero) {{
  let [low, high] = d3.extent(values);
  if (includeZero) {{ low = Math.min(0, low); high = Math.max(0, high); }}
  if (low === high) {{ low -= 0.1; high += 0.1; }}
  const span = Math.max(0.01, high - low);
  const padding = Math.max(0.03, span * 0.06);
  low -= padding;
  high += padding;
  const paddedSpan = high - low;
  const increment = paddedSpan <= 0.5 ? 0.05 : paddedSpan <= 2.5 ? 0.1 : paddedSpan <= 5 ? 0.25 : 0.5;
  low = Math.floor((low + 1e-9) / increment) * increment;
  high = Math.ceil((high - 1e-9) / increment) * increment;
  return [Number(low.toFixed(4)), Number(high.toFixed(4))];
}}

function interpolateAt(values, key, target) {{
  const clean = values.filter(d => finite(d[key]));
  if (!clean.length || target < clean[0].date || target > clean[clean.length - 1].date) return null;
  const i = d3.bisector(d => d.date).left(clean, target);
  if (i === 0) return clean[0][key];
  if (i >= clean.length) return clean[clean.length - 1][key];
  const a = clean[i - 1], b = clean[i];
  const span = b.date - a.date;
  if (!span) return a[key];
  return a[key] + (b[key] - a[key]) * ((target - a.date) / span);
}}

function drawChart(chart) {{
  const shell = document.getElementById(chart.id);
  const tooltip = d3.select(shell).select('.tooltip');
  d3.select(shell).selectAll('svg').remove();
  const width = Math.max(320, shell.clientWidth);
  const height = width < 620 ? 350 : 390;
  const margin = {{top: 30, right: 24, bottom: 58, left: width < 430 ? 56 : 68}};
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const visibleSeries = chart.series.filter(s => !chart.hidden.has(s.key));
  const view = DATA.filter(d => d.date >= selectedStart && d.date <= selectedEnd);
  const displayEnd = selectedEnd.getTime() === maxDataDate.getTime() ? maxViewDate : selectedEnd;

  const svg = d3.select(shell).append('svg').attr('viewBox', `0 0 ${{width}} ${{height}}`).attr('data-x-start',formatDate(selectedStart)).attr('data-x-end',formatDate(displayEnd)).attr('data-selection-end',formatDate(selectedEnd));
  const g = svg.append('g').attr('transform', `translate(${{margin.left}},${{margin.top}})`);
  const x = d3.scaleTime().domain([selectedStart, displayEnd]).range([0, innerWidth]);
  let yValues = [];
  for (const s of visibleSeries) yValues.push(...view.map(d => d[s.key]).filter(finite));
  if (!yValues.length) yValues = [0, 1];
  const y = d3.scaleLinear().domain(adaptiveYDomain(yValues, chart.includeZero)).range([innerHeight, 0]);
  svg.attr('data-y-min', y.domain()[0]).attr('data-y-max', y.domain()[1]);

  g.append('rect').attr('class','plot-frame').attr('width',innerWidth).attr('height',innerHeight);
  g.append('g').attr('class','grid').call(d3.axisLeft(y).ticks(6).tickSize(-innerWidth).tickFormat(''));
  if (y.domain()[0] <= 0 && y.domain()[1] >= 0) g.append('line').attr('class','zero-line').attr('x2',innerWidth).attr('y1',y(0)).attr('y2',y(0));

  const spanDays = Math.max(1, (displayEnd - selectedStart) / DAY_MS);
  const showDetailedDates = spanDays <= 400;
  const tickCount = width < 430 ? (showDetailedDates ? 3 : 4) : width < 500 ? 4 : Math.min(8, Math.max(4, Math.floor(innerWidth / 110)));
  const xTickFormat = showDetailedDates ? d3.timeFormat('%Y-%m-%d') : spanDays <= 1100 ? d3.timeFormat('%Y-%m') : d3.timeFormat('%Y');
  const xAxis = d3.axisBottom(x).tickFormat(xTickFormat);
  if (showDetailedDates) {{
    const exactTicks = d3.range(tickCount).map(i => d3.timeDay.round(new Date(selectedStart.getTime() + (displayEnd - selectedStart) * i / (tickCount - 1))));
    xAxis.tickValues([...new Map(exactTicks.map(date => [formatDate(date), date])).values()]);
  }} else {{
    xAxis.ticks(tickCount);
  }}
  g.append('g').attr('class','axis').attr('transform',`translate(0,${{innerHeight}})`).call(xAxis);
  g.append('g').attr('class','axis').call(d3.axisLeft(y).ticks(6).tickFormat(d => d.toFixed(1)));
  g.append('text').attr('class','axis-title').attr('text-anchor','middle').attr('x',innerWidth/2).attr('y',innerHeight+48).text('Date');
  g.append('text').attr('class','axis-title').attr('text-anchor','middle').attr('transform',`translate(${{-48}},${{innerHeight/2}}) rotate(-90)`).text('Percent (%)');

  const line = key => d3.line().defined(d => finite(d[key])).x(d => x(d.date)).y(d => y(d[key]));
  for (const s of visibleSeries) {{
    g.append('path').datum(view).attr('class','series-line').attr('stroke',s.color).attr('d',line(s.key));
  }}

  const activeEvents = parsedEvents.filter(e => !hiddenEventTypes.has(e.event_type));
  const visibleEventGroups = d3.groups(
    activeEvents.filter(e => e.date >= selectedStart && e.date <= displayEnd),
    e => formatDate(e.date)
  ).map(([dateKey, items]) => ({{
    date: parseDate(dateKey),
    items,
    eventType: items.slice().sort((a,b) => EVENT_TYPES[b.event_type].priority - EVENT_TYPES[a.event_type].priority)[0].event_type
  }}));
  const eventLayer = g.append('g');
  let lastEventLabelX = -Infinity;
  const showStandardLabels = (displayEnd - selectedStart) <= 400 * DAY_MS;
  visibleEventGroups.forEach(group => {{
    const ex = x(group.date);
    eventLayer.append('line').attr('class',`event-line event-${{group.eventType}}`).attr('x1',ex).attr('x2',ex).attr('y1',0).attr('y2',innerHeight);
    const majorItem = group.items.find(item => item.event_type === 'major');
    const labelItem = majorItem || group.items[0];
    if ((majorItem || showStandardLabels) && ex - lastEventLabelX >= 28) {{
      eventLayer.append('text').attr('class','event-label').attr('transform',`translate(${{ex+5}},12) rotate(90)`).text(labelItem.short_label);
      lastEventLabelX = ex;
    }}
  }});

  const guide = g.append('line').attr('class','hover-guide').attr('y1',0).attr('y2',innerHeight).style('display','none');
  const markerLayer = g.append('g').style('display','none');
  const overlay = g.append('rect').attr('class','overlay').attr('data-chart-hit','').attr('data-chart-hover-overlay','cross-series').attr('width',innerWidth).attr('height',innerHeight);

  function finishPan(event, commit) {{
    if (!dragActive || event.pointerId !== dragPointerId) return;
    const moved = Math.abs(event.clientX - dragStartClientX) >= 3;
    const finalRange = pendingPanRange;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragActive = false;
    dragPointerId = null;
    dragStartRange = null;
    pendingPanRange = null;
    document.body.classList.remove('plot-dragging');
    if (commit && moved && finalRange) setRange(finalRange[0], finalRange[1]);
    else {{ startInput.value = formatDate(selectedStart); endInput.value = formatDate(selectedEnd); }}
  }}

  overlay.on('pointerdown', event => {{
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    dragActive = true;
    dragPointerId = event.pointerId;
    dragStartClientX = event.clientX;
    dragStartRange = [new Date(selectedStart), new Date(selectedEnd)];
    pendingPanRange = dragStartRange;
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add('plot-dragging');
    guide.style('display','none'); markerLayer.style('display','none'); tooltip.style('display','none');
    event.preventDefault();
  }}).on('pointermove', event => {{
    if (dragActive && event.pointerId === dragPointerId && dragStartRange) {{
      const deltaPixels = event.clientX - dragStartClientX;
      const spanMs = dragStartRange[1] - dragStartRange[0];
      pendingPanRange = shiftedRange(dragStartRange[0], dragStartRange[1], -(deltaPixels / innerWidth) * spanMs);
      startInput.value = formatDate(d3.timeDay.floor(pendingPanRange[0]));
      endInput.value = formatDate(d3.timeDay.floor(pendingPanRange[1]));
      return;
    }}
    const [mx] = d3.pointer(event, overlay.node());
    const date = x.invert(Math.max(0, Math.min(innerWidth, mx)));
    guide.attr('x1',x(date)).attr('x2',x(date)).style('display',null);
    markerLayer.selectAll('*').remove(); markerLayer.style('display',null);
    const rows = [];
    for (const s of visibleSeries) {{
      const value = interpolateAt(view, s.key, date);
      if (value === null) continue;
      markerLayer.append('circle').attr('class','hover-dot').attr('cx',x(date)).attr('cy',y(value)).attr('r',4).attr('fill',s.color);
      rows.push(`<div class="tooltip-row"><span class="tooltip-dot" style="background:${{s.color}}"></span><span>${{s.label}}</span><span>${{value.toFixed(3)}}%</span></div>`);
    }}
    const nearbyEvents = visibleEventGroups.filter(group => Math.abs(x(group.date) - x(date)) <= 7);
    const eventHtml = nearbyEvents.length ? `<div class="tooltip-events">${{nearbyEvents.flatMap(group => group.items.map(item => `<div class="tooltip-event"><strong>${{EVENT_TYPES[item.event_type].label}}:</strong> ${{item.label}}</div>`)).join('')}}</div>` : '';
    tooltip.html(`<strong>${{formatDate(date)}}</strong>${{rows.join('')}}${{eventHtml}}`).style('display','block');
    const localX = margin.left + x(date);
    const tipWidth = 245;
    tooltip.style('left',`${{Math.min(width-tipWidth-8, Math.max(8, localX+12))}}px`).style('top',`${{margin.top+8}}px`);
  }}).on('pointerup', event => finishPan(event, true))
    .on('pointercancel', event => finishPan(event, false))
    .on('pointerleave', () => {{ guide.style('display','none'); markerLayer.style('display','none'); tooltip.style('display','none'); }})
    .on('wheel', event => {{
      if (dragActive) return;
      event.preventDefault();
      const [mx] = d3.pointer(event, overlay.node());
      zoomAround(x.invert(Math.max(0, Math.min(innerWidth, mx))), event.deltaY > 0 ? 1.25 : 0.8);
    }}, {{passive:false}});
}}

let drawIntradayAll = () => {{}};

function initializeIntradayCharts() {{
  if (!RAW_INTRADAY_DATA.length || !INTRADAY_META.event_timestamp_et) {{
    document.querySelector('#intraday-event-study .error')?.insertAdjacentText('beforeend', ' Run the Yahoo event-data downloader and rebuild the dashboard.');
    return;
  }}

  const intradayData = RAW_INTRADAY_DATA.map(d => ({{...d, time:new Date(d.timestamp_utc)}}));
  const yieldData = intradayData.filter(d => d.ticker === '^TNX' || d.ticker === '^TYX');
  if (!yieldData.length) {{
    document.querySelector('#intraday-event-study .error')?.insertAdjacentText('beforeend', ' No ^TNX or ^TYX OHLC observations are available.');
    return;
  }}
  const eventTime = new Date(INTRADAY_META.event_timestamp_et);
  const eventDateText = INTRADAY_META.event_timestamp_et.slice(0, 10);
  const eventOffset = INTRADAY_META.event_timestamp_et.slice(-6);
  const eventDayStart = new Date(`${{eventDateText}}T00:00:00${{eventOffset}}`);
  const eventDayEnd = new Date(`${{eventDateText}}T23:59:59${{eventOffset}}`);
  const intradayMin = d3.min(yieldData, d => d.time);
  const intradayMax = d3.max(yieldData, d => d.time);
  let intradayStart = eventDayStart < intradayMin ? intradayMin : eventDayStart;
  let intradayEnd = eventDayEnd > intradayMax ? intradayMax : eventDayEnd;
  let activeIntradayRange = 'day';

  const intradayCharts = [
    {{
      id:'chart-intraday-10y', ticker:'^TNX', label:'10Y yield', yTitle:'10Y yield (%)'
    }},
    {{
      id:'chart-intraday-30y', ticker:'^TYX', label:'30Y yield', yTitle:'30Y yield (%)'
    }}
  ];

  function updateIntradayRangeButtons() {{
    document.querySelectorAll('[data-intraday-range]').forEach(button =>
      button.setAttribute('aria-pressed', button.dataset.intradayRange === activeIntradayRange ? 'true' : 'false')
    );
  }}

  function setIntradayRange(start, end, rangeCode='custom') {{
    let startMs = Math.max(intradayMin.getTime(), start.getTime());
    let endMs = Math.min(intradayMax.getTime(), end.getTime());
    if (endMs <= startMs) return;
    intradayStart = new Date(startMs);
    intradayEnd = new Date(endMs);
    activeIntradayRange = rangeCode;
    updateIntradayRangeButtons();
    drawIntradayAll();
  }}

  document.querySelectorAll('[data-intraday-range]').forEach(button => button.addEventListener('click', () => {{
    const code = button.dataset.intradayRange;
    if (code === 'full') setIntradayRange(intradayMin, intradayMax, code);
    else if (code === 'day') setIntradayRange(eventDayStart, eventDayEnd, code);
    else setIntradayRange(new Date(eventTime.getTime() - 60 * 60 * 1000), new Date(eventTime.getTime() + 120 * 60 * 1000), code);
  }}));

  function zoomIntraday(factor, anchor=new Date((intradayStart.getTime() + intradayEnd.getTime()) / 2)) {{
    const fullSpan = intradayMax - intradayMin;
    const currentSpan = Math.max(15 * 60 * 1000, intradayEnd - intradayStart);
    const nextSpan = Math.max(15 * 60 * 1000, Math.min(fullSpan, currentSpan * factor));
    const ratio = Math.max(0, Math.min(1, (anchor - intradayStart) / currentSpan));
    let startMs = anchor.getTime() - nextSpan * ratio;
    let endMs = startMs + nextSpan;
    if (startMs < intradayMin.getTime()) {{ endMs += intradayMin.getTime() - startMs; startMs = intradayMin.getTime(); }}
    if (endMs > intradayMax.getTime()) {{ startMs -= endMs - intradayMax.getTime(); endMs = intradayMax.getTime(); }}
    setIntradayRange(new Date(startMs), new Date(endMs));
  }}

  document.getElementById('intraday-zoom-in').addEventListener('click', () => zoomIntraday(0.5));
  document.getElementById('intraday-zoom-out').addEventListener('click', () => zoomIntraday(2));

  const etTime = new Intl.DateTimeFormat('en-US', {{timeZone:'America/New_York', hour:'2-digit', minute:'2-digit', hour12:false}});
  const etDateTime = new Intl.DateTimeFormat('en-US', {{timeZone:'America/New_York', month:'short', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}});
  const etTooltip = new Intl.DateTimeFormat('en-CA', {{timeZone:'America/New_York', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}});

  function shiftedIntradayRange(start, end, shiftMs) {{
    let startMs = start.getTime() + shiftMs;
    let endMs = end.getTime() + shiftMs;
    if (startMs < intradayMin.getTime()) {{ endMs += intradayMin.getTime() - startMs; startMs = intradayMin.getTime(); }}
    if (endMs > intradayMax.getTime()) {{ startMs -= endMs - intradayMax.getTime(); endMs = intradayMax.getTime(); }}
    return [new Date(startMs), new Date(endMs)];
  }}

  let intradayDrag = null;

  function drawIntradayChart(chart) {{
    const shell = document.getElementById(chart.id);
    const tooltip = d3.select(shell).select('.tooltip');
    d3.select(shell).selectAll('svg').remove();
    const width = Math.max(320, shell.clientWidth);
    const height = width < 620 ? 350 : 390;
    const margin = {{top:30, right:24, bottom:58, left:width < 430 ? 58 : 70}};
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const view = yieldData
      .filter(d => d.ticker === chart.ticker && d.time >= intradayStart && d.time <= intradayEnd)
      .filter(d => finite(d.open) && finite(d.high) && finite(d.low) && finite(d.close))
      .sort((a,b) => a.time-b.time);
    const svg = d3.select(shell).append('svg').attr('viewBox',`0 0 ${{width}} ${{height}}`).attr('data-x-start',intradayStart.toISOString()).attr('data-x-end',intradayEnd.toISOString());
    svg.append('title').text(`One-minute OHLC candlesticks for ${{chart.label}} around the Jackson Hole speech`);
    const g = svg.append('g').attr('transform',`translate(${{margin.left}},${{margin.top}})`);
    const x = d3.scaleTime().domain([intradayStart,intradayEnd]).range([0,innerWidth]);
    let yValues = view.flatMap(d => [d.low,d.high]).filter(finite);
    if (!yValues.length) yValues = [0,1];
    let [yLow,yHigh] = d3.extent(yValues);
    if (yLow === yHigh) {{ yLow -= 0.01; yHigh += 0.01; }}
    const yPad = Math.max(0.002, (yHigh-yLow)*0.06);
    const y = d3.scaleLinear().domain([yLow-yPad,yHigh+yPad]).nice(6).range([innerHeight,0]);
    svg.attr('data-y-min',y.domain()[0]).attr('data-y-max',y.domain()[1]);

    g.append('rect').attr('class','plot-frame').attr('data-chart-frame','').attr('width',innerWidth).attr('height',innerHeight);
    g.append('g').attr('class','grid').call(d3.axisLeft(y).ticks(6).tickSize(-innerWidth).tickFormat(''));
    const spanHours = (intradayEnd-intradayStart)/(60*60*1000);
    const tickCount = width < 430 ? 3 : Math.min(8,Math.max(4,Math.floor(innerWidth/120)));
    const xFormat = spanHours <= 24 ? etTime.format : etDateTime.format;
    g.append('g').attr('class','axis').attr('transform',`translate(0,${{innerHeight}})`).call(d3.axisBottom(x).ticks(tickCount).tickFormat(xFormat));
    g.append('g').attr('class','axis').call(d3.axisLeft(y).ticks(6).tickFormat(d => Number(d).toFixed(3)));
    g.append('text').attr('class','axis-title').attr('data-axis','x').attr('text-anchor','middle').attr('x',innerWidth/2).attr('y',innerHeight+48).text('Time (ET)');
    g.append('text').attr('class','axis-title').attr('data-axis','y').attr('text-anchor','middle').attr('transform',`translate(${{-50}},${{innerHeight/2}}) rotate(-90)`).text(chart.yTitle);

    const candleWidth = Math.max(1,Math.min(9,(innerWidth/Math.max(1,view.length))*0.72));
    const candles = g.append('g').attr('class','candles');
    candles.selectAll('line').data(view).join('line')
      .attr('class',d => `candle-wick ${{d.close>d.open?'candle-up':d.close<d.open?'candle-down':'candle-doji'}}`)
      .attr('x1',d => x(d.time)).attr('x2',d => x(d.time))
      .attr('y1',d => y(d.high)).attr('y2',d => y(d.low));
    candles.selectAll('rect').data(view).join('rect')
      .attr('class',d => `candle-body ${{d.close>d.open?'candle-up':d.close<d.open?'candle-down':'candle-doji'}}`)
      .attr('x',d => x(d.time)-candleWidth/2).attr('width',candleWidth)
      .attr('y',d => y(Math.max(d.open,d.close)))
      .attr('height',d => Math.max(1,Math.abs(y(d.open)-y(d.close))));

    if (eventTime >= intradayStart && eventTime <= intradayEnd) {{
      const eventX = x(eventTime);
      g.append('line').attr('class','intraday-event-line').attr('x1',eventX).attr('x2',eventX).attr('y1',0).attr('y2',innerHeight);
      g.append('text').attr('class','intraday-event-label').attr('transform',`translate(${{eventX+5}},10) rotate(90)`).text('Warsh · 10:00 ET');
    }}

    const guide = g.append('line').attr('class','hover-guide').attr('data-chart-hover-guide','').attr('y1',0).attr('y2',innerHeight).style('display','none');
    const markerLayer = g.append('g').style('display','none');
    const overlay = g.append('rect').attr('class','overlay').attr('data-chart-hit','').attr('data-chart-hover-overlay','cross-series').attr('width',innerWidth).attr('height',innerHeight);

    function endDrag(event, commit) {{
      if (!intradayDrag || event.pointerId !== intradayDrag.pointerId) return;
      const moved = Math.abs(event.clientX-intradayDrag.startX) >= 3;
      const range = intradayDrag.pending;
      if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      intradayDrag = null;
      document.body.classList.remove('plot-dragging');
      if (commit && moved && range) setIntradayRange(range[0],range[1]);
    }}

    overlay.on('pointerdown', event => {{
      if (event.pointerType !== 'mouse' || event.button !== 0) return;
      intradayDrag = {{pointerId:event.pointerId,startX:event.clientX,start:[new Date(intradayStart),new Date(intradayEnd)],pending:null}};
      event.currentTarget.setPointerCapture(event.pointerId);
      document.body.classList.add('plot-dragging');
      guide.style('display','none'); markerLayer.style('display','none'); tooltip.style('display','none');
      event.preventDefault();
    }}).on('pointermove', event => {{
      if (intradayDrag && event.pointerId === intradayDrag.pointerId) {{
        const spanMs = intradayDrag.start[1]-intradayDrag.start[0];
        intradayDrag.pending = shiftedIntradayRange(intradayDrag.start[0],intradayDrag.start[1],-((event.clientX-intradayDrag.startX)/innerWidth)*spanMs);
        return;
      }}
      const [mx] = d3.pointer(event,overlay.node());
      const date = x.invert(Math.max(0,Math.min(innerWidth,mx)));
      const index = d3.bisector(d => d.time).left(view,date);
      const candidates = [view[index-1],view[index]].filter(Boolean);
      const candle = candidates.sort((a,b) => Math.abs(a.time-date)-Math.abs(b.time-date))[0];
      if (!candle || Math.abs(candle.time-date) > 10*60*1000) {{
        guide.style('display','none'); markerLayer.style('display','none'); tooltip.style('display','none');
        return;
      }}
      guide.attr('x1',x(candle.time)).attr('x2',x(candle.time)).style('display',null);
      markerLayer.selectAll('*').remove(); markerLayer.style('display',null);
      markerLayer.append('circle').attr('class','hover-dot').attr('cx',x(candle.time)).attr('cy',y(candle.close)).attr('r',4).attr('fill','var(--fg)');
      const direction = candle.close>candle.open ? 'Up' : candle.close<candle.open ? 'Down' : 'Unchanged';
      tooltip.html(`<strong>${{etTooltip.format(candle.time)}} ET · ${{chart.label}}</strong>`+
        `<div>Open: ${{candle.open.toFixed(3)}}%</div>`+
        `<div>High: ${{candle.high.toFixed(3)}}%</div>`+
        `<div>Low: ${{candle.low.toFixed(3)}}%</div>`+
        `<div>Close: ${{candle.close.toFixed(3)}}%</div>`+
        `<div>Direction: ${{direction}}</div>`).style('display','block');
      const localX = margin.left+x(candle.time), tipWidth=250;
      tooltip.style('left',`${{Math.min(width-tipWidth-8,Math.max(8,localX+12))}}px`).style('top',`${{margin.top+8}}px`);
    }}).on('pointerup',event => endDrag(event,true))
      .on('pointercancel',event => endDrag(event,false))
      .on('pointerleave',() => {{ guide.style('display','none'); markerLayer.style('display','none'); tooltip.style('display','none'); }})
      .on('wheel',event => {{
        event.preventDefault();
        const [mx] = d3.pointer(event,overlay.node());
        zoomIntraday(event.deltaY > 0 ? 1.25 : 0.8,x.invert(Math.max(0,Math.min(innerWidth,mx))));
      }},{{passive:false}});
  }}

  drawIntradayAll = () => intradayCharts.forEach(drawIntradayChart);
  updateIntradayRangeButtons();
  drawIntradayAll();
}}

initializeIntradayCharts();

function drawAll() {{ charts.forEach(drawChart); drawIntradayAll(); }}
let resizeTimer;
new ResizeObserver(() => {{ clearTimeout(resizeTimer); resizeTimer = setTimeout(drawAll, 100); }}).observe(document.querySelector('main'));
drawAll();
</script>
</body>
</html>'''


if __name__ == "__main__":
    build()
