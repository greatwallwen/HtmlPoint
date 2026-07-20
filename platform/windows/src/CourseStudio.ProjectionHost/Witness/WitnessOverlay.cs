using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using CourseStudio.ProjectionHost.Core;
using CourseStudio.ProjectionHost.Native;
using CourseStudio.ProjectionHost.Windows;

namespace CourseStudio.ProjectionHost.Witness;

internal sealed class WitnessOverlaySurface : IWitnessSurface
{
    private readonly List<WitnessOverlay> _overlays = [];
    private WitnessInputDialog? _dialog;

    public event Action<string, string>? CodesSubmitted;

    public event Action? Cancelled;

    public Task ShowAsync(
        string stageCode,
        string presenterCode,
        IReadOnlyList<RoleWindowEvidence> windows,
        DateTimeOffset expiresAt,
        CancellationToken cancellationToken) =>
        InvokeAsync(
            () =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                RoleWindowEvidence stage = windows.Single(window => window.Role == Role.Stage);
                RoleWindowEvidence presenter = windows.Single(window =>
                    window.Role == Role.Presenter);
                try
                {
                    WitnessOverlay stageOverlay = new(Role.Stage, stageCode, expiresAt);
                    ShowAt(stageOverlay, OverlayBounds(stage.TargetRect));
                    _overlays.Add(stageOverlay);

                    WitnessOverlay presenterOverlay = new(
                        Role.Presenter,
                        presenterCode,
                        expiresAt);
                    ShowAt(presenterOverlay, OverlayBounds(presenter.TargetRect));
                    _overlays.Add(presenterOverlay);

                    _dialog = new WitnessInputDialog(expiresAt);
                    _dialog.Submitted += (stageInput, presenterInput) =>
                        CodesSubmitted?.Invoke(stageInput, presenterInput);
                    _dialog.Cancelled += () => Cancelled?.Invoke();
                    ShowAt(_dialog, DialogBounds(presenter.TargetRect));
                    _dialog.Activate();
                    return true;
                }
                catch
                {
                    if (_dialog is not null)
                    {
                        _dialog.AllowProgrammaticClose();
                        _dialog.Close();
                        _dialog = null;
                    }

                    foreach (WitnessOverlay overlay in _overlays)
                    {
                        overlay.Close();
                    }

                    _overlays.Clear();
                    throw;
                }
            });

    public Task HideAsync(CancellationToken cancellationToken) =>
        InvokeAsync(
            () =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (_dialog is not null)
                {
                    _dialog.AllowProgrammaticClose();
                    _dialog.Close();
                    _dialog = null;
                }

                foreach (WitnessOverlay overlay in _overlays)
                {
                    overlay.Close();
                }

                _overlays.Clear();
                return true;
            });

    private static Dispatcher UiDispatcher =>
        Application.Current?.Dispatcher ?? Dispatcher.CurrentDispatcher;

    private static async Task<T> InvokeAsync<T>(Func<T> action) =>
        await UiDispatcher.InvokeAsync(action, DispatcherPriority.Send);

    private static void ShowAt(Window window, PhysicalRect bounds)
    {
        window.Show();
        nint handle = new WindowInteropHelper(window).Handle;
        if (handle == 0
            || !WindowNative.SetWindowPos(
                handle,
                0,
                checked((int)bounds.X),
                checked((int)bounds.Y),
                checked((int)bounds.Width),
                checked((int)bounds.Height),
                WindowNative.NoZOrder
                    | WindowNative.ShowWindow
                    | WindowNative.FrameChanged))
        {
            throw new Win32Exception("The witness window could not be positioned.");
        }
    }

    private static PhysicalRect OverlayBounds(PhysicalRect target) =>
        new(
            target.X + Math.Max(24, target.Width - 424),
            target.Y + 24,
            Math.Min(400, target.Width - 48),
            Math.Min(180, target.Height - 48));

    private static PhysicalRect DialogBounds(PhysicalRect target)
    {
        long width = Math.Min(520, target.Width - 48);
        long height = Math.Min(320, target.Height - 48);
        return new(
            target.X + (target.Width - width) / 2,
            target.Y + (target.Height - height) / 2,
            width,
            height);
    }
}

