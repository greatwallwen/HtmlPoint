# Adaptive Model Router Evaluation

Date: 2026-07-18  
Scope: live routing evidence for the personal AI course platform goal

## Baseline

- Skill source: `C:\Users\alvin\.codex\skills\adaptive-model-router\SKILL.md`
- Size: 60 lines / 510 words.
- `skill-creator/scripts/quick_validate.py`: PASS (`Skill is valid!`).
- `agents/openai.yaml` matches the intended display name, prompt, and implicit
  invocation policy.
- The skill file exists but was absent from this task's startup skill catalog;
  it can be read and followed in-session, but catalog discoverability may
  require a new task/app reload.

## Routing receipts

| Unit | Route | Execution | Evidence | Outcome |
|---|---|---|---|---|
| Task 4 Phase B authority/receipt | P3 Critical | strong coding/high implementation; strong-model audit, then one bounded `gpt-5.6-sol` ultra audit when a slot became available | final root receipt/QA 184/184, related 153/153, full Helper 602/602 selected; strong audit found 1 HIGH + 2 MEDIUM and ultra found 2 additional Important recovery-order issues | Correct escalation. All findings were fixed and independent final review closed Ready: Yes. Ultra follow-up was safety-classifier interrupted, so no false ultra PASS is claimed. |
| Task 4 Phase A bootstrap | P2 Deep | current strong model; exact tested wrapper | two producer attempts failed at the same connect symbol; curl showed Hugging Face timeout and PyPI HTTP 200 | Correctly treated as an external route failure, stopped retries, and did not spend ultra. No candidate/cache/receipt mutation. |
| Task 5 near-duplicate review | P2 Deep | strong coding/high, focused TDD and one root review | focused 130/130; related 98/98; full Helper 595/595 selected | Correct route. Two evidence-driven integrity corrections closed without an ultra-only failure. |
| Task 8 traceable draft | P2 implementation + bounded P3 review | recommendation only; no in-place switch | focused 65/65; full Helper 653/653 selected | Correctly avoided Ultra. Semantic lineage and lifecycle review closed at xhigh. |
| Task 9 source visuals | broad P3, split core P2 + bounded P3 review | recommendation only; no in-place switch | focused 212/212 selected; full Helper 670/670 selected | Split was better than keeping all media/storage construction at xhigh. Review found catalog/index schema coupling. |
| Task 10 dataset charts | broad P3, split core P2 + bounded P3 review | recommendation only; no in-place switch | focused 38/38 selected; full Helper 680/680 selected | Review found five material boundary issues; all closed without Ultra. |
| Task 11 network visual provenance | broad P3 ceiling, P2/high implementation and bounded local security review | recommendation only; no in-place switch | focused 224/224 selected plus one expected skip; repository QA Helper 708 selected/Web 244/typecheck/build | P2/high was sufficient for deterministic implementation. Review tightened active-content failure, canonical unique receipt bytes, fixed failure codes, and rollback. The live TLS timeout is environmental evidence, not a model failure; no Ultra escalation or repeated retry was justified. |
| Task 12 atomic course publication | P2/high implementation with bounded state-machine review | recommendation only; no in-place switch | plan-focused 49/49 selected plus one expected skip; composition 95/95 selected; full Helper 711 selected | P2/high handled deterministic visual binding and transactional publication. Review added exact runtime snapshots, successive immutable revisions, response-loss recovery, and late-failure rollback. No reasoning failure or Ultra-only finding occurred. |
| Task 13A governed upload/inventory | P2/high implementation; P1/medium recommended after close | recommendation only; no in-place switch | focused 19/19; migration regression 21/21; full Helper 731 selected plus 12 expected skips | P2/high closed lease/promotion/expiry concurrency and integrity. The only full-gate failures were two stale migration-version assertions, an input-drift correction rather than a reasoning failure. Ordinary API CRUD can move to P1/medium; keep disconnect/outcome recovery as a bounded P2 review. |
| Task 13B1 upload/inventory API | P1/medium | recommendation only; no in-place switch | endpoint/CORS 14/14; API/server/upload/inventory 74/74 | P1 was adequate for strict endpoint wiring. Review found one low-probability token-shape mismatch and replaced raw-token ownership with a derived safe ID. No P2 promotion trigger occurred; durable disconnect/outcome recovery remains a separate P2 unit. |
| Task 13B2 import operation recovery | router P3/xhigh after initial P2 estimate | recommendation only; no in-place switch | focused import/upload 27 selected plus one permission skip; final operation/job/API 97 selected plus one permission skip | The promotion was justified: bounded review found reparse-shard isolation, post-commit promotion cleanup, and cancel-recovery mutation risks. Deterministic delayed-response and real spawn tests closed them without Ultra. Downgrade after this isolated boundary. |

