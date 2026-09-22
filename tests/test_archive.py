import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.archive_water_levels import archive_recent


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


if __name__ == "__main__":
    unittest.main()
