using System.IO;
using System.Text.Json;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Windows;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace CourseStudio.ProjectionHost.Web;

public enum ProjectionResourceDecision
{
    Deny,
    AllowStatic,
    AllowSessionAsset,
}

public enum ProjectionRequestSource
{
    Document,
    SharedWorker,
    ServiceWorker,
}

public sealed record ProjectionWebSecuritySettings(
    bool NewWindowsAllowed,
    bool DownloadsAllowed,
    bool PermissionsAllowed,
    bool DevToolsAllowed,
    bool ServiceWorkersAllowed,
    bool HostObjectsAllowed,
    bool DefaultContextMenusAllowed,
    bool ExternalFetchAllowed)
{
    public static ProjectionWebSecuritySettings LockedDown { get; } = new(
        false,
        false,
        false,
        false,
        false,
        false,
        false,
        false);
}

public sealed class ProjectionWebPolicy
{
    public const string Origin = "https://projection.course-studio.test";

    private readonly ProjectionSessionAssets _sessionAssets;
    private readonly HashSet<string> _staticPaths;

    public ProjectionWebPolicy(
        ProjectionSessionAssets sessionAssets,
        IEnumerable<string> staticPaths)
    {
        _sessionAssets = sessionAssets;
        _staticPaths = staticPaths
            .Select(NormalizeStaticPath)
            .ToHashSet(StringComparer.Ordinal);
        if (!_staticPaths.Contains("/index.html"))
        {
            throw new ArgumentException("The projection bundle must map /index.html.", nameof(staticPaths));
        }
    }

    public bool IsNavigationAllowed(Uri uri) =>
        IsFixedOrigin(uri)
        && string.IsNullOrEmpty(uri.Query)
        && string.IsNullOrEmpty(uri.Fragment)
        && string.Equals(uri.AbsolutePath, "/index.html", StringComparison.Ordinal);

    public ProjectionResourceDecision DecideResource(
        Uri uri,
        ProjectionRequestSource source)
    {
        if (source is ProjectionRequestSource.ServiceWorker
            or ProjectionRequestSource.SharedWorker
            || !IsFixedOrigin(uri)
            || !string.IsNullOrEmpty(uri.Query)
            || !string.IsNullOrEmpty(uri.Fragment)
            || ContainsTraversal(uri))
        {
            return ProjectionResourceDecision.Deny;
        }

        if (_staticPaths.Contains(uri.AbsolutePath))
        {
            return ProjectionResourceDecision.AllowStatic;
        }

        const string prefix = "/session-assets/";
        if (!uri.AbsolutePath.StartsWith(prefix, StringComparison.Ordinal))
        {
            return ProjectionResourceDecision.Deny;
        }

        string opaqueId = Uri.UnescapeDataString(uri.AbsolutePath[prefix.Length..]);
        if (opaqueId.Length == 0 || opaqueId.Contains('/', StringComparison.Ordinal))
        {
            return ProjectionResourceDecision.Deny;
        }

        return _sessionAssets.Contains(opaqueId)
            ? ProjectionResourceDecision.AllowSessionAsset
            : ProjectionResourceDecision.Deny;
    }

    public bool TryOpenSessionAsset(
        Uri uri,
        out Stream? stream,
        out string? mediaType)
    {
        stream = null;
        mediaType = null;
        if (DecideResource(uri, ProjectionRequestSource.Document)
            != ProjectionResourceDecision.AllowSessionAsset)
        {
            return false;
        }

        string opaqueId = Uri.UnescapeDataString(
            uri.AbsolutePath["/session-assets/".Length..]);
        return _sessionAssets.TryOpen(opaqueId, out stream, out mediaType);
    }

    private static bool IsFixedOrigin(Uri uri) =>
        uri.IsAbsoluteUri
        && string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.Ordinal)
        && string.Equals(uri.Host, "projection.course-studio.test", StringComparison.Ordinal)
        && uri.Port == 443
        && string.IsNullOrEmpty(uri.UserInfo);

    private static bool ContainsTraversal(Uri uri)
    {
        string escaped = uri.GetComponents(UriComponents.Path, UriFormat.UriEscaped);
        string unescaped = Uri.UnescapeDataString(escaped);
        return unescaped.Split('/', StringSplitOptions.RemoveEmptyEntries)
            .Any(segment => segment is "." or "..");
    }

    private static string NormalizeStaticPath(string path)
    {
        if (string.IsNullOrWhiteSpace(path)
            || !path.StartsWith("/", StringComparison.Ordinal)
            || path.Contains("\\", StringComparison.Ordinal)
            || path.Split('/', StringSplitOptions.RemoveEmptyEntries)
                .Any(segment => segment is "." or ".."))
        {
            throw new ArgumentException("Static projection paths must be absolute URL paths.", nameof(path));
        }

        return path;
    }
}

