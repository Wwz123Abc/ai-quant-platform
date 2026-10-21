#!/usr/bin/env bash
# Sends the newest ledger backup to the off-site machine (run by root's cron after the daily backup) and records the
# outcome in /opt/stock-ledger/data/offsite.json, which the ledger shows on its home page. Installed as
# /usr/local/bin/ledger-backup-push. The key /root/.ssh/ledger_backup can only run the receiving command there.
set -uo pipefail
DIR=/opt/stock-ledger/data
TARGET="${LEDGER_BACKUP_TARGET:-ledgerbak@42.194.244.252}"
STATUS="$DIR/offsite.json"
latest=$(ls -1 "$DIR"/backups/ledger-*.db 2>/dev/null | sort | tail -n 1)

record() {  # ok/failed, detail
  python3 - "$STATUS" "$1" "$2" "$(basename "${latest:-none}")" "$TARGET" <<'PY'
import datetime, json, os, sys
path, state, detail, name, target = sys.argv[1:]
host = target.split("@")[-1]
with open(path + ".tmp", "w", encoding="utf-8") as f:
    json.dump({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "ok": state == "ok",
               "file": name, "target": host, "detail": detail}, f, ensure_ascii=False)
os.replace(path + ".tmp", path)
PY
  chown stockledger:stockledger "$STATUS" 2>/dev/null || true
}

if [ -z "$latest" ]; then record failed "没有找到本地备份"; exit 1; fi
local_check=$(python3 -c "import sqlite3,sys; print(sqlite3.connect('file:%s?mode=ro' % sys.argv[1], uri=True).execute('PRAGMA integrity_check').fetchone()[0])" "$latest" 2>&1)
if [ "$local_check" != ok ]; then record failed "本地备份检查不通过：$local_check"; exit 1; fi
err=$(mktemp)
answer=$(ssh -T -i /root/.ssh/ledger_backup -o BatchMode=yes -o ConnectTimeout=20 "$TARGET" < "$latest" 2>"$err")
if [ "${answer%% *}" = ok ]; then record ok "$answer"; rm -f "$err"; else record failed "${answer:-$(head -c 300 "$err")}"; rm -f "$err"; exit 1; fi
