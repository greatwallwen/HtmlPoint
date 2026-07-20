using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.IO;

namespace CourseStudio.ProjectionHost.Web;

public sealed record ProjectionSessionAsset(
    string OpaqueId,
    string Path,
    string Sha256,
    string MediaType);

public sealed partial class ProjectionSessionAssets : IDisposable
{
    private static readonly HashSet<string> AllowedMediaTypes =
    [
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/svg+xml",
    ];

    private readonly IReadOnlyDictionary<string, ProjectionSessionAsset> _assets;
    private bool _disposed;

    public ProjectionSessionAssets(
        string root,
        IReadOnlyList<ProjectionSessionAsset> assets)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        ArgumentNullException.ThrowIfNull(assets);
        string canonicalRoot = Path.GetFullPath(root);
        Dictionary<string, ProjectionSessionAsset> mapped =
            new(StringComparer.Ordinal);
        foreach (ProjectionSessionAsset asset in assets)
        {
            if (!OpaqueIdPattern().IsMatch(asset.OpaqueId)
                || !Sha256Pattern().IsMatch(asset.Sha256)
                || !AllowedMediaTypes.Contains(asset.MediaType))
            {
                throw new ProjectionAssetPolicyException("invalid_session_asset");
            }

            string canonicalPath = Path.GetFullPath(asset.Path);
            string relative = Path.GetRelativePath(canonicalRoot, canonicalPath);
            if (Path.IsPathRooted(relative)
                || relative.Equals("..", StringComparison.Ordinal)
                || relative.StartsWith($"..{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
                || relative.StartsWith($"..{Path.AltDirectorySeparatorChar}", StringComparison.Ordinal))
            {
                throw new ProjectionAssetPolicyException("asset_outside_session_root");
            }

            if (!mapped.TryAdd(asset.OpaqueId, asset with { Path = canonicalPath }))
            {
                throw new ProjectionAssetPolicyException("duplicate_session_asset");
            }
        }

        _assets = mapped;
    }

    private ProjectionSessionAssets()
    {
        _assets = new Dictionary<string, ProjectionSessionAsset>(StringComparer.Ordinal);
    }

    public static ProjectionSessionAssets Empty => new();

    public bool Contains(string opaqueId) =>
        !_disposed && _assets.ContainsKey(opaqueId);

    public bool TryOpen(
        string opaqueId,
        out Stream? stream,
        out string? mediaType)
    {
        stream = null;
        mediaType = null;
        if (_disposed || !_assets.TryGetValue(opaqueId, out ProjectionSessionAsset? asset))
        {
            return false;
        }

        FileStream? candidate = null;
        try
        {
            candidate = new FileStream(
                asset.Path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read,
                bufferSize: 64 * 1024,
                FileOptions.SequentialScan);
            byte[] actual = SHA256.HashData(candidate);
            byte[] expected = Convert.FromHexString(asset.Sha256);
            bool matches = CryptographicOperations.FixedTimeEquals(actual, expected);
            CryptographicOperations.ZeroMemory(actual);
            CryptographicOperations.ZeroMemory(expected);
            if (!matches)
            {
                candidate.Dispose();
                return false;
            }

            candidate.Position = 0;
            stream = candidate;
            mediaType = asset.MediaType;
            return true;
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or CryptographicException)
        {
            candidate?.Dispose();
            return false;
        }
    }

    public void Dispose()
    {
        _disposed = true;
    }

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", RegexOptions.CultureInvariant)]
    private static partial Regex OpaqueIdPattern();

    [GeneratedRegex("^[0-9a-f]{64}$", RegexOptions.CultureInvariant)]
    private static partial Regex Sha256Pattern();
}

public sealed class ProjectionAssetPolicyException(string code)
    : InvalidOperationException(code)
{
    public string Code { get; } = code;
}
