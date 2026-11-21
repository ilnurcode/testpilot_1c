<#
.SYNOPSIS
    Проверяемое восстановление в ПУСТУЮ одноразовую БД: sha256 манифеста, counts, hashes evidence, alembic.
#>
param([Parameter(Mandatory = $true)][string]$Dump, [switch]$Yes)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Yes) { Write-Host 'Отказ: повторите с -Yes'; exit 2 }
foreach ($name in 'TP_BACKUP_PROJECT','TP_BACKUP_COMPOSE_FILE','TP_BACKUP_EXPECT_DB','TP_BACKUP_INSTANCE_TOKEN','TP_BACKUP_DB_USER') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { Write-Host "Отказ: задайте $name"; exit 2 }
}
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }
$args = @('restore','--dump',$Dump,'--yes','--project',$env:TP_BACKUP_PROJECT,
          '--compose-file',$env:TP_BACKUP_COMPOSE_FILE,'--expect-db',$env:TP_BACKUP_EXPECT_DB,
          '--instance-token',$env:TP_BACKUP_INSTANCE_TOKEN,'--db-user',$env:TP_BACKUP_DB_USER)
if ($env:TP_BACKUP_ENV_FILE) { $args += @('--env-file',$env:TP_BACKUP_ENV_FILE) }
& $Python (Join-Path $Root 'scripts\pg_backup.py') @args
exit $LASTEXITCODE
