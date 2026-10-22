param(
    [string]$ConfigPath = "deploy/v2/configs/forward_demo.yaml",
    [string]$EnvPath = ".env",
    [string]$PythonPath = "",
    [int]$ChunkCycles = 50,
    [int]$MaxChunks = 100,
    [int]$RetryDelaySeconds = 30,
    [int]$ConsecutiveFailureLimit = 5,
    [string]$StopFile = "runtime/forward_demo/STOP",
    [string]$LogPath = "logs/forward_demo/forward_observation_supervisor.log"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $Candidate = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $PythonPath = if (Test-Path $Candidate) { $Candidate } else { "python" }
}
if ($ChunkCycles -le 0 -or $MaxChunks -le 0 -or $ConsecutiveFailureLimit -le 0) {
    throw "ChunkCycles, MaxChunks, and ConsecutiveFailureLimit must be positive."
}

$LogDirectory = Split-Path -Parent $LogPath
if ($LogDirectory) { New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null }

function Write-SupervisorLog([string]$Message) {
    $Line = "$((Get-Date).ToUniversalTime().ToString('o')) $Message"
    Write-Host $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

function Stop-Requested {
    return Test-Path -LiteralPath $StopFile
}

if (Stop-Requested) {
    Write-SupervisorLog "OBSERVATION_STOPPED stop_file_exists=$StopFile"
    exit 0
}

Write-SupervisorLog "OBSERVATION_PREFLIGHT_START config=$ConfigPath env=$EnvPath python=$PythonPath"
& $PythonPath -m tsp_v2.run_v2 preflight --config $ConfigPath --env-file $EnvPath
if ($LASTEXITCODE -ne 0) {
    Write-SupervisorLog "OBSERVATION_PREFLIGHT_FAILED exit_code=$LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-SupervisorLog "OBSERVATION_PREFLIGHT_OK"

$Failures = 0
for ($Chunk = 1; $Chunk -le $MaxChunks; $Chunk++) {
    if (Stop-Requested) {
        Write-SupervisorLog "OBSERVATION_STOPPED chunk=$Chunk stop_file=$StopFile"
        exit 0
    }

    Write-SupervisorLog "OBSERVATION_CHUNK_START chunk=$Chunk max_cycles=$ChunkCycles"
    & $PythonPath -m tsp_v2.run_v2 start --config $ConfigPath --env-file $EnvPath --max-cycles $ChunkCycles
    $ExitCode = $LASTEXITCODE
    Write-SupervisorLog "OBSERVATION_CHUNK_STOP chunk=$Chunk exit_code=$ExitCode"

    if ($ExitCode -eq 0) {
        $Failures = 0
        continue
    }

    $Failures++
    if ($Failures -ge $ConsecutiveFailureLimit) {
        Write-SupervisorLog "OBSERVATION_ABORTED consecutive_failures=$Failures limit=$ConsecutiveFailureLimit"
        exit $ExitCode
    }
    Write-SupervisorLog "OBSERVATION_RETRY_WAIT seconds=$RetryDelaySeconds consecutive_failures=$Failures"
    Start-Sleep -Seconds $RetryDelaySeconds
}

Write-SupervisorLog "OBSERVATION_COMPLETED max_chunks=$MaxChunks"
