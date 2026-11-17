<#
.SYNOPSIS
    Обёртка над `docker compose` с тем же предохранителем, что scripts/compose.sh.
.DESCRIPTION
    Проект: -Project/-p/--project, иначе COMPOSE_PROJECT_NAME, иначе testpilot.
    Разрешено: `down -v` и volume-операции только для testpilot-ci / testpilot-restore; `down` без -v; остальные команды.
    Запрещено (код 3): `down -v` на рабочем проекте, `volume rm` с томами testpilot, `volume prune`.
    CI не снимает запрет — только меняет текст подсказки.
    Использование: scripts\compose.ps1 down -v   (аргументы передаются как есть, без разбора switch-параметров)
#>
$ErrorActionPreference = 'Stop'
$Arguments = @($args)
$allowed = @('testpilot-ci', 'testpilot-restore')
$project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { '' }
$hasDown = $false
$hasVolumes = $false
$volumeSub = ''
$volumeTargets = @()

for ($i = 0; $i -lt $Arguments.Count; $i++) {
    $arg = $Arguments[$i]
    switch -Regex ($arg) {
        '^(-p|--project)$' { $i++; $project = $Arguments[$i]; continue }
        '^--project=(.+)$' { $project = $Matches[1]; continue }
        '^-p(\S+)$' { $project = $Matches[1]; continue }
        '^down$' { $hasDown = $true; continue }
        '^(-v|--volumes)$' { $hasVolumes = $true; continue }
        '^volume$' {
            $volumeSub = if ($i + 1 -lt $Arguments.Count) { $Arguments[$i + 1] } else { '' }
            if ($i + 2 -lt $Arguments.Count) { $volumeTargets = $Arguments[($i + 2)..($Arguments.Count - 1)] }
            $i = $Arguments.Count
            continue
        }
    }
}
if (-not $project) { $project = 'testpilot' }
$projectAllowed = $allowed -contains $project
$inCi = $env:CI -in @('1', 'true', 'yes')
$ciHint = if ($inCi) { ' В CI используйте отдельный проект: `-p testpilot-ci`.' } else { '' }

if ($hasDown -and $hasVolumes -and -not $projectAllowed) {
    Write-Host "Отказано: ``down -v`` удаляет тома (БД и evidence). На рабочем стенде недопустимо. Используйте ``-p testpilot-ci`` или ``-p testpilot-restore``, либо ``docker compose down`` без ``-v``.$ciHint" -ForegroundColor Red
    exit 3
}

if ($volumeSub) {
    if ($volumeSub -eq 'prune' -and -not $projectAllowed) {
        Write-Host "Отказано: ``volume prune`` может удалить тома проекта $project (БД и evidence). Используйте ``-p testpilot-ci``/``-p testpilot-restore``.$ciHint" -ForegroundColor Red
        exit 3
    }
    if ($volumeSub -eq 'rm' -and -not $projectAllowed) {
        foreach ($target in $volumeTargets) {
            if ($target -like 'testpilot*') {
                Write-Host "Отказано: удаление томов проекта testpilot уничтожает БД и evidence. Используйте ``-p testpilot-ci`` или ``-p testpilot-restore``.$ciHint" -ForegroundColor Red
                exit 3
            }
        }
    }
}

& docker compose @Arguments
exit $LASTEXITCODE
