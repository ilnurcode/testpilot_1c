<#
.SYNOPSIS
    Исполняемая приёмка TestPilot 1C: PASS/FAIL/SKIP по шагам, сводка и exit code.
    ВНИМАНИЕ: не выполнять `docker compose down -v` на стенде с данными — это уничтожает БД и evidence.
    Для чистого старта используйте отдельный compose-проект (`-p testpilot-ci`) или CI-раннер.
.DESCRIPTION
    Тот же набор шагов, что в scripts/acceptance.sh. PowerShell 5.1+, без внешних модулей.
    Запуск: powershell -ExecutionPolicy Bypass -File scripts\acceptance.ps1 [-CfePath <file>] [-NoBuild] [-IncludeLLM] [-IncludeLive1C] [-Keep] [-ProbeLogin] [-Help]
    По умолчанию проверяются только локальные вещи: без -IncludeLLM нет обращений к провайдеру, без -IncludeLive1C нет прогона на 1С.
    Флаги независимы: -IncludeLive1C работает без ключа LLM (план строится provider=local), при пустом ключе с -IncludeLLM — FAIL.
    Созданный этим запуском проект удаляется в конце (даже при провале); -Keep оставляет его.
    Переменные окружения: EXPECT_DEMO_ISSUES (по умолчанию 12), CFE_PATH, TP_EXPERT_PASSWORD, TP_EXPERT_USER.
#>
param(
    [string]$CfePath = $env:CFE_PATH,
    [switch]$NoBuild,
    [switch]$IncludeLLM,
    [switch]$IncludeLive1C,
    [switch]$Keep,
    [switch]$ProbeLogin,
    [switch]$Help
)

if ($Help) {
    Write-Host 'Использование: scripts\acceptance.ps1 [-CfePath <file>] [-NoBuild] [-IncludeLLM] [-IncludeLive1C] [-Keep] [-ProbeLogin] [-Help]'
    Write-Host '-IncludeLLM: план через внешнего провайдера (нужен TP_LLM_API_KEY); при пустом ключе — FAIL, а не SKIP.'
    Write-Host '-IncludeLive1C: approve навигационного предложения и run-1c на 1С; без -IncludeLLM план строится provider=local.'
    Write-Host 'Флаги независимы. Без обработки 1С: живой прогон запускает владелец.'
    exit 0
}

$ErrorActionPreference = 'Stop'
# Все compose-вызовы идут через предохранитель scripts/compose.ps1 (запрет down -v на рабочем проекте).
function DC { & (Join-Path $PSScriptRoot 'compose.ps1') @args }
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Web = 'http://127.0.0.1:8000'
$Impact = 'http://127.0.0.1:8001'
$Intel = 'http://127.0.0.1:8002'
$ExpectIssues = if ($env:EXPECT_DEMO_ISSUES) { [int]$env:EXPECT_DEMO_ISSUES } else { 12 }

$script:Passed = 0; $script:Failed = 0; $script:Skipped = 0
function Pass([string]$m) { $script:Passed++; Write-Host "PASS  $m" }
function Fail([string]$m) { $script:Failed++; Write-Host "FAIL  $m" }
function Skip([string]$m) { $script:Skipped++; Write-Host "SKIP  $m" }

function Read-DotEnv([string]$key) {
    if (-not (Test-Path '.env')) { return $null }
    foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($key))=(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}

$User = if ($env:TP_EXPERT_USER) { $env:TP_EXPERT_USER } else { 'expert' }
$Password = if ($env:TP_EXPERT_PASSWORD) { $env:TP_EXPERT_PASSWORD } else { Read-DotEnv 'TP_EXPERT_PASSWORD' }
if (-not $Password) {
    $Password = (DC exec -T intelligence printenv TP_EXPERT_PASSWORD 2>$null | Out-String).Trim()
}
if (-not $Password) {
    Write-Host 'FAIL  не найден TP_EXPERT_PASSWORD ни в .env, ни в окружении контейнера'
    exit 1
}
$AuthValue = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${User}:${Password}"))
$Headers = @{ Authorization = $AuthValue }

function Get-Json([string]$url) { Invoke-RestMethod -Uri $url -Headers $Headers -Method Get -TimeoutSec 180 }
function Post-Json([string]$url, $body) {
    $json = if ($body -is [string]) { $body } else { $body | ConvertTo-Json -Depth 8 -Compress }
    # Тело отправляем байтами UTF-8: иначе PowerShell 5.1 портит кириллицу в JSON.
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Uri $url -Headers $Headers -Method Post -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 300
}
function Test-Url([string]$url) {
    try { $r = Invoke-WebRequest -Uri $url -Headers $Headers -Method Get -UseBasicParsing -TimeoutSec 180; return [int]$r.StatusCode }
    catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode.value__ } else { return 0 } }
}