internal sealed class WitnessOverlay : Window
{
    internal WitnessOverlay(Role role, string code, DateTimeOffset expiresAt)
    {
        WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.NoResize;
        ShowActivated = false;
        ShowInTaskbar = false;
        Topmost = true;
        Focusable = false;
        IsHitTestVisible = false;
        Background = role == Role.Stage
            ? new SolidColorBrush(Color.FromRgb(226, 239, 255))
            : new SolidColorBrush(Color.FromRgb(224, 247, 237));
        Content = new Border
        {
            BorderBrush = role == Role.Stage
                ? new SolidColorBrush(Color.FromRgb(54, 111, 214))
                : new SolidColorBrush(Color.FromRgb(32, 137, 93)),
            BorderThickness = new Thickness(3),
            CornerRadius = new CornerRadius(18),
            Padding = new Thickness(24),
            Child = new StackPanel
            {
                Children =
                {
                    new TextBlock
                    {
                        Text = role == Role.Stage ? "Stage code" : "Presenter code",
                        FontSize = 18,
                        Foreground = new SolidColorBrush(Color.FromRgb(41, 52, 68)),
                    },
                    new TextBlock
                    {
                        Text = code,
                        FontSize = 42,
                        FontWeight = FontWeights.Bold,
                        FontFamily = new FontFamily("Consolas"),
                        Foreground = new SolidColorBrush(Color.FromRgb(20, 30, 45)),
                        Margin = new Thickness(0, 8, 0, 4),
                    },
                    new TextBlock
                    {
                        Text = $"Expires {expiresAt.ToLocalTime():HH:mm:ss}",
                        FontSize = 14,
                        Foreground = new SolidColorBrush(Color.FromRgb(74, 84, 98)),
                    },
                },
            },
        };
    }
}

internal sealed class WitnessInputDialog : Window
{
    private readonly TextBox _stageInput;
    private readonly TextBox _presenterInput;
    private bool _programmaticClose;

    internal WitnessInputDialog(DateTimeOffset expiresAt)
    {
        Title = "Confirm physical projection";
        WindowStyle = WindowStyle.SingleBorderWindow;
        ResizeMode = ResizeMode.NoResize;
        ShowInTaskbar = false;
        Topmost = true;
        Background = Brushes.White;
        _stageInput = CodeInput();
        _presenterInput = CodeInput();
        Button confirm = new()
        {
            Content = "Confirm",
            MinWidth = 120,
            MinHeight = 44,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 20, 0, 0),
            IsDefault = true,
        };
        confirm.Click += (_, _) =>
        {
            string stage = _stageInput.Text;
            string presenter = _presenterInput.Text;
            _stageInput.Clear();
            _presenterInput.Clear();
            Submitted?.Invoke(stage, presenter);
        };

        Content = new Border
        {
            Padding = new Thickness(28),
            Child = new StackPanel
            {
                Children =
                {
                    new TextBlock
                    {
                        Text = "Confirm both physical screens",
                        FontSize = 24,
                        FontWeight = FontWeights.SemiBold,
                        Foreground = new SolidColorBrush(Color.FromRgb(28, 43, 62)),
                    },
                    new TextBlock
                    {
                        Text = $"Enter both six-character codes before {expiresAt.ToLocalTime():HH:mm:ss}.",
                        FontSize = 14,
                        Margin = new Thickness(0, 6, 0, 18),
                        Foreground = new SolidColorBrush(Color.FromRgb(78, 89, 104)),
                    },
                    Labelled("Stage code", _stageInput),
                    Labelled("Presenter code", _presenterInput),
                    confirm,
                },
            },
        };
    }

    internal event Action<string, string>? Submitted;

    internal event Action? Cancelled;

    internal void AllowProgrammaticClose() => _programmaticClose = true;

    protected override void OnClosing(CancelEventArgs eventArgs)
    {
        _stageInput.Clear();
        _presenterInput.Clear();
        if (!_programmaticClose)
        {
            Cancelled?.Invoke();
        }

        base.OnClosing(eventArgs);
    }

    private static TextBox CodeInput() =>
        new()
        {
            MaxLength = 6,
            CharacterCasing = CharacterCasing.Upper,
            FontFamily = new FontFamily("Consolas"),
            FontSize = 24,
            MinHeight = 44,
            VerticalContentAlignment = VerticalAlignment.Center,
        };

    private static FrameworkElement Labelled(string label, TextBox input) =>
        new StackPanel
        {
            Margin = new Thickness(0, 0, 0, 12),
            Children =
            {
                new TextBlock
                {
                    Text = label,
                    FontSize = 14,
                    Margin = new Thickness(0, 0, 0, 4),
                },
                input,
            },
        };
}
