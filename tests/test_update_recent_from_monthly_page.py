import unittest

from fetcher.scripts.update_recent_from_monthly_page import parse_monthly_dat


def dat_line(day: str, values: list[float]) -> str:
    assert len(values) == 24
    cells = []
    for value in values:
        cells.append(f"{value:.2f}")
        cells.append("")
    return day + "," + ",".join(cells)


class ParseMonthlyDatTest(unittest.TestCase):
    def test_column_n_is_labelled_n_oclock_not_n_minus_1(self):
        # N列目(N=1..24)は「N時」の正時水位。1列目(N=1)は当日1:00、
        # 24列目(N=24)は翌日0:00として保存されるべき。
        text = dat_line("2026/09/22", [float(n) for n in range(1, 25)])
        records = parse_monthly_dat(text)

        self.assertEqual(records[0].timestamp, "2026-09-22T01:00")
        self.assertEqual(records[0].value, 1.0)
        self.assertEqual(records[22].timestamp, "2026-09-22T23:00")
        self.assertEqual(records[22].value, 23.0)
        self.assertEqual(records[23].timestamp, "2026-09-23T00:00")
        self.assertEqual(records[23].value, 24.0)

    def test_month_end_column_24_rolls_over_to_next_month(self):
        text = dat_line("2026/01/31", [float(n) for n in range(1, 25)])
        records = parse_monthly_dat(text)
        self.assertEqual(records[-1].timestamp, "2026-02-01T00:00")

    def test_year_end_column_24_rolls_over_to_next_year(self):
        text = dat_line("2026/12/31", [float(n) for n in range(1, 25)])
        records = parse_monthly_dat(text)
        self.assertEqual(records[-1].timestamp, "2027-01-01T00:00")

    def test_missing_and_invalid_values_keep_flag_and_timestamp(self):
        row = ["2026/09/22"]
        for n in range(1, 25):
            if n == 5:
                row.extend(["", ""])
            elif n == 6:
                row.extend(["-999.0", "$"])
            else:
                row.extend([f"{n}.0", ""])
        text = ",".join(row)
        records = parse_monthly_dat(text)
        self.assertEqual(records[4].timestamp, "2026-09-22T05:00")
        self.assertIsNone(records[4].value)
        self.assertEqual(records[5].timestamp, "2026-09-22T06:00")
        self.assertIsNone(records[5].value)
        self.assertEqual(records[5].flag, "$")


if __name__ == "__main__":
    unittest.main()
