<#
.SYNOPSIS
    ЧЕРНОВИК: не проверялся на реальном дампе. Проверять только на отдельном стеке
    (`-p testpilot-restore`), не на рабочем.
.DESCRIPTION
    Восстановление БД TestPilot из дампа в указанный compose-проект; тома не удаляются, `down -v` не вызывается.
    Формат по расширению: .sql -> psql -v ON_ERROR_STOP=1, .dump/.backup -> pg_restore --clean --if-exists --no-owner.
    Плейн-дамп содержит CREATE TABLE: заливка в мигрированную БД ломается об существующие таблицы,
    поэтому непустая схема требует осознанного -Force или чистого проекта.
    -Yes подтверждает, -Force разрешает непустую схему, -Project задаёт compose-проект (по умолчанию testpilot).
    Пример: powershell -File scripts\restore.ps1 -Dump .runtime\backups\testpilot-20261117-040000.sql -Yes -Project testpilot-restore
#>
param(
    [Parameter(Mandatory = $true)][string]$Dump,
    [switch]$Yes,
    [switch]$Force,
    [string]$Project = $(if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { 'testpilot' })
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath $Dump -PathType Leaf)) { Write-Host "Отказ: файл дампа не найден: $Dump"; exit 2 }
$size = (Get-Item -LiteralPath $Dump).Length
if ($size -le 0) { Write-Host "Отказ: дамп пуст: $Dump"; exit 2 }

$method = switch -Regex ($Dump) {
    '\.sql$' { 'psql'; break }
    '\.(dump|backup)$' { 'pg_restore'; break }
    default { $null }
}
if (-not $method) { Write-Host "Отказ: неизвестное расширение (ожидается .sql, .dump или .backup): $Dump"; exit 2 }

$dbUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { (docker compose -p $Project exec -T db printenv POSTGRES_USER 2>$null | Out-String).Trim() }
$dbName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { (docker compose -p $Project exec -T db printenv POSTGRES_DB 2>$null | Out-String).Trim() }
if (-not $dbUser) { $dbUser = 'testpilot' }
if (-not $dbName) { $dbName = 'testpilot' }

$state = (docker compose -p $Project exec -T db psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -t -A -c @"
SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='public') || ' ' ||
       CASE WHEN to_regclass('public.impact_projects') IS NULL THEN 0
            ELSE (SELECT count(*) FROM impact_projects) END
"@ 2>$null | Out-String).Trim()
if ($state -notmatch '^(\d+)\s+(\d+)$') {
    Write-Host "Отказ: не удалось прочитать БД проекта $Project (стек не запущен или нет миграций)."
    Write-Host "       Для чистого восстановления: docker compose -p testpilot-restore up -d --wait, затем -Project testpilot-restore"
    exit 2
}
$tables = [int]$Matches[1]
$projects = [int]$Matches[2]

Write-Host 'План восстановления:'
Write-Host "  дамп: $Dump ($size байт, способ: $method)"
Write-Host "  проект compose: $Project; база: $dbUser@db/$dbName"
Write-Host "  текущая схема: $tables таблиц в public; проектов: $projects"
Write-Host '  будут изменены только данные БД; тома, контейнеры и файлы не удаляются'

if (-not $Yes) { Write-Host 'Отказ: повторите с -Yes, чтобы выполнить восстановление.'; exit 1 }
if ($tables -gt 0 -and -not $Force) {
    Write-Host "Отказ: схема уже содержит $tables таблиц: восстановление плейн-дампа перезапишет/сломает её;"
    Write-Host '       используйте -Force осознанно или чистый стек -p testpilot-restore.'
    exit 1
}

if ($method -eq 'psql') {
    & cmd /c "docker compose -p $Project exec -T db psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName < `"$Dump`""
} else {
    & cmd /c "docker compose -p $Project exec -T db pg_restore --clean --if-exists --no-owner -U $dbUser -d $dbName < `"$Dump`""
}
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  восстановление завершилось с кодом $LASTEXITCODE"; exit 1 }

$revision = (docker compose -p $Project exec -T intelligence alembic current 2>$null | Out-String).Trim()
Write-Host "Ревизия после восстановления: $revision"
Write-Host 'Счётчики после восстановления:'
foreach ($query in @(
    "SELECT 'impact_projects='||count(*) FROM impact_projects",
    "SELECT 'impact_uploads='||count(*) FROM impact_uploads",
    "SELECT 'ti_ai_suites='||count(*) FROM ti_ai_suites",
    "SELECT 'code_audit='||count(*) FROM code_audit"
)) {
    (docker compose -p $Project exec -T db psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -t -A -c $query | Out-String).Trim()
}
Write-Host "Готово. Перезапустите сервисы: docker compose -p $Project up -d --wait"
