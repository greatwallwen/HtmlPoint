using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.IO;
using Microsoft.Win32.SafeHandles;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Web;

namespace CourseStudio.ProjectionHost.Transport;

internal sealed class ProjectionHostOpenContext : IDisposable
{
    private readonly ProjectionAssetStagingStore _stagingStore;
    private bool _disposed;

    internal ProjectionHostOpenContext(
        string courseVersionId,
        string runtimeManifestDigest,
        string navigationIdentity,
        JsonElement bootstrap,
        ProjectionAssetStagingStore stagingStore)
    {
        CourseVersionId = courseVersionId;
        RuntimeManifestDigest = runtimeManifestDigest;
        NavigationIdentity = navigationIdentity;
        Bootstrap = bootstrap.Clone();
        _stagingStore = stagingStore;
        SessionAssets = new ProjectionSessionAssets(
            stagingStore.CommittedRoot,
            stagingStore.CommittedAssets);
    }

    internal string CourseVersionId { get; }

    internal string RuntimeManifestDigest { get; }

    internal string NavigationIdentity { get; }

    internal JsonElement Bootstrap { get; }

    internal ProjectionSessionAssets SessionAssets { get; }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        SessionAssets.Dispose();
        _stagingStore.Dispose();
    }
}

internal interface IProjectionHostCommandExecutor : IAsyncDisposable
{
    Task<ProjectionReceipt> ExecuteAsync(
        ProjectionCommand command,
        ProjectionHostOpenContext? openContext,
        CancellationToken cancellationToken);
}

internal sealed partial class ProjectionHostProtocolProcessor : IAsyncDisposable
{
    private static readonly HashSet<string> CommandProperties = ["type", "command"];
    private static readonly HashSet<string> AssetBeginProperties =
        ["type", "assetId", "mediaType", "byteSize", "sha256"];
    private static readonly HashSet<string> AssetChunkProperties =
        ["type", "assetId", "offset", "data"];
    private static readonly HashSet<string> AssetCommitProperties =
        ["type", "assetId", "byteSize", "sha256"];
    private static readonly HashSet<string> BootstrapProperties =
    [
        "type",
        "command",
        "courseVersionId",
        "runtimeManifestDigest",
        "navigationIdentity",
        "bootstrap",
    ];

    private readonly IProjectionHostCommandExecutor _executor;
    private readonly Func<ProjectionAssetStagingStore> _stagingFactory;
    private ProjectionCommand? _pendingOpen;
    private string? _pendingOpenDigest;
    private ProjectionAssetStagingStore? _stagingStore;
    private bool _disposed;

    internal ProjectionHostProtocolProcessor(
        IProjectionHostCommandExecutor executor,
        Func<ProjectionAssetStagingStore> stagingFactory)
    {
        _executor = executor;
        _stagingFactory = stagingFactory;
    }

