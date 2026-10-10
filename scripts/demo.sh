#!/usr/bin/env bash
# Fallback demo: runs every local demo step and records a transcript (no network beyond 127.0.0.1,
# no database, no private configuration). Usage: scripts/demo.sh [--quick] [OUT_FILE]
set -uo pipefail
QUICK=false
if [[ ${1:-} == --quick ]]; then
  QUICK=true
  shift
fi
if [[ $# -gt 1 || ${1:-} == --* ]]; then
  echo "Usage: scripts/demo.sh [--quick] [OUT_FILE]" >&2
  exit 2
fi
OUT=${1:-artifacts/demo/transcript.txt}
mkdir -p "$(dirname "$OUT")"
exec > >(tee "$OUT") 2>&1
step() { echo; echo "=== $* ==="; }
echo "QuoteGate demo  $(date -u +%Y-%m-%dT%H:%M:%SZ)  revision $(git rev-parse --short HEAD 2>/dev/null || echo n/a)"

step "1. Tests (standard library only)"
python3 -m unittest discover -s tests -t . 2>&1 | tail -3

step "2. Market-data path: two venues + aggregator on loopback"
mkdir -p artifacts/logs
python3 -m quote_gate.cli venue --id A --port 19101 --seed 1 --stats-every 3600 > artifacts/logs/demoA.log 2>&1 & VA=$!
python3 -m quote_gate.cli venue --id B --port 19102 --seed 2 --start-bid 10002 --stats-every 3600 > artifacts/logs/demoB.log 2>&1 & VB=$!
sleep 0.5
python3 -m quote_gate.cli aggregator --venue A=127.0.0.1:19101 --venue B=127.0.0.1:19102 --port 19200 --stale-ms 100 --stats-every 3600 > artifacts/logs/demoAgg.log 2>&1 & AG=$!
sleep 1
echo "-- route lookup (both venues fresh):"
python3 -m quote_gate.cli route --port 19200 --side buy --quantity 1 | tr -d '\n '; echo
kill -STOP $VA; sleep 0.3
echo "-- venue A frozen (SIGSTOP) for 300 ms; route lookup:"
python3 -m quote_gate.cli route --port 19200 --side buy --quantity 1 | tr -d '\n '; echo
kill -CONT $VA; kill $VB; sleep 0.5
echo "-- A resumed, B killed; per-venue status:"
python3 -m quote_gate.cli stats --port 19200 | python3 -c "import json,sys; d=json.load(sys.stdin); print({v: s['status'] for v, s in d['venues'].items()})"
kill $VA $AG 2>/dev/null; wait $VA $VB $AG 2>/dev/null

step "3. Networked order path on loopback (seed 101, 2000 steps)"
python3 -m quote_gate.live --steps 2000 | python3 -c "
import json,sys; d=json.load(sys.stdin)
print({k: d[k] for k in ('sent','filled','rejected','timeouts','unresolved','position','max_abs_position')})
print('exec RTT (ns):', {k: d['exec_rtt_ns'][k] for k in ('count','p50','p99')})
print('ledger vs venue:', d['venue_reconciliation'])"

if [[ $QUICK == false ]]; then
step "4. Deterministic quant experiment (paper numbers)"
python3 -m quote_gate.experiments --out artifacts/demo/qg | python3 -c "
import json,sys
for l in sys.stdin:
    d=json.loads(l)
    if 'cell' in d: print(f\"{d['cell']:20s} fills {d['fills_mean']:7.1f}  pre-fee {d['gross']['mean']:9.1f}  post-fee {d['net']['mean']:9.1f}  hit {d['hit_rate']['rate']}\")"
fi
echo; echo "Done. Transcript: $OUT"
