using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using CourseStudio.ProjectionHost.Core;

namespace CourseStudio.ProjectionHost.Transport;

public sealed class AuthenticatedEnvelopeCodec : IDisposable
{
    private const int GeneralLineBytes = 64 * 1024;
    private static readonly HashSet<string> EnvelopeProperties =
    [
        "schemaVersion",
        "direction",
        "sequence",
        "body",
        "mac",
    ];

    private readonly byte[] _key;
    private readonly string _sendDirection;
    private readonly int _maxLineBytes;
    private long _sendSequence;
    private long _receiveSequence;
    private bool _disposed;

    public AuthenticatedEnvelopeCodec(
        ReadOnlySpan<byte> key,
        string sendDirection,
        int maxLineBytes = 72 * 1024)
    {
        if (key.Length != 32)
        {
            throw new ArgumentException("Transport key must be 32 bytes.", nameof(key));
        }

        if (sendDirection is not ("helper" or "host"))
        {
            throw new ArgumentException("Transport direction is invalid.", nameof(sendDirection));
        }

        if (maxLineBytes < 256)
        {
            throw new ArgumentOutOfRangeException(nameof(maxLineBytes));
        }

        _key = key.ToArray();
        _sendDirection = sendDirection;
        _maxLineBytes = maxLineBytes;
    }

    public byte[] Encode(JsonElement payload)
    {
        EnsureAvailable();
        if (payload.ValueKind != JsonValueKind.Object)
        {
            throw new ProjectionTransportException("transport_payload_invalid");
        }

        byte[] body = ProjectionEvidence.ToCanonicalUtf8(payload);
        long sequence = _sendSequence;
        byte[] mac = FrameMac(_sendDirection, sequence, body);
        JsonElement envelope = JsonSerializer.SerializeToElement(new
        {
            schemaVersion = 1,
            direction = _sendDirection,
            sequence,
            body = Base64Url(body),
            mac = Base64Url(mac),
        });
        byte[] encoded = ProjectionEvidence.ToCanonicalUtf8(envelope);
        byte[] line = new byte[checked(encoded.Length + 1)];
        encoded.CopyTo(line, 0);
        line[^1] = (byte)'\n';
        int ceiling = IsAssetChunk(payload)
            ? _maxLineBytes
            : Math.Min(_maxLineBytes, GeneralLineBytes);
        if (line.Length > ceiling)
        {
            throw new ProjectionTransportException("transport_message_too_large");
        }

        _sendSequence++;
        return line;
    }

