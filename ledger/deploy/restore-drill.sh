#!/usr/bin/env bash
# Restore drill: proves the off-site backups can bring the ledger back. On the off-site machine (the old server) it restores the
# newest backup into a throwaway copy of the ledger (the code at this commit), starts it, checks it answers, and
# compares its record counts with the live ledger's; then removes the copy and records the outcome on the live
# server (data/drill.json), which the ledger shows under 系统状态.
#
# Usage: ledger/deploy/restore-drill.sh     (LEDGER_HOST and LEDGER_OFFSITE override the two machines)
set -euo pipefail
LIVE="${LEDGER_HOST:-ubuntu@42.194.244.252}"      # the live ledger (new server): a sudo user, the data is not world-readable
OFFSITE="${LEDGER_OFFSITE:-root@49.232.210.129}"  # the off-site backups (old server)
sudo_for() { case "$1" in root@*) echo "" ;; *) echo "sudo" ;; esac; }
LIVE_SUDO=$(sudo_for "$LIVE"); OFF_SUDO=$(sudo_for "$OFFSITE")
cd "$(dirname "$0")/.."
COUNT='import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro&immutable=1" % sys.argv[1], uri=True)
rows = dict(con.execute("SELECT coll, COUNT(*) FROM docs WHERE uid > 0 GROUP BY coll").fetchall())
rows["accounts"] = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
print(" ".join("%s=%d" % kv for kv in sorted(rows.items())))'

git -C "$(git rev-parse --show-toplevel)" archive --format=tar HEAD:ledger app.py static |
  ssh -o BatchMode=yes "$OFFSITE" 'rm -rf /tmp/ledger-drill && mkdir -p /tmp/ledger-drill/data && tar -x -C /tmp/ledger-drill'
result=$(ssh -o BatchMode=yes "$OFFSITE" "OFF_SUDO='$OFF_SUDO' COUNT='$COUNT' bash -s" <<'REMOTE'
set -euo pipefail
cd /tmp/ledger-drill
latest=$($OFF_SUDO ls -1 /var/backups/ledger/ | grep '^ledger-.*\.db$' | sort | tail -n 1)
$OFF_SUDO cat "/var/backups/ledger/$latest" > data/ledger.db
counts=$(python3 -c "$COUNT" data/ledger.db)
python3 app.py serve --port 5199 >serve.log 2>&1 &
pid=$!
code=000
for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 1; code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5199/api/auth || true); [ "$code" = 200 ] && break; done
asset=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5199/assets/calc.js || true)
kill "$pid" 2>/dev/null || true
cd / && rm -rf /tmp/ledger-drill
echo "$latest|$code|$asset|$counts"
REMOTE
)
IFS='|' read -r backup code asset counts <<<"$result"
live=$(ssh -o BatchMode=yes "$LIVE" "$LIVE_SUDO python3 -c '$COUNT' /opt/stock-ledger/data/ledger.db")
echo "恢复的备份：$backup"
echo "启动后：/api/auth $code，页面资源 $asset"
echo "备份里的记录：$counts"
echo "线上现在：    $live"
ok=false
[ "$code" = 200 ] && [ "$asset" = 200 ] && [ -n "$counts" ] && ok=true
ssh -o BatchMode=yes "$LIVE" "$LIVE_SUDO python3 - /opt/stock-ledger/data/drill.json '$ok' '$backup' '$counts' '$live' && $LIVE_SUDO chown stockledger:stockledger /opt/stock-ledger/data/drill.json" <<'PY'
import datetime, json, os, sys
path, ok, backup, counts, live = sys.argv[1:]
with open(path + ".tmp", "w", encoding="utf-8") as f:
    json.dump({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "ok": ok == "true",
               "backup": backup, "restored": counts, "live": live}, f, ensure_ascii=False)
os.replace(path + ".tmp", path)
PY
[ "$ok" = true ] && echo "演练通过：备份可以恢复成能用的账本。" || { echo "演练没通过，见上面的输出。" >&2; exit 1; }
