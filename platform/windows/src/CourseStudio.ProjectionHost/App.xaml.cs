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
        catch
        {
            Console.Error.WriteLine("projection-host: unexpected termination");
            exitCode = 1;
        }

        Shutdown(exitCode);
    }

    private static async Task<int> RunHostAsync(CancellationToken cancellationToken)
    {
        string runRoot = Path.Combine(
            Path.GetTempPath(),
            "CourseStudio.ProjectionHost",
            Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(runRoot);
        try
        {
            NativeProjectionCommandExecutor executor = new();
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
            Console.Error.WriteLine("projection-host: protocol terminated");
            return 1;
        }
        finally
        {
            try
            {
                if (Directory.Exists(runRoot)
                    && !File.GetAttributes(runRoot).HasFlag(FileAttributes.ReparsePoint))
                {
                    Directory.Delete(runRoot, recursive: true);
                }
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException)
            {
                Console.Error.WriteLine("projection-host: temporary cleanup deferred");
            }
        }
    }
}
