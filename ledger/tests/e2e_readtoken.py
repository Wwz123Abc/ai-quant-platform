"""End-to-end test of the ledger's read-only key on a throwaway copy with an empty database."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent  # the ledger folder
work = Path(tempfile.mkdtemp(prefix="ledger-rt-"))
shutil.copy(SRC / "app.py", work / "app.py")
shutil.copytree(SRC / "static", work / "static")
PORT = 5099
srv = subprocess.Popen([sys.executable, "app.py", "serve", "--port", str(PORT)], cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
BASE = "http://127.0.0.1:%d" % PORT
ok = 0


def call(method, path, body=None, cookie=None, auth=None, xrw=True):
    h = {"Content-Type": "application/json"}
    if xrw:
        h["X-Requested-With"] = "ledger"
    if cookie:
        h["Cookie"] = "sid=" + cookie
    if auth:
        h["Authorization"] = "Bearer " + auth
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), ""


def check(cond, what):
    global ok
    assert cond, what
    ok += 1
    print("ok  ", what)


try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/auth", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    st, _, sc = call("POST", "/api/register", {"name": "tester", "password": "password123", "agree": True})
    check(st == 200 and "sid=" in sc, "register and get a session")
    sid = sc.split("sid=")[1].split(";")[0]
    st, b, _ = call("POST", "/api/c/trades", {"date": "2026-10-15", "side": "buy", "symbol": "MCD", "shares": 2, "price": 244}, sid)
    check(st == 200, "add a trade")
    call("POST", "/api/c/cash", {"name": "汇丰美元储蓄", "currency": "USD", "amount": 2265}, sid)

    st, info, _ = call("GET", "/api/readtoken", cookie=sid)
    check(st == 200 and info["exists"] is False, "no key at first")
    st, _, _ = call("POST", "/api/readtoken", cookie=sid, xrw=False)
    check(st == 403, "creating a key needs the same-site header")
    st, r, _ = call("POST", "/api/readtoken", cookie=sid)
    tok = r["token"]
    check(st == 200 and tok.startswith("lrt_") and r["exists"], "create a key, shown once")

    st, h, _ = call("GET", "/api/holdings", auth=tok)
    check(st == 200 and h["account"] == "tester" and h["trades"][0]["symbol"] == "MCD" and h["cash"][0]["amount"] == 2265,
          "the key reads trades and cash")
    check(set(h) == {"account", "generated", "trades", "fixed", "cash"}, "and nothing else (no settings, notes or plans)")
    st, _, _ = call("GET", "/api/c/trades", auth=tok)
    check(st == 401, "the key cannot use the normal record API")
    st, _, _ = call("POST", "/api/c/trades", {"symbol": "X"}, auth=tok)
    check(st == 401, "the key cannot write")
    st, _, _ = call("GET", "/api/export", auth=tok)
    check(st == 401, "the key cannot export")
    st, _, _ = call("GET", "/api/holdings", cookie=sid)
    check(st == 401, "a browser session cannot use the holdings endpoint")
    st, info, _ = call("GET", "/api/readtoken", cookie=sid)
    check(info["last_used"] is not None, "last use is recorded")

    st, r2, _ = call("POST", "/api/readtoken", cookie=sid)
    st, _, _ = call("GET", "/api/holdings", auth=tok)
    check(st == 401, "rotating invalidates the old key")
    st, _, _ = call("GET", "/api/holdings", auth=r2["token"])
    check(st == 200, "the new key works")
    st, info, _ = call("DELETE", "/api/readtoken", cookie=sid)
    st, _, _ = call("GET", "/api/holdings", auth=r2["token"])
    check(st == 401 and info["exists"] is False, "revoking stops it")

    codes = [call("GET", "/api/holdings", auth="lrt_wrong%d" % i)[0] for i in range(6)]
    check(codes[-1] == 429, "repeated wrong keys are locked out (%s)" % codes)
    st, _, _ = call("POST", "/api/login", {"name": "tester", "password": "password123"})
    check(st == 200, "wrong keys do not lock the owner out of signing in")
    print("all %d checks passed" % ok)
finally:
    srv.terminate()
    srv.wait(5)
    shutil.rmtree(work, ignore_errors=True)
