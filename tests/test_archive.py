import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.archive_water_levels import archive_hourly, archive_recent, update_manifest


class ArchiveRecentTest(unittest.TestCase):
    def test_archives_completed_day_on_regular_grid_and_never_rewrites_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            output = root / "data"
            stations = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
            for station in stations:
                folder = source / station["id"]
                folder.mkdir(parents=True)
                payload = {"records": [
                    {"timestamp": "2026-09-19T00:00", "value": 1.0, "flag": ""},
                    {"timestamp": "2026-09-19T00:10", "value": 9.9, "flag": "$"},
                    {"timestamp": "2026-09-20T00:00", "value": 2.0, "flag": ""},
                ]}
                (folder / "recent_10min.json").write_text(json.dumps(payload), encoding="utf-8")

            config = {"stations": stations}
            self.assertEqual(archive_recent(config, str(source), output, 2, date(2026, 9, 22)), 2)
            archived = json.loads((output / "10min/2026/09/2026-09-19.json").read_text())
            self.assertEqual(len(archived["stations"]["a"]["values"]), 144)
            self.assertEqual(archived["stations"]["a"]["values"][:2], [1.0, None])
            self.assertEqual(archive_recent(config, str(source), output, 2, date(2026, 9, 22)), 0)


class ArchiveHourlyTest(unittest.TestCase):
    def test_archives_jst_year_grid_and_manifest_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            output = root / "data"
            stations = [{"id": "station-a", "name": "A"}, {"id": "station-b", "name": "B"}]
            station_a = source / "station-a"
            station_a.mkdir(parents=True)
            history = {"records": [
                {"timestamp": "2024-01-01T00:00", "value": 1.0, "flag": ""},
                {"timestamp": "2024-01-01T01:00", "value": 9.9, "flag": "$"},
            ]}
            recent = {"records": [
                {"timestamp": "2024-01-01T02:00", "value": 2.0, "flag": ""},
            ]}
            (station_a / "historical_hourly.json").write_text(json.dumps(history), encoding="utf-8")
            (station_a / "recent_hourly.json").write_text(json.dumps(recent), encoding="utf-8")
            station_b = source / "station-b"
            station_b.mkdir(parents=True)
            empty = json.dumps({"records": []})
            (station_b / "historical_hourly.json").write_text(empty, encoding="utf-8")
            (station_b / "recent_hourly.json").write_text(empty, encoding="utf-8")

            config = {"stations": stations}
            self.assertEqual(archive_hourly(config, str(source), output), 1)
            update_manifest(config, output)

            yearly = json.loads((output / "hourly/station-a/2024.json").read_text(encoding="utf-8"))
            self.assertEqual(len(yearly["values"]), 8784)
            self.assertEqual(yearly["values"][:4], [1.0, None, 2.0, None])
            self.assertEqual(yearly["flags"]["1"], "$")
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stations"], stations)
            self.assertEqual(manifest["hourlyYears"], {"station-a": [2024]})


if __name__ == "__main__":
    unittest.main()
