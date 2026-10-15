[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DistributionPath,
    [Parameter(Mandatory)][string]$FixturePath,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$codexPath = (Get-Command codex -ErrorAction Stop).Source
if (-not $OutputPath) {
    $outputPath = Join-Path (Split-Path -Parent $repoRoot) ('hve-core-codex-artifacts\edge-cli-live-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$scenarioPath = Join-Path $repoRoot 'tests\codex-host\edge.json'
$scenarioCount = @((Get-Content -LiteralPath $scenarioPath -Raw | ConvertFrom-Json).scenarios).Count

foreach ($requiredPath in @($pythonPath, $distributionPath, $fixturePath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required path does not exist: $requiredPath"
    }
}

# Use normal official CLI setup and trust. Never disable its sandbox or approvals.
function Assert-DesktopRuntimeAvailable {
    $runtimeProcesses = @(Get-Process -Name node_repl -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like '*\OpenAI\Codex\runtimes\cua_node\*\bin\node_repl.exe'
    })
    if ($runtimeProcesses.Count -gt 0) {
        $processIds = ($runtimeProcesses.Id -join ', ')
        throw "The known conflicting Codex runtime is still running (PIDs: $processIds). Exit its owning Codex sessions before retrying. No paid tests were started."
    }
}

Assert-DesktopRuntimeAvailable
& $codexPath --version
& $codexPath sandbox --include-managed-config --permission-profile ':workspace' -C $fixturePath -- powershell.exe -NoProfile -Command 'Get-Content src/app.py'
if ($LASTEXITCODE -ne 0) {
    throw 'Official CLI sandbox readiness failed. No model tests were started.'
}
Assert-DesktopRuntimeAvailable

Write-Host "Running the full declared host suite ($scenarioCount cases) with Luna Max. This uses paid model access."
Write-Host "Results: $outputPath"
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$consoleLog = Join-Path $outputPath 'runner-console.log'
& $pythonPath -m hve_codex_porter.cli host-test run --distribution $distributionPath --scenarios $scenarioPath --output $outputPath --codex-command $codexPath --model gpt-6-luna --reasoning-effort max --max-sessions $scenarioCount --timeout-seconds 3600 --isolate-computer-use --json *> $consoleLog
$runnerExitCode = $LASTEXITCODE
Write-Host "Full console output: $consoleLog"
Write-Host "Structured report: $(Join-Path $outputPath 'evaluations-report.json')"
if ($runnerExitCode -ne 0) {
    throw "Acceptance did not fully pass. Preserve and inspect results at $outputPath."
}