    internal async Task<JsonElement?> ProcessAsync(
        JsonElement message,
        CancellationToken cancellationToken)
    {
        EnsureAvailable();
        EnsureNoDuplicateProperties(message);
        if (message.ValueKind != JsonValueKind.Object
            || !message.TryGetProperty("type", out JsonElement typeElement)
            || typeElement.ValueKind != JsonValueKind.String)
        {
            throw new ProjectionTransportException("host_message_invalid");
        }

        string type = typeElement.GetString()!;
        try
        {
            return type switch
            {
                "projection_command" => await ProcessCommandAsync(
                    message,
                    cancellationToken),
                "asset_begin" => ProcessAssetBegin(message),
                "asset_chunk" => ProcessAssetChunk(message),
                "asset_commit" => ProcessAssetCommit(message),
                "projection_bootstrap" => await ProcessBootstrapAsync(
                    message,
                    cancellationToken),
                _ => throw new ProjectionTransportException("host_message_invalid"),
            };
        }
        catch
        {
            if (type is "projection_command" or "projection_bootstrap")
            {
                ResetPendingOpen();
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
        ResetPendingOpen();
        await _executor.DisposeAsync();
    }

    private async Task<JsonElement?> ProcessCommandAsync(
        JsonElement message,
        CancellationToken cancellationToken)
    {
        EnsureExactProperties(message, CommandProperties);
        ProjectionCommand command = ProjectionJson.DeserializeCommand(
            message.GetProperty("command").GetRawText());
        if (_pendingOpen is not null)
        {
            throw new ProjectionTransportException("open_session_already_pending");
        }

        if (command.Command == ProjectionCommandName.OpenProjectionSession)
        {
            if (command.SessionId is null)
            {
                throw new ProjectionTransportException("session_identity_missing");
            }

            _pendingOpen = command;
            _pendingOpenDigest = ProjectionEvidence.Digest(command);
            _stagingStore = _stagingFactory();
            return JsonSerializer.SerializeToElement(new
            {
                type = "asset_ready",
                commandId = command.CommandId,
            });
        }

        ProjectionReceipt receipt = await _executor.ExecuteAsync(
            command,
            null,
            cancellationToken);
        return ReceiptMessage(receipt);
    }

    private JsonElement? ProcessAssetBegin(JsonElement message)
    {
        EnsureExactProperties(message, AssetBeginProperties);
        ProjectionAssetStagingStore store = PendingStore();
        store.Begin(
            RequiredString(message, "assetId"),
            RequiredString(message, "mediaType"),
            RequiredInt64(message, "byteSize"),
            RequiredString(message, "sha256"));
        return null;
    }

    private JsonElement? ProcessAssetChunk(JsonElement message)
    {
        EnsureExactProperties(message, AssetChunkProperties);
        ProjectionAssetStagingStore store = PendingStore();
        byte[] bytes = DecodeBase64Url(RequiredString(message, "data"));
        try
        {
            store.Append(
                RequiredString(message, "assetId"),
                RequiredInt64(message, "offset"),
                bytes);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(bytes);
        }

        return null;
    }

    private JsonElement? ProcessAssetCommit(JsonElement message)
    {
        EnsureExactProperties(message, AssetCommitProperties);
        ProjectionAssetStagingStore store = PendingStore();
        store.Commit(
            RequiredString(message, "assetId"),
            RequiredInt64(message, "byteSize"),
            RequiredString(message, "sha256"));
        return null;
    }

    private async Task<JsonElement?> ProcessBootstrapAsync(
        JsonElement message,
        CancellationToken cancellationToken)
    {
        EnsureExactProperties(message, BootstrapProperties);
        ProjectionCommand command = ProjectionJson.DeserializeCommand(
            message.GetProperty("command").GetRawText());
        if (_pendingOpen is null
            || _pendingOpenDigest is null
            || ProjectionEvidence.Digest(command) != _pendingOpenDigest
            || _stagingStore is null
            || _stagingStore.HasPendingAssets)
        {
            throw new ProjectionTransportException("bootstrap_identity_invalid");
        }

        string courseVersionId = RequiredString(message, "courseVersionId");
        string runtimeManifestDigest = RequiredString(message, "runtimeManifestDigest");
        string navigationIdentity = RequiredString(message, "navigationIdentity");
        if (!OpaqueIdPattern().IsMatch(courseVersionId)
            || !DigestPattern().IsMatch(runtimeManifestDigest)
            || !DigestPattern().IsMatch(navigationIdentity))
        {
            throw new ProjectionTransportException("bootstrap_identity_invalid");
        }

        JsonElement bootstrap = message.GetProperty("bootstrap");
        if (bootstrap.ValueKind != JsonValueKind.Object
            || Encoding.UTF8.GetByteCount(bootstrap.GetRawText()) > 40 * 1024)
        {
            throw new ProjectionTransportException("bootstrap_size_exceeded");
        }

        ProjectionAssetStagingStore store = _stagingStore;
        _stagingStore = null;
        ProjectionHostOpenContext context = new(
            courseVersionId,
            runtimeManifestDigest,
            navigationIdentity,
            bootstrap,
            store);
        try
        {
            ProjectionReceipt receipt = await _executor.ExecuteAsync(
                command,
                context,
                cancellationToken);
            _pendingOpen = null;
            _pendingOpenDigest = null;
            return ReceiptMessage(receipt);
        }
        catch
        {
            context.Dispose();
            throw;
        }
    }

    private ProjectionAssetStagingStore PendingStore() =>
        _pendingOpen is not null && _stagingStore is not null
            ? _stagingStore
            : throw new ProjectionTransportException("asset_transfer_not_ready");

    private void ResetPendingOpen()
    {
        _pendingOpen = null;
        _pendingOpenDigest = null;
        _stagingStore?.Dispose();
        _stagingStore = null;
    }

    private static JsonElement ReceiptMessage(ProjectionReceipt receipt) =>
        JsonSerializer.SerializeToElement(
            new { type = "projection_receipt", receipt },
            ProjectionJson.Options);

    private static string RequiredString(JsonElement message, string propertyName) =>
        message.GetProperty(propertyName).ValueKind == JsonValueKind.String
            ? message.GetProperty(propertyName).GetString()!
            : throw new ProjectionTransportException("host_message_invalid");

    private static long RequiredInt64(JsonElement message, string propertyName)
    {
        JsonElement property = message.GetProperty(propertyName);
        if (property.ValueKind != JsonValueKind.Number || !property.TryGetInt64(out long value))
        {
            throw new ProjectionTransportException("host_message_invalid");
        }

        return value;
    }

    private static void EnsureExactProperties(
        JsonElement message,
        IReadOnlySet<string> expected)
    {
        HashSet<string> actual = message
            .EnumerateObject()
            .Select(property => property.Name)
            .ToHashSet(StringComparer.Ordinal);
        if (!actual.SetEquals(expected) || actual.Count != message.EnumerateObject().Count())
        {
            throw new ProjectionTransportException("host_message_invalid");
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
                    throw new ProjectionTransportException("host_message_invalid");
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

    private static byte[] DecodeBase64Url(string value)
    {
        if (!Base64UrlPattern().IsMatch(value))
        {
            throw new ProjectionTransportException("asset_chunk_invalid");
        }

        string padded = value.Replace('-', '+').Replace('_', '/');
        padded += new string('=', (4 - padded.Length % 4) % 4);
        try
        {
            byte[] decoded = Convert.FromBase64String(padded);
            string canonical = Convert.ToBase64String(decoded)
                .TrimEnd('=')
                .Replace('+', '-')
                .Replace('/', '_');
            if (!string.Equals(canonical, value, StringComparison.Ordinal))
            {
                CryptographicOperations.ZeroMemory(decoded);
                throw new ProjectionTransportException("asset_chunk_invalid");
            }

            return decoded;
        }
        catch (FormatException)
        {
            throw new ProjectionTransportException("asset_chunk_invalid");
        }
    }

    private void EnsureAvailable()
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(nameof(ProjectionHostProtocolProcessor));
        }
    }

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", RegexOptions.CultureInvariant)]
    private static partial Regex OpaqueIdPattern();

    [GeneratedRegex("^[0-9a-f]{64}$", RegexOptions.CultureInvariant)]
    private static partial Regex DigestPattern();

    [GeneratedRegex("^[A-Za-z0-9_-]+$", RegexOptions.CultureInvariant)]
    private static partial Regex Base64UrlPattern();
}

internal sealed class ProjectionHostProtocolServer
{
    private const int HandshakeLineBytes = 4096;
    private const int TransportLineBytes = 72 * 1024;

    private readonly Stream _input;
    private readonly Stream _output;
    private readonly ProjectionHostProtocolProcessor _processor;

    internal ProjectionHostProtocolServer(
        Stream input,
        Stream output,
        ProjectionHostProtocolProcessor processor)
    {
        _input = input;
        _output = output;
        _processor = processor;
    }

    internal async Task RunAsync(CancellationToken cancellationToken)
    {
        byte[] bootstrapKey = BootstrapSecret.Read();
        byte[] hostNonce = RandomNumberGenerator.GetBytes(32);
        byte[]? sessionKey = null;
        AuthenticatedEnvelopeCodec? codec = null;
        try
        {
            byte[] hostMac = HMACSHA256.HashData(
                bootstrapKey,
                Combine(Encoding.ASCII.GetBytes("host_hello\0"), hostNonce));
            await WriteRawAsync(
                JsonSerializer.SerializeToElement(new
                {
                    schemaVersion = 1,
                    type = "host_hello",
                    hostNonce = Base64Url(hostNonce),
                    mac = Base64Url(hostMac),
                }),
                cancellationToken);
            CryptographicOperations.ZeroMemory(hostMac);

            byte[] helperHelloLine = await ReadLineAsync(
                    _input,
                    HandshakeLineBytes,
                    cancellationToken)
                .WaitAsync(TimeSpan.FromSeconds(10), cancellationToken);
            sessionKey = AcceptHelperHello(bootstrapKey, hostNonce, helperHelloLine);
            codec = new AuthenticatedEnvelopeCodec(sessionKey, "host");

            while (!cancellationToken.IsCancellationRequested)
            {
                byte[]? line = await TryReadLineAsync(
                    _input,
                    TransportLineBytes,
                    cancellationToken);
                if (line is null)
                {
                    return;
                }

                using JsonDocument message = codec.Decode(line, "helper");
                JsonElement? response = await _processor.ProcessAsync(
                    message.RootElement,
                    cancellationToken);
                if (response is JsonElement value)
                {
                    await _output.WriteAsync(codec.Encode(value), cancellationToken);
                    await _output.FlushAsync(cancellationToken);
                }
            }
        }
        finally
        {
            codec?.Dispose();
            if (sessionKey is not null)
            {
                CryptographicOperations.ZeroMemory(sessionKey);
            }

            CryptographicOperations.ZeroMemory(hostNonce);
            CryptographicOperations.ZeroMemory(bootstrapKey);
            await _processor.DisposeAsync();
        }
    }

    private async Task WriteRawAsync(
        JsonElement message,
        CancellationToken cancellationToken)
    {
        byte[] bytes = ProjectionEvidence.ToCanonicalUtf8(message);
        if (bytes.Length + 1 > HandshakeLineBytes)
        {
            throw new ProjectionTransportException("host_handshake_invalid");
        }

        await _output.WriteAsync(bytes, cancellationToken);
        await _output.WriteAsync(new byte[] { (byte)'\n' }, cancellationToken);
        await _output.FlushAsync(cancellationToken);
    }

    private static byte[] AcceptHelperHello(
        ReadOnlySpan<byte> bootstrapKey,
        ReadOnlySpan<byte> hostNonce,
        ReadOnlySpan<byte> line)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(line[..^1].ToArray());
            JsonElement root = document.RootElement;
            HashSet<string> properties = root.EnumerateObject()
                .Select(property => property.Name)
                .ToHashSet(StringComparer.Ordinal);
            if (!properties.SetEquals(["schemaVersion", "type", "helperNonce", "mac"])
                || properties.Count != root.EnumerateObject().Count()
                || root.GetProperty("schemaVersion").GetInt32() != 1
                || !string.Equals(
                    root.GetProperty("type").GetString(),
                    "helper_hello",
                    StringComparison.Ordinal))
            {
                throw new JsonException();
            }

            byte[] helperNonce = DecodeHandshakeValue(root, "helperNonce");
            byte[] suppliedMac = DecodeHandshakeValue(root, "mac");
            if (helperNonce.Length != 32)
            {
                throw new JsonException();
            }

            byte[] expectedMac = HMACSHA256.HashData(
                bootstrapKey,
                Combine(
                    Encoding.ASCII.GetBytes("helper_hello\0"),
                    hostNonce,
                    helperNonce));
            bool matches = CryptographicOperations.FixedTimeEquals(
                suppliedMac,
                expectedMac);
            CryptographicOperations.ZeroMemory(suppliedMac);
            CryptographicOperations.ZeroMemory(expectedMac);
            if (!matches)
            {
                throw new JsonException();
            }

            byte[] sessionKey = HMACSHA256.HashData(
                bootstrapKey,
                Combine(
                    Encoding.ASCII.GetBytes("session\0"),
                    hostNonce,
                    helperNonce));
            CryptographicOperations.ZeroMemory(helperNonce);
            return sessionKey;
        }
        catch (Exception exception) when (
            exception is JsonException
                or FormatException
                or InvalidOperationException)
        {
            throw new ProjectionTransportException("host_handshake_invalid");
        }
    }

