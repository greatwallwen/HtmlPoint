using System.ComponentModel;
using System.Runtime.InteropServices;
using CourseStudio.ProjectionHost.Core;

namespace CourseStudio.ProjectionHost.Native;

public interface IDisplayTopologyProvider
{
    DisplayTopology Read(ReadOnlySpan<byte> sessionSalt);
}

public sealed class Win32DisplayTopologyProvider : IDisplayTopologyProvider
{
    private const int MaximumQueryAttempts = 5;

    public DisplayTopology Read(ReadOnlySpan<byte> sessionSalt)
    {
        if (sessionSalt.Length < 16)
        {
            throw new ArgumentException(
                "Projection session salt must contain at least 128 bits.",
                nameof(sessionSalt));
        }

        string sessionKind = IsRemoteSession() ? "remote" : "interactive_local";
        DateTimeOffset capturedAt = DateTimeOffset.UtcNow;
        if (!DpiContextScope.TryEnter(out DpiContextScope? scope))
        {
            return DisplayTopologyMapper.UnknownTopology(
                sessionKind,
                sessionSalt,
                "per_monitor_v2_unavailable",
                capturedAt);
        }

        using (scope)
        {
            try
            {
                IReadOnlyList<RawDisplaySnapshot> snapshots = ReadSnapshots();
                DisplayTopologyMapping mapping = DisplayTopologyMapper.Map(
                    snapshots,
                    sessionKind,
                    sessionSalt,
                    capturedAt);
                if (string.Equals(mapping.Topology.Mode, "extended", StringComparison.Ordinal)
                    && !mapping.CertificationEligible)
                {
                    return mapping.Topology with { Mode = "unknown" };
                }

                return mapping.Topology;
            }
            catch (Exception exception) when (
                exception is Win32Exception
                    or ExternalException
                    or OverflowException)
            {
                return DisplayTopologyMapper.UnknownTopology(
                    sessionKind,
                    sessionSalt,
                    "native_topology_read_failed",
                    capturedAt);
            }
        }
    }

    private static IReadOnlyList<RawDisplaySnapshot> ReadSnapshots()
    {
        IReadOnlyDictionary<string, MonitorSnapshot> monitors = ReadMonitors();
        DisplayNative.DisplayConfigPathInfo[] paths = ReadActivePaths();
        List<RawDisplaySnapshot> snapshots = [];
        foreach (DisplayNative.DisplayConfigPathInfo path in paths)
        {
            DisplayNative.DisplayConfigSourceDeviceName source = SourceInfo(path);
            DisplayNative.DisplayConfigTargetDeviceName target = TargetInfo(path);
            monitors.TryGetValue(source.ViewGdiDeviceName, out MonitorSnapshot? monitor);

            DisplayNative.DisplayConfigVideoOutputTechnology technology =
                path.TargetInfo.OutputTechnology;
            bool internalHint = IsInternal(technology);
            bool virtualIndicator = IsKnownVirtual(
                technology,
                source.ViewGdiDeviceName,
                target.MonitorFriendlyDeviceName,
                target.MonitorDevicePath);
            uint refreshRate = RefreshRateMilliHertz(path.TargetInfo.RefreshRate);
            snapshots.Add(
                new RawDisplaySnapshot(
                    path.TargetInfo.AdapterId.ToString(),
                    path.TargetInfo.Id,
                    path.SourceInfo.Id,
                    source.ViewGdiDeviceName,
                    target.MonitorFriendlyDeviceName,
                    target.MonitorDevicePath,
                    monitor?.Bounds ?? new PhysicalRect(0, 0, 0, 0),
                    monitor?.WorkArea ?? new PhysicalRect(0, 0, 0, 0),
                    monitor?.DpiX ?? 0,
                    monitor?.DpiY ?? 0,
                    refreshRate,
                    monitor?.Primary ?? false,
                    internalHint,
                    !internalHint,
                    path.TargetInfo.TargetAvailable
                        && monitor is not null
                        && !string.IsNullOrWhiteSpace(target.MonitorDevicePath),
                    !virtualIndicator,
                    RotationDegrees(path.TargetInfo.Rotation),
                    true));
        }

        return snapshots;
    }

