# Knowledge Library Foundation and Reference Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working vertical slice of the local knowledge library: strict versioned contracts, read-only source ingestion, PPTX/Markdown/dataset Demo parsing, controlled tags, exact deduplication, FTS5 retrieval evidence, a typed loopback API, and a light-theme knowledge-preparation panel.

**Architecture:** A Python 3.12 local Helper owns source-root validation, deterministic parsing, SQLite/FTS5 metadata, DuckDB dataset profiling, and typed jobs. The React/Vite application consumes only typed JSON views and never receives raw absolute paths, database transactions, or arbitrary command execution. This plan intentionally leaves full course composition/visual web retrieval and the Win11 WebView2 projection host for their own dependent plans.

**Tech Stack:** Python 3.12.4, Pydantic 2.5.3, FastAPI 0.115.6, Uvicorn 0.32.1, SQLite 3.45.3 with FTS5, python-pptx 1.0.2, Pillow 12.3.0, markdown-it-py 2.2.0, openpyxl 3.1.2, DuckDB 1.5.4, pytest 8.3.4, React 19.2.0, TypeScript 5.9.3, Zod 4.1.12, Vitest 3.2.7.

## Global Constraints

- Work only in `D:/cursor/AI培训/.worktrees/course-studio` on `codex/course-studio-light`.
- `D:/cursor/AI培训/references` is a read-only registered source root. Never rename, rewrite, delete, format, or copy its raw source files into Git.
- Never read, copy, or modify `Course_AIProduct/`.
- Inventory scans store path, type, byte size, and modified time. Compute a full streaming digest only for a white-listed object that is actually ingested.
- Browser interactive upload remains capped at exactly 20 MiB; large registered-root sources are parsed only by the Helper under allowlist, timeout, and resource limits.
- Raw source binaries, visual binaries, and datasets never enter browser `localStorage`.
- SQLite is the knowledge metadata fact source; DuckDB is only the analytical dataset runtime.
- Every published object is immutable. Courses will pin `cardVersionId`; this plan establishes the contracts but does not yet migrate course composition.
- Helper binds only to `127.0.0.1`, validates a per-session token and allowed origin, and accepts only discriminated typed jobs.
- All persistent and transient web UI remains light theme and follows the existing 44 px icon-control accessibility contract.
- Use TDD for every production change. Run focused tests before broad gates. Stage paths explicitly; never use `git add -A`.

---

## Planned File Map

```text
.gitignore
platform/
  helper/
    pyproject.toml                         # pinned Python package and test configuration
    course_helper/
      __init__.py
      __main__.py                         # `python -m course_helper` bounded server entry
      api.py                              # loopback FastAPI application and auth/origin guard
      session.py                          # one-time launch exchange and in-memory session secret
      server.py                           # argparse/configuration and loopback-only Uvicorn startup
      jobs.py                             # discriminated typed job dispatcher
      source_roots.py                     # root registration, containment, fingerprints, streaming digest
      catalog.py                          # SQLite migrations and repository methods
      cards.py                            # candidate construction, exact dedup, review and publish
      retrieval.py                        # FTS5 retrieval and evidence
      demo.py                             # reference-demo CLI orchestration
      domain/
        __init__.py
        common.py                         # VersionMeta, SourceLocator, shared enums
        sources.py                        # source/chunk/visual/dataset contracts
        knowledge.py                      # tags and KnowledgeCardVersion
        evidence.py                       # EvidenceObject and structured failures
      migrations/
        0001_knowledge_catalog.sql
      parsers/
        __init__.py
        markdown_parser.py
        pptx_parser.py
        dataset_profiler.py
      demo/
        reference-demo.json               # relative-path white list only
    tests/
      conftest.py
      test_api.py
      test_cards.py
      test_catalog.py
      test_dataset_profiler.py
      test_demo.py
      test_markdown_parser.py
      test_pptx_parser.py
      test_retrieval.py
      test_source_roots.py
  web/src/
    components/KnowledgePreparationPanel.tsx
    components/KnowledgePreparationPanel.test.tsx
    components/ImportStep.tsx             # mount panel directly below the existing source list
    components/ImportStep.test.tsx
    domain/knowledge.ts
    domain/knowledge-schema.ts
    services/helper-session.ts
    services/helper-session.test.ts
    services/knowledge-client.ts
    app/App.tsx                           # inject Helper client/session into the import step
    app/App.test.tsx
    app/app.css                           # light-theme knowledge preparation styles
  qa/
    run.py                                # add Helper and Demo focused gates
    test_run.py
```

Runtime databases and caches live under a caller-supplied app-data directory and are ignored by Git. Versioned acceptance receipts may be written only under `platform/helper/evidence/` by the final acceptance task.

---

### Task 1: Bootstrap the Helper and strict domain contracts

**Files:**
- Create: `platform/helper/pyproject.toml`
- Create: `platform/helper/course_helper/__init__.py`
- Create: `platform/helper/course_helper/domain/__init__.py`
- Create: `platform/helper/course_helper/domain/common.py`
- Create: `platform/helper/course_helper/domain/sources.py`
- Create: `platform/helper/course_helper/domain/knowledge.py`
- Create: `platform/helper/course_helper/domain/evidence.py`
- Create: `platform/helper/tests/test_domain_contracts.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `SourceLocator`, `VersionMeta`, `SourceAssetVersion`, `ExtractedChunk`, `VisualAssetVersion`, `DatasetAssetVersion`, `TagVocabularyVersion`, `TagAssignment`, `KnowledgeCardVersion`, `LineageEdge`, `ReviewTask`, `EvidenceObject`, and `ExtractionResult` Pydantic models.
- Consumes: no earlier task.

- [ ] **Step 1: Write failing model-contract tests**

```python
import pytest
from pydantic import ValidationError

from course_helper.domain.common import SourceLocator, VersionMeta
from course_helper.domain.knowledge import KnowledgeCardVersion, ReviewTask, TagVocabularyVersion
from course_helper.domain.evidence import LineageEdge


def test_source_locator_rejects_absolute_and_parent_paths() -> None:
    for invalid in (r"C:\\secret.txt", "/etc/passwd", "../escape.md"):
        try:
            SourceLocator(root_id="reference-demo", relative_path=invalid)
        except ValidationError:
            continue
        raise AssertionError(f"accepted unsafe locator: {invalid}")


def test_published_card_is_frozen_and_requires_a_citation() -> None:
    card = KnowledgeCardVersion.example_for_test(status="published")
    assert card.chunk_citations
    try:
        card.title = "mutated"
    except ValidationError:
        return
    raise AssertionError("published version was mutable")


def test_vocabulary_lineage_and_review_contracts_forbid_unknown_fields() -> None:
    vocabulary = vocabulary_fixture()
    assert isinstance(TagVocabularyVersion.model_validate(vocabulary.model_dump()), TagVocabularyVersion)
    for model in (lineage_fixture(), review_task_fixture()):
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unexpected": True})
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_domain_contracts.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'course_helper'`.

- [ ] **Step 3: Add the pinned Python package**

Create `platform/helper/pyproject.toml` with these exact sections:

```toml
[build-system]
requires = ["setuptools==75.6.0"]
build-backend = "setuptools.build_meta"

