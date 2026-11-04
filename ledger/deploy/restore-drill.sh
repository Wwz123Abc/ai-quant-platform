#!/usr/bin/env bash
# Restore drill: proves the off-site backups can bring the ledger back. On this computer it restores the newest backup
# (from backup-pull.sh) into a throwaway copy of the ledger (the code at this commit), starts it, checks it answers,
# and compares its record counts with the live ledger's; then removes the copy and records the outcome on the live
# server (data/drill.json), which the ledger shows under 系统状态.
#
# Usage: ledger/deploy/restore-drill.sh     (LEDGER_HOST, LEDGER_BACKUP_DIR override the server and the folder)
set -euo pipefail
LIVE="${LEDGER_HOST:-wwz}"                          # ssh alias of the live ledger (new server, a sudo user)
BACKUPS="${LEDGER_BACKUP_DIR:-$HOME/Desktop/stock-projects/ledger-backups}"
PORT=5199
cd "$(dirname "$0")/.."                             # ledger/
COUNT='import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro&immutable=1" % sys.argv[1], uri=True)
rows = dict(con.execute("SELECT coll, COUNT(*) FROM docs WHERE uid > 0 GROUP BY coll").fetchall())
rows["accounts"] = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
print(" ".join("%s=%d" % kv for kv in sorted(rows.items())))'

latest=$(ls -1 "$BACKUPS"/ledger-*.db 2>/dev/null | sort | tail -n 1 || true)
[ -n "$latest" ] || { echo "本机没有备份：先运行 ledger/deploy/backup-pull.sh" >&2; exit 1; }
backup=$(basename "$latest")

work=$(mktemp -d)
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; rm -rf "$work"; }
trap cleanup EXIT
mkdir -p "$work/data"
cp app.py "$work/app.py" && cp -r static "$work/static"
cp "$latest" "$work/data/ledger.db"
cd "$work"
counts=$(python -c "$COUNT" data/ledger.db)
python app.py serve --port $PORT >serve.log 2>&1 &
pid=$!
code=000
for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 1; code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/auth || true); [ "$code" = 200 ] && break; done
asset=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/assets/calc.js || true)
kill "$pid" 2>/dev/null || true; pid=""

live=$(ssh -o BatchMode=yes "$LIVE" "sudo python3 -c '$COUNT' /opt/stock-ledger/data/ledger.db")
echo "恢复的备份：$backup"
echo "启动后：/api/auth $code，页面资源 $asset"
echo "备份里的记录：$counts"
echo "线上现在：    $live"
ok=false
[ "$code" = 200 ] && [ "$asset" = 200 ] && [ -n "$counts" ] && ok=true
ssh -o BatchMode=yes "$LIVE" "sudo python3 - /opt/stock-ledger/data/drill.json '$ok' '$backup' '$counts' '$live' && sudo chown stockledger:stockledger /opt/stock-ledger/data/drill.json" <<'PY'
import datetime, json, os, sys
path, ok, backup, counts, live = sys.argv[1:]
with open(path + ".tmp", "w", encoding="utf-8") as f:
    json.dump({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "ok": ok == "true",
               "backup": backup, "restored": counts, "live": live}, f, ensure_ascii=False)
os.replace(path + ".tmp", path)
PY
[ "$ok" = true ] && echo "演练通过：备份可以恢复成能用的账本。" || { echo "演练没通过，见上面的输出。" >&2; exit 1; }
