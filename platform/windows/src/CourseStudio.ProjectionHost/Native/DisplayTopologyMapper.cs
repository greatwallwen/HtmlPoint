using System.Security.Cryptography;
using System.Text;
using CourseStudio.ProjectionHost.Core;

namespace CourseStudio.ProjectionHost.Native;

public sealed record PhysicalRect(long X, long Y, long Width, long Height);

public sealed record DisplayCandidate(
    string AnonymousDisplayId,
    PhysicalRect Bounds,
    PhysicalRect WorkArea,
    uint DpiX,
    uint DpiY,
    bool Primary,
    bool InternalHint,
    bool ExternalHint,
    bool HardwareCandidate,
    bool NoKnownVirtualIndicator);

public sealed record DisplayTopologyMapping(
    DisplayTopology Topology,
    IReadOnlyList<DisplayCandidate> Candidates,
    bool CertificationEligible,
    string? FailureCode);

internal sealed record RawDisplaySnapshot(
    string AdapterIdentity,
    uint TargetId,
    uint SourceId,
    string SourceDeviceName,
    string TargetFriendlyName,
    string MonitorDevicePath,
    PhysicalRect Bounds,
    PhysicalRect WorkArea,
    uint DpiX,
    uint DpiY,
    uint RefreshRateMilliHertz,
    bool Primary,
    bool InternalHint,
    bool ExternalHint,
    bool HardwareCandidate,
    bool NoKnownVirtualIndicator,
    int RotationDegrees,
    bool Active);

public static class DisplayTopologyMapper
{
    internal static DisplayTopologyMapping Map(
        IReadOnlyList<RawDisplaySnapshot> snapshots,
        string sessionKind,
        ReadOnlySpan<byte> sessionSalt,
        DateTimeOffset capturedAt)
    {
        ArgumentNullException.ThrowIfNull(snapshots);
        if (sessionSalt.Length < 16)
        {
            throw new ArgumentException(
                "Projection session salt must contain at least 128 bits.",
                nameof(sessionSalt));
        }

        byte[] salt = sessionSalt.ToArray();
        RawDisplaySnapshot[] active = snapshots
            .Where(snapshot => snapshot.Active)
            .OrderByDescending(snapshot => snapshot.Primary)
            .ThenBy(snapshot => snapshot.Bounds.X)
            .ThenBy(snapshot => snapshot.Bounds.Y)
            .ThenBy(snapshot => snapshot.TargetId)
            .ToArray();

        string? failureCode = FailureCode(active);
        List<(RawDisplaySnapshot Raw, DisplayCandidate Candidate)> pairs = [];
        foreach (RawDisplaySnapshot raw in active)
        {
            if (!IsRectInContract(raw.Bounds) || !IsRectInContract(raw.WorkArea))
            {
                continue;
            }

            DisplayCandidate candidate = new(
                AnonymousId(raw, salt),
                raw.Bounds,
                raw.WorkArea,
                raw.DpiX,
                raw.DpiY,
                raw.Primary,
                raw.InternalHint,
                raw.ExternalHint,
                raw.HardwareCandidate,
                raw.NoKnownVirtualIndicator);
            pairs.Add((raw, candidate));
        }

        string mode = failureCode is null ? ClassifyMode(active) : "unknown";
        ProjectionDisplay[] displays = pairs
            .Select(pair => ToProjectionDisplay(pair.Raw, pair.Candidate))
            .ToArray();
        string topologyId = Hmac(
            salt,
            string.Join(
                '|',
                sessionKind,
                mode,
                string.Join(',', pairs.Select(pair => pair.Candidate.AnonymousDisplayId))));
        DisplayTopology topology = new(
            1,
            topologyId,
            capturedAt,
            NormalizeSessionKind(sessionKind),
            mode,
            displays);

        IReadOnlyList<DisplayCandidate> candidates = pairs
            .Select(pair => pair.Candidate)
            .ToArray();
        bool eligible = IsCertificationEligible(topology, candidates, failureCode);
        return new DisplayTopologyMapping(topology, candidates, eligible, failureCode);
    }

