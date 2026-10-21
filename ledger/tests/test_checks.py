"""Cross-checking Tencent's closes with Eastmoney's (app.cross_check). Run: python -m unittest discover -s ledger/tests"""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ledger_app", Path(__file__).resolve().parent.parent / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

DAYS = ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]


def bars(closes, days=DAYS):
    return [[d, c, c, c, c, 1000] for d, c in zip(days, closes)]


class CrossCheck(unittest.TestCase):
    def test_same_closes_agree(self):
        r = app.cross_check(bars([1, 2, 3, 4, 5, 6]), bars([1, 2, 3, 4, 5, 6]), "2026-10-21")
        self.assertEqual((r["mismatches"], r["missing"], r["stale"], r["latest"]), ([], [], False, "2026-10-20"))
        self.assertEqual(r["days"], DAYS[-5:])  # the last five days only

    def test_a_close_off_by_more_than_the_tolerance(self):
        r = app.cross_check(bars([1, 2, 3, 4, 5, 6.1]), bars([1, 2, 3, 4, 5, 6]), "2026-10-21")
        self.assertEqual(r["mismatches"], [["2026-10-20", 6.1, 6]])
        tiny = app.cross_check(bars([1, 2, 3, 4, 5, 6.006]), bars([1, 2, 3, 4, 5, 6]), "2026-10-21")
        self.assertEqual(tiny["mismatches"], [])  # 0.1% is rounding, not a disagreement

    def test_a_day_one_source_lacks(self):
        t = bars([1, 2, 3, 5, 6], DAYS[:3] + DAYS[4:])  # Tencent skips 10-16
        r = app.cross_check(t, bars([1, 2, 3, 4, 5, 6]), "2026-10-21")
        self.assertEqual(r["missing"], [["2026-10-16", "腾讯"]])

    def test_stale_when_the_latest_close_is_old(self):
        self.assertTrue(app.cross_check(bars([1, 2, 3], DAYS[:3]), bars([1, 2, 3], DAYS[:3]), "2026-10-23")["stale"])
        self.assertFalse(app.cross_check(bars([1, 2, 3], DAYS[:3]), bars([1, 2, 3], DAYS[:3]), "2026-10-19")["stale"])

    def test_windows_line_up_when_one_source_has_more_history(self):
        long = bars([0.5, 1, 2, 3, 4, 5, 6], ["2026-10-12"] + DAYS)
        r = app.cross_check(long, bars([3, 4, 5, 6], DAYS[2:]), "2026-10-21")
        self.assertEqual((r["mismatches"], r["missing"]), ([], []))

    def test_eastmoney_market_ids(self):
        self.assertEqual(app.em_markets("QQQM.OQ"), ["105"])
        self.assertEqual(app.em_markets("MCD.N"), ["106"])
        self.assertEqual(app.em_markets("VOO.AM"), ["107"])
        self.assertEqual(app.em_markets("VOO"), ["105", "106", "107"])


if __name__ == "__main__":
    unittest.main()
