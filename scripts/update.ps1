<#
.SYNOPSIS
    Явное обновление схемы БД до головы миграций (не часть обычного запуска).
.DESCRIPTION
    Требует -Confirmed: сначала проверенный backup/restore, затем обновление.
    Никогда не понижает схему; неинициализированную БД отправляет в scripts\init.ps1.
    Без -Docker: локальный venv, scripts/schema_actions.py --update --confirmed.
    С -Docker: docker compose --profile init run --rm migrate и проверка головы миграций.
    На рабочей БД запускает только владелец после отдельного допуска.
    Использование: powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -Confirmed [-Docker]
#>
param([switch]$Confirmed, [switch]$Docker)
. (Join-Path $PSScriptRoot 'windows-common.ps1')
Assert-Windows
Push-Location $script:ProjectRoot
try {
    if (-not $Confirmed) {
        throw 'Обновление схемы не выполнено: нужен -Confirmed и проверенный backup/restore. Обычный запуск схему не меняет.'
    }
    if (-not $Docker -and -not (Test-Path -LiteralPath $script:PythonExe)) { throw 'Run scripts/install.ps1 first.' }
    Assert-DataDirWritable
    Assert-Docker
    if (-not $Docker) { Import-LocalEnv }
    Invoke-Checked docker @('compose', 'up', '-d', '--wait', 'db')
    if ($Docker) {
        # Одинаковый fail-closed контракт с локальной веткой: --update требует --confirmed.
        Invoke-Checked docker @('compose', '--profile', 'init', 'run', '--rm', '--build', 'migrate', 'python', 'scripts/schema_actions.py', '--update', '--confirmed')
        Invoke-Checked docker @('compose', '--profile', 'init', 'run', '--rm', 'migrate', 'python', 'scripts/schema_state.py', '--require-head')
    } else {
        Invoke-Checked $script:PythonExe @('scripts/schema_actions.py', '--update', '--confirmed')
        Invoke-Checked $script:PythonExe @('scripts/schema_state.py', '--require-head')
    }
    Write-Host 'Схема обновлена до головы миграций. Дальше: .\scripts\start.ps1'
} finally {
    Pop-Location
}
