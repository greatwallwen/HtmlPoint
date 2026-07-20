namespace CourseStudio.ProjectionHost.Core;

public sealed class ProjectionReducer : IProjectionReducer
{
    public ProjectionTransition Apply(ProjectionState state, ProjectionSignal signal)
    {
        ArgumentNullException.ThrowIfNull(state);
        ArgumentNullException.ThrowIfNull(signal);

        if (state.Phase is ProjectionPhase.Invalidated or ProjectionPhase.Closed)
        {
            return Invalidate(state, signal, "transition_after_terminal");
        }

        return signal switch
        {
            DisplaysDetected detected => ApplyDisplaysDetected(state, detected),
            WindowsAssigned assigned => ApplyWindowsAssigned(state, assigned),
            FullscreenVerified fullscreen => ApplyFullscreenVerified(state, fullscreen),
            FrameCommitted committed => ApplyFrameCommitted(state, committed),
            WitnessChallengeIssued challenge => ApplyChallengeIssued(state, challenge),
            NativeWitnessAccepted accepted => ApplyWitnessAccepted(state, accepted),
            SimulatedWitnessObserved => Invalidate(state, signal, "simulated_witness"),
            TopologyChanged => Invalidate(state, signal, "topology_changed"),
            DpiChanged => Invalidate(state, signal, "dpi_changed"),
            RoleCollisionDetected => Invalidate(state, signal, "role_collision"),
            WindowMinimized => Invalidate(state, signal, "window_minimized"),
            WindowCloaked => Invalidate(state, signal, "window_cloaked"),
            IdentityMismatchDetected => Invalidate(state, signal, "identity_mismatch"),
            HeartbeatExpired => Invalidate(state, signal, "heartbeat_expired"),
            NavigationChanged => Invalidate(state, signal, "navigation_changed"),
            RuntimeChanged => Invalidate(state, signal, "runtime_changed"),
            HelperRestarted => Invalidate(state, signal, "helper_restarted"),
            HostRestarted => Invalidate(state, signal, "host_restarted"),
            _ => Invalidate(state, signal, "unknown_signal"),
        };
    }

    private static ProjectionTransition ApplyDisplaysDetected(
        ProjectionState state,
        DisplaysDetected signal)
    {
        if (state.Phase != ProjectionPhase.Undetected || !IsCandidate(signal.Topology))
        {
            return Invalidate(state, signal, "invalid_topology");
        }

        return Advance(
            state with
            {
                Phase = ProjectionPhase.Candidate,
                Topology = signal.Topology,
            },
            signal);
    }

    private static ProjectionTransition ApplyWindowsAssigned(
        ProjectionState state,
        WindowsAssigned signal)
    {
        if (state.Phase != ProjectionPhase.Candidate || state.Topology is null)
        {
            return Invalidate(state, signal, "invalid_transition");
        }

        HashSet<string> displays = state.Topology.Displays
            .Select(display => display.DisplayId)
            .ToHashSet(StringComparer.Ordinal);
        bool collision =
            string.Equals(
                signal.StageWindow.WindowId,
                signal.PresenterWindow.WindowId,
                StringComparison.Ordinal)
            || string.Equals(
                signal.StageWindow.DisplayId,
                signal.PresenterWindow.DisplayId,
                StringComparison.Ordinal);
        bool invalidWindowIdentity =
            !IsDigest(signal.StageWindow.WindowId)
            || !IsDigest(signal.PresenterWindow.WindowId)
            || signal.StageWindow.WindowGeneration < 0
            || signal.PresenterWindow.WindowGeneration < 0;
        bool unknownDisplay =
            !displays.Contains(signal.StageWindow.DisplayId)
            || !displays.Contains(signal.PresenterWindow.DisplayId);
        if (collision)
        {
            return Invalidate(state, signal, "role_collision");
        }

        if (unknownDisplay || invalidWindowIdentity)
        {
            return Invalidate(state, signal, "display_not_found");
        }

        return Advance(
            state with
            {
                Phase = ProjectionPhase.Assigned,
                Assignment = new RoleAssignment(
                    signal.StageWindow,
                    signal.PresenterWindow),
            },
            signal);
    }

