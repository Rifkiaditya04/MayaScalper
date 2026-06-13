$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "..\pradita\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m tsp.deploy `
    --mode live `
    --config (Join-Path $RepoRoot "deploy\configs\contest_safe.yaml") `
    --env-file (Join-Path $RepoRoot ".env")
