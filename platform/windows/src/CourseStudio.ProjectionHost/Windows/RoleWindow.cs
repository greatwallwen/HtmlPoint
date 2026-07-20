using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;

namespace CourseStudio.ProjectionHost.Windows;

internal sealed class RoleWindow : Window
{
    private bool _programmaticClose;
    private bool _closeReasonRaised;

    internal RoleWindow(Role role)
    {
        Role = role;
        Title = role == Role.Stage ? "Course Studio - Stage" : "Course Studio - Presenter";
        WindowStartupLocation = WindowStartupLocation.Manual;
        WindowStyle = WindowStyle.SingleBorderWindow;
        ResizeMode = ResizeMode.CanResize;
        ShowActivated = false;
        ShowInTaskbar = true;
        Topmost = true;
        MinWidth = 320;
        MinHeight = 180;
        Background = role == Role.Stage
            ? new SolidColorBrush(Color.FromRgb(235, 243, 255))
            : new SolidColorBrush(Color.FromRgb(237, 250, 244));
        Content = new Grid
        {
            Children =
            {
                new TextBlock
                {
                    Text = role == Role.Stage ? "Stage" : "Presenter",
                    FontSize = 32,
                    FontWeight = FontWeights.SemiBold,
                    Foreground = new SolidColorBrush(Color.FromRgb(28, 43, 62)),
                    HorizontalAlignment = HorizontalAlignment.Center,
                    VerticalAlignment = VerticalAlignment.Center,
                },
            },
        };
    }

    internal Role Role { get; }

    internal bool MonitoringEnabled { get; set; }

    internal event Action<RoleWindow, string>? Invalidated;

    internal void AllowProgrammaticClose()
    {
        _programmaticClose = true;
        MonitoringEnabled = false;
    }

    protected override void OnPreviewKeyDown(KeyEventArgs eventArgs)
    {
        if (eventArgs.Key == Key.Escape)
        {
            RaiseInvalidation("escape");
            eventArgs.Handled = true;
            Close();
            return;
        }

        base.OnPreviewKeyDown(eventArgs);
    }

    protected override void OnClosing(CancelEventArgs eventArgs)
    {
        if (!_programmaticClose)
        {
            RaiseInvalidation("user_close");
        }

        base.OnClosing(eventArgs);
    }

    protected override void OnLocationChanged(EventArgs eventArgs)
    {
        base.OnLocationChanged(eventArgs);
        if (MonitoringEnabled)
        {
            RaiseInvalidation("window_moved");
        }
    }

    protected override void OnStateChanged(EventArgs eventArgs)
    {
        base.OnStateChanged(eventArgs);
        if (MonitoringEnabled && WindowState == WindowState.Minimized)
        {
            RaiseInvalidation("window_minimized");
        }
    }

    protected override void OnDpiChanged(DpiScale oldDpi, DpiScale newDpi)
    {
        base.OnDpiChanged(oldDpi, newDpi);
        if (MonitoringEnabled
            && (oldDpi.DpiScaleX != newDpi.DpiScaleX
                || oldDpi.DpiScaleY != newDpi.DpiScaleY))
        {
            RaiseInvalidation("dpi_changed");
        }
    }

    private void RaiseInvalidation(string code)
    {
        if (_closeReasonRaised)
        {
            return;
        }

        _closeReasonRaised = true;
        Invalidated?.Invoke(this, code);
    }
}

internal sealed class NativeRoleWindowPlatform : IRoleWindowPlatform
{
    private readonly Dictionary<RoleWindow, PlatformRoleWindow> _windows = [];

    public event EventHandler<PlatformWindowInvalidatedEventArgs>? Invalidated;

