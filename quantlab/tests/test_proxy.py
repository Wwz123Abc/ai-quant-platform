"""Behind the ledger site: nginx checks the ledger session and passes the user in a header, under /lab."""
import pytest
from fastapi.testclient import TestClient

from quantlab.api import app as app_module
from quantlab.api.app import create_app
from quantlab.config import Settings, get_settings

USER = {"X-Ledger-User": "admin"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANTLAB_AUTH_MODE", "proxy")
    monkeypatch.setenv("QUANTLAB_BASE_PATH", "/lab/")
    monkeypatch.setenv("QUANTLAB_HOME_URL", "/")
    get_settings.cache_clear()
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><html><head><title>x</title></head><body></body></html>", encoding="utf-8")
    monkeypatch.setattr(app_module, "WEB", web)
    return TestClient(create_app(), root_path="/lab")  # uvicorn gets root_path=base_path in `quantlab serve`


def test_user_header_replaces_the_token(client):
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/session", headers={"Authorization": "Bearer test-token"}).status_code == 401
    r = client.get("/api/session", headers=USER)
    assert r.status_code == 200 and r.json()["auth"] == "proxy" and r.json()["home_url"] == "/"


def test_writes_need_the_same_site_header(client):
    body = {"symbol": "QQQM", "fast": 5, "slow": 20}
    assert client.post("/api/backtests", json=body, headers=USER).status_code == 403
    r = client.post("/api/backtests", json=body, headers={**USER, "X-Requested-With": "quantlab"})
    assert r.status_code != 403


def test_page_gets_the_base_href(client):
    for path in ("/", "/holdings", "/index.html"):
        r = client.get(path)
        assert '<head>\n    <base href="/lab/" />' in r.text and r.headers["Cache-Control"] == "no-cache"


def test_docs_point_at_the_base_path(client):
    assert "/lab/api/openapi.json" in client.get("/api/docs").text


@pytest.mark.parametrize("bad", ["lab", "/lab/../x", "/la b", '/"><script>'])
def test_base_path_is_checked(monkeypatch, bad):
    monkeypatch.setenv("QUANTLAB_BASE_PATH", bad)
    with pytest.raises(ValueError):
        Settings()


def test_token_mode_is_unchanged():
    get_settings.cache_clear()
    c = TestClient(create_app())
    assert c.get("/api/session", headers=USER).status_code == 401
    assert c.get("/api/health").json()["auth"] == "token"


def test_secrets_set_writes_env_without_echo(tmp_path, monkeypatch, capsys):
    import getpass

    from quantlab import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FRED_API_KEY=old\nOTHER=1\n", encoding="utf-8")
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "new-value ")
    cli.main(["secrets", "set", "FRED_API_KEY"])
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "OTHER=1\nFRED_API_KEY=new-value\n"
    assert "new-value" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["secrets", "set", "NOT_A_KEY"])
