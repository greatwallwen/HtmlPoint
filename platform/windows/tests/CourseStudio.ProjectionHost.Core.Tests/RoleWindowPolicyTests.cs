using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;
using CourseStudio.ProjectionHost.Windows;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class RoleWindowPolicyTests
{
    [TestMethod]
    public void DefaultAndSwapKeepRolesOnDistinctPhysicalDisplays()
    {
        DisplayTopology topology = Topology();

        RoleDisplayAssignment defaults = RoleWindowAssignmentPolicy.Default(topology);

        Assert.AreEqual(DisplayId("external"), defaults.StageDisplayId);
        Assert.AreEqual(DisplayId("internal"), defaults.PresenterDisplayId);
        Assert.AreNotEqual(defaults.StageDisplayId, defaults.PresenterDisplayId);
        Assert.AreEqual(new PhysicalRect(0, 0, 1920, 1080), defaults.StageBounds);
        Assert.AreEqual(new PhysicalRect(-1536, 0, 1536, 864), defaults.PresenterBounds);

        RoleDisplayAssignment swapped = RoleWindowAssignmentPolicy.Swap(defaults);
        Assert.AreEqual(defaults.PresenterDisplayId, swapped.StageDisplayId);
        Assert.AreEqual(defaults.StageDisplayId, swapped.PresenterDisplayId);
        Assert.AreEqual(defaults.PresenterBounds, swapped.StageBounds);
        Assert.AreEqual(defaults.StageBounds, swapped.PresenterBounds);
    }

    [TestMethod]
    public async Task ControllerUsesUniqueWindowsExactPhysicalRectsAndRestoresStyles()
    {
        FakeWindowPlatform platform = new();
        await using RoleWindowController controller = new(platform);

        RoleAssignment opened = await controller.OpenAsync(Topology(), CancellationToken.None);

        Assert.AreNotEqual(opened.StageWindow.WindowId, opened.PresenterWindow.WindowId);
        Assert.AreNotEqual(opened.StageWindow.DisplayId, opened.PresenterWindow.DisplayId);
        Assert.AreEqual(1, opened.StageWindow.WindowGeneration);
        Assert.AreEqual(2, platform.Created.Count);

        RoleAssignment swapped = await controller.SwapAsync(1, CancellationToken.None);
        Assert.AreEqual(DisplayId("internal"), swapped.StageWindow.DisplayId);
        Assert.AreEqual(DisplayId("external"), swapped.PresenterWindow.DisplayId);
        Assert.AreEqual(2, swapped.StageWindow.WindowGeneration);

        IReadOnlyList<RoleWindowEvidence> fullscreen =
            await controller.EnterFullscreenAsync(2, CancellationToken.None);
        Assert.HasCount(2, fullscreen);
        Assert.IsTrue(fullscreen.All(evidence => evidence.IsExactFullscreen));
        CollectionAssert.AreEquivalent(
            new[]
            {
                new PhysicalRect(-1536, 0, 1536, 864),
                new PhysicalRect(0, 0, 1920, 1080),
            },
            platform.FullscreenTargets.ToArray());

        IReadOnlyList<RoleWindowEvidence> verified =
            await controller.VerifyAsync(2, CancellationToken.None);
        Assert.IsTrue(verified.All(evidence => evidence.IsExactFullscreen));

        await controller.CloseAsync(CancellationToken.None);
        Assert.HasCount(2, platform.RestoredAndClosed);
        Assert.IsTrue(platform.RestoredAndClosed.All(window => window.StyleWasSaved));
    }

    [TestMethod]
    public async Task PartialOpenFailureRollsBackTheFirstWindow()
    {
        FakeWindowPlatform platform = new() { FailCreateRole = Role.Presenter };
        await using RoleWindowController controller = new(platform);

        await Assert.ThrowsExactlyAsync<InvalidOperationException>(() =>
            controller.OpenAsync(Topology(), CancellationToken.None));

        Assert.HasCount(1, platform.RestoredAndClosed);
        Assert.AreEqual(Role.Stage, platform.RestoredAndClosed[0].Role);
        Assert.IsFalse(controller.IsOpen);
    }

    [TestMethod]
    public async Task MoveMinimizeCloakEscapeAndUserCloseInvalidate()
    {
        (string Kind, string Code)[] evidenceCases =
        [
            ("move", "window_moved"),
            ("minimize", "window_minimized"),
            ("cloak", "window_cloaked"),
        ];
        foreach ((string kind, string code) in evidenceCases)
        {
            FakeWindowPlatform platform = new();
            await using RoleWindowController controller = new(platform);
            await controller.OpenAsync(Topology(), CancellationToken.None);
            await controller.EnterFullscreenAsync(1, CancellationToken.None);
            platform.InvalidEvidenceKind = kind;

            ProjectionWindowInvalidatedException error =
                await Assert.ThrowsExactlyAsync<ProjectionWindowInvalidatedException>(() =>
                    controller.VerifyAsync(1, CancellationToken.None));
            Assert.AreEqual(code, error.Code);
        }

        foreach (string reason in new[] { "escape", "user_close" })
        {
            FakeWindowPlatform platform = new();
            await using RoleWindowController controller = new(platform);
            string? invalidation = null;
            controller.Invalidated += (_, code) => invalidation = code;
            await controller.OpenAsync(Topology(), CancellationToken.None);

            platform.Trigger(Role.Stage, reason);

            Assert.AreEqual(reason, invalidation);
        }
    }

    [TestMethod]
    public async Task StaleGenerationIsRejectedBeforeWindowMutation()
    {
        FakeWindowPlatform platform = new();
        await using RoleWindowController controller = new(platform);
        await controller.OpenAsync(Topology(), CancellationToken.None);

        await Assert.ThrowsExactlyAsync<ProjectionWindowGenerationException>(() =>
            controller.SwapAsync(0, CancellationToken.None));

        Assert.AreEqual(0, platform.AssignCalls);
    }

    private static DisplayTopology Topology() =>
        new(
            1,
            DisplayId("topology"),
            DateTimeOffset.UnixEpoch,
            "interactive_local",
            "extended",
            [
                new ProjectionDisplay(
                    DisplayId("internal"),
                    new ProjectionRectangle(-1536, 0, 1536, 864),
                    new ProjectionRectangle(-1536, 0, 1536, 824),
                    true,
                    true,
                    125,
                    60_000),
                new ProjectionDisplay(
                    DisplayId("external"),
                    new ProjectionRectangle(0, 0, 1920, 1080),
                    new ProjectionRectangle(0, 0, 1920, 1040),
                    false,
                    false,
                    100,
                    60_000),
            ]);

    private static string DisplayId(string seed) => ProjectionEvidence.Sha256(seed);

    private sealed class FakeWindowPlatform : IRoleWindowPlatform
    {
        private readonly List<PlatformRoleWindow> _windows = [];

        public event EventHandler<PlatformWindowInvalidatedEventArgs>? Invalidated;

        public Role? FailCreateRole { get; init; }

        public string? InvalidEvidenceKind { get; set; }

        public List<PlatformRoleWindow> Created { get; } = [];

        public List<PlatformRoleWindow> RestoredAndClosed { get; } = [];

        public List<PhysicalRect> FullscreenTargets { get; } = [];

        public int AssignCalls { get; private set; }

        public Task<PlatformRoleWindow> CreateAsync(
            Role role,
            string displayId,
            PhysicalRect targetBounds,
            long generation,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (role == FailCreateRole)
            {
                throw new InvalidOperationException("synthetic partial-open failure");
            }

            PlatformRoleWindow window = new(
                role,
                ProjectionEvidence.Sha256($"window-{role}"),
                displayId,
                targetBounds,
                generation,
                true,
                new object());
            _windows.Add(window);
            Created.Add(window);
            return Task.FromResult(window);
        }

        public Task<PlatformRoleWindow> AssignAsync(
            PlatformRoleWindow window,
            string displayId,
            PhysicalRect targetBounds,
            long generation,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            AssignCalls++;
            PlatformRoleWindow assigned = window with
            {
                DisplayId = displayId,
                TargetBounds = targetBounds,
                Generation = generation,
            };
            _windows[_windows.IndexOf(window)] = assigned;
            return Task.FromResult(assigned);
        }

        public Task EnterFullscreenAsync(
            PlatformRoleWindow window,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            FullscreenTargets.Add(window.TargetBounds);
            return Task.CompletedTask;
        }

        public Task<RoleWindowEvidence> ReadEvidenceAsync(
            PlatformRoleWindow window,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            PhysicalRect actual = InvalidEvidenceKind == "move"
                ? window.TargetBounds with { X = window.TargetBounds.X + 1 }
                : window.TargetBounds;
            bool minimized = InvalidEvidenceKind == "minimize";
            bool cloaked = InvalidEvidenceKind == "cloak";
            return Task.FromResult(
                new RoleWindowEvidence(
                    window.Role,
                    window.WindowId,
                    window.DisplayId,
                    window.Generation,
                    window.TargetBounds,
                    actual,
                    actual,
                    window.TargetBounds,
                    true,
                    minimized,
                    cloaked));
        }

        public Task RestoreAndCloseAsync(
            PlatformRoleWindow window,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            RestoredAndClosed.Add(window);
            _windows.Remove(window);
            return Task.CompletedTask;
        }

        public void Trigger(Role role, string reason)
        {
            PlatformRoleWindow window = _windows.Single(candidate => candidate.Role == role);
            Invalidated?.Invoke(this, new PlatformWindowInvalidatedEventArgs(window, reason));
        }
    }
}