try {
Write-Host '=== 1. Стек ==='
Write-Host "ПРЕДУПРЕЖДЕНИЕ: не выполнять 'docker compose down -v' на стенде с данными — это уничтожает БД и evidence. Для чистого старта: отдельный проект (-p testpilot-ci) или CI-раннер."
if ($NoBuild) {
    Skip 'DC up (указан -NoBuild)'
} else {
    $up = DC up -d --build --wait 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) { Pass 'DC up -d --build --wait' }
    else {
        Fail "DC up -d --build --wait (код $LASTEXITCODE)"
        exit 1
    }
}
try {
    $health = Get-Json "$Web/health"
    if ($health.status -eq 'ok') { Pass 'GET /health status=ok' } else { Fail "GET /health status=$($health.status)" }
} catch { Fail "GET /health: $($_.Exception.Message)" }
$revision = (DC exec -T intelligence alembic current 2>$null | Out-String).Trim() -split "`n" | Select-Object -Last 1
# Не сверяем с конкретной ревизией: любая голова допустима, важно, что схема на актуальной миграции.
if ($revision -match '\(head\)') { Pass "alembic current: $revision" } else { Fail "alembic current: $revision" }

Write-Host '=== 2. Аудит демо-ZIP ==='
$project = $null
try {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $project = (Post-Json "$Impact/api/v1/projects" @{ name = "Acceptance $stamp"; description = 'acceptance.ps1' }).id
    Pass "проект создан: $project"
} catch { Fail "создание проекта: $($_.Exception.Message)" }

if ($project) {
    try {
        $upload = Invoke-RestMethod -Uri "$Impact/api/v1/projects/$project/uploads?filename=demo_vulnerable_extension.zip" `
            -Method Post -Headers $Headers -InFile 'samples/demo_vulnerable_extension.zip' -ContentType 'application/octet-stream' -TimeoutSec 180
        Pass "загружен demo_vulnerable_extension.zip: $($upload.id)"
    } catch { Fail "загрузка демо-ZIP: $($_.Exception.Message)" }

    if ($upload) {
        try {
            $audit = Post-Json "$Web/api/v1/audit/scan-upload" @{ project_id = $project; upload_id = $upload.id }
            if ($audit.total_issues -eq $ExpectIssues) { Pass "аудит: total_issues=$($audit.total_issues)" }
            else { Fail "аудит: total_issues=$($audit.total_issues) (ожидалось $ExpectIssues)" }
            $sev = $audit.issues_by_severity
            $dist = @($sev.CRITICAL, $sev.HIGH, $sev.MEDIUM, $sev.LOW) -join '/'
            if ($dist -eq '1/7/1/3') { Pass 'распределение 1/7/1/3' } else { Fail "распределение: $dist (ожидалось 1/7/1/3)" }
            if ($audit.total_files -eq 8) { Pass 'проверено файлов: 8' } else { Fail "проверено файлов: $($audit.total_files) (ожидалось 8)" }
        } catch { Fail "аудит загрузки: $($_.Exception.Message)" }

        try {
            $analysis = Post-Json "$Impact/api/v1/projects/$project/uploads/$($upload.id)/analyze" @{}
            $affected = @($analysis.package.affected_objects).Count
            $delivered = Post-Json "$Impact/api/v1/analyses/$($analysis.analysis_id)/deliver" @{}
            $package = $delivered.package_id
            if ($affected -eq 4) { Pass "analyze: затронуто объектов 4, package_id=$package" }
            else { Fail "analyze: затронуто объектов $affected (ожидалось 4)" }
        } catch { Fail "analyze: $($_.Exception.Message)" }
    }
}

Write-Host '=== 3. Бинарный .cfe ==='
if (-not $CfePath -or -not (Test-Path -LiteralPath $CfePath)) {
    Skip '.cfe: путь не задан или файл не найден (-CfePath/CFE_PATH)'
} elseif (-not $project) {
    Fail '.cfe: нет проекта для загрузки'
} else {
    try {
        $name = Split-Path -Leaf $CfePath
        $cfe = Invoke-RestMethod -Uri "$Impact/api/v1/projects/$project/uploads?filename=$name" `
            -Method Post -Headers $Headers -InFile $CfePath -ContentType 'application/octet-stream' -TimeoutSec 180
        if ($cfe.status -eq 'metadata_only') { Pass '.cfe статус загрузки: metadata_only' }
        else { Fail ".cfe статус загрузки: $($cfe.status) (ожидалось metadata_only)" }
        $cfeAudit = Post-Json "$Web/api/v1/audit/scan-upload" @{ project_id = $project; upload_id = $cfe.id }
        if ($cfeAudit.status -eq 'no_bsl_modules') { Pass '.cfe аудит: no_bsl_modules' }
        else { Fail ".cfe аудит: $($cfeAudit.status) (ожидалось no_bsl_modules)" }
    } catch { Fail ".cfe: $($_.Exception.Message)" }
}

