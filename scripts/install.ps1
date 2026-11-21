# Run from PowerShell on Windows; no Ubuntu terminal is required.
. (Join-Path $PSScriptRoot 'windows-common.ps1')
Assert-Windows
Push-Location $script:ProjectRoot
try {
    Assert-Docker
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Install Python 3.12 x64 with the Python launcher, then reopen PowerShell.'
    }
    Invoke-Checked py @('-3.12', '-c', "import struct; assert struct.calcsize('P')==8, 'Python x64 required'")
    if (-not (Test-Path -LiteralPath $script:PythonExe)) {
        if (Test-Path -LiteralPath $script:VenvDir) {
            throw "An incompatible environment exists at $script:VenvDir (possibly from Linux). Rename it before installing on Windows."
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $script:VenvDir) -Force | Out-Null
        Invoke-Checked py @('-3.12', '-m', 'venv', $script:VenvDir)
    }
    $env:PYTHONUTF8 = '1'
    Invoke-Checked $script:PythonExe @('-c', "import sys; assert sys.version_info[:2]==(3,12), 'Python 3.12 required'")
    Invoke-Checked $script:PythonExe @('-m', 'pip', 'install', '-r', 'requirements.lock')
    if (Test-WritableDirectory $script:ProjectRoot) {
        Invoke-Checked $script:PythonExe @('-m', 'pip', 'install', '--no-deps', '-e', '.')
    } else {
        # Пакет неизменяем (например Program Files): приложение импортируется по PYTHONPATH,
        # который выставляет windows-common.ps1. Зависимости уже стоят в окружении данных.
        Write-Host "Каталог программы только для чтения: editable-установка не выполняется, импорт идёт из $script:ProjectRoot."
    }
    Invoke-Checked $script:PythonExe @('scripts/local_env.py')
    Import-LocalEnv
    Invoke-Checked $script:PythonExe @('-m', 'playwright', 'install', 'chromium')
    # Native Windows defaults to Playwright's managed browser. Preserve explicit overrides.
    Invoke-Checked $script:PythonExe @('-c', "from playwright.sync_api import sync_playwright; from testpilot.ai.executor import browser_path; path=browser_path(); assert path, 'Install Chromium or fix TP_CHROMIUM_PATH'; p=sync_playwright().start(); b=p.chromium.launch(executable_path=path, headless=True); b.close(); p.stop(); print('Chromium: OK')")
    Write-Host 'Installed. Run: .\scripts\start.ps1'
} finally {
    Pop-Location
}
