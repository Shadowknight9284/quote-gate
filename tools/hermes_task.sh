#!/usr/bin/env bash
# Manual bounded delegation round trip to Hermes on the delegation VM (host details: gitignored docs/private/remote.env).
#   tools/hermes_task.sh send   TASK_ID BUNDLE_DIR   # create remote workspace + baseline commit
#   tools/hermes_task.sh run    TASK_ID [MAX_TURNS] [BUDGET_S]
#   tools/hermes_task.sh fetch  TASK_ID OUT_DIR      # patch (incl. new files) + report, never applied
# Limitation: --in is NOT a sandbox; Hermes runs as the unrestricted 'pranav' account.
set -euo pipefail

[ -f docs/private/remote.env ] && . docs/private/remote.env
HOST=${QG_REMOTE_SSH:?set QG_REMOTE_SSH}
SSH=(ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=5 -i "${QG_SSH_KEY:?set QG_SSH_KEY}" "$HOST")
HERMES=${QG_HERMES_BIN:?set QG_HERMES_BIN}

cmd=$1; task=$2
[[ $task =~ ^[A-Za-z0-9_-]+$ ]] || { echo "bad task id" >&2; exit 2; }
ws="qg-tasks/$task"

case $cmd in
  send)
    bundle=$3
    "${SSH[@]}" "test ! -e $ws && mkdir -p $ws"
    tar -C "$bundle" -cf - . | "${SSH[@]}" "tar -C $ws -xf - && cd $ws && git init -q &&
      git -c user.name=qg-baseline -c user.email=baseline@invalid add -A &&
      git -c user.name=qg-baseline -c user.email=baseline@invalid commit -qm baseline && git log --oneline"
    ;;
  run)
    turns=${3:-20}; budget=${4:-120}
    # External hard timeout above the agent's own run budget.
    "${SSH[@]}" "cd $ws && timeout -k 10 $((budget + 60)) $HERMES chat -Q --ignore-rules \
      --source tool --max-turns $turns --run-budget $budget --format text \
      --in \$HOME/$ws --query-file TASK.md > hermes-output.txt 2>&1; echo exit=\$?"
    ;;
  fetch)
    out=$3; mkdir -p "$out"
    "${SSH[@]}" "cd $ws && git add -N . && git diff --stat -- . ':!hermes-output.txt' ':!TASK.md' >&2 &&
      git diff --binary -- . ':!hermes-output.txt' ':!TASK.md'" > "$out/$task.patch"
    "${SSH[@]}" "cat $ws/hermes-output.txt" > "$out/$task.report.txt"
    echo "changed paths:"; grep '^diff --git' "$out/$task.patch" | awk '{print $4}' | sed 's#^b/##'
    ;;
  *) echo "unknown command" >&2; exit 2 ;;
esac
