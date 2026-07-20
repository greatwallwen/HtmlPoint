# Win11 Physical Dual-Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and visibly certify a live personal Win11 teaching session with Stage and Presenter assigned to two distinct physical displays while keeping release-signature certification false.

**Architecture:** The existing React Studio sends six typed projection commands to the Python Helper. The Helper resolves the immutable published course, supervises one contained .NET 10 WPF/WebView2 Host over authenticated anonymous pipes, and owns bounded evidence; the Host owns display enumeration, two HWND-backed role windows, exact fullscreen checks, WebView2 projection rendering, and the attended two-code witness.

**Tech Stack:** .NET SDK 10.0.301, C# 14, WPF, Microsoft.Web.WebView2 1.0.4078.44, MSTest 4.3.2, Python 3.12/Pydantic/FastAPI/pytest, React 19/TypeScript/Zod/Vitest, Win32 display and DWM APIs.

## Global Constraints

- Work only in `D:/cursor/AI培训`; do not use `.worktrees`.
- Never read, enumerate, copy, hash, or modify `Course_AIProduct/` or `references/`.
- Keep `COURSE_REFERENCE_ROOT`, `COURSE_PROJECTION_INTEGRATION_TEST`, and `COURSE_PROJECTION_HARDWARE_TEST` unset except in their explicit gates.
- Install the SDK and restore caches only under ignored `.tools/`; do not modify system PATH or install global workloads.
- Pin .NET SDK `10.0.301` and WebView2 SDK `1.0.4078.44`; every normal build/test command uses `--no-restore` after the explicit restore gate.
- The Host may use installed Microsoft Evergreen WebView2 only for this personal-device milestone; bind its version, canonical process path, Microsoft signature, and executable digest into the live session.
- `physicalDualScreenCertified` means only a live attended personal-device session; `releaseSignatureCertified` remains `false`.
- The Host may control only its own windows and must never change resolution, primary display, clone/extend mode, brightness, color, or system display settings.
- Browser rehearsal, fake adapters, automated tests, RDP, duplicate topology, and two windows on one display never certify physical dual-screen output.
- Every production behavior begins with a failing focused test, followed by the smallest passing implementation and a focused commit.
- Build, browser, desktop, and test outputs follow the Supergrill artifact lifecycle gate; retain only named final evidence and durable source.

## File and Responsibility Map

### Shared contracts

- Create `platform/contracts/projection/v1/projection-command.schema.json`: strict six-command envelope and receipts.
- Create `platform/contracts/projection/v1/projection-event.schema.json`: strict host/render/witness lifecycle events.
- Create `platform/contracts/projection/v1/fixtures/`: one canonical valid fixture and bounded invalid fixtures.

### .NET native layer

- Create `global.json`: exact local SDK selection.
- Create `platform/windows/Directory.Build.props`: warnings, deterministic builds, nullable, C# 14.
- Create `platform/windows/Directory.Packages.props`: exact MSTest and WebView2 packages.
- Create `platform/windows/CourseStudio.ProjectionHost.slnx`: Core, Host, and tests.
- Create `platform/windows/src/CourseStudio.ProjectionHost.Core/`: contracts, reducer, topology/window abstractions, evidence digests.
- Create `platform/windows/src/CourseStudio.ProjectionHost/`: Win32/WPF/WebView2 adapters, pipe transport, native witness UI.
- Create `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/`: deterministic unit/contract tests.
- Create `platform/windows/tests/CourseStudio.ProjectionHost.IntegrationTests/`: marked real-host tests.
- Create `platform/windows/host-policy.json`: fixed protocol/origin/build/runtime policy without user paths.

### Helper authority

- Create `platform/helper/course_helper/domain/projection.py`: Pydantic contracts matching C#/Zod.
- Create `platform/helper/course_helper/projection_host.py`: contained child supervision, handshake, transport, assets, cleanup.
- Create `platform/helper/course_helper/projection_events.py`: serialized session state and evidence summaries.
- Modify `platform/helper/course_helper/jobs.py`: six typed projection jobs and bounded dispatch.
- Modify `platform/helper/course_helper/api.py`: existing authenticated `/v1/jobs` path exposes the new typed jobs only.

### Web product layer

- Create `platform/web/src/domain/projection.ts`: strict types/reducer/friendly status projection.
- Create `platform/web/src/domain/projection-schema.ts`: Zod parity and native WebView bootstrap/frame parsing.
- Create `platform/web/src/services/projection-client.ts`: job-specific Helper calls.
- Create `platform/web/src/services/native-projection.ts`: WebView2 bridge adapter and frame acknowledgements.
- Modify `platform/web/src/components/TeachingSetup.tsx`: one-button native flow, Swap/Retry/Re-witness/Close, fallback.
- Modify `platform/web/src/components/StageView.tsx` and `PresenterView.tsx`: injected bootstrap/frame commit reporting.
- Modify `platform/web/src/app/App.tsx`: choose native projection input only when trusted WebView2 bridge is present.

### QA and evidence

- Modify `platform/qa/run.py` and `platform/qa/test_run.py`: restore, contracts, host, integration, and hardware gates.
- Create `platform/windows/evidence/projection-integration.json`: sealed non-certifying automation receipt.
- Create `platform/windows/evidence/physical-dual-screen-current.json`: bounded visible witness receipt.
- Create `platform/windows/projection-design-qa.md`: final certification boundary and artifact inventory.

