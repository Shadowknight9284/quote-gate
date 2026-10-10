# Published evidence

Index of evidence files referenced by [`docs/submission-lucera.md`](../submission-lucera.md).
Provenance: **human-run** = executed and reported by a team member; **agent-run** = executed by the
AI coding agent. Verify with `sha256sum -c` from the repository root (paste the block below).

| File | Provenance | What |
|---|---|---|
| `docs/demo-transcript-human-e9a71db.txt` | human-run, revision `e9a71db`, 2026-10-10T21:06Z | full demo transcript (tests, stale filtering, 1,217-fill reconciliation, experiment table) |
| `docs/demo-transcript.txt` | agent-run, revision `1032c4d`, 2026-10-10T17:52Z | historical demo transcript, unchanged |
| `docs/evidence/synthetic-matrix/source-20261010T165552Z.json` | agent-run | paper's source matrix |
| `docs/evidence/synthetic-matrix/reproduced-20261010T210644Z.json` | human-run | reproduction (differs only in `run_id`) |
| `docs/evidence/synthetic-matrix/README.md` | — | provenance and the exact comparison command |
| `paper/fig_paired.dat` | agent-run | Figure 1 data (identical to the source matrix's figure data) |
| `docs/evidence/perf-exec-rtt-20261010T212410Z/environment.md` | agent-run | sanitized environment and workload |
| `docs/evidence/perf-exec-rtt-20261010T212410Z/run1.json` … `run5.json` | agent-run | complete per-run reports |

Not published: raw Coinbase market data, private host logs, the local environment record with
session-specific identifiers, and database credentials.

## SHA-256
```
edd9a35ce4801b8c302810716eb0e0f5ac7b1dbab28d19bffba53c233e72a90b  docs/demo-transcript-human-e9a71db.txt
895e153d5e9144b5b7a70e4fe82a4c81113c55bf525e3f941e202ea7b08cda0d  docs/demo-transcript.txt
4d6b67d3e8017b9d89ed34c2bd711592468f2800aeb6a18a7c54f795ad80492e  docs/evidence/synthetic-matrix/source-20261010T165552Z.json
12e20d29d8630a3ecb8166c43046b3198063455d89ed28fdb7a755523e19234c  docs/evidence/synthetic-matrix/reproduced-20261010T210644Z.json
dcd4ef3cf6bd979ac12f89b3a4684b5f9286ce6c9c05fa19972bedfec390d707  docs/evidence/synthetic-matrix/README.md
5ec27562ccf7d83b0b205599de3dc06852fd897062ac69aa4bdcfde18c9af4bd  paper/fig_paired.dat
65ca8b175f5bca639224d4a954ef473b56c884315c192a0e0527172e4c9c43ec  docs/evidence/perf-exec-rtt-20261010T212410Z/environment.md
f2b7a1300632a5eb7c0df77ab8b2d587d9893056d1c78ffebcbc89aed7f9bc48  docs/evidence/perf-exec-rtt-20261010T212410Z/run1.json
8c1a431ea5e0e2e46e7f51b2d7688faac7f63a1190b7c79e314ce13b42ae49d7  docs/evidence/perf-exec-rtt-20261010T212410Z/run2.json
8f17a7efdb24aa8629442675ac2845c22bc3ca74c84e4204d3166ac1768f5447  docs/evidence/perf-exec-rtt-20261010T212410Z/run3.json
82674f4654c167d02463ad24575afdcfffb618b6d5b0476bc1ee041f5c787d23  docs/evidence/perf-exec-rtt-20261010T212410Z/run4.json
3e3f36249dfab40422ec8c903c095f1b4a94543db010dc64100428f134c22984  docs/evidence/perf-exec-rtt-20261010T212410Z/run5.json
```
