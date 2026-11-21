<#
.SYNOPSIS
    Явная инициализация схемы БД с нуля (не часть обычного запуска).
.DESCRIPTION
    Без -Docker: локальный venv, scripts/schema_actions.py --init (только для неинициализированной БД).
    С -Docker: docker compose --profile init run --rm migrate и проверка головы миграций.
    Существующая схема не пересоздаётся: отказ и подсказка scripts\update.ps1.
    Использование: powershell -ExecutionPolicy Bypass -File scripts\init.ps1 [-Docker]
#>
param([switch]$Docker)
. (Join-Path $PSScriptRoot 'windows-common.ps1')
Assert-Windows
Push-Location $script:ProjectRoot
try {
    if (-not $Docker -and -not (Test-Path -LiteralPath $script:PythonExe)) { throw 'Run scripts/install.ps1 first.' }
    Assert-DataDirWritable
    Assert-Docker
    if (-not $Docker) { Import-LocalEnv }
    Invoke-Checked docker @('compose', 'up', '-d', '--wait', 'db')
    if ($Docker) {
        # Та же проверка состояния, что и в локальной ветке: uninitialized/pending/ahead — отказ.
        Invoke-Checked docker @('compose', '--profile', 'init', 'run', '--rm', '--build', 'migrate', 'python', 'scripts/schema_actions.py', '--init')
        Invoke-Checked docker @('compose', '--profile', 'init', 'run', '--rm', 'migrate', 'python', 'scripts/schema_state.py', '--require-head')
    } else {
        Invoke-Checked $script:PythonExe @('scripts/schema_actions.py', '--init')
        Invoke-Checked $script:PythonExe @('scripts/schema_state.py', '--require-head')
    }
    Write-Host 'Схема инициализирована. Дальше: .\scripts\start.ps1'
} finally {
    Pop-Location
}
