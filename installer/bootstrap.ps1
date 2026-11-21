# Обёртка над installer/preflight.py для чистой установки из исходников.
#
# Ничего не устанавливает и не копирует: находит Python 3.12, запускает проверку
# и печатает, чего не хватает. Существующий .env не переносится и не изменяется.
#
#   powershell -ExecutionPolicy Bypass -File installer\bootstrap.ps1
#   powershell -ExecutionPolicy Bypass -File installer\bootstrap.ps1 -Prepare -DataDir "$env:LOCALAPPDATA\TestPilot1C"
#   powershell -ExecutionPolicy Bypass -File installer\bootstrap.ps1 -NoProbes

param(
    [string]$Root = '',
    [string]$DataDir = '',
    [switch]$Prepare,
    [switch]$NoProbes,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Write-Error 'Проверка установки из исходников рассчитана на Windows.'
    exit 1
}

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $DataDir) {
    $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:USERPROFILE }
    $DataDir = Join-Path $base 'TestPilot1C'
}

function Find-Python312 {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3.12 -c "import sys; assert sys.version_info[:2] >= (3, 12) and sys.maxsize > 2**32" 2>$null
        if ($LASTEXITCODE -eq 0) { return @('py', '-3.12') }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & python -c "import sys; assert sys.version_info[:2] >= (3, 12) and sys.maxsize > 2**32" 2>$null
        if ($LASTEXITCODE -eq 0) { return @('python') }
    }
    return $null
}

$interpreter = Find-Python312
if (-not $interpreter) {
    Write-Host 'НЕ ГОТОВО: не найден Python 3.12 x64.' -ForegroundColor Yellow
    Write-Host 'Установите Python 3.12 x64 вручную (python.org) и повторите проверку.'
    Write-Host 'Этот скрипт ничего не устанавливает и не скачивает.'
    exit 2
}

$preflight = Join-Path $PSScriptRoot 'preflight.py'
if (-not (Test-Path -LiteralPath $preflight)) {
    Write-Host "НЕ ГОТОВО: нет $preflight — пакет неполный."
    exit 2
}

$arguments = @($preflight, '--root', $Root, '--data-dir', $DataDir)
if ($Prepare) { $arguments += '--prepare' }
if ($NoProbes) { $arguments += '--no-probes' }
if ($Json) { $arguments += '--json' }

$program = $interpreter[0]
$rest = @()
if ($interpreter.Count -gt 1) { $rest = $interpreter[1..($interpreter.Count - 1)] }
& $program @rest @arguments
exit $LASTEXITCODE
