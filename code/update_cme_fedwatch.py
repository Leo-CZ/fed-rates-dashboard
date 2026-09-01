from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent
CME_DIR = ROOT / "data" / "cme_fedwatch"
SOURCE_URL = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
TARGET_PATTERN = re.compile(r"^\d+-\d+$")
CENTRAL_OFFSETS = {timedelta(hours=-5), timedelta(hours=-6)}
ACQUISITION_METHODS = {
    "manual_csv": "Validated manual CSV import from the public CME FedWatch table",
    "browser_assisted": "Validated browser-assisted extraction from the public CME FedWatch table",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and install a manually exported CME FedWatch probability snapshot."
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="CSV table copied or exported from CME FedWatch. Relative paths are resolved from the repository root.",
    )
    parser.add_argument(
        "--current-target",
        required=True,
        help="Current federal-funds target range in basis points, for example 350-375.",
    )
    parser.add_argument(
        "--snapshot-time",
        required=True,
        help="CME snapshot time as ISO 8601 with its Central-Time UTC offset.",
    )
    parser.add_argument(
        "--acquisition-method",
        choices=sorted(ACQUISITION_METHODS),
        default="manual_csv",
        help="How the source table was acquired before validation.",
    )
    return parser.parse_args()


def parse_snapshot_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--snapshot-time must include its UTC offset, such as -05:00 or -06:00.")
    if parsed.utcoffset() not in CENTRAL_OFFSETS:
        raise ValueError("--snapshot-time must use the applicable Central-Time offset (-05:00 or -06:00).")
    return parsed


def read_and_validate(path: Path, current_target: str) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("The input CSV has no header row.")
        headers = [header.strip() for header in reader.fieldnames]
        rows = list(reader)

    if headers[0].lower() not in {"meeting date", "meeting_date"}:
        raise ValueError("The first CSV column must be 'Meeting Date'.")
    targets = headers[1:]
    if not targets or any(not TARGET_PATTERN.fullmatch(target) for target in targets):
        raise ValueError("Every probability column must be a basis-point range such as 350-375.")
    if current_target not in targets:
        raise ValueError(f"Current target {current_target} is not present in the CSV columns.")
    if not rows:
        raise ValueError("The input CSV has no meeting rows.")

    normalized: list[dict[str, str]] = []
    seen_dates: set[str] = set()
    for row_number, source_row in enumerate(rows, start=2):
        meeting_text = str(source_row.get(reader.fieldnames[0], "")).strip()
        try:
            meeting = datetime.strptime(meeting_text, "%m/%d/%Y")
        except ValueError as exc:
            raise ValueError(f"Row {row_number} has an invalid meeting date: {meeting_text}") from exc
        meeting_date = f"{meeting.month}/{meeting.day}/{meeting.year}"
        if meeting_date in seen_dates:
            raise ValueError(f"Duplicate meeting date: {meeting_date}")
        seen_dates.add(meeting_date)

        normalized_row = {"Meeting Date": meeting_date}
        probability_sum = 0.0
        populated = 0
        for original_header, target in zip(reader.fieldnames[1:], targets):
            raw_value = str(source_row.get(original_header, "")).strip().removesuffix("%").strip()
            if raw_value in {"", "—", "-"}:
                normalized_row[target] = ""
                continue
            try:
                probability = float(raw_value)
            except ValueError as exc:
                raise ValueError(f"Row {row_number}, {target} contains a non-numeric probability.") from exc
            if not 0 <= probability <= 100:
                raise ValueError(f"Row {row_number}, {target} is outside 0-100%.")
            normalized_row[target] = f"{probability:.1f}"
            probability_sum += probability
            populated += 1

        if not populated:
            raise ValueError(f"Row {row_number} contains no probabilities.")
        if not 99.5 <= probability_sum <= 100.5:
            raise ValueError(
                f"Row {row_number} probabilities sum to {probability_sum:.3f}%; expected approximately 100%."
            )
        normalized.append(normalized_row)

    normalized.sort(key=lambda row: datetime.strptime(row["Meeting Date"], "%m/%d/%Y"))
    return ["Meeting Date", *targets], normalized


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def resolve_input_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (ROOT / path).resolve()


def main() -> None:
    args = parse_args()
    if not TARGET_PATTERN.fullmatch(args.current_target):
        raise ValueError("--current-target must look like 350-375.")
    snapshot = parse_snapshot_time(args.snapshot_time)
    input_path = resolve_input_path(args.input)
    if not input_path.is_file():
        raise FileNotFoundError(f"CME input CSV not found: {input_path}")
    headers, rows = read_and_validate(input_path, args.current_target)
    CME_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = snapshot.strftime("%Y%m%d_%H%M%S")
    archive_path = CME_DIR / f"cme_fedwatch_snapshot_{timestamp}_CT.csv"
    current_path = CME_DIR / "cme_fedwatch_snapshot.csv"
    metadata_path = CME_DIR / "cme_fedwatch_snapshot.json"
    write_csv(archive_path, headers, rows)
    write_csv(current_path, headers, rows)

    metadata = {
        "source": SOURCE_URL,
        "snapshot_date": snapshot.strftime("%Y-%m-%d"),
        "snapshot_time": snapshot.strftime("%H:%M:%S"),
        "timezone": "CT",
        "current_target_bps": args.current_target,
        "units": "Probability percent",
        "meetings": len(rows),
        "update_method": ACQUISITION_METHODS[args.acquisition_method],
    }
    temporary_metadata = metadata_path.with_suffix(".json.tmp")
    temporary_metadata.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary_metadata.replace(metadata_path)

    print(f"Updated {current_path.relative_to(ROOT)}")
    print(f"Archived {archive_path.relative_to(ROOT)}")
    print(f"Validated {len(rows)} meeting rows; every row sums to approximately 100%.")


if __name__ == "__main__":
    main()