public static class ProjectionWebBundle
{
    public static IReadOnlySet<string> Inventory(string root)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        string canonicalRoot = Path.GetFullPath(root);
        DirectoryInfo directory = new(canonicalRoot);
        if (!directory.Exists || directory.Attributes.HasFlag(FileAttributes.ReparsePoint))
        {
            throw new ProjectionWebPolicyException("web_bundle_root_invalid");
        }

        HashSet<string> paths = new(StringComparer.Ordinal);
        InventoryDirectory(directory, canonicalRoot, paths);
        if (!paths.Contains("/index.html"))
        {
            throw new ProjectionWebPolicyException("web_bundle_index_missing");
        }

        return paths;
    }

    private static void InventoryDirectory(
        DirectoryInfo directory,
        string root,
        HashSet<string> paths)
    {
        foreach (DirectoryInfo child in directory.EnumerateDirectories())
        {
            if (child.Attributes.HasFlag(FileAttributes.ReparsePoint))
            {
                throw new ProjectionWebPolicyException("web_bundle_reparse_point");
            }

            if (child.Name.Equals(".vite", StringComparison.Ordinal)
                || child.Name.StartsWith(".", StringComparison.Ordinal))
            {
                continue;
            }

            InventoryDirectory(child, root, paths);
        }

        foreach (FileInfo file in directory.EnumerateFiles())
        {
            if (file.Attributes.HasFlag(FileAttributes.ReparsePoint))
            {
                throw new ProjectionWebPolicyException("web_bundle_reparse_point");
            }

            if (file.Name.StartsWith(".", StringComparison.Ordinal)
                || file.Extension.Equals(".map", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            string relative = Path.GetRelativePath(root, file.FullName);
            string path = "/" + string.Join(
                '/',
                relative
                    .Split(
                        [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar],
                        StringSplitOptions.RemoveEmptyEntries)
                    .Select(Uri.EscapeDataString));
            if (!paths.Add(path))
            {
                throw new ProjectionWebPolicyException("web_bundle_path_collision");
            }
        }
    }
}

internal sealed class ProjectionWebViewHost : IAsyncDisposable
{
    private const string HostName = "projection.course-studio.test";
    private const string DocumentUrl = ProjectionWebPolicy.Origin + "/index.html";

    private readonly RoleWindow _window;
    private readonly string _webRoot;
    private readonly string _userDataFolder;
    private readonly ProjectionWebBinding _binding;
    private readonly ProjectionWebPolicy _resourcePolicy;
    private readonly ProjectionWebMessageGate _messageGate;
    private readonly WebView2 _view = new();
    private readonly List<Stream> _responseStreams = [];
    private CoreWebView2Environment? _environment;
    private CoreWebView2? _core;
    private WebViewRuntimePolicy? _runtimePolicy;
    private string? _bootstrapJson;
    private bool _bootstrapPosted;
    private bool _initialized;
    private bool _disposed;
    private bool _invalidated;

    private ProjectionWebViewHost(
        RoleWindow window,
        string webRoot,
        string userDataFolder,
        ProjectionSessionAssets sessionAssets,
        ProjectionWebBinding binding)
    {
        ArgumentNullException.ThrowIfNull(window);
        ArgumentNullException.ThrowIfNull(sessionAssets);
        ArgumentNullException.ThrowIfNull(binding);
        if (window.Role != binding.Role)
        {
            throw new ProjectionWebPolicyException("web_role_mismatch");
        }

        _window = window;
        _webRoot = Path.GetFullPath(webRoot);
        _userDataFolder = Path.GetFullPath(userDataFolder);
        _binding = binding;
        _resourcePolicy = new ProjectionWebPolicy(
            sessionAssets,
            ProjectionWebBundle.Inventory(_webRoot));
        _messageGate = new ProjectionWebMessageGate(binding);
    }

    internal event Action<ProjectionWebMessage>? MessageReceived;

    internal event Action<string>? Invalidated;

    internal WebViewRuntimeIdentity? RuntimeIdentity { get; private set; }

    internal static async Task<ProjectionWebViewHost> CreateAsync(
        RoleWindow window,
        string webRoot,
        string userDataFolder,
        ProjectionSessionAssets sessionAssets,
        ProjectionWebBinding binding,
        CancellationToken cancellationToken)
    {
        ProjectionWebViewHost host = new(
            window,
            webRoot,
            userDataFolder,
            sessionAssets,
            binding);
        try
        {
            await host.InitializeAsync(cancellationToken);
            return host;
        }
        catch
        {
            await host.DisposeAsync();
            throw;
        }
    }

    internal void StageBootstrap(string json, FrameIdentity frame)
    {
        EnsureAvailable();
        if (_bootstrapJson is not null)
        {
            throw new ProjectionWebPolicyException("bootstrap_already_staged");
        }

        ProjectionOutboundEnvelope.Validate(
            json,
            "projection_bootstrap",
            _binding,
            frame);
        _messageGate.RegisterFrame(frame);
        _bootstrapJson = json;
        PostBootstrapWhenReady();
    }

    internal void PostFrame(string json, FrameIdentity frame)
    {
        EnsureAvailable();
        if (!_bootstrapPosted)
        {
            throw new ProjectionWebPolicyException("bootstrap_not_posted");
        }

        ProjectionOutboundEnvelope.Validate(
            json,
            "projection_frame",
            _binding,
            frame);
        _messageGate.RegisterFrame(frame);
        _core!.PostWebMessageAsJson(json);
    }

    public async ValueTask DisposeAsync()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        if (_core is not null)
        {
            Unsubscribe(_core);
            try
            {
                await _core.Profile.ClearBrowsingDataAsync(
                    CoreWebView2BrowsingDataKinds.ServiceWorkers
                        | CoreWebView2BrowsingDataKinds.AllDomStorage
                        | CoreWebView2BrowsingDataKinds.CacheStorage);
            }
            catch (Exception exception) when (
                exception is InvalidOperationException
                    or System.Runtime.InteropServices.COMException)
            {
                // The isolated profile may already be closing. Nothing is reused.
            }

            try
            {
                _core.ClearVirtualHostNameToFolderMapping(HostName);
            }
            catch (Exception exception) when (
                exception is InvalidOperationException
                    or System.Runtime.InteropServices.COMException)
            {
                // The runtime process can exit before teardown finishes.
            }
        }

        _view.Dispose();
        foreach (Stream stream in _responseStreams)
        {
            stream.Dispose();
        }

        _responseStreams.Clear();
    }

    private async Task InitializeAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Directory.CreateDirectory(_userDataFolder);
        DirectoryInfo userData = new(_userDataFolder);
        if (userData.Attributes.HasFlag(FileAttributes.ReparsePoint))
        {
            throw new ProjectionWebPolicyException("web_user_data_reparse_point");
        }

        CoreWebView2EnvironmentOptions options = new()
        {
            AllowSingleSignOnUsingOSPrimaryAccount = false,
            AreBrowserExtensionsEnabled = false,
            EnableTrackingPrevention = false,
            ExclusiveUserDataFolderAccess = true,
            ReleaseChannels = CoreWebView2ReleaseChannels.Stable,
        };
        _environment = await CoreWebView2Environment.CreateAsync(
            browserExecutableFolder: null,
            userDataFolder: _userDataFolder,
            options);
        cancellationToken.ThrowIfCancellationRequested();

        _window.SetProjectionContent(_view);
        await _view.EnsureCoreWebView2Async(_environment);
        cancellationToken.ThrowIfCancellationRequested();
        _core = _view.CoreWebView2
            ?? throw new ProjectionWebPolicyException("webview_initialization_failed");

        ApplySecuritySettings(_core);
        Subscribe(_core);
        await _core.Profile.ClearBrowsingDataAsync(
            CoreWebView2BrowsingDataKinds.ServiceWorkers
                | CoreWebView2BrowsingDataKinds.AllDomStorage
                | CoreWebView2BrowsingDataKinds.CacheStorage);
        _core.Profile.AreWebViewScriptApisEnabledForServiceWorkers = false;

        _core.SetVirtualHostNameToFolderMapping(
            HostName,
            _webRoot,
            CoreWebView2HostResourceAccessKind.Deny);
        _core.AddWebResourceRequestedFilter(
            "*",
            CoreWebView2WebResourceContext.All,
            CoreWebView2WebResourceRequestSourceKinds.All);
        await _core.AddScriptToExecuteOnDocumentCreatedAsync(HandshakeScript(_binding));

        RuntimeIdentity = CaptureRuntimeIdentity(_environment);
        _runtimePolicy = new WebViewRuntimePolicy(RuntimeIdentity);
        _runtimePolicy.Verify(CaptureRuntimeIdentity(_environment));
        _initialized = true;
        _core.Navigate(DocumentUrl);
    }

    private static void ApplySecuritySettings(CoreWebView2 core)
    {
        CoreWebView2Settings settings = core.Settings;
        settings.IsScriptEnabled = true;
        settings.IsWebMessageEnabled = true;
        settings.AreDevToolsEnabled = false;
        settings.AreDefaultContextMenusEnabled = false;
        settings.AreDefaultScriptDialogsEnabled = false;
        settings.AreHostObjectsAllowed = false;
        settings.AreBrowserAcceleratorKeysEnabled = false;
        settings.IsStatusBarEnabled = false;
        settings.IsZoomControlEnabled = false;
        settings.IsGeneralAutofillEnabled = false;
        settings.IsPasswordAutosaveEnabled = false;
    }

    private void Subscribe(CoreWebView2 core)
    {
        core.NavigationStarting += OnNavigationStarting;
        core.NavigationCompleted += OnNavigationCompleted;
        core.NewWindowRequested += OnNewWindowRequested;
        core.DownloadStarting += OnDownloadStarting;
        core.PermissionRequested += OnPermissionRequested;
        core.LaunchingExternalUriScheme += OnLaunchingExternalUriScheme;
        core.WebResourceRequested += OnWebResourceRequested;
        core.WebMessageReceived += OnWebMessageReceived;
        core.ProcessFailed += OnProcessFailed;
        core.Profile.ServiceWorkerManager.ServiceWorkerRegistered += OnServiceWorkerRegistered;
        _environment!.ProcessInfosChanged += OnProcessInfosChanged;
    }

    private void Unsubscribe(CoreWebView2 core)
    {
        core.NavigationStarting -= OnNavigationStarting;
        core.NavigationCompleted -= OnNavigationCompleted;
        core.NewWindowRequested -= OnNewWindowRequested;
        core.DownloadStarting -= OnDownloadStarting;
        core.PermissionRequested -= OnPermissionRequested;
        core.LaunchingExternalUriScheme -= OnLaunchingExternalUriScheme;
        core.WebResourceRequested -= OnWebResourceRequested;
        core.WebMessageReceived -= OnWebMessageReceived;
        core.ProcessFailed -= OnProcessFailed;
        core.Profile.ServiceWorkerManager.ServiceWorkerRegistered -= OnServiceWorkerRegistered;
        if (_environment is not null)
        {
            _environment.ProcessInfosChanged -= OnProcessInfosChanged;
        }
    }

    private void OnNavigationStarting(
        object? sender,
        CoreWebView2NavigationStartingEventArgs eventArgs)
    {
        if (!Uri.TryCreate(eventArgs.Uri, UriKind.Absolute, out Uri? uri)
            || !_resourcePolicy.IsNavigationAllowed(uri))
        {
            eventArgs.Cancel = true;
            Invalidate("navigation_rejected");
        }
    }

    private void OnNavigationCompleted(
        object? sender,
        CoreWebView2NavigationCompletedEventArgs eventArgs)
    {
        if (!eventArgs.IsSuccess)
        {
            Invalidate("navigation_failed");
        }
    }

    private void OnNewWindowRequested(
        object? sender,
        CoreWebView2NewWindowRequestedEventArgs eventArgs)
    {
        eventArgs.Handled = true;
        Invalidate("new_window_rejected");
    }

    private void OnDownloadStarting(
        object? sender,
        CoreWebView2DownloadStartingEventArgs eventArgs)
    {
        eventArgs.Cancel = true;
        Invalidate("download_rejected");
    }

    private void OnPermissionRequested(
        object? sender,
        CoreWebView2PermissionRequestedEventArgs eventArgs)
    {
        eventArgs.State = CoreWebView2PermissionState.Deny;
        eventArgs.Handled = true;
        Invalidate("permission_rejected");
    }

    private void OnLaunchingExternalUriScheme(
        object? sender,
        CoreWebView2LaunchingExternalUriSchemeEventArgs eventArgs)
    {
        eventArgs.Cancel = true;
        Invalidate("external_uri_rejected");
    }

    private void OnWebResourceRequested(
        object? sender,
        CoreWebView2WebResourceRequestedEventArgs eventArgs)
    {
        CoreWebView2WebResourceRequestSourceKinds sourceKinds =
            eventArgs.RequestedSourceKind;
        ProjectionRequestSource source =
            (sourceKinds & CoreWebView2WebResourceRequestSourceKinds.ServiceWorker) != 0
                ? ProjectionRequestSource.ServiceWorker
                : (sourceKinds & CoreWebView2WebResourceRequestSourceKinds.SharedWorker) != 0
                    ? ProjectionRequestSource.SharedWorker
                    : ProjectionRequestSource.Document;
        if (!Uri.TryCreate(eventArgs.Request.Uri, UriKind.Absolute, out Uri? uri))
        {
            Deny(eventArgs);
            return;
        }

        ProjectionResourceDecision decision = _resourcePolicy.DecideResource(uri, source);
        if (decision == ProjectionResourceDecision.AllowStatic)
        {
            return;
        }

        if (decision == ProjectionResourceDecision.AllowSessionAsset
            && _resourcePolicy.TryOpenSessionAsset(uri, out Stream? stream, out string? mediaType)
            && stream is not null
            && mediaType is not null)
        {
            _responseStreams.Add(stream);
            eventArgs.Response = _environment!.CreateWebResourceResponse(
                stream,
                200,
                "OK",
                $"Content-Type: {mediaType}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff");
            return;
        }

        Deny(eventArgs);
    }

    private void Deny(CoreWebView2WebResourceRequestedEventArgs eventArgs)
    {
        MemoryStream empty = new(Array.Empty<byte>(), writable: false);
        _responseStreams.Add(empty);
        eventArgs.Response = _environment!.CreateWebResourceResponse(
            empty,
            403,
            "Forbidden",
            "Cache-Control: no-store\r\nContent-Type: text/plain; charset=utf-8");
    }

    private void OnWebMessageReceived(
        object? sender,
        CoreWebView2WebMessageReceivedEventArgs eventArgs)
    {
        if (eventArgs.AdditionalObjects.Count != 0
            || !Uri.TryCreate(eventArgs.Source, UriKind.Absolute, out Uri? source)
            || !_resourcePolicy.IsNavigationAllowed(source))
        {
            Invalidate("web_message_source_invalid");
            return;
        }

        try
        {
            ProjectionWebMessage message = _messageGate.Accept(eventArgs.WebMessageAsJson);
            if (message.Kind == ProjectionWebMessageKind.Rejected)
            {
                Invalidate(message.RejectionCode ?? "renderer_rejected");
                return;
            }

            MessageReceived?.Invoke(message);
            if (message.Kind == ProjectionWebMessageKind.Ready)
            {
                PostBootstrapWhenReady();
            }
        }
        catch (ProjectionWebMessageException exception)
        {
            Invalidate(exception.Code);
        }
    }

    private void OnProcessFailed(
        object? sender,
        CoreWebView2ProcessFailedEventArgs eventArgs) =>
        Invalidate("webview_process_failed");

    private void OnServiceWorkerRegistered(
        object? sender,
        CoreWebView2ServiceWorkerRegisteredEventArgs eventArgs) =>
        Invalidate("service_worker_rejected");

    private void OnProcessInfosChanged(object? sender, object eventArgs)
    {
        if (_disposed || _runtimePolicy is null || _environment is null)
        {
            return;
        }

        try
        {
            _runtimePolicy.Verify(CaptureRuntimeIdentity(_environment));
        }
        catch (RuntimeIdentityChangedException exception)
        {
            Invalidate(exception.Code);
        }
        catch (Exception exception) when (
            exception is ArgumentException
                or InvalidOperationException
                or IOException
                or UnauthorizedAccessException)
        {
            Invalidate("runtime_identity_changed");
        }
    }

    private void PostBootstrapWhenReady()
    {
        if (_bootstrapPosted || !_messageGate.IsReady || _bootstrapJson is null)
        {
            return;
        }

        _core!.PostWebMessageAsJson(_bootstrapJson);
        _bootstrapPosted = true;
    }

    private void Invalidate(string code)
    {
        if (_invalidated || _disposed)
        {
            return;
        }

        _invalidated = true;
        Invalidated?.Invoke(code);
    }

    private void EnsureAvailable()
    {
        if (!_initialized || _disposed || _invalidated || _core is null)
        {
            throw new ProjectionWebPolicyException("webview_unavailable");
        }
    }

    private static WebViewRuntimeIdentity CaptureRuntimeIdentity(
        CoreWebView2Environment environment) =>
        WebViewRuntimeIdentity.Capture(
            environment.BrowserVersionString,
            environment.GetProcessInfos().Select(process => process.ProcessId));

    private static string HandshakeScript(ProjectionWebBinding binding)
    {
        string role = binding.Role == Role.Stage ? "stage" : "presenter";
        string handshake = JsonSerializer.Serialize(new
        {
            schemaVersion = 1,
            role,
            channelId = binding.ChannelId,
            origin = ProjectionWebPolicy.Origin,
        });
        return $$"""
        (() => {
          "use strict";
          const handshake = Object.freeze({{handshake}});
          Object.defineProperty(window, "__courseStudioProjection", {
            value: handshake,
            writable: false,
            configurable: false,
            enumerable: false
          });
          const blocked = () => { throw new DOMException("Blocked by projection policy", "SecurityError"); };
          for (const key of ["localStorage", "sessionStorage"]) {
            try { Object.defineProperty(window, key, { get: blocked, configurable: false }); } catch {}
          }
          try { Object.defineProperty(navigator, "serviceWorker", { value: undefined, configurable: false }); } catch {}
        })();
        """;
    }
}