[project]
name = "course-studio-helper"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "duckdb==1.5.4",
  "fastapi==0.115.6",
  "markdown-it-py==2.2.0",
  "openpyxl==3.1.2",
  "Pillow==12.3.0",
  "pydantic==2.5.3",
  "python-pptx==1.0.2",
  "uvicorn==0.32.1",
]

[project.optional-dependencies]
dev = ["httpx==0.28.1", "pytest==8.3.4"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Install only into the active Python environment:

Run: `python -m pip install -e "platform/helper[dev]"`

Expected: installation succeeds and `python -c "import duckdb; print(duckdb.__version__)"` prints `1.5.4`.

- [ ] **Step 4: Implement frozen, strict models**

Use `ConfigDict(extra="forbid", frozen=True)` on every published/version model. The shared metadata must have these exact fields:

```python
from pathlib import PurePosixPath, PureWindowsPath


class SourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("relative_path cannot contain NUL")
        windows_path = PureWindowsPath(value)
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if windows_path.drive or windows_path.is_absolute() or path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside its source root")
        return path.as_posix()


class VersionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    logical_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_version_id: str | None = None
    created_at: datetime
    created_by: ActorRef
```

Define card status as `draft | review | published | superseded | archived`. A published card validator must require at least one chunk citation for `concept`, `procedure`, `example`, `case`, `evidence`, `misconception`, or `warning`. `TagVocabularyVersion` contains version metadata plus dimensions, each with `one | many` cardinality and version-scoped values whose status is `active | deprecated`. `LineageEdge` has typed relation literals, real version endpoints, and required `evidence_id`. `ReviewTask` has typed kind, subject version, `open | resolved | dismissed` status, blocking flag, and evidence references. All three use `extra="forbid"`; versioned vocabulary is frozen. Keep all fixture builders inside the test module rather than adding test-only production helpers.

- [ ] **Step 5: Ignore runtime state**

Append exactly these patterns to `.gitignore`:

```gitignore
platform/helper/.artifacts/
platform/helper/*.db
platform/helper/*.db-*
platform/helper/course_studio_helper.egg-info/
platform/helper/**/__pycache__/
```

- [ ] **Step 6: Verify contracts and commit**

Run: `python -m pytest platform/helper/tests/test_domain_contracts.py -q`

Expected: PASS.

Run: `git diff --check`

Expected: no output.

```powershell
git add -- .gitignore platform/helper/pyproject.toml platform/helper/course_helper/__init__.py platform/helper/course_helper/domain platform/helper/tests/test_domain_contracts.py
git commit -m "feat(helper): add versioned knowledge contracts"
```

---

### Task 2: Add safe source roots and the SQLite catalog

**Files:**
- Create: `platform/helper/course_helper/source_roots.py`
- Create: `platform/helper/course_helper/catalog.py`
- Create: `platform/helper/course_helper/migrations/0001_knowledge_catalog.sql`
- Create: `platform/helper/tests/test_source_roots.py`
- Create: `platform/helper/tests/test_catalog.py`

**Interfaces:**
- Consumes: `SourceLocator`, `SourceAssetVersion`, `KnowledgeCardVersion`, and `EvidenceObject` from Task 1.
- Produces: `SourceRootRegistry.resolve(locator) -> Path`, `quick_fingerprint(path) -> FileFingerprint`, `stream_sha256(path) -> str`, `register_or_reuse_source(...) -> SourceRegistration`, deterministic object-ID helpers, and `KnowledgeCatalog` repository methods.

- [ ] **Step 1: Write containment and immutability tests**

```python
def test_registry_resolves_only_files_inside_registered_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.md").write_text("ok", encoding="utf-8")
    registry = SourceRootRegistry({"demo": root})
    assert registry.resolve(SourceLocator(root_id="demo", relative_path="ok.md")) == root / "ok.md"
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    with pytest.raises(SourceRootViolation):
        registry.resolve(SourceLocator(root_id="demo", relative_path="linked.md"))


def test_catalog_rejects_a_different_payload_for_existing_version(tmp_path: Path) -> None:
    catalog = KnowledgeCatalog.open(tmp_path / "knowledge.db")
    source = source_version_fixture()
    catalog.insert_source(source)
    with pytest.raises(ImmutableVersionConflict):
        catalog.insert_source(source.model_copy(update={"byte_size": source.byte_size + 1}))


def test_same_locator_and_digest_reuses_source_version_without_changing_created_at(tmp_path: Path) -> None:
    catalog = KnowledgeCatalog.open(tmp_path / "knowledge.db")
    first = register_or_reuse_source(catalog, source_input("demo", "AI.pptx", digest="a" * 64))
    second = register_or_reuse_source(catalog, source_input("demo", "AI.pptx", digest="a" * 64))
    assert second.version_id == first.version_id
    assert second.revision == 1
    assert second.created_at == first.created_at


def test_changed_source_digest_creates_next_revision_and_supersedes(tmp_path: Path) -> None:
    catalog = KnowledgeCatalog.open(tmp_path / "knowledge.db")
    first = register_or_reuse_source(catalog, source_input("demo", "AI.pptx", digest="a" * 64))
    changed = register_or_reuse_source(catalog, source_input("demo", "AI.pptx", digest="b" * 64))
    assert changed.logical_id == first.logical_id
    assert changed.revision == 2
    assert changed.supersedes_version_id == first.version_id
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_source_roots.py platform/helper/tests/test_catalog.py -q`

Expected: FAIL because `SourceRootRegistry` and `KnowledgeCatalog` do not exist.

- [ ] **Step 3: Implement path containment and incremental identity**

`resolve()` must call `Path.resolve(strict=True)`, then `resolved.relative_to(root.resolve(strict=True))`. `quick_fingerprint()` returns byte size plus `st_mtime_ns`; `stream_sha256()` reads 1 MiB chunks and is called only by explicit ingest code.

```python
@dataclass(frozen=True)
class FileFingerprint:
    byte_size: int
    modified_ns: int


def stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
```

Use a fixed UUIDv5 namespace owned by the application. A normalized `root_id + "\0" + relative_path` determines source `logical_id`; `logical_id + "\0" + content_digest` determines source `version_id`. The same locator and digest returns the stored object verbatim, including its original `created_at`. A changed digest increments `revision` and sets `supersedes_version_id`. Chunk logical IDs derive from source logical ID plus the canonical AST/slide locator; chunk version IDs add source version ID and chunk digest. Visual, dataset, and card candidate IDs follow the same rule: stable semantic locator for logical ID, then parent version IDs plus canonical content digest for version ID. Never include clocks, random UUIDs, or absolute paths in deterministic IDs.

- [ ] **Step 4: Create migration 0001**

The SQL must enable foreign keys and create version tables, lineage, evidence, controlled vocabulary, review tasks, and FTS5:

```sql
PRAGMA foreign_keys = ON;
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE sources(version_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, revision INTEGER NOT NULL, content_digest TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, source_version_id TEXT NOT NULL REFERENCES sources(version_id), ordinal INTEGER NOT NULL, content_digest TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE visuals(version_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, revision INTEGER NOT NULL, content_digest TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE datasets(version_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, revision INTEGER NOT NULL, content_digest TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE cards(version_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, content_digest TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE lineage(edge_id TEXT PRIMARY KEY, from_version_id TEXT NOT NULL, to_version_id TEXT NOT NULL, relation TEXT NOT NULL, evidence_id TEXT NOT NULL);
CREATE TABLE evidence(evidence_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE review_tasks(task_id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_version_id TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE tag_vocabularies(version_id TEXT PRIMARY KEY, content_digest TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE tag_values(vocabulary_version_id TEXT NOT NULL REFERENCES tag_vocabularies(version_id), tag_id TEXT NOT NULL, dimension_id TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(vocabulary_version_id, tag_id));
CREATE TABLE card_tags(card_version_id TEXT NOT NULL REFERENCES cards(version_id), vocabulary_version_id TEXT NOT NULL, tag_id TEXT NOT NULL, PRIMARY KEY(card_version_id, vocabulary_version_id, tag_id), FOREIGN KEY(vocabulary_version_id, tag_id) REFERENCES tag_values(vocabulary_version_id, tag_id));
CREATE VIRTUAL TABLE card_fts USING fts5(version_id UNINDEXED, title, learning_objective, body, chunk_text, projected_text, tokenize='trigram');
```

Add unique indexes for `(logical_id, revision)` on every version table that has those columns and validate migration version on open. Migration tests must prove the same stable `tag_id` can occur in two vocabulary versions, `(logical_id, revision)` uniqueness is enforced for sources/cards/visuals/datasets, and `pragma_table_info('card_fts')` contains `projected_text`. Because lineage endpoints span heterogeneous version tables, repository methods must verify both endpoints exist before inserting an edge; `evidence_id` must resolve to a persisted evidence object.

- [ ] **Step 5: Implement immutable repository writes**

Repository inserts run inside `with connection:` transactions. Re-inserting byte-identical canonical JSON is idempotent; the same `version_id` with different JSON raises `ImmutableVersionConflict`. JSON serialization must use one helper equivalent to `json.dumps(model.model_dump(mode="json", by_alias=False, exclude_none=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"))`; never rely on insertion order.

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest platform/helper/tests/test_source_roots.py platform/helper/tests/test_catalog.py -q`

Expected: PASS, including an assertion that `SELECT count(*) FROM pragma_module_list WHERE name='fts5'` equals `1`.

```powershell
git add -- platform/helper/course_helper/source_roots.py platform/helper/course_helper/catalog.py platform/helper/course_helper/migrations platform/helper/tests/test_source_roots.py platform/helper/tests/test_catalog.py
git commit -m "feat(helper): add safe source catalog"
```

---

### Task 3: Parse PPTX notes, slide text, and media relationships

**Files:**
- Create: `platform/helper/course_helper/parsers/__init__.py`
- Create: `platform/helper/course_helper/parsers/pptx_parser.py`
- Create: `platform/helper/tests/test_pptx_parser.py`

**Interfaces:**
- Consumes: `SourceRootRegistry`, `SourceLocator`, `ExtractedChunk`, `VisualAssetVersion`, `EvidenceObject`.
- Produces: `PptxParser.parse(locator, slide_range: range | None) -> ExtractionResult`.

- [ ] **Step 1: Write a generated-fixture unit test and local Demo integration test**

```python
def test_pptx_parser_keeps_slide_text_and_visual_relationship(tmp_path: Path) -> None:
    fixture = build_small_pptx(tmp_path / "fixture.pptx", title="Transformer", image_bytes=PNG_1X1)
    result = parser_for(tmp_path).parse(SourceLocator(root_id="fixture", relative_path="fixture.pptx"))
    assert [chunk.locator.slide_number for chunk in result.chunks] == [1]
    assert "Transformer" in result.chunks[0].normalized_text
    assert result.visuals[0].source_locator.slide_number == 1
    assert result.visuals[0].content_digest == hashlib.sha256(PNG_1X1).hexdigest()


@pytest.mark.reference_demo
def test_ai_pptx_demo_extracts_notes_first() -> None:
    result = demo_parser().parse(SourceLocator(root_id="reference-demo", relative_path="AI.pptx"), range(3, 19))
    assert len(result.chunks) == 16
    assert all(chunk.locator.slide_number in range(3, 19) for chunk in result.chunks)
    assert all(chunk.notes_text.strip() for chunk in result.chunks)
    assert any(visual.source_locator.slide_number == 3 for visual in result.visuals)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_pptx_parser.py -q -m "not reference_demo"`

Expected: FAIL because `PptxParser` is missing.

- [ ] **Step 3: Implement deterministic notes-first parsing**

Use `Presentation(path)` and 1-based slide numbers. For every selected slide:

```python
slide_text = "\n".join(
    shape.text.strip()
    for shape in slide.shapes
    if getattr(shape, "has_text_frame", False) and shape.text.strip()
)
notes_frame = slide.notes_slide.notes_text_frame
notes_text = notes_frame.text.strip() if notes_frame is not None else ""
normalized = normalize_text("\n\n".join(part for part in (notes_text, slide_text) if part))
```

Iterate `slide.part.rels.values()` and accept only image relationships. Stream related blobs, compute SHA-256, inspect dimensions without writing to `references/`, and emit `VisualAssetVersion` with slide number and relationship ID. One malformed media relationship records a failed check in extraction evidence but does not discard valid text.

- [ ] **Step 4: Verify unit and local Demo behavior**

Run: `python -m pytest platform/helper/tests/test_pptx_parser.py -q -m "not reference_demo"`

Expected: PASS.

Run:

```powershell
$env:COURSE_REFERENCE_ROOT='D:/cursor/AI培训/references'
python -m pytest platform/helper/tests/test_pptx_parser.py -q -m reference_demo
```

Expected: PASS with 16 extracted slide chunks and non-empty notes.

- [ ] **Step 5: Commit**

```powershell
git add -- platform/helper/course_helper/parsers platform/helper/tests/test_pptx_parser.py
git commit -m "feat(helper): parse PPTX knowledge sources"
```

---

### Task 4: Parse Markdown as an AST and surface unresolved images

**Files:**
- Create: `platform/helper/course_helper/parsers/markdown_parser.py`
- Create: `platform/helper/tests/test_markdown_parser.py`

**Interfaces:**
- Consumes: source and evidence contracts.
- Produces: `MarkdownParser.parse(locator, heading_selectors: tuple[str, ...] = ()) -> ExtractionResult`.

- [ ] **Step 1: Write fence-aware and image-evidence tests**

```python
def test_markdown_parser_does_not_treat_fenced_python_comments_as_headings(tmp_path: Path) -> None:
    write(tmp_path / "demo.md", "# 主题\n```python\n# 加载数据\nprint('ok')\n```\n## 方法\n正文")
    result = parser_for(tmp_path).parse(locator("demo.md"))
    assert [chunk.heading for chunk in result.chunks] == ["主题", "方法"]
    assert "# 加载数据" in result.chunks[0].code_blocks[0]


def test_absolute_missing_image_creates_unresolved_link_evidence(tmp_path: Path) -> None:
    write(tmp_path / "demo.md", "# 主题\n![图](E:/missing/assets/a.png)")
    result = parser_for(tmp_path).parse(locator("demo.md"))
    assert result.evidence.checks[0].code == "unresolved-link"
    assert result.evidence.status == "warning"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_markdown_parser.py -q`

Expected: FAIL because `MarkdownParser` is missing.

- [ ] **Step 3: Implement Markdown-it token walking**

Use `MarkdownIt("commonmark", {"html": False}).enable("table")`. Build a heading stack only from `heading_open` tokens; associate paragraphs, fences, tables, and images with the current AST section. Walk each `inline` token's children to collect image references instead of assuming images are top-level tokens. Resolve relative images through `SourceRootRegistry`; reject absolute image paths and record an `unresolved-link` check. A selected H1 includes its descendants until the next H1.

```python
tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(text)
for index, token in enumerate(tokens):
    if token.type == "heading_open":
        level = int(token.tag[1])
        heading = tokens[index + 1].content.strip()
        start_section(level, heading, token.map)
    elif token.type == "fence":
        current_section.add_code(token.info.strip(), token.content)
```

- [ ] **Step 4: Add local Demo selectors**

The parser must return exactly the selected top-level units for:

- `AIGC实操 -数据分析.md`: `自行车共享需求`
- `AIGC实操-Prompt工程.md`: `Prompt概论` and `正确提问`

The test may skip only when `COURSE_REFERENCE_ROOT` is unset. The Demo acceptance command sets it and treats a skip as failure.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest platform/helper/tests/test_markdown_parser.py -q`

Expected: PASS.

```powershell
git add -- platform/helper/course_helper/parsers/markdown_parser.py platform/helper/tests/test_markdown_parser.py
git commit -m "feat(helper): parse Markdown knowledge sources"
```

---

### Task 5: Inventory and profile safe dataset assets

**Files:**
- Create: `platform/helper/course_helper/parsers/dataset_profiler.py`
- Create: `platform/helper/tests/test_dataset_profiler.py`

**Interfaces:**
- Produces: `inventory_directory(locator) -> DatasetInventory`, `profile_csv(locator, sample_limit=20) -> DatasetAssetVersion`, and `profile_xlsx(locator, sheet_name=None, sample_limit=20) -> DatasetAssetVersion`.
- Consumes: `SourceRootRegistry`, DuckDB, openpyxl, dataset and evidence contracts.

- [ ] **Step 1: Write denylist and bounded-profile tests**

```python
@pytest.mark.parametrize("name", ["model.pth", "model.pt", "partial.tmp", "package.whl"])
def test_inventory_quarantines_non_dataset_payloads(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"not executable by the helper")
    item = profiler_for(tmp_path).inventory_directory(locator("."))[0]
    assert item.disposition == "quarantined"


def test_csv_profile_records_schema_grain_and_bounded_sample(tmp_path: Path) -> None:
    write(tmp_path / "sales.csv", "order_id,customer,amount\n1,A,10\n2,B,20\n")
    profile = profiler_for(tmp_path).profile_csv(locator("sales.csv"), sample_limit=1)
    assert profile.row_count == 2
    assert [column.name for column in profile.columns] == ["order_id", "customer", "amount"]
    assert len(profile.sample_rows) == 1
    assert profile.grain == "one row per order_id"


def test_sensitive_values_are_redacted_and_require_review(tmp_path: Path) -> None:
    write(tmp_path / "people.csv", "person_id,email,phone\n1,a@example.com,13800138000\n")
    profile = profiler_for(tmp_path).profile_csv(locator("people.csv"), sample_limit=1)
    assert profile.sample_rows[0]["email"] == "[REDACTED]"
    assert profile.sample_rows[0]["phone"] == "[REDACTED]"
    assert {check.code for check in profile.evidence.checks} >= {"sensitive-column", "sample-redacted"}
    assert profile.review_status == "needs-review"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_dataset_profiler.py -q`

Expected: FAIL because `DatasetProfiler` is missing.

- [ ] **Step 3: Implement metadata inventory without full hashing**

Inventory recursively records relative path, extension, byte size, modified time, category, and disposition. It must never open quarantined extensions. Categorize supported files with deterministic filename rules and keep `unclassified` explicit.

- [ ] **Step 4: Implement bounded CSV/XLSX profiling**

Use DuckDB `read_csv_auto` with `SAMPLE_SIZE=20480` for schema and `count(*)`; issue separate bound aggregate and `LIMIT ?` queries for missingness, bounded uniqueness evidence, and at most 20 sample rows. Use openpyxl in `read_only=True, data_only=True` mode for XLSX metadata and samples. Inventory legacy `.xls` files, but return an `unsupported-deep-profile` evidence check without opening them until a deterministic legacy parser is deliberately added. Never evaluate formulas, macros, Notebook cells, or Python scripts.

```python
relation_sql = "read_csv_auto(?, SAMPLE_SIZE=20480, ALL_VARCHAR=false)"
row_count = connection.execute(f"SELECT count(*) FROM {relation_sql}", [str(path)]).fetchone()[0]
sample_rows = connection.execute(f"SELECT * FROM {relation_sql} LIMIT ?", [str(path), sample_limit]).fetchall()
```

Parameterize paths; do not concatenate source paths into SQL. If DuckDB cannot bind a table-function path in the installed version, use `connection.read_csv(str(path))` and query the registered relation instead.

Before serializing samples, classify columns by normalized names (`email`, `phone`, `mobile`, `id_card`, `ssn`, `address`) and conservative value patterns. Replace every sampled value in a flagged column with `[REDACTED]`, persist only the detection category/count, and create a blocking `sensitive-sample` review task; raw flagged values must not enter SQLite, JSON evidence, logs, or API responses. Compute per-column missing counts and rates with aggregates. Infer grain only when a non-sensitive ID-like column is non-null and `count(distinct column) == row_count`; record the aggregate query and confidence as evidence. Otherwise set grain to `unknown` and create a non-blocking `grain-needs-review` check instead of guessing from the filename. XLSX profiling applies the same rules with a bounded in-memory distinct set only for the white-listed workbook; evidence must label whether statistics are full-file or sampled.

- [ ] **Step 5: Add representative local Demo checks**

With `COURSE_REFERENCE_ROOT` set, assert:

- `dataset/1-train.csv` profiles as 12 columns and a non-zero row count.
- `AIExcelData/ex-17-RFM.xlsx` has at least one sheet and a non-empty schema.
- A fixture with the `.xls` extension is inventoried but returns `unsupported-deep-profile` without being opened.
- `AIExcelData/weights/sam_vit_h_4b8939.pth` is quarantined without being opened or hashed.

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest platform/helper/tests/test_dataset_profiler.py -q`

Expected: PASS.

```powershell
git add -- platform/helper/course_helper/parsers/dataset_profiler.py platform/helper/tests/test_dataset_profiler.py
git commit -m "feat(helper): profile safe dataset assets"
```

---

### Task 6: Build candidate cards, controlled tags, deduplication, and publish gates

**Files:**
- Create: `platform/helper/course_helper/cards.py`
- Create: `platform/helper/tests/test_cards.py`

**Interfaces:**
- Produces: `seed_vocabulary()`, `build_candidates(extraction)`, `find_exact_duplicate(card, catalog)`, `create_review_task(...)`, and `publish_card(card, catalog) -> KnowledgeCardVersion`.
- Consumes: extracted chunks/assets, catalog, evidence, and knowledge models.

- [ ] **Step 1: Write atomicity, tag, dedup, and immutability tests**

```python
def test_candidate_groups_at_most_three_adjacent_pptx_slides() -> None:
    candidates = build_candidates(extraction_with_slides(3, 4, 5, 6))
    assert all(1 <= len(card.chunk_citations) <= 3 for card in candidates)


def test_exact_duplicate_is_not_published_twice(catalog: KnowledgeCatalog) -> None:
    first = publish_card(reviewed_card_fixture(), catalog)
    duplicate = reviewed_card_fixture(logical_id="different-logical-id")
    result = publish_card(duplicate, catalog)
    assert result.version_id == first.version_id
    assert catalog.card_status(duplicate.version_id) == "archived"
    assert catalog.has_lineage(
        from_version_id=duplicate.version_id,
        to_version_id=first.version_id,
        relation="deduplicates",
    )


def test_republishing_same_deterministic_version_is_idempotent(catalog: KnowledgeCatalog) -> None:
    candidate = reviewed_card_fixture()
    first = publish_card(candidate, catalog)
    second = publish_card(candidate, catalog)
    assert second == first
    assert catalog.count_cards() == 1
    assert not catalog.has_lineage(first.version_id, first.version_id, "deduplicates")


def test_unknown_tag_blocks_publish(catalog: KnowledgeCatalog) -> None:
    card = reviewed_card_fixture(tag_id="topic:invented")
    with pytest.raises(PublishBlocked, match="unknown tag"):
        publish_card(card, catalog)


def test_deprecated_tag_blocks_publish(catalog: KnowledgeCatalog) -> None:
    seed_vocabulary(catalog, deprecated_tag_id="tool:legacy")
    card = reviewed_card_fixture(tag_id="tool:legacy")
    with pytest.raises(PublishBlocked, match="deprecated tag"):
        publish_card(card, catalog)


def test_two_values_in_single_cardinality_dimension_block_publish(catalog: KnowledgeCatalog) -> None:
    card = reviewed_card_fixture(tag_ids=["difficulty:beginner", "difficulty:advanced"])
    with pytest.raises(PublishBlocked, match="single-cardinality tag conflict"):
        publish_card(card, catalog)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_cards.py -q`

Expected: FAIL because card workflow functions are missing.

- [ ] **Step 3: Seed vocabulary version 1**

Create stable IDs for `topic`, `audience`, `difficulty`, `pedagogy`, `tool`, `scenario`, and `dataType`. Main types are `concept`, `procedure`, `example`, `case`, `exercise`, `assessment`, `evidence`, `misconception`, and `warning`. Store labels and aliases as data rows, not Python enums beyond contract validation.

- [ ] **Step 4: Implement deterministic Demo candidate construction**

For the Demo only, split cards by semantic heading and contiguous slide title. A PPTX candidate may cite 1–3 adjacent selected slides; a Markdown candidate cites one AST unit plus its child chunks. Build `contentAst` from normalized text and preserve citations. Do not invent unsupported facts or auto-publish.

- [ ] **Step 5: Implement exact dedup and review-gated publish**

Canonical card content excludes IDs and timestamps. `publish_card()` first checks deterministic `version_id`: if the byte-identical version already exists, it returns the stored version without creating a card or lineage edge. Otherwise, on an exact content digest match owned by a different logical/version ID, persist the duplicate candidate version as `archived`, record its version-level `deduplicates` edge to the existing published version, and return that existing published version. This keeps lineage endpoints real without creating a second published card. Near-duplicate hooks create an open `review_task` but do not merge; the full semantic implementation belongs to the dependent composition plan. Publishing requires `status == "review"`, a pinned vocabulary version, active known tags, no multiple values in a `one` cardinality dimension, valid citations, valid visual/dataset references, and zero open blocking review tasks.

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest platform/helper/tests/test_cards.py -q`

Expected: PASS.

```powershell
git add -- platform/helper/course_helper/cards.py platform/helper/tests/test_cards.py
git commit -m "feat(helper): publish governed knowledge cards"
```

---

### Task 7: Add FTS5 retrieval with explicit degraded evidence

**Files:**
- Create: `platform/helper/course_helper/retrieval.py`
- Create: `platform/helper/tests/test_retrieval.py`
- Modify: `platform/helper/course_helper/catalog.py`
- Modify: `platform/helper/course_helper/cards.py`
- Modify: `platform/helper/tests/test_catalog.py`
- Modify: `platform/helper/tests/test_cards.py`

**Interfaces:**
- Produces: `KnowledgeRetriever.search(query: RetrievalQuery) -> RetrievalResult`.
- Consumes: published cards and chunks in `KnowledgeCatalog`.

- [ ] **Step 1: Write retrieval filtering and evidence tests**

```python
def test_search_returns_only_published_cards_with_matching_tags(catalog: KnowledgeCatalog) -> None:
    seed_retrieval_cards(catalog)
    result = KnowledgeRetriever(catalog).search(
        RetrievalQuery(text="语言模型能力边界", required_tag_ids=["topic:llm"], limit=5)
    )
    assert [hit.card.status for hit in result.hits] == ["published"]
    assert all("topic:llm" in hit.card_tag_ids for hit in result.hits)


def test_fts_only_search_is_honestly_marked_degraded(catalog: KnowledgeCatalog) -> None:
    result = KnowledgeRetriever(catalog, embedding_provider=None).search(RetrievalQuery(text="RFM"))
    assert result.evidence.status == "degraded"
    assert result.evidence.checks[0].code == "embedding-unavailable"


def test_query_quotes_and_operators_are_treated_as_literals(catalog: KnowledgeCatalog) -> None:
    seed_retrieval_cards(catalog)
    result = KnowledgeRetriever(catalog).search(RetrievalQuery(text='"RFM" OR title:*'))
    assert result.evidence.status in {"verified", "degraded"}
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_retrieval.py -q`

Expected: FAIL because `KnowledgeRetriever` is missing.

- [ ] **Step 3: Index published card text transactionally**

On publish, insert title, learning objective, plain text projected from `contentAst`, cited chunk text, and their canonical concatenation as `projected_text` into `card_fts`. Superseded or archived versions are deleted from FTS in the same catalog transaction that changes their status. Add catalog/card integration tests that force a transaction failure and prove neither card status nor its FTS row changes partially.

- [ ] **Step 4: Implement escaped FTS queries and stable ranking**

Normalize Unicode and whitespace in `safe_fts_match()`. Split only on whitespace, escape embedded quotes by doubling them, wrap each resulting literal in double quotes, and join literals with `OR`; an unspaced Chinese phrase therefore remains one literal and the FTS5 trigram tokenizer supplies substring matching. Empty normalized input is rejected. Since trigram MATCH cannot find literals shorter than three Unicode code points, use bound `instr(lower(projected_text), lower(?))` predicates for those literals and label that branch in retrieval evidence; never interpolate them into SQL. Filter by status and tag IDs with bound SQL parameters, order FTS hits by `bm25(card_fts)` then `version_id`, append short-literal-only hits in stable `version_id` order, deduplicate, and cap limit at 50. Return per-hit score components, index schema version, query digest, and retrieval evidence. Do not call an embedding provider in this plan; emit the required degraded evidence.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest platform/helper/tests/test_retrieval.py -q`

Expected: PASS.

```powershell
git add -- platform/helper/course_helper/retrieval.py platform/helper/course_helper/catalog.py platform/helper/course_helper/cards.py platform/helper/tests/test_retrieval.py platform/helper/tests/test_catalog.py platform/helper/tests/test_cards.py
git commit -m "feat(helper): retrieve published knowledge cards"
```

---

### Task 8: Expose only authenticated typed jobs on loopback

**Files:**
- Modify: `platform/helper/pyproject.toml`
- Create: `platform/helper/course_helper/__main__.py`
- Create: `platform/helper/course_helper/server.py`
- Create: `platform/helper/course_helper/session.py`
- Create: `platform/helper/course_helper/jobs.py`
- Create: `platform/helper/course_helper/api.py`
- Create: `platform/helper/tests/test_api.py`
- Create: `platform/helper/tests/test_server.py`

**Interfaces:**
- Produces: `LaunchSession`, `BoundedJobRunner`, `create_app(runtime: HelperRuntime) -> FastAPI`, `server.main()`, `KnowledgeSummary`, and discriminated job models for `source_ingest`, `dataset_profile`, `knowledge_retrieve`, and `knowledge_publish`.
- Consumes: parser, card, catalog, and retrieval services.

- [ ] **Step 1: Write auth, origin, schema, and dispatch tests**

```python
def test_jobs_require_session_token_and_allowed_origin(client: TestClient) -> None:
    response = client.post("/v1/jobs", json={"type": "knowledge_retrieve", "query": "RFM"})
    assert response.status_code == 401
    response = client.post(
        "/v1/jobs",
        headers={"Origin": "http://127.0.0.1:4173", "X-Course-Session": "wrong"},
        json={"type": "knowledge_retrieve", "query": "RFM"},
    )
    assert response.status_code == 401


def test_launch_nonce_is_single_use_and_exchanged_for_session_token(client: TestClient, launch_nonce: str) -> None:
    headers = {"Origin": "http://127.0.0.1:4173"}
    first = client.post("/v1/session/exchange", headers=headers, json={"nonce": launch_nonce})
    assert first.status_code == 200
    assert len(first.json()["sessionToken"]) >= 43
    replay = client.post("/v1/session/exchange", headers=headers, json={"nonce": launch_nonce})
    assert replay.status_code == 401


def test_unknown_or_shell_like_job_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/v1/jobs", headers=auth_headers, json={"type": "shell", "command": "whoami"})
    assert response.status_code == 422


def test_typed_retrieval_job_returns_structured_evidence(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={"type": "knowledge_retrieve", "query": "RFM", "limit": 5},
    )
    assert response.status_code == 200
    assert response.json()["evidence"]["kind"] == "retrieval"


def test_timed_out_job_is_terminated_and_returns_failure_evidence(timeout_client: TestClient) -> None:
    response = timeout_client.post(
        "/v1/jobs",
        headers=timeout_client.auth_headers,
        json={"type": "knowledge_retrieve", "query": "RFM"},
    )
    assert response.status_code == 504
    assert response.json()["evidence"]["checks"][0]["code"] == "job-timeout"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/helper/tests/test_api.py -q`

Expected: FAIL because `create_app` is missing.

- [ ] **Step 3: Implement discriminated Pydantic job specs**

```python
class SourceIngestJob(BaseModel):
    type: Literal["source_ingest"]
    locator: SourceLocator
    selection: SourceSelection

class DatasetProfileJob(BaseModel):
    type: Literal["dataset_profile"]
    locator: SourceLocator
    sample_limit: int = Field(default=20, ge=0, le=20)

class KnowledgeRetrieveJob(BaseModel):
    type: Literal["knowledge_retrieve"]
    query: str = Field(min_length=1, max_length=2000)
    required_tag_ids: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=10, ge=1, le=50)
