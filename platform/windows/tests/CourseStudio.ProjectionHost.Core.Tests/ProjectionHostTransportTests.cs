using System.Security.Cryptography;
using System.Text.Json;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Transport;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class ProjectionHostTransportTests
{
    [TestMethod]
    public void AuthenticatedEnvelopeRejectsTamperReplayDirectionAndOversize()
    {
        byte[] key = Enumerable.Range(0, 32).Select(value => checked((byte)value)).ToArray();
        using AuthenticatedEnvelopeCodec helper = new(key, "helper", 1024);
        using AuthenticatedEnvelopeCodec host = new(key, "host", 1024);
        using JsonDocument payload = JsonDocument.Parse("{\"type\":\"detect_displays\"}");
        byte[] frame = helper.Encode(payload.RootElement);

        using JsonDocument decoded = host.Decode(frame, "helper");
        Assert.AreEqual("detect_displays", decoded.RootElement.GetProperty("type").GetString());
        Assert.AreEqual(
            "transport_replay",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                host.Decode(frame, "helper")).Code);

        byte[] tampered = frame.ToArray();
        tampered[^8] ^= 1;
        using AuthenticatedEnvelopeCodec fresh = new(key, "host", 1024);
        Assert.AreEqual(
            "transport_authentication_failed",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                fresh.Decode(tampered, "helper")).Code);

        byte[] wrongDirection = helper.Encode(payload.RootElement);
        using AuthenticatedEnvelopeCodec directionGate = new(key, "host", 1024);
        Assert.AreEqual(
            "transport_direction_invalid",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                directionGate.Decode(wrongDirection, "host")).Code);

        using JsonDocument large = JsonDocument.Parse(
            JsonSerializer.Serialize(new { type = "large", value = new string('x', 5000) }));
        Assert.AreEqual(
            "transport_message_too_large",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                helper.Encode(large.RootElement)).Code);

        byte[] crlf = [.. frame[..^1], (byte)'\r', (byte)'\n'];
        using AuthenticatedEnvelopeCodec crlfGate = new(key, "host", 1024);
        Assert.AreEqual(
            "transport_message_too_large",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                crlfGate.Decode(crlf, "helper")).Code);
    }

    [TestMethod]
    public void PythonAuthenticatedFrameFixtureDecodesWithExactIdentity()
    {
        byte[] key = Enumerable.Range(0, 32).Select(value => checked((byte)value)).ToArray();
        using AuthenticatedEnvelopeCodec host = new(key, "host");
        byte[] fixture = File.ReadAllBytes(FindFixture("authenticated-helper-frame.txt"));

        using JsonDocument decoded = host.Decode(fixture, "helper");

        Assert.AreEqual("detect_displays", decoded.RootElement.GetProperty("type").GetString());
    }

    [TestMethod]
    public void AssetIsInvisibleUntilOrderedBytesMatchSizeAndDigest()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-assets-{Guid.NewGuid():N}");
        byte[] content = Enumerable.Range(0, 100_000)
            .Select(value => checked((byte)(value % 251)))
            .ToArray();
        string digest = Convert.ToHexStringLower(SHA256.HashData(content));
        using ProjectionAssetStagingStore store = new(root);

        store.Begin("artifact-1", "image/png", content.Length, digest);
        store.Append("artifact-1", 0, content.AsSpan(0, 36 * 1024));
        Assert.AreEqual(0, store.CommittedAssets.Count);
        Assert.AreEqual(
            "asset_offset_invalid",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                store.Append("artifact-1", 1, content.AsSpan(36 * 1024, 1))).Code);

        store.Append(
            "artifact-1",
            36 * 1024,
            content.AsSpan(36 * 1024, 36 * 1024));
        store.Append(
            "artifact-1",
            72 * 1024,
            content.AsSpan(72 * 1024));
        store.Commit("artifact-1", content.Length, digest);

        Assert.AreEqual(1, store.CommittedAssets.Count);
        Assert.AreEqual("artifact-1", store.CommittedAssets[0].OpaqueId);
        Assert.IsTrue(File.Exists(store.CommittedAssets[0].Path));
    }

    [TestMethod]
    public void AssetStoreRejectsDigestMismatchAndBundleCeilingsWithoutCommit()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-assets-{Guid.NewGuid():N}");
        using ProjectionAssetStagingStore store = new(root);
        byte[] content = [1, 2, 3];
        store.Begin("artifact-1", "image/png", content.Length, new string('a', 64));
        store.Append("artifact-1", 0, content);
        Assert.AreEqual(
            "asset_digest_mismatch",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                store.Commit("artifact-1", content.Length, new string('a', 64))).Code);
        Assert.AreEqual(0, store.CommittedAssets.Count);

        Assert.AreEqual(
            "asset_size_exceeded",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                store.Begin(
                    "artifact-large",
                    "image/png",
                    20 * 1024 * 1024 + 1,
                    new string('b', 64))).Code);
    }

    [TestMethod]
    public void AssetStoreRejectsInterleavedAssetState()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-assets-{Guid.NewGuid():N}");
        using ProjectionAssetStagingStore store = new(root);
        byte[] content = [1, 2, 3];
        string digest = Convert.ToHexStringLower(SHA256.HashData(content));
        store.Begin("artifact-1", "image/png", content.Length, digest);

        Assert.AreEqual(
            "asset_transfer_interleaved",
            Assert.ThrowsExactly<ProjectionTransportException>(() =>
                store.Begin("artifact-2", "image/png", content.Length, digest)).Code);

        store.Append("artifact-1", 0, content);
        store.Commit("artifact-1", content.Length, digest);
        store.Begin("artifact-2", "image/png", content.Length, digest);
    }

    [TestMethod]
    public async Task ProtocolProcessorPublishesOnlyCommittedAssetsToOpenExecutor()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-protocol-{Guid.NewGuid():N}");
        FakeExecutor executor = new();
        await using ProjectionHostProtocolProcessor processor = new(
            executor,
            () => new ProjectionAssetStagingStore(root));
        ProjectionCommand command = Command(ProjectionCommandName.OpenProjectionSession);
        JsonElement? ready = await processor.ProcessAsync(
            Message(new { type = "projection_command", command }),
            CancellationToken.None);
        Assert.AreEqual("asset_ready", ready?.GetProperty("type").GetString());

        byte[] content = [1, 2, 3, 4];
        string digest = Convert.ToHexStringLower(SHA256.HashData(content));
        await processor.ProcessAsync(
            Message(new
            {
                type = "asset_begin",
                assetId = "artifact-1",
                mediaType = "image/png",
                byteSize = content.Length,
                sha256 = digest,
            }),
            CancellationToken.None);
        await processor.ProcessAsync(
            Message(new
            {
                type = "asset_chunk",
                assetId = "artifact-1",
                offset = 0,
                data = Convert.ToBase64String(content).TrimEnd('=').Replace('+', '-').Replace('/', '_'),
            }),
            CancellationToken.None);
        await processor.ProcessAsync(
            Message(new
            {
                type = "asset_commit",
                assetId = "artifact-1",
                byteSize = content.Length,
                sha256 = digest,
            }),
            CancellationToken.None);

        JsonElement? receipt = await processor.ProcessAsync(
            BootstrapMessage(command),
            CancellationToken.None);

        Assert.AreEqual("projection_receipt", receipt?.GetProperty("type").GetString());
        Assert.IsNotNull(executor.OpenContext);
        Assert.IsTrue(executor.OpenContext.SessionAssets.Contains("artifact-1"));
    }

    [TestMethod]
    public async Task ProtocolProcessorRejectsBootstrapWhileAnyAssetIsPartial()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-protocol-{Guid.NewGuid():N}");
        FakeExecutor executor = new();
        await using ProjectionHostProtocolProcessor processor = new(
            executor,
            () => new ProjectionAssetStagingStore(root));
        ProjectionCommand command = Command(ProjectionCommandName.OpenProjectionSession);
        await processor.ProcessAsync(
            Message(new { type = "projection_command", command }),
            CancellationToken.None);
        await processor.ProcessAsync(
            Message(new
            {
                type = "asset_begin",
                assetId = "artifact-1",
                mediaType = "image/png",
                byteSize = 4,
                sha256 = new string('a', 64),
            }),
            CancellationToken.None);

        Assert.AreEqual(
            "bootstrap_identity_invalid",
            (await Assert.ThrowsExactlyAsync<ProjectionTransportException>(() =>
                processor.ProcessAsync(
                    BootstrapMessage(command),
                    CancellationToken.None))).Code);
        Assert.IsNull(executor.OpenContext);
    }

    private static string FindFixture(string name)
    {
        DirectoryInfo? current = new(AppContext.BaseDirectory);
        while (current is not null)
        {
            string candidate = Path.Combine(
                current.FullName,
                "platform",
                "contracts",
                "projection",
                "v1",
                "fixtures",
                name);
            if (File.Exists(candidate))
            {
                return candidate;
            }

            current = current.Parent;
        }

        throw new FileNotFoundException("Projection contract fixture was not found.", name);
    }

    private static ProjectionCommand Command(ProjectionCommandName name) =>
        new(
            1,
            Guid.Parse("44444444-4444-4444-8444-444444444444"),
            name,
            Guid.Parse("55555555-5555-4555-8555-555555555555"),
            0,
            new Dictionary<string, JsonElement>());

    private static JsonElement Message<T>(T value) =>
        JsonSerializer.SerializeToElement(value, ProjectionJson.Options);

    private static JsonElement BootstrapMessage(ProjectionCommand command) =>
        Message(new
        {
            type = "projection_bootstrap",
            command,
            courseVersionId = "course-version-1",
            runtimeManifestDigest = new string('b', 64),
            navigationIdentity = new string('c', 64),
            bootstrap = new { schemaVersion = 1, course = new { id = "course-1" } },
        });

    private sealed class FakeExecutor : IProjectionHostCommandExecutor
    {
        internal ProjectionHostOpenContext? OpenContext { get; private set; }

        public Task<ProjectionReceipt> ExecuteAsync(
            ProjectionCommand command,
            ProjectionHostOpenContext? openContext,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            OpenContext = openContext;
            return Task.FromResult(
                new ProjectionReceipt(
                    1,
                    command.CommandId,
                    command.SessionId,
                    command.Command,
                    true,
                    ProjectionStatus.Assigned,
                    command.ExpectedGeneration,
                    "ok",
                    []));
        }

        public ValueTask DisposeAsync()
        {
            OpenContext?.Dispose();
            OpenContext = null;
            return ValueTask.CompletedTask;
        }
    }
}
