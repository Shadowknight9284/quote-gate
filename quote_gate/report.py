"""Render a benchmark summary.json (see quote_gate.bench) as Markdown. Contract: docs/contracts/T2-report.md.

  python3 -m quote_gate.report artifacts/bench/<run_id>/summary.json > report.md
"""
from __future__ import annotations

import json
import sys

HEADER = ("| trial | offered | completed | not_sent_backlog | steady_samples | throughput rps | "
          "send-to-completion p50/p95/p99 µs | schedule-to-completion p50/p95/p99 µs | generator-delay p99 µs | errors |")
NOTES = (
    "- Latency is measured on the client host only (monotonic clock); "
    "no cross-host one-way latency is claimed.",
    "- schedule-to-completion includes generator delay and connection wait; send-to-completion is the client-observed round trip, not server service time.",
    "- * marks p99 computed from fewer than 1000 samples (not meaningful).",
)


def _mark(metric: dict) -> str:
    return "" if metric.get("p99_meaningful", True) else " *"


def _lat_cell(metric: dict | None) -> str:
    if metric is None:
        return "n/a"
    return " / ".join(str(metric.get(k, "n/a")) for k in ("p50", "p95", "p99")) + _mark(metric)


def _p99_cell(metric: dict | None) -> str:
    return "n/a" if metric is None else str(metric.get("p99", "n/a")) + _mark(metric)


def render_markdown(summary: dict) -> str:
    if "meta" not in summary or "trials" not in summary or not isinstance(summary["trials"], list):
        raise ValueError("summary must contain 'meta' and a list 'trials'")
    m = summary["meta"]
    g = lambda k: m.get(k, "n/a")  # noqa: E731
    lines = [f"# Benchmark: {g('label')}", "",
             f"- run_id: {g('run_id')}", f"- git: {g('git')}", f"- python: {g('python')}",
             f"- offered rate: {g('rate')} req/s",
             f"- duration: {g('duration_s')} s (warm-up {g('warmup_s')} s excluded)",
             f"- connections: {g('connections')}", f"- trials: {g('trials')}",
             f"- instrumentation: {g('instrumentation')}", "",
             "## Per-trial results", "", HEADER, "|" + "---|" * 10]
    outcome_rows = []
    for t in summary["trials"]:
        outcomes = t.get("outcomes", {})
        errors = sum(v for k, v in outcomes.items() if k == "ERROR" or k.startswith("FAIL:"))
        cells = [t.get(k, "n/a") for k in ("trial", "offered", "completed", "not_sent_backlog",
                                           "steady_samples", "steady_throughput_rps")]
        cells += [_lat_cell(t.get("send_to_completion_us")), _lat_cell(t.get("schedule_to_completion_us")),
                  _p99_cell(t.get("generator_delay_us")), errors]
        lines.append("| " + " | ".join(map(str, cells)) + " |")
        outcome_rows += [(t.get("trial", "n/a"), k, v) for k, v in outcomes.items()]
    lines += ["", "## Routing outcomes", "", "| trial | outcome | count |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in sorted(outcome_rows, key=lambda r: (str(r[0]), r[1]))]
    lines += ["", "## Notes", *NOTES]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        sys.stdout.write(render_markdown(json.load(f)))
