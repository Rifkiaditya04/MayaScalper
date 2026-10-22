param(
    [string]$ConfigPath = "deploy/v2/configs/forward_demo.yaml",
    [string]$EnvPath = ".env",
    [string]$PythonPath = "",
    [int]$ChunkCycles = 50,
    [int]$MaxChunks = 100,
    [int]$RetryDelaySeconds = 30,
    [int]$ConsecutiveFailureLimit = 5,
    [string]$StopFile = "runtime/forward_demo/STOP",
    [string]$LogPath = "logs/forward_demo/forward_observation_supervisor.log",
    [string]$ChildLogPath = "logs/forward_demo/forward_observation_child.log"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $Candidate = Join-Path (Split-Path -Parent $RepoRoot) "pradita\Scripts\python.exe"
    if (-not (Test-Path $Candidate)) {
        throw "No repository MT5 interpreter found at $Candidate. Supply -PythonPath explicitly."
    }
    $PythonPath = $Candidate
}
if ($ChunkCycles -le 0 -or $MaxChunks -le 0 -or $ConsecutiveFailureLimit -le 0) {
    throw "ChunkCycles, MaxChunks, and ConsecutiveFailureLimit must be positive."
}

$LogDirectory = Split-Path -Parent $LogPath
if ($LogDirectory) { New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null }
$ChildLogDirectory = Split-Path -Parent $ChildLogPath
if ($ChildLogDirectory) { New-Item -ItemType Directory -Force -Path $ChildLogDirectory | Out-Null }

function Write-SupervisorLog([string]$Message) {
    $Line = "$((Get-Date).ToUniversalTime().ToString('o')) $Message"
    Write-Host $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

function Stop-Requested {
    return Test-Path -LiteralPath $StopFile
}

function Invoke-ObservedChild([string]$Phase, [string[]]$Arguments) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $ChildOutput = & $PythonPath @Arguments 2>&1
        $ChildExitCode = $global:LASTEXITCODE
        $ChildOutput |
            ForEach-Object { $_.ToString() } |
            Tee-Object -FilePath $ChildLogPath -Append |
            ForEach-Object { Write-SupervisorLog "OBSERVATION_CHILD phase=$Phase output=$_" }
        return $ChildExitCode
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

if (Stop-Requested) {
    Write-SupervisorLog "OBSERVATION_STOPPED stop_file_exists=$StopFile"
    exit 0
}

Write-SupervisorLog "OBSERVATION_PREFLIGHT_START config=$ConfigPath env=$EnvPath python=$PythonPath"
$PreflightExitCode = Invoke-ObservedChild "preflight" @("-m", "tsp_v2.run_v2", "preflight", "--live", "--config", $ConfigPath, "--env-file", $EnvPath)
if ($PreflightExitCode -ne 0) {
    Write-SupervisorLog "OBSERVATION_PREFLIGHT_FAILED exit_code=$PreflightExitCode"
    exit $PreflightExitCode
}
Write-SupervisorLog "OBSERVATION_PREFLIGHT_OK"

$Failures = 0
$SuccessfulChunks = 0
$LastFailureCode = 0
while ($SuccessfulChunks -lt $MaxChunks) {
    $Chunk = $SuccessfulChunks + 1
    if (Stop-Requested) {
        Write-SupervisorLog "OBSERVATION_STOPPED chunk=$Chunk stop_file=$StopFile"
        exit 0
    }

    Write-SupervisorLog "OBSERVATION_CHUNK_START chunk=$Chunk max_cycles=$ChunkCycles"
    $ExitCode = Invoke-ObservedChild "chunk=$Chunk" @("-m", "tsp_v2.run_v2", "start", "--config", $ConfigPath, "--env-file", $EnvPath, "--max-cycles", $ChunkCycles)
    Write-SupervisorLog "OBSERVATION_CHUNK_STOP chunk=$Chunk exit_code=$ExitCode"

    if ($ExitCode -eq 0) {
        $Failures = 0
        $LastFailureCode = 0
        $SuccessfulChunks++
        continue
    }

    $Failures++
    $LastFailureCode = $ExitCode
    if ($Failures -ge $ConsecutiveFailureLimit) {
        Write-SupervisorLog "OBSERVATION_ABORTED consecutive_failures=$Failures limit=$ConsecutiveFailureLimit"
        exit $ExitCode
    }
    Write-SupervisorLog "OBSERVATION_RETRY_WAIT seconds=$RetryDelaySeconds consecutive_failures=$Failures"
    Start-Sleep -Seconds $RetryDelaySeconds
}

if ($LastFailureCode -ne 0) {
    Write-SupervisorLog "OBSERVATION_FAILED exit_code=$LastFailureCode"
    exit $LastFailureCode
}
Write-SupervisorLog "OBSERVATION_COMPLETED successful_chunks=$SuccessfulChunks"
