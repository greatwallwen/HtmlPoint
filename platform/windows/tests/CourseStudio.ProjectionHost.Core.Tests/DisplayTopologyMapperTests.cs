using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class DisplayTopologyMapperTests
{
    private static readonly byte[] Salt = Convert.FromHexString(
        "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff");

    [TestMethod]
    public void ExtendedPhysicalPairMapsNegativeCoordinatesRotationAndMixedDpiAnonymously()
    {
        RawDisplaySnapshot presenter = Raw(
            target: 1,
            source: 1,
            rawName: "\\\\.\\DISPLAY_INTERNAL_SECRET",
            path: "DISPLAY#INTERNAL#PNP_SECRET",
            bounds: new PhysicalRect(-1536, 0, 1536, 864),
            dpi: 120,
            primary: true,
            internalHint: true,
            externalHint: false,
            rotation: 0);
        RawDisplaySnapshot stage = Raw(
            target: 2,
            source: 2,
            rawName: "\\\\.\\DISPLAY_SAMSUNG_SECRET",
            path: "DISPLAY#EXTERNAL#PNP_SECRET",
            bounds: new PhysicalRect(0, 0, 1080, 1920),
            dpi: 96,
            primary: false,
            internalHint: false,
            externalHint: true,
            rotation: 90);

        DisplayTopologyMapping result = DisplayTopologyMapper.Map(
            [presenter, stage],
            "interactive_local",
            Salt,
            DateTimeOffset.UnixEpoch);

        Assert.AreEqual("extended", result.Topology.Mode);
        Assert.IsTrue(result.CertificationEligible);
        Assert.HasCount(2, result.Candidates);
        Assert.AreEqual(125, result.Topology.Displays[0].ScalePercent);
        Assert.AreEqual(1080, result.Topology.Displays[1].Bounds.Width);
        Assert.AreEqual(1920, result.Topology.Displays[1].Bounds.Height);
        Assert.AreEqual(-1536, result.Topology.Displays[0].Bounds.X);
        CollectionAssert.AllItemsAreUnique(
            result.Candidates.Select(candidate => candidate.AnonymousDisplayId).ToArray());

        string serialized = ProjectionJson.Serialize(result.Topology);
        Assert.DoesNotContain("DISPLAY_INTERNAL_SECRET", serialized, StringComparison.Ordinal);
        Assert.DoesNotContain("DISPLAY_SAMSUNG_SECRET", serialized, StringComparison.Ordinal);
        Assert.DoesNotContain("PNP_SECRET", serialized, StringComparison.Ordinal);
        Assert.IsTrue(result.Candidates.All(candidate =>
            candidate.AnonymousDisplayId.Length == 64
            && candidate.AnonymousDisplayId.All(character =>
                character is >= '0' and <= '9' or >= 'a' and <= 'f')));

        DisplayTopologyMapping otherSession = DisplayTopologyMapper.Map(
            [presenter, stage],
            "interactive_local",
            Enumerable.Repeat((byte)0x5a, 32).ToArray(),
            DateTimeOffset.UnixEpoch);
        Assert.AreNotEqual(
            result.Candidates[0].AnonymousDisplayId,
            otherSession.Candidates[0].AnonymousDisplayId);
    }

    [TestMethod]
    public void SingleDuplicateRemoteUnknownAndThreeDisplayShapesNeverCertify()
    {
        RawDisplaySnapshot first = Raw(target: 1, source: 1, primary: true);
        RawDisplaySnapshot second = Raw(
            target: 2,
            source: 2,
            bounds: new PhysicalRect(1920, 0, 1920, 1080));
        RawDisplaySnapshot third = Raw(
            target: 3,
            source: 3,
            bounds: new PhysicalRect(3840, 0, 1920, 1080));

        DisplayTopologyMapping single = DisplayTopologyMapper.Map(
            [first],
            "interactive_local",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("single", single.Topology.Mode);
        Assert.IsFalse(single.CertificationEligible);

        DisplayTopologyMapping duplicate = DisplayTopologyMapper.Map(
            [first, second with { SourceId = first.SourceId, Bounds = first.Bounds }],
            "interactive_local",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("duplicate", duplicate.Topology.Mode);
        Assert.IsFalse(duplicate.CertificationEligible);

        DisplayTopologyMapping remote = DisplayTopologyMapper.Map(
            [first, second],
            "remote",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("extended", remote.Topology.Mode);
        Assert.IsFalse(remote.CertificationEligible);

        DisplayTopologyMapping unknown = DisplayTopologyMapper.Map(
            [],
            "unknown",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("unknown", unknown.Topology.Mode);
        Assert.IsFalse(unknown.CertificationEligible);

        DisplayTopologyMapping three = DisplayTopologyMapper.Map(
            [first, second, third],
            "interactive_local",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("extended", three.Topology.Mode);
        Assert.IsFalse(three.CertificationEligible);
    }

    [TestMethod]
    public void MissingMetadataOverflowAndVirtualIndicatorsFailClosed()
    {
        RawDisplaySnapshot first = Raw(target: 1, source: 1, primary: true);
        RawDisplaySnapshot second = Raw(
            target: 2,
            source: 2,
            bounds: new PhysicalRect(1920, 0, 1920, 1080));

        DisplayTopologyMapping missing = DisplayTopologyMapper.Map(
            [first, second with { MonitorDevicePath = string.Empty }],
            "interactive_local",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("unknown", missing.Topology.Mode);
        Assert.AreEqual("missing_display_metadata", missing.FailureCode);
        Assert.IsFalse(missing.CertificationEligible);

        DisplayTopologyMapping overflow = DisplayTopologyMapper.Map(
            [first, second with { Bounds = new PhysicalRect(2_000_000, 0, 1920, 1080) }],
            "interactive_local",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("unknown", overflow.Topology.Mode);
        Assert.AreEqual("coordinate_out_of_range", overflow.FailureCode);
        Assert.IsFalse(overflow.CertificationEligible);

        DisplayTopologyMapping virtualized = DisplayTopologyMapper.Map(
            [first, second with { NoKnownVirtualIndicator = false }],
            "interactive_local",
            Salt,
            DateTimeOffset.UnixEpoch);
        Assert.AreEqual("extended", virtualized.Topology.Mode);
        Assert.IsFalse(virtualized.CertificationEligible);
    }

    private static RawDisplaySnapshot Raw(
        uint target,
        uint source,
        string rawName = "\\\\.\\DISPLAY_RAW",
        string path = "DISPLAY#RAW#PATH",
        PhysicalRect? bounds = null,
        uint dpi = 96,
        bool primary = false,
        bool internalHint = false,
        bool externalHint = true,
        int rotation = 0) =>
        new(
            "adapter-secret",
            target,
            source,
            rawName,
            "friendly-secret",
            path,
            bounds ?? new PhysicalRect(0, 0, 1920, 1080),
            bounds ?? new PhysicalRect(0, 0, 1920, 1040),
            dpi,
            dpi,
            60_000,
            primary,
            internalHint,
            externalHint,
            true,
            true,
            rotation,
            true);
}
