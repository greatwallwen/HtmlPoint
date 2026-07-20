using System.Security.Cryptography;
using System.Text;
using CourseStudio.ProjectionHost.Windows;

namespace CourseStudio.ProjectionHost.Witness;

public sealed record WitnessContext(
    long Generation,
    string TopologyId,
    bool WindowsValid);

public sealed record WitnessChallenge(
    string ChallengeId,
    DateTimeOffset ExpiresAt,
    long Generation,
    string TopologyId);

internal interface IWitnessEntropySource
{
    void Fill(Span<byte> destination);
}

internal interface IWitnessSurface
{
    event Action<string, string>? CodesSubmitted;

    event Action? Cancelled;

    Task ShowAsync(
        string stageCode,
        string presenterCode,
        IReadOnlyList<RoleWindowEvidence> windows,
        DateTimeOffset expiresAt,
        CancellationToken cancellationToken);

    Task HideAsync(CancellationToken cancellationToken);
}

public sealed class HardwareWitnessCoordinator
{
    private const int CodeLength = 6;
    private static readonly byte[] CodeAlphabet =
        "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"u8.ToArray();
    private static readonly TimeSpan ChallengeLifetime = TimeSpan.FromSeconds(90);
    private static readonly object ProofIssuer = new();

    private readonly IWitnessSurface _surface;
    private readonly IWitnessEntropySource _entropy;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private ActiveChallenge? _active;
    private WitnessContext? _surfaceContext;

    public HardwareWitnessCoordinator()
        : this(new WitnessOverlaySurface(), new RandomWitnessEntropySource())
    {
    }

    internal HardwareWitnessCoordinator(
        IWitnessSurface surface,
        IWitnessEntropySource entropy)
    {
        _surface = surface;
        _entropy = entropy;
        _surface.CodesSubmitted += OnCodesSubmitted;
        _surface.Cancelled += OnCancelled;
    }

    public event Action<NativeWitnessProof>? ProofAccepted;

    public event Action<string>? ProofRejected;

    internal int RetainedRawCodeCount => 0;

    internal int RetainedSensitiveByteCount => _active?.SensitiveByteCount ?? 0;

    internal string? ConsumptionCode { get; private set; }

