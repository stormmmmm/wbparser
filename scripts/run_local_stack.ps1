param(
    [string]$HostAddress = "127.0.0.1",
    [int]$ParserPort = 8000,
    [int]$MaxApiPort = 8080,
    [string]$DailyCycleAt = "04:00",
    [int]$PollSeconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunDir = Join-Path $Root "data\run"
$LogDir = Join-Path $Root "data\logs"
$SupervisorLog = Join-Path $LogDir "local-stack-supervisor.log"
$SchedulerLog = Join-Path $LogDir "scheduler.log"
$LastDailyCyclePath = Join-Path $RunDir "daily-cycle-last.txt"

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

function Write-StackLog {
    param([string]$Message)
    $stamp = (Get-Date).ToString("s")
    Add-Content -LiteralPath $SupervisorLog -Value "$stamp $Message"
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$") {
            continue
        }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Set-DefaultEnv {
    param([string]$Name, [string]$Value)
    $current = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($current)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Normalize-SqliteUrl {
    param([string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url) -or -not $Url.StartsWith("sqlite:///")) {
        return $Url
    }
    $pathPart = $Url.Substring("sqlite:///".Length)
    if ($pathPart -match "^[A-Za-z]:[/\\]") {
        return "sqlite:///$($pathPart.Replace('\', '/'))"
    }
    $absolute = (Join-Path $Root $pathPart)
    return "sqlite:///$($absolute.Replace('\', '/'))"
}

function Stop-ProcessFromPidFile {
    param([string]$Name)
    $pidPath = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path -LiteralPath $pidPath)) {
        return
    }
    $rawPid = (Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($rawPid -match "^\d+$") {
        $old = Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue
        if ($null -ne $old -and $old.Id -ne $PID) {
            Write-StackLog "stopping stale $Name pid=$($old.Id)"
            Stop-Process -Id $old.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

function Start-ManagedProcess {
    param(
        [hashtable]$Service
    )
    $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
    $stdout = Join-Path $LogDir "$($Service.Name)-$stamp.out.log"
    $stderr = Join-Path $LogDir "$($Service.Name)-$stamp.err.log"
    $process = Start-Process `
        -FilePath $Service.FilePath `
        -ArgumentList $Service.Arguments `
        -WorkingDirectory $Service.WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath (Join-Path $RunDir "$($Service.Name).pid") -Value $process.Id
    Write-StackLog "started $($Service.Name) pid=$($process.Id) stdout=$stdout stderr=$stderr"
    return $process
}

function Invoke-DailyCycleIfDue {
    $parts = $DailyCycleAt.Split(":")
    if ($parts.Count -ne 2) {
        throw "DailyCycleAt must be HH:mm"
    }
    $now = Get-Date
    $scheduled = Get-Date -Hour ([int]$parts[0]) -Minute ([int]$parts[1]) -Second 0
    if ($now -lt $scheduled) {
        return
    }
    $today = $now.ToString("yyyy-MM-dd")
    $lastRun = ""
    if (Test-Path -LiteralPath $LastDailyCyclePath) {
        $lastRun = (Get-Content -LiteralPath $LastDailyCyclePath -ErrorAction SilentlyContinue | Select-Object -First 1)
    }
    if ($lastRun -eq $today) {
        return
    }
    if ($script:LastDailyAttemptAt -and (($now - $script:LastDailyAttemptAt).TotalMinutes -lt 15)) {
        return
    }
    $script:LastDailyAttemptAt = $now
    try {
        $uri = "http://${HostAddress}:$ParserPort/api/v1/admin/daily-cycle"
        $response = Invoke-WebRequest -Method Post -Uri $uri -TimeoutSec 600 -UseBasicParsing
        Set-Content -LiteralPath $LastDailyCyclePath -Value $today
        Add-Content -LiteralPath $SchedulerLog -Value "$($now.ToString("s")) daily-cycle ok status=$($response.StatusCode)"
    }
    catch {
        Add-Content -LiteralPath $SchedulerLog -Value "$($now.ToString("s")) daily-cycle failed: $($_.Exception.Message)"
    }
}

Import-DotEnv (Join-Path $Root ".env")
$databaseUrl = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
if (-not [string]::IsNullOrWhiteSpace($databaseUrl)) {
    [Environment]::SetEnvironmentVariable(
        "DATABASE_URL",
        (Normalize-SqliteUrl $databaseUrl),
        "Process"
    )
}
Set-DefaultEnv "MAXAPI_BACKEND" "pymax"
Set-DefaultEnv "MAXAPI_TOKEN" "dev-token"
Set-DefaultEnv "MAXAPI_HOST" $HostAddress
Set-DefaultEnv "MAXAPI_PORT" ([string]$MaxApiPort)
Set-DefaultEnv "MAXAPI_PYMAX_WORK_DIR" (Join-Path $Root "data\maxapi")
Set-DefaultEnv "WBBRIDGE_PARSER_BASE_URL" "http://${HostAddress}:$ParserPort"
Set-DefaultEnv "WBBRIDGE_MAXAPI_BASE_URL" "http://${HostAddress}:$MaxApiPort"
Set-DefaultEnv "WBBRIDGE_MAXAPI_TOKEN" ([Environment]::GetEnvironmentVariable("MAXAPI_TOKEN", "Process"))
Set-DefaultEnv "WBBRIDGE_ADMIN_STATE_PATH" (Join-Path $Root "data\admin_state.json")
Set-DefaultEnv "WBBRIDGE_BATCH_SIZE" "1"
Set-DefaultEnv "WBBRIDGE_POLL_INTERVAL_SECONDS" "15"
Set-DefaultEnv "WBBRIDGE_PUBLISH_UNPLANNED_POSTS" "false"
Set-DefaultEnv "WBBRIDGE_WORKER_ID" "wb-bridge-local"

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Bridge = Join-Path $Root ".venv\Scripts\wb-bridge.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python venv not found at $Python"
}
if (-not (Test-Path -LiteralPath $Bridge)) {
    throw "wb-bridge entrypoint not found at $Bridge"
}

$oldSupervisorPath = Join-Path $RunDir "supervisor.pid"
if (Test-Path -LiteralPath $oldSupervisorPath) {
    $oldSupervisorPid = (Get-Content -LiteralPath $oldSupervisorPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldSupervisorPid -match "^\d+$") {
        $oldSupervisor = Get-Process -Id ([int]$oldSupervisorPid) -ErrorAction SilentlyContinue
        if ($null -ne $oldSupervisor -and $oldSupervisor.Id -ne $PID) {
            Write-StackLog "stopping old supervisor pid=$($oldSupervisor.Id)"
            Stop-Process -Id $oldSupervisor.Id -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    }
}

Set-Content -LiteralPath $oldSupervisorPath -Value $PID
foreach ($name in @("bridge", "maxapi", "parser")) {
    Stop-ProcessFromPidFile $name
}

$services = @(
    @{
        Name = "parser"
        FilePath = $Python
        Arguments = @("-m", "uvicorn", "app.api.main:app", "--host", $HostAddress, "--port", ([string]$ParserPort))
        WorkingDirectory = Join-Path $Root "parser"
    },
    @{
        Name = "maxapi"
        FilePath = $Python
        Arguments = @("-m", "uvicorn", "api.main:app", "--host", $HostAddress, "--port", ([string]$MaxApiPort))
        WorkingDirectory = Join-Path $Root "maxapi"
    },
    @{
        Name = "bridge"
        FilePath = $Bridge
        Arguments = @("run-loop")
        WorkingDirectory = Join-Path $Root "bridge"
    }
)

$processes = @{}
foreach ($service in $services) {
    $processes[$service.Name] = Start-ManagedProcess $service
}

Write-StackLog "local stack supervisor online root=$Root"
$script:LastDailyAttemptAt = $null

try {
    while ($true) {
        foreach ($service in $services) {
            $current = $processes[$service.Name]
            if ($null -eq $current -or $current.HasExited) {
                $exitCode = if ($null -eq $current) { "missing" } else { $current.ExitCode }
                Write-StackLog "$($service.Name) stopped exit=$exitCode; restarting"
                Start-Sleep -Seconds 3
                $processes[$service.Name] = Start-ManagedProcess $service
            }
        }
        Invoke-DailyCycleIfDue
        Start-Sleep -Seconds $PollSeconds
    }
}
finally {
    Write-StackLog "supervisor exiting"
}
