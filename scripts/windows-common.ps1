# Shared helpers; compatible with Windows PowerShell 5.1 and PowerShell 7.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:PythonExe = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
$script:RuntimeDir = Join-Path $script:ProjectRoot '.runtime'

function Assert-Windows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'These scripts require Windows. On Linux use scripts/start-presentation.sh.'
    }
}

function Invoke-Checked {
    param([string]$Program, [string[]]$CommandArgs)
    & $Program @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}

function Import-LocalEnv {
    $path = Join-Path $script:ProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $path)) { throw 'Missing .env. Run scripts/install.ps1 first.' }
    $lineNumber = 0
    foreach ($line in [IO.File]::ReadAllLines($path)) {
        $lineNumber++
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith('#')) { continue }
        if ($text -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            throw "Invalid .env syntax on line $lineNumber. Use KEY=value; no shell commands."
        }
        $key = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        # Treat values as data. Never Invoke-Expression or expand $()/backticks.
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
    $env:PYTHONUTF8 = '1'
}

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install Docker Desktop and reopen PowerShell.'
    }
    $dockerOS = & docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or $dockerOS -ne 'linux') { throw 'Docker Desktop must use Linux containers.' }
    Invoke-Checked docker @('compose', 'version')
}

function Get-ProcessRecordPath {
    param([string]$Service)
    return Join-Path $script:RuntimeDir ("windows-$Service.json")
}

function Get-OwnedService {
    param([string]$Service)
    $path = Get-ProcessRecordPath $Service
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $record = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$record.process_id)
    if ($null -eq $proc) { return $null }
    $marker = "testpilot.$Service.api:app"
    if ($null -eq $proc.CommandLine -or $null -eq $proc.ExecutablePath -or
        $proc.CommandLine.IndexOf($marker, [StringComparison]::Ordinal) -lt 0 -or
        $proc.CommandLine.IndexOf($script:ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        -not [string]::Equals($proc.ExecutablePath, $script:PythonExe, [StringComparison]::OrdinalIgnoreCase) -or
        $proc.CreationDate.ToUniversalTime().Ticks.ToString() -ne [string]$record.created_ticks) {
        throw "PID record for $Service does not match this project. No process was stopped. Check $path."
    }
    return $proc
}

function Stop-OwnedServices {
    foreach ($service in @('web', 'intelligence', 'impact')) {
        $proc = Get-OwnedService $service
        if ($null -ne $proc) {
            # A Windows venv launcher may own a child Python process. Stop its tree.
            Invoke-Checked taskkill @('/PID', [string]$proc.ProcessId, '/T', '/F')
            Wait-Process -Id $proc.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
            Write-Host "Stopped: $service"
        }
        $path = Get-ProcessRecordPath $service
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
}

function Assert-FreePort {
    param([int]$Port)
    $listener = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq $Port }
    if ($listener) { throw "Port $Port is occupied. Stop the conflicting application and retry." }
}
