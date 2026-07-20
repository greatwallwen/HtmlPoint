using System.Text.Json;
using System.Text.Json.Serialization;

namespace CourseStudio.ProjectionHost.Core;

public enum ProjectionCommandName
{
    DetectDisplays,
    OpenProjectionSession,
    AssignProjectionWindow,
    EnterProjectionFullscreen,
    VerifyProjectionAssignment,
    CloseProjectionSession,
}

public enum ProjectionStatus
{
    Undetected,
    Candidate,
    Assigned,
    Fullscreen,
    Syncing,
    WitnessPending,
    Certified,
    Invalidated,
    Closed,
}

public enum ProjectionRole
{
    Stage,
    Presenter,
}

public enum ProjectionEventType
{
    TopologyDetected,
    SessionOpened,
    WindowAssigned,
    FullscreenEntered,
    FrameCommitted,
    AssignmentVerified,
    WitnessStarted,
    WitnessConfirmed,
    SessionCertified,
    SessionInvalidated,
    SessionClosed,
    HostError,
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ProjectionCommand(
    [property: JsonPropertyName("schemaVersion")] int SchemaVersion,
    [property: JsonPropertyName("commandId")] Guid CommandId,
    [property: JsonPropertyName("command")] ProjectionCommandName Command,
    [property: JsonPropertyName("sessionId")] Guid? SessionId,
    [property: JsonPropertyName("expectedGeneration")] int ExpectedGeneration,
    [property: JsonPropertyName("payload")] IReadOnlyDictionary<string, JsonElement> Payload);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ProjectionRectangle(
    [property: JsonPropertyName("x")] int X,
    [property: JsonPropertyName("y")] int Y,
    [property: JsonPropertyName("width")] int Width,
    [property: JsonPropertyName("height")] int Height);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ProjectionDisplay(
    [property: JsonPropertyName("displayId")] string DisplayId,
    [property: JsonPropertyName("bounds")] ProjectionRectangle Bounds,
    [property: JsonPropertyName("workArea")] ProjectionRectangle WorkArea,
    [property: JsonPropertyName("isPrimary")] bool IsPrimary,
    [property: JsonPropertyName("isInternal")] bool IsInternal,
    [property: JsonPropertyName("scalePercent")] int ScalePercent,
    [property: JsonPropertyName("refreshRateMilliHertz")] int RefreshRateMilliHertz);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record DisplayTopology(
    [property: JsonPropertyName("schemaVersion")] int SchemaVersion,
    [property: JsonPropertyName("topologyId")] string TopologyId,
    [property: JsonPropertyName("capturedAt")] DateTimeOffset CapturedAt,
    [property: JsonPropertyName("sessionKind")] string SessionKind,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("displays")] IReadOnlyList<ProjectionDisplay> Displays);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ProjectionAssignment(
    [property: JsonPropertyName("role")] ProjectionRole Role,
    [property: JsonPropertyName("displayId")] string DisplayId,
    [property: JsonPropertyName("windowGeneration")] int WindowGeneration);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ProjectionReceipt(
    [property: JsonPropertyName("schemaVersion")] int SchemaVersion,
    [property: JsonPropertyName("commandId")] Guid CommandId,
    [property: JsonPropertyName("sessionId")] Guid? SessionId,
    [property: JsonPropertyName("command")] ProjectionCommandName Command,
    [property: JsonPropertyName("accepted")] bool Accepted,
    [property: JsonPropertyName("status")] ProjectionStatus Status,
    [property: JsonPropertyName("generation")] int Generation,
    [property: JsonPropertyName("message")] string Message,
    [property: JsonPropertyName("assignments")] IReadOnlyList<ProjectionAssignment> Assignments);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ProjectionEvent(
    [property: JsonPropertyName("schemaVersion")] int SchemaVersion,
    [property: JsonPropertyName("eventId")] Guid EventId,
    [property: JsonPropertyName("sessionId")] Guid SessionId,
    [property: JsonPropertyName("generation")] int Generation,
    [property: JsonPropertyName("sequence")] int Sequence,
    [property: JsonPropertyName("occurredAt")] DateTimeOffset OccurredAt,
    [property: JsonPropertyName("eventType")] ProjectionEventType EventType,
    [property: JsonPropertyName("status")] ProjectionStatus Status,
    [property: JsonPropertyName("payload")] IReadOnlyDictionary<string, JsonElement> Payload);

public static class ProjectionJson
{
    public static JsonSerializerOptions Options { get; } = CreateOptions();

    public static ProjectionCommand DeserializeCommand(string json)
    {
        ProjectionCommand command = JsonSerializer.Deserialize<ProjectionCommand>(json, Options)
            ?? throw new JsonException("Projection command cannot be null.");

        if (command.SchemaVersion != 1)
        {
            throw new JsonException("Unsupported projection schema version.");
        }

        if (command.ExpectedGeneration < 0 || command.Payload.Count > 32)
        {
            throw new JsonException("Projection command bounds are invalid.");
        }

        return command;
    }

    public static string Serialize<T>(T value) => JsonSerializer.Serialize(value, Options);

    private static JsonSerializerOptions CreateOptions()
    {
        JsonSerializerOptions options = new()
        {
            PropertyNameCaseInsensitive = false,
            RespectRequiredConstructorParameters = true,
            UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        };
        options.Converters.Add(
            new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower, allowIntegerValues: false));
        return options;
    }
}
