using System.Text.Json;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;
using CourseStudio.ProjectionHost.Transport;
using CourseStudio.ProjectionHost.Web;
using CourseStudio.ProjectionHost.Windows;
using CourseStudio.ProjectionHost.Witness;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class NativeProjectionCommandExecutorTests
{
    [TestMethod]
    public async Task CertifiesOnlyAfterEqualWebFramesAndAttendedNativeWitness()
    {
        DisplayTopology topology = Topology();
        FakeRoleWindows windows = new(topology);
        FakePresentationFactory presentations = new();
        FakeWitnessSession witness = new(topology.TopologyId);
        await using NativeProjectionCommandExecutor executor = new(
            new FakeTopologyProvider(topology),
            windows,
            presentations,
            witness);
        Guid sessionId = Guid.NewGuid();

        ProjectionReceipt detected = await Execute(executor, Command(
            ProjectionCommandName.DetectDisplays,
            null,
            0));
        ProjectionReceipt opened = await Execute(
            executor,
            Command(ProjectionCommandName.OpenProjectionSession, sessionId, 0),
            OpenContext());
        ProjectionReceipt assigned = await Execute(executor, Command(
            ProjectionCommandName.AssignProjectionWindow,
            sessionId,
            0));
        ProjectionReceipt fullscreen = await Execute(executor, Command(
            ProjectionCommandName.EnterProjectionFullscreen,
            sessionId,
            1));
        ProjectionReceipt certified = await Execute(executor, Command(
            ProjectionCommandName.VerifyProjectionAssignment,
            sessionId,
            1));

        Assert.IsTrue(detected.Accepted);
        Assert.IsTrue(opened.Accepted);
        Assert.AreEqual(ProjectionStatus.Assigned, assigned.Status);
        Assert.AreEqual(ProjectionStatus.Fullscreen, fullscreen.Status);
        Assert.AreEqual(ProjectionStatus.Certified, certified.Status);
        Assert.AreEqual("projection_assignment_certified", certified.Message);
        Assert.AreEqual(1, witness.Runs);

        FrameIdentity next = presentations.Session.LatestFrame with
        {
            Sequence = 1,
            FrameDigest = digest("a"),
        };
        presentations.Session.Commit(Role.Stage, next);
        presentations.Session.Commit(Role.Presenter, next);
        ProjectionReceipt recertified = await Execute(executor, Command(
            ProjectionCommandName.VerifyProjectionAssignment,
            sessionId,
            1));
        Assert.AreEqual(ProjectionStatus.Certified, recertified.Status);
        Assert.AreEqual(
            "projection_assignment_certified_after_frame_advance",
            recertified.Message);
        Assert.AreEqual(1, witness.Runs);

        presentations.Session.Invalidate("frame_commit_invalid");
        ProjectionReceipt rejected = await Execute(executor, Command(
            ProjectionCommandName.VerifyProjectionAssignment,
            sessionId,
            1));
        Assert.IsFalse(rejected.Accepted);
        Assert.AreEqual(ProjectionStatus.Invalidated, rejected.Status);
        Assert.AreEqual("frame_commit_invalid", rejected.Message);
    }

    private static Task<ProjectionReceipt> Execute(
        NativeProjectionCommandExecutor executor,
        ProjectionCommand command,
        ProjectionHostOpenContext? context = null) =>
        executor.ExecuteAsync(command, context, CancellationToken.None);

    private static ProjectionCommand Command(
        ProjectionCommandName name,
        Guid? sessionId,
        int generation) =>
        new(1, Guid.NewGuid(), name, sessionId, generation, new Dictionary<string, JsonElement>());

    private static ProjectionHostOpenContext OpenContext()
    {
        string root = Path.Combine(Path.GetTempPath(), $"projection-executor-{Guid.NewGuid():N}");
        ProjectionAssetStagingStore store = new(root);
        using JsonDocument bootstrap = JsonDocument.Parse("{}");
        return new ProjectionHostOpenContext(
            "course-version-1",
            digest("b"),
            digest("c"),
            bootstrap.RootElement,
            store);
    }

    private static DisplayTopology Topology()
    {
        ProjectionRectangle primaryBounds = new(0, 0, 1536, 864);
        ProjectionRectangle externalBounds = new(3840, 0, 1920, 1080);
        return new DisplayTopology(
            1,
            digest("c"),
            DateTimeOffset.UtcNow,
            "interactive_local",
            "extended",
            [
                new ProjectionDisplay(
                    digest("a"),
                    primaryBounds,
                    primaryBounds,
                    true,
                    true,
                    100,
                    60_000),
                new ProjectionDisplay(
                    digest("b"),
                    externalBounds,
                    externalBounds,
                    false,
                    false,
                    100,
                    60_000),
            ]);
    }

    private static string digest(string character) =>
        string.Concat(Enumerable.Repeat(character, 64));

    private sealed class FakeTopologyProvider(DisplayTopology topology)
        : IDisplayTopologyProvider
    {
        public DisplayTopology Read(ReadOnlySpan<byte> sessionSalt)
        {
            Assert.IsTrue(sessionSalt.Length >= 16);
            return topology;
        }
    }

    private sealed class FakeRoleWindows(DisplayTopology topology) : IRoleWindowController
    {
        private int _generation;

        public Task<RoleAssignment> OpenAsync(
            DisplayTopology actual,
            CancellationToken cancellationToken)
        {
            Assert.AreEqual(topology, actual);
            cancellationToken.ThrowIfCancellationRequested();
            _generation = 1;
            return Task.FromResult(Assignment());
        }

        public Task<RoleAssignment> SwapAsync(
            long expectedGeneration,
            CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<IReadOnlyList<RoleWindowEvidence>> EnterFullscreenAsync(
            long expectedGeneration,
            CancellationToken cancellationToken) =>
            VerifyAsync(expectedGeneration, cancellationToken);

        public Task<IReadOnlyList<RoleWindowEvidence>> VerifyAsync(
            long expectedGeneration,
            CancellationToken cancellationToken)
        {
            Assert.AreEqual(expectedGeneration, _generation);
            cancellationToken.ThrowIfCancellationRequested();
            RoleAssignment assignment = Assignment();
            return Task.FromResult<IReadOnlyList<RoleWindowEvidence>>([
                Evidence(Role.Stage, assignment.StageWindow, topology.Displays[1]),
                Evidence(Role.Presenter, assignment.PresenterWindow, topology.Displays[0]),
            ]);
        }

        public Task CloseAsync(CancellationToken cancellationToken)
        {
            _generation = 0;
            return Task.CompletedTask;
        }

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;

        private RoleAssignment Assignment() => new(
            new WindowIdentity(digest("d"), topology.Displays[1].DisplayId, _generation),
            new WindowIdentity(digest("e"), topology.Displays[0].DisplayId, _generation));

        private static RoleWindowEvidence Evidence(
            Role role,
            WindowIdentity window,
            ProjectionDisplay display)
        {
            PhysicalRect rect = new(
                display.Bounds.X,
                display.Bounds.Y,
                display.Bounds.Width,
                display.Bounds.Height);
            return new RoleWindowEvidence(
                role,
                window.WindowId,
                window.DisplayId,
                window.WindowGeneration,
                rect,
                rect,
                rect,
                rect,
                true,
                false,
                false);
        }
    }

    private sealed class FakePresentationFactory : IProjectionPresentationSessionFactory
    {
        internal FakePresentationSession Session { get; } = new();

        public Task<IProjectionPresentationSession> StartAsync(
            ProjectionHostOpenContext context,
            Guid sessionId,
            long generation,
            CancellationToken cancellationToken)
        {
            _ = context;
            _ = sessionId;
            _ = generation;
            cancellationToken.ThrowIfCancellationRequested();
            return Task.FromResult<IProjectionPresentationSession>(Session);
        }
    }

    private sealed class FakePresentationSession : IProjectionPresentationSession
    {
        public event Action<Role, FrameIdentity>? FrameCommitted;

        public event Action<FrameIdentity>? SyncStarted;

        public event Action<string>? Invalidated;

        public FrameIdentity LatestFrame { get; private set; } = new(
            "course-version-1",
            digest("b"),
            digest("c"),
            0,
            digest("d"));

        public string RuntimeIdentityDigest => digest("f");

        internal void Commit(Role role, FrameIdentity frame)
        {
            if (role == Role.Stage)
            {
                SyncStarted?.Invoke(frame);
            }
            LatestFrame = frame;
            FrameCommitted?.Invoke(role, frame);
        }

        internal void Invalidate(string code) => Invalidated?.Invoke(code);

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

    private sealed class FakeWitnessSession(string topologyId) : IAttendedWitnessSession
    {
        internal int Runs { get; private set; }

        public Task<AttendedWitnessResult> RunAsync(
            IReadOnlyList<RoleWindowEvidence> windows,
            WitnessContext context,
            DateTimeOffset startedAt,
            CancellationToken cancellationToken)
        {
            Assert.AreEqual(2, windows.Count);
            Assert.AreEqual(topologyId, context.TopologyId);
            Assert.IsTrue(context.WindowsValid);
            Runs++;
            WitnessChallenge challenge = new(
                "challenge-1",
                startedAt.AddSeconds(90),
                context.Generation,
                context.TopologyId);
            return Task.FromResult(new AttendedWitnessResult(
                challenge,
                digest("e"),
                startedAt.AddSeconds(1),
                context.Generation,
                context.TopologyId));
        }

        public Task InvalidateAsync(string code, CancellationToken cancellationToken) =>
            Task.CompletedTask;
    }
}