---

### Task 1: Exact toolchain and cross-language projection contracts

**Files:**
- Create: `global.json`
- Create: `platform/windows/Directory.Build.props`
- Create: `platform/windows/Directory.Packages.props`
- Create: `platform/windows/CourseStudio.ProjectionHost.slnx`
- Create: `platform/windows/src/CourseStudio.ProjectionHost.Core/CourseStudio.ProjectionHost.Core.csproj`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/CourseStudio.ProjectionHost.csproj`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj`
- Create: `platform/contracts/projection/v1/projection-command.schema.json`
- Create: `platform/contracts/projection/v1/projection-event.schema.json`
- Create: `platform/contracts/projection/v1/fixtures/detect-displays.json`
- Create: `platform/helper/course_helper/domain/projection.py`
- Create: `platform/helper/tests/test_projection_contracts.py`
- Create: `platform/web/src/domain/projection-schema.ts`
- Create: `platform/web/src/domain/projection-schema.test.ts`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ProjectionCommand`, `ProjectionReceipt`, `DisplayTopology`, `ProjectionEvent`, `ProjectionStatus` with identical field names and enum values in C#, Python, and TypeScript.
- Consumes: existing Helper strict-model conventions and Web Zod schema conventions.

- [x] **Step 1: Add only project skeletons and exact version pins**

Create `global.json`:

```json
{
  "sdk": {
    "version": "10.0.301",
    "rollForward": "disable",
    "allowPrerelease": false
  }
}
```

Create `Directory.Build.props` with `TargetFramework=net10.0-windows`,
`LangVersion=14.0`, `Nullable=enable`, `ImplicitUsings=enable`,
`TreatWarningsAsErrors=true`, `Deterministic=true`, and
`ContinuousIntegrationBuild=true`. Create `Directory.Packages.props` with
`Microsoft.Web.WebView2=1.0.4078.44`, `Microsoft.NET.Test.Sdk=18.8.1`,
`MSTest.TestAdapter=4.3.2`, and `MSTest.TestFramework=4.3.2`. Create the Core,
Host, and test project files plus solution; do not add production classes. The
Host project starts as a WPF-enabled library with the WebView2 package reference
so restore locks the final dependencies before any source exists. Task 4 changes
only its output type to `WinExe`.

- [x] **Step 2: Perform the explicit local restore prerequisite**

Run from PowerShell after downloading the official installer script to
`.tools/bootstrap/dotnet-install.ps1` and verifying its downloaded SHA-256 in
`.tools/bootstrap/dotnet-install.sha256`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .tools/bootstrap/dotnet-install.ps1 -Version 10.0.301 -InstallDir .tools/dotnet -NoPath
.tools/dotnet/dotnet.exe restore platform/windows/CourseStudio.ProjectionHost.slnx --packages .tools/nuget/packages --use-lock-file
.tools/dotnet/dotnet.exe restore platform/windows/CourseStudio.ProjectionHost.slnx --packages .tools/nuget/packages --locked-mode
```

Expected: `.tools/dotnet/dotnet.exe --version` prints `10.0.301`; restore exits
0 and creates committed `packages.lock.json` files. Record package-source,
lock-file, and cache digests in
`.superpowers/sdd/projection-toolchain-restore.json`. Unset any download flag
before continuing.

- [x] **Step 3: Write failing Python and TypeScript contract tests**

The Python test must include this behavior:

```python
def test_detect_display_fixture_is_strict_and_round_trips() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    command = ProjectionCommand.model_validate(payload)
    assert command.command == "detect_displays"
    assert command.model_dump(mode="json") == payload
    with pytest.raises(ValidationError):
        ProjectionCommand.model_validate({**payload, "executablePath": "bad.exe"})
```

The TypeScript test must parse the same fixture and reject the same extra key:

```typescript
it("round-trips the canonical detect fixture and rejects extras", () => {
  expect(projectionCommandSchema.parse(fixture)).toEqual(fixture);
  const unsafe = Object.assign({}, fixture, { executablePath: "bad.exe" });
  expect(() => projectionCommandSchema.parse(unsafe)).toThrow();
});
```

- [x] **Step 4: Run the contract tests and verify RED**

Run:

```powershell
python -m pytest platform/helper/tests/test_projection_contracts.py -q
npm.cmd --prefix platform/web test -- --run src/domain/projection-schema.test.ts
```

Expected: both fail because the projection contract modules do not exist.

- [x] **Step 5: Implement the minimal strict contracts**

Use these exact discriminators and statuses:

```python
ProjectionCommandName = Literal[
    "detect_displays",
    "open_projection_session",
    "assign_projection_window",
    "enter_projection_fullscreen",
    "verify_projection_assignment",
    "close_projection_session",
]

ProjectionStatus = Literal[
    "undetected",
    "candidate",
    "assigned",
    "fullscreen",
    "syncing",
    "witness_pending",
    "certified",
    "invalidated",
    "closed",
]

class ProjectionCommand(HttpRequestModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    command_id: UUID = Field(alias="commandId")
    command: ProjectionCommandName
    session_id: UUID | None = Field(default=None, alias="sessionId")
    expected_generation: int = Field(ge=0, alias="expectedGeneration")
    payload: dict[str, JsonValue]
```

