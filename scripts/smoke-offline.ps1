<#
.SYNOPSIS
    Офлайн-проверка: БД и сеть не нужны. Docker/compose/psql не вызываются.
.DESCRIPTION
    Запускает только тесты, которым хватает локального venv и файловой системы (19 файлов).
    Запуск: powershell -ExecutionPolicy Bypass -File scripts\smoke-offline.ps1
    Переменная PYTHON позволяет указать свой интерпретатор.
#>
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$python = $env:PYTHON
if (-not $python) {
    $windowsVenv = Join-Path (Get-Location) '.venv\Scripts\python.exe'
    $posixVenv = Join-Path (Get-Location) '.venv/bin/python'
    if (Test-Path $windowsVenv) { $python = $windowsVenv }
    elseif (Test-Path $posixVenv) { $python = $posixVenv }
    else {
        Write-Host 'Не найден локальный venv (.venv\Scripts\python.exe или .venv/bin/python).'
        Write-Host 'Создайте его: python -m venv .venv, затем установите зависимости.'
        exit 2
    }
}

$files = @(
    'tests/test_1c_odata.py',
    'tests/test_1c_probe.py',
    'tests/test_api_docs_complete.py',
    'tests/test_binary_adapter.py',
    'tests/test_browser_runtime.py',
    'tests/test_compose_guard.py',
    'tests/test_docs_links.py',
    'tests/test_env_example_complete.py',
    'tests/test_gateway_forwarding.py',
    'tests/test_gateway_methods.py',
    'tests/test_numeric_env.py',
    'tests/test_open_url_security.py',
    'tests/test_openapi_access.py',
    'tests/test_openapi_operation_ids.py',
    'tests/test_optimizer.py',
    'tests/test_pytest_addopts.py',
    'tests/test_scripts_syntax.py',
    'tests/test_ui_smoke.py',
    'tests/test_xml_adapter.py'
)

Write-Host "Офлайн-проверка: БД и сеть не нужны (файлов: $($files.Count))"
& $python -m pytest @files
exit $LASTEXITCODE
