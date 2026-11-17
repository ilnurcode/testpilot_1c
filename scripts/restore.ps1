<#
.SYNOPSIS
    ЧЕРНОВИК: не проверялся на реальном дампе; только отдельный стек (`-p testpilot-restore`), не рабочий;
    pg_restore с --exit-on-error.
.DESCRIPTION
    Восстановление БД TestPilot из дампа в указанный compose-проект; тома не удаляются, `down -v` не вызывается.
    ТОЛЬКО В ПУСТУЮ СХЕМУ: плейн-дамп содержит CREATE TABLE, а `up -d --wait` запускает migrate и создаёт таблицы.
    Правильный порядок:
      1) docker compose -p testpilot-restore up -d db          # только база, без migrate
      2) powershell -File scripts\restore.ps1 -Dump PATH -Yes -Project testpilot-restore
      3) есть alembic_version в дампе -> docker compose -p testpilot-restore up -d --wait
         нет alembic_version -> сверить таблицы и выполнить alembic stamp head вручную; `up -d --wait` до этого упадёт.
    Формат: .sql -> psql -v ON_ERROR_STOP=1; .dump/.backup -> pg_restore --clean --if-exists --no-owner --exit-on-error.
#>
param(
    [Parameter(Mandatory = $true)][string]$Dump,
    [switch]$Yes,
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

$tables = (docker compose -p $Project exec -T db psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>$null | Out-String).Trim()
if ($tables -notmatch '^\d+$') {
    Write-Host "Отказ: не удалось прочитать БД проекта $Project (контейнер db не запущен)."
    Write-Host "       Запустите только базу: docker compose -p $Project up -d db, затем повторите restore."
    exit 2
}

$hasAlembic = if ($method -eq 'psql') {
    [bool](Select-String -LiteralPath $Dump -Pattern 'COPY (public\.)?alembic_version|INSERT INTO (public\.)?alembic_version' -Quiet)
} else {
    # Редирект делает cmd: PowerShell не поддерживает '<' для stdin.
    $listing = (& cmd /c "docker compose -p $Project exec -T db pg_restore --exit-on-error --list < `"$Dump`"" 2>$null | Out-String)
    [bool]($listing -match 'alembic_version')
}
$hasAlembicText = if ($hasAlembic) { 'да' } else { 'нет' }

Write-Host 'План восстановления:'
Write-Host "  дамп: $Dump ($size байт, способ: $method)"
Write-Host "  проект compose: $Project; база: $dbUser@db/$dbName"
Write-Host "  схема public: $tables таблиц (нужна пустая)"
Write-Host "  alembic_version в дампе: $hasAlembicText"
Write-Host '  будут изменены только данные БД; тома, контейнеры и файлы не удаляются'

if ([int]$tables -gt 0) {
    Write-Host "Отказ: схема содержит $tables таблиц: восстановление возможно только в пустую БД. Запустите только базу: ``docker compose -p testpilot-restore up -d db``, затем повторите restore; приложение поднимайте после восстановления."
    exit 1
}
if (-not $Yes) { Write-Host 'Отказ: повторите с -Yes, чтобы выполнить восстановление.'; exit 1 }

if ($method -eq 'psql') {
    & cmd /c "docker compose -p $Project exec -T db psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName < `"$Dump`""
} else {
    & cmd /c "docker compose -p $Project exec -T db pg_restore --clean --if-exists --no-owner --exit-on-error -U $dbUser -d $dbName < `"$Dump`""
}
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  восстановление завершилось с кодом $LASTEXITCODE"; exit 1 }

Write-Host 'Счётчики после восстановления:'
foreach ($query in @(
    "SELECT 'impact_projects='||count(*) FROM impact_projects",
    "SELECT 'impact_uploads='||count(*) FROM impact_uploads",
    "SELECT 'ti_ai_suites='||count(*) FROM ti_ai_suites",
    "SELECT 'code_audit='||count(*) FROM code_audit"
)) {
    (docker compose -p $Project exec -T db psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -t -A -c $query | Out-String).Trim()
}
if ($hasAlembic) {
    Write-Host "Готово. Поднимайте приложение: docker compose -p $Project up -d --wait"
} else {
    Write-Host 'ВНИМАНИЕ: в дампе нет alembic_version. Сверьте таблицы и выполните alembic stamp head вручную:'
    Write-Host "  docker compose -p $Project run --rm migrate alembic stamp head  (или exec в поднятом сервисе)"
    Write-Host "  'up -d --wait' до этого упадёт: migrate попытается создать уже существующие таблицы."
}