    private static ProjectionTransition ApplyFullscreenVerified(
        ProjectionState state,
        FullscreenVerified signal)
    {
        if (state.Phase != ProjectionPhase.Assigned
            || state.Topology is null
            || state.Assignment is null)
        {
            return Invalidate(state, signal, "invalid_transition");
        }

        if (!GeometryMatches(
                state.Topology,
                state.Assignment.StageWindow,
                signal.StageGeometry)
            || !GeometryMatches(
                state.Topology,
                state.Assignment.PresenterWindow,
                signal.PresenterGeometry))
        {
            return Invalidate(state, signal, "fullscreen_mismatch");
        }

        return Advance(state with { Phase = ProjectionPhase.Fullscreen }, signal);
    }

    private static ProjectionTransition ApplyFrameCommitted(
        ProjectionState state,
        FrameCommitted signal)
    {
        if (state.Phase is not (
                ProjectionPhase.Fullscreen
                or ProjectionPhase.Syncing
                or ProjectionPhase.Certified))
        {
            return Invalidate(state, signal, "invalid_transition");
        }

        if (signal.Frame.Sequence < 0
            || signal.Frame.CourseVersionId is not { Length: >= 1 and <= 128 }
            || !IsDigest(signal.Frame.RuntimeManifestDigest)
            || !IsDigest(signal.Frame.NavigationIdentity)
            || !IsDigest(signal.Frame.FrameDigest))
        {
            return Invalidate(state, signal, "invalid_frame");
        }

        if (state.LatestFrame is not null)
        {
            if (signal.Frame.Sequence < state.LatestFrame.Sequence)
            {
                return Invalidate(state, signal, "frame_rollback");
            }

            if (signal.Frame.Sequence == state.LatestFrame.Sequence)
            {
                if (signal.Frame != state.LatestFrame)
                {
                    return Invalidate(state, signal, "identity_mismatch");
                }

                if (state.Phase == ProjectionPhase.Certified)
                {
                    return Invalidate(state, signal, "frame_replayed");
                }
            }
            else
            {
                if (signal.Frame.Sequence != state.LatestFrame.Sequence + 1)
                {
                    return Invalidate(state, signal, "frame_gap");
                }

                string? identityChange = IdentityChange(state.LatestFrame, signal.Frame);
                if (identityChange is not null)
                {
                    return Invalidate(state, signal, identityChange);
                }
            }
        }

        RoleCommit commit = new(signal.Role, signal.Frame);
        ProjectionState next = signal.Role switch
        {
            Role.Stage => state with
            {
                StageCommit = commit,
                Phase = ProjectionPhase.Syncing,
                PhysicalDualScreenCertified = false,
            },
            Role.Presenter => state with
            {
                PresenterCommit = commit,
                Phase = ProjectionPhase.Syncing,
                PhysicalDualScreenCertified = false,
            },
            _ => throw new ArgumentOutOfRangeException(nameof(signal), signal.Role, null),
        };

        if (next.StageCommit is not null && next.PresenterCommit is not null)
        {
            if (next.StageCommit.Frame.Sequence == next.PresenterCommit.Frame.Sequence
                && next.StageCommit.Frame != next.PresenterCommit.Frame)
            {
                return Invalidate(state, signal, "identity_mismatch");
            }

            if (next.StageCommit.Frame == next.PresenterCommit.Frame)
            {
                next = next with { LatestFrame = next.StageCommit.Frame };
                if (next.Witness?.WitnessDigest is not null)
                {
                    next = next with
                    {
                        Phase = ProjectionPhase.Certified,
                        PhysicalDualScreenCertified = true,
                    };
                }
            }
        }

        return Advance(next, signal);
    }

    private static ProjectionTransition ApplyChallengeIssued(
        ProjectionState state,
        WitnessChallengeIssued signal)
    {
        bool synchronized =
            state.StageCommit is not null
            && state.PresenterCommit is not null
            && state.StageCommit.Frame == state.PresenterCommit.Frame
            && state.LatestFrame == state.StageCommit.Frame;
        bool challengeValid =
            signal.ChallengeIdentity.ChallengeId is { Length: >= 1 and <= 128 }
            && IsDigest(signal.ChallengeIdentity.ChallengeDigest)
            && signal.Expiry > signal.ChallengeIdentity.ObservedAt;
        if (state.Phase != ProjectionPhase.Syncing || !synchronized || !challengeValid)
        {
            return Invalidate(state, signal, "invalid_witness_challenge");
        }

        WitnessIdentity challenge = signal.ChallengeIdentity with
        {
            ExpiresAt = signal.Expiry,
            WitnessDigest = null,
        };
        return Advance(
            state with
            {
                Phase = ProjectionPhase.WitnessPending,
                Witness = challenge,
            },
            signal);
    }

