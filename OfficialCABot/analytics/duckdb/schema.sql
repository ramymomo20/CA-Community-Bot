CREATE SCHEMA IF NOT EXISTS cache_meta;
CREATE SCHEMA IF NOT EXISTS core_cache;
CREATE SCHEMA IF NOT EXISTS hub_cache;

CREATE TABLE IF NOT EXISTS cache_meta.sync_state (
    source_name VARCHAR PRIMARY KEY,
    target_schema VARCHAR NOT NULL,
    target_table VARCHAR NOT NULL,
    sync_mode VARCHAR NOT NULL,
    watermark_column VARCHAR NULL,
    watermark_value TIMESTAMP NULL,
    last_row_count BIGINT NOT NULL DEFAULT 0,
    last_synced_at TIMESTAMP NULL,
    status VARCHAR NOT NULL DEFAULT 'idle',
    last_error VARCHAR NULL
);
