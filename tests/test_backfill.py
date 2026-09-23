import unittest
from datetime import date
import json
from pathlib import Path

from scripts.backfill_10min_history import archive_day, snapshot_days


class BackfillTest(unittest.TestCase):
    def test_snapshot_days_overlap_seven_day_rolling_window(self):
        self.assertEqual(
            snapshot_days(date(2026, 7, 14), date(2026, 7, 24)),
            [date(2026, 7, 17), date(2026, 7, 21), date(2026, 7, 25)],
        )

    def test_archive_day_uses_jst_slots_and_preserves_invalid_flags(self):
        records = {"example": {
            "2026-07-14T00:00": {"timestamp": "2026-07-14T00:00", "value": 1.2, "flag": ""},
            "2026-07-14T00:10": {"timestamp": "2026-07-14T00:10", "value": 9.9, "flag": "$"},
            "2026-07-15T00:00": {"timestamp": "2026-07-15T00:00", "value": 2.1, "flag": ""},
        }}
        archived = archive_day(date(2026, 7, 14), ["example"], records)
        station = archived["stations"]["example"]
        self.assertEqual(len(station["values"]), 144)
        self.assertEqual(station["values"][:3], [1.2, None, None])
        self.assertEqual(station["flags"], {"1": "$"})

    def test_recovered_days_have_complete_grid_and_match_available_hourly_values(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads((root / "reports/backfill-10min-2026.json").read_text())
        station_ids = list(next(iter(report["days"].values()))["stationValidCounts"])
        hourly = {
            station: json.loads((root / "data/hourly" / station / "2026.json").read_text())["values"]
            for station in station_ids
        }
        compared = 0
        for day, info in report["days"].items():
            payload = json.loads((root / "data/10min" / day[:4] / day[5:7] / f"{day}.json").read_text())
            self.assertEqual(payload["date"], day)
            base_hour = (date.fromisoformat(day) - date(2026, 1, 1)).days * 24
            for station in station_ids:
                values = payload["stations"][station]["values"]
                self.assertEqual(len(values), 144)
                self.assertEqual(sum(value is not None for value in values), info["stationValidCounts"][station])
                for hour in range(24):
                    value = values[hour * 6]
                    earlier_hourly = hourly[station][base_hour + hour - 1]
                    if value is not None and earlier_hourly is not None:
                        self.assertEqual(value, earlier_hourly)
                        compared += 1
        self.assertGreater(compared, 8000)


if __name__ == "__main__":
    unittest.main()
