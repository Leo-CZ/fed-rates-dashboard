from __future__ import annotations

import csv
import io
import multiprocessing
import shutil
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent
RAW_DIRECTORY = ROOT / "data" / "fred" / "raw"
START_DATE = date(2019, 1, 1)
SERIES = ("DGS2", "DGS10", "DGS30", "T10YIE", "DFII10", "DFII30")
USER_AGENT = "rate-analysis-data-refresh/2.0"
CONNECT_TIMEOUT_SECONDS = 15
TOTAL_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3
MISSING_VALUE_MARKERS = {"", "."}

Row = tuple[str, str]
Fetcher = Callable[[str], bytes]


def fred_url(series_id: str, start_date: date) -> str:
    query = urlencode({"id": series_id, "cosd": start_date.isoformat()})
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?{query}"


def _download_worker(url: str, sender: object) -> None:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
            sender.send((True, response.read()))
    except Exception as exc:
        sender.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        sender.close()


def receive_download_result(receiver: object, process: object, timeout: float) -> bytes:
    if not receiver.poll(timeout):
        process.terminate()
        process.join(5)
        raise TimeoutError(f"Download exceeded the {timeout:g}-second total deadline.")
    succeeded, result = receiver.recv()
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
    if not succeeded:
        raise RuntimeError(str(result))
    return result


def download(url: str) -> bytes:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_download_worker, args=(url, sender), daemon=True)
    process.start()
    sender.close()
    try:
        return receive_download_result(receiver, process, TOTAL_TIMEOUT_SECONDS)
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(5)


def validate_rate_value(value: str, series_id: str, source: str, row_number: int) -> None:
    if value in MISSING_VALUE_MARKERS:
        return
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid rate value in {source} at row {row_number}, {series_id}: {value}"
        ) from exc
    if not number.is_finite():
        raise ValueError(
            f"Non-finite rate value in {source} at row {row_number}, {series_id}: {value}"
        )


def validate_rows(rows: list[Row], series_id: str, source: str) -> None:
    parsed_dates: list[date] = []
    for row_number, (date_text, value) in enumerate(rows, start=2):
        try:
            parsed = date.fromisoformat(date_text)
        except ValueError as exc:
            raise ValueError(f"Invalid date in {source} at row {row_number}: {date_text}") from exc
        if parsed.isoformat() != date_text:
            raise ValueError(f"Noncanonical date in {source} at row {row_number}: {date_text}")
        validate_rate_value(value, series_id, source, row_number)
        parsed_dates.append(parsed)

    if len(set(parsed_dates)) != len(parsed_dates):
        raise ValueError(f"Duplicate dates in {source} for {series_id}.")
    if parsed_dates != sorted(parsed_dates):
        raise ValueError(f"Dates are not ordered in {source} for {series_id}.")


def parse_csv_text(text: str, series_id: str, source: str) -> list[Row]:
    records = list(csv.reader(io.StringIO(text)))
    expected_header = ["observation_date", series_id]
    if not records or records[0] != expected_header:
        actual = records[0] if records else []
        raise ValueError(f"Unexpected CSV header in {source}: {actual}; expected {expected_header}.")

    rows: list[Row] = []
    for row_number, record in enumerate(records[1:], start=2):
        if len(record) != 2:
            raise ValueError(f"Unexpected column count in {source} at row {row_number}.")
        rows.append((record[0], record[1]))
    validate_rows(rows, series_id, source)
    return rows


def read_stored_rows(path: Path, series_id: str) -> list[Row]:
    return parse_csv_text(path.read_text(encoding="utf-8-sig"), series_id, str(path))


def fetch_with_retries(url: str, series_id: str, fetcher: Fetcher) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fetcher(url)
        except Exception as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to download {series_id} after {MAX_ATTEMPTS} attempts.") from last_error


def append_atomically(
    destination: Path,
    series_id: str,
    existing_rows: list[Row],
    new_rows: list[Row],
) -> None:
    temporary = destination.with_name(f".{series_id}.merge")
    try:
        if destination.exists():
            shutil.copyfile(destination, temporary)
            if temporary.stat().st_size:
                with temporary.open("rb") as handle:
                    handle.seek(-1, 2)
                    final_byte = handle.read(1)
                if final_byte not in {b"\n", b"\r"}:
                    with temporary.open("ab") as handle:
                        handle.write(b"\n")
            with temporary.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle, lineterminator="\n").writerows(new_rows)
        else:
            with temporary.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(["observation_date", series_id])
                writer.writerows(new_rows)

        merged_rows = read_stored_rows(temporary, series_id)
        if len(merged_rows) != len(existing_rows) + len(new_rows):
            raise ValueError(
                f"Merged row count for {series_id} is {len(merged_rows)}; "
                f"expected {len(existing_rows) + len(new_rows)}."
            )
        if merged_rows[: len(existing_rows)] != existing_rows:
            raise ValueError(f"Existing observations changed while merging {series_id}.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_series(
    series_id: str,
    raw_directory: Path = RAW_DIRECTORY,
    fetcher: Fetcher = download,
) -> int:
    raw_directory.mkdir(parents=True, exist_ok=True)
    destination = raw_directory / f"{series_id}.csv"
    existing_rows = read_stored_rows(destination, series_id) if destination.exists() else []
    last_stored = date.fromisoformat(existing_rows[-1][0]) if existing_rows else None
    download_start = last_stored + timedelta(days=1) if last_stored else START_DATE

    payload = fetch_with_retries(fred_url(series_id, download_start), series_id, fetcher)
    downloaded_rows = parse_csv_text(
        payload.decode("utf-8-sig"), series_id, f"FRED response for {series_id}"
    )
    new_rows = [
        row
        for row in downloaded_rows
        if last_stored is None or date.fromisoformat(row[0]) > last_stored
    ]

    if not new_rows:
        through = last_stored.isoformat() if last_stored else "no stored observations"
        print(f"{series_id} is already current through {through}")
        return 0

    append_atomically(destination, series_id, existing_rows, new_rows)
    print(f"Appended {len(new_rows)} new {series_id} rows beginning {new_rows[0][0]}")
    return len(new_rows)


def main() -> None:
    for series_id in SERIES:
        refresh_series(series_id)
    print(
        "Incremental FRED update complete. Existing observations were not replaced "
        "and missing values were not imputed."
    )


if __name__ == "__main__":
    main()
