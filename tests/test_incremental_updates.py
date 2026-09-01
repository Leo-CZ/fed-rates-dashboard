from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "code" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def yahoo_payload(timestamp: datetime, close: float, volume: int = 0) -> dict[str, object]:
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "exchangeTimezoneName": "America/New_York",
                        "instrumentType": "INDEX",
                    },
                    "timestamp": [int(timestamp.timestamp())],
                    "indicators": {
                        "quote": [
                            {
                                "open": [close - 0.01],
                                "high": [close + 0.02],
                                "low": [close - 0.02],
                                "close": [close],
                                "volume": [volume],
                            }
                        ]
                    },
                }
            ],
        }
    }


def empty_yahoo_payload() -> dict[str, object]:
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "exchangeTimezoneName": "America/New_York",
                        "instrumentType": "INDEX",
                    },
                    "timestamp": [],
                    "indicators": {
                        "quote": [
                            {"open": [], "high": [], "low": [], "close": [], "volume": []}
                        ]
                    },
                }
            ],
        }
    }


class CmePathTests(unittest.TestCase):
    def test_relative_input_is_repository_relative(self) -> None:
        module = load_module("update_cme_fedwatch_test", "update_cme_fedwatch.py")
        relative = Path("data/cme_fedwatch/cme_fedwatch_snapshot.csv")
        self.assertEqual(module.resolve_input_path(relative), (ROOT / relative).resolve())


class YahooIncrementalTests(unittest.TestCase):
    def test_yield_ohlc_rows_append_without_storing_volume(self) -> None:
        module = load_module("download_yahoo_event_data_test", "download_yahoo_event_data.py")
        with tempfile.TemporaryDirectory() as temporary:
            event_dir = Path(temporary)
            module.EVENT_DIR = event_dir
            csv_path = event_dir / "event_intraday_yahoo.csv"
            first = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)
            new = datetime(2026, 8, 31, 13, 31, tzinfo=timezone.utc)
            existing = {}
            for index, ticker in enumerate(module.TICKERS):
                existing[(ticker, first)] = {
                    "timestamp_utc": "2026-08-28T14:00:00Z",
                    "timestamp_et": "2026-08-28T10:00:00-04:00",
                    "session_date_et": "2026-08-28",
                    "ticker": ticker,
                    "open": f"{4 + index:.6f}",
                    "high": f"{4.02 + index:.6f}",
                    "low": f"{3.98 + index:.6f}",
                    "close": f"{4.01 + index:.6f}",
                }
            module.write_rows(csv_path, existing)
            original_rows = {key: dict(value) for key, value in existing.items()}
            original_bytes = csv_path.read_bytes()
            closes = {ticker: 4.1 + index for index, ticker in enumerate(module.TICKERS)}

            def fake_fetch(ticker, query_start, query_end):
                return yahoo_payload(new, closes[ticker])

            fixed_now = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)
            with patch.object(module, "fetch", side_effect=fake_fetch), patch.object(
                module, "utc_now", return_value=fixed_now
            ):
                module.build()

            merged, migrated, _ = module.load_existing_rows(csv_path)
            self.assertFalse(migrated)
            for key, original in original_rows.items():
                self.assertEqual(merged[key], original)
            self.assertTrue(csv_path.read_bytes().startswith(original_bytes))
            self.assertNotIn("volume", merged[("^TNX", new)])
            self.assertEqual(merged[("^TNX", new)]["open"], f"{closes['^TNX'] - 0.01:.6f}")
            metadata = json.loads(
                (event_dir / "event_intraday_yahoo_metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["storage_action"], "appended new ticker-minute rows")
            self.assertEqual(metadata["coverage"]["^TNX"]["volume_nonzero_returned"], 0)
            self.assertEqual(
                metadata["coverage"]["^TNX"]["volume_storage"],
                "not stored because Yahoo returned only zeros",
            )
            self.assertFalse(any((event_dir / "raw").glob("*.json")))
            self.assertEqual(
                len((event_dir / "event_intraday_yahoo_updates.jsonl").read_text().splitlines()),
                1,
            )

            before_no_op = csv_path.read_bytes()
            with patch.object(module, "fetch", side_effect=lambda *args: empty_yahoo_payload()), patch.object(
                module,
                "utc_now",
                return_value=datetime(2026, 8, 31, 18, 10, tzinfo=timezone.utc),
            ):
                module.build()
            self.assertEqual(csv_path.read_bytes(), before_no_op)

    def test_previous_long_schema_drops_volume(self) -> None:
        module = load_module("download_yahoo_event_migration_test", "download_yahoo_event_data.py")
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "event_intraday_yahoo.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=module.PREVIOUS_LONG_FIELDNAMES)
                writer.writeheader()
                writer.writerow(
                    {
                        "timestamp_utc": "2026-08-28T14:00:00Z",
                        "timestamp_et": "2026-08-28T10:00:00-04:00",
                        "session_date_et": "2026-08-28",
                        "ticker": "^TNX",
                        "open": "4.000000",
                        "high": "4.020000",
                        "low": "3.980000",
                        "close": "4.010000",
                        "volume": "0",
                    }
                )

            migrated_rows, migrated, stats = module.load_existing_rows(csv_path)
            self.assertTrue(migrated)
            self.assertEqual(len(migrated_rows), 1)
            key = ("^TNX", datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc))
            self.assertNotIn("volume", migrated_rows[key])
            self.assertEqual(stats, {})

    def test_nonzero_yield_volume_requires_review(self) -> None:
        module = load_module("download_yahoo_event_volume_test", "download_yahoo_event_data.py")
        timestamp = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)
        start = datetime(2026, 8, 28, 0, 0, tzinfo=module.ET)
        end = datetime(2026, 8, 28, 23, 59, tzinfo=module.ET)
        with self.assertRaisesRegex(ValueError, "non-zero volume"):
            module.extract_payload_rows(yahoo_payload(timestamp, 4.0, volume=1), "^TNX", start, end)

    def test_retained_raw_snapshot_drops_zero_volume_only(self) -> None:
        module = load_module("download_yahoo_event_raw_test", "download_yahoo_event_data.py")
        timestamp = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            raw_dir = Path(temporary)
            for filename in ("index_TNX.json", "index_TYX.json"):
                (raw_dir / filename).write_text(
                    json.dumps(yahoo_payload(timestamp, 4.0)), encoding="utf-8"
                )
            self.assertEqual(module.remove_zero_volume_from_retained_raw_snapshots(raw_dir), 2)
            for filename in ("index_TNX.json", "index_TYX.json"):
                payload = json.loads((raw_dir / filename).read_text(encoding="utf-8"))
                quote = payload["chart"]["result"][0]["indicators"]["quote"][0]
                self.assertNotIn("volume", quote)


if __name__ == "__main__":
    unittest.main()