## Evaluation criteria

For each work unit record: selected profile, concrete promotion trigger, model
and effort actually used, focused/broad verification, non-trivial correction
rounds, environmental failures, and whether a lower-cost profile would likely
have produced the same verified result. Exact token accounting is unavailable,
so use test scope, correction rounds, delegated-agent count, and elapsed tool
work only as qualitative cost proxies.

## Early strengths

- Routes the next work unit instead of keeping the whole goal on an expensive
  profile.
- Correctly isolates security/hardware review from ordinary UI and test work.
- Explicitly forbids claiming a model switch that did not occur.
- Its token controls match this repository's targeted-read and focused-test
  rules.

## Early weaknesses to validate before editing

- No explicit distinction between implementation profile and independent
  reviewer profile; Task 4 benefited from high implementation while only the
  final adversarial pass justified ultra.
- No rule that environment/permission/network failures must not count as
  evidence-based model failures.
- No preflight/fallback when the desired model or agent slot is unavailable.
- No compact routing-receipt schema, making longitudinal cost/quality
  comparison subjective.

## Task 4 audit update

The P3 route paid for itself: ordinary focused suites were green, while the
strong-model adversarial pass found a receipt-integrity race plus timestamp and
write-evidence weaknesses. The later ultra pass found two more Important
failure-order/prior-recovery gaps that the first pass missed. This supports
using ultra for one bounded final review of security-sensitive state machines;
it does **not** justify keeping the whole goal on ultra. After the Task 4 live
gate, the recommended default is a lower-cost P1/P2 model, with P3 promoted
only for another bounded security, hardware-certification, or concurrency
review.

Do not revise the skill from these hypotheses alone. Re-evaluate after Task 5
and one P1 UI work unit, then patch only rules supported by at least two
different work-unit outcomes and rerun `quick_validate.py`.

## Task 8-10 update

Three independent implementation units now support one change to the routing
guidance: a broad `security=true` signal should choose the review ceiling, not
automatically force every deterministic construction step to P3. Task 9 and
Task 10 both benefited from P2/high implementation plus a bounded P3/xhigh
review, and both reviews found material issues. Ultra remained unnecessary.
The missing in-place switch continues to be the main operational limitation;
all recent receipts correctly remain `recommended_only`.

Task 11 adds a useful negative result: an external TLS timeout must not promote
the route or count as a reasoning failure. The lowest reliable profile remains
P2/high for Task 12 construction, with a bounded P3 review only when the atomic
publication state machine and rights/freshness matrix are complete.

Task 12 confirms the revised routing rule on a security-adjacent state machine:
P2/high plus focused adversarial tests was sufficient. A P3 review ceiling is
still appropriate for later API/browser publication, but there is no evidence
that Ultra would improve the current deterministic implementation unit.

Task 13A adds a clean downgrade point. The risky upload/lease/promotion state
machine passed focused concurrency, rollback, replay, privacy, and full Helper
gates after one mechanical migration-expectation correction. The next ordinary
API schema/CRUD batch should use P1/medium on the balanced model, while the
disconnect-versus-committed-outcome boundary remains a separate P2/high review.
The task still cannot execute an in-place model switch, so this is a
`recommended_only` route, not a claim that the root model changed.

Task 13B3 confirms that downgrade. Bounded authenticated review and upgrade
read projections passed 91 focused job/API tests under the P1/medium route.
No retries, architectural surprises, or security-boundary escalation occurred;
Ultra would have added cost without new evidence. The next mutation/recovery
boundary must be routed independently rather than inheriting this low tier.

