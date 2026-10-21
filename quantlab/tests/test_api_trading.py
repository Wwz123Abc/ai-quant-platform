import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.core import data
from quantlab.trading.broker import connect

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(monkeypatch):
    idx = pd.bdate_range(end="2026-10-16", periods=5)
    monkeypatch.setattr(data, "load", lambda sym, refresh=False: pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}, index=idx))
    return TestClient(create_app())


def test_limits(client):
    lim = client.get("/api/trading/limits", headers=AUTH).json()
    assert lim["capital"] == 500 and lim["live_enabled"] is False and lim["busy"] is False


def test_account_lifecycle(client):
    r = client.post("/api/trading/accounts", json={"name": "模拟", "mode": "paper", "capital": 400}, headers=AUTH)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["value"] == 400 and a["positions"] == [] and a["events"][0]["kind"] == "account"
    assert client.post("/api/trading/accounts", json={"name": "模拟"}, headers=AUTH).status_code == 409
    assert client.post("/api/trading/accounts", json={"name": "大", "capital": 10_000}, headers=AUTH).status_code == 400

    con = connect()
    with con:
        con.execute("INSERT INTO positions VALUES ('模拟', 'AAA', 1.5)")
        con.execute("UPDATE accounts SET cash = 250 WHERE name = '模拟'")
    con.close()
    a = client.get("/api/trading/accounts/模拟", headers=AUTH).json()
    assert a["value"] == pytest.approx(400) and a["positions"][0]["weight"] == pytest.approx(150 / 400)

    a = client.post("/api/trading/accounts/模拟/halt", json={"reason": "测试"}, headers=AUTH).json()
    assert a["halted"] == "测试"
    assert client.post("/api/trading/accounts/模拟/resume", headers=AUTH).json()["halted"] is None
    r = client.post("/api/trading/accounts/模拟/mode", json={"mode": "live"}, headers=AUTH)
    assert r.status_code == 503 and "实盘" in r.json()["detail"]
    assert client.post("/api/trading/accounts/模拟/mode", json={"mode": "plan"}, headers=AUTH).json()["mode"] == "plan"
    assert client.get("/api/trading/accounts/没有", headers=AUTH).status_code == 404


def test_signal_not_computed_yet(client):
    assert client.get("/api/ml/signal", headers=AUTH).status_code == 404
