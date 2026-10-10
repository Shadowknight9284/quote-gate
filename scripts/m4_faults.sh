#!/usr/bin/env bash
# M4: fault injection during scheduled load (all local, loopback).
# Labeled fault actions (timestamps in faults.log, UTC + monotonic not available from bash: UTC only):
#   N_FREEZE x (SIGSTOP venue A for FREEZE_MS, then SIGCONT, wait 0.5 s)
#   then kill venue B, wait 1.5 s, restart venue B.
set -euo pipefail
LABEL=${LABEL:-m4-faults}; RATE=${RATE:-2000}; DUR=${DUR:-30}; N_FREEZE=${N_FREEZE:-20}; FREEZE_MS=${FREEZE_MS:-300}
OUT=artifacts/m4/$(date -u +%Y%m%dT%H%M%SZ)-$LABEL; mkdir -p "$OUT"
python3 -m quote_gate.cli venue --id A --port 19101 --seed 1 --stats-every 3600 > "$OUT/venueA.log" 2>&1 & VA=$!
python3 -m quote_gate.cli venue --id B --port 19102 --seed 2 --start-bid 10002 --stats-every 3600 > "$OUT/venueB.log" 2>&1 & VB=$!
sleep 0.5
python3 -m quote_gate.cli aggregator --venue A=127.0.0.1:19101 --venue B=127.0.0.1:19102 --port 19200 \
  --stale-ms 100 --stats-every 3600 --observer aggregator@vm1 --artifact-dir "$OUT/aggregator" > "$OUT/aggregator.log" 2>&1 & AG=$!
sleep 1.5
python3 -m quote_gate.bench --port 19200 --rate "$RATE" --duration "$DUR" --warmup 2 --connections 8 --trials 1 \
  --label "$LABEL" --observer bench@vm1 --out "$OUT/bench" > "$OUT/bench.jsonl" & BE=$!
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ) $*" >> "$OUT/faults.log"; }
sleep 3
for i in $(seq "$N_FREEZE"); do
  log "fault=freeze venue=A action=SIGSTOP"; kill -STOP $VA; sleep "$(echo "$FREEZE_MS/1000" | bc -l)"
  kill -CONT $VA; log "fault=freeze venue=A action=SIGCONT"; sleep 0.5
done
log "fault=kill venue=B"; kill $VB; wait $VB 2>/dev/null || true; sleep 1.5
python3 -m quote_gate.cli venue --id B --port 19102 --seed 2 --start-bid 10002 --stats-every 3600 >> "$OUT/venueB.log" 2>&1 & VB=$!
log "fault=restart venue=B"
wait $BE
python3 -m quote_gate.cli stats --port 19200 > "$OUT/stats.json"
kill $AG; wait $AG 2>/dev/null || true
kill $VA $VB; wait 2>/dev/null || true
echo "$OUT"
