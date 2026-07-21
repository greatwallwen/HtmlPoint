using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.IO;
using System.Windows;
using System.Windows.Threading;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Transport;
using CourseStudio.ProjectionHost.Windows;

namespace CourseStudio.ProjectionHost.Web;

internal interface IProjectionWebSurface : IAsyncDisposable
{
    Role Role { get; }

    WebViewRuntimeIdentity RuntimeIdentity { get; }

    event Action<ProjectionWebMessage>? MessageReceived;

    event Action<string>? Invalidated;

    void StageBootstrap(string json, FrameIdentity frame);

    void PostFrame(string json, FrameIdentity frame);
}

internal interface IProjectionWebSurfaceFactory
{
    Task<IProjectionWebSurface> CreateAsync(
        Role role,
        ProjectionWebBinding binding,
        ProjectionSessionAssets sessionAssets,
        CancellationToken cancellationToken);
}

internal interface IProjectionPresentationSession : IAsyncDisposable
{
    event Action<Role, FrameIdentity>? FrameCommitted;

    event Action<FrameIdentity>? SyncStarted;

    event Action<string>? Invalidated;

    FrameIdentity LatestFrame { get; }

    string RuntimeIdentityDigest { get; }
}

internal interface IProjectionPresentationSessionFactory
{
    Task<IProjectionPresentationSession> StartAsync(
        ProjectionHostOpenContext context,
        Guid sessionId,
        long generation,
        CancellationToken cancellationToken);
}

internal sealed partial class ProjectionPresentationSession : IProjectionPresentationSession
{
    private static readonly TimeSpan CommitTimeout = TimeSpan.FromSeconds(25);

    private readonly object _stateGate = new();
    private readonly SemaphoreSlim _controlGate = new(1, 1);
    private readonly Dictionary<Role, IProjectionWebSurface> _surfaces = [];
    private readonly Dictionary<Role, ProjectionWebBinding> _bindings = [];
    private readonly HashSet<Role> _pendingCommits = [];
    private readonly string[] _lessonIds;
    private readonly JsonElement _course;
    private readonly JsonElement _slideDeck;
    private readonly string _courseDigest;
    private readonly string _sessionId;
    private FrameIdentity? _pendingFrame;
    private TaskCompletionSource<bool>? _commitWaiter;
    private TeachingFrameSnapshot _teachingFrame;
    private bool _disposed;

    private ProjectionPresentationSession(
        ProjectionPresentationContent content,
        Guid sessionId,
        long generation)
    {
        _course = content.Course;
        _slideDeck = content.SlideDeck;
        _courseDigest = content.CourseDigest;
        _lessonIds = content.LessonIds;
        _sessionId = sessionId.ToString("D");
        _teachingFrame = new TeachingFrameSnapshot(
            _sessionId,
            content.CourseVersionId,
            _lessonIds[0],
            0,
            _lessonIds.Length,
            false,
            0,
            0,
            DateTimeOffset.UtcNow);
        foreach (Role role in new[] { Role.Stage, Role.Presenter })
        {
            _bindings.Add(
                role,
                new ProjectionWebBinding(
                    role,
                    Guid.NewGuid(),
                    _sessionId,
                    content.CourseVersionId,
                    content.RuntimeManifestDigest,
                    content.NavigationIdentity,
                    generation));
        }
    }

    public event Action<Role, FrameIdentity>? FrameCommitted;

    public event Action<FrameIdentity>? SyncStarted;

    public event Action<string>? Invalidated;

    public FrameIdentity LatestFrame { get; private set; } = null!;

    public string RuntimeIdentityDigest { get; private set; } = string.Empty;

