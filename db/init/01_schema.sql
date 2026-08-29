CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE TABLE IF NOT EXISTS telemetry_data (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    channel INT NOT NULL,
    temperature DOUBLE PRECISION NOT NULL,
    job_id TEXT,
    PRIMARY KEY (time, device_id, channel)
);
SELECT create_hypertable('telemetry_data', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS device_channel_metadata (
    device_id VARCHAR(64) NOT NULL,
    job_id VARCHAR(64) NOT NULL,
    friendly_name VARCHAR(64) NOT NULL,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    location VARCHAR(64) NOT NULL DEFAULT 'Extern',
    interface VARCHAR(64) NOT NULL DEFAULT 'Lieferschein',
    custom_string VARCHAR(255),
    recipe_id VARCHAR(64),
    cement_name VARCHAR(64) NOT NULL DEFAULT 'cem100',
    cement_id VARCHAR(64),
    PRIMARY KEY (device_id, job_id)
);

CREATE TABLE IF NOT EXISTS device_channel_metadata_history (
    device_id VARCHAR(64) NOT NULL,
    job_id VARCHAR(64) NOT NULL,
    friendly_name VARCHAR(64) NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS analyzer_state (
    device_id TEXT NOT NULL,
    channel INT NOT NULL,
    job_id TEXT NOT NULL,
    turnaround_sent BOOLEAN DEFAULT FALSE,
    trigger_sent BOOLEAN DEFAULT FALSE,
    export_30_sent BOOLEAN DEFAULT FALSE,
    export_120_sent BOOLEAN DEFAULT FALSE,
    t_min_temp DOUBLE PRECISION,
    t_min_time TIMESTAMPTZ,
    t_ab_time TIMESTAMPTZ,
    t_ab_temp DOUBLE PRECISION,
    last_evaluated TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ DEFAULT NOW(),
    force_export BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (device_id, channel, job_id)
);

CREATE TABLE IF NOT EXISTS alerts_history (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    device_id TEXT NOT NULL,
    channel INT NOT NULL,
    event_type TEXT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    acknowledged BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS device_status (
    device_id TEXT PRIMARY KEY,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pending_count INT DEFAULT 0
);