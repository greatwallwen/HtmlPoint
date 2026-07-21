$ErrorActionPreference = 'Stop'

$platformRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$webRoot = Join-Path $platformRoot 'web'
$helperRoot = Join-Path $platformRoot 'helper'
$dist = Join-Path $webRoot 'dist'
$appData = Join-Path $env:LOCALAPPDATA 'CourseStudio'
$sourceRoot = Join-Path $appData 'sources'
$database = Join-Path $appData 'knowledge.db'

New-Item -ItemType Directory -Force -Path $appData, $sourceRoot | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $dist '.vite\manifest.json'))) {
    & npm.cmd --prefix $webRoot run build
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Push-Location -LiteralPath $helperRoot
try {
    & python -m course_helper --database $database --app-data $appData --reference-root $sourceRoot --web-origin 'http://127.0.0.1:8765' --web-root $dist --port 8765
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