    internal static async Task<ProjectionPresentationSession> StartAsync(
        IProjectionWebSurfaceFactory factory,
        ProjectionHostOpenContext context,
        Guid sessionId,
        long generation,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(factory);
        ArgumentNullException.ThrowIfNull(context);
        ProjectionPresentationSession session = new(
            ProjectionPresentationContent.Parse(context),
            sessionId,
            generation);
        try
        {
            await session.InitializeAsync(factory, context, cancellationToken);
            return session;
        }
        catch
        {
            try
            {
                await session.DisposeAsync();
            }
            catch (Exception cleanupException) when (
                cleanupException is NotImplementedException
                    or InvalidOperationException
                    or System.Runtime.InteropServices.COMException)
            {
                // Preserve the creation failure; all native processes remain job-owned.
            }
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
        _controlGate.Dispose();
        foreach (IProjectionWebSurface surface in _surfaces.Values.Reverse())
        {
            await surface.DisposeAsync();
        }

        _surfaces.Clear();
        lock (_stateGate)
        {
            _commitWaiter?.TrySetCanceled();
            _commitWaiter = null;
            _pendingFrame = null;
            _pendingCommits.Clear();
        }
    }

    private async Task InitializeAsync(
        IProjectionWebSurfaceFactory factory,
        ProjectionHostOpenContext context,
        CancellationToken cancellationToken)
    {
        foreach (Role role in new[] { Role.Stage, Role.Presenter })
        {
            IProjectionWebSurface surface = await factory.CreateAsync(
                role,
                _bindings[role],
                context.SessionAssets,
                cancellationToken);
            surface.MessageReceived += message => OnMessage(role, message);
            surface.Invalidated += Invalidate;
            _surfaces.Add(role, surface);
        }

        RuntimeIdentityDigest = BindRuntimeIdentity();
        (FrameIdentity frame, Dictionary<Role, string> envelopes) = BuildFrame(
            _teachingFrame,
            bootstrap: true);
        Task committed = BeginPendingFrame(frame);
        foreach (Role role in new[] { Role.Stage, Role.Presenter })
        {
            _surfaces[role].StageBootstrap(envelopes[role], frame);
        }

        await committed.WaitAsync(CommitTimeout, cancellationToken);
    }

    private void OnMessage(Role role, ProjectionWebMessage message)
    {
        if (_disposed)
        {
            return;
        }

        if (message.Kind == ProjectionWebMessageKind.FrameCommitted)
        {
            ObserveCommit(role, message);
            return;
        }

        if (message.Kind == ProjectionWebMessageKind.Control
            && role == Role.Presenter
            && message.Control is not null)
        {
            _ = ApplyControlAsync(message.Control);
        }
    }

    private void ObserveCommit(Role role, ProjectionWebMessage message)
    {
        FrameIdentity? committed = null;
        TaskCompletionSource<bool>? waiter = null;
        lock (_stateGate)
        {
            FrameIdentity? pending = _pendingFrame;
            if (pending is null
                || message.Sequence != pending.Sequence
                || !string.Equals(
                    message.FrameDigest,
                    pending.FrameDigest,
                    StringComparison.Ordinal)
                || !_pendingCommits.Add(role))
            {
                Invalidate("frame_commit_invalid");
                return;
            }

            committed = pending;
            if (_pendingCommits.Count == 2)
            {
                LatestFrame = pending;
                _pendingFrame = null;
                _pendingCommits.Clear();
                waiter = _commitWaiter;
                _commitWaiter = null;
            }
        }

        FrameCommitted?.Invoke(role, committed);
        waiter?.TrySetResult(true);
    }

    private async Task ApplyControlAsync(ProjectionTeachingControl control)
    {
        try
        {
            await _controlGate.WaitAsync();
            if (_disposed)
            {
                return;
            }

            int index = Array.IndexOf(_lessonIds, control.LessonId);
            if (control.BaseSequence != LatestFrame.Sequence
                || index < 0
                || index != control.LessonIndex)
            {
                Invalidate("projection_control_invalid");
                return;
            }

            TeachingFrameSnapshot next = _teachingFrame with
            {
                LessonId = control.LessonId,
                LessonIndex = control.LessonIndex,
                Playing = control.Playing,
                ElapsedSeconds = control.ElapsedSeconds,
                Sequence = checked(_teachingFrame.Sequence + 1),
                SentAt = DateTimeOffset.UtcNow,
            };
            (FrameIdentity frame, Dictionary<Role, string> envelopes) = BuildFrame(
                next,
                bootstrap: false);
            Task committed = BeginPendingFrame(frame);
            _teachingFrame = next;
            SyncStarted?.Invoke(frame);
            foreach (Role role in new[] { Role.Stage, Role.Presenter })
            {
                _surfaces[role].PostFrame(envelopes[role], frame);
            }

            await committed.WaitAsync(CommitTimeout);
        }
        catch (Exception exception) when (
            exception is not OperationCanceledException
            && exception is not ObjectDisposedException)
        {
            Invalidate("frame_sync_failed");
        }
        finally
        {
            if (!_disposed)
            {
                _controlGate.Release();
            }
        }
    }

    private Task BeginPendingFrame(FrameIdentity frame)
    {
        lock (_stateGate)
        {
            if (_pendingFrame is not null || _commitWaiter is not null)
            {
                throw new ProjectionWebPolicyException("frame_sync_pending");
            }

            _pendingFrame = frame;
            _pendingCommits.Clear();
            _commitWaiter = new TaskCompletionSource<bool>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            return _commitWaiter.Task;
        }
    }

    private (FrameIdentity Frame, Dictionary<Role, string> Envelopes) BuildFrame(
        TeachingFrameSnapshot teaching,
        bool bootstrap)
    {
        Dictionary<string, object?> teachingValue = TeachingValue(teaching);
        string frameDigest = Convert.ToHexStringLower(
            SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(teachingValue)));
        ProjectionWebBinding stage = _bindings[Role.Stage];
        FrameIdentity identity = new(
            stage.CourseVersionId,
            stage.RuntimeManifestDigest,
            stage.NavigationIdentity,
            teaching.Sequence,
            frameDigest);
        Dictionary<Role, string> envelopes = [];
        foreach (Role role in new[] { Role.Stage, Role.Presenter })
        {
            ProjectionWebBinding binding = _bindings[role];
            Dictionary<string, object?> frame = new()
            {
                ["schemaVersion"] = 1,
                ["type"] = "projection_frame",
                ["role"] = RoleName(role),
                ["channelId"] = binding.ChannelId,
                ["sessionId"] = binding.SessionId,
                ["courseVersionId"] = binding.CourseVersionId,
                ["runtimeManifestDigest"] = binding.RuntimeManifestDigest,
                ["navigationIdentity"] = binding.NavigationIdentity,
                ["generation"] = binding.Generation,
                ["sequence"] = teaching.Sequence,
                ["frameDigest"] = frameDigest,
                ["teachingFrame"] = teachingValue,
            };
            Dictionary<string, object?> envelope = bootstrap
                ? new Dictionary<string, object?>
                {
                    ["schemaVersion"] = 1,
                    ["type"] = "projection_bootstrap",
                    ["role"] = RoleName(role),
                    ["channelId"] = binding.ChannelId,
                    ["sessionId"] = binding.SessionId,
                    ["courseVersionId"] = binding.CourseVersionId,
                    ["courseDigest"] = _courseDigest,
                    ["runtimeManifestDigest"] = binding.RuntimeManifestDigest,
                    ["navigationIdentity"] = binding.NavigationIdentity,
                    ["generation"] = binding.Generation,
                    ["course"] = _course,
                    ["slideDeck"] = _slideDeck,
                    ["frame"] = frame,
                }
                : frame;
            envelopes.Add(role, JsonSerializer.Serialize(envelope));
        }

        return (identity, envelopes);
    }