    public static bool IsCertificationEligible(
        DisplayTopology topology,
        IReadOnlyList<DisplayCandidate> candidates,
        string? failureCode = null)
    {
        if (failureCode is not null
            || !string.Equals(
                topology.SessionKind,
                "interactive_local",
                StringComparison.Ordinal)
            || !string.Equals(topology.Mode, "extended", StringComparison.Ordinal)
            || candidates.Count != 2
            || topology.Displays.Count != 2)
        {
            return false;
        }

        return candidates
                .Select(candidate => candidate.AnonymousDisplayId)
                .Distinct(StringComparer.Ordinal)
                .Count() == 2
            && candidates.All(candidate =>
                candidate.HardwareCandidate
                && candidate.NoKnownVirtualIndicator)
            && candidates.Count(candidate => candidate.Primary) == 1;
    }

    internal static DisplayTopology UnknownTopology(
        string sessionKind,
        ReadOnlySpan<byte> sessionSalt,
        string failureCode,
        DateTimeOffset capturedAt)
    {
        byte[] salt = sessionSalt.ToArray();
        return new DisplayTopology(
            1,
            Hmac(salt, $"{sessionKind}|unknown|{failureCode}"),
            capturedAt,
            NormalizeSessionKind(sessionKind),
            "unknown",
            []);
    }

    private static string? FailureCode(IReadOnlyList<RawDisplaySnapshot> active)
    {
        if (active.Any(snapshot =>
                !IsRectInContract(snapshot.Bounds)
                || !IsRectInContract(snapshot.WorkArea)))
        {
            return "coordinate_out_of_range";
        }

        if (active.Any(snapshot =>
                string.IsNullOrWhiteSpace(snapshot.AdapterIdentity)
                || string.IsNullOrWhiteSpace(snapshot.SourceDeviceName)
                || string.IsNullOrWhiteSpace(snapshot.MonitorDevicePath)
                || snapshot.DpiX == 0
                || snapshot.DpiY == 0
                || snapshot.RefreshRateMilliHertz == 0))
        {
            return "missing_display_metadata";
        }

        return null;
    }

    private static string ClassifyMode(IReadOnlyList<RawDisplaySnapshot> active)
    {
        if (active.Count == 0)
        {
            return "unknown";
        }

        if (active.Count == 1)
        {
            return "single";
        }

        bool duplicate = active
            .GroupBy(
                snapshot => (snapshot.AdapterIdentity, snapshot.SourceId),
                EqualityComparer<(string, uint)>.Default)
            .Any(group => group.Count() > 1);
        return duplicate ? "duplicate" : "extended";
    }

    private static ProjectionDisplay ToProjectionDisplay(
        RawDisplaySnapshot raw,
        DisplayCandidate candidate)
    {
        uint averageDpi = checked((candidate.DpiX + candidate.DpiY) / 2);
        int scale = Math.Clamp(
            checked((int)Math.Round(averageDpi * 100d / 96d)),
            50,
            500);
        return new ProjectionDisplay(
            candidate.AnonymousDisplayId,
            ToProjectionRectangle(candidate.Bounds),
            ToProjectionRectangle(candidate.WorkArea),
            candidate.Primary,
            candidate.InternalHint,
            scale,
            checked((int)Math.Clamp(raw.RefreshRateMilliHertz, 1_000u, 1_000_000u)));
    }

    private static ProjectionRectangle ToProjectionRectangle(PhysicalRect rectangle) =>
        new(
            checked((int)rectangle.X),
            checked((int)rectangle.Y),
            checked((int)rectangle.Width),
            checked((int)rectangle.Height));

    private static bool IsRectInContract(PhysicalRect rectangle) =>
        rectangle.X is >= -1_000_000 and <= 1_000_000
        && rectangle.Y is >= -1_000_000 and <= 1_000_000
        && rectangle.Width is >= 1 and <= 100_000
        && rectangle.Height is >= 1 and <= 100_000;

    private static string AnonymousId(RawDisplaySnapshot raw, byte[] salt) =>
        Hmac(
            salt,
            string.Join(
                '|',
                raw.AdapterIdentity,
                raw.TargetId.ToString(System.Globalization.CultureInfo.InvariantCulture),
                raw.SourceId.ToString(System.Globalization.CultureInfo.InvariantCulture),
                raw.MonitorDevicePath,
                raw.RotationDegrees.ToString(System.Globalization.CultureInfo.InvariantCulture)));

    private static string Hmac(byte[] key, string value) =>
        Convert.ToHexStringLower(
            HMACSHA256.HashData(key, Encoding.UTF8.GetBytes(value)));

    private static string NormalizeSessionKind(string sessionKind) => sessionKind switch
    {
        "interactive_local" => "interactive_local",
        "remote" => "remote",
        _ => "unknown",
    };
}