Implement equivalent sealed C# records and strict Zod schemas. The two JSON
schemas set `additionalProperties=false`, integer ceilings, finite rectangle
coordinates, unique role/display assignments, and bounded text. The canonical
fixture contains no device path, friendly hardware name, URL, token, HWND,
executable path, or raw course body.

- [x] **Step 6: Run GREEN and lock contract parity**

Run the two commands from Step 4 plus:

```powershell
.tools/dotnet/dotnet.exe test platform/windows/CourseStudio.ProjectionHost.slnx --no-restore --filter "TestCategory!=projection_integration"
git diff --check
```

Expected: all pass with no warnings or whitespace errors.

- [x] **Step 7: Commit**

```powershell
git add -- global.json .gitignore platform/contracts/projection platform/windows/Directory.Build.props platform/windows/Directory.Packages.props platform/windows/CourseStudio.ProjectionHost.slnx platform/windows/src/CourseStudio.ProjectionHost.Core platform/windows/src/CourseStudio.ProjectionHost/CourseStudio.ProjectionHost.csproj platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests platform/helper/course_helper/domain/projection.py platform/helper/tests/test_projection_contracts.py platform/web/src/domain/projection-schema.ts platform/web/src/domain/projection-schema.test.ts
git commit -m "test(projection): define personal dual-screen contracts"
```

### Task 2: Deterministic certification state machine

**Files:**
- Create: `platform/windows/src/CourseStudio.ProjectionHost.Core/ProjectionState.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost.Core/ProjectionReducer.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost.Core/ProjectionEvidence.cs`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/ProjectionReducerTests.cs`

**Interfaces:**
- Consumes: Task 1 contract enums and records.
- Produces: `IProjectionReducer.Apply(ProjectionState, ProjectionSignal) -> ProjectionTransition`, `ProjectionTransition.Events`, and deterministic evidence digests.

- [x] **Step 1: Write reducer tests for the complete happy path and invalidation**

Use a table-driven test whose valid signal order is exactly:

```csharp
var signals = new ProjectionSignal[]
{
    new DisplaysDetected(topology),
    new WindowsAssigned(stageWindow, presenterWindow),
    new FullscreenVerified(stageGeometry, presenterGeometry),
    new FrameCommitted(Role.Stage, frameIdentity),
    new FrameCommitted(Role.Presenter, frameIdentity),
    new WitnessChallengeIssued(challengeIdentity, expiry),
    new NativeWitnessAccepted(challengeIdentity, witnessDigest),
};
```

Assert the terminal state is `Certified`, both certification booleans are
`physicalDualScreenCertified=true` and `releaseSignatureCertified=false`, a
valid forward frame enters `Syncing` then returns to `Certified`, and each of
topology change, DPI change, role collision, minimize, cloak, frame rollback,
identity mismatch, heartbeat expiry, navigation change, Runtime change, Helper
restart, and Host restart transitions to `Invalidated`.

- [x] **Step 2: Run RED**

```powershell
.tools/dotnet/dotnet.exe test platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj --no-restore --filter "FullyQualifiedName~ProjectionReducerTests"
```

Expected: FAIL because reducer types are missing.

- [x] **Step 3: Implement immutable state and transition interfaces**

Use these public shapes:

```csharp
public sealed record ProjectionState(
    ProjectionPhase Phase,
    long Generation,
    DisplayTopology? Topology,
    RoleAssignment? Assignment,
    FrameIdentity? LatestFrame,
    RoleCommit? StageCommit,
    RoleCommit? PresenterCommit,
    WitnessIdentity? Witness,
    bool PhysicalDualScreenCertified,
    bool ReleaseSignatureCertified,
    string? InvalidationCode);

public sealed record ProjectionTransition(
    ProjectionState State,
    IReadOnlyList<ProjectionEvent> Events);

public interface IProjectionReducer
{
    ProjectionTransition Apply(ProjectionState state, ProjectionSignal signal);
}
```

Implement one sealed `ProjectionReducer : IProjectionReducer`. The reducer is
pure: no clock, random, Win32, WebView2, file, process, network,
or UI calls. All event ordering and evidence JSON use canonical ordinal field
ordering and SHA-256. Reject invalid transitions with stable codes rather than
silently ignoring them.

- [x] **Step 4: Add deterministic replay and fake-certification rejection tests**

Replay one seeded signal sequence 100 times and assert identical receipt bytes.
Pass a `SimulatedWitnessObserved` signal and assert it cannot produce
`Certified`. Pass an expired or reused challenge and assert `Invalidated` with
`witness_expired` or `witness_replayed`.

- [x] **Step 5: Run GREEN**

```powershell
.tools/dotnet/dotnet.exe test platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj --no-restore
git diff --check
```

Expected: all Core tests pass and replay receipts are byte-identical.

- [x] **Step 6: Commit**

```powershell
git add -- platform/windows/src/CourseStudio.ProjectionHost.Core platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests
git commit -m "feat(projection): add deterministic certification reducer"
```

### Task 3: Win32 topology and anonymous display evidence

**Files:**
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Native/DisplayNative.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Native/Win32DisplayTopologyProvider.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Native/DisplayTopologyMapper.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/app.manifest`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/DisplayTopologyMapperTests.cs`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/ForbiddenDisplayApiTests.cs`

