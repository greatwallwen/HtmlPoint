CREATE TABLE course_requirements(
    requirement_id TEXT PRIMARY KEY,
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE course_outlines(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    requirement_id TEXT NOT NULL REFERENCES course_requirements(requirement_id),
    domain_digest TEXT NOT NULL CHECK(length(domain_digest) = 64),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(logical_id, revision)
);

CREATE TABLE card_placements(
    placement_id TEXT PRIMARY KEY,
    outline_version_id TEXT NOT NULL REFERENCES course_outlines(version_id),
    card_version_id TEXT NOT NULL REFERENCES cards(version_id),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(outline_version_id, placement_id)
);

CREATE TABLE outline_confirmations(
    confirmation_id TEXT PRIMARY KEY,
    requirement_id TEXT NOT NULL REFERENCES course_requirements(requirement_id),
    outline_version_id TEXT NOT NULL REFERENCES course_outlines(version_id),
    expected_outline_digest TEXT NOT NULL CHECK(length(expected_outline_digest) = 64),
    confirmation_digest TEXT NOT NULL UNIQUE CHECK(length(confirmation_digest) = 64),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(outline_version_id)
);

CREATE TABLE course_versions(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    requirement_id TEXT NOT NULL REFERENCES course_requirements(requirement_id),
    outline_version_id TEXT NOT NULL REFERENCES course_outlines(version_id),
    confirmation_digest TEXT NOT NULL REFERENCES outline_confirmations(confirmation_digest),
    domain_digest TEXT NOT NULL CHECK(length(domain_digest) = 64),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(logical_id, revision)
);

CREATE TABLE slide_decks(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    course_version_id TEXT NOT NULL REFERENCES course_versions(version_id),
    domain_digest TEXT NOT NULL CHECK(length(domain_digest) = 64),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(logical_id, revision)
);

CREATE TABLE runtime_manifests(
    version_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    course_version_id TEXT NOT NULL REFERENCES course_versions(version_id),
    slide_deck_version_id TEXT NOT NULL REFERENCES slide_decks(version_id),
    domain_digest TEXT NOT NULL CHECK(length(domain_digest) = 64),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(logical_id, revision)
);

CREATE TABLE visual_placements(
    placement_id TEXT PRIMARY KEY,
    visual_version_id TEXT NOT NULL REFERENCES visuals(version_id),
    authenticity_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    license_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE review_task_current(
    task_id TEXT PRIMARY KEY REFERENCES review_tasks(task_id),
    category TEXT NOT NULL CHECK(category IN (
        'candidate-card', 'exact-duplicate', 'near-duplicate', 'tag',
        'source-changed', 'course-feedback', 'visual-rights'
    )),
    reason_code TEXT NOT NULL CHECK(reason_code IN (
        'source-changed', 'near-duplicate', 'unknown-tag', 'deprecated-tag',
        'tag-conflict', 'citation-missing', 'visual-rights', 'visual-unverified',
        'dataset-reference', 'sensitive-sample', 'grain-needs-review',
        'provenance', 'manual-review', 'exact-duplicate', 'course-feedback'
    )),
    review_digest TEXT NOT NULL CHECK(length(review_digest) = 64),
    current_status TEXT NOT NULL CHECK(current_status IN ('open', 'resolved', 'dismissed')),
    resolution_id TEXT
);

INSERT INTO review_task_current(
    task_id, category, reason_code, review_digest, current_status, resolution_id
)
SELECT
    task_id,
    CASE
        WHEN kind = 'source-changed' THEN 'source-changed'
        WHEN kind = 'near-duplicate' THEN 'near-duplicate'
        WHEN kind IN ('unknown-tag', 'deprecated-tag', 'tag-conflict') THEN 'tag'
        WHEN kind IN ('visual-rights', 'visual-unverified') THEN 'visual-rights'
        WHEN kind IN (
            'citation-missing', 'dataset-reference', 'sensitive-sample',
            'grain-needs-review', 'provenance', 'manual-review'
        ) THEN 'candidate-card'
        WHEN kind = 'exact-duplicate' THEN 'exact-duplicate'
        WHEN kind = 'course-feedback' THEN 'course-feedback'
        ELSE NULL
    END,
    kind,
    sha256_hex(payload_json),
    status,
    NULL
FROM review_tasks
ORDER BY task_id;

CREATE TABLE review_resolutions(
    resolution_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES review_tasks(task_id),
    decision TEXT NOT NULL CHECK(decision IN ('accept', 'reject', 'dismiss')),
    expected_review_digest TEXT NOT NULL CHECK(length(expected_review_digest) = 64),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE review_resolution_evidence(
    resolution_id TEXT NOT NULL REFERENCES review_resolutions(resolution_id),
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    PRIMARY KEY(resolution_id, evidence_id)
);

CREATE TABLE upgrade_suggestions(
    suggestion_id TEXT PRIMARY KEY,
    review_task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
    current_version_id TEXT NOT NULL,
    candidate_version_id TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE feedback_suggestions(
    suggestion_id TEXT PRIMARY KEY,
    review_task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
    course_version_id TEXT NOT NULL REFERENCES course_versions(version_id),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE upgrade_suggestion_evidence(
    suggestion_id TEXT NOT NULL REFERENCES upgrade_suggestions(suggestion_id),
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    PRIMARY KEY(suggestion_id, evidence_id)
);

CREATE TABLE feedback_suggestion_evidence(
    suggestion_id TEXT NOT NULL REFERENCES feedback_suggestions(suggestion_id),
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    PRIMARY KEY(suggestion_id, evidence_id)
);

CREATE TABLE operation_outcomes(
    operation_id TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
    actor_id TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('human', 'service', 'model', 'system')),
    session_digest TEXT NOT NULL CHECK(length(session_digest) = 64),
    status TEXT NOT NULL CHECK(status IN ('committed', 'rolled-back')),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE operation_item_outcomes(
    operation_id TEXT NOT NULL REFERENCES operation_outcomes(operation_id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    item_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('committed', 'rolled-back')),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(operation_id, ordinal),
    UNIQUE(operation_id, item_id)
);

CREATE TABLE knowledge_index_outbox(
    outbox_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES operation_outcomes(operation_id),
    request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
    card_version_id TEXT NOT NULL REFERENCES cards(version_id),
    action TEXT NOT NULL CHECK(action IN ('upsert', 'delete')),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(operation_id, card_version_id, action)
);

CREATE INDEX review_task_current_category_idx
    ON review_task_current(current_status, category, reason_code, task_id);
CREATE INDEX operation_outbox_operation_idx
    ON knowledge_index_outbox(operation_id, outbox_id);

CREATE TRIGGER review_tasks_known_kind_insert
BEFORE INSERT ON review_tasks
WHEN NEW.kind NOT IN (
    'source-changed', 'near-duplicate', 'unknown-tag', 'deprecated-tag',
    'tag-conflict', 'citation-missing', 'visual-rights', 'visual-unverified',
    'dataset-reference', 'sensitive-sample', 'grain-needs-review',
    'provenance', 'manual-review', 'exact-duplicate', 'course-feedback'
)
BEGIN
    SELECT RAISE(ABORT, 'unknown review task kind');
END;

CREATE TRIGGER review_tasks_open_only_insert
BEFORE INSERT ON review_tasks
WHEN NEW.status != 'open'
  OR json_extract(NEW.payload_json, '$.task_id') IS NOT NEW.task_id
  OR json_extract(NEW.payload_json, '$.kind') IS NOT NEW.kind
  OR json_extract(NEW.payload_json, '$.subject_version_id') IS NOT NEW.subject_version_id
  OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
  OR json_type(NEW.payload_json, '$.resolved_at') IS NOT NULL
  OR json_type(NEW.payload_json, '$.resolved_by') IS NOT NULL
  OR json_type(NEW.payload_json, '$.created_at') IS NOT 'text'
  OR julianday(json_extract(NEW.payload_json, '$.created_at')) IS NULL
  OR NOT COALESCE(
      json_extract(NEW.payload_json, '$.created_at') GLOB '*Z'
      OR json_extract(NEW.payload_json, '$.created_at') GLOB '*+??:??'
      OR json_extract(NEW.payload_json, '$.created_at') GLOB '*-??:??',
      0
  )
  OR NOT EXISTS (
      SELECT 1
      FROM (
          SELECT version_id AS artifact_id FROM sources
          UNION ALL SELECT chunk_id FROM chunks
          UNION ALL SELECT version_id FROM visuals
          UNION ALL SELECT version_id FROM datasets
          UNION ALL SELECT version_id FROM cards
          UNION ALL SELECT version_id FROM tag_vocabularies
          UNION ALL SELECT requirement_id FROM course_requirements
          UNION ALL SELECT version_id FROM course_outlines
          UNION ALL SELECT placement_id FROM card_placements
          UNION ALL SELECT confirmation_id FROM outline_confirmations
          UNION ALL SELECT version_id FROM course_versions
          UNION ALL SELECT version_id FROM slide_decks
          UNION ALL SELECT version_id FROM runtime_manifests
          UNION ALL SELECT placement_id FROM visual_placements
      ) AS artifacts
      WHERE artifacts.artifact_id = NEW.subject_version_id
  )
BEGIN
    SELECT RAISE(ABORT, 'new review task facts are invalid');
END;

CREATE TRIGGER review_tasks_projection_insert
AFTER INSERT ON review_tasks
BEGIN
    INSERT INTO review_task_current(
        task_id, category, reason_code, review_digest, current_status, resolution_id
    ) VALUES (
        NEW.task_id,
        CASE
            WHEN NEW.kind = 'source-changed' THEN 'source-changed'
            WHEN NEW.kind = 'near-duplicate' THEN 'near-duplicate'
            WHEN NEW.kind IN ('unknown-tag', 'deprecated-tag', 'tag-conflict') THEN 'tag'
            WHEN NEW.kind IN ('visual-rights', 'visual-unverified') THEN 'visual-rights'
            WHEN NEW.kind IN (
                'citation-missing', 'dataset-reference', 'sensitive-sample',
                'grain-needs-review', 'provenance', 'manual-review'
            ) THEN 'candidate-card'
            WHEN NEW.kind = 'exact-duplicate' THEN 'exact-duplicate'
            WHEN NEW.kind = 'course-feedback' THEN 'course-feedback'
            ELSE NULL
        END,
        NEW.kind,
        sha256_hex(NEW.payload_json),
        NEW.status,
        NULL
    );
END;

CREATE TRIGGER review_resolutions_validate_insert
BEFORE INSERT ON review_resolutions
WHEN NOT EXISTS (
    SELECT 1
    FROM review_tasks AS task
    JOIN review_task_current AS current ON current.task_id = task.task_id
    WHERE task.task_id = NEW.task_id
      AND current.review_digest = sha256_hex(task.payload_json)
      AND NEW.expected_review_digest = sha256_hex(task.payload_json)
      AND current.current_status = 'open'
      AND current.resolution_id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'review task projection or digest mismatch');
END;

CREATE TRIGGER review_resolutions_envelope_insert
BEFORE INSERT ON review_resolutions
WHEN json_extract(NEW.payload_json, '$.resolution_id') IS NOT NEW.resolution_id
  OR json_extract(NEW.payload_json, '$.task_id') IS NOT NEW.task_id
  OR json_extract(NEW.payload_json, '$.decision') IS NOT NEW.decision
  OR json_extract(NEW.payload_json, '$.expected_review_digest')
      IS NOT NEW.expected_review_digest
  OR NEW.content_digest IS NOT sha256_hex(NEW.payload_json)
  OR json_type(NEW.payload_json, '$.resolved_at') IS NOT 'text'
  OR json_extract(NEW.payload_json, '$.resolved_at') IS NOT NEW.created_at
  OR julianday(json_extract(NEW.payload_json, '$.resolved_at')) IS NULL
  OR NOT COALESCE(
      json_extract(NEW.payload_json, '$.resolved_at') GLOB '*Z'
      OR json_extract(NEW.payload_json, '$.resolved_at') GLOB '*+??:??'
      OR json_extract(NEW.payload_json, '$.resolved_at') GLOB '*-??:??',
      0
  )
BEGIN
    SELECT RAISE(ABORT, 'review resolution envelope mismatch');
END;

CREATE TRIGGER review_resolutions_projection_insert
AFTER INSERT ON review_resolutions
BEGIN
    UPDATE review_task_current
    SET current_status = CASE WHEN NEW.decision = 'dismiss' THEN 'dismissed' ELSE 'resolved' END,
        resolution_id = NEW.resolution_id
    WHERE task_id = NEW.task_id;
END;

CREATE TRIGGER review_resolution_evidence_membership_insert
BEFORE INSERT ON review_resolution_evidence
WHEN NOT EXISTS (
    SELECT 1
    FROM review_resolutions AS resolution,
         json_each(resolution.payload_json, '$.evidence_ids') AS item
    WHERE resolution.resolution_id = NEW.resolution_id
      AND item.value = NEW.evidence_id
)
BEGIN
    SELECT RAISE(ABORT, 'resolution evidence is not declared by immutable payload');
END;

CREATE TRIGGER review_resolutions_evidence_insert
AFTER INSERT ON review_resolutions
BEGIN
    INSERT INTO review_resolution_evidence(resolution_id, evidence_id)
    SELECT NEW.resolution_id, item.value
    FROM json_each(NEW.payload_json, '$.evidence_ids') AS item;
END;

CREATE TRIGGER upgrade_suggestion_evidence_membership_insert
BEFORE INSERT ON upgrade_suggestion_evidence
WHEN NOT EXISTS (
    SELECT 1
    FROM upgrade_suggestions AS suggestion,
         json_each(suggestion.payload_json, '$.evidence_ids') AS item
    WHERE suggestion.suggestion_id = NEW.suggestion_id
      AND item.value = NEW.evidence_id
)
BEGIN
    SELECT RAISE(ABORT, 'upgrade evidence is not declared by immutable payload');
END;

CREATE TRIGGER upgrade_suggestions_envelope_insert
BEFORE INSERT ON upgrade_suggestions
WHEN json_extract(NEW.payload_json, '$.suggestion_id') IS NOT NEW.suggestion_id
  OR json_extract(NEW.payload_json, '$.review_task_id') IS NOT NEW.review_task_id
  OR json_extract(NEW.payload_json, '$.current_version_id') IS NOT NEW.current_version_id
  OR json_extract(NEW.payload_json, '$.candidate_version_id') IS NOT NEW.candidate_version_id
  OR NEW.content_digest IS NOT sha256_hex(NEW.payload_json)
  OR json_type(NEW.payload_json, '$.created_at') IS NOT 'text'
  OR json_extract(NEW.payload_json, '$.created_at') IS NOT NEW.created_at
  OR julianday(json_extract(NEW.payload_json, '$.created_at')) IS NULL
  OR NOT COALESCE(
      json_extract(NEW.payload_json, '$.created_at') GLOB '*Z'
      OR json_extract(NEW.payload_json, '$.created_at') GLOB '*+??:??'
      OR json_extract(NEW.payload_json, '$.created_at') GLOB '*-??:??',
      0
  )
  OR NOT EXISTS (
      SELECT 1 FROM review_tasks
      WHERE task_id = NEW.review_task_id
        AND kind = json_extract(NEW.payload_json, '$.reason_code')
        AND subject_version_id = NEW.candidate_version_id
  )
BEGIN
    SELECT RAISE(ABORT, 'upgrade suggestion envelope mismatch');
END;

CREATE TRIGGER upgrade_suggestions_evidence_insert
AFTER INSERT ON upgrade_suggestions
BEGIN
    INSERT INTO upgrade_suggestion_evidence(suggestion_id, evidence_id)
    SELECT NEW.suggestion_id, item.value
    FROM json_each(NEW.payload_json, '$.evidence_ids') AS item;
END;

CREATE TRIGGER feedback_suggestion_evidence_membership_insert
BEFORE INSERT ON feedback_suggestion_evidence
WHEN NOT EXISTS (
    SELECT 1
    FROM feedback_suggestions AS suggestion,
         json_each(suggestion.payload_json, '$.evidence_ids') AS item
    WHERE suggestion.suggestion_id = NEW.suggestion_id
      AND item.value = NEW.evidence_id
)
BEGIN
    SELECT RAISE(ABORT, 'feedback evidence is not declared by immutable payload');
END;

CREATE TRIGGER feedback_suggestions_envelope_insert
BEFORE INSERT ON feedback_suggestions
WHEN json_extract(NEW.payload_json, '$.suggestion_id') IS NOT NEW.suggestion_id
  OR json_extract(NEW.payload_json, '$.review_task_id') IS NOT NEW.review_task_id
  OR json_extract(NEW.payload_json, '$.course_version_id') IS NOT NEW.course_version_id
  OR NEW.content_digest IS NOT sha256_hex(NEW.payload_json)
  OR json_type(NEW.payload_json, '$.created_at') IS NOT 'text'
  OR json_extract(NEW.payload_json, '$.created_at') IS NOT NEW.created_at
  OR julianday(json_extract(NEW.payload_json, '$.created_at')) IS NULL
  OR NOT COALESCE(
      json_extract(NEW.payload_json, '$.created_at') GLOB '*Z'
      OR json_extract(NEW.payload_json, '$.created_at') GLOB '*+??:??'
      OR json_extract(NEW.payload_json, '$.created_at') GLOB '*-??:??',
      0
  )
  OR NOT EXISTS (
      SELECT 1 FROM review_tasks
      WHERE task_id = NEW.review_task_id
        AND kind = 'course-feedback'
        AND subject_version_id = NEW.course_version_id
  )
BEGIN
    SELECT RAISE(ABORT, 'feedback suggestion envelope mismatch');
END;

CREATE TRIGGER feedback_suggestions_evidence_insert
AFTER INSERT ON feedback_suggestions
BEGIN
    INSERT INTO feedback_suggestion_evidence(suggestion_id, evidence_id)
    SELECT NEW.suggestion_id, item.value
    FROM json_each(NEW.payload_json, '$.evidence_ids') AS item;
END;

CREATE TRIGGER review_tasks_immutable_update
BEFORE UPDATE ON review_tasks BEGIN SELECT RAISE(ABORT, 'immutable review task'); END;
CREATE TRIGGER review_tasks_immutable_delete
BEFORE DELETE ON review_tasks BEGIN SELECT RAISE(ABORT, 'immutable review task'); END;

CREATE TRIGGER course_requirements_immutable_update
BEFORE UPDATE ON course_requirements BEGIN SELECT RAISE(ABORT, 'immutable course requirement'); END;
CREATE TRIGGER course_requirements_immutable_delete
BEFORE DELETE ON course_requirements BEGIN SELECT RAISE(ABORT, 'immutable course requirement'); END;
CREATE TRIGGER course_outlines_immutable_update
BEFORE UPDATE ON course_outlines BEGIN SELECT RAISE(ABORT, 'immutable course outline'); END;
CREATE TRIGGER course_outlines_immutable_delete
BEFORE DELETE ON course_outlines BEGIN SELECT RAISE(ABORT, 'immutable course outline'); END;
CREATE TRIGGER card_placements_immutable_update
BEFORE UPDATE ON card_placements BEGIN SELECT RAISE(ABORT, 'immutable card placement'); END;
CREATE TRIGGER card_placements_immutable_delete
BEFORE DELETE ON card_placements BEGIN SELECT RAISE(ABORT, 'immutable card placement'); END;
CREATE TRIGGER outline_confirmations_immutable_update
BEFORE UPDATE ON outline_confirmations BEGIN SELECT RAISE(ABORT, 'immutable outline confirmation'); END;
CREATE TRIGGER outline_confirmations_immutable_delete
BEFORE DELETE ON outline_confirmations BEGIN SELECT RAISE(ABORT, 'immutable outline confirmation'); END;
CREATE TRIGGER course_versions_immutable_update
BEFORE UPDATE ON course_versions BEGIN SELECT RAISE(ABORT, 'immutable course version'); END;
CREATE TRIGGER course_versions_immutable_delete
BEFORE DELETE ON course_versions BEGIN SELECT RAISE(ABORT, 'immutable course version'); END;
CREATE TRIGGER slide_decks_immutable_update
BEFORE UPDATE ON slide_decks BEGIN SELECT RAISE(ABORT, 'immutable slide deck'); END;
CREATE TRIGGER slide_decks_immutable_delete
BEFORE DELETE ON slide_decks BEGIN SELECT RAISE(ABORT, 'immutable slide deck'); END;
CREATE TRIGGER runtime_manifests_immutable_update
BEFORE UPDATE ON runtime_manifests BEGIN SELECT RAISE(ABORT, 'immutable runtime manifest'); END;
CREATE TRIGGER runtime_manifests_immutable_delete
BEFORE DELETE ON runtime_manifests BEGIN SELECT RAISE(ABORT, 'immutable runtime manifest'); END;
CREATE TRIGGER visual_placements_immutable_update
BEFORE UPDATE ON visual_placements BEGIN SELECT RAISE(ABORT, 'immutable visual placement'); END;
CREATE TRIGGER visual_placements_immutable_delete
BEFORE DELETE ON visual_placements BEGIN SELECT RAISE(ABORT, 'immutable visual placement'); END;
CREATE TRIGGER review_resolutions_immutable_update
BEFORE UPDATE ON review_resolutions BEGIN SELECT RAISE(ABORT, 'append-only review resolution'); END;
CREATE TRIGGER review_resolutions_immutable_delete
BEFORE DELETE ON review_resolutions BEGIN SELECT RAISE(ABORT, 'append-only review resolution'); END;
CREATE TRIGGER review_resolution_evidence_immutable_update
BEFORE UPDATE ON review_resolution_evidence BEGIN SELECT RAISE(ABORT, 'immutable review evidence link'); END;
CREATE TRIGGER review_resolution_evidence_immutable_delete
BEFORE DELETE ON review_resolution_evidence BEGIN SELECT RAISE(ABORT, 'immutable review evidence link'); END;
CREATE TRIGGER upgrade_suggestions_immutable_update
BEFORE UPDATE ON upgrade_suggestions BEGIN SELECT RAISE(ABORT, 'immutable upgrade suggestion'); END;
CREATE TRIGGER upgrade_suggestions_immutable_delete
BEFORE DELETE ON upgrade_suggestions BEGIN SELECT RAISE(ABORT, 'immutable upgrade suggestion'); END;
CREATE TRIGGER feedback_suggestions_immutable_update
BEFORE UPDATE ON feedback_suggestions BEGIN SELECT RAISE(ABORT, 'immutable feedback suggestion'); END;
CREATE TRIGGER feedback_suggestions_immutable_delete
BEFORE DELETE ON feedback_suggestions BEGIN SELECT RAISE(ABORT, 'immutable feedback suggestion'); END;
CREATE TRIGGER upgrade_suggestion_evidence_immutable_update
BEFORE UPDATE ON upgrade_suggestion_evidence BEGIN SELECT RAISE(ABORT, 'immutable upgrade evidence link'); END;
CREATE TRIGGER upgrade_suggestion_evidence_immutable_delete
BEFORE DELETE ON upgrade_suggestion_evidence BEGIN SELECT RAISE(ABORT, 'immutable upgrade evidence link'); END;
CREATE TRIGGER feedback_suggestion_evidence_immutable_update
BEFORE UPDATE ON feedback_suggestion_evidence BEGIN SELECT RAISE(ABORT, 'immutable feedback evidence link'); END;
CREATE TRIGGER feedback_suggestion_evidence_immutable_delete
BEFORE DELETE ON feedback_suggestion_evidence BEGIN SELECT RAISE(ABORT, 'immutable feedback evidence link'); END;
CREATE TRIGGER operation_outcomes_immutable_update
BEFORE UPDATE ON operation_outcomes BEGIN SELECT RAISE(ABORT, 'immutable operation outcome'); END;
CREATE TRIGGER operation_outcomes_immutable_delete
BEFORE DELETE ON operation_outcomes BEGIN SELECT RAISE(ABORT, 'immutable operation outcome'); END;
CREATE TRIGGER operation_item_outcomes_immutable_update
BEFORE UPDATE ON operation_item_outcomes BEGIN SELECT RAISE(ABORT, 'immutable item outcome'); END;
CREATE TRIGGER operation_item_outcomes_immutable_delete
BEFORE DELETE ON operation_item_outcomes BEGIN SELECT RAISE(ABORT, 'immutable item outcome'); END;
CREATE TRIGGER knowledge_index_outbox_immutable_update
BEFORE UPDATE ON knowledge_index_outbox BEGIN SELECT RAISE(ABORT, 'append-only index outbox'); END;
CREATE TRIGGER knowledge_index_outbox_immutable_delete
BEFORE DELETE ON knowledge_index_outbox BEGIN SELECT RAISE(ABORT, 'append-only index outbox'); END;
