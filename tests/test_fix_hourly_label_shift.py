import json
import tempfile
import unittest
from pathlib import Path

from scripts.archive_water_levels import year_minutes
from scripts.fix_hourly_label_shift import shift_station, target_station_ids


def make_year_file(directory: Path, station_id: str, year: int, values: list, flags: dict | None = None) -> Path:
    payload = {
        "schemaVersion": 1,
        "year": year,
        "stepMinutes": 60,
        "station": station_id,
        "values": values,
        "source": "river.go.jp via tanyeee/kuji-waterlevel",
    }
    if flags:
        payload["flags"] = flags
    path = directory / f"{year}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TargetStationIdsTest(unittest.TestCase):
    def test_only_stations_with_hourly_station_id_are_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "stations.json"
            config_path.write_text(json.dumps({"stations": [
                {"id": "monthly-page-station", "hourly": {"station_id": "123"}},
                {"id": "kawabou-only-station", "kawabou": {"ofc_cd": "1"}},
            ]}), encoding="utf-8")
            self.assertEqual(target_station_ids(config_path), ["monthly-page-station"])


class ShiftStationTest(unittest.TestCase):
    def test_shift_moves_values_across_year_boundary_and_nulls_oldest_head(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            station_dir = root / "hourly" / "example"
            station_dir.mkdir(parents=True)

            len_2016 = year_minutes(2016, 60)
            len_2017 = year_minutes(2017, 60)
            values_2016 = [None] * len_2016
            values_2016[0] = 1.0
            values_2016[-1] = 9.0
            values_2017 = [None] * len_2017
            values_2017[0] = 2.0

            make_year_file(station_dir, "example", 2016, values_2016, flags={"0": "*", str(len_2016 - 1): "*"})
            make_year_file(station_dir, "example", 2017, values_2017, flags={"0": "*"})

            status, stats = shift_station(root, "example")
            self.assertEqual(status, "fixed")

            fixed_2016 = json.loads((station_dir / "2016.json").read_text())
            fixed_2017 = json.loads((station_dir / "2017.json").read_text())

            self.assertIsNone(fixed_2016["values"][0])
            self.assertEqual(fixed_2016["values"][1], 1.0)
            # 2016年末の最終要素(9.0)は2017年の先頭へ移る。
            self.assertEqual(fixed_2017["values"][0], 9.0)
            self.assertEqual(fixed_2017["values"][1], 2.0)
            self.assertTrue(fixed_2016["labelShiftFixed"])
            self.assertTrue(fixed_2017["labelShiftFixed"])

            # フラグも値と同じだけずれる。
            self.assertNotIn("0", fixed_2016.get("flags", {}))
            self.assertEqual(fixed_2016["flags"]["1"], "*")
            self.assertEqual(fixed_2017["flags"]["0"], "*")
            self.assertEqual(fixed_2017["flags"]["1"], "*")

            self.assertEqual(len(fixed_2016["values"]), len_2016)
            self.assertEqual(len(fixed_2017["values"]), len_2017)
            self.assertEqual(stats["non_null_before"], stats["non_null_after"])

    def test_running_twice_is_a_noop_second_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            station_dir = root / "hourly" / "example"
            station_dir.mkdir(parents=True)
            length = year_minutes(2016, 60)
            values = [None] * length
            values[5] = 1.5
            make_year_file(station_dir, "example", 2016, values)

            status_first, _ = shift_station(root, "example")
            self.assertEqual(status_first, "fixed")
            after_first = json.loads((station_dir / "2016.json").read_text())

            status_second, stats_second = shift_station(root, "example")
            self.assertEqual(status_second, "skipped-already-fixed")
            self.assertEqual(stats_second, {})

            after_second = json.loads((station_dir / "2016.json").read_text())
            self.assertEqual(after_first, after_second)

    def test_refuses_to_shift_when_last_hour_of_latest_year_has_real_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            station_dir = root / "hourly" / "example"
            station_dir.mkdir(parents=True)
            length = year_minutes(2016, 60)
            values = [None] * length
            values[-1] = 3.3  # 未来枠にデータがある異常系
            make_year_file(station_dir, "example", 2016, values)

            with self.assertRaises(ValueError):
                shift_station(root, "example")

    def test_no_data_directory_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status, stats = shift_station(root, "missing-station")
            self.assertEqual(status, "skipped-no-data")
            self.assertEqual(stats, {})


if __name__ == "__main__":
    unittest.main()