    public Task<PlatformRoleWindow> CreateAsync(
        Role role,
        string displayId,
        PhysicalRect targetBounds,
        long generation,
        CancellationToken cancellationToken) =>
        InvokeAsync(
            () =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                RoleWindow window = new(role);
                window.Invalidated += OnWindowInvalidated;
                window.Show();
                nint handle = new WindowInteropHelper(window).Handle;
                if (handle == 0)
                {
                    window.AllowProgrammaticClose();
                    window.Close();
                    throw new Win32Exception("WPF did not create a native role window handle.");
                }

                NativeWindowState state = NativeWindowState.Capture(window, handle, targetBounds);
                MoveWindow(handle, targetBounds, frameChanged: false);
                string windowId = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(32));
                PlatformRoleWindow platformWindow = new(
                    role,
                    windowId,
                    displayId,
                    targetBounds,
                    generation,
                    true,
                    state);
                _windows[window] = platformWindow;
                return platformWindow;
            });

    public Task<PlatformRoleWindow> AssignAsync(
        PlatformRoleWindow window,
        string displayId,
        PhysicalRect targetBounds,
        long generation,
        CancellationToken cancellationToken) =>
        InvokeAsync(
            () =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                NativeWindowState state = NativeState(window);
                state.Window.MonitoringEnabled = false;
                state.TargetBounds = targetBounds;
                MoveWindow(state.Handle, targetBounds, frameChanged: true);
                PlatformRoleWindow assigned = window with
                {
                    DisplayId = displayId,
                    TargetBounds = targetBounds,
                    Generation = generation,
                };
                _windows[state.Window] = assigned;
                return assigned;
            });

    public Task EnterFullscreenAsync(
        PlatformRoleWindow window,
        CancellationToken cancellationToken) =>
        InvokeAsync(
            () =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                NativeWindowState state = NativeState(window);
                state.Window.MonitoringEnabled = false;
                nint currentStyle = WindowNative.GetWindowLongPtr(
                    state.Handle,
                    WindowNative.WindowLongStyle);
                long popupStyle =
                    (currentStyle.ToInt64() & ~WindowNative.OverlappedWindowStyle)
                    | WindowNative.PopupStyle;
                _ = WindowNative.SetWindowLongPtr(
                    state.Handle,
                    WindowNative.WindowLongStyle,
                    new nint(popupStyle));
                MoveWindow(state.Handle, state.TargetBounds, frameChanged: true);
                state.Window.MonitoringEnabled = true;
                return true;
            });

    public Task<RoleWindowEvidence> ReadEvidenceAsync(
        PlatformRoleWindow window,
        CancellationToken cancellationToken) =>
        InvokeAsync(
            () =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                NativeWindowState state = NativeState(window);
                if (!WindowNative.GetWindowRect(state.Handle, out WindowNative.NativeRect rect))
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }

                int frameError = WindowNative.DwmGetWindowAttribute(
                    state.Handle,
                    WindowNative.ExtendedFrameBoundsAttribute,
                    out WindowNative.NativeRect frame,
                    Marshal.SizeOf<WindowNative.NativeRect>());
                if (frameError != 0)
                {
                    throw new Win32Exception(frameError);
                }

                nint monitor = WindowNative.MonitorFromWindow(
                    state.Handle,
                    WindowNative.MonitorDefaultToNearest);
                WindowNative.MonitorInfo monitorInfo = new()
                {
                    Size = checked((uint)Marshal.SizeOf<WindowNative.MonitorInfo>()),
                };
                if (monitor == 0 || !WindowNative.GetMonitorInfo(monitor, ref monitorInfo))
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }

                int cloakedError = WindowNative.DwmGetWindowAttribute(
                    state.Handle,
                    WindowNative.CloakedAttribute,
                    out int cloaked,
                    sizeof(int));
                if (cloakedError != 0)
                {
                    throw new Win32Exception(cloakedError);
                }

                return new RoleWindowEvidence(
                    window.Role,
                    window.WindowId,
                    window.DisplayId,
                    window.Generation,
                    window.TargetBounds,
                    rect.ToPhysicalRect(),
                    frame.ToPhysicalRect(),
                    monitorInfo.Monitor.ToPhysicalRect(),
                    WindowNative.IsWindowVisible(state.Handle),
                    WindowNative.IsIconic(state.Handle),
                    cloaked != 0);
            });

    public Task RestoreAndCloseAsync(
        PlatformRoleWindow window,
        CancellationToken cancellationToken) =>
        InvokeAsync(
            () =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                NativeWindowState state = NativeState(window);
                state.Window.MonitoringEnabled = false;
                _ = WindowNative.SetWindowLongPtr(
                    state.Handle,
                    WindowNative.WindowLongStyle,
                    state.SavedStyle);
                WindowNative.WindowPlacement placement = state.SavedPlacement;
                _ = WindowNative.SetWindowPlacement(state.Handle, ref placement);
                _ = WindowNative.SetWindowPos(
                    state.Handle,
                    0,
                    0,
                    0,
                    0,
                    0,
                    WindowNative.NoMove
                        | WindowNative.NoSize
                        | WindowNative.NoZOrder
                        | WindowNative.NoActivate
                        | WindowNative.FrameChanged);
                state.Window.AllowProgrammaticClose();
                state.Window.Invalidated -= OnWindowInvalidated;
                state.Window.Close();
                _windows.Remove(state.Window);
                return true;
            });

    private static Dispatcher UiDispatcher =>
        Application.Current?.Dispatcher ?? Dispatcher.CurrentDispatcher;

    private static async Task<T> InvokeAsync<T>(Func<T> action) =>
        await UiDispatcher.InvokeAsync(action, DispatcherPriority.Send);

    private static void MoveWindow(
        nint handle,
        PhysicalRect bounds,
        bool frameChanged)
    {
        uint flags = WindowNative.NoZOrder
            | WindowNative.NoActivate
            | WindowNative.ShowWindow;
        if (frameChanged)
        {
            flags |= WindowNative.FrameChanged;
        }

        bool moved = WindowNative.SetWindowPos(
            handle,
            0,
            checked((int)bounds.X),
            checked((int)bounds.Y),
            checked((int)bounds.Width),
            checked((int)bounds.Height),
            flags);
        if (!moved)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    private static NativeWindowState NativeState(PlatformRoleWindow window) =>
        window.NativeToken as NativeWindowState
        ?? throw new InvalidOperationException("The role window token is invalid.");

    private void OnWindowInvalidated(RoleWindow window, string code)
    {
        if (_windows.TryGetValue(window, out PlatformRoleWindow? platformWindow))
        {
            Invalidated?.Invoke(
                this,
                new PlatformWindowInvalidatedEventArgs(platformWindow, code));
        }
    }

    private sealed class NativeWindowState
    {
        private NativeWindowState(
            RoleWindow window,
            nint handle,
            nint savedStyle,
            WindowNative.WindowPlacement savedPlacement,
            PhysicalRect targetBounds)
        {
            Window = window;
            Handle = handle;
            SavedStyle = savedStyle;
            SavedPlacement = savedPlacement;
            TargetBounds = targetBounds;
        }

        internal RoleWindow Window { get; }

        internal nint Handle { get; }

        internal nint SavedStyle { get; }

        internal WindowNative.WindowPlacement SavedPlacement { get; }

        internal PhysicalRect TargetBounds { get; set; }

        internal static NativeWindowState Capture(
            RoleWindow window,
            nint handle,
            PhysicalRect targetBounds)
        {
            nint style = WindowNative.GetWindowLongPtr(handle, WindowNative.WindowLongStyle);
            WindowNative.WindowPlacement placement = new()
            {
                Length = checked((uint)Marshal.SizeOf<WindowNative.WindowPlacement>()),
            };
            if (!WindowNative.GetWindowPlacement(handle, ref placement))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }

            return new NativeWindowState(window, handle, style, placement, targetBounds);
        }
    }
}

