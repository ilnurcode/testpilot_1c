<#
.SYNOPSIS
    Единая точка первого запуска TestPilot 1C (окружение → схема → запуск).
.DESCRIPTION
    Идемпотентно: повторный запуск не переустанавливает окружение и не меняет схему,
    если она уже на голове миграций.

    -Packaged — режим пакетной установки: код в {app} может быть только для чтения,
    поэтому данные, окружение и логи живут в TP_DATA_DIR (по умолчанию
    %LOCALAPPDATA%\TestPilot1C), а пакет импортируется по PYTHONPATH.
    -Docker — канонический Docker-вход: БД поднимается отдельно, схема проверяется
    (init только для пустой БД, устаревшая — отказ с требованием update), затем
    compose up --wait и фактическая проверка health всех трёх сервисов. Venv не нужен;
    миграции владельца не выполняются.

    Схему создаёт только явный scripts\init.ps1; при устаревшей схеме скрипт
    отказывает и требует scripts\update.ps1 -Confirmed после проверенного backup.
    Python и Docker не устанавливаются — только проверяются с понятным сообщением.
    Использование: powershell -ExecutionPolicy Bypass -File scripts\first-run.ps1 [-Packaged] [-DataDir <путь>]
#>
param(
    [switch]$Packaged,
    [switch]$Docker,
    [string]$DataDir = ''
)

if (-not $env:TP_DATA_DIR) {
    if ($DataDir) {
        $resolved = if ([IO.Path]::IsPathRooted($DataDir)) { $DataDir } else { Join-Path (Get-Location).Path $DataDir }
        $env:TP_DATA_DIR = [IO.Path]::GetFullPath($resolved)
    } elseif ($Packaged) {
        $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:USERPROFILE }
        $env:TP_DATA_DIR = Join-Path $base 'TestPilot1C'
    }
}
if (-not $env:TP_VENV -and $env:TP_DATA_DIR) {
    $env:TP_VENV = Join-Path $env:TP_DATA_DIR '.venv'
}

function Assert-Script {
    param([string]$Name)
    $path = Join-Path $PSScriptRoot $Name
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host ('НЕ ГОТОВО: отсутствует {0}; запуск не подменяется другим профилем.' -f $path) -ForegroundColor Yellow
        exit 2
    }
}

. (Join-Path $PSScriptRoot 'windows-common.ps1')
Assert-Windows
Push-Location $script:ProjectRoot
try {
    Write-Host 'TestPilot 1C: первый запуск'
    Write-Host "  код: $script:ProjectRoot"
    Write-Host "  данные и окружение: $script:DataDir"

    # 1. Предпосылки. В Docker-режиме Python/venv не нужны: всё выполняется в образе.
    if (-not $Docker -and -not (Get-Command py -ErrorAction SilentlyContinue)) {
        Write-Host 'НЕ ГОТОВО: не найден Python 3.12 x64 (python launcher `py`). Установите его вручную и повторите.' -ForegroundColor Yellow
        exit 2
    }
    if (-not $Docker) {
        & py -3.12 -c "import struct; assert struct.calcsize('P')==8" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'НЕ ГОТОВО: нужен Python 3.12 x64 (64-бит).' -ForegroundColor Yellow
            exit 2
        }
    }
    # Предпосылки: отказ с кодом 2 (Docker CLI/права записи в каталог данных).
    try {
        Assert-Docker
        Assert-DataDirWritable
    } catch {
        Write-Host ('НЕ ГОТОВО: {0}' -f $_.Exception.Message) -ForegroundColor Yellow
        exit 2
    }

    if (-not $Docker) {
        Assert-Script 'install.ps1'
        # 2. Окружение и зависимости (идемпотентно). Ошибки install.ps1 пробрасываются исключением.
        if (Test-Path -LiteralPath $script:PythonExe) {
            Write-Host 'Окружение найдено: зависимости не переустанавливаются.'
        } else {
            & (Join-Path $PSScriptRoot 'install.ps1')
            if ($LASTEXITCODE -ne 0) {
                Write-Host ('НЕ ГОТОВО: установка зависимостей вернула код {0}.' -f $LASTEXITCODE) -ForegroundColor Yellow
                exit $LASTEXITCODE
            }
        }
        Import-LocalEnv
    }

    # 3. Схема: создаём только неинициализированную; обновление — отдельное явное действие.
    Invoke-Checked docker @('compose', 'up', '-d', '--wait', 'db')
    if ($Docker) {
        # Без venv: схема читается и создаётся официальным образом в профиле init.
        $raw = (& docker @('compose', '--profile', 'init', 'run', '--rm', '--build', 'migrate',
                           'python', 'scripts/schema_state.py', '--json') | Out-String).Trim()
    } else {
        $raw = (& $script:PythonExe @('scripts/schema_state.py', '--json') | Out-String).Trim()
    }
    if (-not $raw) { throw 'Не удалось прочитать состояние схемы БД (см. сообщение выше).' }
    $state = $raw | ConvertFrom-Json
    $current = if ($state.current) { [string]$state.current } else { 'нет' }
    $heads = if ($state.heads) { ($state.heads -join ', ') } else { 'нет' }
    Write-Host ('Схема БД: {0} (БД={1}, код={2})' -f $state.state, $current, $heads)
    switch ($state.state) {
        'ok' { }
        'uninitialized' {
            Assert-Script 'init.ps1'
            if ($Docker) {
                & (Join-Path $PSScriptRoot 'init.ps1') -Docker
            } else {
                & (Join-Path $PSScriptRoot 'init.ps1')
            }
            if ($LASTEXITCODE -ne 0) {
                Write-Host ('НЕ ГОТОВО: инициализация схемы вернула код {0}.' -f $LASTEXITCODE) -ForegroundColor Yellow
                exit $LASTEXITCODE
            }
        }
        default {
            $hint = if ($Docker) { '.\scripts\update.ps1 -Confirmed -Docker' } else { '.\scripts\update.ps1 -Confirmed' }
            Write-Host ('НЕ ГОТОВО: схема БД не готова ({0}). Обычный запуск её не меняет. Сделайте проверенный backup и выполните {1}.' -f $state.state, $hint) -ForegroundColor Yellow
            exit 3
        }
    }

    # 4. Обычный запуск и открытие интерфейса только после подтверждённой готовности.
    if ($Docker) {
        Assert-Script 'start-docker.ps1'
        & (Join-Path $PSScriptRoot 'start-docker.ps1')
        exit $LASTEXITCODE
    }
    Assert-Script 'start.ps1'
    & (Join-Path $PSScriptRoot 'start.ps1')
    if ($LASTEXITCODE -ne 0) {
        Write-Host ('НЕ ГОТОВО: запуск сервисов вернул код {0}.' -f $LASTEXITCODE) -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
    & $script:PythonExe @('launcher.py')
    exit $LASTEXITCODE
} catch {
    Write-Host ('НЕ ГОТОВО: {0}' -f $_.Exception.Message) -ForegroundColor Yellow
    exit 1
} finally {
    Pop-Location
}
