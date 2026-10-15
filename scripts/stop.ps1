param([switch]$Database)
. (Join-Path $PSScriptRoot 'windows-common.ps1')
Assert-Windows
Push-Location $script:ProjectRoot
try {
    Stop-OwnedServices
    if ($Database) {
        Import-LocalEnv
        Assert-Docker
        Invoke-Checked docker @('compose', 'stop', 'db')
    }
    Write-Host 'Stopped. Files, .env and PostgreSQL volume were preserved.'
} finally {
    Pop-Location
}
