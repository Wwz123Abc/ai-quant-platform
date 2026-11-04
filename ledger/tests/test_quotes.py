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
