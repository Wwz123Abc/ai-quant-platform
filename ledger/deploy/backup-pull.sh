#!/usr/bin/env bash
# Off-site backup of the ledger onto this computer: pulls the newest database backup from the server, checks it is an
# intact SQLite database, keeps the newest 60 in the backup folder, and records the outcome on the server
# (data/offsite.json), which the ledger shows under 系统状态.
#
# Usage: ledger/deploy/backup-pull.sh     (LEDGER_HOST and LEDGER_BACKUP_DIR override the server and the folder)
set -uo pipefail
HOST="${LEDGER_HOST:-wwz}"   # ssh alias of the new server (ubuntu, with its own key)
DEST="${LEDGER_BACKUP_DIR:-$HOME/Desktop/stock-projects/ledger-backups}"
mkdir -p "$DEST" && cd "$DEST" || exit 1

record() {  # ok|failed, detail, file
  ssh -o BatchMode=yes "$HOST" "sudo -u stockledger python3 - /opt/stock-ledger/data/offsite.json '$1' '$2' '$3'" <<'PY' || true
import datetime, json, os, sys
path, state, detail, name = sys.argv[1:]
with open(path + ".tmp", "w", encoding="utf-8") as f:
    json.dump({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "ok": state == "ok",
               "file": name, "target": "本机", "detail": detail}, f, ensure_ascii=False)
os.replace(path + ".tmp", path)
PY
}

latest=$(ssh -o BatchMode=yes "$HOST" "sudo ls -1 /opt/stock-ledger/data/backups | grep '^ledger-.*\.db\$' | sort | tail -n 1")
[ -n "$latest" ] || { record failed "服务器上没有找到备份" none; echo "服务器上没有找到备份" >&2; exit 1; }
if [ ! -f "$latest" ]; then
  ssh -o BatchMode=yes "$HOST" "sudo cat /opt/stock-ledger/data/backups/$latest" > ".incoming" || { rm -f .incoming; record failed "下载失败" "$latest"; exit 1; }
  check=$(python -c "import sqlite3; print(sqlite3.connect('file:.incoming?mode=ro&immutable=1', uri=True).execute('PRAGMA integrity_check').fetchone()[0])" 2>&1)
  if [ "$check" != ok ]; then rm -f .incoming; record failed "备份检查不通过：$check" "$latest"; echo "备份检查不通过：$check" >&2; exit 1; fi
  mv .incoming "$latest"
fi
ls -1 ledger-*.db | sort | head -n -60 | xargs -r rm -f
size=$(stat -c %s "$latest" 2>/dev/null || wc -c < "$latest")
record ok "已拉取到本机 $DEST（$size 字节，检查通过）" "$latest"
echo "ok $latest -> $DEST"
