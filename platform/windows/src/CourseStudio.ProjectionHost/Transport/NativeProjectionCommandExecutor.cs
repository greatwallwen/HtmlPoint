using System.Security.Cryptography;
using System.Text.Json;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;
using CourseStudio.ProjectionHost.Windows;

namespace CourseStudio.ProjectionHost.Transport;

internal sealed class NativeProjectionCommandExecutor : IProjectionHostCommandExecutor
{
    private readonly IDisplayTopologyProvider _topologyProvider;
    private readonly IRoleWindowController _windows;
    private byte[] _sessionSalt = RandomNumberGenerator.GetBytes(32);
    private DisplayTopology? _topology;
    private ProjectionHostOpenContext? _openContext;
    private Guid? _sessionId;
    private int _generation;
    private ProjectionStatus _status = ProjectionStatus.Undetected;
    private string? _invalidationCode;
    private bool _disposed;

    internal NativeProjectionCommandExecutor()
        : this(new Win32DisplayTopologyProvider(), new RoleWindowController())
    {
    }

    internal NativeProjectionCommandExecutor(
        IDisplayTopologyProvider topologyProvider,
        IRoleWindowController windows)
    {
        _topologyProvider = topologyProvider;
        _windows = windows;
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
            _status = ProjectionStatus.Invalidated;
            _invalidationCode = exception.Code;
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
        _status = ProjectionStatus.WitnessPending;
        return Receipt(
            command,
            true,
            _status,
            "native_witness_required",
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

        await _windows.CloseAsync(cancellationToken);
        _openContext?.Dispose();
        _openContext = null;
        _sessionId = null;
        _topology = null;
        _status = ProjectionStatus.Closed;
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
        _invalidationCode = SafeCode(code);
        _status = ProjectionStatus.Invalidated;
    }
}
