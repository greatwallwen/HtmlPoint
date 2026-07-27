$ErrorActionPreference = 'Stop'

$platformRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$webRoot = Join-Path $platformRoot 'web'
$helperRoot = Join-Path $platformRoot 'helper'
$dist = Join-Path $webRoot 'dist'
$appData = Join-Path $env:LOCALAPPDATA 'CourseStudio'
$sourceRoot = Join-Path $appData 'sources'
$database = Join-Path $appData 'knowledge.db'

New-Item -ItemType Directory -Force -Path $appData, $sourceRoot | Out-Null

# --- Resolve Node.js toolchain (npm.cmd / node.exe) ---
# npm.cmd may not be on PATH when Node.js was extracted without installing.
$npmCmd = $null
$npmResolved = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($npmResolved -and (Test-Path -LiteralPath $npmResolved.Source)) {
    $npmCmd = $npmResolved.Source
} else {
    $nodeDirs = @('E:\nodejs\node-v22.18.0-win-x64', 'C:\Program Files\nodejs')
    foreach ($dir in $nodeDirs) {
        $candidate = Join-Path $dir 'npm.cmd'
        if (Test-Path -LiteralPath $candidate) {
            $npmCmd = $candidate
            $env:PATH = "$dir$([IO.Path]::PathSeparator)$env:PATH"
            break
        }
    }
}
if (-not $npmCmd) {
    throw "npm.cmd not found. Install Node.js (>=18) or add its directory to PATH."
}

# Install web dependencies on first run.
$nodeModules = Join-Path $webRoot 'node_modules'
if (-not (Test-Path -LiteralPath $nodeModules)) {
    Write-Host "Installing web dependencies (first run)..." -ForegroundColor Cyan
    & $npmCmd --prefix $webRoot install
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

# --- Resolve Python 3.12 (project requires >=3.12,<3.13) ---
# 'python' on PATH may resolve to Anaconda or another version; prefer the
# py launcher with an explicit -3.12 tag, then verify.
$pythonExe = $null
$pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
if ($pyLauncher) {
    try {
        $pyOut = & $pyLauncher -3.12 -c "import sys; print(sys.executable)" 2>&1
        $pyLine = ($pyOut | Where-Object { $_ -is [string] } | Select-Object -Last 1)
        if ($pyLine -and (Test-Path -LiteralPath $pyLine.Trim())) {
            $pythonExe = $pyLine.Trim()
        }
    } catch { }
}
if (-not $pythonExe) {
    # Fallback: check 'python' on PATH and confirm it is 3.12.
    $pyResolved = Get-Command python -ErrorAction SilentlyContinue
    if ($pyResolved -and (Test-Path -LiteralPath $pyResolved.Source)) {
        try {
            $verOut = & $pyResolved.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>&1
            $verLine = ($verOut | Where-Object { $_ -is [string] } | Select-Object -Last 1)
            if ($verLine -and $verLine.Trim() -eq '3.12') {
                $pythonExe = $pyResolved.Source
            }
        } catch { }
    }
}
if (-not $pythonExe) {
    throw "Python 3.12 not found. Install Python 3.12 (from python.org) or add it to PATH."
}

# Install helper dependencies on first run.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$depsOk = $false
try {
    & $pythonExe -c "import fastapi, uvicorn, duckdb, pydantic" 2>$null | Out-Null
    $depsOk = ($LASTEXITCODE -eq 0)
} catch { $depsOk = $false }
$ErrorActionPreference = $prevEAP
if (-not $depsOk) {
    Write-Host "Installing Python helper dependencies (first run)..." -ForegroundColor Cyan
    & $pythonExe -m pip install -e $helperRoot
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $dist '.vite\manifest.json'))) {
    & $npmCmd --prefix $webRoot run build
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Push-Location -LiteralPath $helperRoot
try {
    & $pythonExe -m course_helper --database $database --app-data $appData --reference-root $sourceRoot --web-origin 'http://127.0.0.1:8765' --web-root $dist --port 8765
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