**Interfaces:**
- Consumes: `DisplayTopology`, `PhysicalRect`, and certification eligibility from Task 2.
- Produces: `IDisplayTopologyProvider.Read(ReadOnlySpan<byte> sessionSalt)` and a PerMonitorV2 native provider.

- [x] **Step 1: Write failing mapping and safety tests**

Define test cases for single, extended, duplicate, remote, unknown, negative
coordinates, rotated monitors, mixed DPI, missing metadata, overflow, and a
virtual/indirect indicator. Assert the current eligible shape requires exactly
two different candidates in extended topology and never returns a raw device
name. Add a source test that fails if production files contain any of:

```text
ChangeDisplaySettings
ChangeDisplaySettingsEx
SetDisplayConfig
DisplaySwitch.exe
SetCimInstance
Set-WmiInstance
```

- [x] **Step 2: Run RED**

```powershell
.tools/dotnet/dotnet.exe test platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj --no-restore --filter "FullyQualifiedName~DisplayTopologyMapperTests|FullyQualifiedName~ForbiddenDisplayApiTests"
```

Expected: FAIL because native mapping is absent.

- [x] **Step 3: Implement the provider behind one interface**

Use this interface and output boundary:

```csharp
public interface IDisplayTopologyProvider
{
    DisplayTopology Read(ReadOnlySpan<byte> sessionSalt);
}

public sealed record DisplayCandidate(
    string AnonymousDisplayId,
    PhysicalRect Bounds,
    PhysicalRect WorkArea,
    uint DpiX,
    uint DpiY,
    bool Primary,
    bool InternalHint,
    bool ExternalHint,
    bool HardwareCandidate,
    bool NoKnownVirtualIndicator);
```

Call `QueryDisplayConfig`, `DisplayConfigGetDeviceInfo`,
`EnumDisplayMonitors`, and `GetMonitorInfo`; keep raw target/adapter/PnP values
inside the adapter. Derive `AnonymousDisplayId` as HMAC-SHA256 over the raw
stable tuple with the per-session salt. Assert effective PerMonitorV2 awareness
before window creation; failure returns `unknown` and prevents Host startup.

- [x] **Step 4: Run unit GREEN and a non-certifying read-only smoke**

```powershell
.tools/dotnet/dotnet.exe test platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj --no-restore
.tools/dotnet/dotnet.exe test platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj --no-restore --filter "TestCategory=projection_detect_smoke"
```

Expected on the current machine: extended topology with two anonymous
candidates corresponding to the integrated and Samsung displays, and
`physicalDualScreenCertified=false`.

- [x] **Step 5: Commit**

```powershell
git add -- platform/windows/src/CourseStudio.ProjectionHost/Native platform/windows/src/CourseStudio.ProjectionHost/app.manifest platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests
git commit -m "feat(projection): enumerate Win11 display candidates"
```

### Task 4: WPF role windows, exact fullscreen, and native witness

**Files:**
- Modify: `platform/windows/src/CourseStudio.ProjectionHost/CourseStudio.ProjectionHost.csproj`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/App.xaml`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/App.xaml.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Windows/RoleWindow.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Windows/RoleWindowController.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Witness/HardwareWitnessCoordinator.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Witness/WitnessOverlay.cs`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/RoleWindowPolicyTests.cs`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/HardwareWitnessTests.cs`

**Interfaces:**
- Consumes: Task 2 reducer and Task 3 candidate displays.
- Produces: `IRoleWindowController`, verified `RoleWindowEvidence`, and one-use `NativeWitnessProof`.

- [x] **Step 1: Write failing window and witness tests**

Cover one unique window per role, external-Stage/internal-Presenter defaults,
Swap, negative/mixed-DPI coordinates, exact target rectangles, style restore,
minimize/cloak/move invalidation, Escape, user close, partial-open rollback,
90-second expiry, wrong codes, replay, one attempt, zeroization, and proof that
a fake coordinator cannot create `NativeWitnessProof`.

- [x] **Step 2: Run RED**

```powershell
.tools/dotnet/dotnet.exe test platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj --no-restore --filter "FullyQualifiedName~RoleWindowPolicyTests|FullyQualifiedName~HardwareWitnessTests"
```

Expected: FAIL because window policy and witness coordinator are absent.

- [x] **Step 3: Implement role-window policy and exact verification**

Use this boundary:

```csharp
public interface IRoleWindowController : IAsyncDisposable
{
    Task<RoleAssignment> OpenAsync(DisplayTopology topology, CancellationToken cancellationToken);
    Task<RoleAssignment> SwapAsync(long expectedGeneration, CancellationToken cancellationToken);
    Task<IReadOnlyList<RoleWindowEvidence>> EnterFullscreenAsync(long expectedGeneration, CancellationToken cancellationToken);
    Task<IReadOnlyList<RoleWindowEvidence>> VerifyAsync(long expectedGeneration, CancellationToken cancellationToken);
    Task CloseAsync(CancellationToken cancellationToken);
}
```

