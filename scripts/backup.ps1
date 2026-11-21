<#
.SYNOPSIS
    Атомарный backup одноразового стенда (dump + sha256/манифест).
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
foreach ($name in 'TP_BACKUP_PROJECT','TP_BACKUP_COMPOSE_FILE','TP_BACKUP_EXPECT_DB','TP_BACKUP_INSTANCE_TOKEN','TP_BACKUP_DB_USER') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { Write-Host "Отказ: задайте $name"; exit 2 }
}
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }
$args = @('backup','--project',$env:TP_BACKUP_PROJECT,'--compose-file',$env:TP_BACKUP_COMPOSE_FILE,
          '--expect-db',$env:TP_BACKUP_EXPECT_DB,'--instance-token',$env:TP_BACKUP_INSTANCE_TOKEN,
          '--db-user',$env:TP_BACKUP_DB_USER,'--dump-dir',$(if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $Root '.runtime\backups' }))
if ($env:TP_BACKUP_ENV_FILE) { $args += @('--env-file',$env:TP_BACKUP_ENV_FILE) }
if ($env:TP_BACKUP_MANUAL -eq '1') {
    if (-not $env:TP_BACKUP_CONFIRM_PROJECT) { Write-Host 'Отказ: ручной backup требует TP_BACKUP_CONFIRM_PROJECT'; exit 2 }
    $args += @('--manual','--confirm-project',$env:TP_BACKUP_CONFIRM_PROJECT)
}
& $Python (Join-Path $Root 'scripts\pg_backup.py') @args
exit $LASTEXITCODE