```

All HTTP boundary models share an alias generator that emits lower camel case, forbid extras, and serialize FastAPI responses by alias. Internal domain/catalog fields stay snake case. Add the publish job with only `cardVersionId`; it cannot accept arbitrary card JSON. Define the server-side `KnowledgeSummary` with the same lower-camel JSON fields and literals later validated by the web Zod schema.

- [ ] **Step 4: Implement one-time launch exchange**

At Helper startup, generate a cryptographically random launch nonce with a 60-second expiry and a separate session token. Open the configured web application with `webbrowser.open()` using a connect URL whose fragment carries only the loopback base URL and nonce, for example `http://127.0.0.1:4173/#helper=http%3A%2F%2F127.0.0.1%3A8765&nonce=<value>`. The fragment is not sent in HTTP requests. If browser launch fails, abort startup with a redacted error rather than printing the secret URL. `POST /v1/session/exchange` requires an exact allowed `Origin`, accepts the nonce once, consumes it on a successful exchange, and returns the session token. Reject expired and replayed nonces. Never log, persist, or print either secret.

- [ ] **Step 5: Implement the allowlisted bounded job runner**

Map each discriminated job type to a module-level allowlisted callable; never accept a command, executable, module name, or arbitrary function from JSON. Run file/parser work in a dedicated `multiprocessing` child created with the Windows-safe `spawn` context so timeout or client cancellation can terminate that child without leaving work running. Apply these exact ceilings before launch and record them in `EvidenceObject`: `source_ingest` 120 seconds, 512 MiB source, and at most 64 selected PPTX slides; `dataset_profile` 60 seconds, 1 GiB CSV/Parquet or 512 MiB XLSX, and 20 returned samples; `knowledge_retrieve` 5 seconds, 2,000 query characters, 50 tags, and 50 hits; `knowledge_publish` 10 seconds and one existing `cardVersionId`. A size/selection violation returns 413/422 without spawning work. Timeout returns 504 with `job-timeout`. On client cancellation, terminate the child and persist `job-cancelled` evidence for later inspection because the disconnected caller cannot receive a response. Unexpected exceptions return sanitized `job-failed` evidence with no absolute path or secret.