Create one WPF window per fixed role. Convert no coordinates to WPF DIPs;
position with Win32 physical rectangles after HWND creation. Enter borderless
fullscreen by saving/restoring style and placement and applying `WS_POPUP` with
`SWP_FRAMECHANGED`. Verify `GetWindowRect`, DWM extended-frame bounds,
`MonitorFromWindow`, `IsWindowVisible`, `IsIconic`, and `DWMWA_CLOAKED`.

- [x] **Step 4: Implement attended witness UI**

`HardwareWitnessCoordinator.Begin` generates two independent six-character
codes with `RandomNumberGenerator.Fill`, stores only salted HMAC digests, shows
role-colored non-focus overlays, and opens one Presenter-native input dialog.
`Submit` accepts one pair before the 90-second deadline, compares with
`CryptographicOperations.FixedTimeEquals`, hides overlays, zeroizes temporary
buffers, and returns an internal `NativeWitnessProof`. Any wrong, expired,
cancelled, moved, minimized, topology-changed, or replayed attempt consumes the
challenge and invalidates the session.

- [x] **Step 5: Run GREEN**

```powershell
.tools/dotnet/dotnet.exe test platform/windows/CourseStudio.ProjectionHost.slnx --no-restore --filter "TestCategory!=projection_integration"
git diff --check
```

Expected: all native-policy unit tests pass without opening visible windows.

- [x] **Step 6: Commit**

```powershell
git add -- platform/windows/src/CourseStudio.ProjectionHost platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests
git commit -m "feat(projection): control role windows and native witness"
```

### Task 5: Trusted WebView2 projection bridge and frame commits

**Files:**
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Web/ProjectionWebViewHost.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Web/ProjectionSessionAssets.cs`
- Create: `platform/windows/src/CourseStudio.ProjectionHost/Web/WebViewRuntimeIdentity.cs`
- Create: `platform/windows/host-policy.json`
- Create: `platform/web/src/services/native-projection.ts`
- Create: `platform/web/src/services/native-projection.test.ts`
- Modify: `platform/web/src/app/App.tsx`
- Modify: `platform/web/src/components/StageView.tsx`
- Modify: `platform/web/src/components/PresenterView.tsx`
- Modify: `platform/web/vite.config.ts`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/ProjectionWebPolicyTests.cs`

**Interfaces:**
- Consumes: immutable course projection, role windows, Task 1 schemas.
- Produces: `ProjectionBootstrap`, same-origin session assets, role-bound `message_accepted` and `frame_committed` events.

- [x] **Step 1: Write failing web/native bridge tests**

TypeScript tests must prove that native mode accepts a strict bootstrap only
from `window.chrome.webview`, never reads Chrome localStorage for course data,
rejects wrong role/course/runtime/navigation generation, and emits a commit
only after React commit plus two animation frames. C# policy tests must reject
network origins, arbitrary paths, new windows, downloads, permissions,
DevTools, service workers, unknown asset IDs, Runtime identity drift, and
unmapped navigation.

- [x] **Step 2: Run RED**

```powershell
npm.cmd --prefix platform/web test -- --run src/services/native-projection.test.ts
.tools/dotnet/dotnet.exe test platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests/CourseStudio.ProjectionHost.Core.Tests.csproj --no-restore --filter "FullyQualifiedName~ProjectionWebPolicyTests"
```

Expected: FAIL because the native projection adapters are missing.

- [x] **Step 3: Implement the strict browser adapter**

Use this injected boundary:

```typescript
export interface NativeProjectionAdapter {
  role: "stage" | "presenter";
  waitForBootstrap(): Promise<ProjectionBootstrap>;
  subscribeFrame(listener: (frame: ProjectionFrame) => void): () => void;
  reportMessageAccepted(frame: ProjectionFrame): void;
  reportFrameCommitted(frame: ProjectionFrame): void;
}

export function detectNativeProjectionAdapter(
  scope: Window & typeof globalThis,
): NativeProjectionAdapter | undefined;
```

`App.tsx` selects native input only when the bridge exists and a strict
host-injected role handshake succeeds. Stage/Presenter report
`message_accepted` after schema/order validation and `frame_committed` from a
frame-dependent `useLayoutEffect` followed by two `requestAnimationFrame`
callbacks. Browser rehearsal behavior remains unchanged.

- [x] **Step 4: Implement Host resource and Runtime policy**

Map the exact built web folder to
`https://projection.course-studio.test/index.html` with deny-by-default host
resource access. Bind each WebView instance to its native role. Serve only
digest-verified assets from
`https://projection.course-studio.test/session-assets/{opaqueId}`. Reject every
other origin, redirect, external fetch, unknown path, popup, download,
permission, or host object.

At environment creation, record `BrowserVersionString`, canonical Runtime
process paths, Microsoft Authenticode verification, and SHA-256. Store the
result in `WebViewRuntimeIdentity`; any changed process/version/path/digest
emits `runtime_identity_changed` and invalidates certification.

- [x] **Step 5: Run GREEN and build the exact web bundle**

```powershell
npm.cmd --prefix platform/web test -- --run src/services/native-projection.test.ts src/domain/teaching.test.ts
npm.cmd --prefix platform/web run typecheck
npm.cmd --prefix platform/web run build
.tools/dotnet/dotnet.exe test platform/windows/CourseStudio.ProjectionHost.slnx --no-restore --filter "TestCategory!=projection_integration"
git diff --check
```

