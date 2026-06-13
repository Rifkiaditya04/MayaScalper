$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "..\pradita\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m tests.harness all --json
& $Python -m tsp.deploy `
    --mode forward_test `
    --config (Join-Path $RepoRoot "deploy\configs\forward_test.yaml") `
    --env-file (Join-Path $RepoRoot ".env") `
    --dry-run