- [ ] **Step 6: Implement loopback app, startup entry, and guards**

Add `[project.scripts] course-helper = "course_helper.server:main"`; `course_helper.__main__` delegates to the same function. CLI arguments may configure database/app-data paths, the registered reference root, the exact allowed web origin, and port, but there is no `--host`: Uvicorn is always called with `host="127.0.0.1"`. Except for the one-time exchange route, every `/v1/*` route requires exact `Origin` membership and constant-time session-token comparison. Expose authenticated `GET /v1/knowledge/summary` from catalog aggregates; do not accept a source path on that route. `/health` returns service version, schema version, and database readiness but no paths. Configure CORS only for the explicit origin. `test_server.py` monkeypatches Uvicorn and proves the host is fixed, the startup entry creates the launch session, and CLI help exposes no arbitrary host/command option.

- [ ] **Step 7: Verify and commit**

Run: `python -m pytest platform/helper/tests/test_api.py platform/helper/tests/test_server.py -q`

Expected: PASS.

```powershell
git add -- platform/helper/pyproject.toml platform/helper/course_helper/__main__.py platform/helper/course_helper/server.py platform/helper/course_helper/session.py platform/helper/course_helper/jobs.py platform/helper/course_helper/api.py platform/helper/tests/test_api.py platform/helper/tests/test_server.py
git commit -m "feat(helper): expose typed knowledge jobs"
```

