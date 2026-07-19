# Course composition Task 1 report

Date: 2026-07-17  
Scope: `docs/superpowers/plans/2026-07-16-course-composition-and-authentic-visuals.md`, Task 1 only  
Execution protocol: `docs/superpowers/plans/2026-07-17-gitless-execution-amendment.md`

## Boundary result

- Worked only in `D:\cursor\AI培训\.worktrees\course-studio`.
- Did not run Git commands or initialize Git metadata.
- Did not access `Course_AIProduct/` or `references/`.
- Did not start Task 2 or edit its migration/storage surfaces.

## RED evidence

Command:

```powershell
python -m pytest platform/helper/tests/test_composition_contracts.py -q
```

Observed before production implementation:

```text
Fsssssssssssssss
FAILED test_task_1_contract_modules_exist
AssertionError: Task 1 must provide the canonical composition, Slide AST, and
visual-policy modules; missing ('course_helper.domain.composition',
'course_helper.domain.slide_ast', 'course_helper.domain.visual_policy')
1 failed, 15 skipped in 0.35s
```

The failure was the intended missing-contract failure. No production Task 1
module existed at that point.

## Minimal implementation

- Added strict frozen `CourseRequirement`, `CardPlacement`, adjustable
  `CourseOutline`, and digest-bound `CourseVersion` contracts plus canonical
  JSON/SHA-256 helpers.
- Added render-neutral `SlideNode`, `SlideDeckAst`, opaque immutable asset
  bindings, typed runtime-job references, and `RuntimeManifest`.
- Added `TrustedExternalLink` as the only typed URL-bearing browser/API
  projection, immutable `VisualPlacement`, crop/transformation/attribution
  contracts, versioned authenticity/license policies, and deterministic
  scope-aware policy decisions.
- Extended the canonical `ReviewTask` without rewriting serialized schema-v1
  fields. All 13 persisted legacy kinds expose exact category/reason properties;
  `exact-duplicate` and `course-feedback` use their own category/reason.
- Kept legacy `VisualAssetVersion.landing_page_url/asset_url` bytes readable
  only through a server-only provenance projection. Its bounded browser view
  copies neither legacy URL field nor the final media URL.
- Added compatibility re-exports from `course_helper.domain`.

## GREEN evidence

Focused command:

```powershell
python -m pytest platform/helper/tests/test_composition_contracts.py -q
```

Result:

```text
................
16 passed in 0.39s
```

Focused plus canonical regression:

```powershell
python -m pytest platform/helper/tests/test_composition_contracts.py platform/helper/tests/test_domain_contracts.py -q
```

Result:

```text
.....................................
37 passed in 0.39s
```

One intermediate focused run produced 14 passes and 2 failures because the
schema-v1 fixture was incorrectly passed through the strict Python-object
entrypoint. A minimal diagnostic proved the persisted JSON reader
`model_validate_json` correctly accepts JSON arrays and RFC 3339 timestamps;
the two fixture reads were corrected, with no production-policy weakening.

## Exact changed-file SHA-256 inventory

```text
734EEC5FBC843AB3D0C723593883889083EE3D79A7CB0A86D82949BD5FE28333  platform/helper/course_helper/domain/composition.py
2C1074B38AE08270D9DF44091EA4AAB2320D5C10DA8757A87F9A95EA55AC818A  platform/helper/course_helper/domain/slide_ast.py
AF5AB5967C2E75CBE17A836A5F5E6EA59176866FBE9EFAABE4644B0E7A48CC39  platform/helper/course_helper/domain/visual_policy.py
1BC68506885F7A7AD41B8C6F0B0C7C0B02FB435807E3B2DD3DD39ADD614EE404  platform/helper/course_helper/domain/knowledge.py
F40A33E88275F884F2BD27B7AE84D7EBB920E448240FAEC7BA0683480B5A86A2  platform/helper/course_helper/domain/sources.py
D455F7CFD0E0F3F861840820145AFC878D9E4DD720D17B8AD8914588BB4EB0A7  platform/helper/course_helper/domain/__init__.py
FF46D66E28F65C8943F5A3255079D10524A5D73DACAA52AB83BA8DE35F42BBBF  platform/helper/tests/test_composition_contracts.py
EF4A1E33D28918541F4A948A3533D8D7519B96454027271B469F4182C113F9B5  platform/helper/tests/fixtures/schema-v1/review-visual.json
```

This report is excluded from its own embedded inventory because a file cannot
contain its final SHA-256 without changing that digest. Its final digest is
reported to the coordinating agent after this file is sealed.