    private string BindRuntimeIdentity()
    {
        WebViewRuntimeIdentity stage = _surfaces[Role.Stage].RuntimeIdentity;
        WebViewRuntimeIdentity presenter = _surfaces[Role.Presenter].RuntimeIdentity;
        if (!string.Equals(
            stage.BrowserVersionString,
            presenter.BrowserVersionString,
            StringComparison.Ordinal))
        {
            throw new ProjectionWebPolicyException("runtime_identity_changed");
        }

        object safeIdentity = new
        {
            browserVersion = stage.BrowserVersionString,
            stage = stage.Processes.Select(process => new
            {
                process.Sha256,
                process.Publisher,
                process.SignatureValid,
            }),
            presenter = presenter.Processes.Select(process => new
            {
                process.Sha256,
                process.Publisher,
                process.SignatureValid,
            }),
        };
        return Convert.ToHexStringLower(
            SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(safeIdentity)));
    }

    private void Invalidate(string code)
    {
        if (_disposed || !ErrorCodePattern().IsMatch(code))
        {
            code = "presentation_invalidated";
        }

        TaskCompletionSource<bool>? waiter;
        lock (_stateGate)
        {
            waiter = _commitWaiter;
            _commitWaiter = null;
            _pendingFrame = null;
            _pendingCommits.Clear();
        }
        waiter?.TrySetException(new ProjectionWebPolicyException(code));
        Invalidated?.Invoke(code);
    }

    private static Dictionary<string, object?> TeachingValue(
        TeachingFrameSnapshot frame) => new()
    {
        ["sessionId"] = frame.SessionId,
        ["courseId"] = frame.CourseId,
        ["lessonId"] = frame.LessonId,
        ["lessonIndex"] = frame.LessonIndex,
        ["lessonCount"] = frame.LessonCount,
        ["playing"] = frame.Playing,
        ["elapsedSeconds"] = frame.ElapsedSeconds,
        ["sequence"] = frame.Sequence,
        ["sentAt"] = frame.SentAt.ToUniversalTime().ToString("O"),
    };

    private static string RoleName(Role role) =>
        role == Role.Stage ? "stage" : "presenter";

    [GeneratedRegex("^[a-z][a-z0-9_]{0,63}$", RegexOptions.CultureInvariant)]
    private static partial Regex ErrorCodePattern();

    private sealed record TeachingFrameSnapshot(
        string SessionId,
        string CourseId,
        string LessonId,
        int LessonIndex,
        int LessonCount,
        bool Playing,
        int ElapsedSeconds,
        long Sequence,
        DateTimeOffset SentAt);

    private sealed record ProjectionPresentationContent(
        string CourseVersionId,
        string CourseDigest,
        string RuntimeManifestDigest,
        string NavigationIdentity,
        JsonElement Course,
        JsonElement SlideDeck,
        string[] LessonIds)
    {
        internal static ProjectionPresentationContent Parse(
            ProjectionHostOpenContext context)
        {
            JsonElement root = context.Bootstrap;
            if (root.ValueKind != JsonValueKind.Object
                || !ExactProperties(
                    root,
                    "schemaVersion",
                    "courseDigest",
                    "course",
                    "projection")
                || root.GetProperty("schemaVersion").GetInt32() != 1)
            {
                throw new ProjectionWebPolicyException("projection_content_invalid");
            }

            string courseDigest = RequiredDigest(root, "courseDigest");
            JsonElement course = root.GetProperty("course");
            JsonElement projection = root.GetProperty("projection");
            if (course.ValueKind != JsonValueKind.Object
                || projection.ValueKind != JsonValueKind.Object
                || !ExactProperties(
                    projection,
                    "courseVersion",
                    "requirement",
                    "outline",
                    "slideDeck",
                    "runtimeManifest")
                || !StringEquals(course, "id", context.CourseVersionId))
            {
                throw new ProjectionWebPolicyException("projection_content_invalid");
            }

            JsonElement slideDeck = projection.GetProperty("slideDeck");
            if (slideDeck.ValueKind != JsonValueKind.Object
                || !StringEquals(
                    slideDeck,
                    "courseVersionId",
                    context.CourseVersionId))
            {
                throw new ProjectionWebPolicyException("projection_content_invalid");
            }

            string[] lessonIds = course.GetProperty("chapters")
                .EnumerateArray()
                .SelectMany(chapter => chapter.GetProperty("lessons").EnumerateArray())
                .Select(lesson => lesson.GetProperty("id").GetString())
                .Where(identifier => identifier is not null)
                .Cast<string>()
                .ToArray();
            if (lessonIds.Length == 0
                || lessonIds.Length > 10_000
                || lessonIds.Distinct(StringComparer.Ordinal).Count() != lessonIds.Length
                || lessonIds.Any(identifier => !OpaqueIdPattern().IsMatch(identifier)))
            {
                throw new ProjectionWebPolicyException("projection_content_invalid");
            }

            return new ProjectionPresentationContent(
                context.CourseVersionId,
                courseDigest,
                context.RuntimeManifestDigest,
                context.NavigationIdentity,
                course.Clone(),
                slideDeck.Clone(),
                lessonIds);
        }

        private static bool ExactProperties(JsonElement element, params string[] names)
        {
            HashSet<string> expected = names.ToHashSet(StringComparer.Ordinal);
            string[] actual = element.EnumerateObject()
                .Select(property => property.Name)
                .ToArray();
            return actual.Length == expected.Count
                && actual.All(expected.Contains);
        }

        private static string RequiredDigest(JsonElement root, string propertyName)
        {
            string? value = root.GetProperty(propertyName).GetString();
            return value is not null && DigestPattern().IsMatch(value)
                ? value
                : throw new ProjectionWebPolicyException("projection_content_invalid");
        }

        private static bool StringEquals(
            JsonElement root,
            string propertyName,
            string expected) =>
            root.TryGetProperty(propertyName, out JsonElement property)
            && property.ValueKind == JsonValueKind.String
            && string.Equals(property.GetString(), expected, StringComparison.Ordinal);
    }

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", RegexOptions.CultureInvariant)]
    private static partial Regex OpaqueIdPattern();

    [GeneratedRegex("^[0-9a-f]{64}$", RegexOptions.CultureInvariant)]
    private static partial Regex DigestPattern();
}

