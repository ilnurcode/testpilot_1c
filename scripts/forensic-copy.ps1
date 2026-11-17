<#
.SYNOPSIS
    Консистентная копия диска Docker Desktop (WSL2) для форензики — только чтение источника.
.DESCRIPTION
    СНАЧАЛА остановите Docker Desktop и выполните `wsl --shutdown`, иначе копия будет несогласованной.
    Копирует vhdx через Copy-Item, считает SHA-256 источника и копии и сверяет их.
    Ничего не удаляет, не останавливает и не монтирует. Запускать вручную, не из приёмки.
    Пример: powershell -File scripts\forensic-copy.ps1 -Destination E:\forensic
#>
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$Source = "$env:LOCALAPPDATA\Docker\wsl\disk\docker_data.vhdx"
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw "Источник не найден: $Source" }
$sourceItem = Get-Item -LiteralPath $Source
if (-not (Test-Path -LiteralPath $Destination)) { New-Item -ItemType Directory -Path $Destination | Out-Null }
$target = Join-Path $Destination $sourceItem.Name
if (Test-Path -LiteralPath $target) { throw "Копия уже существует, перезапись запрещена: $target" }
$drive = Get-PSDrive -Name (Split-Path -Qualifier $Destination).TrimEnd(':')
if ($drive.Free -lt $sourceItem.Length) {
    throw ("Мало места: нужно {0:N0} байт, свободно {1:N0}" -f $sourceItem.Length, $drive.Free)
}
Write-Host ("Копирование {0} ({1:N0} байт) -> {2}" -f $Source, $sourceItem.Length, $target)
Copy-Item -LiteralPath $Source -Destination $target
$sourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
$copyHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
$copySize = (Get-Item -LiteralPath $target).Length
Write-Host "Источник SHA-256: $sourceHash"
Write-Host "Копия    SHA-256: $copyHash"
if ($sourceHash -ne $copyHash) { throw 'SHA-256 не совпал: копия не консистентна, источник не меняйте и повторите после wsl --shutdown' }
if ($copySize -ne $sourceItem.Length) { throw 'Размер копии не совпал с источником' }
Write-Host ("OK  копия: {0} ({1:N0} байт)" -f $target, $copySize)
