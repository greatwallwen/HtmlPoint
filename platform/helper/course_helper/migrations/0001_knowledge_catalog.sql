PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations(
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE sources(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE chunks(
    chunk_id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL REFERENCES sources(version_id),
    ordinal INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE visuals(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE datasets(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE cards(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE lineage(
    edge_id TEXT PRIMARY KEY,
    from_version_id TEXT NOT NULL,
    to_version_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    evidence_id TEXT NOT NULL
);

CREATE TABLE evidence(
    evidence_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE review_tasks(
    task_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject_version_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE tag_vocabularies(
    version_id TEXT PRIMARY KEY,
    content_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE tag_values(
    vocabulary_version_id TEXT NOT NULL REFERENCES tag_vocabularies(version_id),
    tag_id TEXT NOT NULL,
    dimension_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(vocabulary_version_id, tag_id)
);

CREATE TABLE card_tags(
    card_version_id TEXT NOT NULL REFERENCES cards(version_id),
    vocabulary_version_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY(card_version_id, vocabulary_version_id, tag_id),
    FOREIGN KEY(vocabulary_version_id, tag_id)
        REFERENCES tag_values(vocabulary_version_id, tag_id)
);

CREATE UNIQUE INDEX sources_logical_revision_uq
    ON sources(logical_id, revision);
CREATE UNIQUE INDEX visuals_logical_revision_uq
    ON visuals(logical_id, revision);
CREATE UNIQUE INDEX datasets_logical_revision_uq
    ON datasets(logical_id, revision);
CREATE UNIQUE INDEX cards_logical_revision_uq
    ON cards(logical_id, revision);

CREATE VIRTUAL TABLE card_fts USING fts5(
    version_id UNINDEXED,
    title,
    learning_objective,
    body,
    chunk_text,
    projected_text,
    tokenize='trigram'
);
