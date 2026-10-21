import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.core import data
from quantlab.errors import DataSourceError, NotFound

AUTH = {"Authorization": "Bearer test-token"}


def fake_bars(n=1600, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    idx = pd.bdate_range("2019-01-01", periods=n)
    return pd.DataFrame({"open": close * 0.998, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6}, index=idx)


@pytest.fixture
def client(monkeypatch):
    market = {"QQQM": fake_bars(seed=1), "VOO": fake_bars(seed=2)}

    def load(sym, refresh=False):
        sym = data.normalize(sym)
        if sym == "DOWN":
            raise DataSourceError("连不上 Yahoo 行情")
        if sym not in market:
            raise NotFound("Yahoo 找不到 %s 的数据" % sym)
        return market[sym]

    monkeypatch.setattr(data, "load", load)
    return TestClient(create_app())


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "test-token"}])
def test_token_required(client, headers):
    r = client.get("/api/signals?symbols=QQQM", headers=headers)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"


def test_session_checks_the_token(client):
    assert client.get("/api/session", headers=AUTH).json()["status"] == "ok"
    assert client.get("/api/session").status_code == 401


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"


def test_backtest(client):
    r = client.post("/api/backtests", json={"symbol": "qqqm", "fast": 20, "slow": 60, "slippage": 0}, headers=AUTH)
    assert r.status_code == 200, r.text
    b = r.json()
    n = len(b["series"]["dates"])
    assert n > 1000 and all(len(b["series"][k]) == n for k in ("close", "fast", "slow", "equity", "benchmark"))
    assert b["symbol"] == "QQQM" and b["summary"]["trades"] == len(b["trades"])
    assert b["benchmark"]["trades"] == 1 and b["summary"]["final"] == pytest.approx(b["series"]["equity"][-1])
    assert all("return" in t for t in b["trades"])


@pytest.mark.parametrize("body,status", [
    ({"symbol": "QQQM", "fast": 200, "slow": 50}, 422),   # fast must be below slow
    ({"symbol": "QQQM", "cash": -1}, 422),
    ({"symbol": "A B!"}, 400),            # passes the schema, fails symbol validation
    ({"symbol": "NOPE"}, 404),
    ({"symbol": "DOWN"}, 502),
])
def test_backtest_errors(client, body, status):
    r = client.post("/api/backtests", json=body, headers=AUTH)
    assert r.status_code == status, r.text
    assert r.json()["detail"]


def test_scan(client):
    body = {"symbol": "VOO", "fasts": [10, 20, 50], "slows": [50, 100, 200], "split": "2023-01-02"}
    r = client.post("/api/scans", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    s = r.json()
    assert len(s["rows"]) == 8  # pairs with fast < slow
    assert [x["is_rank"] for x in s["rows"]] == sorted(x["is_rank"] for x in s["rows"])
    assert s["in_sample"]["end"] < "2023-01-02" <= s["out_sample"]["start"]
    assert -1 <= s["rank_corr"] <= 1


def test_scan_too_many_pairs(client):
    body = {"symbol": "VOO", "fasts": list(range(2, 22)), "slows": list(range(30, 50))}
    r = client.post("/api/scans", json=body, headers=AUTH)
    assert r.status_code == 400 and "最多" in r.json()["detail"]


def test_signals_report_each_symbol(client):
    r = client.get("/api/signals?symbols=QQQM,NOPE,VOO&fast=20&slow=60", headers=AUTH)
    assert r.status_code == 200
    items = {x["symbol"]: x for x in r.json()}
    assert items["QQQM"]["ok"] and items["VOO"]["ok"] and not items["NOPE"]["ok"]
    assert items["NOPE"]["error"]


def test_bars(client):
    r = client.get("/api/market/qqqm/bars?start=2024-01-01", headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["dates"][0] >= "2024-01-01" and len(b["dates"]) == len(b["close"])


def test_paper_account_lifecycle(client):
    body = {"name": "核心", "symbols": ["QQQM", "voo", "QQQM"], "fast": 20, "slow": 60, "cash": 10000}
    r = client.post("/api/paper/accounts", json=body, headers=AUTH)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["symbols"] == ["QQQM", "VOO"] and a["days"] == 0 and a["value"] == 10000
    assert {p["symbol"] for p in a["positions"]} == {"QQQM", "VOO"}

    assert client.post("/api/paper/accounts", json=body, headers=AUTH).status_code == 409
    assert client.post("/api/paper/accounts", json={**body, "name": "a b"}, headers=AUTH).status_code == 400

    r = client.post("/api/paper/accounts/核心/record", headers=AUTH)
    assert r.status_code == 200 and r.json()["recorded"] == 0  # nothing new until the next close

    assert [x["name"] for x in client.get("/api/paper/accounts", headers=AUTH).json()] == ["核心"]
    assert client.get("/api/paper/accounts/核心", headers=AUTH).json()["equity"] == {"dates": [], "strategy": [], "benchmark": []}

    assert client.delete("/api/paper/accounts/核心", headers=AUTH).status_code == 204
    assert client.get("/api/paper/accounts/核心", headers=AUTH).status_code == 404
    assert client.delete("/api/paper/accounts/核心", headers=AUTH).status_code == 404


def test_unknown_api_path(client):
    assert client.get("/api/nothing", headers=AUTH).status_code == 404
