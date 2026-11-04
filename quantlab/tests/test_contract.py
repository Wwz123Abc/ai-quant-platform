"""The ledger's rules, applied by the quant platform: the same answers as the ledger's own code gives to the cases in
ledger/tests/fixtures/contract.json (the ledger's tests check that file against its code)."""
import json
from pathlib import Path

import pytest

from quantlab.core import price_check
from quantlab.holdings import ledger

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "ledger" / "tests" / "fixtures" / "contract.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CONTRACT["holdings"], ids=lambda c: c["name"])
def test_positions_from_what_the_ledger_hands_out(case):
    got = {p["symbol"]: p for p in ledger.positions({"trades": case["programs"]})}
    for sym, want in case["holdings"].items():
        if want["shares"] < 1e-9:
            assert sym not in got           # sold out: not a position
        else:
            assert got[sym]["shares"] == pytest.approx(want["shares"]) and got[sym]["cost"] == pytest.approx(want["cost"])


@pytest.mark.parametrize("case", CONTRACT["fixed"], ids=lambda c: c["name"])
def test_treasuries_and_deposits(case):
    item = ledger.fixed_items({"fixed": [case["item"]]})[0]
    for day, want in zip(case["dates"], case["expected"], strict=True):
        got = ledger.fixed_on(item, day)
        if want is None:
            assert got is None, day
        else:
            assert got["value"] == pytest.approx(want["value"]) and got["matured"] == want["matured"], day


@pytest.mark.parametrize("case", CONTRACT["cross_check"], ids=lambda c: c["name"])
def test_cross_check(case):
    assert price_check.cross_check(case["tencent"], case["other"], case["today"], "新浪财经") == case["expected"]
