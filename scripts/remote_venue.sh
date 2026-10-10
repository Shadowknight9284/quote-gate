#!/usr/bin/env bash
# Venue B on the second VM (M3). Only touches processes recorded in this task's pid file.
#   scripts/remote_venue.sh deploy   TASK        # new ~/qg-venue/TASK, runtime source from HEAD only
#   scripts/remote_venue.sh start    TASK [TIMEOUT_S]
#   scripts/remote_venue.sh identity TASK
#   scripts/remote_venue.sh signal   TASK STOP|CONT
#   scripts/remote_venue.sh stop     TASK
set -euo pipefail

# Host details live in the gitignored docs/private/remote.env (QG_REMOTE_SSH, QG_REMOTE_BIND).
[ -f docs/private/remote.env ] && . docs/private/remote.env
HOST=${QG_REMOTE_SSH:?set QG_REMOTE_SSH}
BIND=${QG_REMOTE_BIND:?set QG_REMOTE_BIND}
PORT=19102
SSH=(ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=5 -i "${QG_SSH_KEY:?set QG_SSH_KEY}" "$HOST")
RUNTIME=(quote_gate/__init__.py quote_gate/aggregator.py quote_gate/book.py quote_gate/cli.py
         quote_gate/metrics.py quote_gate/protocol.py quote_gate/router.py quote_gate/synthetic.py
         quote_gate/venue.py)

cmd=$1; task=$2
[[ $task =~ ^[A-Za-z0-9_-]+$ ]] || { echo "bad task id" >&2; exit 2; }
dir="qg-venue/$task"
local_dir="artifacts/m3/$task"

# Resolve and verify the python child of our recorded `timeout` process.
CHILD='pid=$(cat venue.pid); child=$(pgrep -P "$pid" -f "quote_gate.cli venue" || true);
  [ -n "$child" ] && [ "$(readlink /proc/$child/cwd)" = "$PWD" ] || { echo "no verified venue process" >&2; exit 3; }'

case $cmd in
  deploy)
    mkdir -p "$local_dir"
    sha=$(git rev-parse HEAD)
    git diff --quiet HEAD -- "${RUNTIME[@]}" || { echo "runtime files differ from HEAD" >&2; exit 2; }
    "${SSH[@]}" "test ! -e $dir && mkdir -p $dir"
    git archive --format=tar HEAD "${RUNTIME[@]}" | "${SSH[@]}" "tar -x -C $dir"
    { echo "task: $task"; echo "source_commit: $sha"; echo "remote_dir: ~/$dir";
      echo "bind: $BIND:$PORT"; echo "files (sha256):"; sha256sum "${RUNTIME[@]}"; } > "$local_dir/manifest.txt"
    "${SSH[@]}" "cd $dir && sha256sum ${RUNTIME[*]}" > "$local_dir/remote_sha256.txt"
    diff <(sha256sum "${RUNTIME[@]}") "$local_dir/remote_sha256.txt" && echo "deployed $sha; checksums match"
    ;;
  start)
    t=${3:-1800}
    "${SSH[@]}" "cd $dir && if ss -ltn 'sport = :$PORT' | tail -n +2 | grep -q .; then echo 'port $PORT busy' >&2; exit 4; fi
      setsid nohup timeout -k 5 $t python3 -m quote_gate.cli venue --id B --host $BIND --port $PORT \
        --seed 2 --start-bid 10002 --rate 1000 --stats-every 3600 >> venue.log 2>&1 < /dev/null &
      echo \$! > venue.pid; sleep 1; cat venue.pid"
    ;;
  identity)
    "${SSH[@]}" "cd $dir && $CHILD; hostname; ps -o pid,ppid,etimes,args -p \$pid,\$child;
      echo cwd=\$(readlink /proc/\$child/cwd); ss -ltnp 'sport = :$PORT' | tail -n +2"
    ;;
  signal)
    [[ $3 == STOP || $3 == CONT ]] || exit 2
    "${SSH[@]}" "cd $dir && $CHILD; kill -$3 \$child && echo signalled \$child"
    ;;
  stop)
    "${SSH[@]}" "cd $dir && pid=\$(cat venue.pid); $CHILD; kill -CONT \$child; kill -TERM \$pid;
      for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 \$pid 2>/dev/null || break; sleep 0.5; done;
      kill -0 \$pid 2>/dev/null && { echo 'still running' >&2; exit 5; };
      ss -ltn 'sport = :$PORT' | tail -n +2 | grep -q . && echo 'port still bound' >&2 || echo 'stopped; port free'"
    mkdir -p "$local_dir"
    "${SSH[@]}" "tail -c 20000 $dir/venue.log" > "$local_dir/venue.log"
    ;;
  *) echo "unknown command" >&2; exit 2 ;;
esac
