using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace CourseStudio.ProjectionHost.Web;

public sealed record WebViewRuntimeProcessIdentity(
    string CanonicalPath,
    string Sha256,
    string Publisher,
    bool SignatureValid);

public sealed record WebViewRuntimeIdentity(
    string BrowserVersionString,
    IReadOnlyList<WebViewRuntimeProcessIdentity> Processes)
{
    public static WebViewRuntimeIdentity Capture(
        string browserVersionString,
        IEnumerable<int> processIds)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(browserVersionString);
        Dictionary<string, WebViewRuntimeProcessIdentity> identities =
            new(StringComparer.OrdinalIgnoreCase);
        foreach (int processId in processIds.Distinct())
        {
            using Process process = Process.GetProcessById(processId);
            string path = process.MainModule?.FileName
                ?? throw new InvalidOperationException("WebView Runtime process path is unavailable.");
            string canonicalPath = Path.GetFullPath(path);
            using FileStream executable = new(
                canonicalPath,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read | FileShare.Delete,
                bufferSize: 64 * 1024,
                FileOptions.SequentialScan);
            string digest = Convert.ToHexStringLower(SHA256.HashData(executable));
            (string publisher, bool signatureValid) = AuthenticodeIdentity.Read(canonicalPath);
            WebViewRuntimeProcessIdentity identity =
                new WebViewRuntimeProcessIdentity(
                    canonicalPath,
                    digest,
                    publisher,
                    signatureValid);
            if (identities.TryGetValue(canonicalPath, out WebViewRuntimeProcessIdentity? existing)
                && existing != identity)
            {
                throw new RuntimeIdentityChangedException();
            }

            identities[canonicalPath] = identity;
        }

        return new WebViewRuntimeIdentity(
            browserVersionString,
            identities.Values
                .OrderBy(identity => identity.CanonicalPath, StringComparer.OrdinalIgnoreCase)
                .ToArray());
    }
}

public sealed class WebViewRuntimePolicy
{
    private readonly WebViewRuntimeIdentity _bound;

    public WebViewRuntimePolicy(WebViewRuntimeIdentity initial)
    {
        EnsureTrusted(initial);
        _bound = Normalize(initial);
    }

    public bool Verify(WebViewRuntimeIdentity candidate)
    {
        try
        {
            EnsureTrusted(candidate);
            WebViewRuntimeIdentity normalized = Normalize(candidate);
            if (!string.Equals(
                    _bound.BrowserVersionString,
                    normalized.BrowserVersionString,
                    StringComparison.Ordinal)
                || _bound.Processes.Count != normalized.Processes.Count)
            {
                throw new RuntimeIdentityChangedException();
            }

            for (int index = 0; index < _bound.Processes.Count; index++)
            {
                WebViewRuntimeProcessIdentity expected = _bound.Processes[index];
                WebViewRuntimeProcessIdentity actual = normalized.Processes[index];
                if (!string.Equals(
                        expected.CanonicalPath,
                        actual.CanonicalPath,
                        StringComparison.OrdinalIgnoreCase)
                    || !string.Equals(expected.Sha256, actual.Sha256, StringComparison.Ordinal)
                    || !string.Equals(expected.Publisher, actual.Publisher, StringComparison.Ordinal)
                    || expected.SignatureValid != actual.SignatureValid)
                {
                    throw new RuntimeIdentityChangedException();
                }
            }

            return true;
        }
        catch (RuntimeIdentityChangedException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is ArgumentException or InvalidOperationException)
        {
            throw new RuntimeIdentityChangedException();
        }
    }

    private static void EnsureTrusted(WebViewRuntimeIdentity identity)
    {
        if (string.IsNullOrWhiteSpace(identity.BrowserVersionString)
            || identity.Processes.Count == 0
            || identity.Processes.Any(process =>
                !process.SignatureValid
                || process.Sha256.Length != 64
                || !process.Publisher.Contains(
                    "Microsoft",
                    StringComparison.OrdinalIgnoreCase)))
        {
            throw new RuntimeIdentityChangedException();
        }
    }

    private static WebViewRuntimeIdentity Normalize(WebViewRuntimeIdentity identity) =>
        identity with
        {
            Processes = identity.Processes
                .Select(process => process with
                {
                    CanonicalPath = Path.GetFullPath(process.CanonicalPath),
                })
                .OrderBy(process => process.CanonicalPath, StringComparer.OrdinalIgnoreCase)
                .ToArray(),
        };
}

public sealed class RuntimeIdentityChangedException()
    : InvalidOperationException("runtime_identity_changed")
{
    public string Code { get; } = "runtime_identity_changed";
}

internal static class AuthenticodeIdentity
{
    private static readonly Guid GenericVerifyAction =
        new("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");

    internal static (string Publisher, bool SignatureValid) Read(string path)
    {
        bool signatureValid = Verify(path);
        try
        {
#pragma warning disable SYSLIB0057 // No X509CertificateLoader API extracts the signer from an Authenticode PE file.
            using X509Certificate certificate = X509Certificate.CreateFromSignedFile(path);
#pragma warning restore SYSLIB0057
            using X509Certificate2 certificate2 = X509CertificateLoader.LoadCertificate(
                certificate.Export(X509ContentType.Cert));
            string publisher = certificate2.GetNameInfo(
                X509NameType.SimpleName,
                forIssuer: false);
            return (publisher, signatureValid);
        }
        catch (CryptographicException)
        {
            return (string.Empty, false);
        }
    }

    private static bool Verify(string path)
    {
        nint pathPointer = Marshal.StringToCoTaskMemUni(path);
        nint fileInfoPointer = 0;
        try
        {
            WinTrustFileInfo fileInfo = new()
            {
                Size = checked((uint)Marshal.SizeOf<WinTrustFileInfo>()),
                FilePath = pathPointer,
            };
            fileInfoPointer = Marshal.AllocCoTaskMem(Marshal.SizeOf<WinTrustFileInfo>());
            Marshal.StructureToPtr(fileInfo, fileInfoPointer, fDeleteOld: false);
            WinTrustData trustData = new()
            {
                Size = checked((uint)Marshal.SizeOf<WinTrustData>()),
                UiChoice = 2,
                RevocationChecks = 0,
                UnionChoice = 1,
                FileInfo = fileInfoPointer,
                StateAction = 1,
                ProviderFlags = 0x00001000,
            };
            Guid action = GenericVerifyAction;
            int result = WinVerifyTrust(0, ref action, ref trustData);
            trustData.StateAction = 2;
            _ = WinVerifyTrust(0, ref action, ref trustData);
            return result == 0;
        }
        finally
        {
            if (fileInfoPointer != 0)
            {
                Marshal.FreeCoTaskMem(fileInfoPointer);
            }

            Marshal.FreeCoTaskMem(pathPointer);
        }
    }

    [DllImport("wintrust.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int WinVerifyTrust(
        nint window,
        ref Guid actionId,
        ref WinTrustData trustData);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustFileInfo
    {
        internal uint Size;
        internal nint FilePath;
        internal nint FileHandle;
        internal nint KnownSubject;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustData
    {
        internal uint Size;
        internal nint PolicyCallbackData;
        internal nint SipClientData;
        internal uint UiChoice;
        internal uint RevocationChecks;
        internal uint UnionChoice;
        internal nint FileInfo;
        internal uint StateAction;
        internal nint StateData;
        internal nint UrlReference;
        internal uint ProviderFlags;
        internal uint UiContext;
        internal nint SignatureSettings;
    }
}
