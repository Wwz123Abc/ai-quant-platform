"""Automatic live prices (app.refresh_quotes). Run: python -m unittest discover -s ledger/tests"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("ledger_app", Path(__file__).resolve().parent.parent / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

LIVE = {"price": 701.25, "date": "2026-11-04", "currency": "USD", "name": "VOO", "code": "VOO.AM", "kind": "live"}


class RefreshQuotes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [mock.patch.object(app, "DATA", self.dir.name),
                        mock.patch.object(app, "DB_PATH", os.path.join(self.dir.name, "ledger.db"))]
        for p in self.patches:
            p.start()
        app.init_db()
        self.uid = app.create_user("tester", "x")
        app.put_doc(self.uid, "trades", "t1", {"symbol": "VOO", "side": "buy", "shares": 1, "price": 690, "date": "2026-10-13"})
        app.put_doc(self.uid, "config", "main", {"prices": {"VOO": 693.54}, "quotes": {"VOO": {"date": "2026-11-03", "kind": "close", "source": "腾讯财经"}}})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def price(self):
        cfg = app.get_doc(self.uid, "config", "main")
        return cfg["prices"]["VOO"], cfg["quotes"]["VOO"]

    def refresh(self, quote, session=True):
        with mock.patch.object(app, "quotes", lambda syms: ({"VOO": quote} if quote else {}, {})), \
                mock.patch.object(app, "in_session", lambda sym, now=None: session):
            return app.refresh_quotes(self.uid)

    def test_a_live_price_replaces_the_close_and_is_marked_live(self):
        self.assertEqual(self.refresh(LIVE), {"VOO": 701.25})
        px, meta = self.price()
        self.assertEqual((px, meta["kind"], meta["date"], meta["source"]), (701.25, "live", "2026-11-04", "腾讯财经"))

    def test_nothing_happens_outside_the_session(self):
        before = app.get_meta("version:%d" % self.uid)
        self.assertEqual(self.refresh(LIVE, session=False), {})
        self.assertEqual(self.price()[0], 693.54)
        self.assertEqual(app.get_meta("version:%d" % self.uid), before)

    def test_an_unchanged_price_writes_nothing_so_the_page_version_stays(self):
        self.refresh(LIVE)
        version = app.get_meta("version:%d" % self.uid)
        self.assertEqual(self.refresh(LIVE), {})
        self.assertEqual(app.get_meta("version:%d" % self.uid), version)
        self.assertEqual(self.refresh({**LIVE, "price": 701.5}), {"VOO": 701.5})

    def test_a_close_from_the_quote_changes_nothing_while_the_stored_price_is_a_close(self):
        self.assertEqual(self.refresh({**LIVE, "kind": "close"}), {})
        self.assertEqual(self.price()[1]["kind"], "close")

    def test_when_the_session_ends_the_last_live_price_becomes_the_close_once(self):
        self.refresh(LIVE)                                                    # during the session
        closing = {**LIVE, "price": 702.0, "kind": "close"}
        self.assertEqual(self.refresh(closing, session=False), {"VOO": 702.0})  # just after the bell
        px, meta = self.price()
        self.assertEqual((px, meta["kind"]), (702.0, "close"))
        self.assertEqual(self.refresh(closing, session=False), {})            # and then it is left alone

    def test_a_manual_price_from_a_later_day_is_kept(self):
        app.put_doc(self.uid, "config", "main", {"quotes": {"VOO": {"date": "2026-11-05", "kind": "close", "source": "手动"}}}, merge=True)
        self.assertEqual(self.refresh(LIVE), {})
        self.assertEqual(self.price()[0], 693.54)

    def test_a_missing_quote_leaves_the_old_price(self):
        self.assertEqual(self.refresh(None), {})
        self.assertEqual(self.price()[0], 693.54)

    def test_only_stocks_in_session_are_asked_for(self):
        asked = []
        with mock.patch.object(app, "quotes", lambda syms: (asked.append(list(syms)) or {}, {})), \
                mock.patch.object(app, "in_session", lambda sym, now=None: sym == "VOO"):
            app.put_doc(self.uid, "trades", "t2", {"symbol": "0700", "side": "buy", "shares": 100, "price": 400, "date": "2026-10-13"})
            app.refresh_quotes(self.uid)
        self.assertEqual(asked, [["VOO"]])


if __name__ == "__main__":
    unittest.main()


class MinuteSeries(unittest.TestCase):
    US = '{"code":0,"data":{"usVOO.AM":{"data":{"data":["0930 692.10 1189108","0931 692.39 1440682","0932 bad","0933 0 5","1559 693.50 9"],"date":"20261103"}}}}'

    def setUp(self):
        app._minutes.clear()

    def test_minutes_become_epoch_seconds_in_the_market_timezone(self):
        with mock.patch.object(app, "fetch", lambda url, **kw: self.US):
            s = app.minute_series("VOO")
        self.assertEqual(s["date"], "2026-11-03")
        # 09:30 in New York on 2026-11-03 is EST (UTC-5): 14:30 UTC
        self.assertEqual(s["open"], 1793716200)
        self.assertEqual(s["close"] - s["open"], 6.5 * 3600)
        self.assertEqual(s["points"], [[1793716200, 692.1], [1793716260, 692.39], [1793716200 + 389 * 60, 693.5]])  # junk rows are skipped

    def test_hong_kong_uses_its_own_clock(self):
        hk = '{"code":0,"data":{"hk00700":{"data":{"data":["0930 397.800 6 7"],"date":"20261104"}}}}'
        with mock.patch.object(app, "fetch", lambda url, **kw: hk) as f:
            s = app.minute_series("0700")
        self.assertEqual(s["open"], 1793755800)  # 09:30 HKT = 01:30 UTC

    def test_the_same_request_within_the_ttl_is_served_from_memory(self):
        calls = []
        def fake(url, **kw):
            calls.append(url)
            return self.US
        with mock.patch.object(app, "fetch", fake):
            app.minute_series("VOO")
            app.minute_series("VOO")
        self.assertEqual(len(calls), 1)
        self.assertIn("UsMinute", calls[0])

    def test_no_data_is_an_error_not_an_empty_chart(self):
        with mock.patch.object(app, "fetch", lambda url, **kw: '{"code":0,"data":{"usVOO":{"data":{"data":[],"date":"20261103"}}}}'):
            with self.assertRaises(ValueError):
                app.minute_series("VOO")


class NewStock(unittest.TestCase):
    """A stock bought for the first time gets its price and history by itself (app.fill_missing)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [mock.patch.object(app, "DATA", self.dir.name), mock.patch.object(app, "DB_PATH", os.path.join(self.dir.name, "ledger.db"))]
        for p in self.patches:
            p.start()
        app.init_db()
        app._missing_tries.clear()
        self.uid = app.create_user("tester", "x")
        app.put_doc(self.uid, "trades", "t1", {"symbol": "VOO", "side": "buy", "shares": 3, "price": 690, "date": "2026-10-13"})
        app.put_doc(self.uid, "trades", "t2", {"symbol": "PEP", "side": "buy", "shares": 2, "price": 131, "date": "2026-10-15"})
        app.put_doc(self.uid, "trades", "t3", {"symbol": "PEP", "side": "sell", "shares": 2, "price": 137, "date": "2026-10-22"})   # sold out
        app.put_doc(self.uid, "config", "main", {"prices": {"VOO": 693.54}})
        app.put_doc(0, "history", "VOO", {"symbol": "VOO", "closes": {"2026-11-03": 693.54}})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def test_only_stocks_held_now_are_wanted(self):
        self.assertEqual(app.held_symbols(self.uid), ["VOO"])           # PEP was bought and sold: not held
        self.assertEqual(app.missing_market_data(self.uid), [])

    def test_a_new_buy_without_a_price_is_fetched_once_and_not_hammered(self):
        app.put_doc(self.uid, "trades", "t4", {"symbol": "BAC", "side": "buy", "shares": 1, "price": 50, "date": "2026-11-06"})
        self.assertEqual(app.missing_market_data(self.uid), ["BAC"])
        calls = []
        with mock.patch.object(app, "refresh_market", lambda uid: calls.append(uid)):
            self.assertEqual(app.fill_missing(self.uid, now=1000.0), ["BAC"])
            self.assertEqual(app.fill_missing(self.uid, now=1010.0), [])            # too soon to try again
            self.assertEqual(app.fill_missing(self.uid, now=1000.0 + app.MISSING_RETRY + 1), ["BAC"])
            self.assertEqual(app.fill_missing(self.uid, now=2000.0 + 2 * app.MISSING_RETRY), ["BAC"])
            self.assertEqual(app.fill_missing(self.uid, now=9e6), [])               # three tries and it still has no data: stop
        self.assertEqual(len(calls), 3)

    def test_nothing_is_fetched_when_every_held_stock_has_data(self):
        with mock.patch.object(app, "refresh_market", lambda uid: self.fail("should not run")):
            self.assertEqual(app.fill_missing(self.uid), [])

    def test_a_stock_with_a_price_but_no_history_still_counts_as_missing(self):
        app.put_doc(self.uid, "trades", "t5", {"symbol": "KO", "side": "buy", "shares": 1, "price": 60, "date": "2026-11-06"})
        app.put_doc(self.uid, "config", "main", {"prices": {"VOO": 693.54, "KO": 61.0}}, merge=True)
        self.assertEqual(app.missing_market_data(self.uid), ["KO"])