## Independent review and remediation

Two independent read-only reviews found no Critical issue but blocked Task 2
on unsafe collection IDs, unbounded Slide AST depth/total nodes, incomplete
public authorization/attribution checks, canonicalized-rather-than-byte-exact
legacy fixture assertions, legacy final-media URL relabeling, unconstrained
legacy usage scopes, and contradictory crop/transformation/license decisions.

Root-cause reproduction proved collection fields only applied uniqueness while
scalar fields used an opaque-ID pattern. The review-fix RED command was:

```powershell
python -m pytest platform/helper/tests/test_composition_contracts.py -q
```

Observed result before the fix:

```text
11 failed, 15 passed in 0.57s
```

The smallest coherent fix introduced one shared strict `OpaqueId`, applied it
at the affected persisted boundaries, bounded the raw Slide AST iteratively to
500 nodes and depth 32, made public authorization global and public
data-derived attribution explicit, validated legacy scopes, rejected the
known legacy final-media URL from trusted-link projection, and enforced
crop/transformation/license consistency. The fixture now stores each original
canonical payload as a string plus its original SHA-256, and the compatibility
tests require exact serialized bytes after reading.

Fresh post-review GREEN commands:

```text
python -m pytest platform/helper/tests/test_composition_contracts.py platform/helper/tests/test_domain_contracts.py -q
47 passed in 0.37s

python -m pytest platform/helper/tests -m "not reference_demo" -q
333 passed, 4 skipped, 7 deselected in 45.15s
```

Post-review exact changed-file SHA-256 inventory:

```text
0C2BD6E76CD7FD8C65D2DF2F1528AB73D2AAED7A778E36D2B2D051C67C305E62  platform/helper/course_helper/domain/common.py
2F5D9A0595C666E4FC34E8391390B6A9511B84D559BAD5713B745D595DB6880B  platform/helper/course_helper/domain/composition.py
CF71B5EE7D17C9F97004026E4AA6FC44B7278ED59AC1F8D45330BF64F1A092FF  platform/helper/course_helper/domain/slide_ast.py
0C2DB17431701AF50749D15F28E35C1B6805EC113578D8019A62BE81CEE71C38  platform/helper/course_helper/domain/visual_policy.py
8CB11A02CE5789D94E2BA05F8C18A26D1044749BB44A3563EAF45F892D1FC789  platform/helper/course_helper/domain/knowledge.py
3E0FA4A3712EDD22E866B1997D833519C2D5D72DA69818D75181CEE0C2D3B575  platform/helper/course_helper/domain/sources.py
D455F7CFD0E0F3F861840820145AFC878D9E4DD720D17B8AD8914588BB4EB0A7  platform/helper/course_helper/domain/__init__.py
EB9F52918513AD3693C53AD1A267C45FBA13A6F0F2F5DDDD75A92C8AA8051250  platform/helper/tests/test_composition_contracts.py
23D581C08184F14AADD523103B9FD25B311016834F6FC8BAE76841CAE3B82195  platform/helper/tests/fixtures/schema-v1/review-visual.json
```

### Final URL-identity re-review

The final reviewer found one remaining Important bypass: an equivalent legacy
final-media URL could change hostname case, add the default HTTPS port, or
percent-encode an unreserved path character and evade a raw string comparison.
The focused fixture test was extended first and reproduced the bypass as one
failure. URL deny-list identity comparison now normalizes scheme, IDNA/lowercase
hostname, default port, Unicode/percent-decoded normalized path, and query
pairs. Fresh verification after the fix:

```text
python -m pytest platform/helper/tests/test_composition_contracts.py platform/helper/tests/test_domain_contracts.py -q
47 passed in 0.40s

python -m pytest platform/helper/tests -m "not reference_demo" -q
333 passed, 4 skipped, 7 deselected in 47.51s
```

The previous post-review inventory is superseded for these three files only:

```text
83E7D98896E608A1242059EFABC644CAA10AD15D48DE15889FEC5D2EB43C6D2D  platform/helper/course_helper/domain/visual_policy.py
9323E7B4FDFE54F35C0F20298CB9694079A3AD6DC168CD41B9A8C8EE5D1EFE82  platform/helper/course_helper/domain/sources.py
BE0891088337A74225FEF00DBED40CA796D191554E3CA5843F863FEDB4E45B06  platform/helper/tests/test_composition_contracts.py
```

Final independent re-review: 0 Critical / 0 Important / 0 Minor. Task 1 is
approved to enter Task 2.
