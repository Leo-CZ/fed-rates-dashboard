from __future__ import annotations

import argparse
import csv
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent
CME_DIR = ROOT / "data" / "cme_fedwatch"
SOURCE_URL = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
TOOLS_URL = (
    "https://cmegroup-tools.quikstrike.net/User/QuikStrikeTools.aspx"
    "?viewitemid=IntegratedFedWatchTool&userId=lwolf&jobRole=&company=&companyType="
)
VIEW_URL = (
    "https://cmegroup-tools.quikstrike.net/User/QuikStrikeView.aspx"
    "?viewitemid=IntegratedFedWatchTool&userId=lwolf&jobRole=&company=&companyType=&"
)
PROBABILITY_POSTBACK = "ctl00$MainContent$ucViewControl_IntegratedFedWatchTool$lbPTree"
USER_AGENT = "Mozilla/5.0 (compatible; FedRatesDashboard/1.0)"
TARGET_PATTERN = re.compile(r"^\d+-\d+$")
SNAPSHOT_PATTERN = re.compile(r"^cme_fedwatch_snapshot_(\d{8})_(\d{6})_ET\.csv$")
CENTRAL = ZoneInfo("America/Chicago")
EASTERN = ZoneInfo("America/New_York")
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 30


class ParsedDocument(HTMLParser):
    """Collect form fields, visible text, and non-nested table rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.form_action = ""
        self.hidden_fields: dict[str, str] = {}
        self.text_parts: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._table_stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "form" and not self.form_action:
            self.form_action = attributes.get("action", "")
        if (
            tag == "input"
            and attributes.get("type", "").lower() == "hidden"
            and attributes.get("name")
        ):
            self.hidden_fields[attributes["name"]] = attributes.get("value", "")

        if tag == "table":
            self._table_stack.append({"rows": [], "row": None, "cell": None})
        elif self._table_stack and tag == "tr":
            self._table_stack[-1]["row"] = []
        elif self._table_stack and tag in {"th", "td"}:
            if self._table_stack[-1]["row"] is not None:
                self._table_stack[-1]["cell"] = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._table_stack and self._table_stack[-1]["cell"] is not None:
            cell = self._table_stack[-1]["cell"]
            assert isinstance(cell, list)
            cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._table_stack:
            return
        table = self._table_stack[-1]
        if tag in {"th", "td"} and table["cell"] is not None:
            cell = table["cell"]
            row = table["row"]
            assert isinstance(cell, list)
            assert isinstance(row, list)
            row.append(collapse_space(" ".join(cell)))
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            row = table["row"]
            rows = table["rows"]
            assert isinstance(row, list)
            assert isinstance(rows, list)
            if row:
                rows.append(row)
            table["row"] = None
        elif tag == "table":
            completed = self._table_stack.pop()
            rows = completed["rows"]
            assert isinstance(rows, list)
            if rows:
                self.tables.append(rows)


def collapse_space(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def parse_document(payload: str) -> ParsedDocument:
    parser = ParsedDocument()
    parser.feed(payload)
    return parser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download, validate, and archive the current CME FedWatch probability snapshot."
    )
    parser.add_argument(
        "--overwrite",
        choices=("ask", "yes", "no", "cancel"),
        default="ask",
        help="What to do when a snapshot already exists for the CME data date (default: ask).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Optional manual CSV fallback. Relative paths are resolved from the repository root.",
    )
    parser.add_argument(
        "--current-target",
        help="Required with --input; federal-funds target range in basis points, such as 350-375.",
    )
    parser.add_argument(
        "--snapshot-time",
        help="Required with --input; ISO 8601 CME snapshot time with its UTC offset.",
    )
    return parser.parse_args()


def parse_snapshot_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--snapshot-time must include its UTC offset.")
    return parsed.astimezone(EASTERN)


def resolve_input_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (ROOT / path).resolve()


def normalize_and_validate(
    headers: list[str], source_rows: list[dict[str, str]], current_target: str
) -> tuple[list[str], list[dict[str, str]]]:
    if not headers or headers[0].strip().lower() not in {"meeting date", "meeting_date"}:
        raise ValueError("The first column must be 'Meeting Date'.")
    targets = [header.strip() for header in headers[1:]]
    if not targets or any(not TARGET_PATTERN.fullmatch(target) for target in targets):
        raise ValueError("Every probability column must be a basis-point range such as 350-375.")
    if current_target not in targets:
        raise ValueError(f"Current target {current_target} is not present in the probability columns.")
    if not source_rows:
        raise ValueError("The CME probability table has no meeting rows.")

    normalized: list[dict[str, str]] = []
    seen_dates: set[str] = set()
    for row_number, source_row in enumerate(source_rows, start=1):
        meeting_text = str(source_row.get(headers[0], "")).strip()
        parsed_meeting: datetime | None = None
        for date_format in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                parsed_meeting = datetime.strptime(meeting_text, date_format)
                break
            except ValueError:
                pass
        if parsed_meeting is None:
            raise ValueError(f"Meeting row {row_number} has an invalid date: {meeting_text}")
        meeting_date = f"{parsed_meeting.month}/{parsed_meeting.day}/{parsed_meeting.year}"
        if meeting_date in seen_dates:
            raise ValueError(f"Duplicate meeting date: {meeting_date}")
        seen_dates.add(meeting_date)

        normalized_row = {"Meeting Date": meeting_date}
        probability_sum = 0.0
        populated = 0
        for source_header, target in zip(headers[1:], targets):
            raw_value = str(source_row.get(source_header, "")).strip().removesuffix("%").strip()
            if raw_value in {"", "—", "-"}:
                normalized_row[target] = ""
                continue
            try:
                probability = float(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"Meeting row {row_number}, {target} contains a non-numeric probability."
                ) from exc
            if not 0 <= probability <= 100:
                raise ValueError(f"Meeting row {row_number}, {target} is outside 0-100%.")
            normalized_row[target] = f"{probability:.1f}"
            probability_sum += probability
            populated += 1

        if not populated:
            raise ValueError(f"Meeting row {row_number} contains no probabilities.")
        if not 99.5 <= probability_sum <= 100.5:
            raise ValueError(
                f"Meeting row {row_number} probabilities sum to {probability_sum:.3f}%; "
                "expected approximately 100%."
            )
        normalized.append(normalized_row)

    normalized.sort(key=lambda row: datetime.strptime(row["Meeting Date"], "%m/%d/%Y"))
    return ["Meeting Date", *targets], normalized


def read_and_validate(path: Path, current_target: str) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("The input CSV has no header row.")
        headers = list(reader.fieldnames)
        rows = list(reader)
    return normalize_and_validate(headers, rows, current_target)


def extract_snapshot_details(payload: str) -> tuple[datetime, str]:
    document = parse_document(payload)
    page_text = collapse_space(" ".join(document.text_parts))
    timestamp_match = re.search(
        r"Data as of\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2})\s+CT\b",
        page_text,
        flags=re.IGNORECASE,
    )
    if not timestamp_match:
        raise ValueError("CME's 'Data as of' timestamp was not found.")
    snapshot = (
        datetime.strptime(timestamp_match.group(1), "%d %b %Y %H:%M:%S")
        .replace(tzinfo=CENTRAL)
        .astimezone(EASTERN)
    )

    target_match = re.search(r"Current target rate is\s+(\d+-\d+)", page_text, re.IGNORECASE)
    if not target_match:
        for table in document.tables:
            for row in table:
                if row and "(Current)" in row[0]:
                    target_match = re.search(r"(\d+-\d+)", row[0])
                    break
            if target_match:
                break
    if not target_match:
        raise ValueError("CME's current target-rate range was not found.")
    return snapshot, target_match.group(1)


def extract_probability_table(
    payload: str, current_target: str
) -> tuple[list[str], list[dict[str, str]]]:
    document = parse_document(payload)
    selected: list[list[str]] | None = None
    for table in document.tables:
        flattened = " ".join(cell for row in table for cell in row)
        if "Conditional Meeting Probabilities" in flattened:
            selected = table
            break
    if selected is None:
        raise ValueError("CME's conditional meeting probability table was not found.")

    header_index = next(
        (index for index, row in enumerate(selected) if row and row[0].lower() == "meeting date"),
        None,
    )
    if header_index is None:
        raise ValueError("CME's conditional probability table has no Meeting Date header.")
    headers = selected[header_index]
    source_rows: list[dict[str, str]] = []
    for row in selected[header_index + 1 :]:
        if not row or not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", row[0]):
            continue
        padded = [*row, *([""] * (len(headers) - len(row)))]
        source_rows.append(dict(zip(headers, padded)))
    return normalize_and_validate(headers, source_rows, current_target)


def open_text(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    referer: str,
    data: bytes | None = None,
) -> tuple[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Referer": referer,
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Origin"] = "https://cmegroup-tools.quikstrike.net"
    request = urllib.request.Request(url, data=data, headers=headers)
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", "replace"), response.geturl()


def acquire_automatic_once() -> tuple[datetime, str, list[str], list[dict[str, str]]]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    bootstrap, bootstrap_url = open_text(opener, TOOLS_URL, referer=SOURCE_URL)
    cache_match = re.search(r'id="global_instanceCache"[^>]*value="([^"]+)"', bootstrap)
    if not cache_match:
        raise ValueError("CME's browser-session identifiers were not found.")
    view_url = VIEW_URL + html.unescape(cache_match.group(1))

    current_page, current_url = open_text(opener, view_url, referer=bootstrap_url)
    snapshot, current_target = extract_snapshot_details(current_page)
    document = parse_document(current_page)
    if not document.form_action or "__VIEWSTATE" not in document.hidden_fields:
        raise ValueError("CME's FedWatch form state was not found.")
    fields = dict(document.hidden_fields)
    fields["__EVENTTARGET"] = PROBABILITY_POSTBACK
    fields["__EVENTARGUMENT"] = ""
    probability_url = urllib.parse.urljoin(current_url, html.unescape(document.form_action))
    probability_page, _ = open_text(
        opener,
        probability_url,
        referer=current_url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
    )
    headers, rows = extract_probability_table(probability_page, current_target)
    return snapshot, current_target, headers, rows


def acquire_automatic(
    attempt: Callable[[], tuple[datetime, str, list[str], list[dict[str, str]]]] = acquire_automatic_once,
) -> tuple[datetime, str, list[str], list[dict[str, str]]]:
    errors: list[str] = []
    for attempt_number in range(1, MAX_ATTEMPTS + 1):
        try:
            return attempt()
        except Exception as exc:
            errors.append(f"attempt {attempt_number}: {exc}")
            if attempt_number < MAX_ATTEMPTS:
                time.sleep(2**attempt_number)
    raise RuntimeError("CME FedWatch download failed; " + "; ".join(errors))


def snapshot_stem(snapshot: datetime) -> str:
    return f"cme_fedwatch_snapshot_{snapshot.astimezone(EASTERN):%Y%m%d_%H%M%S}_ET"


def daily_snapshots(snapshot: datetime, directory: Path = CME_DIR) -> list[Path]:
    date_token = snapshot.astimezone(EASTERN).strftime("%Y%m%d")
    matches: list[Path] = []
    if directory.exists():
        for path in directory.iterdir():
            match = SNAPSHOT_PATTERN.fullmatch(path.name)
            if path.is_file() and match and match.group(1) == date_token:
                matches.append(path)
    return sorted(matches)


def overwrite_decision(
    existing: list[Path],
    mode: str,
    input_function: Callable[[str], str] = input,
) -> str:
    if not existing:
        return "yes"
    print("A CME FedWatch snapshot already exists for this CME data date:")
    for path in existing:
        print(f"  {path.relative_to(ROOT)}")
    if mode != "ask":
        return mode
    while True:
        try:
            answer = input_function("Overwrite today's snapshot? [yes/no/cancel]: ").strip().lower()
        except EOFError as exc:
            raise RuntimeError(
                "A daily snapshot already exists. Re-run interactively or pass "
                "--overwrite yes, --overwrite no, or --overwrite cancel."
            ) from exc
        aliases = {
            "y": "yes",
            "yes": "yes",
            "n": "no",
            "no": "no",
            "c": "cancel",
            "cancel": "cancel",
        }
        if answer in aliases:
            return aliases[answer]
        print("Please enter yes, no, or cancel.")


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def install_snapshot(
    snapshot: datetime,
    current_target: str,
    headers: list[str],
    rows: list[dict[str, str]],
    *,
    directory: Path = CME_DIR,
    update_method: str = "Automated browser-session extraction from the public CME FedWatch table",
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = snapshot_stem(snapshot)
    csv_path = directory / f"{stem}.csv"
    metadata_path = directory / f"{stem}.json"
    temporary_csv = directory / f".{stem}.csv.tmp"
    temporary_metadata = directory / f".{stem}.json.tmp"

    metadata = {
        "source": SOURCE_URL,
        "snapshot_date": snapshot.astimezone(EASTERN).strftime("%Y-%m-%d"),
        "snapshot_time": snapshot.astimezone(EASTERN).strftime("%H:%M:%S"),
        "timezone": "ET",
        "iana_timezone": "America/New_York",
        "current_target_bps": current_target,
        "units": "Probability percent",
        "meetings": len(rows),
        "update_method": update_method,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        write_csv(temporary_csv, headers, rows)
        temporary_metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        temporary_csv.replace(csv_path)
        temporary_metadata.replace(metadata_path)
    finally:
        temporary_csv.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)

    for old_csv in daily_snapshots(snapshot, directory):
        if old_csv != csv_path:
            old_csv.unlink()
            old_csv.with_suffix(".json").unlink(missing_ok=True)
    return csv_path, metadata_path


def manual_snapshot(args: argparse.Namespace) -> tuple[datetime, str, list[str], list[dict[str, str]]]:
    if not args.input:
        raise ValueError("Manual snapshot requested without --input.")
    if not args.current_target or not args.snapshot_time:
        raise ValueError("--input requires both --current-target and --snapshot-time.")
    if not TARGET_PATTERN.fullmatch(args.current_target):
        raise ValueError("--current-target must look like 350-375.")
    input_path = resolve_input_path(args.input)
    if not input_path.is_file():
        raise FileNotFoundError(f"CME input CSV not found: {input_path}")
    headers, rows = read_and_validate(input_path, args.current_target)
    return parse_snapshot_time(args.snapshot_time), args.current_target, headers, rows


def main() -> int:
    args = parse_args()
    if args.input:
        snapshot, current_target, headers, rows = manual_snapshot(args)
        update_method = "Validated manual CSV import from the public CME FedWatch table"
    else:
        if args.current_target or args.snapshot_time:
            raise ValueError("--current-target and --snapshot-time may only be used with --input.")
        print("Downloading the current CME FedWatch probability table...")
        snapshot, current_target, headers, rows = acquire_automatic()
        update_method = "Automated browser-session extraction from the public CME FedWatch table"

    existing = daily_snapshots(snapshot)
    decision = overwrite_decision(existing, args.overwrite)
    if decision == "no":
        print("Keeping the existing daily snapshot; no files were changed.")
        return 0
    if decision == "cancel":
        print("CME FedWatch update cancelled; no files were changed.")
        return 2

    csv_path, metadata_path = install_snapshot(
        snapshot,
        current_target,
        headers,
        rows,
        update_method=update_method,
    )
    print(f"Saved {csv_path.relative_to(ROOT)}")
    print(f"Saved {metadata_path.relative_to(ROOT)}")
    print(
        f"CME snapshot: {snapshot.astimezone(EASTERN):%Y-%m-%d %H:%M:%S} ET; "
        f"validated {len(rows)} meeting rows."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
