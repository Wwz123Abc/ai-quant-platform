#!/usr/bin/env bash
# Receives one ledger database backup on stdin (the off-site machine), checks it is an intact SQLite database,
# keeps it as ledger-YYYYmmdd-HHMMSS.db and keeps the newest 60. Installed as /usr/local/bin/ledger-backup-receive
# and run only through the sending key: authorized_keys of the ledgerbak user has
#   restrict,command="/usr/local/bin/ledger-backup-receive" ssh-ed25519 AAAA... ledger-backup
set -euo pipefail
DIR=/var/backups/ledger
umask 077
tmp=$(mktemp "$DIR/.incoming.XXXXXX")
trap 'rm -f "$tmp"' EXIT
head -c 209715200 > "$tmp"                                   # at most 200 MB
check=$(python3 - "$tmp" <<'PY'
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro&immutable=1" % sys.argv[1], uri=True)  # immutable: no lock files beside it
ok = con.execute("PRAGMA integrity_check").fetchone()[0]
docs = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
print(ok, users, docs)
PY
)
set -- $check
[ "$1" = ok ] || { echo "rejected: integrity check says $check"; exit 1; }
name="ledger-$(date +%Y%m%d-%H%M%S).db"
mv "$tmp" "$DIR/$name"
trap - EXIT
ls -1 "$DIR"/ledger-*.db | sort | head -n -60 | xargs -r rm -f
echo "ok $name $(stat -c %s "$DIR/$name") bytes, $2 accounts, $3 records"
