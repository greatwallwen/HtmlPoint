using System.Security.Cryptography;
using System.IO;
using System.Text;
using System.Text.Json;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;
using CourseStudio.ProjectionHost.Web;
using CourseStudio.ProjectionHost.Windows;
using CourseStudio.ProjectionHost.Witness;

namespace CourseStudio.ProjectionHost.Transport;

internal sealed class NativeProjectionCommandExecutor : IProjectionHostCommandExecutor
{
    private readonly IDisplayTopologyProvider _topologyProvider;
    private readonly IRoleWindowController _windows;
    private readonly IProjectionPresentationSessionFactory _presentationFactory;
    private readonly IAttendedWitnessSession _witness;
    private readonly IProjectionReducer _reducer = new ProjectionReducer();
    private readonly object _stateGate = new();
    private byte[] _sessionSalt = RandomNumberGenerator.GetBytes(32);
    private DisplayTopology? _topology;
    private ProjectionHostOpenContext? _openContext;
    private Guid? _sessionId;
    private int _generation;
    private ProjectionStatus _status = ProjectionStatus.Undetected;
    private ProjectionState _certificationState = ProjectionState.Initial;
    private IProjectionPresentationSession? _presentation;
    private string? _invalidationCode;
    private FrameIdentity? _witnessedFrame;
    private bool _postWitnessSyncStarted;
    private bool _postWitnessFrameAdvanceCertified;
    private bool _disposed;

