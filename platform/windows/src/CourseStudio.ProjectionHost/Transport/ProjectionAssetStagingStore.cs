using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.IO;
using CourseStudio.ProjectionHost.Web;

namespace CourseStudio.ProjectionHost.Transport;

public sealed partial class ProjectionAssetStagingStore : IDisposable
{
    private const int MaxAssets = 128;
    private const long MaxAssetBytes = 20L * 1024 * 1024;
    private const long MaxBundleBytes = 96L * 1024 * 1024;
    private const int MaxChunkBytes = 36 * 1024;

    private static readonly HashSet<string> AllowedMediaTypes =
    [
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/svg+xml",
    ];

    private readonly string _root;
    private readonly string _partialRoot;
    private readonly string _committedRoot;
    private readonly Dictionary<string, StagedAsset> _staged = new(StringComparer.Ordinal);
    private readonly List<ProjectionSessionAsset> _committed = [];
    private long _declaredBundleBytes;
    private bool _disposed;

    public ProjectionAssetStagingStore(string root)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        _root = Path.GetFullPath(root);
        if (Directory.Exists(_root)
            || File.Exists(_root)
            || HasReparsePoint(Path.GetDirectoryName(_root)!))
        {
            throw new ProjectionTransportException("asset_staging_root_invalid");
        }

        Directory.CreateDirectory(_root);
        _partialRoot = Directory.CreateDirectory(Path.Combine(_root, "partial")).FullName;
        _committedRoot = Directory.CreateDirectory(Path.Combine(_root, "committed")).FullName;
    }

    public IReadOnlyList<ProjectionSessionAsset> CommittedAssets => _committed.ToArray();

    public string CommittedRoot => _committedRoot;

    public bool HasPendingAssets => _staged.Count != 0;

    public void Begin(
        string opaqueId,
        string mediaType,
        long byteSize,
        string sha256)
    {
        EnsureAvailable();
        if (!OpaqueIdPattern().IsMatch(opaqueId)
            || !AllowedMediaTypes.Contains(mediaType)
            || byteSize < 1
            || !DigestPattern().IsMatch(sha256)
            || _staged.ContainsKey(opaqueId)
            || _committed.Any(asset =>
                string.Equals(asset.OpaqueId, opaqueId, StringComparison.Ordinal)))
        {
            throw new ProjectionTransportException("asset_metadata_invalid");
        }

        if (_staged.Count != 0)
        {
            throw new ProjectionTransportException("asset_transfer_interleaved");
        }

        if (byteSize > MaxAssetBytes)
        {
            throw new ProjectionTransportException("asset_size_exceeded");
        }

        if (_staged.Count + _committed.Count >= MaxAssets)
        {
            throw new ProjectionTransportException("asset_count_exceeded");
        }

        if (_declaredBundleBytes + byteSize > MaxBundleBytes)
        {
            throw new ProjectionTransportException("asset_bundle_size_exceeded");
        }

        string fileIdentity = Convert.ToHexStringLower(
            SHA256.HashData(Encoding.UTF8.GetBytes(opaqueId)));
        string partialPath = Path.Combine(_partialRoot, $"{fileIdentity}.partial");
        FileStream stream = new(
            partialPath,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            bufferSize: 64 * 1024,
            FileOptions.SequentialScan | FileOptions.WriteThrough);
        _staged.Add(
            opaqueId,
            new StagedAsset(
                opaqueId,
                mediaType,
                byteSize,
                sha256,
                partialPath,
                fileIdentity,
                stream,
                IncrementalHash.CreateHash(HashAlgorithmName.SHA256)));
        _declaredBundleBytes += byteSize;
    }

    public void Append(string opaqueId, long offset, ReadOnlySpan<byte> bytes)
    {
        EnsureAvailable();
        if (!_staged.TryGetValue(opaqueId, out StagedAsset? asset))
        {
            throw new ProjectionTransportException("asset_not_started");
        }

        if (offset != asset.Written)
        {
            throw new ProjectionTransportException("asset_offset_invalid");
        }

        if (bytes.Length == 0 || bytes.Length > MaxChunkBytes)
        {
            throw new ProjectionTransportException("asset_chunk_invalid");
        }

        long next = checked(asset.Written + bytes.Length);
        if (next > asset.ByteSize)
        {
            throw new ProjectionTransportException("asset_size_mismatch");
        }

        asset.Stream.Write(bytes);
        asset.Digest.AppendData(bytes);
        asset.Written = next;
    }

    public void Commit(string opaqueId, long byteSize, string sha256)
    {
        EnsureAvailable();
        if (!_staged.Remove(opaqueId, out StagedAsset? asset))
        {
            throw new ProjectionTransportException("asset_not_started");
        }

        try
        {
            asset.Stream.Flush(flushToDisk: true);
            asset.Stream.Dispose();
            byte[] actual = asset.Digest.GetHashAndReset();
            byte[] expected = Convert.FromHexString(asset.Sha256);
            bool matches = CryptographicOperations.FixedTimeEquals(actual, expected);
            CryptographicOperations.ZeroMemory(actual);
            CryptographicOperations.ZeroMemory(expected);
            if (byteSize != asset.ByteSize
                || asset.Written != asset.ByteSize
                || !string.Equals(sha256, asset.Sha256, StringComparison.Ordinal)
                || !matches)
            {
                throw new ProjectionTransportException(
                    matches ? "asset_size_mismatch" : "asset_digest_mismatch");
            }

            string committedPath = Path.Combine(_committedRoot, $"{asset.FileIdentity}.asset");
            File.Move(asset.PartialPath, committedPath);
            _committed.Add(
                new ProjectionSessionAsset(
                    asset.OpaqueId,
                    committedPath,
                    asset.Sha256,
                    asset.MediaType));
        }
        catch
        {
            File.Delete(asset.PartialPath);
            throw;
        }
        finally
        {
            asset.Dispose();
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        foreach (StagedAsset asset in _staged.Values)
        {
            asset.Dispose();
        }

        _staged.Clear();
        if (Directory.Exists(_root) && !HasReparsePoint(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private static bool HasReparsePoint(string path) =>
        File.GetAttributes(path).HasFlag(FileAttributes.ReparsePoint);

    private void EnsureAvailable()
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(nameof(ProjectionAssetStagingStore));
        }
    }

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", RegexOptions.CultureInvariant)]
    private static partial Regex OpaqueIdPattern();

    [GeneratedRegex("^[0-9a-f]{64}$", RegexOptions.CultureInvariant)]
    private static partial Regex DigestPattern();

    private sealed class StagedAsset : IDisposable
    {
        internal StagedAsset(
            string opaqueId,
            string mediaType,
            long byteSize,
            string sha256,
            string partialPath,
            string fileIdentity,
            FileStream stream,
            IncrementalHash digest)
        {
            OpaqueId = opaqueId;
            MediaType = mediaType;
            ByteSize = byteSize;
            Sha256 = sha256;
            PartialPath = partialPath;
            FileIdentity = fileIdentity;
            Stream = stream;
            Digest = digest;
        }

        internal string OpaqueId { get; }

        internal string MediaType { get; }

        internal long ByteSize { get; }

        internal string Sha256 { get; }

        internal string PartialPath { get; }

        internal string FileIdentity { get; }

        internal FileStream Stream { get; }

        internal IncrementalHash Digest { get; }

        internal long Written { get; set; }

        public void Dispose()
        {
            Stream.Dispose();
            Digest.Dispose();
        }
    }
}
