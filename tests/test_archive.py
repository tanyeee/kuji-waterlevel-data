import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts.archive_water_levels import archive_hourly, archive_recent, main, update_manifest, year_minutes


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
    def test_cli_uses_separate_hourly_station_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source_station = source / "archive-only"
            source_station.mkdir(parents=True)
            (source_station / "recent_hourly.json").write_text(json.dumps({"records": [
                {"timestamp": "2026-09-22T12:00", "value": 1.2, "flag": ""},
            ]}), encoding="utf-8")

            config = root / "config.json"
            config.write_text(json.dumps({"stations": [{"id": "display-station", "name": "表示地点"}]}), encoding="utf-8")
            hourly_config = root / "hourly-config.json"
            hourly_config.write_text(json.dumps({"stations": [{"id": "archive-only", "name": "履歴地点"}]}), encoding="utf-8")
            output = root / "data"

            with patch.object(sys, "argv", [
                "archive_water_levels.py", "--config", str(config),
                "--hourly-config", str(hourly_config), "--source-base", str(source),
                "--output", str(output), "--skip-recent",
            ]):
                main()

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stations"], [{"id": "display-station", "name": "表示地点"}])
            self.assertEqual(manifest["hourlyYears"], {"archive-only": [2026]})

    def test_merges_recent_data_into_existing_years_without_legacy_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            output = root / "data"
            stations = [{"id": "station-a", "name": "A"}, {"id": "station-b", "name": "B"}]
            station_a = source / "station-a"
            station_a.mkdir(parents=True)
            recent = {"records": [
                {"timestamp": "2024-01-01T00:00", "value": 1.5, "flag": ""},
                {"timestamp": "2024-01-01T02:00", "value": 2.0, "flag": ""},
                {"timestamp": "2025-01-01T00:00", "value": 3.0, "flag": ""},
            ]}
            (station_a / "recent_hourly.json").write_text(json.dumps(recent), encoding="utf-8")
            station_b = source / "station-b"
            station_b.mkdir(parents=True)
            (station_b / "recent_hourly.json").write_text(json.dumps({"records": [
                {"timestamp": "2026-01-01T00:00", "value": 4.0, "flag": ""},
            ]}), encoding="utf-8")

            old_values = [None] * year_minutes(2016, 60)
            old_values[0] = 0.7
            old_path = output / "hourly/station-a/2016.json"
            old_path.parent.mkdir(parents=True)
            old_path.write_text(json.dumps({
                "schemaVersion": 1, "year": 2016, "stepMinutes": 60,
                "station": "station-a", "values": old_values,
            }), encoding="utf-8")

            archived_values = [None] * year_minutes(2024, 60)
            archived_values[0] = 1.0
            archived_path = output / "hourly/station-a/2024.json"
            archived_path.write_text(json.dumps({
                "schemaVersion": 1, "year": 2024, "stepMinutes": 60,
                "station": "station-a", "values": archived_values,
                "flags": {"1": "$"},
            }), encoding="utf-8")

            config = {"stations": stations}
            self.assertEqual(archive_hourly(config, str(source), output), 3)
            update_manifest(config, output)

            yearly = json.loads((output / "hourly/station-a/2024.json").read_text(encoding="utf-8"))
            self.assertEqual(len(yearly["values"]), 8784)
            self.assertEqual(yearly["values"][:4], [1.5, None, 2.0, None])
            self.assertEqual(yearly["flags"]["1"], "$")
            self.assertEqual(json.loads(old_path.read_text())["values"][0], 0.7)
            self.assertEqual(json.loads((output / "hourly/station-a/2025.json").read_text())["values"][0], 3.0)
            self.assertEqual(json.loads((output / "hourly/station-b/2026.json").read_text())["values"][0], 4.0)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stations"], stations)
            self.assertEqual(manifest["hourlyYears"], {"station-a": [2016, 2024, 2025], "station-b": [2026]})


if __name__ == "__main__":
    unittest.main()
