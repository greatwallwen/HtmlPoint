# Platform expectation reflection

Date: 2026-07-19 (Asia/Shanghai)

## Verdict

The platform now meets the expected standard for a **local personal desktop
pilot**: one person can govern source ingestion, evolve tagged knowledge cards,
compose and publish an evidence-bound course, and open consistent Editor,
Stage, and Presenter projections in the bright desktop UI.

It does **not** yet meet the stronger standard of a fully certified real-world
deployment. Physical Win11 dual-screen placement/fullscreen behavior, current
live-network visual authorization, protected reference-library ingestion, and a
signed one-click desktop distribution remain outside the verified boundary.

## Evidence that supports the verdict

| Expected capability | Current evidence | Verdict |
| --- | --- | --- |
| Governed personal knowledge workflow | MD, CSV, and PPTX import; review; publication; exact index wait exercised by real Chrome | Verified locally |
| Reusable multi-tag knowledge | Versioned cards, tags, lifecycle, retrieval snapshot, gap evidence, and course recomposition contracts | Verified locally |
| Authentic course visuals | PPTX source image, digest-bound dataset chart, and licensed network fixture all rendered as decoded images | Verified in fixture-backed loopback |
| Stable course publication | Validation, idempotent publish replay, byte-bound reopen, and immutable projection IDs | Verified locally |
| Consistent teaching views | Editor, Stage, and Presenter reopen the same Slide AST bindings | Verified locally |
| Concise bright desktop UI | Light-theme/token/design gates pass; browser screenshots were visually reviewed | Verified for desktop viewport |
| Win11 multi-screen readiness | Screen detection and projection state-machine paths exist; separate teaching windows open | Implemented, physical hardware NOT CERTIFIED |
| Current online visual truth | Provenance and revalidation contracts exist | Historical/fixture evidence only; current authorization NOT CERTIFIED |
| Protected local reference demo | Existing sealed receipt remains verifiable | Not rerun under the current protected-root boundary |

Final acceptance evidence:

- `python platform/qa/run.py all`: exit 0.
- Python QA: 167 passed.
- Helper offline suite: 785 passed, 6 skipped, 7 deselected.
- Web suite: 279 passed across 25 files.
- TypeScript typecheck and Vite production build: passed.
- Real system-Chrome E2E: 1 passed in 2.9 minutes.
- Browser receipt SHA-256:
  `D750E059839EE621B9D605776861E3671D013F42780AE295037511A45087C28E`.
- Protected roots were not staged or accessed; physical dual-screen and live
  network certification flags remain false in the receipt.

## What still feels less simple than the product promise

1. Internal UUIDs and other technical identifiers still leak into ordinary
   user-facing screens. They increase cognitive load without helping a personal
   course author.
2. Generated labels such as `Knowledge unit 1` are structurally correct but
   insufficiently meaningful. The platform should derive human-readable names
   from the governed card/topic evidence.
3. Review is safe but too sequential and manual. A personal workflow needs a
   compact review inbox, sensible defaults, and bulk approval only where the
   evidence class is identical.
4. Visual placement is evidence-correct but not yet compositionally polished;
   several visuals can accumulate on one slide instead of being automatically
   distributed across an effective teaching narrative.
5. The verified course is intentionally small. Longer-course pacing, richer
   lesson structure, and upgrade behavior need product-level evaluation rather
   than more schema work.
6. Browser E2E takes 2.9 minutes because the local Helper lifecycle is exercised
   end to end. This is acceptable as a release gate but too slow for every tiny
   UI edit.

## Root-cause reflection

The platform previously drifted because infrastructure proof and product
simplicity were treated as equivalent. The evidence architecture is now strong,
but a personal user experiences names, defaults, ordering, and the number of
decisions—not schemas and digests. The next iteration should preserve the
evidence boundary while removing visible machinery.

The smallest high-value direction is therefore a **one-click personal course
workflow**: friendly naming, a short review inbox, automatic lesson/visual
layout, and progressive disclosure of evidence details. Physical dual-screen
certification should remain a separate hardware acceptance track, not block
this usability improvement.

## Model-routing reflection

P3/xhigh was justified for authentication, artifact integrity, idempotent
publication, and protected-path corrections. No failure required Ultra. The
next phase is product discovery and bounded UX planning, so P1 with
`gpt-5.6-terra` at medium reasoning is the lowest-cost reliable route. Escalate
only if the brainstorm selects physical multi-monitor automation or another
security-sensitive cross-system integration.

