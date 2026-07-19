CREATE TABLE artifact_metadata(
    artifact_id TEXT PRIMARY KEY,
    artifact_digest TEXT NOT NULL UNIQUE CHECK(length(artifact_digest) = 64),
    byte_size INTEGER NOT NULL CHECK(byte_size > 0),
    media_type TEXT NOT NULL CHECK(media_type IN (
        'image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'
    )),
    width INTEGER NOT NULL CHECK(width > 0),
    height INTEGER NOT NULL CHECK(height > 0),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(artifact_id = 'artifact-' || artifact_digest)
);

CREATE TABLE source_visual_artifacts(
    materialization_id TEXT PRIMARY KEY,
    visual_version_id TEXT NOT NULL UNIQUE REFERENCES visuals(version_id),
    artifact_id TEXT NOT NULL REFERENCES artifact_metadata(artifact_id),
    source_version_id TEXT NOT NULL REFERENCES sources(version_id),
    source_content_digest TEXT NOT NULL CHECK(length(source_content_digest) = 64),
    visual_content_digest TEXT NOT NULL CHECK(length(visual_content_digest) = 64),
    slide_number INTEGER NOT NULL CHECK(slide_number > 0),
    relationship_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_version_id, slide_number, relationship_id, visual_version_id)
);

CREATE INDEX source_visual_artifact_source_idx
    ON source_visual_artifacts(source_version_id, slide_number, relationship_id);
CREATE INDEX source_visual_artifact_artifact_idx
    ON source_visual_artifacts(artifact_id, visual_version_id);

CREATE TRIGGER artifact_metadata_immutable_update
BEFORE UPDATE ON artifact_metadata
BEGIN SELECT RAISE(ABORT, 'immutable artifact metadata'); END;
CREATE TRIGGER artifact_metadata_immutable_delete
BEFORE DELETE ON artifact_metadata
BEGIN SELECT RAISE(ABORT, 'immutable artifact metadata'); END;
CREATE TRIGGER source_visual_artifacts_immutable_update
BEFORE UPDATE ON source_visual_artifacts
BEGIN SELECT RAISE(ABORT, 'immutable source visual artifact'); END;
CREATE TRIGGER source_visual_artifacts_immutable_delete
BEFORE DELETE ON source_visual_artifacts
BEGIN SELECT RAISE(ABORT, 'immutable source visual artifact'); END;
