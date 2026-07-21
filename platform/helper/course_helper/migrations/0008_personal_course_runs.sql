CREATE TABLE personal_course_runs (
  run_id TEXT PRIMARY KEY,
  request_digest TEXT NOT NULL CHECK (length(request_digest) = 64 AND request_digest NOT GLOB '*[^0-9a-f]*'),
  source_snapshot_digest TEXT NOT NULL CHECK (length(source_snapshot_digest) = 64 AND source_snapshot_digest NOT GLOB '*[^0-9a-f]*'),
  status TEXT NOT NULL CHECK (status IN (
    'queued', 'importing', 'organizing_knowledge', 'composing',
    'assigning_visuals', 'validating', 'needs_attention', 'ready', 'failed'
  )),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX personal_course_runs_request_snapshot
ON personal_course_runs(request_digest, source_snapshot_digest);
