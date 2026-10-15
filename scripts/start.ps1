. (Join-Path $PSScriptRoot 'windows-common.ps1')
Assert-Windows
Push-Location $script:ProjectRoot
$started = @()
try {
    if (-not (Test-Path -LiteralPath $script:PythonExe)) { throw 'Run scripts/install.ps1 first.' }
    Import-LocalEnv
    # This entrypoint serves only the local host publication.
    $env:IMPACT_URL = 'http://127.0.0.1:8001'
    $env:INTELLIGENCE_URL = 'http://127.0.0.1:8002'
    $env:WEB_URL = 'http://127.0.0.1:8000'
    Assert-Docker
    Invoke-Checked $script:PythonExe @('-c', "from playwright.sync_api import sync_playwright; from testpilot.ai.executor import browser_path; path=browser_path(); assert path, 'Install Chromium or fix TP_CHROMIUM_PATH'; p=sync_playwright().start(); b=p.chromium.launch(executable_path=path, headless=True); b.close(); p.stop(); print('Chromium: OK')")
    # No image rebuild or browser installation is required on an ordinary restart.
    Invoke-Checked docker @('compose', 'up', '-d', '--wait', 'db')
    Invoke-Checked $script:PythonExe @('-m', 'alembic', 'upgrade', 'head')
    Invoke-Checked docker @('compose', 'stop', 'impact', 'intelligence', 'web')
    Stop-OwnedServices
    foreach ($port in @(8001, 8002, 8000)) { Assert-FreePort $port }
    New-Item -ItemType Directory -Path $script:RuntimeDir -Force | Out-Null
    foreach ($spec in @(@('impact', 8001), @('intelligence', 8002), @('web', 8000))) {
        $service = [string]$spec[0]
        $port = [int]$spec[1]
        # Start-Process joins ArgumentList; quote the app directory explicitly for spaces.
        $arguments = '-m uvicorn testpilot.{0}.api:app --app-dir "{1}" --host 127.0.0.1 --port {2}' -f $service, $script:ProjectRoot, $port
        $proc = Start-Process -FilePath $script:PythonExe -ArgumentList $arguments -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $script:RuntimeDir ("windows-$service.log")) `
            -RedirectStandardError (Join-Path $script:RuntimeDir ("windows-$service.error.log"))
        $started += $proc
        $native = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $proc.Id)
        if ($null -eq $native) { throw "$service exited at startup. Check .runtime/windows-$service.error.log" }
        @{ process_id = $proc.Id; created_ticks = $native.CreationDate.ToUniversalTime().Ticks.ToString() } |
            ConvertTo-Json | Set-Content -LiteralPath (Get-ProcessRecordPath $service) -Encoding UTF8
    }
    Invoke-Checked $script:PythonExe @('scripts/smoke.py', '--health-only')
    foreach ($service in @('impact', 'intelligence', 'web')) {
        if ($null -eq (Get-OwnedService $service)) { throw "$service exited. Check .runtime logs." }
    }
    Write-Host 'Ready: http://localhost:8000 (login: expert; password: local .env)'
    Write-Host 'Demo: Overview -> Open demonstration. Stop: .\scripts\stop.ps1'
} catch {
    # Roll back only processes created by this start attempt; preserve PostgreSQL data.
    foreach ($proc in $started) {
        $proc.Refresh()
        if (-not $proc.HasExited) {
            & taskkill /PID $proc.Id /T /F | Out-Null
            if (-not $proc.WaitForExit(15000)) {
                Write-Warning 'A process from this failed start is still running. Check .runtime logs.'
            }
        }
    }
    throw
} finally {
    Pop-Location
}
