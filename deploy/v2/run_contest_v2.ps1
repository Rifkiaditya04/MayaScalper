param(
    [string]$ConfigPath = "deploy/v2/configs/contest_balanced.yaml",
    [string]$EnvPath = ".env"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $RepoRoot "..\pradita\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m tsp_v2.run_v2 start --config $ConfigPath --env-file $EnvPath --dry-run
