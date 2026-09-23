import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.live_site import checked_records, package, prepare


def payload(*values):
    records = [{"timestamp": timestamp, "value": level, "flag": "", "resolution": "10min"}
               for timestamp, level in values]
    return {"meta": {"station_code": "123", "dataset_end": records[-1]["timestamp"]}, "records": records}


class LiveSiteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.viewer = self.root / "viewer"
        (self.viewer / "config").mkdir(parents=True)
        (self.viewer / "data/stations/example").mkdir(parents=True)
        (self.viewer / "config/stations.json").write_text(json.dumps({"stations": [
            {"id": "example", "data_dir": "data/stations/example"}
        ]}))
        self.station_file = self.viewer / "data/stations/example/recent_10min.json"
        self.station_file.write_text(json.dumps(payload(("2026-09-22T09:00", 1.0))))
        self.state = self.root / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_bootstrap_uses_checkout_and_packages_without_git_writes(self):
        with patch("scripts.live_site.read_live", return_value=None):
            self.assertEqual(prepare(self.viewer, self.state, "https://example.org/live", True),
                             {"example": "2026-09-22T09:00"})
        self.station_file.write_text(json.dumps(payload(
            ("2026-09-22T09:00", 1.0), ("2026-09-22T09:10", 1.1))))
        output = self.root / "site"
        self.assertEqual(package(self.viewer, self.state, output), {"example": "2026-09-22T09:10"})
        published = json.loads((output / "live/stations/example/recent_10min.json").read_text())
        self.assertEqual(len(published["records"]), 2)
        self.assertTrue((output / "index.html").exists())

    def test_live_state_wins_on_duplicate_timestamp(self):
        live = payload(("2026-09-22T09:00", 2.0), ("2026-09-22T09:10", 2.1))
        with patch("scripts.live_site.read_live", return_value=live):
            prepare(self.viewer, self.state, "https://example.org/live", False)
        merged = json.loads(self.station_file.read_text())["records"]
        self.assertEqual([record["value"] for record in merged], [2.0, 2.1])

    def test_regular_run_requires_live_state(self):
        with patch("scripts.live_site.read_live", return_value=None):
            with self.assertRaises(ValueError):
                prepare(self.viewer, self.state, "https://example.org/live", False)

    def test_unsorted_source_rejected(self):
        with self.assertRaises(ValueError):
            checked_records(payload(("2026-09-22T09:10", 1), ("2026-09-22T09:00", 2)), "fixture")


if __name__ == "__main__":
    unittest.main()
