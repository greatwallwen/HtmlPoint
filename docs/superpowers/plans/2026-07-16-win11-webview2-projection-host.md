# Win11 WebView2 Dual-Screen Projection Host Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`, `superpowers:test-driven-development` for every production change, and `superpowers:systematic-debugging` for runtime failures. Stop at each task boundary if its focused checks are not green. Checkboxes are the execution record.

**Goal:** Add deterministic Win11 screen enumeration and dual-window control through a secure .NET 10 WPF WebView2 sidecar while preserving the existing browser rehearsal fallback. Automated verification and real physical certification remain separate; no screen count, virtual test, or simulated event may certify physical dual-screen output.

**Architecture:** The Chrome course workspace communicates only with the authenticated loopback Python Helper. The prerequisite course-composition release returns the first Helper-issued `runtimeManifestId`; the Helper owns the verified application session, resolves that immutable ID into a bounded `ProjectionBootstrap` plus hashed `ProjectionAssetBundle`, supervises one fixed projection-host executable, and journals commands, teaching frames, host events, and runtime evidence. Development may run source Python and is always non-certifying; certifying mode requires the exact packaged Helper executable/runtime/dependencies to be covered by the same signed release catalog and launched under fixed policy. The WPF host owns two HWND-backed WebView2 windows, Win32 topology, exact placement/fullscreen checks, a fixed built web bundle, session-scoped verified visual assets, and the native bridge. Chrome and WebView2 deliberately use separate browser profiles; teaching state crosses Chrome → Helper → private host pipe → WebView2. BroadcastChannel/localStorage is only an optimization between the two WebView2 roles, never the Chrome-to-host authority.

**Tech stack:** .NET SDK 10.0.301 (`net10.0-windows`, Active LTS through 2028-11-14), C# 14, WPF, Microsoft.Web.WebView2 SDK 1.0.4078.44, an exact hash-locked WebView2 Fixed Version Runtime for certifying mode, Microsoft.NET.Test.Sdk 18.8.1, MSTest 4.3.2, Win32, System.Text.Json, Python 3.12/FastAPI, React 19/TypeScript/Zod/Vitest.

