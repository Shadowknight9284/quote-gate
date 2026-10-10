#!/usr/bin/env bash
# Start two local venues + aggregator on loopback; prints PIDs. Stop with: kill $(cat artifacts/stack.pids)
set -euo pipefail
mkdir -p artifacts/logs
RATE=${RATE:-1000}
python3 -m quote_gate.cli venue --id A --port 19101 --seed 1 --rate "$RATE" --stats-every 3600 > artifacts/logs/venueA.log 2>&1 & echo $! > artifacts/stack.pids
if [ -z "${VENUE_B:-}" ]; then  # VENUE_B=host:port uses an already-running remote venue B
  python3 -m quote_gate.cli venue --id B --port 19102 --seed 2 --rate "$RATE" --start-bid 10002 --stats-every 3600 > artifacts/logs/venueB.log 2>&1 & echo $! >> artifacts/stack.pids
fi
sleep 0.5
python3 -m quote_gate.cli aggregator --venue A=127.0.0.1:19101 --venue B=${VENUE_B:-127.0.0.1:19102} --port 19200 --stale-ms ${STALE_MS:-100} --stats-every 3600 > artifacts/logs/aggregator.log 2>&1 & echo $! >> artifacts/stack.pids
sleep 1
cat artifacts/stack.pids