    private static DisplayNative.DisplayConfigPathInfo[] ReadActivePaths()
    {
        uint flags = DisplayNative.QueryActivePaths | DisplayNative.QueryVirtualModeAware;
        for (int attempt = 0; attempt < MaximumQueryAttempts; attempt++)
        {
            int error = DisplayNative.GetDisplayConfigBufferSizes(
                flags,
                out uint pathCount,
                out uint modeCount);
            DisplayNative.ThrowIfFailed(error, nameof(DisplayNative.GetDisplayConfigBufferSizes));

            DisplayNative.DisplayConfigPathInfo[] paths =
                new DisplayNative.DisplayConfigPathInfo[pathCount];
            DisplayNative.DisplayConfigModeInfo[] modes =
                new DisplayNative.DisplayConfigModeInfo[modeCount];
            error = DisplayNative.QueryDisplayConfig(
                flags,
                ref pathCount,
                paths,
                ref modeCount,
                modes,
                0);
            if (error == DisplayNative.ErrorInsufficientBuffer)
            {
                continue;
            }

            DisplayNative.ThrowIfFailed(error, nameof(DisplayNative.QueryDisplayConfig));
            return paths.Take(checked((int)pathCount)).ToArray();
        }

        throw new Win32Exception(
            DisplayNative.ErrorInsufficientBuffer,
            "The active display topology changed repeatedly while it was read.");
    }

    private static IReadOnlyDictionary<string, MonitorSnapshot> ReadMonitors()
    {
        Dictionary<string, MonitorSnapshot> monitors =
            new(StringComparer.OrdinalIgnoreCase);
        DisplayNative.MonitorEnumProcedure callback = (
            nint monitor,
            nint _,
            ref DisplayNative.NativeRect __,
            nint ___) =>
        {
            DisplayNative.MonitorInfoEx info = new()
            {
                Size = checked((uint)Marshal.SizeOf<DisplayNative.MonitorInfoEx>()),
                DeviceName = string.Empty,
            };
            if (!DisplayNative.GetMonitorInfo(monitor, ref info))
            {
                return true;
            }

            uint dpiX = 96;
            uint dpiY = 96;
            int dpiError = DisplayNative.GetDpiForMonitor(
                monitor,
                DisplayNative.MonitorDpiEffective,
                out uint measuredDpiX,
                out uint measuredDpiY);
            if (dpiError == 0 && measuredDpiX > 0 && measuredDpiY > 0)
            {
                dpiX = measuredDpiX;
                dpiY = measuredDpiY;
            }

            monitors[info.DeviceName] = new MonitorSnapshot(
                info.Monitor.ToPhysicalRect(),
                info.WorkArea.ToPhysicalRect(),
                dpiX,
                dpiY,
                (info.Flags & DisplayNative.MonitorInfoPrimary) != 0);
            return true;
        };

        if (!DisplayNative.EnumDisplayMonitors(0, 0, callback, 0))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }

