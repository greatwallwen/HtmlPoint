using System.ComponentModel;
using System.Runtime.InteropServices;

namespace CourseStudio.ProjectionHost.Native;

internal static class DisplayNative
{
    internal const uint QueryActivePaths = 0x00000002;
    internal const uint QueryVirtualModeAware = 0x00000010;
    internal const int ErrorInsufficientBuffer = 122;
    internal const int MonitorInfoPrimary = 0x00000001;
    internal const int MonitorDpiEffective = 0;
    internal const int RemoteSessionMetric = 0x1000;
    internal const int RemoteControlMetric = 0x2001;
    internal static readonly nint PerMonitorAwareV2 = new(-4);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern int GetDisplayConfigBufferSizes(
        uint flags,
        out uint pathCount,
        out uint modeCount);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern int QueryDisplayConfig(
        uint flags,
        ref uint pathCount,
        [Out] DisplayConfigPathInfo[] paths,
        ref uint modeCount,
        [Out] DisplayConfigModeInfo[] modes,
        nint currentTopologyId);

    [DllImport(
        "user32.dll",
        EntryPoint = "DisplayConfigGetDeviceInfo",
        CharSet = CharSet.Unicode,
        SetLastError = true)]
    internal static extern int GetSourceDeviceInfo(
        ref DisplayConfigSourceDeviceName request);

    [DllImport(
        "user32.dll",
        EntryPoint = "DisplayConfigGetDeviceInfo",
        CharSet = CharSet.Unicode,
        SetLastError = true)]
    internal static extern int GetTargetDeviceInfo(
        ref DisplayConfigTargetDeviceName request);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool EnumDisplayMonitors(
        nint deviceContext,
        nint clipRectangle,
        MonitorEnumProcedure callback,
        nint data);

