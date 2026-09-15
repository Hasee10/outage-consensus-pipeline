-- Outage Intelligence Pipeline — schema.
-- Applied by migrations/migrate.py against the target database (DB_NAME).
-- Idempotent: safe to run more than once.

-- 1. Raw payloads exactly as fetched from each source (audit trail).
--    JSON sources are stored verbatim; HTML sources as the extracted
--    micro-data plus a fingerprint (byte size + sha256) of the page.
CREATE TABLE IF NOT EXISTS raw_ingests (
    id           BIGSERIAL PRIMARY KEY,
    region       TEXT        NOT NULL,
    source_id    TEXT        NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload      JSONB,
    success      BOOLEAN     NOT NULL,
    error_detail JSONB
);
CREATE INDEX IF NOT EXISTS idx_raw_ingests_region_src   ON raw_ingests (region, source_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_ingests_fetched_at   ON raw_ingests (fetched_at);
CREATE INDEX IF NOT EXISTS idx_raw_ingests_payload_gin  ON raw_ingests USING GIN (payload);

-- 2. Consensus snapshots: one row per ingestion pass per region. The API
--    serves the newest row for the requested region.
CREATE TABLE IF NOT EXISTS consensus_snapshots (
    id                       BIGSERIAL PRIMARY KEY,
    region                   TEXT        NOT NULL,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of                    TIMESTAMPTZ NOT NULL,
    total_customers_affected INTEGER     NOT NULL,
    confidence               DOUBLE PRECISION,
    quality_score            DOUBLE PRECISION,
    verified                 BOOLEAN     NOT NULL DEFAULT FALSE,
    -- [{area, customers_affected, customers_tracked, eta, confidence, ...}]
    areas                    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    -- [{utility, customers_affected, customers_tracked, confidence, ...}]
    utilities                JSONB       NOT NULL DEFAULT '[]'::jsonb,
    -- [{source_id, publisher, retrieved_at}] — stored here so provenance
    -- survives TTL purging of raw rows.
    sources_used             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    warnings                 JSONB       NOT NULL DEFAULT '[]'::jsonb,
    stale                    BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_region_time   ON consensus_snapshots (region, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_areas_gin     ON consensus_snapshots USING GIN (areas);
CREATE INDEX IF NOT EXISTS idx_snapshots_utilities_gin ON consensus_snapshots USING GIN (utilities);

-- 3. Hourly rollups: fine-grained snapshots older than SNAPSHOT_TTL are
--    summarised here (per region/area/hour) before being purged.
CREATE TABLE IF NOT EXISTS area_rollups (
    region        TEXT        NOT NULL,
    area          TEXT        NOT NULL,
    hour_start    TIMESTAMPTZ NOT NULL,
    samples       INTEGER     NOT NULL,
    max_affected  INTEGER     NOT NULL,
    avg_affected  DOUBLE PRECISION NOT NULL,
    min_confidence DOUBLE PRECISION,
    PRIMARY KEY (region, area, hour_start)
);

-- 4. Explicit, structured ingestion errors (no silent fallbacks).
CREATE TABLE IF NOT EXISTS ingestion_errors (
    id            BIGSERIAL PRIMARY KEY,
    region        TEXT        NOT NULL,
    source_id     TEXT        NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    error_type    TEXT        NOT NULL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_errors_region ON ingestion_errors (region, occurred_at DESC);

-- 5. Per-request audit log for the serving API.
CREATE TABLE IF NOT EXISTS request_log (
    id           BIGSERIAL PRIMARY KEY,
    endpoint     TEXT        NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    latency_ms   INTEGER,
    status_code  INTEGER
);