Write-Host '=== 4. Журнал аудита кода ==='
$code = "Процедура Тест()`n`tОтключитьБезопасныйРежим(Истина);`n`tЗапрос.Текст = `"ВЫБРАТЬ * ИЗ Справочник.Товары`"`n`t`t+ Переменная;`n`tОбъект = Новый COMОбъект(`"Excel.Application`");`nКонецПроцедуры`n"
$scan = $null
try {
    $scan = Post-Json "$Web/api/v1/audit/scan" @{ code = $code }
    $rules = @($scan.issues | ForEach-Object { $_.rule_id })
    if (($rules -join ',') -eq 'SEC010,SEC011,SEC012') { Pass "скан трёх конструкций: SEC010/SEC011/SEC012, scan_id=$($scan.scan_id)" }
    else { Fail "скан трёх конструкций: $($rules -join ',')" }
} catch { Fail "скан кода: $($_.Exception.Message)" }

if ($scan) {
    $mdPath = Join-Path $env:TEMP 'tp_acceptance.md'
    $csvPath = Join-Path $env:TEMP 'tp_acceptance.csv'
    $mdCode = Test-Url "$Web/api/v1/audit/scans/$($scan.scan_id)/report"
    if ($mdCode -eq 200) {
        Invoke-WebRequest -Uri "$Web/api/v1/audit/scans/$($scan.scan_id)/report" -Headers $Headers -OutFile $mdPath -UseBasicParsing | Out-Null
        $size = (Get-Item -LiteralPath $mdPath).Length
        if ($size -gt 0) { Pass "отчёт md: 200, $size байт" } else { Fail 'отчёт md: пустой' }
    } else { Fail "отчёт md: код $mdCode" }
    $csvCode = Test-Url "$Web/api/v1/audit/scans/$($scan.scan_id)/report?format=csv"
    if ($csvCode -eq 200) {
        Invoke-WebRequest -Uri "$Web/api/v1/audit/scans/$($scan.scan_id)/report?format=csv" -Headers $Headers -OutFile $csvPath -UseBasicParsing | Out-Null
        $size = (Get-Item -LiteralPath $csvPath).Length
        if ($size -gt 0) { Pass "отчёт csv: 200, $size байт" } else { Fail 'отчёт csv: пустой' }
    } else { Fail "отчёт csv: код $csvCode" }
}

Write-Host '=== 5. ИИ: план и прогон ==='
$llmKey = (DC exec -T intelligence sh -c 'printf "%s" "${TP_LLM_API_KEY:-}"' 2>$null | Out-String).Trim()
$markers = @('записать', 'провести', 'создать', 'удалить', 'отменить проведение', 'пометить на удаление', 'скопировать', 'изменить')
function Test-CleanProposal($proposal) {
    if ($proposal.engine -ne 'playwright') { return $false }
    foreach ($case in @($proposal.cases)) {
        foreach ($step in @($case.steps)) {
            $action = ([string]$step.action).ToLowerInvariant()
            $target = ([string]$step.target).ToLowerInvariant()
            if ($action -eq 'fill') { return $false }
            if ($action -eq 'click') { foreach ($m in $markers) { if ($target.Contains($m)) { return $false } } }
        }
    }
    return $true
}
if (-not $IncludeLLM -and -not $IncludeLive1C) {
    Skip 'генерация плана и прогон отключены: добавьте -IncludeLLM (внешний провайдер) и/или -IncludeLive1C (прогон на 1С)'
} else {
    $provider = if ($IncludeLLM) { 'llm' } else { 'local' }
    $suite = $null
    $planProject = $project
    $liveError = $false
    if ($IncludeLive1C) {
        $liveProject = $env:TP_ACCEPTANCE_PROJECT_ID
        if (-not $liveProject) {
            Fail '--live-1c требует TP_ACCEPTANCE_PROJECT_ID: id проекта с настроенным подключением (web_url/odata_url/пользователь)'
            $liveError = $true
        } else {
            try {
                $liveJson = Get-Json "$Impact/api/v1/projects/$liveProject"
                $liveWeb = [string]$liveJson.connection_settings.web_url
                if (-not $liveWeb) {
                    Fail "у проекта $liveProject не настроен web_url; заполните PUT /connection и повторите"
                    $liveError = $true
                } else {
                    $planProject = $liveProject
                }
            } catch {
                Fail "проект ${liveProject}: $($_.Exception.Message)"
                $liveError = $true
            }
        }
    }
    if ($liveError) {
        # причина напечатана выше; план и прогон не создаются
    } elseif ($provider -eq 'llm' -and -not $llmKey) {
        Fail 'генерация плана llm: TP_LLM_API_KEY не задан'
    } elseif (-not $package) {
        Fail 'генерация плана: нет package_id из анализа ZIP'
    } elseif ($provider -eq 'llm') {
        try {
            $suite = Post-Json "$Intel/api/v1/ai/suites" @{ package_id = $package; project_id = $planProject; provider = 'llm'; consent = $true; object_names = @{}; model_context = @() }
        } catch {
            Fail "генерация плана llm: $($_.Exception.Message)"
        }
    } else {
        try {
            $suite = Post-Json "$Intel/api/v1/ai/suites" @{ package_id = $package; project_id = $planProject; provider = 'local' }
        } catch {
            Fail "генерация плана local: $($_.Exception.Message)"
        }
    }
    if ($suite) {
        $index = $null
        for ($i = 0; $i -lt @($suite.suggestion.proposals).Count; $i++) {
            if (Test-CleanProposal $suite.suggestion.proposals[$i]) { $index = $i; break }
        }
        if ($null -eq $index) {
            Fail "в плане $($suite.suite_id) нет неразрушающего playwright-предложения"
        } else {
            Pass "план ${provider}: suite_id=$($suite.suite_id), предложение $index (навигация)"
            try {
                $approved = Post-Json "$Intel/api/v1/ai/suites/$($suite.suite_id)/approve" @{ reason = 'acceptance'; selected_proposals = @([int]$index) }
                if ($approved.status -eq 'approved') { Pass 'approve: approved' } else { Fail "approve: $($approved.status)" }
            } catch { Fail "approve: $($_.Exception.Message)" }
            if (-not $IncludeLive1C) {
                Skip 'прогон на 1С отключён: добавьте -IncludeLive1C'
            } else {
            try {
                $run = Post-Json "$Intel/api/v1/ai/suites/$($suite.suite_id)/run-1c" @{}
                $passedCount = [int]$run.counts.passed
                if ($passedCount -ge 1) { Pass "прогон: passed=$passedCount" } else { Fail "прогон: passed=$passedCount" }
                $evidence = @($run.results | Where-Object { $_.evidence_id } | Select-Object -First 1)
                if ($evidence.Count -gt 0) { Pass "evidence: $($evidence[0].evidence_id)" } else { Fail 'evidence отсутствует' }
            } catch { Fail "прогон: $($_.Exception.Message)" }
            $reportCode = Test-Url "$Intel/api/v1/ai/suites/$($suite.suite_id)/report"
            if ($reportCode -eq 200) { Pass 'отчёт о прогоне md: 200' } else { Fail "отчёт о прогоне md: $reportCode" }
            }
        }
    }
}

Write-Host '=== 5a. Зонд формы входа (read-only) ==='
if (-not $ProbeLogin) {
    Skip 'probe-login: флаг не передан'
} else {
    $probeProject = $env:TP_ACCEPTANCE_PROJECT_ID
    if (-not $probeProject) {
        Fail 'probe-login: укажите TP_ACCEPTANCE_PROJECT_ID — id проекта с настроенным web_url'
    } else {
        try {
            $probe = Post-Json "$Intel/api/v1/ai/connection/probe-login" @{ project_id = $probeProject }
            $fieldsCount = @($probe.fields).Count
            $buttonsCount = @($probe.buttons).Count
            Pass "probe-login: login_form_visible=$($probe.login_form_visible), fields=$fieldsCount, buttons=$buttonsCount"
        } catch {
            $body = ''
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $body = $_.ErrorDetails.Message }
            elseif ($_.Exception.Message) { $body = $_.Exception.Message }
            Fail "probe-login: $($body -replace '\s+', ' ')"
        }
    }
}

