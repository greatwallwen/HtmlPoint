CREATE TABLE embedding_index_candidates(
    candidate_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL CHECK(policy_id = 'course-studio-rrf-v1'),
    model_manifest_digest TEXT CHECK(
        model_manifest_digest IS NULL OR length(model_manifest_digest) = 64
    ),
    eligible_set_digest TEXT NOT NULL CHECK(length(eligible_set_digest) = 64),
    lifecycle_digest TEXT NOT NULL CHECK(length(lifecycle_digest) = 64),
    outbox_digest TEXT NOT NULL CHECK(length(outbox_digest) = 64),
    outbox_watermark INTEGER NOT NULL CHECK(outbox_watermark >= 0),
    candidate_digest TEXT NOT NULL CHECK(length(candidate_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE embedding_index_fts_rows(
    candidate_id TEXT NOT NULL
        REFERENCES embedding_index_candidates(candidate_id),
    card_version_id TEXT NOT NULL REFERENCES cards(version_id),
    card_content_digest TEXT NOT NULL CHECK(length(card_content_digest) = 64),
    policy_id TEXT NOT NULL CHECK(policy_id = 'course-studio-rrf-v1'),
    model_manifest_digest TEXT CHECK(
        model_manifest_digest IS NULL OR length(model_manifest_digest) = 64
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY(candidate_id, card_version_id)
);

CREATE TABLE card_embedding_rows(
    candidate_id TEXT NOT NULL
        REFERENCES embedding_index_candidates(candidate_id),
    card_version_id TEXT NOT NULL REFERENCES cards(version_id),
    card_content_digest TEXT NOT NULL CHECK(length(card_content_digest) = 64),
    policy_id TEXT NOT NULL CHECK(policy_id = 'course-studio-rrf-v1'),
    model_manifest_digest TEXT NOT NULL CHECK(length(model_manifest_digest) = 64),
    vector_dimension INTEGER NOT NULL CHECK(vector_dimension = 512),
    vector_digest TEXT NOT NULL CHECK(length(vector_digest) = 64),
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(candidate_id, card_version_id)
);

CREATE TABLE embedding_index_snapshots(
    index_snapshot_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE
        REFERENCES embedding_index_candidates(candidate_id),
    status TEXT NOT NULL CHECK(status IN ('ready', 'degraded')),
    retrieval_mode TEXT NOT NULL CHECK(retrieval_mode IN ('hybrid', 'fts-degraded')),
    policy_id TEXT NOT NULL CHECK(policy_id = 'course-studio-rrf-v1'),
    model_manifest_digest TEXT CHECK(
        model_manifest_digest IS NULL OR length(model_manifest_digest) = 64
    ),
    eligible_set_digest TEXT NOT NULL CHECK(length(eligible_set_digest) = 64),
    lifecycle_digest TEXT NOT NULL CHECK(length(lifecycle_digest) = 64),
    outbox_digest TEXT NOT NULL CHECK(length(outbox_digest) = 64),
    outbox_watermark INTEGER NOT NULL CHECK(outbox_watermark >= 0),
    candidate_digest TEXT NOT NULL CHECK(length(candidate_digest) = 64),
    snapshot_digest TEXT NOT NULL UNIQUE CHECK(length(snapshot_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(
        (status = 'ready' AND retrieval_mode = 'hybrid'
         AND model_manifest_digest IS NOT NULL)
        OR
        (status = 'degraded' AND retrieval_mode = 'fts-degraded'
         AND model_manifest_digest IS NULL)
    )
);

CREATE TABLE knowledge_index_outbox_claims(
    claim_id TEXT PRIMARY KEY,
    outbox_id TEXT NOT NULL REFERENCES knowledge_index_outbox(outbox_id),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    worker_id TEXT NOT NULL,
    outbox_content_digest TEXT NOT NULL CHECK(length(outbox_content_digest) = 64),
    lease_expires_at TEXT NOT NULL,
    claim_digest TEXT NOT NULL UNIQUE CHECK(length(claim_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(outbox_id, attempt)
);

CREATE TABLE knowledge_index_outbox_results(
    result_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL UNIQUE
        REFERENCES knowledge_index_outbox_claims(claim_id),
    outbox_id TEXT NOT NULL REFERENCES knowledge_index_outbox(outbox_id),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed', 'lease-expired')),
    index_snapshot_id TEXT REFERENCES embedding_index_snapshots(index_snapshot_id),
    result_digest TEXT NOT NULL UNIQUE CHECK(length(result_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(
        (status = 'succeeded' AND index_snapshot_id IS NOT NULL)
        OR (status IN ('failed', 'lease-expired') AND index_snapshot_id IS NULL)
    )
);

CREATE TABLE knowledge_index_outbox_consumptions(
    outbox_id TEXT PRIMARY KEY REFERENCES knowledge_index_outbox(outbox_id),
    claim_id TEXT NOT NULL UNIQUE REFERENCES knowledge_index_outbox_claims(claim_id),
    result_id TEXT NOT NULL UNIQUE REFERENCES knowledge_index_outbox_results(result_id),
    index_snapshot_id TEXT NOT NULL
        REFERENCES embedding_index_snapshots(index_snapshot_id),
    outbox_content_digest TEXT NOT NULL CHECK(length(outbox_content_digest) = 64),
    created_at TEXT NOT NULL
);

CREATE INDEX embedding_candidate_created_idx
    ON embedding_index_candidates(created_at, candidate_id);
CREATE INDEX embedding_snapshot_created_idx
    ON embedding_index_snapshots(created_at, index_snapshot_id);
CREATE INDEX card_embedding_candidate_idx
    ON card_embedding_rows(candidate_id, card_version_id);
CREATE INDEX embedding_fts_candidate_idx
    ON embedding_index_fts_rows(candidate_id, card_version_id);
CREATE INDEX index_outbox_claim_idx
    ON knowledge_index_outbox_claims(outbox_id, attempt);
CREATE INDEX index_outbox_result_idx
    ON knowledge_index_outbox_results(outbox_id, attempt);

CREATE TRIGGER embedding_index_fts_rows_validate_insert
BEFORE INSERT ON embedding_index_fts_rows
WHEN NOT EXISTS (
        SELECT 1
        FROM embedding_index_candidates AS candidate
        JOIN cards ON cards.version_id = NEW.card_version_id
        JOIN card_lifecycle_current AS lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE candidate.candidate_id = NEW.candidate_id
          AND candidate.policy_id = NEW.policy_id
          AND candidate.model_manifest_digest IS NEW.model_manifest_digest
          AND cards.content_digest = NEW.card_content_digest
          AND lifecycle.status = 'published'
          AND lifecycle.suspended = 0
    )
BEGIN SELECT RAISE(ABORT, 'invalid FTS candidate row'); END;

CREATE TRIGGER card_embedding_rows_validate_insert
BEFORE INSERT ON card_embedding_rows
WHEN NOT EXISTS (
        SELECT 1
        FROM embedding_index_candidates AS candidate
        JOIN cards ON cards.version_id = NEW.card_version_id
        JOIN card_lifecycle_current AS lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE candidate.candidate_id = NEW.candidate_id
          AND candidate.policy_id = NEW.policy_id
          AND candidate.model_manifest_digest = NEW.model_manifest_digest
          AND cards.content_digest = NEW.card_content_digest
          AND lifecycle.status = 'published'
          AND lifecycle.suspended = 0
    )
BEGIN SELECT RAISE(ABORT, 'invalid semantic candidate row'); END;

CREATE TRIGGER embedding_index_fts_rows_reject_sealed_candidate
BEFORE INSERT ON embedding_index_fts_rows
WHEN EXISTS (
    SELECT 1 FROM embedding_index_snapshots
    WHERE candidate_id = NEW.candidate_id
)
BEGIN SELECT RAISE(ABORT, 'sealed embedding candidate'); END;

CREATE TRIGGER card_embedding_rows_reject_sealed_candidate
BEFORE INSERT ON card_embedding_rows
WHEN EXISTS (
    SELECT 1 FROM embedding_index_snapshots
    WHERE candidate_id = NEW.candidate_id
)
BEGIN SELECT RAISE(ABORT, 'sealed embedding candidate'); END;

CREATE TRIGGER embedding_index_snapshots_validate_insert
BEFORE INSERT ON embedding_index_snapshots
WHEN NOT EXISTS (
        SELECT 1 FROM embedding_index_candidates AS candidate
        WHERE candidate.candidate_id = NEW.candidate_id
          AND candidate.policy_id = NEW.policy_id
          AND candidate.model_manifest_digest IS NEW.model_manifest_digest
          AND candidate.eligible_set_digest = NEW.eligible_set_digest
          AND candidate.lifecycle_digest = NEW.lifecycle_digest
          AND candidate.outbox_digest = NEW.outbox_digest
          AND candidate.outbox_watermark = NEW.outbox_watermark
          AND candidate.candidate_digest = NEW.candidate_digest
    )
BEGIN SELECT RAISE(ABORT, 'snapshot seal does not match candidate'); END;

CREATE TRIGGER embedding_index_snapshots_complete_insert
BEFORE INSERT ON embedding_index_snapshots
WHEN
    (SELECT json_type(candidate.payload_json, '$.core.eligible_cards')
     FROM embedding_index_candidates AS candidate
     WHERE candidate.candidate_id = NEW.candidate_id) IS NOT 'array'
    OR
    (SELECT count(*) FROM embedding_index_fts_rows AS fts
     WHERE fts.candidate_id = NEW.candidate_id)
    !=
    (SELECT json_array_length(candidate.payload_json, '$.core.eligible_cards')
     FROM embedding_index_candidates AS candidate
     WHERE candidate.candidate_id = NEW.candidate_id)
    OR EXISTS (
        SELECT 1
        FROM embedding_index_candidates AS candidate,
             json_each(candidate.payload_json, '$.core.eligible_cards') AS eligible
        LEFT JOIN embedding_index_fts_rows AS fts
          ON fts.candidate_id = candidate.candidate_id
         AND fts.card_version_id = json_extract(eligible.value, '$.card_version_id')
         AND fts.card_content_digest = json_extract(eligible.value, '$.card_content_digest')
         AND fts.policy_id = NEW.policy_id
         AND fts.model_manifest_digest IS NEW.model_manifest_digest
        WHERE candidate.candidate_id = NEW.candidate_id
          AND fts.card_version_id IS NULL
    )
    OR (
        NEW.retrieval_mode = 'hybrid'
        AND (
            (SELECT count(*) FROM card_embedding_rows AS semantic
             WHERE semantic.candidate_id = NEW.candidate_id)
            !=
            (SELECT json_array_length(candidate.payload_json, '$.core.eligible_cards')
             FROM embedding_index_candidates AS candidate
             WHERE candidate.candidate_id = NEW.candidate_id)
            OR EXISTS (
                SELECT 1
                FROM embedding_index_candidates AS candidate,
                     json_each(candidate.payload_json, '$.core.eligible_cards') AS eligible
                LEFT JOIN card_embedding_rows AS semantic
                  ON semantic.candidate_id = candidate.candidate_id
                 AND semantic.card_version_id = json_extract(eligible.value, '$.card_version_id')
                 AND semantic.card_content_digest = json_extract(eligible.value, '$.card_content_digest')
                 AND semantic.policy_id = NEW.policy_id
                 AND semantic.model_manifest_digest = NEW.model_manifest_digest
                 AND semantic.vector_dimension = 512
                WHERE candidate.candidate_id = NEW.candidate_id
                  AND semantic.card_version_id IS NULL
            )
        )
    )
    OR (
        NEW.retrieval_mode = 'fts-degraded'
        AND EXISTS (
            SELECT 1 FROM card_embedding_rows AS semantic
            WHERE semantic.candidate_id = NEW.candidate_id
        )
    )
BEGIN SELECT RAISE(ABORT, 'incomplete embedding snapshot row set'); END;

CREATE TRIGGER knowledge_index_outbox_results_validate_insert
BEFORE INSERT ON knowledge_index_outbox_results
WHEN NOT EXISTS (
        SELECT 1
        FROM knowledge_index_outbox_claims AS claim
        WHERE claim.claim_id = NEW.claim_id
          AND claim.outbox_id = NEW.outbox_id
          AND claim.attempt = NEW.attempt
          AND claim.worker_id = NEW.worker_id
    )
BEGIN SELECT RAISE(ABORT, 'index result does not match claim owner'); END;

CREATE TRIGGER knowledge_index_outbox_consumptions_validate_insert
BEFORE INSERT ON knowledge_index_outbox_consumptions
WHEN NOT EXISTS (
        SELECT 1
        FROM knowledge_index_outbox_results AS result
        JOIN knowledge_index_outbox_claims AS claim
          ON claim.claim_id = result.claim_id
        JOIN knowledge_index_outbox AS outbox
          ON outbox.outbox_id = result.outbox_id
        WHERE result.result_id = NEW.result_id
          AND result.status = 'succeeded'
          AND result.index_snapshot_id = NEW.index_snapshot_id
          AND result.outbox_id = NEW.outbox_id
          AND claim.claim_id = NEW.claim_id
          AND outbox.content_digest = NEW.outbox_content_digest
    )
BEGIN SELECT RAISE(ABORT, 'index consumption does not match successful result'); END;

CREATE TRIGGER embedding_index_candidates_immutable_update
BEFORE UPDATE ON embedding_index_candidates
BEGIN SELECT RAISE(ABORT, 'immutable embedding index candidate'); END;
CREATE TRIGGER embedding_index_candidates_immutable_delete
BEFORE DELETE ON embedding_index_candidates
BEGIN SELECT RAISE(ABORT, 'immutable embedding index candidate'); END;
CREATE TRIGGER embedding_index_fts_rows_immutable_update
BEFORE UPDATE ON embedding_index_fts_rows
BEGIN SELECT RAISE(ABORT, 'immutable FTS candidate row'); END;
CREATE TRIGGER embedding_index_fts_rows_immutable_delete
BEFORE DELETE ON embedding_index_fts_rows
BEGIN SELECT RAISE(ABORT, 'immutable FTS candidate row'); END;
CREATE TRIGGER embedding_index_snapshots_immutable_update
BEFORE UPDATE ON embedding_index_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable embedding index snapshot'); END;
CREATE TRIGGER embedding_index_snapshots_immutable_delete
BEFORE DELETE ON embedding_index_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable embedding index snapshot'); END;
CREATE TRIGGER card_embedding_rows_immutable_update
BEFORE UPDATE ON card_embedding_rows
BEGIN SELECT RAISE(ABORT, 'immutable card embedding row'); END;
CREATE TRIGGER card_embedding_rows_immutable_delete
BEFORE DELETE ON card_embedding_rows
BEGIN SELECT RAISE(ABORT, 'immutable card embedding row'); END;
CREATE TRIGGER knowledge_index_outbox_claims_immutable_update
BEFORE UPDATE ON knowledge_index_outbox_claims
BEGIN SELECT RAISE(ABORT, 'append-only index outbox claim'); END;
CREATE TRIGGER knowledge_index_outbox_claims_immutable_delete
BEFORE DELETE ON knowledge_index_outbox_claims
BEGIN SELECT RAISE(ABORT, 'append-only index outbox claim'); END;
CREATE TRIGGER knowledge_index_outbox_results_immutable_update
BEFORE UPDATE ON knowledge_index_outbox_results
BEGIN SELECT RAISE(ABORT, 'append-only index outbox result'); END;
CREATE TRIGGER knowledge_index_outbox_results_immutable_delete
BEFORE DELETE ON knowledge_index_outbox_results
BEGIN SELECT RAISE(ABORT, 'append-only index outbox result'); END;
CREATE TRIGGER knowledge_index_outbox_consumptions_immutable_update
BEFORE UPDATE ON knowledge_index_outbox_consumptions
BEGIN SELECT RAISE(ABORT, 'immutable index outbox consumption'); END;
CREATE TRIGGER knowledge_index_outbox_consumptions_immutable_delete
BEFORE DELETE ON knowledge_index_outbox_consumptions
BEGIN SELECT RAISE(ABORT, 'immutable index outbox consumption'); END;