    private static ProjectionTransition ApplyWitnessAccepted(
        ProjectionState state,
        NativeWitnessAccepted signal)
    {
        if (state.Phase == ProjectionPhase.Certified
            || state.Witness?.WitnessDigest is not null)
        {
            return Invalidate(state, signal, "witness_replayed");
        }

        if (state.Phase != ProjectionPhase.WitnessPending || state.Witness is null)
        {
            return Invalidate(state, signal, "invalid_transition");
        }

        bool identityMatches =
            string.Equals(
                state.Witness.ChallengeId,
                signal.ChallengeIdentity.ChallengeId,
                StringComparison.Ordinal)
            && string.Equals(
                state.Witness.ChallengeDigest,
                signal.ChallengeIdentity.ChallengeDigest,
                StringComparison.Ordinal);
        if (!identityMatches)
        {
            return Invalidate(state, signal, "witness_identity_mismatch");
        }

        if (signal.ChallengeIdentity.ObservedAt > state.Witness.ExpiresAt)
        {
            return Invalidate(state, signal, "witness_expired");
        }

        if (!IsDigest(signal.WitnessDigest))
        {
            return Invalidate(state, signal, "invalid_witness_digest");
        }

        return Advance(
            state with
            {
                Phase = ProjectionPhase.Certified,
                Witness = state.Witness with { WitnessDigest = signal.WitnessDigest },
                PhysicalDualScreenCertified = true,
                ReleaseSignatureCertified = false,
            },
            signal);
    }

    private static ProjectionTransition Advance(
        ProjectionState state,
        ProjectionSignal signal)
    {
        ProjectionState next = state with
        {
            Generation = checked(state.Generation + 1),
            ReleaseSignatureCertified = false,
            InvalidationCode = null,
        };
        return new ProjectionTransition(next, [ProjectionEvidence.EventFor(next, signal)]);
    }

    private static ProjectionTransition Invalidate(
        ProjectionState state,
        ProjectionSignal signal,
        string code)
    {
        ProjectionState next = state with
        {
            Phase = ProjectionPhase.Invalidated,
            Generation = checked(state.Generation + 1),
            PhysicalDualScreenCertified = false,
            ReleaseSignatureCertified = false,
            InvalidationCode = code,
        };
        return new ProjectionTransition(next, [ProjectionEvidence.EventFor(next, signal)]);
    }

    private static bool IsCandidate(DisplayTopology topology)
    {
        if (topology.SchemaVersion != 1
            || !IsDigest(topology.TopologyId)
            || !string.Equals(topology.SessionKind, "interactive_local", StringComparison.Ordinal)
            || !string.Equals(topology.Mode, "extended", StringComparison.Ordinal)
            || topology.Displays.Count < 2
            || topology.Displays.Count > 16
            || topology.Displays.Count(display => display.IsPrimary) != 1)
        {
            return false;
        }

        return topology.Displays.All(display => IsDigest(display.DisplayId))
            && topology.Displays
                .Select(display => display.DisplayId)
                .Distinct(StringComparer.Ordinal)
                .Count() == topology.Displays.Count;
    }

    private static bool GeometryMatches(
        DisplayTopology topology,
        WindowIdentity window,
        WindowGeometry geometry)
    {
        ProjectionDisplay? display = topology.Displays.FirstOrDefault(candidate =>
            string.Equals(candidate.DisplayId, window.DisplayId, StringComparison.Ordinal));
        return display is not null
            && string.Equals(geometry.DisplayId, window.DisplayId, StringComparison.Ordinal)
            && geometry.Bounds == display.Bounds
            && geometry.Dpi is >= 48 and <= 768
            && geometry.IsFullscreen
            && !geometry.IsMinimized
            && !geometry.IsCloaked;
    }

    private static string? IdentityChange(FrameIdentity current, FrameIdentity next)
    {
        if (!string.Equals(
                current.CourseVersionId,
                next.CourseVersionId,
                StringComparison.Ordinal))
        {
            return "identity_mismatch";
        }

        if (!string.Equals(
                current.RuntimeManifestDigest,
                next.RuntimeManifestDigest,
                StringComparison.Ordinal))
        {
            return "runtime_changed";
        }

        if (!string.Equals(
                current.NavigationIdentity,
                next.NavigationIdentity,
                StringComparison.Ordinal))
        {
            return "navigation_changed";
        }

        return null;
    }

    private static bool IsDigest(string? value) =>
        value is { Length: 64 } && value.All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f');
}