---

### Task 9: Orchestrate the read-only reference Demo and evidence receipt

**Files:**
- Create: `platform/helper/course_helper/demo/reference-demo.json`
- Create: `platform/helper/course_helper/demo.py`
- Create: `platform/helper/tests/test_demo.py`
- Create at acceptance time: `platform/helper/evidence/reference-demo-receipt.json`

**Interfaces:**
- Produces: `run_reference_demo(source_root, database_path, evidence_path) -> DemoReceipt` and `python -m course_helper.demo`.
- Consumes: all Helper services from Tasks 2–8.

- [ ] **Step 1: Write the white-list manifest**

```json
{
  "schemaVersion": 1,
  "rootId": "reference-demo",
  "inventoryRoots": ["dataset", "AIExcelData"],
  "sources": [
    {"kind": "pptx", "path": "AI.pptx", "slides": {"start": 3, "endInclusive": 18}},
    {"kind": "markdown", "path": "AIGC实操 -数据分析.md", "headings": ["自行车共享需求"]},
    {"kind": "markdown", "path": "AIGC实操-Prompt工程.md", "headings": ["Prompt概论", "正确提问"]},
    {"kind": "csv", "path": "dataset/1-train.csv"},
    {"kind": "xlsx", "path": "AIExcelData/ex-17-RFM.xlsx"}
  ],
  "quarantinedExtensions": [".pth", ".pt", ".tmp", ".whl"]
}
```

