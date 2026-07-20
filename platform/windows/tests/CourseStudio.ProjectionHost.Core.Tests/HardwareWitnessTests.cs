using System.Reflection;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;
using CourseStudio.ProjectionHost.Windows;
using CourseStudio.ProjectionHost.Witness;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class HardwareWitnessTests
{
    private static readonly DateTimeOffset StartedAt =
        new(2026, 7, 20, 2, 0, 0, TimeSpan.Zero);

    [TestMethod]
    public async Task CorrectIndependentCodesIssueOneUseProofAndZeroizeSecrets()
    {
        FakeWitnessSurface surface = new();
        HardwareWitnessCoordinator coordinator = new(surface, new FakeEntropy());
        WitnessContext context = Context();

        WitnessChallenge challenge = await coordinator.BeginAsync(
            Evidence(),
            context,
            StartedAt,
            CancellationToken.None);

        Assert.AreEqual(StartedAt.AddSeconds(90), challenge.ExpiresAt);
        Assert.HasCount(6, surface.StageCode);
        Assert.HasCount(6, surface.PresenterCode);
        Assert.AreNotEqual(surface.StageCode, surface.PresenterCode);
        Assert.AreEqual(0, coordinator.RetainedRawCodeCount);
        Assert.IsGreaterThan(0, coordinator.RetainedSensitiveByteCount);

        HardwareWitnessCoordinator.NativeWitnessProof proof =
            await coordinator.SubmitAsync(
                surface.StageCode,
                surface.PresenterCode,
                context,
                StartedAt.AddSeconds(30),
                CancellationToken.None);

        Assert.AreEqual(challenge.ChallengeId, proof.ChallengeId);
        Assert.AreEqual(context.Generation, proof.Generation);
        Assert.AreEqual(context.TopologyId, proof.TopologyId);
        Assert.AreEqual(64, proof.WitnessDigest.Length);
        Assert.AreEqual(0, coordinator.RetainedSensitiveByteCount);
        Assert.AreEqual(1, surface.HideCalls);

        await Assert.ThrowsExactlyAsync<WitnessConsumedException>(() =>
            coordinator.SubmitAsync(
                surface.StageCode,
                surface.PresenterCode,
                context,
                StartedAt.AddSeconds(31),
                CancellationToken.None));
    }

    [TestMethod]
    public async Task WrongExpiredAndStateChangedAttemptsConsumeChallenge()
    {
        await AssertConsumedAfterFailure(
            (coordinator, surface, context) => coordinator.SubmitAsync(
                "WRONG1",
                surface.PresenterCode,
                context,
                StartedAt.AddSeconds(1),
                CancellationToken.None),
            "witness_code_mismatch");

        await AssertConsumedAfterFailure(
            (coordinator, surface, context) => coordinator.SubmitAsync(
                surface.StageCode,
                surface.PresenterCode,
                context,
                StartedAt.AddSeconds(91),
                CancellationToken.None),
            "witness_expired");

        await AssertConsumedAfterFailure(
            (coordinator, surface, context) => coordinator.SubmitAsync(
                surface.StageCode,
                surface.PresenterCode,
                context with { Generation = context.Generation + 1 },
                StartedAt.AddSeconds(1),
                CancellationToken.None),
            "witness_state_changed");
    }

    [TestMethod]
    public async Task CancellationMoveMinimizeAndTopologyChangeConsumeChallenge()
    {
        foreach (string reason in new[]
                 {
                     "cancelled",
                     "window_moved",
                     "window_minimized",
                     "topology_changed",
                 })
        {
            FakeWitnessSurface surface = new();
            HardwareWitnessCoordinator coordinator = new(surface, new FakeEntropy());
            WitnessContext context = Context();
            await coordinator.BeginAsync(
                Evidence(),
                context,
                StartedAt,
                CancellationToken.None);

            await coordinator.InvalidateAsync(reason, CancellationToken.None);

            Assert.AreEqual(reason, coordinator.ConsumptionCode);
            Assert.AreEqual(0, coordinator.RetainedSensitiveByteCount);
            await Assert.ThrowsExactlyAsync<WitnessConsumedException>(() =>
                coordinator.SubmitAsync(
                    surface.StageCode,
                    surface.PresenterCode,
                    context,
                    StartedAt.AddSeconds(1),
                    CancellationToken.None));
        }
    }

    [TestMethod]
    public async Task InvalidWindowsAndCancelledBeginNeverExposeAChallenge()
    {
        FakeWitnessSurface surface = new();
        HardwareWitnessCoordinator coordinator = new(surface, new FakeEntropy());

        await Assert.ThrowsExactlyAsync<WitnessRejectedException>(() =>
            coordinator.BeginAsync(
                Evidence(),
                Context() with { WindowsValid = false },
                StartedAt,
                CancellationToken.None));
        Assert.AreEqual(0, surface.ShowCalls);

        using CancellationTokenSource cancellation = new();
        cancellation.Cancel();
        await Assert.ThrowsExactlyAsync<OperationCanceledException>(() =>
            coordinator.BeginAsync(
                Evidence(),
                Context(),
                StartedAt,
                cancellation.Token));
        Assert.AreEqual(0, coordinator.RetainedSensitiveByteCount);
    }

    [TestMethod]
    public void FakeCoordinatorCannotConstructNativeWitnessProof()
    {
        Type proofType = typeof(HardwareWitnessCoordinator.NativeWitnessProof);

        Assert.HasCount(0, proofType.GetConstructors(BindingFlags.Public | BindingFlags.Instance));
        Assert.ThrowsExactly<MissingMethodException>(() => Activator.CreateInstance(proofType));
        Assert.ThrowsExactly<UnauthorizedAccessException>(() =>
            new HardwareWitnessCoordinator.NativeWitnessProof(
                new object(),
                "challenge",
                ProjectionEvidence.Sha256("fake-proof"),
                StartedAt,
                1,
                ProjectionEvidence.Sha256("topology")));
    }

    private static async Task AssertConsumedAfterFailure(
        Func<
            HardwareWitnessCoordinator,
            FakeWitnessSurface,
            WitnessContext,
            Task<HardwareWitnessCoordinator.NativeWitnessProof>> submit,
        string expectedCode)
    {
        FakeWitnessSurface surface = new();
        HardwareWitnessCoordinator coordinator = new(surface, new FakeEntropy());
        WitnessContext context = Context();
        await coordinator.BeginAsync(
            Evidence(),
            context,
            StartedAt,
            CancellationToken.None);

        WitnessRejectedException error = await Assert.ThrowsExactlyAsync<WitnessRejectedException>(
            () => submit(coordinator, surface, context));

        Assert.AreEqual(expectedCode, error.Code);
        Assert.AreEqual(expectedCode, coordinator.ConsumptionCode);
        Assert.AreEqual(0, coordinator.RetainedSensitiveByteCount);
        await Assert.ThrowsExactlyAsync<WitnessConsumedException>(() =>
            coordinator.SubmitAsync(
                surface.StageCode,
                surface.PresenterCode,
                context,
                StartedAt.AddSeconds(2),
                CancellationToken.None));
    }

    private static WitnessContext Context() =>
        new(7, ProjectionEvidence.Sha256("topology"), true);

    private static IReadOnlyList<RoleWindowEvidence> Evidence() =>
    [
        ExactEvidence(Role.Stage, new PhysicalRect(1920, 0, 1920, 1080)),
        ExactEvidence(Role.Presenter, new PhysicalRect(0, 0, 1536, 864)),
    ];

    private static RoleWindowEvidence ExactEvidence(Role role, PhysicalRect bounds) =>
        new(
            role,
            ProjectionEvidence.Sha256($"window-{role}"),
            ProjectionEvidence.Sha256($"display-{role}"),
            7,
            bounds,
            bounds,
            bounds,
            bounds,
            true,
            false,
            false);

    private sealed class FakeEntropy : IWitnessEntropySource
    {
        private int _call;

        public void Fill(Span<byte> destination)
        {
            int seed = ++_call * 17;
            for (int index = 0; index < destination.Length; index++)
            {
                destination[index] = checked((byte)((seed + index) % 251));
            }
        }
    }

    private sealed class FakeWitnessSurface : IWitnessSurface
    {
        public event Action<string, string>? CodesSubmitted;

        public event Action? Cancelled;

        public string StageCode { get; private set; } = string.Empty;

        public string PresenterCode { get; private set; } = string.Empty;

        public int ShowCalls { get; private set; }

        public int HideCalls { get; private set; }

        public Task ShowAsync(
            string stageCode,
            string presenterCode,
            IReadOnlyList<RoleWindowEvidence> windows,
            DateTimeOffset expiresAt,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            StageCode = stageCode;
            PresenterCode = presenterCode;
            ShowCalls++;
            return Task.CompletedTask;
        }

        public Task HideAsync(CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            HideCalls++;
            return Task.CompletedTask;
        }

        internal void KeepCompilerAwareOfEvents()
        {
            _ = CodesSubmitted;
            _ = Cancelled;
        }
    }
}