    private static byte[] DecodeHandshakeValue(JsonElement root, string propertyName)
    {
        string value = root.GetProperty(propertyName).GetString()
            ?? throw new JsonException();
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

    private static async Task<byte[]?> TryReadLineAsync(
        Stream stream,
        int maximumBytes,
        CancellationToken cancellationToken)
    {
        using MemoryStream buffer = new();
        byte[] single = new byte[1];
        while (buffer.Length <= maximumBytes)
        {
            int read = await stream.ReadAsync(single, cancellationToken);
            if (read == 0)
            {
                return buffer.Length == 0
                    ? null
                    : throw new ProjectionTransportException("host_eof_mid_frame");
            }

            buffer.WriteByte(single[0]);
            if (single[0] == (byte)'\r')
            {
                throw new ProjectionTransportException("transport_message_too_large");
            }

            if (single[0] == (byte)'\n')
            {
                return buffer.ToArray();
            }
        }

        throw new ProjectionTransportException("transport_message_too_large");
    }

    private static async Task<byte[]> ReadLineAsync(
        Stream stream,
        int maximumBytes,
        CancellationToken cancellationToken) =>
        await TryReadLineAsync(stream, maximumBytes, cancellationToken)
        ?? throw new ProjectionTransportException("host_eof");

    private static byte[] Combine(ReadOnlySpan<byte> first, ReadOnlySpan<byte> second)
    {
        byte[] combined = new byte[checked(first.Length + second.Length)];
        first.CopyTo(combined);
        second.CopyTo(combined.AsSpan(first.Length));

        return combined;
    }

    private static byte[] Combine(
        ReadOnlySpan<byte> first,
        ReadOnlySpan<byte> second,
        ReadOnlySpan<byte> third)
    {
        byte[] combined = new byte[checked(first.Length + second.Length + third.Length)];
        first.CopyTo(combined);
        second.CopyTo(combined.AsSpan(first.Length));
        third.CopyTo(combined.AsSpan(first.Length + second.Length));

        return combined;
    }

    private static string Base64Url(ReadOnlySpan<byte> value) =>
        Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}

internal static class BootstrapSecret
{
    private const string HandleVariable = "COURSE_PROJECTION_BOOTSTRAP_HANDLE";
    private const string ProtocolVariable = "COURSE_PROJECTION_PROTOCOL";

    internal static byte[] Read()
    {
        string? handleValue = Environment.GetEnvironmentVariable(HandleVariable);
        string? protocol = Environment.GetEnvironmentVariable(ProtocolVariable);
        Environment.SetEnvironmentVariable(HandleVariable, null);
        Environment.SetEnvironmentVariable(ProtocolVariable, null);
        if (!string.Equals(protocol, "1", StringComparison.Ordinal)
            || !long.TryParse(
                handleValue,
                System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture,
                out long rawHandle)
            || rawHandle <= 0)
        {
            throw new ProjectionTransportException("bootstrap_handle_invalid");
        }

        using SafeFileHandle handle = new(new nint(rawHandle), ownsHandle: true);
        using FileStream stream = new(handle, FileAccess.Read, bufferSize: 32, isAsync: false);
        byte[] secret = new byte[32];
        try
        {
            stream.ReadExactly(secret);
            if (stream.ReadByte() != -1)
            {
                throw new ProjectionTransportException("bootstrap_secret_invalid");
            }

            return secret;
        }
        catch
        {
            CryptographicOperations.ZeroMemory(secret);
            throw;
        }
    }
}
