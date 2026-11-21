# Shared helpers; compatible with Windows PowerShell 5.1 and PowerShell 7.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
# Каталог данных (настройки .env, runtime-логи, pid-записи) может быть вне каталога программы:
# пакет неизменяем, а Program Files недоступен для записи без администратора.
# Для пакетной установки C2 задаёт TP_DATA_DIR; в checkout по умолчанию корень проекта.
$script:DataDir = if ($env:TP_DATA_DIR) { [IO.Path]::GetFullPath($env:TP_DATA_DIR) } else { $script:ProjectRoot }
$script:EnvFile = Join-Path $script:DataDir '.env'
$script:RuntimeDir = Join-Path $script:DataDir '.runtime'
# Окружение (venv) тоже может жить вне каталога программы: пакет неизменяем.
$script:VenvDir = if ($env:TP_VENV) { [IO.Path]::GetFullPath($env:TP_VENV) } elseif ($env:TP_DATA_DIR) { Join-Path $script:DataDir '.venv' } else { Join-Path $script:ProjectRoot '.venv' }
$script:PythonExe = if ($env:TP_PYTHON_EXE) { $env:TP_PYTHON_EXE } else { Join-Path $script:VenvDir 'Scripts\python.exe' }
# Пакет импортируется и из каталога только для чтения: установка в Program Files не требуется.
$separator = [IO.Path]::PathSeparator
if (-not $env:PYTHONPATH) {
    $env:PYTHONPATH = $script:ProjectRoot
} elseif ($env:PYTHONPATH.IndexOf($script:ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
    $env:PYTHONPATH = $script:ProjectRoot + $separator + $env:PYTHONPATH
}

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
    $path = $script:EnvFile
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing .env at $path. Run scripts/install.ps1 first, or set TP_DATA_DIR to the data directory." }
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

function Test-WritableDirectory {
    param([string]$Path)
    try {
        if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Path $Path -Force | Out-Null }
        $probe = Join-Path $Path ('.write-probe-' + [Guid]::NewGuid().ToString('N'))
        [IO.File]::WriteAllText($probe, 'ok')
        Remove-Item -LiteralPath $probe -Force
        return $true
    } catch {
        return $false
    }
}

function Assert-DataDirWritable {
    if (-not (Test-WritableDirectory $script:DataDir)) {
        throw "Каталог данных $script:DataDir недоступен для записи. Пакет неизменяем: задайте TP_DATA_DIR вне Program Files (например $env:LOCALAPPDATA\TestPilot1C) и повторите."
    }
}

function Get-ReceiptPath {
    return Join-Path $script:RuntimeDir 'start-receipt.json'
}

function Get-RunningComposeServices {
    # Только чтение. Чужие контейнеры и порты этот скрипт не останавливает.
    $services = & docker compose ps --status running --services 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Не удалось получить состояние Docker-стека этого проекта. Обновите Docker Compose (нужен v2 с `compose ps --status`).'
    }
    return @($services | Where-Object { $_ })
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
