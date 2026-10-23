"""Cross-checking Tencent's closes with a second source's (app.cross_check). Run: python -m unittest discover -s ledger/tests"""
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

    def test_the_second_source_not_yet_updated_is_not_missing(self):
        r = app.cross_check(bars([1, 2, 3, 4, 5, 6]), bars([1, 2, 3, 4, 5], DAYS[:5]), "2026-10-21", "新浪财经")
        self.assertEqual((r["missing"], r["days"][-1]), ([], "2026-10-19"))  # 10-20 waits for the next refresh

    def test_the_second_source_far_behind_or_with_a_gap_is_missing(self):
        r = app.cross_check(bars([1, 2, 3, 4, 5, 6]), bars([1, 2, 3, 5, 6], DAYS[:3] + DAYS[4:]), "2026-10-21", "新浪财经")
        self.assertEqual(r["missing"], [["2026-10-16", "新浪财经"]])
        old = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]
        r = app.cross_check(bars([1, 2, 3, 4, 5, 6, 7], old[:2] + DAYS[1:]), bars([1, 2, 3, 4, 5], old), "2026-10-21", "新浪财经")
        self.assertTrue(r["missing"])  # two weeks behind is not a lag

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


class SecondSource(unittest.TestCase):
    def test_parse_sina_daily(self):
        text = ('var _QQQM=([{"d":"2026-10-20","o":"294.54","h":"297.56","l":"292.52","c":"295.59","v":"3664385"},'
                '{"d":"bad"},{"d":"2026-10-21","o":"297.44","h":"299.74","l":"296.50","c":"298.64","v":"2476663"}]);')
        self.assertEqual(app.parse_sina_daily(text), [["2026-10-20", 294.54, 297.56, 292.52, 295.59, 3664385],
                                                      ["2026-10-21", 297.44, 299.74, 296.5, 298.64, 2476663]])
        self.assertEqual(app.parse_sina_daily("<html>blocked</html>"), [])

    def test_sina_first_then_eastmoney(self):
        real = app.sina_bars, app.em_bars
        try:
            app.sina_bars = lambda sym, days: bars([1, 2, 3])
            app.em_bars = lambda sym, code, days: self.fail("Eastmoney asked although Sina answered")
            self.assertEqual(app.second_source("X", "X", 5)[0], "新浪财经")

            def down(*a):
                raise OSError("Remote end closed connection")
            app.sina_bars = down
            app.em_bars = lambda sym, code, days: bars([1, 2, 3])
            name, got, problems = app.second_source("X", "X", 5)
            self.assertEqual((name, len(got)), ("东方财富", 3))
            self.assertIn("新浪财经：Remote end closed connection", problems[0])
            app.em_bars = lambda sym, code, days: []
            self.assertEqual(app.second_source("X", "X", 5)[:2], ("", []))
        finally:
            app.sina_bars, app.em_bars = real


class SourceCheck(unittest.TestCase):
    def setUp(self):
        names = ("quotes", "daily_bars", "sina_bars", "us_splits", "fx_rates", "usdcny_bars", "gold_bars", "em_bars", "em_quote")
        self.real = {n: getattr(app, n) for n in names}
        today = "2026-10-21"
        app.quotes = lambda syms: ({s: {"price": 1.0, "date": today} for s in syms}, {})
        app.daily_bars = lambda sym, code, days=320: bars([1, 2, 3])
        app.sina_bars = lambda sym, days=320: bars([1, 2, 3])
        app.us_splits = lambda sym: [["2015-07-15", 1, 7.0], ["2025-11-17", 1, 10.0]]
        app.fx_rates = lambda: {"USDHKD": 7.8}
        app.usdcny_bars = lambda since="": [[today, 7, 7, 7, 7]]
        app.gold_bars = lambda: [[today, 1, 1, 1, 1]]
        app.em_bars = lambda sym, code, days=320: bars([1, 2, 3])
        app.em_quote = lambda sym, code: {"price": 1.0, "date": today}

    def tearDown(self):
        for n, f in self.real.items():
            setattr(app, n, f)

    def by_name(self, today="2026-10-21"):
        return {r["name"]: r for r in app.check_sources(today)}

    def test_all_sources_answering(self):
        self.assertTrue(all(r["ok"] for r in self.by_name().values()))

    def test_a_failing_source_and_a_stale_one_are_reported(self):
        def down(*a):
            raise OSError("Remote end closed connection")
        app.sina_bars = down
        app.gold_bars = lambda: [["2026-09-01", 1, 1, 1, 1]]
        r = self.by_name()
        self.assertFalse(r["新浪日 K 线"]["ok"])
        self.assertIn("Remote end closed connection", r["新浪日 K 线"]["detail"])
        self.assertFalse(r["新浪黄金期货"]["ok"])  # a month old
        self.assertTrue(r["腾讯日 K 线"]["ok"])

    def test_a_split_missing_from_the_list_is_caught(self):
        app.us_splits = lambda sym: [["2015-07-15", 1, 7.0]]
        self.assertFalse(self.by_name()["新浪拆股记录"]["ok"])

    def test_stand_ins_are_marked(self):
        roles = {n: r["role"] for n, r in self.by_name().items()}
        self.assertEqual(roles["东方财富日 K 线"], "备用")
        self.assertEqual(roles["新浪日 K 线"], "核对")


if __name__ == "__main__":
    unittest.main()
