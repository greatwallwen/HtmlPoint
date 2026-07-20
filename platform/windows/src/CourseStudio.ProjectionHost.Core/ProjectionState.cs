namespace CourseStudio.ProjectionHost.Core;

public enum ProjectionPhase
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

public enum Role
{
    Stage,
    Presenter,
}

public sealed record WindowIdentity(
    string WindowId,
    string DisplayId,
    long WindowGeneration);

public sealed record RoleAssignment(
    WindowIdentity StageWindow,
    WindowIdentity PresenterWindow);

public sealed record WindowGeometry(
    string DisplayId,
    ProjectionRectangle Bounds,
    int Dpi,
    bool IsFullscreen,
    bool IsMinimized,
    bool IsCloaked);

public sealed record FrameIdentity(
    string CourseVersionId,
    string RuntimeManifestDigest,
    string NavigationIdentity,
    long Sequence,
    string FrameDigest);

public sealed record RoleCommit(Role Role, FrameIdentity Frame);

public sealed record WitnessIdentity(
    string ChallengeId,
    string ChallengeDigest,
    DateTimeOffset ObservedAt,
    DateTimeOffset ExpiresAt,
    string? WitnessDigest);

public sealed record ProjectionState(
    ProjectionPhase Phase,
    long Generation,
    DisplayTopology? Topology,
    RoleAssignment? Assignment,
    FrameIdentity? LatestFrame,
    RoleCommit? StageCommit,
    RoleCommit? PresenterCommit,
    WitnessIdentity? Witness,
    bool PhysicalDualScreenCertified,
    bool ReleaseSignatureCertified,
    string? InvalidationCode)
{
    public static ProjectionState Initial { get; } = new(
        ProjectionPhase.Undetected,
        0,
        null,
        null,
        null,
        null,
        null,
        null,
        false,
        false,
        null);
}

public abstract record ProjectionSignal;

public sealed record DisplaysDetected(DisplayTopology Topology) : ProjectionSignal;

public sealed record WindowsAssigned(
    WindowIdentity StageWindow,
    WindowIdentity PresenterWindow) : ProjectionSignal;

public sealed record FullscreenVerified(
    WindowGeometry StageGeometry,
    WindowGeometry PresenterGeometry) : ProjectionSignal;

public sealed record FrameCommitted(Role Role, FrameIdentity Frame) : ProjectionSignal;

public sealed record WitnessChallengeIssued(
    WitnessIdentity ChallengeIdentity,
    DateTimeOffset Expiry) : ProjectionSignal;

public sealed record NativeWitnessAccepted(
    WitnessIdentity ChallengeIdentity,
    string WitnessDigest) : ProjectionSignal;

public sealed record SimulatedWitnessObserved(string ChallengeId) : ProjectionSignal;

public sealed record TopologyChanged(string TopologyId) : ProjectionSignal;

public sealed record DpiChanged(Role Role, int Dpi) : ProjectionSignal;

public sealed record RoleCollisionDetected(string EvidenceDigest) : ProjectionSignal;

public sealed record WindowMinimized(Role Role) : ProjectionSignal;

public sealed record WindowCloaked(Role Role) : ProjectionSignal;

public sealed record IdentityMismatchDetected(string IdentityKind) : ProjectionSignal;

public sealed record HeartbeatExpired(Role Role) : ProjectionSignal;

public sealed record NavigationChanged(string NavigationIdentity) : ProjectionSignal;

public sealed record RuntimeChanged(string RuntimeDigest) : ProjectionSignal;

public sealed record HelperRestarted(string ProcessIdentity) : ProjectionSignal;

public sealed record HostRestarted(string ProcessIdentity) : ProjectionSignal;

public sealed record ProjectionTransition(
    ProjectionState State,
    IReadOnlyList<ProjectionEvent> Events);

public interface IProjectionReducer
{
    ProjectionTransition Apply(ProjectionState state, ProjectionSignal signal);
}
