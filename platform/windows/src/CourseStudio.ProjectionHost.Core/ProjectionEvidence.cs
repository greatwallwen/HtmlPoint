using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace CourseStudio.ProjectionHost.Core;

public static class ProjectionEvidence
{
    public static string Sha256(string value) =>
        Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)));

    public static byte[] ToCanonicalUtf8<T>(T value)
    {
        JsonElement element = JsonSerializer.SerializeToElement(value, ProjectionJson.Options);
        using MemoryStream stream = new();
        using (Utf8JsonWriter writer = new(stream, new JsonWriterOptions { Indented = false }))
        {
            WriteCanonical(writer, element);
        }

        return stream.ToArray();
    }

    public static string Digest<T>(T value) =>
        Convert.ToHexStringLower(SHA256.HashData(ToCanonicalUtf8(value)));

    internal static ProjectionEvent EventFor(
        ProjectionState state,
        ProjectionSignal signal)
    {
        string seed = string.Join(
            '|',
            state.Generation.ToString(System.Globalization.CultureInfo.InvariantCulture),
            state.Phase.ToString(),
            signal.GetType().Name,
            state.Topology?.TopologyId ?? string.Empty,
            state.LatestFrame?.FrameDigest ?? string.Empty,
            state.Witness?.ChallengeDigest ?? string.Empty,
            state.InvalidationCode ?? string.Empty);
        Guid eventId = GuidFromSeed(seed);

        SortedDictionary<string, JsonElement> payload = new(StringComparer.Ordinal)
        {
            ["signalType"] = JsonSerializer.SerializeToElement(signal.GetType().Name),
        };
        if (state.InvalidationCode is not null)
        {
            payload["invalidationCode"] = JsonSerializer.SerializeToElement(
                state.InvalidationCode);
        }

        return new ProjectionEvent(
            1,
            eventId,
            GuidFromSeed($"session|{state.Topology?.TopologyId ?? "unbound"}"),
            checked((int)state.Generation),
            checked((int)state.Generation),
            DateTimeOffset.UnixEpoch.AddMilliseconds(state.Generation),
            EventTypeFor(state, signal),
            StatusFor(state.Phase),
            payload);
    }

    private static Guid GuidFromSeed(string seed)
    {
        byte[] identity = SHA256.HashData(Encoding.UTF8.GetBytes(seed));
        identity[6] = (byte)((identity[6] & 0x0F) | 0x40);
        identity[8] = (byte)((identity[8] & 0x3F) | 0x80);
        return new Guid(identity.AsSpan(0, 16));
    }

    private static ProjectionEventType EventTypeFor(
        ProjectionState state,
        ProjectionSignal signal)
    {
        if (state.Phase == ProjectionPhase.Invalidated)
        {
            return ProjectionEventType.SessionInvalidated;
        }

        return signal switch
        {
            DisplaysDetected => ProjectionEventType.TopologyDetected,
            WindowsAssigned => ProjectionEventType.WindowAssigned,
            FullscreenVerified => ProjectionEventType.FullscreenEntered,
            FrameCommitted => ProjectionEventType.FrameCommitted,
            WitnessChallengeIssued => ProjectionEventType.WitnessStarted,
            NativeWitnessAccepted => ProjectionEventType.SessionCertified,
            _ => ProjectionEventType.HostError,
        };
    }

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
        _ => throw new ArgumentOutOfRangeException(nameof(phase), phase, null),
    };

    private static void WriteCanonical(Utf8JsonWriter writer, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (JsonProperty property in element
                             .EnumerateObject()
                             .OrderBy(property => property.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    WriteCanonical(writer, property.Value);
                }

                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (JsonElement item in element.EnumerateArray())
                {
                    WriteCanonical(writer, item);
                }

                writer.WriteEndArray();
                break;
            case JsonValueKind.String:
                writer.WriteStringValue(element.GetString());
                break;
            case JsonValueKind.Number:
                writer.WriteRawValue(element.GetRawText(), skipInputValidation: false);
                break;
            case JsonValueKind.True:
                writer.WriteBooleanValue(true);
                break;
            case JsonValueKind.False:
                writer.WriteBooleanValue(false);
                break;
            case JsonValueKind.Null:
                writer.WriteNullValue();
                break;
            default:
                throw new JsonException($"Unsupported JSON value kind: {element.ValueKind}.");
        }
    }
}