- [ ] **Step 2: Write a failing end-to-end Demo test**

```python
@pytest.mark.reference_demo
def test_reference_demo_builds_traceable_cards_and_bounded_dataset_profiles(tmp_path: Path) -> None:
    receipt = run_reference_demo(reference_root(), tmp_path / "knowledge.db", tmp_path / "receipt.json")
    assert receipt.pptx_slide_chunks == 16
    assert receipt.pptx_chunks_with_notes == 16
    assert receipt.markdown_units == {"自行车共享需求", "Prompt概论", "正确提问"}
    assert receipt.profiled_datasets == {"dataset/1-train.csv", "AIExcelData/ex-17-RFM.xlsx"}
    assert receipt.quarantined_extension_counts[".pth"] >= 1
    assert receipt.published_card_count > 0
    assert receipt.forbidden_source_writes == 0
    assert all(item.before_sha256 == item.after_sha256 for item in receipt.source_integrity)
```

- [ ] **Step 3: Run test and verify RED**

Run:

```powershell
$env:COURSE_REFERENCE_ROOT='D:/cursor/AI培训/references'
python -m pytest platform/helper/tests/test_demo.py -q -m reference_demo
```

Expected: FAIL because `run_reference_demo` is missing.

- [ ] **Step 4: Implement deterministic orchestration**