    public JsonDocument Decode(ReadOnlySpan<byte> line, string expectedDirection)
    {
        EnsureAvailable();
        if (expectedDirection is not ("helper" or "host"))
        {
            throw new ArgumentException("Expected direction is invalid.", nameof(expectedDirection));
        }

        if (line.Length == 0
            || line.Length > _maxLineBytes
            || line[^1] != (byte)'\n'
            || line[..^1].Contains((byte)'\n')
            || line.Contains((byte)'\r'))
        {
            throw new ProjectionTransportException("transport_message_too_large");
        }

        try
        {
            using JsonDocument envelopeDocument = JsonDocument.Parse(line[..^1].ToArray());
            JsonElement envelope = envelopeDocument.RootElement;
            EnsureExactProperties(
                envelope,
                EnvelopeProperties);
            if (envelope.GetProperty("schemaVersion").GetInt32() != 1)
            {
                throw new JsonException();
            }

            string direction = envelope.GetProperty("direction").GetString()
                ?? throw new JsonException();
            long sequence = envelope.GetProperty("sequence").GetInt64();
            if (!string.Equals(direction, expectedDirection, StringComparison.Ordinal))
            {
                throw new ProjectionTransportException("transport_direction_invalid");
            }

            byte[] body = DecodeBase64Url(
                envelope.GetProperty("body").GetString() ?? throw new JsonException());
            byte[] suppliedMac = DecodeBase64Url(
                envelope.GetProperty("mac").GetString() ?? throw new JsonException());
            byte[] expectedMac = FrameMac(direction, sequence, body);
            bool authenticated = CryptographicOperations.FixedTimeEquals(
                suppliedMac,
                expectedMac);
            CryptographicOperations.ZeroMemory(suppliedMac);
            CryptographicOperations.ZeroMemory(expectedMac);
            if (!authenticated)
            {
                throw new ProjectionTransportException("transport_authentication_failed");
            }

            if (sequence < _receiveSequence)
            {
                throw new ProjectionTransportException("transport_replay");
            }

            if (sequence != _receiveSequence)
            {
                throw new ProjectionTransportException("transport_sequence_invalid");
            }

            JsonDocument payload = JsonDocument.Parse(body);
            try
            {
                EnsureNoDuplicateProperties(payload.RootElement);
                if (payload.RootElement.ValueKind != JsonValueKind.Object)
                {
                    throw new JsonException();
                }

                int ceiling = IsAssetChunk(payload.RootElement)
                    ? _maxLineBytes
                    : Math.Min(_maxLineBytes, GeneralLineBytes);
                if (line.Length > ceiling)
                {
                    throw new ProjectionTransportException("transport_message_too_large");
                }

                _receiveSequence++;
                return payload;
            }
            catch
            {
                payload.Dispose();
                throw;
            }
        }
        catch (ProjectionTransportException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is JsonException
                or FormatException
                or InvalidOperationException
                or OverflowException)
        {
            throw new ProjectionTransportException("transport_authentication_failed");
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        CryptographicOperations.ZeroMemory(_key);
    }

    private byte[] FrameMac(string direction, long sequence, ReadOnlySpan<byte> body)
    {
        byte[] prefix = Encoding.ASCII.GetBytes(
            $"course-projection-v1\0{direction}\0{sequence.ToString(System.Globalization.CultureInfo.InvariantCulture)}\0");
        byte[] input = new byte[checked(prefix.Length + body.Length)];
        prefix.CopyTo(input, 0);
        body.CopyTo(input.AsSpan(prefix.Length));
        byte[] mac = HMACSHA256.HashData(_key, input);
        CryptographicOperations.ZeroMemory(input);
        return mac;
    }

    private static bool IsAssetChunk(JsonElement payload) =>
        payload.TryGetProperty("type", out JsonElement type)
        && type.ValueKind == JsonValueKind.String
        && string.Equals(type.GetString(), "asset_chunk", StringComparison.Ordinal);

    private static void EnsureExactProperties(
        JsonElement element,
        IReadOnlySet<string> expected)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            throw new JsonException();
        }

        HashSet<string> actual = new(StringComparer.Ordinal);
        foreach (JsonProperty property in element.EnumerateObject())
        {
            if (!actual.Add(property.Name))
            {
                throw new JsonException();
            }
        }

        if (!actual.SetEquals(expected))
        {
            throw new JsonException();
        }
    }

    private static void EnsureNoDuplicateProperties(JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.Object)
        {
            HashSet<string> names = new(StringComparer.Ordinal);
            foreach (JsonProperty property in element.EnumerateObject())
            {
                if (!names.Add(property.Name))
                {
                    throw new JsonException();
                }

                EnsureNoDuplicateProperties(property.Value);
            }
        }
        else if (element.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement item in element.EnumerateArray())
            {
                EnsureNoDuplicateProperties(item);
            }
        }
    }

    private static string Base64Url(ReadOnlySpan<byte> value) =>
        Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static byte[] DecodeBase64Url(string value)
    {
        if (value.Length == 0
            || value.Any(character =>
                !(character is >= 'A' and <= 'Z')
                && !(character is >= 'a' and <= 'z')
                && !(character is >= '0' and <= '9')
                && character is not ('_' or '-')))
        {
            throw new FormatException();
        }

        string padded = value.Replace('-', '+').Replace('_', '/');
        padded += new string('=', (4 - padded.Length % 4) % 4);
        byte[] decoded = Convert.FromBase64String(padded);
        if (!string.Equals(Base64Url(decoded), value, StringComparison.Ordinal))
        {
            CryptographicOperations.ZeroMemory(decoded);
            throw new FormatException();
        }

        return decoded;
    }

    private void EnsureAvailable()
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(nameof(AuthenticatedEnvelopeCodec));
        }
    }
}

public sealed class ProjectionTransportException(string code)
    : InvalidOperationException(code)
{
    public string Code { get; } = code;
}
