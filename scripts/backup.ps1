<#
.SYNOPSIS
    Дамп БД TestPilot из запущенного compose-стека.
.DESCRIPTION
    Пишет только файл дампа в .runtime\backups\ и ничего не удаляет.
    Запуск: powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
    Переменные: POSTGRES_USER/POSTGRES_DB (иначе берутся из контейнера db), BACKUP_DIR.
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$backupDir = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { '.runtime\backups' }
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$dbUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { (docker compose exec -T db printenv POSTGRES_USER 2>$null | Out-String).Trim() }
$dbName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { (docker compose exec -T db printenv POSTGRES_DB 2>$null | Out-String).Trim() }
if (-not $dbUser) { $dbUser = 'testpilot' }
if (-not $dbName) { $dbName = 'testpilot' }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$file = Join-Path $backupDir "testpilot-$stamp.sql"
# Редирект через cmd, чтобы дамп не прошёл через кодировку PowerShell.
& cmd /c "docker compose exec -T db pg_dump -U $dbUser $dbName > `"$file`""
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  pg_dump вернул код $LASTEXITCODE"; exit 1 }

$size = (Get-Item $file).Length
if ($size -le 0) { Write-Host "FAIL  дамп пуст: $file"; exit 1 }
Write-Host "OK  дамп: $file ($size байт)"
