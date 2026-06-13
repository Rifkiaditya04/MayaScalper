param(
    [string]$ConfigPath = "deploy/v2/configs/forward_safe.yaml",
    [string]$EnvPath = ".env"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $RepoRoot "..\pradita\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m tsp_v2.run_v2 preflight --config $ConfigPath --env-file $EnvPath