        GC.KeepAlive(callback);
        return monitors;
    }

    private static DisplayNative.DisplayConfigSourceDeviceName SourceInfo(
        DisplayNative.DisplayConfigPathInfo path)
    {
        DisplayNative.DisplayConfigSourceDeviceName request = new()
        {
            Header = new DisplayNative.DisplayConfigDeviceInfoHeader
            {
                Type = DisplayNative.DisplayConfigDeviceInfoType.GetSourceName,
                Size = checked((uint)Marshal.SizeOf<DisplayNative.DisplayConfigSourceDeviceName>()),
                AdapterId = path.SourceInfo.AdapterId,
                Id = path.SourceInfo.Id,
            },
            ViewGdiDeviceName = string.Empty,
        };
        int error = DisplayNative.GetSourceDeviceInfo(ref request);
        DisplayNative.ThrowIfFailed(error, nameof(DisplayNative.GetSourceDeviceInfo));
        return request;
    }

    private static DisplayNative.DisplayConfigTargetDeviceName TargetInfo(
        DisplayNative.DisplayConfigPathInfo path)
    {
        DisplayNative.DisplayConfigTargetDeviceName request = new()
        {
            Header = new DisplayNative.DisplayConfigDeviceInfoHeader
            {
                Type = DisplayNative.DisplayConfigDeviceInfoType.GetTargetName,
                Size = checked((uint)Marshal.SizeOf<DisplayNative.DisplayConfigTargetDeviceName>()),
                AdapterId = path.TargetInfo.AdapterId,
                Id = path.TargetInfo.Id,
            },
            MonitorFriendlyDeviceName = string.Empty,
            MonitorDevicePath = string.Empty,
        };
        int error = DisplayNative.GetTargetDeviceInfo(ref request);
        DisplayNative.ThrowIfFailed(error, nameof(DisplayNative.GetTargetDeviceInfo));
        return request;
    }

    private static uint RefreshRateMilliHertz(
        DisplayNative.DisplayConfigRational refreshRate)
    {
        if (refreshRate.Denominator == 0)
        {
            return 0;
        }

        double milliHertz =
            refreshRate.Numerator * 1000d / refreshRate.Denominator;
        return checked((uint)Math.Round(milliHertz));
    }

    private static bool IsInternal(
        DisplayNative.DisplayConfigVideoOutputTechnology technology) => technology is
        DisplayNative.DisplayConfigVideoOutputTechnology.Lvds
        or DisplayNative.DisplayConfigVideoOutputTechnology.DisplayPortEmbedded
        or DisplayNative.DisplayConfigVideoOutputTechnology.UdiEmbedded
        or DisplayNative.DisplayConfigVideoOutputTechnology.Internal;

    private static bool IsKnownVirtual(
        DisplayNative.DisplayConfigVideoOutputTechnology technology,
        params string[] identities)
    {
        if (technology is DisplayNative.DisplayConfigVideoOutputTechnology.Miracast
            or DisplayNative.DisplayConfigVideoOutputTechnology.IndirectWired)
        {
            return true;
        }

        string[] indicators = ["RDP", "VIRTUAL", "INDIRECT", "MIRACAST"];
        return identities.Any(identity => indicators.Any(indicator =>
            identity.Contains(indicator, StringComparison.OrdinalIgnoreCase)));
    }

    private static int RotationDegrees(DisplayNative.DisplayConfigRotation rotation) =>
        rotation switch
        {
            DisplayNative.DisplayConfigRotation.Identity => 0,
            DisplayNative.DisplayConfigRotation.Rotate90 => 90,
            DisplayNative.DisplayConfigRotation.Rotate180 => 180,
            DisplayNative.DisplayConfigRotation.Rotate270 => 270,
            _ => -1,
        };

    private static bool IsRemoteSession() =>
        DisplayNative.GetSystemMetrics(DisplayNative.RemoteSessionMetric) != 0
        || DisplayNative.GetSystemMetrics(DisplayNative.RemoteControlMetric) != 0;

    private sealed record MonitorSnapshot(
        PhysicalRect Bounds,
        PhysicalRect WorkArea,
        uint DpiX,
        uint DpiY,
        bool Primary);

    private sealed class DpiContextScope : IDisposable
    {
        private readonly nint _previous;
        private readonly bool _restore;

        private DpiContextScope(nint previous, bool restore)
        {
            _previous = previous;
            _restore = restore;
        }

        public static bool TryEnter(out DpiContextScope? scope)
        {
            nint current = DisplayNative.GetThreadDpiAwarenessContext();
            if (current != 0
                && DisplayNative.AreDpiAwarenessContextsEqual(
                    current,
                    DisplayNative.PerMonitorAwareV2))
            {
                scope = new DpiContextScope(current, false);
                return true;
            }

            nint previous = DisplayNative.SetThreadDpiAwarenessContext(
                DisplayNative.PerMonitorAwareV2);
            if (previous == 0)
            {
                scope = null;
                return false;
            }

            nint effective = DisplayNative.GetThreadDpiAwarenessContext();
            if (!DisplayNative.AreDpiAwarenessContextsEqual(
                    effective,
                    DisplayNative.PerMonitorAwareV2))
            {
                _ = DisplayNative.SetThreadDpiAwarenessContext(previous);
                scope = null;
                return false;
            }

            scope = new DpiContextScope(previous, true);
            return true;
        }

        public void Dispose()
        {
            if (_restore)
            {
                _ = DisplayNative.SetThreadDpiAwarenessContext(_previous);
            }
        }
    }
}