    public async Task<WitnessChallenge> BeginAsync(
        IReadOnlyList<RoleWindowEvidence> windows,
        WitnessContext context,
        DateTimeOffset startedAt,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(windows);
        ArgumentNullException.ThrowIfNull(context);
        cancellationToken.ThrowIfCancellationRequested();
        ValidateBegin(windows, context);

        await _gate.WaitAsync(cancellationToken);
        try
        {
            if (_active is not null)
            {
                Consume(_active, "challenge_replaced");
                _active = null;
                await _surface.HideAsync(CancellationToken.None);
            }

            byte[] stageCodeBytes = new byte[CodeLength];
            byte[] presenterCodeBytes = new byte[CodeLength];
            byte[] stageSalt = new byte[32];
            byte[] presenterSalt = new byte[32];
            byte[] challengeIdBytes = new byte[16];
            string? stageCode = null;
            string? presenterCode = null;
            bool sensitiveMaterialTransferred = false;
            try
            {
                _entropy.Fill(stageCodeBytes);
                _entropy.Fill(presenterCodeBytes);
                MapToAlphabet(stageCodeBytes);
                MapToAlphabet(presenterCodeBytes);
                if (CryptographicOperations.FixedTimeEquals(
                        stageCodeBytes,
                        presenterCodeBytes))
                {
                    presenterCodeBytes[^1] = CodeAlphabet[
                        (Array.IndexOf(CodeAlphabet, presenterCodeBytes[^1]) + 1)
                        % CodeAlphabet.Length];
                }

                _entropy.Fill(stageSalt);
                _entropy.Fill(presenterSalt);
                _entropy.Fill(challengeIdBytes);
                stageCode = Encoding.ASCII.GetString(stageCodeBytes);
                presenterCode = Encoding.ASCII.GetString(presenterCodeBytes);
                DateTimeOffset expiresAt = startedAt.Add(ChallengeLifetime);
                ActiveChallenge challenge = new(
                    Convert.ToHexStringLower(challengeIdBytes),
                    expiresAt,
                    context.Generation,
                    context.TopologyId,
                    stageSalt,
                    HMACSHA256.HashData(stageSalt, stageCodeBytes),
                    presenterSalt,
                    HMACSHA256.HashData(presenterSalt, presenterCodeBytes));
                _active = challenge;
                sensitiveMaterialTransferred = true;
                _surfaceContext = context;
                ConsumptionCode = null;
                await _surface.ShowAsync(
                    stageCode,
                    presenterCode,
                    windows,
                    expiresAt,
                    cancellationToken);
                return new WitnessChallenge(
                    challenge.ChallengeId,
                    expiresAt,
                    context.Generation,
                    context.TopologyId);
            }
            catch
            {
                if (_active is not null)
                {
                    Consume(_active, "begin_failed");
                    _active = null;
                    _surfaceContext = null;
                }

                await _surface.HideAsync(CancellationToken.None);
                throw;
            }
            finally
            {
                CryptographicOperations.ZeroMemory(stageCodeBytes);
                CryptographicOperations.ZeroMemory(presenterCodeBytes);
                CryptographicOperations.ZeroMemory(challengeIdBytes);
                if (!sensitiveMaterialTransferred)
                {
                    CryptographicOperations.ZeroMemory(stageSalt);
                    CryptographicOperations.ZeroMemory(presenterSalt);
                }
                stageCode = null;
                presenterCode = null;
            }
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<NativeWitnessProof> SubmitAsync(
        string stageCode,
        string presenterCode,
        WitnessContext context,
        DateTimeOffset observedAt,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(stageCode);
        ArgumentNullException.ThrowIfNull(presenterCode);
        ArgumentNullException.ThrowIfNull(context);
        await _gate.WaitAsync(CancellationToken.None);
        try
        {
            ActiveChallenge challenge = _active
                ?? throw new WitnessConsumedException(ConsumptionCode ?? "no_active_challenge");
            if (cancellationToken.IsCancellationRequested)
            {
                ConsumeAndClear(challenge, "cancelled");
                await _surface.HideAsync(CancellationToken.None);
                cancellationToken.ThrowIfCancellationRequested();
            }

            string? rejection = ValidateSubmission(challenge, context, observedAt);
            byte[] stageBytes = Encoding.ASCII.GetBytes(stageCode);
            byte[] presenterBytes = Encoding.ASCII.GetBytes(presenterCode);
            byte[] stageDigest = [];
            byte[] presenterDigest = [];
            NativeWitnessProof? proof = null;
            try
            {
                stageDigest = HMACSHA256.HashData(challenge.StageSalt, stageBytes);
                presenterDigest = HMACSHA256.HashData(
                    challenge.PresenterSalt,
                    presenterBytes);
                bool stageMatches = stageCode.Length == CodeLength
                    && CryptographicOperations.FixedTimeEquals(
                        stageDigest,
                        challenge.StageDigest);
                bool presenterMatches = presenterCode.Length == CodeLength
                    && CryptographicOperations.FixedTimeEquals(
                        presenterDigest,
                        challenge.PresenterDigest);
                if (rejection is null && !(stageMatches & presenterMatches))
                {
                    rejection = "witness_code_mismatch";
                }

                if (rejection is null)
                {
                    string witnessDigest = WitnessDigest(
                        challenge,
                        stageDigest,
                        presenterDigest,
                        observedAt);
                    proof = new NativeWitnessProof(
                        ProofIssuer,
                        challenge.ChallengeId,
                        witnessDigest,
                        observedAt,
                        challenge.Generation,
                        challenge.TopologyId);
                }
            }
            finally
            {
                CryptographicOperations.ZeroMemory(stageBytes);
                CryptographicOperations.ZeroMemory(presenterBytes);
                CryptographicOperations.ZeroMemory(stageDigest);
                CryptographicOperations.ZeroMemory(presenterDigest);
            }

            ConsumeAndClear(challenge, rejection ?? "accepted");
            await _surface.HideAsync(CancellationToken.None);
            if (rejection is not null)
            {
                throw new WitnessRejectedException(rejection);
            }

            return proof!;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task InvalidateAsync(
        string code,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(code))
        {
            throw new ArgumentException("An invalidation code is required.", nameof(code));
        }

        await _gate.WaitAsync(cancellationToken);
        try
        {
            if (_active is not null)
            {
                ConsumeAndClear(_active, code);
                await _surface.HideAsync(CancellationToken.None);
            }
        }
        finally
        {
            _gate.Release();
        }
    }

    private static void ValidateBegin(
        IReadOnlyList<RoleWindowEvidence> windows,
        WitnessContext context)
    {
        bool valid = context.WindowsValid
            && context.Generation >= 0
            && context.TopologyId.Length == 64
            && windows.Count == 2
            && windows.Select(window => window.Role).Distinct().Count() == 2
            && windows.Select(window => window.WindowId)
                .Distinct(StringComparer.Ordinal)
                .Count() == 2
            && windows.Select(window => window.DisplayId)
                .Distinct(StringComparer.Ordinal)
                .Count() == 2
            && windows.All(window =>
                window.Generation == context.Generation
                && window.IsExactFullscreen);
        if (!valid)
        {
            throw new WitnessRejectedException("witness_windows_invalid");
        }
    }

    private static string? ValidateSubmission(
        ActiveChallenge challenge,
        WitnessContext context,
        DateTimeOffset observedAt)
    {
        if (observedAt > challenge.ExpiresAt)
        {
            return "witness_expired";
        }

        if (!context.WindowsValid
            || context.Generation != challenge.Generation
            || !string.Equals(
                context.TopologyId,
                challenge.TopologyId,
                StringComparison.Ordinal))
        {
            return "witness_state_changed";
        }

        return null;
    }

    private void ConsumeAndClear(ActiveChallenge challenge, string code)
    {
        Consume(challenge, code);
        _active = null;
        _surfaceContext = null;
    }

    private void Consume(ActiveChallenge challenge, string code)
    {
        challenge.Zeroize();
        ConsumptionCode = code;
    }

    private static void MapToAlphabet(Span<byte> code)
    {
        for (int index = 0; index < code.Length; index++)
        {
            code[index] = CodeAlphabet[code[index] % CodeAlphabet.Length];
        }
    }

    private static string WitnessDigest(
        ActiveChallenge challenge,
        byte[] stageDigest,
        byte[] presenterDigest,
        DateTimeOffset observedAt)
    {
        byte[] identity = Encoding.UTF8.GetBytes(
            string.Join(
                '|',
                challenge.ChallengeId,
                challenge.Generation.ToString(System.Globalization.CultureInfo.InvariantCulture),
                challenge.TopologyId,
                observedAt.ToUniversalTime().ToString("O", System.Globalization.CultureInfo.InvariantCulture),
                Convert.ToHexStringLower(stageDigest),
                Convert.ToHexStringLower(presenterDigest)));
        try
        {
            return Convert.ToHexStringLower(SHA256.HashData(identity));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(identity);
        }
    }

    private async void OnCodesSubmitted(string stageCode, string presenterCode)
    {
        WitnessContext? context = _surfaceContext;
        if (context is null)
        {
            return;
        }

        try
        {
            NativeWitnessProof proof = await SubmitAsync(
                stageCode,
                presenterCode,
                context,
                DateTimeOffset.UtcNow,
                CancellationToken.None);
            ProofAccepted?.Invoke(proof);
        }
        catch (WitnessRejectedException exception)
        {
            ProofRejected?.Invoke(exception.Code);
        }
        catch (WitnessConsumedException exception)
        {
            ProofRejected?.Invoke(exception.Code);
        }
    }

    private async void OnCancelled()
    {
        try
        {
            await InvalidateAsync("cancelled", CancellationToken.None);
            ProofRejected?.Invoke("cancelled");
        }
        catch (Exception exception)
        {
            ProofRejected?.Invoke($"cancel_failed:{exception.GetType().Name}");
        }
    }

    public sealed class NativeWitnessProof
    {
        internal NativeWitnessProof(
            object issuer,
            string challengeId,
            string witnessDigest,
            DateTimeOffset acceptedAt,
            long generation,
            string topologyId)
        {
            if (!ReferenceEquals(issuer, ProofIssuer))
            {
                throw new UnauthorizedAccessException(
                    "Only the attended witness coordinator can issue a proof.");
            }

            ChallengeId = challengeId;
            WitnessDigest = witnessDigest;
            AcceptedAt = acceptedAt;
            Generation = generation;
            TopologyId = topologyId;
        }

        public string ChallengeId { get; }

        public string WitnessDigest { get; }

        public DateTimeOffset AcceptedAt { get; }

        public long Generation { get; }

        public string TopologyId { get; }
    }

    private sealed class ActiveChallenge
    {
        internal ActiveChallenge(
            string challengeId,
            DateTimeOffset expiresAt,
            long generation,
            string topologyId,
            byte[] stageSalt,
            byte[] stageDigest,
            byte[] presenterSalt,
            byte[] presenterDigest)
        {
            ChallengeId = challengeId;
            ExpiresAt = expiresAt;
            Generation = generation;
            TopologyId = topologyId;
            StageSalt = stageSalt;
            StageDigest = stageDigest;
            PresenterSalt = presenterSalt;
            PresenterDigest = presenterDigest;
        }

        internal string ChallengeId { get; }

        internal DateTimeOffset ExpiresAt { get; }

        internal long Generation { get; }

        internal string TopologyId { get; }

        internal byte[] StageSalt { get; private set; }

        internal byte[] StageDigest { get; private set; }

        internal byte[] PresenterSalt { get; private set; }

        internal byte[] PresenterDigest { get; private set; }

        internal int SensitiveByteCount =>
            StageSalt.Length
            + StageDigest.Length
            + PresenterSalt.Length
            + PresenterDigest.Length;

        internal void Zeroize()
        {
            CryptographicOperations.ZeroMemory(StageSalt);
            CryptographicOperations.ZeroMemory(StageDigest);
            CryptographicOperations.ZeroMemory(PresenterSalt);
            CryptographicOperations.ZeroMemory(PresenterDigest);
            StageSalt = [];
            StageDigest = [];
            PresenterSalt = [];
            PresenterDigest = [];
        }
    }

    private sealed class RandomWitnessEntropySource : IWitnessEntropySource
    {
        public void Fill(Span<byte> destination) =>
            RandomNumberGenerator.Fill(destination);
    }
}

public sealed class WitnessRejectedException(string code)
    : InvalidOperationException(code)
{
    public string Code { get; } = code;
}

public sealed class WitnessConsumedException(string code)
    : InvalidOperationException(code)
{
    public string Code { get; } = code;
}
