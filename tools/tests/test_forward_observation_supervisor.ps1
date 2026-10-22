param(
    [string]$PowerShellPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Supervisor = Join-Path $RepoRoot "tools\run_forward_observation.ps1"
$Stub = Join-Path $PSScriptRoot "forward_observation_stub.cmd"
$TemporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("tsp_v2_supervisor_test_" + [guid]::NewGuid().ToString("N"))

function Assert-Contains([string]$Content, [string]$Expected, [string]$Label) {
    if ($Content -notlike "*$Expected*") {
        throw "$Label did not contain '$Expected'."
    }
}

function Invoke-Scenario([string]$Mode, [int]$ExpectedExitCode, [string[]]$ExpectedLogEntries) {
    $ScenarioRoot = Join-Path $TemporaryRoot $Mode
    New-Item -ItemType Directory -Force -Path $ScenarioRoot | Out-Null
    $LogPath = Join-Path $ScenarioRoot "supervisor.log"
    $ChildLogPath = Join-Path $ScenarioRoot "child.log"
    $StopFile = Join-Path $ScenarioRoot "STOP"
    $env:TSP_SUPERVISOR_STUB_MODE = $Mode
    $env:TSP_SUPERVISOR_STUB_STATE_FILE = Join-Path $ScenarioRoot "stub-state.txt"

    & $PowerShellPath -NoProfile -ExecutionPolicy Bypass -File $Supervisor `
        -PythonPath $Stub `
        -ChunkCycles 1 `
        -MaxChunks 1 `
        -RetryDelaySeconds 1 `
        -ConsecutiveFailureLimit 2 `
        -StopFile $StopFile `
        -LogPath $LogPath `
        -ChildLogPath $ChildLogPath
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne $ExpectedExitCode) {
        throw "$Mode expected exit code $ExpectedExitCode but received $ExitCode."
    }

    $LogContent = Get-Content -LiteralPath $LogPath -Raw
    foreach ($Entry in $ExpectedLogEntries) {
        Assert-Contains $LogContent $Entry $Mode
    }
}

try {
    Invoke-Scenario "failure" 3 @(
        "OBSERVATION_CHUNK_STOP chunk=1 exit_code=3",
        "OBSERVATION_RETRY_WAIT seconds=1 consecutive_failures=1",
        "OBSERVATION_ABORTED consecutive_failures=2 limit=2"
    )
    Invoke-Scenario "success" 0 @("OBSERVATION_COMPLETED successful_chunks=1")
    Invoke-Scenario "fail_once" 0 @(
        "OBSERVATION_RETRY_WAIT seconds=1 consecutive_failures=1",
        "OBSERVATION_COMPLETED successful_chunks=1"
    )
    Write-Output "Forward observation supervisor stub tests passed."
}
finally {
    Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:TSP_SUPERVISOR_STUB_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:TSP_SUPERVISOR_STUB_STATE_FILE -ErrorAction SilentlyContinue
}
