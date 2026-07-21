using System.Text.Json;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Transport;
using CourseStudio.ProjectionHost.Web;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class ProjectionPresentationSessionTests
{
    [TestMethod]
    public async Task StartsEqualRoleFramesAndAppliesOnePresenterControlToBothSurfaces()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-presentation-{Guid.NewGuid():N}");
        ProjectionAssetStagingStore store = new(root);
        using JsonDocument bootstrap = JsonDocument.Parse(BootstrapJson());
        using ProjectionHostOpenContext context = new(
            "course-version-1",
            digest("b"),
            digest("c"),
            bootstrap.RootElement,
            store);
        FakeSurfaceFactory factory = new();

        await using ProjectionPresentationSession session =
            await ProjectionPresentationSession.StartAsync(
                factory,
                context,
                Guid.Parse("11111111-1111-4111-8111-111111111111"),
                1,
                CancellationToken.None);

        Assert.AreEqual(0, session.LatestFrame.Sequence);
        Assert.AreEqual(session.LatestFrame, factory.Stage.LastFrame);
        Assert.AreEqual(session.LatestFrame, factory.Presenter.LastFrame);
        Assert.AreEqual("stage", factory.Stage.LastEnvelope.GetProperty("role").GetString());
        Assert.AreEqual(
            "presenter",
            factory.Presenter.LastEnvelope.GetProperty("role").GetString());
        Assert.AreEqual(
            factory.Stage.LastEnvelope.GetProperty("frame").GetProperty("teachingFrame").GetRawText(),
            factory.Presenter.LastEnvelope.GetProperty("frame").GetProperty("teachingFrame").GetRawText());

        factory.Presenter.Emit(new ProjectionWebMessage(
            ProjectionWebMessageKind.Control,
            null,
            null,
            null,
            new ProjectionTeachingControl(0, "lesson-2", 1, false, 0)));

        await WaitUntilAsync(() => session.LatestFrame.Sequence == 1);
        Assert.AreEqual(1, factory.Stage.PostedFrames);
        Assert.AreEqual(1, factory.Presenter.PostedFrames);
        Assert.AreEqual(session.LatestFrame, factory.Stage.LastFrame);
        Assert.AreEqual(session.LatestFrame, factory.Presenter.LastFrame);
        Assert.AreEqual(
            "lesson-2",
            factory.Stage.LastEnvelope.GetProperty("teachingFrame").GetProperty("lessonId").GetString());
    }

    [TestMethod]
    public async Task RejectsAControlThatDoesNotMatchThePublishedLessonOrdering()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-presentation-{Guid.NewGuid():N}");
        ProjectionAssetStagingStore store = new(root);
        using JsonDocument bootstrap = JsonDocument.Parse(BootstrapJson());
        using ProjectionHostOpenContext context = new(
            "course-version-1",
            digest("b"),
            digest("c"),
            bootstrap.RootElement,
            store);
        FakeSurfaceFactory factory = new();
        string? invalidation = null;
        await using ProjectionPresentationSession session =
            await ProjectionPresentationSession.StartAsync(
                factory,
                context,
                Guid.NewGuid(),
                1,
                CancellationToken.None);
        session.Invalidated += code => invalidation = code;

        factory.Presenter.Emit(new ProjectionWebMessage(
            ProjectionWebMessageKind.Control,
            null,
            null,
            null,
            new ProjectionTeachingControl(0, "lesson-1", 1, false, 0)));

        await WaitUntilAsync(() => invalidation is not null);
        Assert.AreEqual("projection_control_invalid", invalidation);
        Assert.AreEqual(0, factory.Stage.PostedFrames);
        Assert.AreEqual(0, factory.Presenter.PostedFrames);
    }

    [TestMethod]
    public async Task SurfacesNativeInvalidationWithoutWaitingForCommitTimeout()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-presentation-{Guid.NewGuid():N}");
        ProjectionAssetStagingStore store = new(root);
        using JsonDocument bootstrap = JsonDocument.Parse(BootstrapJson());
        using ProjectionHostOpenContext context = new(
            "course-version-1",
            digest("b"),
            digest("c"),
            bootstrap.RootElement,
            store);
        FakeSurfaceFactory factory = new();
        factory.Stage.InvalidationOnBootstrap = "navigation_failed";
        using CancellationTokenSource timeout = new(TimeSpan.FromSeconds(1));

        ProjectionWebPolicyException exception = await Assert.ThrowsExactlyAsync<
            ProjectionWebPolicyException>(async () =>
                await ProjectionPresentationSession.StartAsync(
                    factory,
                    context,
                    Guid.NewGuid(),
                    1,
                    timeout.Token));

        Assert.AreEqual("navigation_failed", exception.Code);
    }

    private static async Task WaitUntilAsync(Func<bool> predicate)
    {
        using CancellationTokenSource timeout = new(TimeSpan.FromSeconds(2));
        while (!predicate())
        {
            await Task.Delay(10, timeout.Token);
        }
    }

    private static string BootstrapJson() => $$"""
    {
      "schemaVersion": 1,
      "courseDigest": "{{digest("a")}}",
      "course": {
        "schemaVersion": 1,
        "id": "course-version-1",
        "title": "AI course",
        "audience": "Personal learner",
        "goal": "Understand AI",
        "durationMinutes": 30,
        "chapters": [{
          "id": "chapter-1",
          "title": "Start",
          "objective": "Learn",
          "lessons": [
            {"id":"lesson-1","title":"First","summary":"Core","durationMinutes":15,"sourceIds":[],"status":"grounded"},
            {"id":"lesson-2","title":"Second","summary":"Practice","durationMinutes":15,"sourceIds":[],"status":"grounded"}
          ]
        }],
        "sources": [],
        "updatedAt": "2026-07-20T00:00:00Z"
      },
      "projection": {
        "courseVersion": {"versionId":"course-version-1"},
        "requirement": {},
        "outline": {},
        "slideDeck": {
          "schemaVersion":1,
          "logicalId":"deck-1",
          "versionId":"deck-version-1",
          "revision":1,
          "contentDigest":"{{digest("d")}}",
          "supersedesVersionId":null,
          "createdAt":"2026-07-20T00:00:00Z",
          "createdBy":{"actorType":"system","actorId":"host","displayName":null},
          "courseVersionId":"course-version-1",
          "nodes":[]
        },
        "runtimeManifest": {}
      }
    }
    """;

    private static string digest(string character) =>
        string.Concat(Enumerable.Repeat(character, 64));

    private sealed class FakeSurfaceFactory : IProjectionWebSurfaceFactory
    {
        internal FakeSurface Stage { get; } = new(Role.Stage);

        internal FakeSurface Presenter { get; } = new(Role.Presenter);

        public Task<IProjectionWebSurface> CreateAsync(
            Role role,
            ProjectionWebBinding binding,
            ProjectionSessionAssets sessionAssets,
            CancellationToken cancellationToken)
        {
            _ = binding;
            _ = sessionAssets;
            cancellationToken.ThrowIfCancellationRequested();
            return Task.FromResult<IProjectionWebSurface>(
                role == Role.Stage ? Stage : Presenter);
        }
    }

    private sealed class FakeSurface(Role role) : IProjectionWebSurface
    {
        private JsonDocument? _lastDocument;

        public Role Role { get; } = role;

        public event Action<ProjectionWebMessage>? MessageReceived;

        public event Action<string>? Invalidated;

        public WebViewRuntimeIdentity RuntimeIdentity { get; } = new(
            "1.0.0.0",
            [new WebViewRuntimeProcessIdentity(
                "C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\msedgewebview2.exe",
                digest("f"),
                "Microsoft Corporation",
                true)]);

        internal int PostedFrames { get; private set; }

        internal string? InvalidationOnBootstrap { get; set; }

        internal FrameIdentity LastFrame { get; private set; } = null!;

        internal JsonElement LastEnvelope => _lastDocument!.RootElement;

        public void StageBootstrap(string json, FrameIdentity frame)
        {
            Capture(json, frame);
            if (InvalidationOnBootstrap is not null)
            {
                Invalidated?.Invoke(InvalidationOnBootstrap);
                return;
            }
            Commit(frame);
        }

        public void PostFrame(string json, FrameIdentity frame)
        {
            PostedFrames++;
            Capture(json, frame);
            Commit(frame);
        }

        public void Emit(ProjectionWebMessage message) => MessageReceived?.Invoke(message);

        public ValueTask DisposeAsync()
        {
            _lastDocument?.Dispose();
            MessageReceived = null;
            return ValueTask.CompletedTask;
        }

        private void Capture(string json, FrameIdentity frame)
        {
            _lastDocument?.Dispose();
            _lastDocument = JsonDocument.Parse(json);
            LastFrame = frame;
        }

        private void Commit(FrameIdentity frame)
        {
            MessageReceived?.Invoke(new ProjectionWebMessage(
                ProjectionWebMessageKind.FrameCommitted,
                frame.Sequence,
                frame.FrameDigest,
                null));
        }
    }
}