public sealed class ProjectionWebPolicyException(string code)
    : InvalidOperationException(code)
{
    public string Code { get; } = code;
}

internal static class ProjectionOutboundEnvelope
{
    internal static void Validate(
        string json,
        string expectedType,
        ProjectionWebBinding binding,
        FrameIdentity frame)
    {
        if (string.IsNullOrWhiteSpace(json) || json.Length > 16 * 1024 * 1024)
        {
            throw new ProjectionWebPolicyException("outbound_frame_invalid");
        }

        try
        {
            using JsonDocument document = JsonDocument.Parse(
                json,
                new JsonDocumentOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = 128,
                });
            JsonElement root = document.RootElement;
            string role = binding.Role == Role.Stage ? "stage" : "presenter";
            if (root.ValueKind != JsonValueKind.Object
                || !StringEquals(root, "type", expectedType)
                || !StringEquals(root, "role", role)
                || !StringEquals(root, "channelId", binding.ChannelId.ToString("D"))
                || !StringEquals(root, "sessionId", binding.SessionId)
                || !StringEquals(root, "courseVersionId", binding.CourseVersionId)
                || !StringEquals(
                    root,
                    "runtimeManifestDigest",
                    binding.RuntimeManifestDigest)
                || !StringEquals(root, "navigationIdentity", binding.NavigationIdentity)
                || !root.TryGetProperty("generation", out JsonElement generation)
                || !generation.TryGetInt64(out long generationValue)
                || generationValue != binding.Generation)
            {
                throw new ProjectionWebPolicyException("outbound_frame_identity_mismatch");
            }

            JsonElement frameRoot = expectedType == "projection_bootstrap"
                && root.TryGetProperty("frame", out JsonElement nestedFrame)
                    ? nestedFrame
                    : root;
            if (!frameRoot.TryGetProperty("sequence", out JsonElement sequence)
                || !sequence.TryGetInt64(out long sequenceValue)
                || sequenceValue != frame.Sequence
                || !StringEquals(frameRoot, "frameDigest", frame.FrameDigest))
            {
                throw new ProjectionWebPolicyException("outbound_frame_identity_mismatch");
            }
        }
        catch (ProjectionWebPolicyException)
        {
            throw;
        }
        catch (JsonException)
        {
            throw new ProjectionWebPolicyException("outbound_frame_invalid");
        }
    }

    private static bool StringEquals(
        JsonElement root,
        string propertyName,
        string expected) =>
        root.TryGetProperty(propertyName, out JsonElement property)
        && property.ValueKind == JsonValueKind.String
        && string.Equals(property.GetString(), expected, StringComparison.Ordinal);
}
