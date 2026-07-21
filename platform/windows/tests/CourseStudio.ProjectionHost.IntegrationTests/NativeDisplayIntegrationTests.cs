using System.Security.Cryptography;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;

namespace CourseStudio.ProjectionHost.IntegrationTests;

[TestClass]
public sealed class NativeDisplayIntegrationTests
{
    [TestMethod]
    [TestCategory("projection_integration")]
    public void AttendedWin11SessionExposesOneEligibleExtendedPair()
    {
        Assert.AreEqual("1", Environment.GetEnvironmentVariable(
            "COURSE_PROJECTION_INTEGRATION_TEST"));
        Assert.IsTrue(Environment.UserInteractive);
        byte[] salt = RandomNumberGenerator.GetBytes(32);
        try
        {
            DisplayTopology topology = new Win32DisplayTopologyProvider().Read(salt);
            Assert.AreEqual("interactive_local", topology.SessionKind);
            Assert.AreEqual("extended", topology.Mode);
            Assert.HasCount(2, topology.Displays);
            Assert.AreEqual(1, topology.Displays.Count(display => display.IsPrimary));
            Assert.AreEqual(2, topology.Displays
                .Select(display => display.DisplayId)
                .Distinct(StringComparer.Ordinal)
                .Count());
        }
        finally
        {
            CryptographicOperations.ZeroMemory(salt);
        }
    }
}
