using CourseStudio.ProjectionHost.Core;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class ProjectionReducerTests
{
    private static readonly DateTimeOffset IssuedAt =
        new(2026, 7, 20, 1, 30, 0, TimeSpan.Zero);

    [TestMethod]
    public void ExactAttendedSequenceCertifiesPhysicalSessionOnly()
    {
        ProjectionReducer reducer = new();
        ProjectionState state = ProjectionState.Initial;

        foreach (ProjectionSignal signal in HappySignals())
        {
            ProjectionTransition transition = reducer.Apply(state, signal);
            Assert.IsNotEmpty(transition.Events);
            Assert.IsFalse(transition.State.ReleaseSignatureCertified);
            state = transition.State;
        }

        Assert.AreEqual(ProjectionPhase.Certified, state.Phase);
        Assert.IsTrue(state.PhysicalDualScreenCertified);
        Assert.IsFalse(state.ReleaseSignatureCertified);
        Assert.IsNull(state.InvalidationCode);

        FrameIdentity forward = Frame(sequence: 2, frameSeed: "forward-frame");
        state = reducer.Apply(state, new FrameCommitted(Role.Stage, forward)).State;
        Assert.AreEqual(ProjectionPhase.Syncing, state.Phase);
        Assert.IsFalse(state.PhysicalDualScreenCertified);

        state = reducer.Apply(state, new FrameCommitted(Role.Presenter, forward)).State;
        Assert.AreEqual(ProjectionPhase.Certified, state.Phase);
        Assert.IsTrue(state.PhysicalDualScreenCertified);
        Assert.IsFalse(state.ReleaseSignatureCertified);
    }

    [TestMethod]
    public void SafetyRelevantChangesInvalidateCertifiedSession()
    {
        (Func<ProjectionState, ProjectionSignal> Signal, string Code)[] cases =
        [
            (_ => new TopologyChanged(Digest("topology-2")), "topology_changed"),
            (_ => new DpiChanged(Role.Stage, 144), "dpi_changed"),
            (_ => new RoleCollisionDetected(Digest("collision")), "role_collision"),
            (_ => new WindowMinimized(Role.Stage), "window_minimized"),
            (_ => new WindowCloaked(Role.Presenter), "window_cloaked"),
            (state => new FrameCommitted(
                Role.Stage,
                state.LatestFrame! with { Sequence = state.LatestFrame!.Sequence - 1 }),
                "frame_rollback"),
            (_ => new IdentityMismatchDetected("course_identity"), "identity_mismatch"),
            (_ => new HeartbeatExpired(Role.Presenter), "heartbeat_expired"),
            (_ => new NavigationChanged(Digest("navigation-2")), "navigation_changed"),
            (_ => new RuntimeChanged(Digest("runtime-2")), "runtime_changed"),
            (_ => new HelperRestarted(Digest("helper-2")), "helper_restarted"),
            (_ => new HostRestarted(Digest("host-2")), "host_restarted"),
        ];

        foreach ((Func<ProjectionState, ProjectionSignal> signal, string code) in cases)
        {
            ProjectionState certified = Run(HappySignals());
            ProjectionState invalidated = new ProjectionReducer()
                .Apply(certified, signal(certified))
                .State;

            Assert.AreEqual(ProjectionPhase.Invalidated, invalidated.Phase, code);
            Assert.AreEqual(code, invalidated.InvalidationCode, code);
            Assert.IsFalse(invalidated.PhysicalDualScreenCertified, code);
            Assert.IsFalse(invalidated.ReleaseSignatureCertified, code);
        }
    }

    [TestMethod]
    public void SeededReplayProducesByteIdenticalEvidence()
    {
        byte[] expected = ProjectionEvidence.ToCanonicalUtf8(RunTransition(HappySignals()));

        for (int replay = 0; replay < 100; replay++)
        {
            byte[] actual = ProjectionEvidence.ToCanonicalUtf8(RunTransition(HappySignals()));
            CollectionAssert.AreEqual(expected, actual, $"Replay {replay} drifted.");
        }
    }

    [TestMethod]
    public void SimulatedExpiredAndReplayedWitnessesCannotCertify()
    {
        ProjectionSignal[] beforeAcceptance = HappySignals()[..^1];
        ProjectionState witnessPending = Run(beforeAcceptance);
        Assert.IsNotNull(witnessPending.Witness);
        WitnessIdentity challenge = witnessPending.Witness;

        ProjectionState simulated = new ProjectionReducer()
            .Apply(witnessPending, new SimulatedWitnessObserved(challenge.ChallengeId))
            .State;
        AssertInvalid(simulated, "simulated_witness");

        WitnessIdentity observedLate = challenge with
        {
            ObservedAt = challenge.ExpiresAt.AddTicks(1),
        };
        ProjectionState expired = new ProjectionReducer()
            .Apply(
                witnessPending,
                new NativeWitnessAccepted(observedLate, Digest("late-witness")))
            .State;
        AssertInvalid(expired, "witness_expired");

        ProjectionState certified = Run(HappySignals());
        ProjectionState replayed = new ProjectionReducer()
            .Apply(
                certified,
                new NativeWitnessAccepted(challenge, Digest("replayed-witness")))
            .State;
        AssertInvalid(replayed, "witness_replayed");
    }

    [TestMethod]
    public void InvalidOrderFailsClosedWithStableCode()
    {
        ProjectionState state = new ProjectionReducer()
            .Apply(
                ProjectionState.Initial,
                new FrameCommitted(Role.Stage, Frame()))
            .State;

        AssertInvalid(state, "invalid_transition");
    }

    private static ProjectionSignal[] HappySignals()
    {
        DisplayTopology topology = Topology();
        WindowIdentity stageWindow = new(Digest("stage-window"), Digest("display-stage"), 1);
        WindowIdentity presenterWindow = new(
            Digest("presenter-window"),
            Digest("display-presenter"),
            1);
        WindowGeometry stageGeometry = new(
            stageWindow.DisplayId,
            new ProjectionRectangle(1920, 0, 1920, 1080),
            96,
            true,
            false,
            false);
        WindowGeometry presenterGeometry = new(
            presenterWindow.DisplayId,
            new ProjectionRectangle(0, 0, 1536, 864),
            120,
            true,
            false,
            false);
        FrameIdentity frame = Frame();
        WitnessIdentity challenge = new(
            "challenge-1",
            Digest("challenge-1"),
            IssuedAt,
            IssuedAt.AddMinutes(2),
            null);

        return
        [
            new DisplaysDetected(topology),
            new WindowsAssigned(stageWindow, presenterWindow),
            new FullscreenVerified(stageGeometry, presenterGeometry),
            new FrameCommitted(Role.Stage, frame),
            new FrameCommitted(Role.Presenter, frame),
            new WitnessChallengeIssued(challenge, challenge.ExpiresAt),
            new NativeWitnessAccepted(challenge, Digest("witness-accepted")),
        ];
    }

    private static ProjectionTransition RunTransition(IEnumerable<ProjectionSignal> signals)
    {
        ProjectionReducer reducer = new();
        ProjectionTransition transition = new(ProjectionState.Initial, []);
        foreach (ProjectionSignal signal in signals)
        {
            transition = reducer.Apply(transition.State, signal);
        }

        return transition;
    }

    private static ProjectionState Run(IEnumerable<ProjectionSignal> signals) =>
        RunTransition(signals).State;

    private static DisplayTopology Topology() =>
        new(
            1,
            Digest("topology-1"),
            IssuedAt,
            "interactive_local",
            "extended",
            [
                new ProjectionDisplay(
                    Digest("display-presenter"),
                    new ProjectionRectangle(0, 0, 1536, 864),
                    new ProjectionRectangle(0, 0, 1536, 824),
                    true,
                    true,
                    125,
                    60_000),
                new ProjectionDisplay(
                    Digest("display-stage"),
                    new ProjectionRectangle(1920, 0, 1920, 1080),
                    new ProjectionRectangle(1920, 0, 1920, 1040),
                    false,
                    false,
                    100,
                    60_000),
            ]);

    private static FrameIdentity Frame(long sequence = 1, string frameSeed = "frame-1") =>
        new(
            "course-version-1",
            Digest("runtime-1"),
            Digest("navigation-1"),
            sequence,
            Digest(frameSeed));

    private static string Digest(string value) => ProjectionEvidence.Sha256(value);

    private static void AssertInvalid(ProjectionState state, string code)
    {
        Assert.AreEqual(ProjectionPhase.Invalidated, state.Phase);
        Assert.AreEqual(code, state.InvalidationCode);
        Assert.IsFalse(state.PhysicalDualScreenCertified);
        Assert.IsFalse(state.ReleaseSignatureCertified);
    }
}
