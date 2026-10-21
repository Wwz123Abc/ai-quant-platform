"""End-to-end test of the ledger's quant-platform check (/api/sso) on a throwaway copy with an empty database."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent  # the ledger folder
work = Path(tempfile.mkdtemp(prefix="ledger-sso-"))
shutil.copy(SRC / "app.py", work / "app.py")
shutil.copytree(SRC / "static", work / "static")
PORT = 5098
BASE = "http://127.0.0.1:%d" % PORT
ok = 0


def start():
    return subprocess.Popen([sys.executable, "app.py", "serve", "--port", str(PORT)], cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def wait():
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/auth", timeout=1)
            return
        except Exception:
            time.sleep(0.2)


def cli(*args):
    return subprocess.run([sys.executable, "app.py", *args], cwd=work, capture_output=True, text=True, encoding="utf-8", env={**__import__("os").environ, "PYTHONUTF8": "1"})


def call(method, path, body=None, cookie=None):
    h = {"Content-Type": "application/json", "X-Requested-With": "ledger"}
    if cookie:
        h["Cookie"] = "sid=" + cookie
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


def check(cond, what):
    global ok
    assert cond, what
    ok += 1
    print("ok  ", what)


srv = start()
try:
    wait()
    sids = {}
    for name in ("老板", "guest"):
        st, _, h = call("POST", "/api/register", {"name": name, "password": "password123", "agree": True})
        sids[name] = h["Set-Cookie"].split("sid=")[1].split(";")[0]
    check(call("GET", "/api/sso")[0] == 401, "signed out: 401")
    check(call("GET", "/api/sso", cookie=sids["老板"])[0] == 403, "no owner set: 403")
    check(call("GET", "/api/auth", cookie=sids["老板"])[1]["platform"] is False, "auth says no platform yet")

    out = cli("platform-owner", "老板")
    check(out.returncode == 0 and "老板" in out.stdout, "CLI sets the owner")
    check(cli("platform-owner", "nobody").returncode != 0, "CLI rejects an unknown account")
    st, _, h = call("GET", "/api/sso", cookie=sids["老板"])
    check(st == 200 and urllib.parse.unquote(h["X-Ledger-User"]) == "老板", "owner: 200 with the user header")
    check(call("GET", "/api/sso", cookie=sids["guest"])[0] == 403, "other account: 403")
    check(call("GET", "/api/auth", cookie=sids["老板"])[1]["platform"] is True, "auth shows the platform to the owner")
    check(call("GET", "/api/auth", cookie=sids["guest"])[1]["platform"] is False, "and not to others")
    check(call("GET", "/api/sso", cookie="forged")[0] == 401, "forged cookie: 401")

    page = urllib.request.urlopen(urllib.request.Request(BASE + "/", headers={"Cookie": "sid=" + sids["老板"]})).read().decode()
    check('id="labLink" hidden' in page, "the nav link starts hidden")

    cli("platform-owner", "off")
    check(call("GET", "/api/sso", cookie=sids["老板"])[0] == 403, "owner off: 403")
    cli("platform-owner", "guest")
    call("POST", "/api/logout", {}, sids["guest"])
    check(call("GET", "/api/sso", cookie=sids["guest"])[0] == 401, "after sign-out: 401")
    srv.terminate(); srv.wait()
    cli("delete-user", "guest")
    srv = start(); wait()
    st, _, h = call("POST", "/api/register", {"name": "guest", "password": "password123", "agree": True})
    new = h["Set-Cookie"].split("sid=")[1].split(";")[0]
    check(call("GET", "/api/sso", cookie=new)[0] == 403, "deleting the owner clears it; a new account of the same name is not the owner")
    env = work / "q.env"
    env.write_text("FRED_API_KEY=x\nLEDGER_TOKEN=old\n", encoding="utf-8")
    out = cli("read-key", "老板", str(env))
    lines = env.read_text(encoding="utf-8").splitlines()
    key = [ln for ln in lines if ln.startswith("LEDGER_TOKEN=")][0][13:]
    check(out.returncode == 0 and key.startswith("lrt_") and key not in out.stdout and "FRED_API_KEY=x" in lines and len(lines) == 2,
          "read-key writes the key into the file only")
    r = urllib.request.urlopen(urllib.request.Request(BASE + "/api/holdings", headers={"Authorization": "Bearer " + key}))
    check(r.status == 200, "and the written key reads holdings")
    print("%d checks passed" % ok)
finally:
    srv.terminate()
    srv.wait()
    shutil.rmtree(work, ignore_errors=True)