    [DllImport(
        "user32.dll",
        EntryPoint = "GetMonitorInfoW",
        CharSet = CharSet.Unicode,
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetMonitorInfo(
        nint monitor,
        ref MonitorInfoEx info);

    [DllImport("user32.dll")]
    internal static extern int GetSystemMetrics(int index);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern nint GetThreadDpiAwarenessContext();

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern nint SetThreadDpiAwarenessContext(nint awarenessContext);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool AreDpiAwarenessContextsEqual(nint first, nint second);

    [DllImport("shcore.dll")]
    internal static extern int GetDpiForMonitor(
        nint monitor,
        int dpiType,
        out uint dpiX,
        out uint dpiY);

    internal static void ThrowIfFailed(int error, string operation)
    {
        if (error != 0)
        {
            throw new Win32Exception(error, $"{operation} failed with Win32 error {error}.");
        }
    }

    internal delegate bool MonitorEnumProcedure(
        nint monitor,
        nint deviceContext,
        ref NativeRect bounds,
        nint data);

    [StructLayout(LayoutKind.Sequential)]
    internal readonly struct Luid
    {
        internal readonly uint LowPart;
        internal readonly int HighPart;

        public override string ToString() => $"{HighPart:x8}{LowPart:x8}";
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigRational
    {
        internal uint Numerator;
        internal uint Denominator;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigPathSourceInfo
    {
        internal Luid AdapterId;
        internal uint Id;
        internal uint ModeInfoIndex;
        internal uint StatusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigPathTargetInfo
    {
        internal Luid AdapterId;
        internal uint Id;
        internal uint ModeInfoIndex;
        internal DisplayConfigVideoOutputTechnology OutputTechnology;
        internal DisplayConfigRotation Rotation;
        internal DisplayConfigScaling Scaling;
        internal DisplayConfigRational RefreshRate;
        internal DisplayConfigScanLineOrdering ScanLineOrdering;

        [MarshalAs(UnmanagedType.Bool)]
        internal bool TargetAvailable;

        internal uint StatusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigPathInfo
    {
        internal DisplayConfigPathSourceInfo SourceInfo;
        internal DisplayConfigPathTargetInfo TargetInfo;
        internal uint Flags;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigModeInfo
    {
        internal DisplayConfigModeInfoType InfoType;
        internal uint Id;
        internal Luid AdapterId;
        internal DisplayConfigModeInfoUnion ModeInfo;
    }

    [StructLayout(LayoutKind.Explicit)]
    internal struct DisplayConfigModeInfoUnion
    {
        [FieldOffset(0)]
        internal DisplayConfigTargetMode TargetMode;

        [FieldOffset(0)]
        internal DisplayConfigSourceMode SourceMode;

        [FieldOffset(0)]
        internal DisplayConfigDesktopImageInfo DesktopImageInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigTargetMode
    {
        internal DisplayConfigVideoSignalInfo TargetVideoSignalInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigVideoSignalInfo
    {
        internal ulong PixelRate;
        internal DisplayConfigRational HorizontalSyncFrequency;
        internal DisplayConfigRational VerticalSyncFrequency;
        internal DisplayConfigRegion ActiveSize;
        internal DisplayConfigRegion TotalSize;
        internal uint VideoStandard;
        internal DisplayConfigScanLineOrdering ScanLineOrdering;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigRegion
    {
        internal uint Width;
        internal uint Height;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigSourceMode
    {
        internal uint Width;
        internal uint Height;
        internal DisplayConfigPixelFormat PixelFormat;
        internal NativePoint Position;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigDesktopImageInfo
    {
        internal NativePoint PathSourceSize;
        internal NativeRect DesktopImageRegion;
        internal NativeRect DesktopImageClip;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct NativePoint
    {
        internal int X;
        internal int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct NativeRect
    {
        internal int Left;
        internal int Top;
        internal int Right;
        internal int Bottom;

        internal PhysicalRect ToPhysicalRect() =>
            new(Left, Top, (long)Right - Left, (long)Bottom - Top);
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct DisplayConfigDeviceInfoHeader
    {
        internal DisplayConfigDeviceInfoType Type;
        internal uint Size;
        internal Luid AdapterId;
        internal uint Id;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct DisplayConfigSourceDeviceName
    {
        internal DisplayConfigDeviceInfoHeader Header;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        internal string ViewGdiDeviceName;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct DisplayConfigTargetDeviceName
    {
        internal DisplayConfigDeviceInfoHeader Header;
        internal uint Flags;
        internal DisplayConfigVideoOutputTechnology OutputTechnology;
        internal ushort EdidManufactureId;
        internal ushort EdidProductCodeId;
        internal uint ConnectorInstance;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
        internal string MonitorFriendlyDeviceName;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        internal string MonitorDevicePath;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct MonitorInfoEx
    {
        internal uint Size;
        internal NativeRect Monitor;
        internal NativeRect WorkArea;
        internal uint Flags;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        internal string DeviceName;
    }

    internal enum DisplayConfigDeviceInfoType : uint
    {
        GetSourceName = 1,
        GetTargetName = 2,
    }

    internal enum DisplayConfigModeInfoType : uint
    {
        Source = 1,
        Target = 2,
        DesktopImage = 3,
    }

    internal enum DisplayConfigVideoOutputTechnology : uint
    {
        Other = 0xFFFFFFFF,
        Hd15 = 0,
        SVideo = 1,
        CompositeVideo = 2,
        ComponentVideo = 3,
        Dvi = 4,
        Hdmi = 5,
        Lvds = 6,
        DJpn = 8,
        Sdi = 9,
        DisplayPortExternal = 10,
        DisplayPortEmbedded = 11,
        UdiExternal = 12,
        UdiEmbedded = 13,
        SdTvDongle = 14,
        Miracast = 15,
        IndirectWired = 16,
        Internal = 0x80000000,
    }

    internal enum DisplayConfigRotation : uint
    {
        Identity = 1,
        Rotate90 = 2,
        Rotate180 = 3,
        Rotate270 = 4,
    }

    internal enum DisplayConfigScaling : uint
    {
        Identity = 1,
        Centered = 2,
        Stretched = 3,
        AspectRatioCenteredMax = 4,
        Custom = 5,
        Preferred = 128,
    }

    internal enum DisplayConfigScanLineOrdering : uint
    {
        Unspecified = 0,
        Progressive = 1,
        Interlaced = 2,
        InterlacedUpperFieldFirst = 2,
        InterlacedLowerFieldFirst = 3,
    }

    internal enum DisplayConfigPixelFormat : uint
    {
        PixelFormat8Bpp = 1,
        PixelFormat16Bpp = 2,
        PixelFormat24Bpp = 3,
        PixelFormat32Bpp = 4,
        PixelFormatNongdi = 5,
    }
}
