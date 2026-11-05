import unittest

from fetcher.scripts.update_recent_from_kawabou_files import convert_records, drop_trailing_nulls, merge_records


def rec(ts, value):
    return {"timestamp": ts, "value": value, "flag": ""}


class KawabouNullHandlingTest(unittest.TestCase):
    def test_trailing_nulls_dropped_and_existing_value_kept(self):
        fetched = drop_trailing_nulls([rec("2026-11-05T22:50", 1.0), rec("2026-11-05T23:00", None),
                                       rec("2026-11-05T23:10", None)])
        self.assertEqual([r["timestamp"] for r in fetched], ["2026-11-05T22:50"])
        mid = drop_trailing_nulls([rec("a", None), rec("b", 2.0)])
        self.assertEqual(len(mid), 2)
        merged = merge_records([rec("2026-11-05T23:00", 1.5)], [rec("2026-11-05T23:00", None), rec("2026-11-05T23:10", 1.6)])
        self.assertEqual({r["timestamp"]: r["value"] for r in merged},
                         {"2026-11-05T23:00": 1.5, "2026-11-05T23:10": 1.6})


if __name__ == "__main__":
    unittest.main()