internal static class WindowNative
{
    internal const int WindowLongStyle = -16;
    internal const long PopupStyle = 0x80000000L;
    internal const long OverlappedWindowStyle = 0x00CF0000;
    internal const uint NoSize = 0x0001;
    internal const uint NoMove = 0x0002;
    internal const uint NoZOrder = 0x0004;
    internal const uint NoActivate = 0x0010;
    internal const uint FrameChanged = 0x0020;
    internal const uint ShowWindow = 0x0040;
    internal const uint MonitorDefaultToNearest = 0x00000002;
    internal const uint ExtendedFrameBoundsAttribute = 9;
    internal const uint CloakedAttribute = 14;

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    internal static extern nint GetWindowLongPtr(nint window, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    internal static extern nint SetWindowLongPtr(nint window, int index, nint value);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetWindowPlacement(
        nint window,
        ref WindowPlacement placement);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetWindowPlacement(
        nint window,
        ref WindowPlacement placement);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetWindowPos(
        nint window,
        nint insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetWindowRect(nint window, out NativeRect rectangle);

    [DllImport("user32.dll")]
    internal static extern nint MonitorFromWindow(nint window, uint flags);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetMonitorInfo(nint monitor, ref MonitorInfo info);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool IsWindowVisible(nint window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool IsIconic(nint window);

    [DllImport("dwmapi.dll")]
    internal static extern int DwmGetWindowAttribute(
        nint window,
        uint attribute,
        out NativeRect value,
        int size);

    [DllImport("dwmapi.dll")]
    internal static extern int DwmGetWindowAttribute(
        nint window,
        uint attribute,
        out int value,
        int size);

    [StructLayout(LayoutKind.Sequential)]
    internal struct WindowPlacement
    {
        internal uint Length;
        internal uint Flags;
        internal uint ShowCommand;
        internal NativePoint MinimumPosition;
        internal NativePoint MaximumPosition;
        internal NativeRect NormalPosition;
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
    internal struct MonitorInfo
    {
        internal uint Size;
        internal NativeRect Monitor;
        internal NativeRect WorkArea;
        internal uint Flags;
    }
}
