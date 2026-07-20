using System.Security.Cryptography;
using System.Text.Json;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Web;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class ProjectionWebPolicyTests
{
    [TestMethod]
    public void NavigationAndResourcesAreDenyByDefault()
    {
        using ProjectionSessionAssets assets = ProjectionSessionAssets.Empty;
        ProjectionWebPolicy policy = new(
            assets,
            ["/index.html", "/assets/app-a1b2c3.js", "/assets/app-d4e5f6.css"]);

        Assert.IsTrue(policy.IsNavigationAllowed(
            new Uri("https://projection.course-studio.test/index.html")));
        Assert.IsFalse(policy.IsNavigationAllowed(
            new Uri("https://example.com/index.html")));
        Assert.IsFalse(policy.IsNavigationAllowed(
            new Uri("http://projection.course-studio.test/index.html")));
        Assert.IsFalse(policy.IsNavigationAllowed(
            new Uri("https://projection.course-studio.test/unknown.html")));
        Assert.IsFalse(policy.IsNavigationAllowed(
            new Uri("https://projection.course-studio.test/%2e%2e/secrets.txt")));

        Assert.AreEqual(
            ProjectionResourceDecision.AllowStatic,
            policy.DecideResource(
                new Uri("https://projection.course-studio.test/assets/app-a1b2c3.js"),
                ProjectionRequestSource.Document));
        Assert.AreEqual(
            ProjectionResourceDecision.Deny,
            policy.DecideResource(
                new Uri("https://projection.course-studio.test/assets/unknown.js"),
                ProjectionRequestSource.Document));
        Assert.AreEqual(
            ProjectionResourceDecision.Deny,
            policy.DecideResource(
                new Uri("https://projection.course-studio.test/assets/app-a1b2c3.js"),
                ProjectionRequestSource.ServiceWorker));
        Assert.AreEqual(
            ProjectionResourceDecision.Deny,
            policy.DecideResource(
                new Uri("https://cdn.example.com/app.js"),
                ProjectionRequestSource.Document));
    }

    [TestMethod]
    public void BundleInventoryExcludesHiddenMetadataAndRejectsReparsePoints()
    {
        string root = Path.Combine(Path.GetTempPath(), $"course-studio-web-{Guid.NewGuid():N}");
        Directory.CreateDirectory(Path.Combine(root, "assets"));
        Directory.CreateDirectory(Path.Combine(root, ".vite"));
        try
        {
            File.WriteAllText(Path.Combine(root, "index.html"), "<main></main>");
            File.WriteAllText(Path.Combine(root, "assets", "app-a1.js"), "export {};");
            File.WriteAllText(Path.Combine(root, ".vite", "manifest.json"), "{}");

            IReadOnlySet<string> paths = ProjectionWebBundle.Inventory(root);

            CollectionAssert.AreEquivalent(
                new[] { "/index.html", "/assets/app-a1.js" },
                paths.ToArray());
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void HostCapabilitiesRemainDisabled()
    {
        ProjectionWebSecuritySettings settings = ProjectionWebSecuritySettings.LockedDown;

        Assert.IsFalse(settings.NewWindowsAllowed);
        Assert.IsFalse(settings.DownloadsAllowed);
        Assert.IsFalse(settings.PermissionsAllowed);
        Assert.IsFalse(settings.DevToolsAllowed);
        Assert.IsFalse(settings.ServiceWorkersAllowed);
        Assert.IsFalse(settings.HostObjectsAllowed);
        Assert.IsFalse(settings.DefaultContextMenusAllowed);
        Assert.IsFalse(settings.ExternalFetchAllowed);
    }

    [TestMethod]
    public void SessionAssetsRequireKnownOpaqueIdCanonicalRootAndMatchingDigest()
    {
        string root = Path.Combine(
            Path.GetTempPath(),
            $"course-studio-session-assets-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            string assetPath = Path.Combine(root, "asset.png");
            byte[] content = [1, 2, 3, 4, 5];
            File.WriteAllBytes(assetPath, content);
            string digest = Convert.ToHexStringLower(SHA256.HashData(content));
            using ProjectionSessionAssets assets = new(
                root,
                [new ProjectionSessionAsset("asset-1", assetPath, digest, "image/png")]);

            Assert.IsTrue(assets.TryOpen("asset-1", out Stream? stream, out string? mediaType));
            using (stream)
            {
                Assert.AreEqual("image/png", mediaType);
                CollectionAssert.AreEqual(content, ReadAll(stream!));
            }
            Assert.IsFalse(assets.TryOpen("unknown", out _, out _));

            File.WriteAllBytes(assetPath, [9, 9, 9]);
            Assert.IsFalse(assets.TryOpen("asset-1", out _, out _));

            string outside = Path.GetFullPath(Path.Combine(root, "..", "outside.png"));
            Assert.ThrowsExactly<ProjectionAssetPolicyException>(() =>
                new ProjectionSessionAssets(
                    root,
                    [new ProjectionSessionAsset("asset-2", outside, digest, "image/png")]));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void RuntimeVersionPathPublisherSignatureAndDigestAreBound()
    {
        WebViewRuntimeIdentity initial = Identity("123.0.0.0", "a", true, true);
        WebViewRuntimePolicy policy = new(initial);

        Assert.IsTrue(policy.Verify(Identity("123.0.0.0", "a", true, true)));

        (WebViewRuntimeIdentity Identity, string Field)[] drift =
        [
            (Identity("124.0.0.0", "a", true, true), "version"),
            (Identity("123.0.0.0", "b", true, true), "digest"),
            (Identity("123.0.0.0", "a", false, true), "publisher"),
            (Identity("123.0.0.0", "a", true, false), "signature"),
            (new WebViewRuntimeIdentity(
                "123.0.0.0",
                [new WebViewRuntimeProcessIdentity(
                    "D:\\other\\msedgewebview2.exe",
                    digest("a"),
                    "Microsoft Corporation",
                    true)]), "path"),
        ];
        foreach ((WebViewRuntimeIdentity identity, string field) in drift)
        {
            RuntimeIdentityChangedException error =
                Assert.ThrowsExactly<RuntimeIdentityChangedException>(() =>
                    policy.Verify(identity));
            Assert.AreEqual("runtime_identity_changed", error.Code, field);
        }
    }

    [TestMethod]
    public void WebReceiptsRequireReadyExactIdentityKnownFrameAndCommitOrder()
    {
        ProjectionWebBinding binding = new(
            Role.Stage,
            Guid.Parse("11111111-1111-4111-8111-111111111111"),
            "session-1",
            "course-version-1",
            digest("b"),
            digest("c"),
            3);
        ProjectionWebMessageGate gate = new(binding);
        FrameIdentity frame = new(
            binding.CourseVersionId,
            binding.RuntimeManifestDigest,
            binding.NavigationIdentity,
            1,
            digest("d"));
        gate.RegisterFrame(frame);

        ProjectionWebMessage ready = gate.Accept(ReadyJson());
        Assert.AreEqual(ProjectionWebMessageKind.Ready, ready.Kind);
        Assert.ThrowsExactly<ProjectionWebMessageException>(() => gate.Accept(ReadyJson()));

        ProjectionWebMessage accepted = gate.Accept(ReceiptJson("message_accepted"));
        Assert.AreEqual(ProjectionWebMessageKind.MessageAccepted, accepted.Kind);
        ProjectionWebMessage committed = gate.Accept(ReceiptJson("frame_committed"));
        Assert.AreEqual(ProjectionWebMessageKind.FrameCommitted, committed.Kind);
        Assert.AreEqual(1, committed.Sequence);

        string extra = ReceiptJson("message_accepted").Replace(
            "\"frameDigest\"",
            "\"executablePath\":\"bad.exe\",\"frameDigest\"",
            StringComparison.Ordinal);
        Assert.ThrowsExactly<ProjectionWebMessageException>(() =>
            new ProjectionWebMessageGate(binding).Accept(extra));
    }

    [TestMethod]
    public void FrameCommitBeforeAcceptedOrWithChangedIdentityIsRejected()
    {
        ProjectionWebBinding binding = new(
            Role.Presenter,
            Guid.Parse("22222222-2222-4222-8222-222222222222"),
            "session-2",
            "course-version-2",
            digest("e"),
            digest("f"),
            7);
        ProjectionWebMessageGate gate = new(binding);
        gate.RegisterFrame(new FrameIdentity(
            binding.CourseVersionId,
            binding.RuntimeManifestDigest,
            binding.NavigationIdentity,
            9,
            digest("a")));
        gate.Accept(ReadyJson("presenter", binding.ChannelId));

        Assert.AreEqual(
            "frame_commit_order_invalid",
            Assert.ThrowsExactly<ProjectionWebMessageException>(() =>
                gate.Accept(ReceiptJson(
                    "frame_committed",
                    "presenter",
                    binding.ChannelId,
                    binding,
                    9,
                    digest("a")))).Code);

        string changed = ReceiptJson(
            "message_accepted",
            "presenter",
            binding.ChannelId,
            binding,
            9,
            digest("a")).Replace(digest("f"), digest("0"), StringComparison.Ordinal);
        Assert.AreEqual(
            "web_receipt_identity_mismatch",
            Assert.ThrowsExactly<ProjectionWebMessageException>(() => gate.Accept(changed)).Code);
    }

    [TestMethod]
    public void OutboundBootstrapIsBoundBeforeItCanReachTheRenderer()
    {
        ProjectionWebBinding binding = new(
            Role.Stage,
            Guid.Parse("33333333-3333-4333-8333-333333333333"),
            "session-3",
            "course-version-3",
            digest("1"),
            digest("2"),
            11);
        FrameIdentity frame = new(
            binding.CourseVersionId,
            binding.RuntimeManifestDigest,
            binding.NavigationIdentity,
            4,
            digest("3"));
        string json = JsonSerializer.Serialize(new
        {
            schemaVersion = 1,
            type = "projection_bootstrap",
            role = "stage",
            channelId = binding.ChannelId,
            sessionId = binding.SessionId,
            courseVersionId = binding.CourseVersionId,
            runtimeManifestDigest = binding.RuntimeManifestDigest,
            navigationIdentity = binding.NavigationIdentity,
            generation = binding.Generation,
            frame = new
            {
                sequence = frame.Sequence,
                frameDigest = frame.FrameDigest,
            },
        });

        ProjectionOutboundEnvelope.Validate(
            json,
            "projection_bootstrap",
            binding,
            frame);

        string changed = json.Replace(
            "\"role\":\"stage\"",
            "\"role\":\"presenter\"",
            StringComparison.Ordinal);
        Assert.AreEqual(
            "outbound_frame_identity_mismatch",
            Assert.ThrowsExactly<ProjectionWebPolicyException>(() =>
                ProjectionOutboundEnvelope.Validate(
                    changed,
                    "projection_bootstrap",
                    binding,
                    frame)).Code);
    }

    private static WebViewRuntimeIdentity Identity(
        string version,
        string digestSeed,
        bool microsoftPublisher,
        bool signatureValid) =>
        new(
            version,
            [new WebViewRuntimeProcessIdentity(
                "C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application\\123.0.0.0\\msedgewebview2.exe",
                digest(digestSeed),
                microsoftPublisher ? "Microsoft Corporation" : "Unknown Publisher",
                signatureValid)]);

    private static string digest(string character) => character[0].ToString().Repeat(64);

    private static string ReadyJson(
        string role = "stage",
        Guid? channelId = null) =>
        $$"""
        {"schemaVersion":1,"type":"projection_ready","role":"{{role}}","channelId":"{{channelId ?? Guid.Parse("11111111-1111-4111-8111-111111111111")}}"}
        """;

    private static string ReceiptJson(string type) => ReceiptJson(
        type,
        "stage",
        Guid.Parse("11111111-1111-4111-8111-111111111111"),
        new ProjectionWebBinding(
            Role.Stage,
            Guid.Parse("11111111-1111-4111-8111-111111111111"),
            "session-1",
            "course-version-1",
            digest("b"),
            digest("c"),
            3),
        1,
        digest("d"));

    private static string ReceiptJson(
        string type,
        string role,
        Guid channelId,
        ProjectionWebBinding binding,
        long sequence,
        string frameDigest) =>
        $$"""
        {"schemaVersion":1,"type":"{{type}}","role":"{{role}}","channelId":"{{channelId}}","sessionId":"{{binding.SessionId}}","courseVersionId":"{{binding.CourseVersionId}}","runtimeManifestDigest":"{{binding.RuntimeManifestDigest}}","navigationIdentity":"{{binding.NavigationIdentity}}","generation":{{binding.Generation}},"sequence":{{sequence}},"frameDigest":"{{frameDigest}}"}
        """;

    private static byte[] ReadAll(Stream stream)
    {
        using MemoryStream buffer = new();
        stream.CopyTo(buffer);
        return buffer.ToArray();
    }
}

internal static class ProjectionWebPolicyTestExtensions
{
    internal static string Repeat(this string value, int count) =>
        string.Concat(Enumerable.Repeat(value, count));
}