The subsequent Task 4 adversarial verification also completed at P1/medium:
123 passed with one Windows permission-dependent attack-fixture skip. It found
no defect and required no rework. For deterministic, already-covered recovery
and authorization gates, escalating merely because test names mention timeout
or authentication would waste tokens. Escalation remains reserved for new
mutation logic or an attributed reasoning failure.

Task 13B4 validated the split-route rule. The coupled mutation construction was
routed P2/high and its production-path tests found three concrete integration
issues: frozen array result transport, a random-token test fixture mismatch,
and pre-auth domain reads. All were deterministic and closed without Ultra.
The next import parser/candidate phase should route independently rather than
keeping the entire project at P2.

## Evidence-backed skill revision

The P2 model guidance now defaults implementation to `high` and reserves
`xhigh` for a bounded synthesis/review with hidden coupling, unusually large
cross-file state, or an evidence-based reasoning failure. This is the smallest
change supported by Tasks 8-10 and avoids weakening P3 review. Skill validation
passes. Revised `SKILL.md` SHA-256:
`E69F807A92830253072ECEAF5B0752521217DEFFF6EDFF882281E699686B01D3`.

## Dual-screen Task 6A audit

The route was P3 with `gpt-5.6-sol/xhigh` recommended, but execution remained
`recommended_only`; no in-place model switch is claimed. That review ceiling
was justified: two bounded security passes found four Important launch,
containment, and stale-state defects plus two protocol-tightening issues that
the first green suites did not expose. Three correction rounds closed those
findings, the double-base64 chunk ceiling, cancellation interruption, local
.NET runtime policy, and graceful temp cleanup. Final evidence was 24 focused
Helper/publication tests with 2 integration-gated skips, 36 .NET tests, and 23
explicit real-Host tests with no orphan or new temp directory.

This strengthens the current split-route rule: deterministic construction can
stay below the review ceiling, while executable identity, child-process
containment, and authenticated transport deserve a bounded P3 review. Ultra
still has no demonstrated marginal benefit here; the independent P3-class
review converged without an unresolved Critical/Important issue.

## Dual-screen Task 6B audit

The route remained P3 with `gpt-5.6-sol/xhigh` recommended and
`recommended_only`; no model switch is claimed. The bounded security review
found three Important issues that green functional suites did not expose:
catalog/WAL identity replacement, duplicate artifact-binding overwrite, and a
policy stat/read replacement window. Two follow-up passes closed all three,
including the subtler constructor and sidecar lease interval. Final affected
verification passed 105 tests with 2 existing integration-gated skips and no
cache recreation.

The fixed-date publication fixture failure was environment/input drift, not a
reasoning failure, so it correctly did not trigger an upgrade. Task 6B again
supports P3/xhigh as a bounded review ceiling for filesystem identity and
publication trust boundaries. Ultra is still unnecessary: deterministic TDD
plus independent P3 review converged to zero Critical/Important findings. Task
7 should route independently and can use a lower construction profile, with a
bounded security review for authenticated dispatch and replay handling.

## Dual-screen Task 7 audit

The deterministic schema and dispatch construction was small, but the router
classified the authenticated command boundary as P3 and recommended
`gpt-5.6-sol/xhigh`; execution remained `recommended_only`. The bounded review
ceiling was justified. It found that an asyncio timeout could return while the
worker thread later mutated Host state, that synchronous global cancellation
could block the event loop or affect another queued command, and that repeated
Task cancellation could interrupt cleanup. Three correction rounds produced a
single pre-supervisor gate plus independent shielded cancel-and-join cleanup.
The final review found no Critical/Important issue, and the full offline Helper
gate passed 899 tests.

This unit refines the split-route evidence: ordinary strict Pydantic models are
lower-profile work, but their integration with cancellation, threads, and a
stateful native supervisor warrants one P3 review. Ultra remains unnecessary;
the failures were deterministic and converged under targeted concurrency
probes. Task 8 UI/reducer construction should route lower, reserving P3 only
for its final hardware-certification boundary review.
