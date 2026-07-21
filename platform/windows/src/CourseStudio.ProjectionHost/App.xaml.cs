using System.Windows;
using System.IO;
using System.Security.Cryptography;
using CourseStudio.ProjectionHost.Transport;

namespace CourseStudio.ProjectionHost;

public partial class App : Application
{
    private CancellationTokenSource? _shutdown;
    private Task<int>? _hostRun;

    protected override void OnStartup(StartupEventArgs eventArgs)
    {
        base.OnStartup(eventArgs);
        ShutdownMode = ShutdownMode.OnExplicitShutdown;
        _shutdown = new CancellationTokenSource();
        _hostRun = Task.Run(() => RunHostAsync(_shutdown.Token));
        ObserveHostRunAsync(_hostRun);
    }

    protected override void OnExit(ExitEventArgs eventArgs)
    {
        _shutdown?.Cancel();
        _shutdown?.Dispose();
        _shutdown = null;
        base.OnExit(eventArgs);
    }

    private async void ObserveHostRunAsync(Task<int> run)
    {
        int exitCode;
        try
        {
            exitCode = await run;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(
                $"projection-host: unexpected termination:{exception.GetType().Name}");
            exitCode = 1;
        }

        Shutdown(exitCode);
    }

    private static async Task<int> RunHostAsync(CancellationToken cancellationToken)
    {
        string runRoot = ProjectionRunRoot();
        try
        {
            NativeProjectionCommandExecutor executor = new(runRoot);
            ProjectionHostProtocolProcessor processor = new(
                executor,
                () => new ProjectionAssetStagingStore(
                    Path.Combine(runRoot, Guid.NewGuid().ToString("N"))));
            ProjectionHostProtocolServer server = new(
                Console.OpenStandardInput(),
                Console.OpenStandardOutput(),
                processor);
            await server.RunAsync(cancellationToken);
            return 0;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return 0;
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or ProjectionTransportException
                or InvalidOperationException
                or CryptographicException)
        {
            Console.Error.WriteLine(
                $"projection-host: protocol terminated:{exception.GetType().Name}");
            return 1;
        }
        finally
        {
            await CleanupRunRootAsync(runRoot);
        }
    }

    private static async Task CleanupRunRootAsync(string runRoot)
    {
        for (int attempt = 0; attempt < 15; attempt++)
        {
            try
            {
                if (!Directory.Exists(runRoot))
                {
                    return;
                }

                if (File.GetAttributes(runRoot).HasFlag(FileAttributes.ReparsePoint))
                {
                    Console.Error.WriteLine("projection-host: temporary cleanup rejected");
                    return;
                }

                Directory.Delete(runRoot, recursive: true);
                return;
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException)
            {
                if (attempt == 14)
                {
                    Console.Error.WriteLine("projection-host: temporary cleanup deferred");
                    return;
                }

                await Task.Delay(100);
            }
        }
    }

    private static string ProjectionRunRoot()
    {
        string? supplied = Environment.GetEnvironmentVariable(
            "COURSE_PROJECTION_RUN_ROOT");
        if (string.IsNullOrWhiteSpace(supplied))
        {
            throw new InvalidOperationException("Projection run root is missing.");
        }

        string requested = Path.GetFullPath(supplied);
        string expectedParent = Path.GetFullPath(Path.Combine(
            Path.GetTempPath(),
            "CourseStudio.ProjectionHost"));
        DirectoryInfo directory = new(requested);
        if (!directory.Exists
            || directory.Attributes.HasFlag(FileAttributes.ReparsePoint)
            || !string.Equals(
                directory.Parent?.FullName,
                expectedParent,
                StringComparison.OrdinalIgnoreCase)
            || !directory.Name.StartsWith("run-", StringComparison.Ordinal)
            || directory.Name.Length is < 20 or > 64)
        {
            throw new InvalidOperationException("Projection run root is invalid.");
        }

        return requested;
    }
}