Write-Host '=== 6. Тесты ==='
# UI smoke требует адрес интерфейса и пароль эксперта; без них тест сам пропускается.
# Внутри compose web виден как web:8000; для host-запуска задайте TP_UI_BASE_URL=http://127.0.0.1:8000.
if (-not $env:TP_UI_BASE_URL) { $env:TP_UI_BASE_URL = 'http://web:8000' }
if (-not $env:TP_EXPERT_PASSWORD) { $env:TP_EXPERT_PASSWORD = 'TestPilotLocal123!' }
# В приёмке интерфейс обязателен: недоступный UI — FAIL, а не SKIP.
$env:TP_UI_REQUIRED = '1'
$uiOut = DC exec -T -e "TP_UI_BASE_URL=$env:TP_UI_BASE_URL" -e "TP_EXPERT_PASSWORD=$env:TP_EXPERT_PASSWORD" -e "TP_UI_REQUIRED=1" intelligence python -m pytest tests/test_ui_smoke.py -q 2>&1 | Out-String
$uiCode = $LASTEXITCODE
$uiPassed = ($uiOut -split "`n" | Where-Object { $_ -match '\d+ passed' } | Select-Object -Last 1)
$uiSkipped = ($uiOut -split "`n" | Where-Object { $_ -match '\d+ skipped' } | Select-Object -Last 1)
if ($uiCode -eq 0 -and $uiPassed) {
    $uiCount = ([regex]::Match($uiPassed, '(\d+) passed')).Groups[1].Value
    Pass "UI smoke: $uiCount проверок OK (overview, projects, project, audit, ai)"
} elseif ($uiCode -eq 0 -and $uiSkipped) {
    Write-Host "SKIP  UI smoke: $($uiSkipped.Trim())"
    $script:Skipped = $script:Skipped + 1
} else {
    Fail "UI smoke: $(($uiOut -split "`n" | Where-Object { $_ -match 'раздел #|AssertionError' } | Select-Object -First 3) -join ' ')"
}
$pytestOut = DC exec -T -e "TP_UI_BASE_URL=$env:TP_UI_BASE_URL" -e "TP_EXPERT_PASSWORD=$env:TP_EXPERT_PASSWORD" -e "TP_UI_REQUIRED=1" intelligence python -m pytest tests/ -q 2>&1 | Out-String
$pytestCode = $LASTEXITCODE
$summary = ($pytestOut -split "`n" | Where-Object { $_ -match '\d+ passed' } | Select-Object -Last 1)
if ($pytestCode -eq 0 -and $summary) { Pass "pytest: $($summary.Trim())" }
else { Fail "pytest: код $pytestCode, $summary" }

} finally {
    if ($project) {
        if ($Keep) {
            Write-Host "kept project $($project.Substring(0,8)) (-Keep)"
        } else {
            try {
                $deleted = Invoke-RestMethod -Uri "$Impact/api/v1/projects/$project`?confirm=$project" -Method Delete -Headers $Headers -TimeoutSec 120
                Write-Host "deleted project $($project.Substring(0,8))"
            } catch {
                Write-Host "FAIL  удаление проекта $($project.Substring(0,8)): $($_.Exception.Message)"
            }
        }
    }
    $dbUser = (DC exec -T db printenv POSTGRES_USER 2>$null | Out-String).Trim()
    $dbName = (DC exec -T db printenv POSTGRES_DB 2>$null | Out-String).Trim()
    if ($scan -and $scan.scan_id) {
        if ($Keep) {
            Write-Host "kept scan $($scan.scan_id.Substring(0,8)) (-Keep)"
        } else {
            try {
                $scanDeleted = Invoke-RestMethod -Uri "$Web/api/v1/audit/scans/$($scan.scan_id)`?confirm=$($scan.scan_id)" -Method Delete -Headers $Headers -TimeoutSec 120
                Write-Host "deleted scan $($scan.scan_id.Substring(0,8))"
            } catch {
                Write-Host "FAIL  удаление скана $($scan.scan_id.Substring(0,8)): $($_.Exception.Message)"
            }
        }
    }
    $journal = (DC exec -T db psql -U $dbUser -d $dbName -t -A -c 'select count(*) from code_audit' 2>$null | Out-String).Trim()
    if (-not $journal) { $journal = 'неизвестно' }
    Write-Host "журнальных записей аудита кода: $journal"
    Write-Host '=== ИТОГО ==='
    Write-Host "passed=$script:Passed failed=$script:Failed skipped=$script:Skipped"
}
if ($script:Failed -gt 0) { Write-Host 'РЕЗУЛЬТАТ: FAIL'; exit 1 }
Write-Host 'РЕЗУЛЬТАТ: PASS'
exit 0