internal sealed class NativeProjectionPresentationSessionFactory(
    RoleWindowController windows,
    string webRoot,
    string userDataRoot) : IProjectionPresentationSessionFactory
{
    private readonly NativeProjectionWebSurfaceFactory _surfaces = new(
        windows,
        webRoot,
        userDataRoot);

    public async Task<IProjectionPresentationSession> StartAsync(
        ProjectionHostOpenContext context,
        Guid sessionId,
        long generation,
        CancellationToken cancellationToken) =>
        await ProjectionPresentationSession.StartAsync(
            _surfaces,
            context,
            sessionId,
            generation,
            cancellationToken);
}

internal sealed class NativeProjectionWebSurfaceFactory(
    RoleWindowController windows,
    string webRoot,
    string userDataRoot) : IProjectionWebSurfaceFactory
{
    public async Task<IProjectionWebSurface> CreateAsync(
        Role role,
        ProjectionWebBinding binding,
        ProjectionSessionAssets sessionAssets,
        CancellationToken cancellationToken)
    {
        try
        {
            Application application = Application.Current
                ?? throw new ProjectionWebPolicyException("wpf_dispatcher_unavailable");
            RoleWindow window = windows.NativeWindow(role);
            string userData = Path.Combine(
                userDataRoot,
                $"{role.ToString().ToLowerInvariant()}-{Guid.NewGuid():N}");
            DispatcherOperation<Task<ProjectionWebViewHost>> operation =
                application.Dispatcher.InvokeAsync(() => ProjectionWebViewHost.CreateAsync(
                    window,
                    webRoot,
                    userData,
                    sessionAssets,
                    binding,
                    cancellationToken));
            ProjectionWebViewHost host = await (await operation.Task);
            return new NativeProjectionWebSurface(host, role, application.Dispatcher);
        }
        catch (NotImplementedException)
        {
            throw new ProjectionWebPolicyException("webview_feature_unsupported");
        }
    }
}

