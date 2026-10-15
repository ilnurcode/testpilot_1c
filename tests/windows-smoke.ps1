# Portable checks for helpers, not a substitute for a full Windows run.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
foreach ($file in Get-ChildItem (Join-Path $root 'scripts') -Filter '*.ps1') {
    $parseErrors = $null
    $tokens = $null
    [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -gt 0) { throw ($parseErrors | Out-String) }
}
. (Join-Path $root 'scripts/windows-common.ps1')

function Assert-Check {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
function Assert-Rejected {
    param([scriptblock]$Action)
    $rejected = $false
    try { & $Action } catch { $rejected = $true }
    Assert-Check $rejected 'Expected rejection did not occur'
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('testpilot powershell ' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $fixture | Out-Null
try {
    $script:ProjectRoot = $fixture
    $script:PythonExe = Join-Path $fixture '.venv/Scripts/python.exe'
    $script:RuntimeDir = Join-Path $fixture '.runtime'
    New-Item -ItemType Directory -Path $script:RuntimeDir | Out-Null
    @'
# No expansion or command execution is permitted.
TP_PS_TEST_LITERAL='$(throw "must not execute")'
TP_PS_TEST_QUOTED="value with spaces"
TP_PS_TEST_EMPTY=
'@ | Set-Content -LiteralPath (Join-Path $fixture '.env') -Encoding UTF8
    Import-LocalEnv
    Assert-Check ($env:TP_PS_TEST_LITERAL -eq '$(throw "must not execute")') 'Environment data was interpreted'
    Assert-Check ($env:TP_PS_TEST_QUOTED -eq 'value with spaces') 'Quoted value mismatch'
    'invalid syntax' | Set-Content -LiteralPath (Join-Path $fixture '.env')
    Assert-Rejected { Import-LocalEnv }
    $shell = (Get-Process -Id $PID).Path
    Invoke-Checked $shell @('-NoProfile', '-Command', 'exit 0')
    Assert-Rejected { Invoke-Checked $shell @('-NoProfile', '-Command', 'exit 9') }

    $script:SimulatedProcess = [pscustomobject]@{
        ProcessId = 4100
        CreationDate = [datetime]::UtcNow
        ExecutablePath = $script:PythonExe
        CommandLine = 'python -m uvicorn testpilot.web.api:app --app-dir "' + $fixture + '"'
    }
    function Get-CimInstance { param($ClassName, $Filter); return $script:SimulatedProcess }
    $script:Stopped = @()
    function taskkill { $script:Stopped += ,$args; $global:LASTEXITCODE = 0 }
    function Wait-Process { param($Id, $Timeout, $ErrorAction) }

    $record = Get-ProcessRecordPath 'web'
    @{process_id=4100; created_ticks=$script:SimulatedProcess.CreationDate.ToUniversalTime().Ticks.ToString()} |
        ConvertTo-Json | Set-Content -LiteralPath $record
    Assert-Check ((Get-OwnedService 'web').ProcessId -eq 4100) 'Owned process not recognized'

    $saved = $script:SimulatedProcess.CommandLine
    $script:SimulatedProcess.CommandLine = 'python foreign-process.py'
    Assert-Rejected { Stop-OwnedServices }
    Assert-Check ($script:Stopped.Count -eq 0) 'A foreign process would have been stopped'
    $script:SimulatedProcess.CommandLine = $saved
    $script:SimulatedProcess.CreationDate = $script:SimulatedProcess.CreationDate.AddSeconds(1)
    Assert-Rejected { Stop-OwnedServices }
    Assert-Check ($script:Stopped.Count -eq 0) 'A reused PID would have been stopped'
    $script:SimulatedProcess.CreationDate = $script:SimulatedProcess.CreationDate.AddSeconds(-1)
    Stop-OwnedServices
    Assert-Check ($script:Stopped.Count -eq 1) 'Owned process tree was not stopped exactly once'
    Assert-Check (($script:Stopped[0] -join ' ') -eq '/PID 4100 /T /F') 'Wrong process tree arguments'
    Assert-Check (-not (Test-Path -LiteralPath $record)) 'PID record was not removed'
    Stop-OwnedServices
    Assert-Check ($script:Stopped.Count -eq 1) 'Repeated stop was not idempotent'
    Write-Host 'PowerShell: syntax, literal env, exit codes, paths with spaces, ownership/PID reuse guard, process tree and repeated stop: OK'
} finally {
    Remove-Item -LiteralPath $fixture -Recurse -Force
    foreach ($name in @('TP_PS_TEST_LITERAL', 'TP_PS_TEST_QUOTED', 'TP_PS_TEST_EMPTY')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}