    internal NativeProjectionCommandExecutor(string runRoot)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(runRoot);
        RoleWindowController windows = new();
        _topologyProvider = new Win32DisplayTopologyProvider();
        _windows = windows;
        _presentationFactory = new NativeProjectionPresentationSessionFactory(
            windows,
            Path.Combine(AppContext.BaseDirectory, "web"),
            Path.Combine(runRoot, "webview"));
        _witness = new AttendedWitnessSession();
        windows.Invalidated += OnWindowInvalidated;
    }

    internal NativeProjectionCommandExecutor(
        IDisplayTopologyProvider topologyProvider,
        IRoleWindowController windows,
        IProjectionPresentationSessionFactory presentationFactory,
        IAttendedWitnessSession witness)
    {
        _topologyProvider = topologyProvider;
        _windows = windows;
        _presentationFactory = presentationFactory;
        _witness = witness;
        if (windows is RoleWindowController controller)
        {
            controller.Invalidated += OnWindowInvalidated;
        }
    }

    public async Task<ProjectionReceipt> ExecuteAsync(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext,
        CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        cancellationToken.ThrowIfCancellationRequested();
        if (command.SchemaVersion != 1)
        {
            openContext?.Dispose();
            return Reject(command, "schema_version_invalid");
        }

        try
        {
            return command.Command switch
            {
                ProjectionCommandName.DetectDisplays => Detect(command, openContext),
                ProjectionCommandName.OpenProjectionSession => Open(command, openContext),
                ProjectionCommandName.AssignProjectionWindow => await AssignAsync(
                    command,
                    openContext,
                    cancellationToken),
                ProjectionCommandName.EnterProjectionFullscreen => await FullscreenAsync(
                    command,
                    openContext,
                    cancellationToken),
                ProjectionCommandName.VerifyProjectionAssignment => await VerifyAsync(
                    command,
                    openContext,
                    cancellationToken),
                ProjectionCommandName.CloseProjectionSession => await CloseAsync(
                    command,
                    openContext,
                    cancellationToken),
                _ => Reject(command, "command_invalid"),
            };
        }
        catch (ProjectionWindowPolicyException exception)
        {
            openContext?.Dispose();
            Invalidate(exception.Code);
            return Reject(command, exception.Code);
        }
        catch (ProjectionWebPolicyException exception)
        {
            openContext?.Dispose();
            Invalidate(exception.Code);
            return Reject(command, exception.Code);
        }
        catch (WitnessRejectedException exception)
        {
            openContext?.Dispose();
            Invalidate(exception.Code);
            return Reject(command, exception.Code);
        }
        catch (WitnessConsumedException exception)
        {
            openContext?.Dispose();
            Invalidate(exception.Code);
            return Reject(command, exception.Code);
        }
        catch
        {
            openContext?.Dispose();
            throw;
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        if (_windows is RoleWindowController controller)
        {
            controller.Invalidated -= OnWindowInvalidated;
        }

        await DisposePresentationAsync();
        await _witness.InvalidateAsync("host_disposed", CancellationToken.None);
        await _windows.DisposeAsync();
        _openContext?.Dispose();
        _openContext = null;
        CryptographicOperations.ZeroMemory(_sessionSalt);
    }

    private ProjectionReceipt Detect(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext)
    {
        openContext?.Dispose();
        if (command.SessionId is not null || _sessionId is not null)
        {
            return Reject(command, "detect_session_invalid");
        }

        _topology = _topologyProvider.Read(_sessionSalt);
        bool eligible = IsEligible(_topology);
        _generation = command.ExpectedGeneration;
        _status = eligible ? ProjectionStatus.Candidate : ProjectionStatus.Undetected;
        _certificationState = ProjectionState.Initial;
        ResetWitnessedFrameProgress();
        if (eligible)
        {
            ApplySignal(new DisplaysDetected(_topology));
        }
        return Receipt(
            command,
            eligible,
            _status,
            eligible ? "display_candidate_ready" : "display_topology_ineligible",
            _generation,
            []);
    }

    private ProjectionReceipt Open(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext)
    {
        if (openContext is null || command.SessionId is null)
        {
            openContext?.Dispose();
            return Reject(command, "open_context_missing");
        }

        if (_sessionId is not null || _openContext is not null)
        {
            openContext.Dispose();
            return Reject(command, "session_already_open");
        }

        if (_topology is null || !IsEligible(_topology) || command.ExpectedGeneration != _generation)
        {
            openContext.Dispose();
            return Reject(
                command,
                command.ExpectedGeneration != _generation
                    ? "generation_mismatch"
                    : "display_topology_ineligible");
        }

        _openContext = openContext;
        _sessionId = command.SessionId;
        _status = ProjectionStatus.Candidate;
        _invalidationCode = null;
        return Receipt(
            command,
            true,
            _status,
            "projection_session_opened",
            _generation,
            []);
    }

    private async Task<ProjectionReceipt> AssignAsync(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext,
        CancellationToken cancellationToken)
    {
        openContext?.Dispose();
        string? invalid = ValidateSession(command);
        if (invalid is not null || _topology is null)
        {
            return Reject(command, invalid ?? "display_topology_missing");
        }

        bool swap = command.Payload.TryGetValue("swap", out JsonElement swapElement)
            && swapElement.ValueKind is JsonValueKind.True or JsonValueKind.False
            && swapElement.GetBoolean();
        RoleAssignment assignment = swap
            ? await _windows.SwapAsync(command.ExpectedGeneration, cancellationToken)
            : await _windows.OpenAsync(_topology, cancellationToken);
        _generation = checked((int)assignment.StageWindow.WindowGeneration);
        await _witness.InvalidateAsync("assignment_changed", CancellationToken.None);
        await DisposePresentationAsync();
        try
        {
            _presentation = await _presentationFactory.StartAsync(
                _openContext!,
                _sessionId!.Value,
                _generation,
                cancellationToken);
            _presentation.FrameCommitted += OnFrameCommitted;
            _presentation.SyncStarted += OnSyncStarted;
            _presentation.Invalidated += OnPresentationInvalidated;
        }
        catch
        {
            await _windows.CloseAsync(CancellationToken.None);
            throw;
        }

        _certificationState = ProjectionState.Initial;
        ResetWitnessedFrameProgress();
        ApplySignal(new DisplaysDetected(_topology));
        ApplySignal(new WindowsAssigned(
            assignment.StageWindow,
            assignment.PresenterWindow));
        _status = ProjectionStatus.Assigned;
        return Receipt(
            command,
            true,
            _status,
            swap ? "projection_roles_swapped" : "projection_windows_assigned",
            _generation,
            Assignments(assignment));
    }

    private async Task<ProjectionReceipt> FullscreenAsync(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext,
        CancellationToken cancellationToken)
    {
        openContext?.Dispose();
        string? invalid = ValidateSession(command);
        if (invalid is not null)
        {
            return Reject(command, invalid);
        }

        IReadOnlyList<RoleWindowEvidence> evidence = await _windows.EnterFullscreenAsync(
            command.ExpectedGeneration,
            cancellationToken);
        if (evidence.Count != 2 || evidence.Any(item => !item.IsExactFullscreen))
        {
            return Reject(command, "fullscreen_verification_failed");
        }

        if (_presentation is null)
        {
            return Reject(command, "presentation_session_missing");
        }

        RoleWindowEvidence stage = evidence.Single(item => item.Role == Role.Stage);
        RoleWindowEvidence presenter = evidence.Single(item => item.Role == Role.Presenter);
        ApplySignal(new FullscreenVerified(
            Geometry(stage),
            Geometry(presenter)));
        FrameIdentity initialFrame = _presentation.LatestFrame;
        ApplySignal(new FrameCommitted(Role.Stage, initialFrame));
        ApplySignal(new FrameCommitted(Role.Presenter, initialFrame));
        if (_certificationState.Phase != ProjectionPhase.Syncing)
        {
            return Reject(
                command,
                _certificationState.InvalidationCode ?? "frame_sync_failed");
        }

        _status = ProjectionStatus.Fullscreen;
        return Receipt(
            command,
            true,
            _status,
            "projection_fullscreen_verified",
            _generation,
            Assignments(evidence));
    }

    private async Task<ProjectionReceipt> VerifyAsync(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext,
        CancellationToken cancellationToken)
    {
        openContext?.Dispose();
        string? invalid = ValidateSession(command);
        if (invalid is not null)
        {
            return Reject(command, invalid);
        }

        IReadOnlyList<RoleWindowEvidence> evidence = await _windows.VerifyAsync(
            command.ExpectedGeneration,
            cancellationToken);
        if (evidence.Count != 2 || evidence.Any(item => !item.IsExactFullscreen))
        {
            return Reject(command, "fullscreen_verification_failed");
        }

        if (_certificationState.Phase == ProjectionPhase.Certified
            && _certificationState.PhysicalDualScreenCertified)
        {
            _status = ProjectionStatus.Certified;
            string message;
            lock (_stateGate)
            {
                message = _postWitnessFrameAdvanceCertified
                    ? "projection_assignment_certified_after_frame_advance"
                    : "projection_assignment_certified";
            }
            return Receipt(
                command,
                true,
                _status,
                message,
                _generation,
                Assignments(evidence));
        }

        if (_certificationState.Phase != ProjectionPhase.Syncing
            || _certificationState.LatestFrame is null
            || _topology is null)
        {
            return Reject(command, "projection_sync_incomplete");
        }

        _status = ProjectionStatus.WitnessPending;
        DateTimeOffset startedAt = DateTimeOffset.UtcNow;
        AttendedWitnessResult result = await _witness.RunAsync(
            evidence,
            new WitnessContext(_generation, _topology.TopologyId, true),
            startedAt,
            cancellationToken);
        if (result.Generation != _generation
            || !string.Equals(
                result.TopologyId,
                _topology.TopologyId,
                StringComparison.Ordinal)
            || result.Challenge.Generation != _generation
            || !string.Equals(
                result.Challenge.TopologyId,
                _topology.TopologyId,
                StringComparison.Ordinal))
        {
            Invalidate("witness_state_changed");
            return Reject(command, "witness_state_changed");
        }

        string challengeDigest = Digest(
            result.Challenge.ChallengeId,
            result.Challenge.ExpiresAt.ToUniversalTime().ToString("O"),
            result.Challenge.Generation.ToString(
                System.Globalization.CultureInfo.InvariantCulture),
            result.Challenge.TopologyId,
            _presentation?.RuntimeIdentityDigest ?? string.Empty);
        WitnessIdentity challenge = new(
            result.Challenge.ChallengeId,
            challengeDigest,
            startedAt,
            result.Challenge.ExpiresAt,
            null);
        ApplySignal(new WitnessChallengeIssued(
            challenge,
            result.Challenge.ExpiresAt));
        ApplySignal(new NativeWitnessAccepted(
            challenge with { ObservedAt = result.AcceptedAt },
            result.WitnessDigest));
        if (_certificationState.Phase != ProjectionPhase.Certified
            || !_certificationState.PhysicalDualScreenCertified
            || _certificationState.ReleaseSignatureCertified)
        {
            return Reject(
                command,
                _certificationState.InvalidationCode ?? "witness_rejected");
        }

        _status = ProjectionStatus.Certified;
        lock (_stateGate)
        {
            _witnessedFrame = _certificationState.LatestFrame;
            _postWitnessSyncStarted = false;
            _postWitnessFrameAdvanceCertified = false;
        }
        return Receipt(
            command,
            true,
            _status,
            "projection_assignment_certified",
            _generation,
            Assignments(evidence));
    }

    private async Task<ProjectionReceipt> CloseAsync(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext,
        CancellationToken cancellationToken)
    {
        openContext?.Dispose();
        string? invalid = ValidateSession(command, allowInvalidated: true);
        if (invalid is not null)
        {
            return Reject(command, invalid);
        }

        await _witness.InvalidateAsync("session_closed", CancellationToken.None);
        await DisposePresentationAsync();
        await _windows.CloseAsync(cancellationToken);
        _openContext?.Dispose();
        _openContext = null;
        _sessionId = null;
        _topology = null;
        _status = ProjectionStatus.Closed;
        _certificationState = ProjectionState.Initial;
        ResetWitnessedFrameProgress();
        _invalidationCode = null;
        CryptographicOperations.ZeroMemory(_sessionSalt);
        _sessionSalt = RandomNumberGenerator.GetBytes(32);
        return Receipt(
            command,
            true,
            _status,
            "projection_session_closed",
            _generation,
            []);
    }

    private string? ValidateSession(
        ProjectionCommand command,
        bool allowInvalidated = false)
    {
        if (command.SessionId is null
            || _sessionId is null
            || command.SessionId != _sessionId
            || _openContext is null)
        {
            return "session_identity_mismatch";
        }

        if (command.ExpectedGeneration != _generation)
        {
            return "generation_mismatch";
        }

        if (!allowInvalidated && _invalidationCode is not null)
        {
            return _invalidationCode;
        }

        return null;
    }

    private ProjectionReceipt Reject(ProjectionCommand command, string code) =>
        Receipt(
            command,
            false,
            _status == ProjectionStatus.Closed
                ? ProjectionStatus.Closed
                : ProjectionStatus.Invalidated,
            SafeCode(code),
            _generation,
            []);

    private static ProjectionReceipt Receipt(
        ProjectionCommand command,
        bool accepted,
        ProjectionStatus status,
        string message,
        int generation,
        IReadOnlyList<ProjectionAssignment> assignments) =>
        new(
            1,
            command.CommandId,
            command.SessionId,
            command.Command,
            accepted,
            status,
            generation,
            SafeCode(message),
            assignments);

    private static IReadOnlyList<ProjectionAssignment> Assignments(
        RoleAssignment assignment) =>
        [
            new ProjectionAssignment(
                ProjectionRole.Stage,
                assignment.StageWindow.DisplayId,
                checked((int)assignment.StageWindow.WindowGeneration)),
            new ProjectionAssignment(
                ProjectionRole.Presenter,
                assignment.PresenterWindow.DisplayId,
                checked((int)assignment.PresenterWindow.WindowGeneration)),
        ];

    private static IReadOnlyList<ProjectionAssignment> Assignments(
        IReadOnlyList<RoleWindowEvidence> evidence) =>
        evidence
            .OrderBy(item => item.Role)
            .Select(item => new ProjectionAssignment(
                item.Role == Role.Stage ? ProjectionRole.Stage : ProjectionRole.Presenter,
                item.DisplayId,
                checked((int)item.Generation)))
            .ToArray();

    private static bool IsEligible(DisplayTopology topology) =>
        string.Equals(topology.SessionKind, "interactive_local", StringComparison.Ordinal)
        && string.Equals(topology.Mode, "extended", StringComparison.Ordinal)
        && topology.Displays.Count == 2
        && topology.Displays.Select(item => item.DisplayId)
            .Distinct(StringComparer.Ordinal)
            .Count() == 2;

    private WindowGeometry Geometry(RoleWindowEvidence evidence)
    {
        ProjectionDisplay display = _topology?.Displays.SingleOrDefault(item =>
                string.Equals(
                    item.DisplayId,
                    evidence.DisplayId,
                    StringComparison.Ordinal))
            ?? throw new ProjectionWindowPolicyException("display_not_found");
        int dpi = Math.Clamp(
            checked((int)Math.Round(display.ScalePercent * 96d / 100d)),
            48,
            768);
        return new WindowGeometry(
            evidence.DisplayId,
            new ProjectionRectangle(
                checked((int)evidence.TargetRect.X),
                checked((int)evidence.TargetRect.Y),
                checked((int)evidence.TargetRect.Width),
                checked((int)evidence.TargetRect.Height)),
            dpi,
            evidence.IsExactFullscreen,
            evidence.IsMinimized,
            evidence.IsCloaked);
    }

    private void ApplySignal(ProjectionSignal signal)
    {
        lock (_stateGate)
        {
            _certificationState = _reducer.Apply(_certificationState, signal).State;
            _status = StatusFor(_certificationState.Phase);
            if (_certificationState.Phase == ProjectionPhase.Invalidated)
            {
                _invalidationCode = SafeCode(
                    _certificationState.InvalidationCode ?? "projection_invalidated");
            }
        }
    }

    private void Invalidate(string code)
    {
        string safe = SafeCode(code);
        lock (_stateGate)
        {
            _certificationState = _certificationState with
            {
                Phase = ProjectionPhase.Invalidated,
                Generation = checked(_certificationState.Generation + 1),
                PhysicalDualScreenCertified = false,
                ReleaseSignatureCertified = false,
                InvalidationCode = safe,
            };
            _invalidationCode = safe;
            _status = ProjectionStatus.Invalidated;
            _witnessedFrame = null;
            _postWitnessSyncStarted = false;
            _postWitnessFrameAdvanceCertified = false;
        }

        _ = _witness.InvalidateAsync(safe, CancellationToken.None);
    }

    private async Task DisposePresentationAsync()
    {
        IProjectionPresentationSession? presentation = _presentation;
        _presentation = null;
        if (presentation is null)
        {
            return;
        }

        presentation.FrameCommitted -= OnFrameCommitted;
        presentation.SyncStarted -= OnSyncStarted;
        presentation.Invalidated -= OnPresentationInvalidated;
        await presentation.DisposeAsync();
    }

    private void OnFrameCommitted(Role role, FrameIdentity frame)
    {
        if (_disposed || _invalidationCode is not null)
        {
            return;
        }

        ApplySignal(new FrameCommitted(role, frame));
        lock (_stateGate)
        {
            if (_postWitnessSyncStarted
                && _certificationState.Phase == ProjectionPhase.Certified
                && _certificationState.LatestFrame is not null
                && _witnessedFrame is not null
                && _certificationState.LatestFrame.Sequence > _witnessedFrame.Sequence)
            {
                _postWitnessFrameAdvanceCertified = true;
            }
        }
    }

    private void OnSyncStarted(FrameIdentity frame)
    {
        if (!_disposed && _invalidationCode is null)
        {
            lock (_stateGate)
            {
                if (_certificationState.Phase == ProjectionPhase.Certified
                    && _certificationState.PhysicalDualScreenCertified
                    && _witnessedFrame is not null
                    && frame.Sequence > _witnessedFrame.Sequence
                    && string.Equals(
                        frame.CourseVersionId,
                        _witnessedFrame.CourseVersionId,
                        StringComparison.Ordinal)
                    && string.Equals(
                        frame.RuntimeManifestDigest,
                        _witnessedFrame.RuntimeManifestDigest,
                        StringComparison.Ordinal)
                    && string.Equals(
                        frame.NavigationIdentity,
                        _witnessedFrame.NavigationIdentity,
                        StringComparison.Ordinal))
                {
                    _postWitnessSyncStarted = true;
                }

                _status = ProjectionStatus.Syncing;
            }
        }
    }

    private void ResetWitnessedFrameProgress()
    {
        lock (_stateGate)
        {
            _witnessedFrame = null;
            _postWitnessSyncStarted = false;
            _postWitnessFrameAdvanceCertified = false;
        }
    }

    private void OnPresentationInvalidated(string code) => Invalidate(code);

    private static ProjectionStatus StatusFor(ProjectionPhase phase) => phase switch
    {
        ProjectionPhase.Undetected => ProjectionStatus.Undetected,
        ProjectionPhase.Candidate => ProjectionStatus.Candidate,
        ProjectionPhase.Assigned => ProjectionStatus.Assigned,
        ProjectionPhase.Fullscreen => ProjectionStatus.Fullscreen,
        ProjectionPhase.Syncing => ProjectionStatus.Syncing,
        ProjectionPhase.WitnessPending => ProjectionStatus.WitnessPending,
        ProjectionPhase.Certified => ProjectionStatus.Certified,
        ProjectionPhase.Invalidated => ProjectionStatus.Invalidated,
        ProjectionPhase.Closed => ProjectionStatus.Closed,
        _ => ProjectionStatus.Invalidated,
    };

    private static string Digest(params string[] values)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(string.Join('|', values));
        try
        {
            return Convert.ToHexStringLower(SHA256.HashData(bytes));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(bytes);
        }
    }

    private static string SafeCode(string value) =>
        value.Length is > 0 and <= 64
            && value[0] is >= 'a' and <= 'z'
            && value.All(character =>
                character is >= 'a' and <= 'z'
                || character is >= '0' and <= '9'
                || character == '_')
                ? value
                : "host_failure";

    private void OnWindowInvalidated(Role role, string code)
    {
        _ = role;
        Invalidate(code);
    }
}
