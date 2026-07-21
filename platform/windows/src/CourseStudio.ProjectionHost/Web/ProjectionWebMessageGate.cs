using System.Text.Json;
using System.Text.RegularExpressions;
using CourseStudio.ProjectionHost.Core;

namespace CourseStudio.ProjectionHost.Web;

public sealed record ProjectionWebBinding(
    Role Role,
    Guid ChannelId,
    string SessionId,
    string CourseVersionId,
    string RuntimeManifestDigest,
    string NavigationIdentity,
    long Generation);

public enum ProjectionWebMessageKind
{
    Ready,
    MessageAccepted,
    FrameCommitted,
    Control,
    Rejected,
}

public sealed record ProjectionTeachingControl(
    long BaseSequence,
    string LessonId,
    int LessonIndex,
    bool Playing,
    int ElapsedSeconds);

public sealed record ProjectionWebMessage(
    ProjectionWebMessageKind Kind,
    long? Sequence,
    string? FrameDigest,
    string? RejectionCode,
    ProjectionTeachingControl? Control = null);

public sealed partial class ProjectionWebMessageGate
{
    private static readonly HashSet<string> ReadyProperties =
    [
        "schemaVersion",
        "type",
        "role",
        "channelId",
    ];

    private static readonly HashSet<string> ReceiptProperties =
    [
        "schemaVersion",
        "type",
        "role",
        "channelId",
        "sessionId",
        "courseVersionId",
        "runtimeManifestDigest",
        "navigationIdentity",
        "generation",
        "sequence",
        "frameDigest",
    ];

    private static readonly HashSet<string> RejectionProperties =
    [
        "schemaVersion",
        "type",
        "role",
        "channelId",
        "code",
    ];

    private static readonly HashSet<string> ControlProperties =
    [
        "schemaVersion",
        "type",
        "role",
        "channelId",
        "sessionId",
        "courseVersionId",
        "runtimeManifestDigest",
        "navigationIdentity",
        "generation",
        "baseSequence",
        "lessonId",
        "lessonIndex",
        "playing",
        "elapsedSeconds",
    ];

    private readonly ProjectionWebBinding _binding;
    private readonly Dictionary<long, string> _knownFrames = [];
    private readonly HashSet<long> _accepted = [];
    private readonly HashSet<long> _committed = [];
    private long? _pendingControlBase;
    private bool _ready;

    public ProjectionWebMessageGate(ProjectionWebBinding binding)
    {
        ArgumentNullException.ThrowIfNull(binding);
        if (!SessionIdPattern().IsMatch(binding.SessionId)
            || !OpaqueIdPattern().IsMatch(binding.CourseVersionId)
            || !DigestPattern().IsMatch(binding.RuntimeManifestDigest)
            || !DigestPattern().IsMatch(binding.NavigationIdentity)
            || binding.Generation < 0
            || binding.Generation > int.MaxValue)
        {
            throw new ArgumentException("The WebView binding is invalid.", nameof(binding));
        }

        _binding = binding;
    }

    public bool IsReady => _ready;

    public void RegisterFrame(FrameIdentity frame)
    {
        ArgumentNullException.ThrowIfNull(frame);
        if (!string.Equals(frame.CourseVersionId, _binding.CourseVersionId, StringComparison.Ordinal)
            || !string.Equals(
                frame.RuntimeManifestDigest,
                _binding.RuntimeManifestDigest,
                StringComparison.Ordinal)
            || !string.Equals(
                frame.NavigationIdentity,
                _binding.NavigationIdentity,
                StringComparison.Ordinal)
            || frame.Sequence < 0
            || frame.Sequence > int.MaxValue
            || !DigestPattern().IsMatch(frame.FrameDigest)
            || (_knownFrames.Count > 0 && frame.Sequence <= _knownFrames.Keys.Max())
            || !_knownFrames.TryAdd(frame.Sequence, frame.FrameDigest))
        {
            throw new ProjectionWebMessageException("frame_registration_invalid");
        }
    }

    public ProjectionWebMessage Accept(string json)
    {
        if (string.IsNullOrWhiteSpace(json) || json.Length > 16 * 1024)
        {
            throw new ProjectionWebMessageException("web_message_invalid");
        }

        try
        {
            using JsonDocument document = JsonDocument.Parse(
                json,
                new JsonDocumentOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = 8,
                });
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !TryGetInt32(root, "schemaVersion", out int schemaVersion)
                || schemaVersion != 1
                || !TryGetString(root, "type", out string? type))
            {
                throw new ProjectionWebMessageException("web_message_invalid");
            }

