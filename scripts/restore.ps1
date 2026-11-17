<#
.SYNOPSIS
    Восстановление БД TestPilot из дампа (пишет только в БД запущенного стека).
.DESCRIPTION
    Тома не удаляются, `docker compose down -v` не вызывается, файлы не удаляются.
    Формат по расширению: .sql -> psql, .dump/.backup -> pg_restore (без --clean).
    -Yes   подтверждение: без него скрипт печатает план и выходит с кодом 1.
    -Force разрешает заливку в непустую БД (иначе отказ при наличии проектов).
    Переменные: POSTGRES_USER/POSTGRES_DB (иначе из контейнера db), COMPOSE_PROJECT_NAME.
    Пример: powershell -File scripts\restore.ps1 -Dump .runtime\backups\testpilot-20261117-040000.sql -Yes
#>
param(
    [Parameter(Mandatory = $true)][string]$Dump,
    [switch]$Yes,
    [switch]$Force
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

$dbUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { (docker compose exec -T db printenv POSTGRES_USER 2>$null | Out-String).Trim() }
$dbName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { (docker compose exec -T db printenv POSTGRES_DB 2>$null | Out-String).Trim() }
if (-not $dbUser) { $dbUser = 'testpilot' }
if (-not $dbName) { $dbName = 'testpilot' }
$project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { 'testpilot' }

$projects = (docker compose exec -T db psql -U $dbUser -d $dbName -t -A -c 'SELECT count(*) FROM impact_projects' 2>$null | Out-String).Trim()
if ($projects -notmatch '^\d+$') {
    Write-Host "Отказ: не удалось прочитать БД (стек не запущен или нет миграций). Запустите: docker compose -p $project up -d --wait"
    exit 2
}

Write-Host 'План восстановления:'
Write-Host "  дамп: $Dump ($size байт, способ: $method)"
Write-Host "  проект compose: $project; база: $dbUser@db/$dbName"
Write-Host "  текущих проектов в БД: $projects"
Write-Host '  будут изменены только данные БД; тома, контейнеры и файлы не удаляются'

if (-not $Yes) { Write-Host 'Отказ: повторите с -Yes, чтобы выполнить восстановление.'; exit 1 }
if ([int]$projects -gt 0 -and -not $Force) {
    Write-Host "Отказ: в БД уже $projects проектов; заливка поверх живых данных требует -Force."
    exit 1
}

if ($method -eq 'psql') {
    & cmd /c "docker compose exec -T db psql -U $dbUser -d $dbName < `"$Dump`""
} else {
    & cmd /c "docker compose exec -T db pg_restore -U $dbUser -d $dbName < `"$Dump`""
}
if ($LASTEXITCODE -ne 0) { Write-Host "Отказ: восстановление завершилось с кодом $LASTEXITCODE"; exit 1 }

Write-Host 'Счётчики после восстановления:'
foreach ($query in @(
    "SELECT 'impact_projects='||count(*) FROM impact_projects",
    "SELECT 'impact_uploads='||count(*) FROM impact_uploads",
    "SELECT 'ti_ai_suites='||count(*) FROM ti_ai_suites",
    "SELECT 'code_audit='||count(*) FROM code_audit"
)) {
    (docker compose exec -T db psql -U $dbUser -d $dbName -t -A -c $query | Out-String).Trim()
}
Write-Host "Готово. Перезапустите сервисы: docker compose -p $project up -d --wait"
