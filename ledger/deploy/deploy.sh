#!/usr/bin/env bash
# Deploys the ledger (app.py and static/) at the current commit to the server, in one go:
#   1. refuses unless the working copy of ledger/ is committed, and unless every file on the server is exactly the
#      one from the last deployed commit (recorded on the server), so nothing changed by hand is overwritten
#   2. backs up the running files and the database on the server
#   3. installs, restarts, and checks the service answers; if it does not, puts the previous files back
#
# Usage: ledger/deploy/deploy.sh            (LEDGER_HOST overrides root@49.232.210.129)
#        ledger/deploy/deploy.sh --from SHA  the first time, naming the commit the server runs now
set -euo pipefail

HOST="${LEDGER_HOST:-root@49.232.210.129}"
DIR=/opt/stock-ledger
cd "$(dirname "$0")/.."                                     # ledger/
REPO_PATH=ledger
COMMIT=$(git rev-parse HEAD)
FROM=""
if [ "${1:-}" = "--from" ]; then FROM="$2"; fi

say() { printf '%s\n' "$*"; }
fail() { printf '部署中止：%s\n' "$*" >&2; exit 1; }

git diff --quiet HEAD -- . || fail "ledger/ 里有没提交的改动，先提交再部署"
mapfile -t FILES < <(git ls-files app.py static)
[ "${#FILES[@]}" -gt 1 ] || fail "没有找到要部署的文件"

# 1. what the server runs must be a commit we know, file for file
DEPLOYED=$(ssh -o BatchMode=yes "$HOST" "cat $DIR/.deployed 2>/dev/null || true")
BASE="${DEPLOYED:-$FROM}"
[ -n "$BASE" ] || fail "服务器上没有部署记录（$DIR/.deployed）；第一次请用 --from <服务器当前对应的提交>"
git cat-file -e "$BASE^{commit}" 2>/dev/null || fail "本地仓库里没有提交 $BASE"
REMOTE=$(ssh -o BatchMode=yes "$HOST" "cd $DIR && for f in ${FILES[*]}; do if [ -f \"\$f\" ]; then sha256sum \"\$f\"; else echo \"missing  \$f\"; fi; done")
DRIFT=""
for f in "${FILES[@]}"; do
  want=$(git show "$BASE:$REPO_PATH/$f" 2>/dev/null | sha256sum | cut -d' ' -f1 || true)
  have=$(printf '%s\n' "$REMOTE" | awk -v f="$f" '$2 == f { print $1 }')
  if git cat-file -e "$BASE:$REPO_PATH/$f" 2>/dev/null; then
    [ "$want" = "$have" ] || DRIFT="$DRIFT $f"
  fi
done
[ -z "$DRIFT" ] || fail "服务器上这些文件和提交 ${BASE:0:7} 不一致，可能被手动改过：$DRIFT"
CHANGED=$(git diff --name-only "$BASE" HEAD -- app.py static | sed "s#^$REPO_PATH/##")
[ -n "$CHANGED" ] || { say "服务器已经是 ${COMMIT:0:7}，没有要部署的改动。"; exit 0; }
say "从 ${BASE:0:7} 部署到 ${COMMIT:0:7}，改动：$(echo $CHANGED)"

# 2-3. upload, back up, install, restart, check; roll back on failure
TS=$(date +%Y%m%d-%H%M%S)
PKG="/tmp/ledger-$TS.tgz"
git -C "$(git rev-parse --show-toplevel)" archive --format=tar.gz -o "${TMPDIR:-/tmp}/ledger-$TS.tgz" "HEAD:$REPO_PATH" app.py static
scp -q "${TMPDIR:-/tmp}/ledger-$TS.tgz" "$HOST:$PKG"
rm -f "${TMPDIR:-/tmp}/ledger-$TS.tgz"
RESTART=0
case " $CHANGED " in *" app.py "*) RESTART=1 ;; esac

ssh -o BatchMode=yes "$HOST" "TS=$TS PKG=$PKG DIR=$DIR COMMIT=$COMMIT RESTART=$RESTART bash -s" <<'REMOTE'
set -euo pipefail
cd "$DIR"
BK="page-backups/deploy-$TS"
mkdir -p "$BK/static" /tmp/ledger-stage-$TS
cp -p app.py "$BK/"; cp -p static/* "$BK/static/"
runuser -u stockledger -- python3 app.py backup >/dev/null
tar -xzf "$PKG" -C /tmp/ledger-stage-$TS
python3 -m py_compile /tmp/ledger-stage-$TS/app.py
install -o root -g root -m 644 /tmp/ledger-stage-$TS/app.py app.py
for f in /tmp/ledger-stage-$TS/static/*; do install -o root -g root -m 644 "$f" "static/$(basename "$f")"; done
rm -rf /tmp/ledger-stage-$TS "$PKG"
[ "$RESTART" = 1 ] && systemctl restart stock-ledger
healthy() {
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5090/api/auth)" = 200 ] || continue
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5090/assets/calc.js)" = 200 ] && return 0
  done
  return 1
}
if healthy; then
  echo "$COMMIT" > .deployed
  echo "$TS $COMMIT ok" >> deploy.log
  echo "已部署并通过检查；上一版保存在 $DIR/$BK"
else
  cp -p "$BK/app.py" app.py; cp -p "$BK"/static/* static/
  systemctl restart stock-ledger
  echo "$TS $COMMIT rolled-back" >> deploy.log
  echo "检查没通过，已回到上一版（$BK）" >&2
  exit 1
fi
REMOTE
