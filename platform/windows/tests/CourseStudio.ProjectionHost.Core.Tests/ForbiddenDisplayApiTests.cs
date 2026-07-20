using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class ForbiddenDisplayApiTests
{
    [TestMethod]
    public void NativeProjectionSourceContainsNoDisplayMutatingApis()
    {
        string nativeRoot = Path.Combine(
            FindRepositoryRoot(),
            "platform",
            "windows",
            "src",
            "CourseStudio.ProjectionHost",
            "Native");
        string[] forbidden =
        [
            "ChangeDisplaySettings",
            "ChangeDisplaySettingsEx",
            "SetDisplayConfig",
            "DisplaySwitch.exe",
            "SetCimInstance",
            "Set-WmiInstance",
        ];

        foreach (string source in Directory.EnumerateFiles(nativeRoot, "*.cs", SearchOption.AllDirectories))
        {
            string text = File.ReadAllText(source);
            foreach (string token in forbidden)
            {
                Assert.DoesNotContain(token, text, StringComparison.OrdinalIgnoreCase, source);
            }
        }
    }

    [TestMethod]
    [TestCategory("projection_detect_smoke")]
    public void CurrentMachineReadIsAnonymousAndNonCertifying()
    {
        byte[] salt = Convert.FromHexString(
            "102132435465768798a9bacbdcedfe0f102132435465768798a9bacbdcedfe0f");
        IDisplayTopologyProvider provider = new Win32DisplayTopologyProvider();

        DisplayTopology topology = provider.Read(salt);

        Assert.AreEqual("interactive_local", topology.SessionKind);
        Assert.AreEqual("extended", topology.Mode);
        Assert.HasCount(2, topology.Displays);
        CollectionAssert.AllItemsAreUnique(
            topology.Displays.Select(display => display.DisplayId).ToArray());
        Assert.IsTrue(topology.Displays.All(display => display.DisplayId.Length == 64));

        ProjectionState candidate = new ProjectionReducer()
            .Apply(ProjectionState.Initial, new DisplaysDetected(topology))
            .State;
        Assert.AreEqual(ProjectionPhase.Candidate, candidate.Phase);
        Assert.IsFalse(candidate.PhysicalDualScreenCertified);
        Assert.IsFalse(candidate.ReleaseSignatureCertified);
    }

    private static string FindRepositoryRoot()
    {
        DirectoryInfo? directory = new(AppContext.BaseDirectory);
        while (directory is not null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "global.json")))
            {
                return directory.FullName;
            }

            directory = directory.Parent;
        }

        throw new DirectoryNotFoundException("Repository root was not found.");
    }
}