Record an initial metadata fingerprint and streaming SHA-256 for exactly the five white-listed source files. Reuse those digests for source registration instead of hashing the same inputs again during ingest. Parse and profile them, create candidate cards, apply exact dedup, and record a fixture-only review-decision evidence object before publishing each candidate that passes the manifest's deterministic Demo review policy. The general ingestion path never auto-publishes. Index the approved cards and query one known phrase from each topic. Re-read metadata fingerprints and recompute streaming SHA-256 for those same five files afterward; any metadata or digest change is a fatal `forbidden-source-write` check.

Inventory `dataset` and `AIExcelData` by metadata only. Never full-hash or open any non-white-listed or quarantined file. Canonicalize the receipt without absolute paths; store `rootId + relativePath`, the five before/after digests, counts, parser versions, object digests, checks, and command version. “Hash verified” applies only to those five explicit inputs; inventory integrity remains metadata-scoped and is labeled accordingly.

- [ ] **Step 5: Run Demo twice to prove idempotence**

Run:

```powershell
$env:COURSE_REFERENCE_ROOT='D:/cursor/AI培训/references'
python -m course_helper.demo --database platform/helper/.artifacts/demo.db --evidence platform/helper/.artifacts/receipt-1.json
python -m course_helper.demo --database platform/helper/.artifacts/demo.db --evidence platform/helper/.artifacts/receipt-2.json
```

Expected: second run reports zero new source versions and zero duplicate cards; both runs report zero forbidden source writes.

The CLI also accepts `--verify-idempotence`, which performs both passes in one invocation and writes a canonical `idempotence` block with second-pass counts. That mode is required for the committed acceptance receipt.

- [ ] **Step 6: Commit**

```powershell
git add -- platform/helper/course_helper/demo platform/helper/course_helper/demo.py platform/helper/tests/test_demo.py
git commit -m "feat(helper): build the reference knowledge demo"
```

---

### Task 10: Show the knowledge Demo inside the existing import workflow

**Files:**
- Create: `platform/web/src/domain/knowledge.ts`
- Create: `platform/web/src/domain/knowledge-schema.ts`
- Create: `platform/web/src/services/helper-session.ts`
- Create: `platform/web/src/services/helper-session.test.ts`
- Create: `platform/web/src/services/knowledge-client.ts`
- Create: `platform/web/src/components/KnowledgePreparationPanel.tsx`
- Create: `platform/web/src/components/KnowledgePreparationPanel.test.tsx`
- Modify: `platform/web/src/components/ImportStep.tsx`
- Create: `platform/web/src/components/ImportStep.test.tsx`
- Modify: `platform/web/src/app/App.tsx`
- Modify: `platform/web/src/app/App.test.tsx`
- Modify: `platform/web/src/app/app.css`

**Interfaces:**
- Produces: `KnowledgeSummary`, `knowledgeSummarySchema`, `KnowledgeClient.getSummary()`, and `<KnowledgePreparationPanel />`.
- Consumes: Helper `GET /v1/knowledge/summary`, existing light-theme tokens, and existing import-step context.

- [ ] **Step 1: Write failing schema and component tests**

```tsx
it("shows published cards, governed tags, and degraded retrieval without blocking file import", async () => {
  const client = new FakeKnowledgeClient({
    sourceCount: 5,
    publishedCardCount: 12,
    reviewTaskCount: 2,
    retrievalMode: "fts-degraded",
    tagLabels: ["大语言模型", "数据分析", "Prompt 工程"],
  });
  render(<KnowledgePreparationPanel client={client} />);
  expect(await screen.findByText("12 张已发布知识卡")).toBeVisible();
  expect(screen.getByText("全文检索模式")).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("2 项待审核");
});

it("keeps the import workflow usable when the helper is offline", async () => {
  render(<KnowledgePreparationPanel client={new RejectingKnowledgeClient()} />);
  expect(await screen.findByRole("status")).toHaveTextContent("本地知识服务未连接");
  expect(screen.getByRole("button", { name: "重试连接" })).toBeEnabled();
});

it("mounts knowledge preparation directly after the source list", () => {
  render(<ImportStep {...importStepProps()} knowledgeClient={new FakeKnowledgeClient()} />);
  const sourceList = screen.getByRole("list", { name: "已导入资料" });
  const knowledgeRegion = screen.getByRole("region", { name: "知识准备" });
  expect(sourceList.nextElementSibling).toBe(knowledgeRegion);
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `npm --prefix platform/web test -- --run src/components/KnowledgePreparationPanel.test.tsx`

Expected: FAIL because the component and contracts do not exist.

- [ ] **Step 3: Add strict TypeScript/Zod contracts**

```ts
export interface KnowledgeSummary {
  schemaVersion: 1;
  sourceCount: number;
  publishedCardCount: number;
  reviewTaskCount: number;
  retrievalMode: "hybrid" | "fts-degraded";
  tagLabels: string[];
  updatedAt: string;
}