**Primary references:** [.NET 10 download](https://dotnet.microsoft.com/en-us/download/dotnet/10.0), [.NET support policy](https://dotnet.microsoft.com/en-us/platform/support/policy), [WPF WebView2 guide](https://learn.microsoft.com/en-us/microsoft-edge/webview2/get-started/wpf), [WebView2 security guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security), [WebView2 user-data folders](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder), [WebView2 process events](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-related-events), [Win32 display enumeration](https://learn.microsoft.com/en-us/windows/win32/gdi/enumeration-and-display-control), and [PerMonitorV2 manifests](https://learn.microsoft.com/en-us/windows/win32/sbscs/application-manifests).

## Current environment evidence

- Windows 11 Pro x64 build 26200 is present.
- Win32/desktop APIs currently enumerate two monitor records: primary `1536x864` and secondary `1920x1080`.
- WebView2 Evergreen Runtime `132.0.2957.140`, Visual Studio Build Tools/MSBuild, and Windows SDK 10.0.26100.0 are present. The Evergreen runtime is useful development evidence only; it is mutable external state and never enters certifying mode.
- The global `dotnet` host has .NET 6 runtimes but no SDK.
- These facts are implementation prerequisites only. They do not establish two physical panels, extended-mode authenticity, correct role placement, or human observation. The starting and default status is `NOT CERTIFIED`.

## Non-negotiable boundaries

- Work only in `D:/cursor/AI培训/.worktrees/course-studio` on `codex/course-studio-light`.
- Never read, copy, or modify `Course_AIProduct/`. This plan never accesses `references/`; `COURSE_REFERENCE_ROOT` remains unset.
- Install the SDK only under ignored `.tools/dotnet`; never change system PATH, install a global workload, or commit the SDK/NuGet cache.
- No QA command downloads the SDK or packages implicitly. Toolchain acquisition is a distinct, logged prerequisite step.
- Browser input is limited to the course plan's governed publish/prepare jobs, six approved projection commands, and typed teaching-session frames/controls. It never supplies an executable path, command line, HWND, device path, UDF path, arbitrary URL/origin, shell text, WebView2 switch, raw RuntimeManifest, or asset bytes.
- The Helper launches only an installation-relative executable validated by policy, signed release manifest when certifying, containment, and hashes. `shell=False`; no user-controlled arguments.
- The reverse trust edge is mandatory for certification: Host and Witness CLI identify the named-pipe server PID and validate the exact contained packaged Helper executable, `helperBuildDigest`, signed-catalog membership, and pinned Authenticode publisher. A source-Python, editable, externally launched, or unverifiable Helper is functional development mode only and can never set `physicalDualScreenCertified=true`.
- The host may enumerate displays and manage only its own windows. It must never change resolution, primary display, clone/extend mode, brightness, color profile, or any system display setting.
- Contract coordinates are physical pixels. PerMonitorV2 must be asserted at runtime before the first HWND.
- Topology/DPI/window/navigation/process/pipe changes, heartbeat loss/staleness, or frame mismatch/rollback invalidate readiness and certification immediately. A valid monotonic teaching-frame advance is instead a bounded `syncing` transition: readiness pauses while either role is behind and automatically resumes only after both roles commit the same latest frame under unchanged topology/window/navigation/release identity.
- Browser fallback, fake adapters, RDP, remote/indirect/virtual displays, DevTools, two windows on one monitor, and unattended automation can never set `physicalDualScreenCertified=true`.
- Built binaries, publish output, UDFs, crash dumps, screenshots, SDKs, and caches remain ignored. Commit source, lock files, policy, schemas, fixtures, and bounded audit receipts only.
- Certifying mode launches only the release-relative, exact locked WebView2 Fixed Version Runtime through `browserExecutableFolder`; it never falls back to Evergreen. The full fixed-runtime file inventory/digest and Microsoft signatures are part of the combined signed release evidence. Missing, extra, changed, reparse-linked, or wrong-version runtime files fail closed. Development fallback to Evergreen is explicitly non-certifying.
- Stage exact files for each commit; never use `git add -A` or broad `git add -- platform/windows`.

## Stable runtime contracts

### Trusted application and teaching flow

```text
Chrome workspace (profile A)
  | authenticated loopback HTTP + WebSocket
  | six control commands + typed TeachingFrame/TeachingControl events
  v
Python Helper (session/evidence authority)
  | course_publish produced runtimeManifestId/courseProjectionId
  | resolves immutable ProjectionBootstrap + ProjectionAssetBundle
  | validates pinned Slide AST + RuntimeManifest digests
  | fixed child + private binary pipes + HMAC launch handshake
  v
.NET WPF ProjectionHost
  | fixed built bundle mapped to https://projection.course-studio.test
  | injects bounded ProjectionBootstrap after trusted navigation
  |-- Stage HWND + WebView2 (profile B, fixed /projection/stage)
  `-- Presenter HWND + WebView2 (profile B, fixed /projection/presenter)

WebView2 role-to-role optimization: BroadcastChannel + latest-frame localStorage
Authority: Helper journal and private host channel
```

- The upstream `course_publish` job from the course-composition plan validates a confirmed course, atomically stores `SlideDeckAst` and `RuntimeManifest`, and returns the first Helper-issued `runtimeManifestId + runtimeManifestDigest + courseProjectionId`. Reopening the same published course must return the same IDs and bytes. This plan does not accept a browser-authored manifest.
- `open_projection_session` carries only that Helper-issued `runtimeManifestId` or `courseProjectionId` plus an expected digest. The Helper resolves and validates immutable content and a bounded asset manifest before launching the host.
- `ProjectionBootstrap` contains bounded, already-validated Slide AST/RuntimeManifest view data, course/deck/runtime IDs and digests, role policy, and initial frame. It contains no source path, token, arbitrary HTML, command, or external URL.
- Every bootstrap, session receipt, host handshake, witness lease/challenge, runtime evidence object, and certification status binds exact `helperBuildDigest` and `webView2RuntimeDigest` in addition to host/web/course/runtime-manifest digests. A Helper build or WebView2 runtime/process identity change invalidates the session and cannot be repaired by copying an older receipt.
- `ProjectionAssetBundle` contains opaque asset IDs, MIME, byte length, content digest, alt text and attribution IDs. The Helper streams verified bytes over the private pipe into a session cache; the host exposes only digest-checked same-origin `/session-assets/{opaqueId}` responses. No WebView performs a network artifact fetch.
- Asset transfer uses the same private anonymous-pipe handles but a typed LF-JSON envelope, never an ad-hoc binary side channel: `asset_begin`, ordered `asset_chunk`, `asset_ack`, `asset_commit`, and `asset_cancel`. A bundle has at most 128 assets and 96 MiB decoded bytes; each asset is at most 20 MiB. Each chunk carries canonical base64 for at most 48 KiB decoded bytes plus transfer/asset ID, zero-based sequence, offset, decoded length, schema version, and expected total/digest. The transport hard-rejects a line above 524,288 bytes before decode; bootstrap is at most 512 KiB, command/event/control is at most 64 KiB, and an asset-chunk envelope is at most 72 KiB.
- Transfer is strictly sequential with at most four unacknowledged chunks (192 KiB decoded) and a two-second progress timeout. The host incrementally validates sequence, offset, base64, declared/decoded lengths, per-asset and bundle ceilings, MIME, and SHA-256; it writes a contained temporary session-cache file and atomically exposes the opaque asset only after `asset_commit`. Duplicate begin/chunk/commit is idempotent only when every digest/byte count matches. Mismatch, cancel, timeout, pipe close, session close, or backpressure failure deletes the temporary bytes, emits a stable scrubbed error, and cannot navigate a WebView.
- Bootstrap/navigation ordering is fixed: the Host receives and validates bootstrap metadata but does not inject it; it completes and commits every declared asset, verifies the full bundle digest, and only then performs trusted role navigation. After exact origin/path navigation completes, it injects the held bootstrap and waits for role readiness. Any asset failure occurs before navigation and closes the session; no partial bundle or bootstrap reaches a WebView.
- `TeachingFrame` and `TeachingControl` are typed bidirectional session events, not a seventh projection command. They cross Chrome ↔ Helper WebSocket ↔ host pipe ↔ fixed WebViews and have monotonic sequence, optimistic session/course digest, and replay rules.
- Stage/Presenter never query the knowledge catalog and never depend on Chrome localStorage. They render only the injected bootstrap and subsequent typed frames.

### Six projection commands

1. `detect_displays`
2. `open_projection_session`
3. `assign_projection_window`
4. `enter_projection_fullscreen`
5. `verify_projection_assignment`
6. `close_projection_session`

Every command has schema version, UUID command/session IDs, expected topology version, strict payload, and idempotent receipt. Duplicate `commandId` returns the original receipt. A stale topology/session/course/navigation generation fails closed.

The challenge response is two distinct idempotent command operations. The first verify request uses `commandId=A` and may return `confirmationChallengeId=C`; the paired-code submission must use a new `commandId=B` and carry `C`. Replaying `B` returns its original receipt, while reusing `A`, omitting/mismatching `C`, or using a third command ID after `C` is consumed fails closed.

### Display and certification semantics

- `DisplayTopology` reports `single|extended|duplicate|remote|unknown`, session-HMAC display IDs, physical-pixel bounds/work area, DPI/scale, primary/internal hints, `hardwareCandidate`, and `noKnownVirtualIndicator`. It never reports an automatically proven `physical=true`. The Helper generates the HMAC salt per application session and sends it through the private bootstrap pipe; a sidecar restart preserves IDs within that Helper session, while a Helper restart invalidates all display IDs.
- Raw adapter/target IDs, EDID, PnP paths, monitor device paths, and friendly device identifiers remain inside the host and never enter logs/receipts/browser data.
- Automated eligibility requires extended topology, two distinct eligible display IDs, distinct host-bound role windows, exact geometry/fullscreen/visibility, matching latest frame, current heartbeats, trusted bundle/runtime digests, and no invalidation.
- After an eligible projection session is already open, `projection-hardware` alone creates an in-memory `HardwareWitnessLease`, bound to that exact session, the local Windows console session, invoking process, Helper process, topology/windows/roles/release identity, and a 10-minute TTL. The browser cannot create, receive, renew, or enumerate the lease; it only sees whether the current projection session is witness-eligible.
- The existing Helper owns a separate local witness-control named pipe; witness creation is not exposed by HTTP/WebSocket and never starts a second Helper. The pipe uses a per-Helper random name and capability stored only in current-user ACL-protected app data, `PIPE_REJECT_REMOTE_CLIENTS`, a DACL limited to the current logon SID and SYSTEM, a one-client limit, and a two-second authenticated bootstrap. Browser responses/logs never reveal its name or capability.
- `python platform/qa/run.py projection-hardware` is only a wrapper that verifies and visibly launches the fixed `CourseStudio.ProjectionWitnessCli.exe`. The Helper uses `GetNamedPipeClientProcessId` plus process/token inspection to require that exact contained executable, release-manifest hash, pinned Authenticode publisher, same user SID, same active local-console session, non-AppContainer token, and non-RDP/non-remote state. The CLI itself requires a real console, visible foreground confirmation, and one explicit consent phrase; it selects no browser-supplied session and the Helper requires exactly one currently eligible session. The CLI keeps the pipe open and blocks for the workflow lifetime. EOF, Ctrl+C, CLI exit, console switch, pipe failure, or Helper exit immediately revokes the lease/challenge and zeroizes capability material.
- The first `verify_projection_assignment` may return `eligible` and a single-use `confirmationChallengeId`. The Helper generates distinct cryptographically random stage/presenter codes, retains only salted HMAC digests, and sends code values only over the private host pipe to native role-bound overlays. Code values are never returned in API/events/logs.
- The second phase uses a new `commandId` plus the first receipt's `confirmationChallengeId`, supplies the human-entered codes, and consumes the challenge. The challenge is bound to witness session, topology, assignments, physical rectangles, current navigation generations, helper/web/host/WebView2-runtime/runtime-manifest/course digests, and a short TTL. The local client may reject malformed code format before transmission, but there is exactly one server-side paired-code submission. Any mismatch, replay under a different command ID, topology/window/role/navigation/release/WebView2-process/Helper-process change, heartbeat loss, frame mismatch/rollback, disconnect, or timeout consumes both challenge and lease. A new local TTY invocation is required; codes are compared in constant time and are never persisted.
- Normal monotonic frame advances do not revoke the lease or the operator's role/topology witness. They temporarily set the projection to `syncing`; certification is false until both role-bound WebViews report the same latest committed frame and window/heartbeat checks pass again. Only abnormal bound-state changes listed above revoke the lease. Thus an operator does not re-enter codes on every slide, but stale or divergent rendering can never remain certified.
- `operatorWitnessed` is available only during the explicit interactive `projection-hardware` workflow with a local TTY/consent session. Normal browser automation can exercise eligibility and code validation but remains non-certifying.
- A persisted JSON receipt is audit evidence only and never restores active certification after Helper exit. Active trust is session-scoped. Cross-session release identity additionally requires a signed release manifest and Authenticode/pinned publisher policy; without signing authority the status remains `NOT CERTIFIED`.

### Render evidence semantics

- `messageAccepted`: the role validated and queued a frame.
- `domCommitted`: React committed the frame-dependent DOM and emitted `frame_committed` from a `useLayoutEffect` followed by two `requestAnimationFrame` callbacks through an injectable scheduler. This is a deterministic DOM/presentation boundary, not pixel proof.
- `windowVisible`: the host verified HWND visibility, non-minimized/non-cloaked state, target monitor, and exact DWM/window bounds.
- `operatorWitnessed`: an interactive operator confirmed both visible role codes under the current challenge.
- Role identity is bound by the host window/bridge instance, never trusted from web payload. `messageAccepted` or `domCommitted` alone never proves visible pixels.

## Planned durable surfaces

```text
global.json
platform/contracts/projection/v1/{schemas,fixtures}
platform/windows/
  Directory.Build.props
  Directory.Packages.props
  CourseStudio.ProjectionHost.slnx
  host-policy.json
  webview2-fixed-runtime.lock.json
  src/CourseStudio.ProjectionHost.Core/
  src/CourseStudio.ProjectionHost/
  src/CourseStudio.ProjectionWitnessCli/
  tests/
  evidence/
platform/helper/course_helper/{domain/projection.py,projection_host.py,projection_events.py}
platform/helper/packaging/{CourseStudio.Helper.spec,helper-packaging.lock.json,helper-release-manifest.schema.json}
platform/web/src/{domain/projection*,services/projection-client*,components/TeachingSetup*}
platform/qa/{run.py,test_run.py}
```

---

### Task 0: Re-establish Git identity and verify a no-side-effect preflight

**Files:** none.

- [ ] Run `git rev-parse --is-inside-work-tree`, `git rev-parse --show-toplevel`, `git branch --show-current`, `git worktree list --porcelain`, and `git status --short --branch` from the intended worktree.
- [ ] Require exact path `D:/cursor/AI培训/.worktrees/course-studio` and branch `codex/course-studio-light`. If `.git` is absent or wrong, stop before download/edit; restore only from upstream-authorized metadata and never run `git init`.
- [ ] Verify the authoritative reboot plan SHA-256 is `49264B9B1A57F241730BF5839DA871C0165A100E92565040BC860132A2E93E80`, compare this plan with its committed Git blob, and inventory the dirty tree.
- [ ] Require the course-composition plan through its real API/UI integration milestone to be complete. Run its focused gate and prove `course_publish` returns a persisted `runtimeManifestId`, `runtimeManifestDigest`, and `courseProjectionId`; resolve the ID after Helper restart and require byte-identical Slide AST/RuntimeManifest. If this upstream contract is absent, stop before Task 1.
- [ ] Confirm `COURSE_REFERENCE_ROOT`, desktop/hardware smoke flags, and any SDK-download flag are unset. Record OS, architecture, WebView2 Runtime, Build Tools/SDK, and global/local dotnet evidence without opening windows.

---

### Task 1: Acquire the local SDK/packages reproducibly and define cross-language contracts

**Files:** create/modify `global.json`, `.gitignore`, `platform/windows/README.md`, `platform/windows/webview2-fixed-runtime.lock.json`, build/package props, solution/projects/tests skeleton including `CourseStudio.ProjectionWitnessCli`, projection schemas/fixtures, Pydantic and Zod contracts/tests; create `platform/helper/packaging/helper-packaging.lock.json` and manifest schema; add the focused `projection-restore` prerequisite and tests in `platform/qa/run.py` and `platform/qa/test_run.py`.

- [ ] Before any RED test, create README, solution, project/test skeleton, empty interfaces, and schema directories so every command resolves.
- [ ] Toolchain acquisition is explicit and separate: save the official `dotnet-install.ps1` under ignored `.tools/bootstrap` (never pipe-to-execute), retrieve official .NET 10 release metadata, record URLs and hashes, and verify the SDK 10.0.301 Windows x64 archive SHA-512 before/installing under `.tools/dotnet -NoPath`. Never modify PATH.
- [ ] Verify `.tools/dotnet/dotnet.exe --info`; every later command uses that exact executable. QA fails with a prerequisite result if it is absent and never downloads it.
- [ ] Write RED parity tests for the six commands, receipts, topology, `ProjectionBootstrap`, `ProjectionAssetBundle`, all five asset-transfer envelopes, teaching frame/control, host events, render stages, challenge, exact per-kind/aggregate byte ceilings, strict extras/base64, bool-as-int, finite physical rectangles/DPI, unique IDs, sequence/offset/backpressure state, and stable errors.
- [ ] Add an explicit networked `python platform/qa/run.py projection-restore` prerequisite guarded by `COURSE_PROJECTION_RESTORE=1`. Project files declare `RuntimeIdentifiers=win-x64`; the prerequisite restores and locks the SDK reference packs, WPF/WindowsDesktop packs, NuGet packages, WebView2 native assets, and the complete `win-x64` self-contained host/runtime packs required by publish. It downloads the exact official x64 WebView2 Fixed Version Runtime named by a committed lock (version, official URL, archive SHA-256, expected file inventory, Microsoft signer identities), verifies archive/member hashes and signatures, and expands it only into ignored cache; Evergreen is never copied. It also builds an ignored offline wheelhouse from a hash-locked exact CPython 3.12 patch, Helper dependencies, and one exact pinned `onedir` packager version; floating packages/index fallback are forbidden. It records exact package source/cache/lock/RID/wheel/fixed-runtime digests, then performs a locked `win-x64` restore, `pip --require-hashes --no-index` package preflight, fixed-runtime inventory verification, and dry-run publish. Unset the flag. Every later build/test/publish gate is `--no-restore`/`--no-index`, verifies those recorded cache/RID/wheel/runtime digests first, and must pass with sockets denied.
- [ ] Run RED in .NET/Python/Vitest, implement strict C#/Pydantic/Zod models, then run GREEN and locked restore. Core remains WPF-free.
- [ ] Stage exact project/schema/contract/test/lock and focused restore-gate files; commit `test(projection): define secure cross-language contracts`.

---

### Task 2: Build the pure projection state machine and bootstrap reducer

**Files:** create Core contracts/reducer/coordinator interfaces and focused Core tests.

- [ ] Write RED tests for all topology modes; command replay; invalid/stale transitions; open/reuse; immutable bootstrap/bundle digest; begin/chunk/ack/commit/cancel and idempotent replay; unique host-bound roles; assignment/fullscreen; teaching-frame ordering/replay; normal `syncing`/both-role recommit versus mismatch/rollback revocation; message/DOM/window evidence; single-attempt challenge creation/consumption/expiry; cancellation; close; crash/topology invalidation; and cleanup.
- [ ] No fake adapter or test clock path may set `operatorWitnessed` or physical certification.
- [ ] Implement immutable records, one serialized queue, monotonic events, bounded journal, injected clock/random/challenge policy, stable error codes, and deterministic receipt digests. Keep Win32/WPF/WebView2/process calls behind interfaces.
- [ ] Replay seeded command/topology/frame sequences 100 times and require byte-identical receipts. Run Core tests and `git diff --check`.
- [ ] Stage exact Core/test files; commit `feat(projection): add deterministic host state machine`.

---

### Task 3: Enumerate Win11 topology without claiming physical proof

**Files:** create native methods/provider and Windows adapter tests; create `app.manifest`.

- [ ] Write RED injectable mapping tests for `EnumDisplayMonitors`, `GetMonitorInfo`, `QueryDisplayConfig`, `DisplayConfigGetDeviceInfo`, negative coordinates, rotation, mixed DPI, clone/extended/remote/indirect/virtual indicators, missing metadata, overflow/errors, and stable session-HMAC topology digest.
- [ ] Declare PerMonitorV2 and call/assert the effective DPI awareness context before the first HWND. Failure stops window creation.
- [ ] Implement physical-pixel topology with `hardwareCandidate`/`noKnownVirtualIndicator`; never infer physical truth. Native failure returns `unknown` evidence.
- [ ] Run focused tests and a read-only detect smoke. The current expected observation is two monitor records, not two proven physical screens; receipt remains uncertified and contains no raw device identity.
- [ ] Stage exact native/provider/manifest/test files; commit `feat(projection): enumerate candidate Win11 displays`.

---

### Task 4: Manage stage/presenter HWNDs with exact DPI and visibility checks

**Files:** create Stage/Presenter WPF windows, role-bound native `HardwareWitnessOverlay`, Win32 window adapter, native tests.

- [ ] Write RED tests for one window per fixed role, idempotent reuse, cross-role rejection, negative/mixed-DPI placement, `WM_DPICHANGED` suggested rectangle, style restore, user close, partial rollback, and process cleanup.
- [ ] Use physical pixels and Win32 placement only; do not independently apply WPF DIP geometry. After HWND creation use `GetDpiForWindow` and revalidate the target monitor after every DPI/topology change.
- [ ] Fullscreen applies/restores `WS_POPUP` and `SWP_FRAMECHANGED`; verification checks `GetWindowRect`, `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`, `MonitorFromWindow`, `IsIconic`, `IsWindowVisible`, and `DWMWA_CLOAKED` against one documented target rectangle policy.
- [ ] Define and test a host-owned, non-WebView `HardwareWitnessOverlay` for each fixed role. It accepts code material only from the authenticated private session source, is visually role-distinct and bound to the owning role window/monitor, cannot receive keyboard/input focus or expose a browser/automation API, preserves the prior local TTY/controller focus, and zeroizes/hides its code on challenge consumption, expiry, invalidation, navigation, window close, or process shutdown.
- [ ] Add a source/IL gate forbidding `ChangeDisplaySettings*`, `SetDisplayConfig`, `DisplaySwitch.exe`, PowerShell/WMI display-mode changes, and equivalent system-configuration paths.
- [ ] Run focused tests only; visible two-monitor smoke is deferred to an explicit later gate. Stage exact files; commit `feat(projection): place verified role windows`.

---

### Task 5: Serve a fixed trusted web build/assets and isolate WebView2 user data

**Files:** create trusted bundle/asset-manifest/UDF/WebView factory classes and native tests; modify WPF windows; create `host-policy.json`; modify `platform/web/vite.config.ts`, projection entry HTML/routes, CSP, and focused tests.

- [ ] Write RED tests for exact bundle file manifest/digest, fixed role paths, fixed-runtime version/inventory/path/hash/signature and `browserExecutableFolder`, forbidden Evergreen fallback in certifying mode, wrong WebView2 subprocess image, runtime process change, unknown file/symlink/reparse escape, navigation generation, origin/path confusion, new windows, downloads, permissions, external schemes, host objects, DevTools, arbitrary script, and token-bearing URLs.
- [ ] Build real `stage.html` and `presenter.html` entries (or another committed file-exact multi-entry contract) and map the installation-relative bundle to `https://projection.course-studio.test` with `CoreWebView2HostResourceAccessKind.Deny`. Do not rely on SPA fallback or a path that does not exist. Vite/loopback HTTP is developer automation only and can never certify.
- [ ] Verify the entire built web bundle against the release manifest and the complete session asset bundle against its bootstrap manifest before navigation. Hold bootstrap content outside WebView until every asset commits; any missing/corrupt/cancelled asset prevents navigation. Every later native/web message checks exact origin, exact role path, session, bound window role, current navigation generation, `webBuildDigest`, and course/runtime digests.
- [ ] Enforce a strict bundled CSP (`default-src 'self'`, no connect/object/frame/worker source; only required same-origin/blob image/style/font rules), disable service workers, and intercept `WebResourceRequested` to reject every non-mapped origin, network fetch, external font/image, redirect, and unknown session-asset ID. Serve a session asset only after the complete begin/chunk/commit state machine has atomically promoted MIME/length/digest-verified bytes; partial/cancelled transfers never resolve.
- [ ] The Helper chooses a session UDF beneath product app data; browser input never supplies it. Validate containment, reparse points, ownership/ACL, and retention. Do not log UDF or course paths/content.
- [ ] In certifying mode create the environment with the release-relative locked Fixed Version Runtime as `browserExecutableFolder`; before either control is created, verify its complete inventory, `BrowserVersionString`, canonical `webView2RuntimeDigest`, containment/no-reparse policy, every Microsoft-signed executable/DLL, and that all observed WebView2 subprocess images originate from that exact directory. Missing/mismatched runtime or any attempt to resolve Evergreen fails closed. An explicit development adapter may use installed Evergreen but stamps every session/event `non-certifying`.
- [ ] Share one WebView2 environment/profile only between the two host roles. On shutdown close controls, wait for `BrowserProcessExited`, then perform bounded stale UDF and projection-asset cleanup.
- [ ] Commit `host-policy.json` containing product/Helper basenames, protocol/bundle policy, relative layout, exact fixed-runtime lock/digest reference, pinned Microsoft runtime signer and product signer policies—not a mutable publish hash manifest. Stage exact files; commit `feat(projection): host the trusted projection bundle`.

---

### Task 6: Implement the host/web bootstrap and frame bridge behind fake session sources

**Files:** create native bridge/bootstrap adapter, `IProjectionSessionSource`, and host-focused tests; modify `StageView.tsx`, `PresenterView.tsx`, teaching domain/bus and focused tests.

- [ ] With an injected fake `IProjectionSessionSource`, test that two host WebViews use separate storage from a synthetic controller profile yet both render the same bounded bootstrap, verified session assets, and synchronized frame. This task does not claim the real Helper/pipe/WebSocket pipeline is connected.
- [ ] The host injects `ProjectionBootstrap` through `PostWebMessageAsJson` only after trusted navigation. The web app validates once per navigation generation and never reads external Chrome localStorage for course content.
- [ ] Define and unit-test typed frame/control ingress on `IProjectionSessionSource`. Host roles may use BroadcastChannel/localStorage only for low-latency role-to-role recovery; Task 10 wires the real Chrome ↔ Helper ↔ pipe source.
- [ ] Emit `message_accepted` after schema/order validation. Emit host-bound `frame_committed` only from a frame-dependent React `useLayoutEffect` followed by two injectable `requestAnimationFrame` callbacks; payload cannot self-assign role. Heartbeats include no course body/token/path/URL.
- [ ] Test stale/missing bootstrap, out-of-order/replayed frame, navigation during render, different course digest, latest-frame recovery, and browser fallback unchanged using fake sources only.
- [ ] Run focused .NET/Vitest tests and `git diff --check`; stage exact bridge/view/test files; commit `feat(projection): bridge isolated teaching views`.

---

### Task 7: Supervise the fixed sidecar with a hardened private protocol

**Files:** create `projection_host.py`, `projection_events.py`, Helper tests, C# stdio transport/tests; keep `host-policy.json` from Task 5.

- [ ] Specify binary anonymous-pipe handles carrying strict UTF-8 LF-delimited JSON, no BOM, the exact 524,288-byte hard line ceiling plus per-kind ceilings before decode/parse, one stdout writer with per-frame flush, and protocol-only stdout. Define the five asset envelopes exactly as in the stable contract, including strict canonical base64, 128-asset/20-MiB-per-asset/96-MiB-bundle limits, four-chunk acknowledgement window, timeout, cancel, idempotency, and stable failures. Concurrently and continuously drain stdout/stderr; stderr is a scrubbed bounded ring buffer.
- [ ] Use a dedicated inherited bootstrap pipe/handle list to deliver a launch key outside argv/env/log/disk. First protocol exchange is `launchId + helperBuildDigest + challenge` and constant-time HMAC acknowledgement before commands/events. In certifying mode the Host independently inspects its parent process image/path and verifies the exact contained `CourseStudio.Helper.exe`, signed catalog, publisher, and helper release-manifest digest before acknowledging; source-Python parentage forces non-certifying mode.
- [ ] Use a dedicated blocking-I/O worker and serialized command queue; never block the FastAPI event loop. Define EOF, half-frame, invalid UTF-8, flush timeout, host hang, Helper shutdown, and close ordering.
- [ ] Implement a dedicated `IWindowsProcessLauncher` with `CreateProcessW`, `STARTUPINFOEX`, `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`, and retained `PROCESS_INFORMATION.hThread`. Create suspended, assign the child to a `KILL_ON_JOB_CLOSE` Job Object, then `ResumeThread`; close every process/thread/pipe/job handle deterministically. Detect parent-in-job/nested-job restrictions and fail closed rather than launch unsupervised. A `subprocess.Popen` launcher is test/dev-only and can never enter certifying mode.
- [ ] Validate installation containment, reparse points, basename/protocol policy, release manifest/hash, parent Helper identity/build digest, and—for certifying mode—catalog signature plus Authenticode pinned publishers. Dev/test output or any unverified Helper parent is explicitly non-certifying.
- [ ] Fault tests cover path/hash/signature mismatch, stdout contamination, stderr flood, secret leakage, oversized line/bundle, malformed base64, sequence/offset gap, duplicate conflict, digest/length mismatch, missing ack, cancel/EOF during transfer, temp cleanup, replay, crash/hang, restart budget/backoff, and no orphan process/window or partial asset.
- [ ] Run Python/C# focused tests and 20x fault injection; stage exact files; commit `feat(helper): supervise the projection host securely`.

---

### Task 8: Harden the Helper projection session, local witness control, and strict command/frame WebSockets

**Files:** modify `session.py`, `api.py`, `server.py`; create `witness_control.py`, `CourseStudio.ProjectionWitnessCli` implementation/tests, and witness-control tests; modify `platform/qa/run.py`, `platform/qa/test_run.py`; create/modify projection API/server/session tests. App-level TypeScript session ownership is deferred to Task 9.

- [ ] Define projection-only Helper startup without a required reference root. `--reference-root` becomes optional for projection APIs (or resolves only to a Helper-created empty app-data root); with `COURSE_REFERENCE_ROOT` unset, startup must not probe `references/` or any protected path.
- [ ] Helper session tokens are process-lifetime only: no sliding renewal and immediate invalidation on Helper restart. Expose the process/session generation; all HTTP/WS clients close and re-bootstrap on generation mismatch.
- [ ] Add `POST /v1/projection/commands` for only the six commands and `WS /v1/projection/events` for authenticated events plus typed client teaching frames/controls. Before JSON parsing, HTTP middleware requires `application/json`, rejects missing/invalid or greater-than-16-KiB `Content-Length`, counts streamed bytes to the same 16-KiB ceiling to defeat chunked lies, and enforces a per-session 10 requests/second with burst 20 limiter; overflow drains/closes safely with stable `413|429` and no body fragments in logs.
- [ ] Configure the actual ASGI/WebSocket transport—not only Pydantic handlers—with reconstructed-message `max_size=65536`, `max_queue=4`, and compression disabled. Validate WebSocket `Origin` before `accept()`. After accept, allow exactly one text-only ≤4 KiB auth message within two seconds, compare token in constant time, emit no event before auth, and use stable close codes without logging token/frame fragments. Reject binary messages and raw-client fragmented messages whose reconstructed bytes exceed 64 KiB before application delivery; transport tests prove oversize/binary/fragment flood does not allocate an unbounded application buffer. Limit one controller WebSocket per projection session, each accepted teaching frame/control to 64 KiB and 30 messages/second, and the outbound queue to 256 events or 2 MiB; disconnect slow consumers with a stable code and clean session ownership.
- [ ] Maintain bounded journal retention. `resumeFromEventSequence` below the retained floor returns `event_gap` plus `full_snapshot_required`; it never silently skips events.
- [ ] `open_projection_session` resolves the pinned course projection server-side. Worker/process boundaries revalidate schemas/digests; no arbitrary executable, URL, origin, path, HWND, raw content, or command reaches the host.
- [ ] Implement the separate one-client witness-control named pipe and fixed foreground CLI exactly as specified above. RED tests cover pipe DACL/remote rejection/random discovery capability, bootstrap timeout/replay, client peer PID/image/hash/signature/user SID/session/AppContainer/RDP checks, and the CLI's reverse `GetNamedPipeServerProcessId` verification of exact packaged Helper image/catalog/publisher/`helperBuildDigest`; also cover zero/multiple eligible sessions, absent/non-foreground console, explicit consent, lease bind, blocking lifetime, Ctrl+C/EOF/crash/helper-exit revocation, capability zeroization, and proof that no HTTP/WS route or browser schema can create/enumerate a lease or reveal pipe material. `run.py projection-hardware` verifies the certifying release and launches only the contained CLI with no browser-controlled argument; before Task 12 signing it must return an honest non-certifying preflight failure outside tests.
- [ ] Keep high-frequency frames/heartbeats only in the bounded in-memory journal. Persist `EvidenceObject(kind="runtime")` for open, verify, invalidate, close, failure, and periodic/final summaries containing first/last sequence, gap/drop statistics, digests, and key checks—never every frame body. Persisted evidence is audit-only and cannot restore a live certified reducer state.
- [ ] Run focused projection/session/witness/CLI/API/server/QA tests and non-reference Helper regression; stage exact files; commit `feat(helper): expose authenticated projection sessions`.

---

### Task 9: Integrate Helper-first control and the two-phase operator challenge

**Files:** create `projection-client.ts` and tests; modify the verified Helper session service/context and tests, teaching domain/reducer/tests, `TeachingSetup`, App session context, and focused tests.

- [ ] Write RED client/reducer tests for strict receipts, event gaps/full snapshots, stale topology/navigation/frame, duplicate roles/displays, mismatched IDs/digests, message vs DOM vs window evidence, eligible vs user-confirmed vs operator-witnessed, and removal of any bare `PHYSICAL_ASSIGNMENT_CONFIRMED` action.
- [ ] Write RED UI/host integration tests for Helper ready/unavailable/runtime-missing, process-generation invalidation, one-time nonce exchange, KnowledgeClient/ProjectionClient session reuse, display candidate cards, assignment, fullscreen, verification, native role-overlay ownership/visibility/focus preservation/zeroization, role-code entry, single paired submission, cancellation/expiry/wrong/replay, normal frame `syncing` and recommit, abnormal frame/topology invalidation, sidecar/WebSocket failure, direct Teach entry, and browser fallback.
- [ ] Only after a projection session is open and eligible may the local TTY workflow create its session-bound `HardwareWitnessLease`; the browser cannot. The first verify call exposes eligibility/challenge ID and instructions while Helper-generated codes travel only by authenticated private pipe to the host-owned native role overlays. The second verify submits user-observed codes exactly once. Any mismatch, lease/challenge expiry, or abnormal bound-state change consumes it; normal monotonic frame advance only enters `syncing` until both roles recommit. Only the active lease plus current synchronized evidence may set `operatorWitnessed`; ordinary UI/test automation remains non-certifying.
- [ ] Use one App-level verified session and job-specific methods. A stale event invalidates readiness immediately. Fallback stays usable and clearly says `NOT CERTIFIED`.
- [ ] Preserve bright styling, real icons, 44 px controls, keyboard/focus/Escape, and concise stage/presenter screens.
- [ ] Run focused then full web tests, typecheck, build, and `git diff --check`; stage exact files; commit `feat(studio): control evidence-backed Win11 projection`.

---

### Task 10: Prove the real Chrome-to-Helper-to-host cross-profile pipeline

**Files:** create cross-layer integration fixtures/tests, `platform/web/vitest.projection-integration.config.ts`, and a bounded integration receipt; modify only the already implemented supervisor, Helper server/session, projection client, host session source, default Vitest config, browser policy, QA gate/tests, and publish-test manifest files needed for real wiring.

- [ ] Start from the upstream course plan's actual `course_publish` result and its Helper-issued `runtimeManifestId/runtimeManifestDigest/courseProjectionId`; never synthesize or accept a browser manifest.
- [ ] Build a non-certifying dev/test host manifest from the exact host/web outputs, start projection-only Helper with no reference root, launch the real verified child transport, and connect a real authenticated Chrome controller WebSocket using a browser profile distinct from the shared WebView2 profile. Reuse the course plan's committed system-Chrome `browser-policy.json`; with `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, verify exact channel/version/executable hash/Authenticode publisher before launch and fail rather than run `playwright install`, select bundled Chromium, or reach the network.
- [ ] Run open → receive/hold bootstrap metadata → real begin/chunk/ack/commit asset stream → verify complete bundle → trusted role navigation → inject bootstrap → role ready → teaching frame/control → both `messageAccepted`/`domCommitted` → close. Require the same helper/web/course/runtime/asset/frame digests across Chrome, Helper journal, pipe, session cache, stage, presenter, and final runtime evidence; additionally inject cancellation, ack backpressure, corrupt final digest, and retry to prove no partial asset is exposed or navigated.
- [ ] Test one Helper restart, event-gap/full-snapshot recovery, slow-consumer close, and browser fallback. This gate is automation-only and always records non-certifying build/signing state.
- [ ] Mark every real .NET Chrome/WebView2/process test with `TestCategory=projection_integration`, every Python test with `pytest.mark.projection_integration`, and every TypeScript test with a `.projection-integration.test.ts(x)` suffix. Default .NET commands use `--filter "TestCategory!=projection_integration"`; default pytest uses `-m "not projection_integration"`; default Vitest config explicitly excludes that suffix, while `vitest.projection-integration.config.ts` includes only it. QA source/meta-tests fail if an integration fixture lacks its marker or any ordinary/`projection-host`/`all` command omits the exclusion.
- [ ] Run the marked tests only through `COURSE_PROJECTION_INTEGRATION_TEST=1 python platform/qa/run.py projection-integration`. That runner verifies consent/browser/toolchain preflight, then explicitly selects the .NET category, pytest marker, and dedicated Vitest config while orchestrating the real cross-profile path with external sockets denied: only exact `127.0.0.1` Helper/test-server addresses and runner-selected ephemeral ports are allowlisted, while DNS, non-loopback IPs, proxy use, redirects, and any undeclared port fail. It performs deterministic teardown and atomically seals a non-certifying receipt; unset immediately. `all` may validate the sealed receipt digest but never launches Chrome/WebView2 or reruns this gate. Run focused non-desktop contract tests and `git diff --check`; stage exact integration/wiring/config/gate/receipt files; commit `test(projection): connect the isolated projection pipeline`.

---

### Task 11: Recover from topology, DPI, WebView2, pipe, and journal failures

**Files:** modify native adapters/coordinator/tests, Helper supervisor/journal/tests, and web projection/teaching tests.

- [ ] Write a cross-layer RED matrix for unplug/replug, display-ID reuse, duplicate-mode change, `WM_DPICHANGED`, user window move/minimize/cloak, renderer/process failure, WebView2 browser-process exit/image/runtime-digest change, navigation change, UDF cleanup, pipe EOF/half-frame, host hang, Helper restart/process-generation invalidation, WebSocket reconnect, journal gap, stale heartbeat/frame, and recovery exhaustion.
- [ ] Topology/DPI/navigation changes create new versions/generations and revoke assignment/challenge/certification. Renderer reload requires fresh bootstrap and frame commit; browser-process failure recreates both controls/environment only after cleanup.
- [ ] Sidecar restart is bounded; every recovery requires fresh trusted helper/bundle/runtime/course digests, role ready, matching frame commit, window verification, and a new local TTY witness lease plus operator challenge.
- [ ] Run every seeded failure 20 times. Require no orphan process/window/UDF leak, event rollback, stale certification, or fallback regression.
- [ ] Stage exact changed files; commit `feat(projection): recover without false readiness`.

---

### Task 12: Publish a reproducible combined Helper/host artifact and offline gates

**Files:** create `platform/helper/packaging/CourseStudio.Helper.spec`, combined publish script/config, helper/host release-manifest files, evidence files and design QA; modify README, `helper-packaging.lock.json`, `host-policy.json`, `platform/qa/run.py`, `platform/qa/test_run.py`, `.superpowers/sdd/progress.md`.

- [ ] Write RED QA tests for SDK/NuGet/Python/wheel/packager/fixed-WebView2-runtime locks and cache digests, Release build/tests, complete Helper module/data and Fixed Runtime collection, forbidden Evergreen/dynamic import/path/index fallback, contract parity, policy mismatch, unsafe launch config, leaked token/path/device/URL, stale render evidence, `helperBuildDigest`/`webView2RuntimeDigest` mismatch, signing-order mutation, manifest/catalog self-reference, post-sign hash drift, simulated certification, missing witness lease/challenge, and false signed-release claims.
- [ ] `projection-host` requires the preinstalled local SDK and the exact locked SDK/WPF/WindowsDesktop/NuGet/WebView2/`win-x64` self-contained runtime-pack, official Fixed Version WebView2 Runtime cache, and CPython/Helper/packager wheelhouse recorded by `projection-restore`. With sockets denied it verifies those digests, builds/tests/publishes .NET using `--no-restore`, builds a fixed `onedir` `CourseStudio.Helper.exe` using only `--no-index --require-hashes` inputs, and copies the already verified fixed runtime byte-for-byte into a release-relative immutable folder inside one normal ignored combined release folder. The Helper spec explicitly includes the API/domain/migrations/policy/package data required by course publication/projection and excludes references, user content, caches, arbitrary plugins, and development paths. Verify import closure from the packaged executable, apphost/DLLs/`WebView2Loader.dll`/deps/runtimeconfig/self-contained .NET/Python runtimes, complete fixed WebView2 runtime inventory/signatures, built web bundle, and host policy.
- [ ] Use one non-circular release order. First build every member and copy the locked Microsoft-signed Fixed Version Runtime unchanged; for a certifying release, Authenticode-sign and timestamp `CourseStudio.Helper.exe`, `CourseStudio.ProjectionHost.exe`, and `CourseStudio.ProjectionWitnessCli.exe`, then verify those final PE bytes/publishers plus every fixed-runtime Microsoft signature. Next generate canonical `helper-release-manifest.json` and `host-manifest.json` from the final signed member bytes, exact build inputs, protocol/runtime versions, `webView2RuntimeDigest`, observed signature/timestamp state, and relative member names. `helperBuildDigest` is the digest of the canonical final Helper member index before embedding that digest; manifests and the catalog are excluded from their own member indexes. Then generate one Windows catalog (`CourseStudio.CourseProjection.cat`) covering both manifests and every final Helper/Host/Witness/DLL/.NET/Python/Fixed-WebView2-runtime/web-bundle member, sign/timestamp the catalog, and finally re-run member hashes, manifest/runtime digests, catalog membership/signature, Microsoft runtime signatures, and `WinVerifyTrust` for all three product executable publishers against `host-policy.json`. Any byte change after manifest generation fails and requires restarting from build/sign. Host and Witness CLI independently verify the running packaged Helper identity and the same `helperBuildDigest`; Host also verifies the same `webView2RuntimeDigest`. Without signing authority, generate clearly unsigned development manifests from unsigned final bytes, omit a certifying catalog claim, remain non-certifying, and make `projection-hardware` reject the release.
- [ ] `all` never downloads SDK/packages/browsers, opens Chrome/WebView2/windows, enters fullscreen, starts `projection_integration`, or touches monitors. It runs socket-denied unit/contract/source checks and validates committed audit receipts. Receipt staleness is determined by schema, declared input/build/runtime/browser-policy digests and superseding evidence—not wall-clock age—so an honest historical `NOT CERTIFIED` receipt remains valid audit evidence. Non-Windows returns an explicit unsupported/preflight result.
- [ ] `projection-desktop` is a separate explicit, cancelable, visible smoke requiring `COURSE_PROJECTION_DESKTOP_TEST=1`; it never runs in `all`. `projection-hardware` is a still separate interactive TTY workflow.
- [ ] After the separately recorded `projection-restore` prerequisite, run socket-denied .NET/Python/Web tests, typecheck/build, `projection-host`, and `all` with no restore/download; record exact limitations in `projection-design-qa.md`.
- [ ] Stage exact README/policy/source/QA/receipt/progress files, never publish/UDF/crash/screenshot directories; commit `test(platform): gate the Win11 projection host`.

---

### Task 13: Run explicit desktop automation and preserve truthful hardware status

**Files:** update `projection-automation-receipt.json`, `projection-certification-status.json`, and design QA only when the relevant explicit gate actually runs.

- [ ] With explicit consent and `COURSE_PROJECTION_DESKTOP_TEST=1`, run a visible non-certifying smoke: detect; open from a real pinned runtime manifest; assign distinct candidate monitor IDs; fullscreen; inject bootstrap; advance a frame from separate Chrome profile; observe both `frame_committed`; verify window geometry/visibility; invalidate/recover; and close cleanly. Unset the flag.
- [ ] Record `messageAccepted`, `domCommitted`, `windowVisible`, topology, helper/host/web/WebView2-runtime/runtime-manifest/course digests, Helper/fixed-runtime packaging and signing state, and `physicalDualScreenCertified=false`. Do not record raw device identity, tokens, UDF paths, course content, or screenshots by default.
- [ ] If no separately witnessed hardware run occurs, write exact `NOT CERTIFIED` reasons: monitor records are not physical proof, the packaged Helper/Host/Witness signed catalog is absent or unverifiable if so, and no operator witness challenge was consumed.
- [ ] Run the final automated gates:

```powershell
.tools/dotnet/dotnet.exe test platform/windows/CourseStudio.ProjectionHost.slnx -c Release --no-restore --filter "TestCategory!=projection_integration"
.tools/dotnet/dotnet.exe build platform/windows/CourseStudio.ProjectionHost.slnx -c Release --no-restore
python -m pytest platform/helper/tests -m "not reference_demo and not projection_hardware and not projection_integration" -q
npm --prefix platform/web test -- --run
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
python platform/qa/run.py projection-host
python platform/qa/run.py all
```

- [ ] Stage only the two bounded status receipts and design QA if changed; commit `test(projection): record truthful desktop evidence`.

## Separate physical hardware runbook

Run only with a physically present operator, an interactive local TTY, the exact running packaged Helper plus Host/Witness/web bundle and locked Fixed Version WebView2 Runtime covered by one verified certifying signed release/catalog, and two real Win11 displays configured as extended desktop. A source-Python Helper or Evergreen WebView2 session is never eligible:

1. Open the pinned built course through the governed UI and complete detect/assign/fullscreen. Confirm learner stage and presenter windows occupy different real panels and show the correct roles.
2. Confirm the Helper reports two distinct `hardwareCandidate` displays with no known virtual/remote/duplicate indicator and that the open projection session is witness-eligible; this is still eligibility, not physical proof.
3. Run `python platform/qa/run.py projection-hardware` from a local interactive TTY. The wrapper verifies and visibly launches the signed fixed Witness CLI, which authenticates to the existing Helper's private ACL named pipe, requires foreground consent, and binds a new in-memory `HardwareWitnessLease` to the one exact active eligible projection session plus current console/CLI/Helper process. It stays blocked and connected until the workflow ends; Ctrl+C/EOF/exit revokes the lease. It fails if zero or multiple eligible sessions exist and must never be called by `all`, CI, RDP, or an unattended process.
4. First `verify_projection_assignment` creates a bound, expiring challenge and visibly displays different role codes through the two non-WebView, role-bound native overlays without stealing focus.
5. Read both codes from the physical panels and submit the pair once. The second call consumes the challenge; malformed format may be corrected locally before transmission, but a server-side mismatch, replay, expiry, or abnormal bound-state change revokes the lease.
6. Advance once and observe the temporary `syncing` state. Visually confirm both roles reflect the same latest frame and require both `frame_committed` events plus current window/heartbeat checks before certification automatically resumes under the same lease.
7. Perform the requested unplug/reconnect exercise. This topology change must revoke the lease and certification, terminate the blocking Witness CLI workflow, and leave the session `NOT CERTIFIED` until full recovery.
8. After topology stabilizes, repeat detect → assign distinct role displays → fullscreen → trusted bundle/runtime verification → both-role frame commit. Start a new local TTY Witness CLI, create a new lease/challenge, read and submit both newly displayed role codes once, and require synchronized current window/heartbeat evidence. No old display ID, lease, challenge, code, or frame receipt may be reused.
9. Only after step 8 succeeds, and only while packaged Helper identity/`helperBuildDigest`, fixed WebView2 process identity/`webView2RuntimeDigest`, signed release identity, topology, assignments, exact bounds, current heartbeats, matching committed frame, and operator witness all remain valid, may the recovered active session report `physicalDualScreenCertified=true`.

If any step lacks direct evidence, stop with `NOT CERTIFIED`. Desktop enumeration, screenshots, OCR, fake adapters, Chrome automation, or a persisted prior receipt are not substitutes.

## Completion boundary

This plan is implementation-complete when the Helper securely resolves a pinned course projection, supervises the fixed WPF WebView2 host, transports teaching state across isolated browser profiles, deterministically detects/opens/assigns/fullscreens/verifies/recovers/closes both role windows, distinguishes accepted/DOM-committed/visible/operator-witnessed evidence, packages the full certifying TCB with an independently verifiable `helperBuildDigest`, preserves browser rehearsal, and passes all locally executable .NET/Python/Web/QA gates.

Physical certification remains a separate outcome. Without a verified signed catalog covering the exact running packaged Helper, Host, Witness CLI, fixed WebView2/.NET/Python runtimes and dependencies, and web bundle—or without a successful real-hardware witness challenge—a fully implemented and automation-verified product must still report `NOT CERTIFIED`.
