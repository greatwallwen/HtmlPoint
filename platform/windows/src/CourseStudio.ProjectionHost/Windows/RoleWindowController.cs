using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;

namespace CourseStudio.ProjectionHost.Windows;

public sealed record RoleDisplayAssignment(
    string StageDisplayId,
    PhysicalRect StageBounds,
    string PresenterDisplayId,
    PhysicalRect PresenterBounds);

public sealed record RoleWindowEvidence(
    Role Role,
    string WindowId,
    string DisplayId,
    long Generation,
    PhysicalRect TargetRect,
    PhysicalRect WindowRect,
    PhysicalRect ExtendedFrameRect,
    PhysicalRect MonitorRect,
    bool IsVisible,
    bool IsMinimized,
    bool IsCloaked)
{
    public bool IsExactFullscreen =>
        IsVisible
        && !IsMinimized
        && !IsCloaked
        && WindowRect == TargetRect
        && ExtendedFrameRect == TargetRect
        && MonitorRect == TargetRect;
}

internal sealed record PlatformRoleWindow(
    Role Role,
    string WindowId,
    string DisplayId,
    PhysicalRect TargetBounds,
    long Generation,
    bool StyleWasSaved,
    object NativeToken);

internal sealed class PlatformWindowInvalidatedEventArgs(
    PlatformRoleWindow window,
    string code) : EventArgs
{
    public PlatformRoleWindow Window { get; } = window;

    public string Code { get; } = code;
}

internal interface IRoleWindowPlatform
{
    event EventHandler<PlatformWindowInvalidatedEventArgs>? Invalidated;

    Task<PlatformRoleWindow> CreateAsync(
        Role role,
        string displayId,
        PhysicalRect targetBounds,
        long generation,
        CancellationToken cancellationToken);

    Task<PlatformRoleWindow> AssignAsync(
        PlatformRoleWindow window,
        string displayId,
        PhysicalRect targetBounds,
        long generation,
        CancellationToken cancellationToken);

    Task EnterFullscreenAsync(
        PlatformRoleWindow window,
        CancellationToken cancellationToken);

    Task<RoleWindowEvidence> ReadEvidenceAsync(
        PlatformRoleWindow window,
        CancellationToken cancellationToken);

    Task RestoreAndCloseAsync(
        PlatformRoleWindow window,
        CancellationToken cancellationToken);
}

public static class RoleWindowAssignmentPolicy
{
    public static RoleDisplayAssignment Default(DisplayTopology topology)
    {
        ArgumentNullException.ThrowIfNull(topology);
        if (!string.Equals(topology.SessionKind, "interactive_local", StringComparison.Ordinal)
            || !string.Equals(topology.Mode, "extended", StringComparison.Ordinal)
            || topology.Displays.Count != 2
            || topology.Displays.Select(display => display.DisplayId)
                .Distinct(StringComparer.Ordinal)
                .Count() != 2)
        {
            throw new ProjectionWindowPolicyException("display_topology_ineligible");
        }

        ProjectionDisplay? internalDisplay = topology.Displays
            .SingleOrDefault(display => display.IsInternal);
        ProjectionDisplay? externalDisplay = topology.Displays
            .SingleOrDefault(display => !display.IsInternal);
        ProjectionDisplay presenter = internalDisplay
            ?? topology.Displays.Single(display => display.IsPrimary);
        ProjectionDisplay stage = externalDisplay
            ?? topology.Displays.Single(display => !display.IsPrimary);
        if (string.Equals(stage.DisplayId, presenter.DisplayId, StringComparison.Ordinal))
        {
            throw new ProjectionWindowPolicyException("role_collision");
        }

        return new RoleDisplayAssignment(
            stage.DisplayId,
            ToPhysicalRect(stage.Bounds),
            presenter.DisplayId,
            ToPhysicalRect(presenter.Bounds));
    }

    public static RoleDisplayAssignment Swap(RoleDisplayAssignment assignment) =>
        new(
            assignment.PresenterDisplayId,
            assignment.PresenterBounds,
            assignment.StageDisplayId,
            assignment.StageBounds);

    private static PhysicalRect ToPhysicalRect(ProjectionRectangle rectangle) =>
        new(rectangle.X, rectangle.Y, rectangle.Width, rectangle.Height);
}