Expected: focused Web/.NET tests, typecheck, and build pass; no visible windows
open.

- [x] **Step 6: Commit**

```powershell
git add -- platform/windows/src/CourseStudio.ProjectionHost/Web platform/windows/host-policy.json platform/windows/tests/CourseStudio.ProjectionHost.Core.Tests platform/web/src/services/native-projection.ts platform/web/src/services/native-projection.test.ts platform/web/src/app/App.tsx platform/web/src/components/StageView.tsx platform/web/src/components/PresenterView.tsx platform/web/vite.config.ts
git commit -m "feat(projection): bridge trusted WebView2 teaching views"
```

### Task 6: Helper supervision, authenticated transport, and asset evidence

**Files:**
- Create: `platform/helper/course_helper/projection_host.py`
- Create: `platform/helper/course_helper/projection_events.py`
- Create: `platform/helper/tests/test_projection_host.py`
- Modify: `platform/helper/course_helper/server.py`

**Interfaces:**
- Consumes: Task 1 Pydantic contracts, existing Helper session and course projection endpoint, Host stdin/stdout protocol.
- Produces: one supervised live `ProjectionSession`, authenticated Host transport, verified session assets, and bounded runtime evidence.

- [ ] **Step 1: Write failing supervisor and job tests**

Cover fixed executable containment, `shell=False`, no caller-controlled argv,
random launch key outside argv/log/disk, handshake HMAC, strict UTF-8 LF-JSON,
one stdout writer, stderr draining, 64 KiB command/event ceiling, 72 KiB asset
chunk ceiling, 128 assets, 20 MiB per asset, 96 MiB bundle, sequence/offset,
digest mismatch, cancel, EOF, crash, timeout, Helper restart, idempotent command
replay, and no orphan process.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest platform/helper/tests/test_projection_host.py -q
```

Expected: FAIL because supervisor and projection jobs do not exist.

- [ ] **Step 3: Implement the supervisor boundary**

Use these public methods:

```python
class ProjectionHostSupervisor:
    def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="detect_displays")

    def open_session(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="open_projection_session")

    def assign_windows(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="assign_projection_window")

    def enter_fullscreen(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="enter_projection_fullscreen")

    def verify_assignment(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="verify_projection_assignment")

    def close_session(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="close_projection_session")

    def shutdown(self) -> None:
        self._transport.close()
        self._job.close()
```

Implement `_execute` as one serialized command queue. Resolve Host path solely from the
Helper-owned install policy, verify containment/no reparse/build digest, launch
with `subprocess.Popen` argument arrays and `shell=False`, assign the child to a
Windows `KILL_ON_JOB_CLOSE` Job Object, and authenticate the first pipe exchange
with a random launch key delivered through an inherited anonymous bootstrap
pipe. Host EOF closes windows; Helper shutdown closes the Job Object.

- [ ] **Step 4: Implement bounded asset transfer and runtime evidence**

Before WebView navigation, resolve the published Helper-issued
`runtimeManifestId`, exact Slide AST, and governed artifacts. Send
`asset_begin`, ordered `asset_chunk`, `asset_commit`, then the strict bootstrap.
The Host exposes no partial asset. Persist only open, verify, invalidate, close,
failure, and final summary evidence containing identities/digests, sequences,
window checks, Runtime checks, witness status, and stable errors; never persist
codes, tokens, raw device identities, paths, course body, or every frame.

- [ ] **Step 5: Run GREEN and transport regression**

```powershell
python -m pytest platform/helper/tests/test_projection_host.py platform/helper/tests/test_course_publication.py -q
git diff --check
```

Expected: focused transport and publication regression tests pass; no Host
process is left running.

- [ ] **Step 6: Commit**

```powershell
git add -- platform/helper/course_helper/projection_host.py platform/helper/course_helper/projection_events.py platform/helper/course_helper/server.py platform/helper/tests/test_projection_host.py
git commit -m "feat(helper): supervise the native projection host"
```

### Task 7: Six authenticated Helper projection jobs

**Files:**
- Create: `platform/helper/tests/test_projection_jobs.py`
- Modify: `platform/helper/course_helper/jobs.py`
- Modify: `platform/helper/course_helper/api.py`

**Interfaces:**
- Consumes: Task 1 Pydantic contracts and Task 6 `ProjectionHostSupervisor`.
- Produces: six strict authenticated operations through the existing `/v1/jobs` route.

- [ ] **Step 1: Write failing strict job tests**

Test each job's valid minimum payload, exact field set, session authorization,
command replay, stale generation, supervisor dispatch, timeout ceiling, and
sanitized response. Assert every job rejects URL, path, HWND, token, shell text,
raw manifest, raw course, and asset bytes.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest platform/helper/tests/test_projection_jobs.py -q
```

Expected: FAIL because the six job models and dispatch registrations are absent.

- [ ] **Step 3: Add six discriminated job models**

Create `ProjectionDetectDisplaysJob`, `ProjectionOpenSessionJob`,
`ProjectionAssignWindowJob`, `ProjectionEnterFullscreenJob`,
`ProjectionVerifyAssignmentJob`, and `ProjectionCloseSessionJob`. Each model
contains only the command/session/generation identities and its bounded typed
payload. Register exact job ceilings and reject extras through the existing
strict base model.

