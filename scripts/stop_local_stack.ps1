Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunDir = Join-Path $Root "data\run"
$LogDir = Join-Path $Root "data\logs"
$SupervisorLog = Join-Path $LogDir "local-stack-supervisor.log"

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

function Write-StackLog {
    param([string]$Message)
    $stamp = (Get-Date).ToString("s")
    Add-Content -LiteralPath $SupervisorLog -Value "$stamp $Message"
}

function Stop-ProcessFromPidFile {
    param([string]$Name)
    $pidPath = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path -LiteralPath $pidPath)) {
        return
    }
    $rawPid = (Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($rawPid -match "^\d+$") {
        $process = Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue
        if ($null -ne $process -and $process.Id -ne $PID) {
            Write-StackLog "stopping $Name pid=$($process.Id)"
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

foreach ($name in @("bridge", "maxapi", "parser", "supervisor")) {
    Stop-ProcessFromPidFile $name
}