export const knowledgeSummarySchema = z.object({
  schemaVersion: z.literal(1),
  sourceCount: z.number().int().nonnegative(),
  publishedCardCount: z.number().int().nonnegative(),
  reviewTaskCount: z.number().int().nonnegative(),
  retrievalMode: z.enum(["hybrid", "fts-degraded"]),
  tagLabels: z.array(z.string().min(1)),
  updatedAt: z.string().datetime(),
}) satisfies z.ZodType<KnowledgeSummary>;
```

- [ ] **Step 4: Exchange and contain the Helper session secret**

`helper-session.ts` reads the loopback base URL and one-time nonce from the URL fragment, immediately clears the fragment with `history.replaceState`, exchanges the nonce against the exact configured loopback origin, and stores the returned token in `sessionStorage` only. It rejects non-loopback Helper URLs, malformed fragments, and responses that fail Zod validation. Tests prove the fragment is cleared, the token does not reach `localStorage`, and a page without launch material remains safely offline.

- [ ] **Step 5: Implement a bounded client and contextual panel**

The client uses only the validated loopback base URL and session token from `helper-session.ts`, validates every response with Zod, uses `AbortController` with a 5-second timeout, and never accepts paths. `ImportStep.tsx`, which owns the existing source list, mounts the panel immediately after that list; `App.tsx` only injects the session-derived client. It is not a top-level navigation item. Add compact responsive styles to `app.css` using existing light tokens and real Phosphor icons, with 44 px controls and an `aria-live` status.

- [ ] **Step 6: Integrate without changing the four-step workflow**

Render the panel only in the import step. Helper offline, degraded retrieval, or pending card review must not prevent ordinary browser file import. Do not add a fifth workflow step.

- [ ] **Step 7: Verify and commit**

Run: `npm --prefix platform/web test -- --run src/services/helper-session.test.ts src/components/KnowledgePreparationPanel.test.tsx src/components/ImportStep.test.tsx src/app/App.test.tsx`

Expected: PASS.

Run: `npm --prefix platform/web run typecheck`

Expected: PASS.

```powershell
git add -- platform/web/src/domain/knowledge.ts platform/web/src/domain/knowledge-schema.ts platform/web/src/services/helper-session.ts platform/web/src/services/helper-session.test.ts platform/web/src/services/knowledge-client.ts platform/web/src/components/KnowledgePreparationPanel.tsx platform/web/src/components/KnowledgePreparationPanel.test.tsx platform/web/src/components/ImportStep.tsx platform/web/src/components/ImportStep.test.tsx platform/web/src/app/App.tsx platform/web/src/app/App.test.tsx platform/web/src/app/app.css
git commit -m "feat(studio): show knowledge preparation status"
```

---

### Task 11: Add repository gates and commit the acceptance receipt

**Files:**
- Modify: `platform/qa/run.py`
- Modify: `platform/qa/test_run.py`
- Create: `platform/helper/evidence/reference-demo-receipt.json`
- Create: `platform/helper/design-qa.md`

**Interfaces:**
- Produces: `run_knowledge_demo_gate(repo_root, require_source_root)`, `python platform/qa/run.py knowledge-demo`, and expands `all` to include Helper unit tests without requiring physical dual-screen certification.
- Consumes: all prior tasks.

- [ ] **Step 1: Write failing QA-gate tests**

```python
def test_all_gate_includes_helper_contracts_and_reference_demo(monkeypatch, tmp_path):
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)
    monkeypatch.setattr(qa, "run_focused", lambda _root: [])
    monkeypatch.setattr(
        qa,
        "run_command",
        lambda name, _args, _cwd: qa.CheckResult(name=name, ok=True, details="stubbed"),
    )
    names = [check.name for check in qa.run_all(tmp_path)]
    assert "helper tests" in names
    assert "knowledge demo" in names


def test_explicit_knowledge_demo_gate_requires_registered_root(monkeypatch, tmp_path):
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)
    result = qa.run_knowledge_demo_gate(tmp_path, require_source_root=True)
    assert result.ok is False
    assert "COURSE_REFERENCE_ROOT" in result.details


def test_demo_receipt_must_report_zero_reference_writes(tmp_path):
    receipt = valid_demo_receipt()
    receipt["forbiddenSourceWrites"] = 1
    write_receipt(tmp_path, receipt)
    result = qa.check_knowledge_demo_receipt(tmp_path)
    assert result.ok is False
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest platform/qa/test_run.py -q`

Expected: FAIL because the new checks are absent.

- [ ] **Step 3: Implement focused Helper and Demo checks**

`helper tests` runs all non-reference Helper tests. In `all`, `knowledge demo` runs the white-listed Demo when `COURSE_REFERENCE_ROOT` is set; when unset it returns `ok=True` with the exact `NOT CERTIFIED: COURSE_REFERENCE_ROOT unset` detail so ordinary browser development remains usable without implying certification. The explicit `knowledge-demo` CLI calls `run_knowledge_demo_gate(..., require_source_root=True)` and fails when the variable is absent. Validate receipt schema, parser versions, white-list paths, counts, the five before/after source digests, object/evidence digests, quarantined extensions, zero source writes, and idempotence.

- [ ] **Step 4: Generate the committed receipt from a clean implementation commit**

Run:

```powershell
$env:COURSE_REFERENCE_ROOT='D:/cursor/AI培训/references'
python -m course_helper.demo --database platform/helper/.artifacts/acceptance.db --evidence platform/helper/evidence/reference-demo-receipt.json --verify-idempotence
```

Expected: receipt reports 16 PPTX slide chunks with notes, the three Markdown units, the two dataset profiles, at least one quarantined weight, published cards greater than zero, zero forbidden source writes, and zero new source/card versions on its internal second pass.

- [ ] **Step 5: Run focused and broad gates**

Run: `python -m pytest platform/helper/tests -q`

Expected: all Helper tests pass.

Run: `python platform/qa/run.py knowledge-demo`

Expected: all knowledge Demo checks pass with hash-verified evidence.

Run: `npm --prefix platform/web test -- --run`

Expected: all web tests pass.

Run: `npm --prefix platform/web run typecheck`

Expected: PASS.

Run: `npm --prefix platform/web run build`

Expected: PASS.

Run: `python platform/qa/run.py all`

Expected: every release check passes; output must not claim physical dual-screen certification.

- [ ] **Step 6: Verify the protected source root and commit evidence**

Re-run the Demo metadata comparison and confirm zero changes. Check only Git path metadata for protected tracked paths; do not hash or recursively read unrelated protected directories.

```powershell
git add -- platform/qa/run.py platform/qa/test_run.py platform/helper/evidence/reference-demo-receipt.json platform/helper/design-qa.md
git commit -m "test(platform): certify the knowledge reference demo"
```

---

## Plan Completion Boundary

This plan is complete when the read-only reference Demo is reproducible, governed cards are stored and retrievable with evidence, the existing import workflow displays the Helper summary without becoming a second product, all release gates pass, and the working tree is clean.

The next dependent plan must implement hybrid semantic retrieval, `CourseRequirement → CourseOutline → CardPlacement → Slide AST`, version-upgrade UX, and real visual provenance/web retrieval. The independent Win11 plan must implement the WebView2 projection host and real-hardware certification; neither is silently folded into this foundation plan.