- [ ] **Step 4: Dispatch only to matching supervisor methods**

Map each discriminator to exactly one Task 6 method. Reuse the existing
authenticated App session and `/v1/jobs` handler. Resolve published course and
runtime identities server-side in `ProjectionOpenSessionJob`; never accept a
browser-authored manifest or executable/runtime path.

- [ ] **Step 5: Run GREEN and full offline Helper regression**

```powershell
python -m pytest platform/helper/tests/test_projection_jobs.py platform/helper/tests/test_api.py platform/helper/tests/test_course_publication.py -q
python -m pytest platform/helper/tests -m "not reference_demo and not network_visual and not model_download and not projection_integration" -q
git diff --check
```

Expected: focused and complete offline Helper suites pass; projection jobs do
not start a Host unless an explicit projection operation executes.

- [ ] **Step 6: Commit**

```powershell
git add -- platform/helper/course_helper/jobs.py platform/helper/course_helper/api.py platform/helper/tests/test_projection_jobs.py
git commit -m "feat(helper): expose six governed projection jobs"
```

### Task 8: Concise Studio dual-screen controls

**Files:**
- Create: `platform/web/src/domain/projection.ts`
- Create: `platform/web/src/domain/projection.test.ts`
- Create: `platform/web/src/services/projection-client.ts`
- Create: `platform/web/src/services/projection-client.test.ts`
- Modify: `platform/web/src/components/TeachingSetup.tsx`
- Modify: `platform/web/src/components/TeachingSetup.test.tsx`
- Modify: `platform/web/src/app/app.css`

**Interfaces:**
- Consumes: six Helper jobs and bounded receipts.
- Produces: one-button Detect→Assign→Fullscreen→Verify flow, Swap/Retry/Re-witness/Close, friendly status, and non-certifying fallback.

- [ ] **Step 1: Write failing reducer, client, and UI tests**

Test strict receipt parsing, stale generation rejection, external Stage/internal
Presenter default, Swap invalidation, four compact steps, native witness
instructions, certified/syncing/invalidated statuses, Runtime unavailable,
Host unavailable, single/duplicate/remote topology, Escape/Close, reload, and
fallback. Assert default UI contains no UUID, raw display name, HWND, path,
digest, Runtime process path, or code value.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix platform/web test -- --run src/domain/projection.test.ts src/services/projection-client.test.ts src/components/TeachingSetup.test.tsx
```

Expected: FAIL because projection domain/client and native flow are absent.

- [ ] **Step 3: Implement the strict client and UI reducer**

Use this job-specific client boundary:

```typescript
export interface ProjectionClient {
  detect(input: DetectProjectionInput): Promise<ProjectionReceipt>;
  open(input: OpenProjectionInput): Promise<ProjectionReceipt>;
  assign(input: AssignProjectionInput): Promise<ProjectionReceipt>;
  fullscreen(input: FullscreenProjectionInput): Promise<ProjectionReceipt>;
  verify(input: VerifyProjectionInput): Promise<ProjectionReceipt>;
  close(input: CloseProjectionInput): Promise<ProjectionReceipt>;
}
```

The reducer accepts only a receipt matching its pending command ID, session ID,
generation, course projection, and manifest digest. Stale or mismatched results
cannot advance UI state.

- [ ] **Step 4: Implement the concise teaching setup**

Show one primary `Start dual-screen teaching` action. Internally execute Detect,
Open, Assign, and Fullscreen; then present the native witness instruction.
Render four compact progress labels and expand details only for attention.
Provide Swap before witness, Retry after retryable failure, Re-witness after
invalidation, and Close always. Show separate badges `Personal device session
certified` and `Release signature not certified`. Preserve browser rehearsal as
available but never certified.

- [ ] **Step 5: Run GREEN, accessibility, and visual build gates**

```powershell
npm.cmd --prefix platform/web test -- --run src/domain/projection.test.ts src/services/projection-client.test.ts src/components/TeachingSetup.test.tsx
npm.cmd --prefix platform/web test -- --run
npm.cmd --prefix platform/web run typecheck
npm.cmd --prefix platform/web run build
python platform/qa/run.py focused
git diff --check
```

Expected: all Web tests, typecheck, build, and focused QA pass; light-theme and
44 px accessible control gates remain green.

- [ ] **Step 6: Commit**

```powershell
git add -- platform/web/src/domain/projection.ts platform/web/src/domain/projection.test.ts platform/web/src/services/projection-client.ts platform/web/src/services/projection-client.test.ts platform/web/src/components/TeachingSetup.tsx platform/web/src/components/TeachingSetup.test.tsx platform/web/src/app/app.css
git commit -m "feat(studio): guide native dual-screen certification"
```

### Task 9: Real pipeline, visible hardware witness, and final gates

**Files:**
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.IntegrationTests/CourseStudio.ProjectionHost.IntegrationTests.csproj`
- Create: `platform/windows/tests/CourseStudio.ProjectionHost.IntegrationTests/ProjectionPipelineTests.cs`
- Create: `platform/helper/tests/test_projection_integration.py`
- Create: `platform/web/src/domain/projection.projection-integration.test.ts`
- Modify: `platform/qa/run.py`
- Modify: `platform/qa/test_run.py`
- Create: `platform/windows/evidence/projection-integration.json`
- Create: `platform/windows/evidence/physical-dual-screen-current.json`
- Create: `platform/windows/projection-design-qa.md`
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: all preceding tasks and one already published immutable course projection.
- Produces: non-certifying integration receipt, attended current-device hardware receipt, QA commands, and final design verdict.

