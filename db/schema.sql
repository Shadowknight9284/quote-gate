-- QuoteGate experiment evidence store (Tiger Data / TimescaleDB).
-- Additive only: creates schema `quotegate` and its own objects if absent.
-- Never drops, truncates, or alters objects outside this schema.
-- Time semantics: observed_at is DERIVED as anchor_wall_ns + (mono - anchor_mono_ns) from one
-- paired wall/monotonic reading taken when the observer started (see docs/evidence.md).
-- Observations with no recorded anchor (v1 artifacts) go to *_untimed tables; no time is invented.

CREATE SCHEMA IF NOT EXISTS quotegate;

CREATE TABLE IF NOT EXISTS quotegate.experiment_runs (
    run_id              text PRIMARY KEY,
    label               text NOT NULL,
    git                 text,
    python              text,
    operation           text,
    target              text,
    placement           text NOT NULL CHECK (placement IN ('loopback', 'cross_vm_lan', 'unknown')),
    rate_rps            double precision NOT NULL,
    duration_s          double precision NOT NULL,
    warmup_s            double precision NOT NULL,
    connections         integer NOT NULL,
    trials              integer NOT NULL,
    run_started_utc     timestamptz,          -- NULL when not recorded
    run_started_source  text,                 -- 'meta.run_started_utc_ns' | 'run_id_utc_second' | NULL
    source_artifact     text NOT NULL,        -- repo-relative artifact directory
    imported_at         timestamptz NOT NULL DEFAULT now()
);

-- Per-observation latency with derived UTC time (v2+ artifacts).
CREATE TABLE IF NOT EXISTS quotegate.latency_samples (
    observed_at                 timestamptz NOT NULL,
    sample_key                  text NOT NULL,
    run_id                      text NOT NULL REFERENCES quotegate.experiment_runs(run_id),
    trial                       integer,
    kind                        text NOT NULL CHECK (kind IN ('route', 'feed_rtt')),
    venue_id                    text,          -- feed_rtt: venue; route: chosen venue or NULL
    value_ns                    bigint NOT NULL, -- route: send_to_completion; feed_rtt: rtt
    schedule_to_completion_ns   bigint,
    schedule_to_send_ns         bigint,
    outcome                     text,
    quote_age_ns                bigint,
    warmup                      boolean,
    observed_mono_ns            bigint NOT NULL,
    observer                    text NOT NULL,
    PRIMARY KEY (sample_key, observed_at)
);

-- Same observations when the artifact recorded no wall-clock anchor (v1). No observed_at.
CREATE TABLE IF NOT EXISTS quotegate.latency_samples_untimed (
    sample_key                  text PRIMARY KEY,
    run_id                      text NOT NULL REFERENCES quotegate.experiment_runs(run_id),
    trial                       integer,
    kind                        text NOT NULL CHECK (kind IN ('route', 'feed_rtt')),
    venue_id                    text,
    value_ns                    bigint NOT NULL,
    schedule_to_completion_ns   bigint,
    schedule_to_send_ns         bigint,
    outcome                     text,
    quote_age_ns                bigint,
    warmup                      boolean,
    observed_mono_ns            bigint NOT NULL,
    observer                    text
);

CREATE TABLE IF NOT EXISTS quotegate.venue_events (
    observed_at         timestamptz NOT NULL,
    event_key           text NOT NULL,
    run_id              text NOT NULL REFERENCES quotegate.experiment_runs(run_id),
    venue_id            text NOT NULL,
    kind                text NOT NULL,        -- connect | disconnect | status
    detail              jsonb NOT NULL,
    observed_mono_ns    bigint NOT NULL,
    observer            text NOT NULL,
    PRIMARY KEY (event_key, observed_at)
);

-- Summary statistics as recorded by the run (used where no per-sample data exists).
CREATE TABLE IF NOT EXISTS quotegate.run_metric_summaries (
    metric_key  text PRIMARY KEY,
    run_id      text NOT NULL REFERENCES quotegate.experiment_runs(run_id),
    trial       integer NOT NULL,
    source      text NOT NULL,                -- client | aggregator
    metric      text NOT NULL,
    stat        text NOT NULL,
    value_ns    bigint NOT NULL
);

SELECT create_hypertable('quotegate.latency_samples', by_range('observed_at', INTERVAL '1 hour'),
                         if_not_exists => TRUE);
SELECT create_hypertable('quotegate.venue_events', by_range('observed_at', INTERVAL '1 day'),
                         if_not_exists => TRUE);