            return type switch
            {
                "projection_ready" => AcceptReady(root),
                "message_accepted" => AcceptReceipt(
                    root,
                    ProjectionWebMessageKind.MessageAccepted),
                "frame_committed" => AcceptReceipt(
                    root,
                    ProjectionWebMessageKind.FrameCommitted),
                "projection_control" => AcceptControl(root),
                "projection_rejected" => AcceptRejection(root),
                _ => throw new ProjectionWebMessageException("web_message_invalid"),
            };
        }
        catch (ProjectionWebMessageException)
        {
            throw;
        }
        catch (JsonException)
        {
            throw new ProjectionWebMessageException("web_message_invalid");
        }
    }

    private ProjectionWebMessage AcceptReady(JsonElement root)
    {
        EnsureExactProperties(root, ReadyProperties);
        EnsureRoleAndChannel(root);
        if (_ready)
        {
            throw new ProjectionWebMessageException("projection_ready_replayed");
        }

        _ready = true;
        return new ProjectionWebMessage(
            ProjectionWebMessageKind.Ready,
            null,
            null,
            null);
    }

    private ProjectionWebMessage AcceptReceipt(
        JsonElement root,
        ProjectionWebMessageKind kind)
    {
        EnsureExactProperties(root, ReceiptProperties);
        EnsureRoleAndChannel(root);
        if (!_ready
            || !TryGetString(root, "sessionId", out string? sessionId)
            || !TryGetString(root, "courseVersionId", out string? courseVersionId)
            || !TryGetString(root, "runtimeManifestDigest", out string? manifestDigest)
            || !TryGetString(root, "navigationIdentity", out string? navigationIdentity)
            || !TryGetInt64(root, "generation", out long generation)
            || !TryGetInt64(root, "sequence", out long sequence)
            || !TryGetString(root, "frameDigest", out string? frameDigest)
            || !string.Equals(sessionId, _binding.SessionId, StringComparison.Ordinal)
            || !string.Equals(courseVersionId, _binding.CourseVersionId, StringComparison.Ordinal)
            || !string.Equals(manifestDigest, _binding.RuntimeManifestDigest, StringComparison.Ordinal)
            || !string.Equals(navigationIdentity, _binding.NavigationIdentity, StringComparison.Ordinal)
            || generation != _binding.Generation
            || !_knownFrames.TryGetValue(sequence, out string? knownDigest)
            || !string.Equals(frameDigest, knownDigest, StringComparison.Ordinal))
        {
            throw new ProjectionWebMessageException("web_receipt_identity_mismatch");
        }

        if (kind == ProjectionWebMessageKind.MessageAccepted)
        {
            if (!_accepted.Add(sequence))
            {
                throw new ProjectionWebMessageException("message_accepted_replayed");
            }
        }
        else if (!_accepted.Contains(sequence) || !_committed.Add(sequence))
        {
            throw new ProjectionWebMessageException("frame_commit_order_invalid");
        }

        if (kind == ProjectionWebMessageKind.FrameCommitted
            && _pendingControlBase is long pending
            && sequence > pending)
        {
            _pendingControlBase = null;
        }

        return new ProjectionWebMessage(kind, sequence, frameDigest, null);
    }

    private ProjectionWebMessage AcceptControl(JsonElement root)
    {
        EnsureExactProperties(root, ControlProperties);
        EnsureRoleAndChannel(root);
        if (_binding.Role != Role.Presenter)
        {
            throw new ProjectionWebMessageException("projection_control_forbidden");
        }

        if (!_ready
            || !BindingIdentityMatches(root)
            || !TryGetInt64(root, "baseSequence", out long baseSequence)
            || !TryGetString(root, "lessonId", out string? lessonId)
            || !TryGetInt32(root, "lessonIndex", out int lessonIndex)
            || !TryGetBoolean(root, "playing", out bool playing)
            || !TryGetInt32(root, "elapsedSeconds", out int elapsedSeconds)
            || lessonId is null
            || !OpaqueIdPattern().IsMatch(lessonId)
            || lessonIndex is < 0 or > 10_000
            || elapsedSeconds is < 0 or > 172_800
            || _committed.Count == 0
            || baseSequence != _committed.Max())
        {
            throw new ProjectionWebMessageException("projection_control_invalid");
        }

        if (_pendingControlBase is not null)
        {
            throw new ProjectionWebMessageException("projection_control_pending");
        }

        _pendingControlBase = baseSequence;
        return new ProjectionWebMessage(
            ProjectionWebMessageKind.Control,
            null,
            null,
            null,
            new ProjectionTeachingControl(
                baseSequence,
                lessonId,
                lessonIndex,
                playing,
                elapsedSeconds));
    }

    private ProjectionWebMessage AcceptRejection(JsonElement root)
    {
        EnsureExactProperties(root, RejectionProperties);
        EnsureRoleAndChannel(root);
        if (!TryGetString(root, "code", out string? code)
            || code is null
            || !ErrorCodePattern().IsMatch(code))
        {
            throw new ProjectionWebMessageException("web_message_invalid");
        }

        return new ProjectionWebMessage(
            ProjectionWebMessageKind.Rejected,
            null,
            null,
            code);
    }

    private void EnsureRoleAndChannel(JsonElement root)
    {
        string expectedRole = _binding.Role == Role.Stage ? "stage" : "presenter";
        if (!TryGetString(root, "role", out string? role)
            || !TryGetString(root, "channelId", out string? channelId)
            || !string.Equals(role, expectedRole, StringComparison.Ordinal)
            || !Guid.TryParseExact(channelId, "D", out Guid parsedChannel)
            || parsedChannel != _binding.ChannelId)
        {
            throw new ProjectionWebMessageException("web_receipt_identity_mismatch");
        }
    }

    private bool BindingIdentityMatches(JsonElement root) =>
        TryGetString(root, "sessionId", out string? sessionId)
        && TryGetString(root, "courseVersionId", out string? courseVersionId)
        && TryGetString(root, "runtimeManifestDigest", out string? manifestDigest)
        && TryGetString(root, "navigationIdentity", out string? navigationIdentity)
        && TryGetInt64(root, "generation", out long generation)
        && string.Equals(sessionId, _binding.SessionId, StringComparison.Ordinal)
        && string.Equals(courseVersionId, _binding.CourseVersionId, StringComparison.Ordinal)
        && string.Equals(
            manifestDigest,
            _binding.RuntimeManifestDigest,
            StringComparison.Ordinal)
        && string.Equals(
            navigationIdentity,
            _binding.NavigationIdentity,
            StringComparison.Ordinal)
        && generation == _binding.Generation;

    private static void EnsureExactProperties(
        JsonElement root,
        HashSet<string> expected)
    {
        int count = 0;
        foreach (JsonProperty property in root.EnumerateObject())
        {
            count++;
            if (!expected.Contains(property.Name))
            {
                throw new ProjectionWebMessageException("web_message_invalid");
            }
        }

        if (count != expected.Count)
        {
            throw new ProjectionWebMessageException("web_message_invalid");
        }
    }

    private static bool TryGetString(
        JsonElement root,
        string propertyName,
        out string? value)
    {
        value = null;
        return root.TryGetProperty(propertyName, out JsonElement property)
            && property.ValueKind == JsonValueKind.String
            && (value = property.GetString()) is not null;
    }

    private static bool TryGetInt32(
        JsonElement root,
        string propertyName,
        out int value)
    {
        value = 0;
        return root.TryGetProperty(propertyName, out JsonElement property)
            && property.ValueKind == JsonValueKind.Number
            && property.TryGetInt32(out value);
    }

    private static bool TryGetInt64(
        JsonElement root,
        string propertyName,
        out long value)
    {
        value = 0;
        return root.TryGetProperty(propertyName, out JsonElement property)
            && property.ValueKind == JsonValueKind.Number
            && property.TryGetInt64(out value);
    }

    private static bool TryGetBoolean(
        JsonElement root,
        string propertyName,
        out bool value)
    {
        value = false;
        if (!root.TryGetProperty(propertyName, out JsonElement property)
            || property.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
        {
            return false;
        }

        value = property.GetBoolean();
        return true;
    }

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", RegexOptions.CultureInvariant)]
    private static partial Regex SessionIdPattern();

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", RegexOptions.CultureInvariant)]
    private static partial Regex OpaqueIdPattern();

    [GeneratedRegex("^[0-9a-f]{64}$", RegexOptions.CultureInvariant)]
    private static partial Regex DigestPattern();

    [GeneratedRegex("^[a-z][a-z0-9_]{0,63}$", RegexOptions.CultureInvariant)]
    private static partial Regex ErrorCodePattern();
}

public sealed class ProjectionWebMessageException(string code)
    : InvalidOperationException(code)
{
    public string Code { get; } = code;
}