- [ ] **Step 1: Write failing QA meta-tests before adding commands**

Require these commands and isolation rules:

```text
python platform/qa/run.py projection-restore
python platform/qa/run.py projection-contracts
python platform/qa/run.py projection-host
python platform/qa/run.py projection-integration
python platform/qa/run.py projection-hardware
```

`projection-restore` requires `COURSE_PROJECTION_RESTORE=1` and is the only
dependency-download gate. `projection-integration` requires
`COURSE_PROJECTION_INTEGRATION_TEST=1`. `projection-hardware` requires
`COURSE_PROJECTION_HARDWARE_TEST=1`, a local interactive console, current
published course, and visible windows. `focused` and `all` must keep every flag
unset and never restore, launch Host, open windows, or certify from automation.

- [ ] **Step 2: Run QA RED**

```powershell
python -m pytest platform/qa/test_run.py -q
```

Expected: FAIL because projection commands and isolation checks are missing.

- [ ] **Step 3: Implement marked integration tests and QA orchestration**

Mark real .NET tests `TestCategory=projection_integration`, Python tests
`pytest.mark.projection_integration`, and Web files with
`.projection-integration.test.ts`. Default runners explicitly exclude each
marker. The integration gate starts the real Helper, child Host, and WebView2
using the committed system-Chrome/browser policy where Chrome control is
needed, transfers one real published bootstrap and its assets, verifies equal
Stage/Presenter frame identities, injects one invalid digest and one Host
restart, proves cleanup/recovery, and seals
`platform/windows/evidence/projection-integration.json` with both certification
flags false.

- [ ] **Step 4: Run all automated gates before visible hardware**

```powershell
$env:COURSE_PROJECTION_INTEGRATION_TEST='1'
python platform/qa/run.py projection-integration
Remove-Item Env:COURSE_PROJECTION_INTEGRATION_TEST
python platform/qa/run.py projection-contracts
python platform/qa/run.py projection-host
python platform/qa/run.py focused
python platform/qa/run.py all
```

Expected: all pass; integration receipt is hash-bound and explicitly
non-certifying. Inventory and remove temporary Host publish, WebView2 UDF,
browser trace, screenshots, logs, and test databases after confirming they are
reproducible and inside ignored project paths.

- [ ] **Step 5: Run the attended current-device certification**

Run from a visible local console:

```powershell
$env:COURSE_PROJECTION_HARDWARE_TEST='1'
python platform/qa/run.py projection-hardware
Remove-Item Env:COURSE_PROJECTION_HARDWARE_TEST
```

The run must detect two eligible anonymous candidates corresponding to the
current integrated and Samsung displays, place Presenter/Stage on distinct
screens, enter exact fullscreen, complete the two-code native witness, advance
one frame through `syncing` back to certified, invalidate when one window is
moved or minimized, restore/re-witness, and close without an orphan process.

Expected final receipt fields:

```json
{
  "status": "verified",
  "mode": "attended-personal-device",
  "physicalDualScreenCertified": true,
  "releaseSignatureCertified": false,
  "operatorWitnessed": true,
  "distinctRoleDisplays": true,
  "exactFullscreenGeometry": true,
  "matchingCommittedFrame": true,
  "invalidationDemonstrated": true,
  "orphanProcessCount": 0
}
```

- [ ] **Step 6: Seal design QA and artifact inventory**

`projection-design-qa.md` records the current machine/receipt hashes,
certification scope, explicit non-certifying release status, commands, retained
files, removed temporary paths and sizes, regeneration methods, and any
deferred removal. It must not contain raw device identities, codes, tokens,
user-data paths, or course body.

- [ ] **Step 7: Record Supergrill experience and evolution check**

Create one strict redacted experience receipt for the dual-screen milestone,
add it with `experience-add`, then run `evolution-check`. If the store remains
ineligible, record `insufficient_evidence` and do not alter the stable skill. If
eligible, use `evolution-propose` to create a non-active candidate only; do not
activate it without explicit user approval.

- [ ] **Step 8: Commit final hardware evidence**

```powershell
git add -- platform/windows/tests/CourseStudio.ProjectionHost.IntegrationTests platform/helper/tests/test_projection_integration.py platform/web/src/domain/projection.projection-integration.test.ts platform/qa/run.py platform/qa/test_run.py platform/windows/evidence/projection-integration.json platform/windows/evidence/physical-dual-screen-current.json platform/windows/projection-design-qa.md .superpowers/sdd/progress.md
git commit -m "test(projection): certify current Win11 dual-screen session"
```

### Final milestone checkpoint

Before starting the separate one-click implementation plan, require:

```powershell
python platform/qa/run.py focused
python platform/qa/run.py all
git status --short
```

Expected: all gates pass, Git has no tracked changes, the current hardware
receipt remains hash-valid, `physicalDualScreenCertified=true` is scoped to its
attended historical session, and active UI correctly starts un-certified until
a fresh witness occurs.
