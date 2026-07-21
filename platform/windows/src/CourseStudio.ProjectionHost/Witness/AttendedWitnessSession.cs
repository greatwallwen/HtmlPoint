using CourseStudio.ProjectionHost.Windows;

namespace CourseStudio.ProjectionHost.Witness;

internal sealed record AttendedWitnessResult(
    WitnessChallenge Challenge,
    string WitnessDigest,
    DateTimeOffset AcceptedAt,
    long Generation,
    string TopologyId);

internal interface IAttendedWitnessSession
{
    Task<AttendedWitnessResult> RunAsync(
        IReadOnlyList<RoleWindowEvidence> windows,
        WitnessContext context,
        DateTimeOffset startedAt,
        CancellationToken cancellationToken);

    Task InvalidateAsync(string code, CancellationToken cancellationToken);
}

internal sealed class AttendedWitnessSession : IAttendedWitnessSession
{
    private readonly HardwareWitnessCoordinator _coordinator = new();
    private readonly SemaphoreSlim _gate = new(1, 1);

    public async Task<AttendedWitnessResult> RunAsync(
        IReadOnlyList<RoleWindowEvidence> windows,
        WitnessContext context,
        DateTimeOffset startedAt,
        CancellationToken cancellationToken)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            TaskCompletionSource<HardwareWitnessCoordinator.NativeWitnessProof> completion =
                new(TaskCreationOptions.RunContinuationsAsynchronously);
            void Accept(HardwareWitnessCoordinator.NativeWitnessProof proof) =>
                completion.TrySetResult(proof);
            void Reject(string code) => completion.TrySetException(
                new WitnessRejectedException(code));
            _coordinator.ProofAccepted += Accept;
            _coordinator.ProofRejected += Reject;
            try
            {
                WitnessChallenge challenge = await _coordinator.BeginAsync(
                    windows,
                    context,
                    startedAt,
                    cancellationToken);
                HardwareWitnessCoordinator.NativeWitnessProof proof;
                try
                {
                    proof = await completion.Task.WaitAsync(cancellationToken);
                }
                catch (OperationCanceledException)
                {
                    await _coordinator.InvalidateAsync(
                        "cancelled",
                        CancellationToken.None);
                    throw;
                }

                return new AttendedWitnessResult(
                    challenge,
                    proof.WitnessDigest,
                    proof.AcceptedAt,
                    proof.Generation,
                    proof.TopologyId);
            }
            finally
            {
                _coordinator.ProofAccepted -= Accept;
                _coordinator.ProofRejected -= Reject;
            }
        }
        finally
        {
            _gate.Release();
        }
    }

    public Task InvalidateAsync(string code, CancellationToken cancellationToken) =>
        _coordinator.InvalidateAsync(code, cancellationToken);
}