public interface IRoleWindowController : IAsyncDisposable
{
    Task<RoleAssignment> OpenAsync(
        DisplayTopology topology,
        CancellationToken cancellationToken);

    Task<RoleAssignment> SwapAsync(
        long expectedGeneration,
        CancellationToken cancellationToken);

    Task<IReadOnlyList<RoleWindowEvidence>> EnterFullscreenAsync(
        long expectedGeneration,
        CancellationToken cancellationToken);

    Task<IReadOnlyList<RoleWindowEvidence>> VerifyAsync(
        long expectedGeneration,
        CancellationToken cancellationToken);

    Task CloseAsync(CancellationToken cancellationToken);
}

public sealed class RoleWindowController : IRoleWindowController
{
    private readonly IRoleWindowPlatform _platform;
    private PlatformRoleWindow? _stage;
    private PlatformRoleWindow? _presenter;
    private RoleDisplayAssignment? _assignment;
    private long _generation;
    private string? _invalidationCode;

    public RoleWindowController()
        : this(new NativeRoleWindowPlatform())
    {
    }

    internal RoleWindowController(IRoleWindowPlatform platform)
    {
        _platform = platform;
        _platform.Invalidated += OnPlatformInvalidated;
    }

    public event Action<Role, string>? Invalidated;

    public bool IsOpen => _stage is not null && _presenter is not null;

    public async Task<RoleAssignment> OpenAsync(
        DisplayTopology topology,
        CancellationToken cancellationToken)
    {
        if (IsOpen)
        {
            throw new ProjectionWindowPolicyException("session_already_open");
        }

        RoleDisplayAssignment assignment = RoleWindowAssignmentPolicy.Default(topology);
        _generation = 1;
        _invalidationCode = null;
        try
        {
            _stage = await _platform.CreateAsync(
                Role.Stage,
                assignment.StageDisplayId,
                assignment.StageBounds,
                _generation,
                cancellationToken);
            _presenter = await _platform.CreateAsync(
                Role.Presenter,
                assignment.PresenterDisplayId,
                assignment.PresenterBounds,
                _generation,
                cancellationToken);
            _assignment = assignment;
            return ToRoleAssignment();
        }
        catch
        {
            await RollbackPartialOpenAsync();
            throw;
        }
    }

    public async Task<RoleAssignment> SwapAsync(
        long expectedGeneration,
        CancellationToken cancellationToken)
    {
        EnsureReady(expectedGeneration);
        PlatformRoleWindow oldStage = _stage!;
        PlatformRoleWindow oldPresenter = _presenter!;
        RoleDisplayAssignment oldAssignment = _assignment!;
        RoleDisplayAssignment swapped = RoleWindowAssignmentPolicy.Swap(oldAssignment);
        long nextGeneration = checked(_generation + 1);
        try
        {
            PlatformRoleWindow stage = await _platform.AssignAsync(
                oldStage,
                swapped.StageDisplayId,
                swapped.StageBounds,
                nextGeneration,
                cancellationToken);
            PlatformRoleWindow presenter = await _platform.AssignAsync(
                oldPresenter,
                swapped.PresenterDisplayId,
                swapped.PresenterBounds,
                nextGeneration,
                cancellationToken);
            _stage = stage;
            _presenter = presenter;
            _assignment = swapped;
            _generation = nextGeneration;
            return ToRoleAssignment();
        }
        catch
        {
            _invalidationCode = "swap_failed";
            await CloseAsync(CancellationToken.None);
            throw;
        }
    }

    public async Task<IReadOnlyList<RoleWindowEvidence>> EnterFullscreenAsync(
        long expectedGeneration,
        CancellationToken cancellationToken)
    {
        EnsureReady(expectedGeneration);
        try
        {
            await _platform.EnterFullscreenAsync(_stage!, cancellationToken);
            await _platform.EnterFullscreenAsync(_presenter!, cancellationToken);
        }
        catch
        {
            _invalidationCode = "fullscreen_partial_failure";
            await CloseAsync(CancellationToken.None);
            throw;
        }

        return await VerifyAsync(expectedGeneration, cancellationToken);
    }

