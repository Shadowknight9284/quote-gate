-- QuoteGate evidence queries. Percentiles use percentile_disc, which equals the nearest-rank
-- method used by quote_gate.metrics.percentile; local summaries round to 0.1 µs, so DB values
-- (ns) must match within 50 ns.

-- name: feed_rtt_by_placement
-- Application-level feed connection RTT (aggregator-host ping -> pong), per sample; NOT quote age.
SELECT r.placement, s.venue_id, count(*) AS n,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY s.value_ns) AS p50_ns,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY s.value_ns) AS p95_ns,
       percentile_disc(0.99) WITHIN GROUP (ORDER BY s.value_ns) AS p99_ns
FROM quotegate.latency_samples s JOIN quotegate.experiment_runs r USING (run_id)
WHERE s.kind = 'feed_rtt'
GROUP BY r.placement, s.venue_id ORDER BY r.placement, s.venue_id;

-- name: feed_rtt_summaries_by_placement
-- Historic runs that kept only summary statistics of feed RTT (no per-sample rows).
SELECT r.placement, r.run_id, m.metric, m.stat, m.value_ns
FROM quotegate.run_metric_summaries m JOIN quotegate.experiment_runs r USING (run_id)
WHERE m.metric LIKE 'feed_rtt_ns:%' AND m.trial = r.trials - 1
ORDER BY r.placement, r.run_id, m.metric, m.stat;

-- name: route_rtt_by_run_trial
-- Client-observed route lookup round trip (send -> reply), steady state only. Never contacts a venue.
WITH s AS (
    SELECT run_id, trial, value_ns, schedule_to_completion_ns, warmup FROM quotegate.latency_samples WHERE kind = 'route'
    UNION ALL
    SELECT run_id, trial, value_ns, schedule_to_completion_ns, warmup FROM quotegate.latency_samples_untimed WHERE kind = 'route'
)
SELECT r.placement, s.run_id, s.trial, count(*) AS n,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY s.value_ns) AS send_to_completion_p50_ns,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY s.value_ns) AS send_to_completion_p95_ns,
       percentile_disc(0.99) WITHIN GROUP (ORDER BY s.value_ns) AS send_to_completion_p99_ns,
       percentile_disc(0.99) WITHIN GROUP (ORDER BY s.schedule_to_completion_ns) AS schedule_to_completion_p99_ns
FROM s JOIN quotegate.experiment_runs r USING (run_id)
WHERE NOT s.warmup
GROUP BY r.placement, s.run_id, s.trial ORDER BY s.run_id, s.trial;

-- name: venue_event_timeline
SELECT e.observed_at, e.run_id, e.venue_id, e.kind, e.detail->>'from' AS from_status,
       e.detail->>'to' AS to_status, (e.detail->>'epoch')::int AS epoch,
       (e.detail->>'detect_delay_ns')::bigint AS stale_detect_delay_ns, e.observer
FROM quotegate.venue_events e
ORDER BY e.run_id, e.observed_at, e.event_key;

-- name: route_rtt_per_second
-- Time-bucketed route RTT for runs with derived UTC timestamps.
SELECT time_bucket(INTERVAL '1 second', observed_at) AS bucket, run_id, count(*) AS n,
       percentile_disc(0.99) WITHIN GROUP (ORDER BY value_ns) AS send_to_completion_p99_ns,
       count(*) FILTER (WHERE outcome = 'NO_ROUTE') AS no_route
FROM quotegate.latency_samples
WHERE kind = 'route'
GROUP BY bucket, run_id ORDER BY run_id, bucket;

-- name: import_row_counts
SELECT 'experiment_runs' AS t, count(*) FROM quotegate.experiment_runs
UNION ALL SELECT 'latency_samples', count(*) FROM quotegate.latency_samples
UNION ALL SELECT 'latency_samples_untimed', count(*) FROM quotegate.latency_samples_untimed
UNION ALL SELECT 'venue_events', count(*) FROM quotegate.venue_events
UNION ALL SELECT 'run_metric_summaries', count(*) FROM quotegate.run_metric_summaries;
