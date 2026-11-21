# Канонический Docker-запуск TestPilot на Windows: только явная схема, затем up и фактическая готовность.
# Миграции владельца здесь не выполняются: пустая схема создаётся first-run.ps1 -Docker через init.ps1.
# Остановка — scripts/compose.ps1 stop (guard), не down без тома.
. (Join-Path $PSScriptRoot 'windows-common.ps1')
Assert-Windows
Push-Location $script:ProjectRoot
try {
    Assert-Docker
    $webPort = if ($env:TP_WEB_HOST_PORT) { [int]$env:TP_WEB_HOST_PORT } else { 8000 }
    $impactPort = if ($env:TP_IMPACT_HOST_PORT) { [int]$env:TP_IMPACT_HOST_PORT } else { 8001 }
    $intelPort = if ($env:TP_INTEL_HOST_PORT) { [int]$env:TP_INTEL_HOST_PORT } else { 8002 }
    $ports = @{ web = $webPort; impact = $impactPort; intelligence = $intelPort }
    # Обычный запуск: compose не запускает профиль init, поэтому схема не меняется.
    Invoke-Checked docker @('compose', 'up', '-d', '--wait', 'web', 'impact', 'intelligence')
    # Фактическая ревизия схемы против головы миграций кода (внутри официального образа).
    $schemaRaw = (& docker @('compose', '--profile', 'init', 'run', '--rm', 'migrate',
                             'python', 'scripts/schema_state.py', '--json') | Out-String).Trim()
    if (-not $schemaRaw) { throw 'Не удалось прочитать ревизию схемы БД.' }
    $schema = $schemaRaw | ConvertFrom-Json
    if ($schema.state -ne 'ok') {
        throw ('Схема БД не на голове миграций: {0} (БД={1}, код={2}).' -f $schema.state, $schema.current, (($schema.heads) -join ', '))
    }

    $deadline = (Get-Date).AddSeconds(120)
    $health = @{}
    foreach ($service in @('web', 'impact', 'intelligence')) {
        $url = 'http://127.0.0.1:{0}/health' -f $ports[$service]
        $ready = $false
        $last = 'нет ответа'
        while (-not $ready -and (Get-Date) -lt $deadline) {
            try {
                $response = Invoke-RestMethod -Uri $url -TimeoutSec 3
                if ($response.status -eq 'ok') {
                    $health[$service] = $response
                    $ready = $true
                } else {
                    $last = 'status=' + $response.status
                }
            } catch {
                $last = $_.Exception.Message
            }
            if (-not $ready) { Start-Sleep -Seconds 1 }
        }
        if (-not $ready) {
            throw ("{0} не готов ({1}). Stop: scripts\compose.ps1 logs --tail=60 {0}" -f $service, $last)
        }
    }

    # Квитанция: идентичность БД, ревизия, health и контейнеры проекта. Секретов здесь нет.
    New-Item -ItemType Directory -Path $script:RuntimeDir -Force | Out-Null
    $containers = @(& docker compose ps -q 2>$null | Where-Object { $_ })
    # Имя проекта берём из фактического compose-конфига, а не из догадки/литерала.
    $project = 'unknown'
    $configRaw = (& docker compose config --format json 2>$null | Out-String).Trim()
    if ($configRaw) {
        try { $project = [string](($configRaw | ConvertFrom-Json).name) } catch { $project = 'unknown' }
    }
    if (-not $project) { $project = 'unknown' }
    $receipt = @{
        mode = 'docker'
        compose_project = $project
        ports = $ports
        containers = $containers
        health = $health
        schema = $schema
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
    }
    $receiptPath = Join-Path $script:RuntimeDir 'docker-start-receipt.json'
    $temp = $receiptPath + '.tmp'
    $receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $receiptPath -Force

    Write-Host ('Ready: http://localhost:{0} (login: expert; password: local .env)' -f $ports['web'])
    Write-Host 'Stop: powershell -ExecutionPolicy Bypass -File scripts\compose.ps1 stop'
    Start-Process ('http://localhost:{0}' -f $ports['web'])
} finally {
    Pop-Location
}
