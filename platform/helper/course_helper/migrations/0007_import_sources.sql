CREATE TABLE governed_uploads(
    upload_id TEXT PRIMARY KEY,
    session_digest TEXT NOT NULL CHECK(length(session_digest) = 64),
    safe_name TEXT NOT NULL CHECK(length(safe_name) BETWEEN 1 AND 240),
    source_kind TEXT NOT NULL CHECK(source_kind IN (
        'pptx', 'markdown', 'csv', 'parquet', 'xls', 'xlsx'
    )),
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 20971520),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    state TEXT NOT NULL CHECK(state IN (
        'available', 'leased', 'promoted', 'cancelled', 'expired'
    )),
    expires_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE import_leases(
    import_id TEXT PRIMARY KEY,
    upload_id TEXT NOT NULL UNIQUE REFERENCES governed_uploads(upload_id),
    operation_id TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
    actor_id TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('human', 'service', 'model', 'system')),
    session_digest TEXT NOT NULL CHECK(length(session_digest) = 64),
    state TEXT NOT NULL CHECK(state IN ('active', 'promoted', 'cancelled', 'failed')),
    source_version_id TEXT REFERENCES sources(version_id),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE governed_source_blobs(
    source_version_id TEXT PRIMARY KEY REFERENCES sources(version_id),
    source_logical_id TEXT NOT NULL,
    upload_id TEXT NOT NULL UNIQUE REFERENCES governed_uploads(upload_id),
    blob_digest TEXT NOT NULL CHECK(length(blob_digest) = 64),
    safe_name TEXT NOT NULL CHECK(length(safe_name) BETWEEN 1 AND 240),
    source_kind TEXT NOT NULL CHECK(source_kind IN (
        'pptx', 'markdown', 'csv', 'parquet', 'xls', 'xlsx'
    )),
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 20971520),
    status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX governed_upload_expiry_idx
    ON governed_uploads(state, expires_at, upload_id);
CREATE INDEX import_lease_state_idx
    ON import_leases(state, updated_at, import_id);
CREATE INDEX governed_source_inventory_idx
    ON governed_source_blobs(status, created_at, source_version_id);
CREATE INDEX governed_source_digest_idx
    ON governed_source_blobs(blob_digest, source_version_id);

CREATE TRIGGER governed_source_blobs_immutable_update
BEFORE UPDATE ON governed_source_blobs
BEGIN SELECT RAISE(ABORT, 'immutable governed source blob'); END;
CREATE TRIGGER governed_source_blobs_immutable_delete
BEFORE DELETE ON governed_source_blobs
BEGIN SELECT RAISE(ABORT, 'immutable governed source blob'); END;