    public async Task<IReadOnlyList<RoleWindowEvidence>> VerifyAsync(
        long expectedGeneration,
        CancellationToken cancellationToken)
    {
        EnsureReady(expectedGeneration);
        RoleWindowEvidence stage = await _platform.ReadEvidenceAsync(
            _stage!,
            cancellationToken);
        RoleWindowEvidence presenter = await _platform.ReadEvidenceAsync(
            _presenter!,
            cancellationToken);
        if (string.Equals(stage.WindowId, presenter.WindowId, StringComparison.Ordinal)
            || string.Equals(stage.DisplayId, presenter.DisplayId, StringComparison.Ordinal))
        {
            return ThrowInvalid([stage, presenter], "role_collision");
        }

        foreach (RoleWindowEvidence evidence in new[] { stage, presenter })
        {
            if (!evidence.IsVisible)
            {
                return ThrowInvalid([stage, presenter], "window_hidden");
            }

            if (evidence.IsMinimized)
            {
                return ThrowInvalid([stage, presenter], "window_minimized");
            }

            if (evidence.IsCloaked)
            {
                return ThrowInvalid([stage, presenter], "window_cloaked");
            }

            if (!evidence.IsExactFullscreen)
            {
                return ThrowInvalid([stage, presenter], "window_moved");
            }
        }

        return [stage, presenter];
    }

    public async Task CloseAsync(CancellationToken cancellationToken)
    {
        List<Exception> failures = [];
        foreach (PlatformRoleWindow? window in new[] { _presenter, _stage })
        {
            if (window is null)
            {
                continue;
            }

            try
            {
                await _platform.RestoreAndCloseAsync(window, CancellationToken.None);
            }
            catch (Exception exception)
            {
                failures.Add(exception);
            }
        }

        Reset();
        if (failures.Count > 0)
        {
            throw new AggregateException("One or more role windows failed to close.", failures);
        }
    }

    public async ValueTask DisposeAsync()
    {
        _platform.Invalidated -= OnPlatformInvalidated;
        if (IsOpen)
        {
            await CloseAsync(CancellationToken.None);
        }
    }

    private IReadOnlyList<RoleWindowEvidence> ThrowInvalid(
        IReadOnlyList<RoleWindowEvidence> evidence,
        string code)
    {
        _invalidationCode = code;
        Role role = evidence.FirstOrDefault(item => !item.IsExactFullscreen)?.Role
            ?? Role.Stage;
        Invalidated?.Invoke(role, code);
        throw new ProjectionWindowInvalidatedException(code);
    }

    private void EnsureReady(long expectedGeneration)
    {
        if (!IsOpen || _assignment is null)
        {
            throw new ProjectionWindowPolicyException("session_not_open");
        }

        if (_invalidationCode is not null)
        {
            throw new ProjectionWindowInvalidatedException(_invalidationCode);
        }

        if (expectedGeneration != _generation)
        {
            throw new ProjectionWindowGenerationException(_generation, expectedGeneration);
        }
    }

    private RoleAssignment ToRoleAssignment() =>
        new(
            new WindowIdentity(
                _stage!.WindowId,
                _stage.DisplayId,
                _stage.Generation),
            new WindowIdentity(
                _presenter!.WindowId,
                _presenter.DisplayId,
                _presenter.Generation));

    private async Task RollbackPartialOpenAsync()
    {
        foreach (PlatformRoleWindow? window in new[] { _presenter, _stage })
        {
            if (window is not null)
            {
                try
                {
                    await _platform.RestoreAndCloseAsync(window, CancellationToken.None);
                }
                catch
                {
                    // Preserve the original open failure; all owned references are dropped below.
                }
            }
        }

        Reset();
    }

    private void Reset()
    {
        _stage = null;
        _presenter = null;
        _assignment = null;
        _generation = 0;
        _invalidationCode = null;
    }

    private void OnPlatformInvalidated(
        object? sender,
        PlatformWindowInvalidatedEventArgs eventArgs)
    {
        _invalidationCode = eventArgs.Code;
        Invalidated?.Invoke(eventArgs.Window.Role, eventArgs.Code);
    }
}

public class ProjectionWindowPolicyException(string code)
    : InvalidOperationException(code)
{
    public string Code { get; } = code;
}

public sealed class ProjectionWindowInvalidatedException(string code)
    : ProjectionWindowPolicyException(code);

public sealed class ProjectionWindowGenerationException(
    long actualGeneration,
    long expectedGeneration)
    : ProjectionWindowPolicyException("generation_mismatch")
{
    public long ActualGeneration { get; } = actualGeneration;

    public long ExpectedGeneration { get; } = expectedGeneration;
}
