-- CVE Intelligence Pipeline — schema.
-- Applied by migrations/migrate.py against the target database (DB_NAME).
-- Idempotent: safe to run more than once.

-- 1. Raw payloads exactly as fetched from each source (audit trail).
CREATE TABLE IF NOT EXISTS raw_ingests (
    id           BIGSERIAL PRIMARY KEY,
    cve_id       TEXT        NOT NULL,
    source_id    TEXT        NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload      JSONB,
    success      BOOLEAN     NOT NULL,
    error_detail JSONB
);
CREATE INDEX IF NOT EXISTS idx_raw_ingests_cve_id      ON raw_ingests (cve_id);
CREATE INDEX IF NOT EXISTS idx_raw_ingests_cve_source  ON raw_ingests (cve_id, source_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_ingests_fetched_at  ON raw_ingests (fetched_at);
CREATE INDEX IF NOT EXISTS idx_raw_ingests_payload_gin ON raw_ingests USING GIN (payload);

-- 2. Computed single-source-of-truth consensus (one row per CVE).
CREATE TABLE IF NOT EXISTS consensus (
    cve_id                TEXT PRIMARY KEY,
    severity              TEXT,
    cvss                  DOUBLE PRECISION,
    affected_products     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    published_at          TIMESTAMPTZ,
    consensus_computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confidence            DOUBLE PRECISION,
    quality_score         DOUBLE PRECISION,
    -- List of {source_id, publisher, retrieved_at} objects for the sources that
    -- contributed. Stored here (not derived from raw_ingests) so provenance
    -- survives TTL purging of raw rows.
    sources_used          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    stale                 BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_consensus_cve_id            ON consensus (cve_id);
CREATE INDEX IF NOT EXISTS idx_consensus_products_gin      ON consensus USING GIN (affected_products);

-- 3. Explicit, structured ingestion errors (no silent fallbacks).
CREATE TABLE IF NOT EXISTS ingestion_errors (
    id            BIGSERIAL PRIMARY KEY,
    cve_id        TEXT        NOT NULL,
    source_id     TEXT        NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    error_type    TEXT        NOT NULL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_errors_cve_id ON ingestion_errors (cve_id);

-- 4. Per-request audit log for the serving API.
CREATE TABLE IF NOT EXISTS request_log (
    id           BIGSERIAL PRIMARY KEY,
    endpoint     TEXT        NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    latency_ms   INTEGER,
    status_code  INTEGER
);
