CREATE TABLE card_lifecycle_events(
    event_id TEXT PRIMARY KEY,
    card_version_id TEXT NOT NULL REFERENCES cards(version_id),
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'backfill', 'register', 'publish', 'supersede', 'archive', 'suspend', 'reinstate'
    )),
    request_digest TEXT NOT NULL,
    status_before TEXT NOT NULL CHECK(status_before IN (
        'draft', 'review', 'published', 'superseded', 'archived'
    )),
    status_after TEXT NOT NULL CHECK(status_after IN (
        'draft', 'review', 'published', 'superseded', 'archived'
    )),
    suspended_before INTEGER NOT NULL CHECK(suspended_before IN (0, 1)),
    suspended_after INTEGER NOT NULL CHECK(suspended_after IN (0, 1)),
    occurred_at TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(card_version_id, sequence)
);

CREATE TABLE card_lifecycle_current(
    card_version_id TEXT PRIMARY KEY REFERENCES cards(version_id),
    status TEXT NOT NULL CHECK(status IN (
        'draft', 'review', 'published', 'superseded', 'archived'
    )),
    suspended INTEGER NOT NULL CHECK(suspended IN (0, 1)),
    last_sequence INTEGER NOT NULL CHECK(last_sequence >= 1),
    last_event_id TEXT NOT NULL REFERENCES card_lifecycle_events(event_id)
);

CREATE INDEX card_lifecycle_current_status_idx
    ON card_lifecycle_current(status, suspended, card_version_id);
CREATE INDEX card_lifecycle_events_card_idx
    ON card_lifecycle_events(card_version_id, sequence);

INSERT INTO card_lifecycle_events(
    event_id,
    card_version_id,
    sequence,
    event_type,
    request_digest,
    status_before,
    status_after,
    suspended_before,
    suspended_after,
    occurred_at,
    actor_id,
    payload_json
)
SELECT
    'backfill:' || version_id,
    version_id,
    1,
    'backfill',
    content_digest,
    status,
    status,
    0,
    0,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    'course-helper/migration-0002',
    json_object(
        'card_version_id', version_id,
        'event_type', 'backfill',
        'request_digest', content_digest,
        'status_after', status,
        'suspended_after', 0
    )
FROM cards
ORDER BY version_id;

INSERT INTO card_lifecycle_current(
    card_version_id,
    status,
    suspended,
    last_sequence,
    last_event_id
)
SELECT
    version_id,
    status,
    0,
    1,
    'backfill:' || version_id
FROM cards
ORDER BY version_id;

CREATE TRIGGER cards_immutable_lifecycle_columns
BEFORE UPDATE ON cards
BEGIN
    SELECT RAISE(ABORT, 'immutable card bytes; append a lifecycle event');
END;

CREATE TRIGGER card_lifecycle_events_append_only_update
BEFORE UPDATE ON card_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'append-only lifecycle events cannot be updated');
END;

CREATE TRIGGER card_lifecycle_events_append_only_delete
BEFORE DELETE ON card_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'append-only lifecycle events cannot be deleted');
END;
