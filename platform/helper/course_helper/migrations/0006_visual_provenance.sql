CREATE TABLE network_visual_candidates(
    candidate_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK(provider = 'wikimedia-commons'),
    provider_page_id INTEGER NOT NULL CHECK(provider_page_id > 0),
    query_digest TEXT NOT NULL CHECK(length(query_digest) = 64),
    metadata_digest TEXT NOT NULL CHECK(length(metadata_digest) = 64),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE network_visual_acquisitions(
    acquisition_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES network_visual_candidates(candidate_id),
    visual_version_id TEXT NOT NULL UNIQUE REFERENCES visuals(version_id),
    artifact_id TEXT NOT NULL REFERENCES artifact_metadata(artifact_id),
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    provider TEXT NOT NULL CHECK(provider = 'wikimedia-commons'),
    provider_page_id INTEGER NOT NULL CHECK(provider_page_id > 0),
    metadata_digest TEXT NOT NULL CHECK(length(metadata_digest) = 64),
    provider_sha1 TEXT NOT NULL CHECK(length(provider_sha1) = 40),
    license_id TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, metadata_digest)
);

CREATE TABLE network_visual_verifications(
    visual_version_id TEXT PRIMARY KEY REFERENCES visuals(version_id),
    status TEXT NOT NULL CHECK(status IN (
        'verified', 'expired', 'removed', 'license-changed', 'content-changed'
    )),
    metadata_digest TEXT NOT NULL CHECK(length(metadata_digest) = 64),
    provider_sha1 TEXT NOT NULL CHECK(length(provider_sha1) = 40),
    license_id TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX network_visual_candidate_expiry_idx
    ON network_visual_candidates(expires_at, candidate_id);
CREATE INDEX network_visual_acquisition_artifact_idx
    ON network_visual_acquisitions(artifact_id, visual_version_id);
CREATE INDEX network_visual_verification_expiry_idx
    ON network_visual_verifications(status, expires_at, visual_version_id);

CREATE TRIGGER network_visual_candidates_immutable_update
BEFORE UPDATE ON network_visual_candidates
BEGIN SELECT RAISE(ABORT, 'immutable network visual candidate'); END;
CREATE TRIGGER network_visual_candidates_immutable_delete
BEFORE DELETE ON network_visual_candidates
BEGIN SELECT RAISE(ABORT, 'immutable network visual candidate'); END;
CREATE TRIGGER network_visual_acquisitions_immutable_update
BEFORE UPDATE ON network_visual_acquisitions
BEGIN SELECT RAISE(ABORT, 'immutable network visual acquisition'); END;
CREATE TRIGGER network_visual_acquisitions_immutable_delete
BEFORE DELETE ON network_visual_acquisitions
BEGIN SELECT RAISE(ABORT, 'immutable network visual acquisition'); END;