internal sealed class NativeProjectionWebSurface : IProjectionWebSurface
{
    private readonly ProjectionWebViewHost _host;
    private readonly Dispatcher _dispatcher;

    internal NativeProjectionWebSurface(
        ProjectionWebViewHost host,
        Role role,
        Dispatcher dispatcher)
    {
        _host = host;
        Role = role;
        _dispatcher = dispatcher;
        _host.MessageReceived += message => MessageReceived?.Invoke(message);
        _host.Invalidated += code => Invalidated?.Invoke(code);
        RuntimeIdentity = host.RuntimeIdentity
            ?? throw new ProjectionWebPolicyException("runtime_identity_missing");
    }

    public Role Role { get; }

    public WebViewRuntimeIdentity RuntimeIdentity { get; }

    public event Action<ProjectionWebMessage>? MessageReceived;

    public event Action<string>? Invalidated;

    public void StageBootstrap(string json, FrameIdentity frame) =>
        Invoke(() => _host.StageBootstrap(json, frame));

    public void PostFrame(string json, FrameIdentity frame) =>
        Invoke(() => _host.PostFrame(json, frame));

    public async ValueTask DisposeAsync()
    {
        if (_dispatcher.CheckAccess())
        {
            await _host.DisposeAsync();
            return;
        }

        DispatcherOperation<ValueTask> operation =
            _dispatcher.InvokeAsync(() => _host.DisposeAsync());
        await (await operation.Task);
    }

    private void Invoke(Action action)
    {
        if (_dispatcher.CheckAccess())
        {
            action();
        }
        else
        {
            _dispatcher.Invoke(action);
        }
    }
}
