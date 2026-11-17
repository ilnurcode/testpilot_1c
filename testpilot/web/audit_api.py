from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any
from zipfile import ZipFile, BadZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from testpilot.common import expert, user
from testpilot.db import session, UploadRow, CodeAuditRow
from testpilot import audit_limit
from testpilot.scanner import ScannerEngine, Severity, ScanSummary
from testpilot.web.audit_report import render_audit_report

router = APIRouter(prefix="/api/v1/audit", tags=["Аудит и v8std"])
_engine = ScannerEngine()
# Граница хранения: в замечаниях сохраняются только фрагменты строк, не длиннее FRAGMENT_LIMIT символов.
FRAGMENT_LIMIT = 140


def _text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _directory_fingerprint(directory: Path) -> str:
    """Отпечаток проверенного каталога: sha256 манифеста «относительный путь + sha256(содержимое файла)».

    Манифест упорядочен по относительному пути в posix-виде, поэтому перепечатка не зависит от обхода файловой системы.
    """
    directory = directory.resolve()
    entries = []
    for path in sorted(_engine.source_files(directory), key=lambda item: item.relative_to(directory).as_posix()):
        relative = path.relative_to(directory).as_posix()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = "unreadable"
        entries.append(f"{relative}\0{digest}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _clip_fragment(issue: dict[str, Any]) -> dict[str, Any]:
    return {**issue, "code_fragment": str(issue.get("code_fragment") or "")[:FRAGMENT_LIMIT]}


def _summarize(issues, total_files: int, *, status: str = "scanned", message: str | None = None,
               sources_checked: int | None = None) -> dict[str, Any]:
    """Сводка аудита. status отличает реальную проверку от случая, когда проверять было нечего.

    Счётчики (total_issues, issues_by_severity/category) считаются по всем замечаниям, а детализация
    в записи и ответе ограничена audit_limit.MAX_ISSUES_KEPT: 1.6-МБ файл даёт 200k замечаний и десятки МБ JSON.
    """
    sev_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    for issue in issues:
        sev_counts[issue.severity.value] = sev_counts.get(issue.severity.value, 0) + 1
        cat_counts[issue.category.value] = cat_counts.get(issue.category.value, 0) + 1
    kept = issues[:audit_limit.MAX_ISSUES_KEPT]
    summary = ScanSummary(
        total_files=total_files,
        total_issues=len(issues),
        issues_by_severity=sev_counts,
        issues_by_category=cat_counts,
        passed=(sev_counts.get(Severity.CRITICAL.value, 0) == 0 and sev_counts.get(Severity.HIGH.value, 0) == 0),
        issues=kept,
    )
    result = summary.to_dict()
    result.update({
        "status": status,
        "message": message,
        "sources_checked": sources_checked,
        "issues_kept": len(kept),
        "issues_truncated": len(issues) > len(kept),
    })
    return result


def _no_sources(status: str, message: str) -> dict[str, Any]:
    """Результат без проверенных исходников: это не «пройдено», а «нечего проверять»."""
    result = _summarize([], 0, status=status, message=message, sources_checked=0)
    result["passed"] = False
    return result


def _issues_url(row: CodeAuditRow) -> str:
    return f"/api/v1/audit/scans/{row.id}/issues"


def _code_audit_result(row: CodeAuditRow, *, include_issues: bool | None = None) -> dict[str, Any]:
    """Карточка сохранённого аудита в той же форме, что отдаёт POST /scan.

    `sha256` — отпечаток проверенного входа: текст вставленного кода либо манифест файлов каталога.
    Полный исходник не сохраняется: в замечаниях есть только фрагменты строк (code_fragment), не длиннее FRAGMENT_LIMIT.

    Совместимость: пока замечаний не больше audit_limit.MAX_ISSUES_KEPT, карточка несёт `issues` как раньше.
    Если их больше, детали в карточку не попадают — их отдаёт постранично `issues_url`
    (иначе ответ на скан с 5000 деталями весит мегабайты).
    """
    kept = row.issues_kept if row.issues_kept is not None else len(row.issues)
    keep_details = row.total_issues <= audit_limit.MAX_ISSUES_KEPT if include_issues is None else include_issues
    result = {
        "scan_id": row.id,
        "source_name": row.source_name,
        "sha256": row.sha256,
        "created_at": row.created_at,
        "status": row.status,
        "message": row.message,
        "total_files": row.total_files,
        "sources_checked": row.sources_checked,
        "total_issues": row.total_issues,
        "issues_by_severity": row.issues_by_severity,
        "issues_by_category": row.issues_by_category,
        "issues_url": _issues_url(row),
        "issues_kept": kept,
        "issues_truncated": bool(row.issues_truncated),
        "passed": row.passed,
        "duration_ms": row.duration_ms,
    }
    if keep_details:
        result["issues"] = row.issues
    return result


def _code_audit_row(source_name: str, fingerprint: str, result: dict[str, Any]) -> CodeAuditRow:
    return CodeAuditRow(
        source_name=source_name,
        sha256=fingerprint,
        total_files=int(result.get("total_files") or 0),
        sources_checked=result.get("sources_checked"),
        total_issues=int(result.get("total_issues") or 0),
        issues_by_severity=result.get("issues_by_severity") or {},
        issues_by_category=result.get("issues_by_category") or {},
        issues=[_clip_fragment(issue) for issue in (result.get("issues") or [])],
        issues_kept=int(result.get("issues_kept") if result.get("issues_kept") is not None
                         else len(result.get("issues") or [])),
        issues_truncated=bool(result.get("issues_truncated")),
        passed=bool(result.get("passed")),
        status=result.get("status") or "scanned",
        message=result.get("message"),
        duration_ms=float(result.get("duration_ms") or 0.0),
    )


def _save_result(row, result):
    """Сохраняет аудит загрузки и возвращает его карточку.

    В БД остаётся полный срез деталей (он нужен отчёту), а в ответе детали не отдаются,
    если замечаний больше audit_limit.MAX_ISSUES_KEPT: их выдаёт постранично `issues_url`.
    """
    row.audit_result = result
    response = {**result, "issues_url": _upload_issues_url(row.id)}
    if int(result.get("total_issues") or 0) > audit_limit.MAX_ISSUES_KEPT:
        response.pop("issues", None)
    return response


@router.post("/scan")
async def scan_endpoint(
    data: dict[str, Any],
    actor=Depends(user),
    s=Depends(session, scope="function"),
) -> dict[str, Any]:
    code = str(data.get("code", "")).strip()
    path_str = str(data.get("path", "")).strip()
    min_sev_str = data.get("min_severity")
    min_sev = Severity(min_sev_str) if min_sev_str else None
    source_name = "вставленный код"
    fingerprint = _text_fingerprint(code)

    if code:
        issues = _engine.scan_code(code, "web_input.bsl")
        result = _summarize(issues, 1, sources_checked=1)
    elif path_str:
        p = Path(path_str).resolve()
        source_name = p.name
        if p.is_file():
            issues = _engine.scan_file(p)
            fingerprint = _text_fingerprint(p.read_text(encoding="utf-8-sig", errors="replace"))
            result = _summarize(issues, 1, sources_checked=1)
        elif p.is_dir():
            summary = _engine.scan_directory(p, min_severity=min_sev)
            fingerprint = _directory_fingerprint(p)
            if not summary.total_files:
                result = _no_sources("no_sources", "В каталоге не найдено файлов .bsl/.os для проверки.")
            else:
                result = summary.to_dict()
                result.update({"status": "scanned", "message": None, "sources_checked": summary.total_files})
        else:
            raise HTTPException(400, f"Указанный путь не найден: {p}")
    else:
        raise HTTPException(400, "Передайте 'code' или 'path' для анализа.")

    record = _code_audit_row(source_name, fingerprint, result)
    s.add(record)
    s.flush()
    return _code_audit_result(record)


@router.get("/scans")
def list_code_audits(
    limit: int = 20,
    actor=Depends(user),
    s=Depends(session, scope="function"),
) -> list[dict[str, Any]]:
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit должен быть от 1 до 100")
    rows = s.scalars(
        select(CodeAuditRow)
        .order_by(CodeAuditRow.created_at.desc(), CodeAuditRow.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "scan_id": row.id,
            "source_name": row.source_name,
            "sha256": row.sha256,
            "total_issues": row.total_issues,
            "issues_by_severity": row.issues_by_severity,
            "issues_kept": row.issues_kept if row.issues_kept is not None else len(row.issues),
            "issues_truncated": bool(row.issues_truncated),
            "passed": row.passed,
            "status": row.status,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/scans/{scan_id}")
def get_code_audit(scan_id: str, actor=Depends(user), s=Depends(session, scope="function")) -> dict[str, Any]:
    row = s.get(CodeAuditRow, scan_id)
    if row is None:
        raise HTTPException(404, "Сохранённый аудит не найден")
    return _code_audit_result(row)


def _ordered_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Стабильный порядок выдачи: файл, строка, колонка, затем порядок сохранения (индекс в срезе).

    Сортировка не зависит от порядка обхода каталога при сканировании, поэтому страницы
    между запросами не пересекаются и не переставляются.
    """
    def key(item: tuple[int, dict[str, Any]]) -> tuple[str, int, int, int]:
        index, issue = item
        try:
            line = int(issue.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        try:
            col = int(issue.get("col") or 1)
        except (TypeError, ValueError):
            col = 1
        return (str(issue.get("file_path") or ""), line, col, index)

    return [issue for _, issue in sorted(enumerate(issues), key=key)]


@router.get("/scans/{scan_id}/issues")
def list_code_audit_issues(
    scan_id: str,
    offset: int = 0,
    limit: int = 100,
    actor=Depends(user),
    s=Depends(session, scope="function"),
) -> dict[str, Any]:
    if limit < 1 or limit > 1000:
        raise HTTPException(400, "limit должен быть от 1 до 1000")
    if offset < 0:
        raise HTTPException(400, "offset не может быть отрицательным")
    row = s.get(CodeAuditRow, scan_id)
    if row is None:
        raise HTTPException(404, "Сохранённый аудит не найден")
    ordered = _ordered_issues(row.issues or [])
    kept = row.issues_kept if row.issues_kept is not None else len(ordered)
    return {
        "items": ordered[offset:offset + limit],
        "total": row.total_issues,
        "offset": offset,
        "limit": limit,
        "issues_truncated": bool(row.issues_truncated),
        "issues_kept": kept,
    }

@router.delete("/scans/{scan_id}")
def delete_code_audit(
    scan_id: str,
    confirm: str | None = None,
    actor=Depends(expert),
    s=Depends(session, scope="function"),
) -> dict[str, Any]:
    """Адресное удаление одной записи журнала аудита; массового удаления нет."""
    if confirm != scan_id:
        raise HTTPException(409, f"подтвердите удаление: передайте confirm={scan_id}")
    row = s.get(CodeAuditRow, scan_id)
    if row is None:
        raise HTTPException(404, "Сохранённый аудит не найден")
    s.delete(row)
    s.flush()
    return {"scan_id": scan_id, "deleted": True}


@router.get("/scans/{scan_id}/report")
def get_code_audit_report(
    scan_id: str,
    format: str = "md",
    actor=Depends(user),
    s=Depends(session, scope="function"),
) -> Response:
    row = s.get(CodeAuditRow, scan_id)
    if row is None:
        raise HTTPException(404, "Сохранённый аудит не найден")
    return render_audit_report(row.id, row.source_name, row.created_at,
                               _code_audit_result(row, include_issues=True), format)


@router.post("/scan-upload")
def scan_upload_endpoint(data: dict[str, Any], actor=Depends(user), s=Depends(session, scope="function")) -> dict[str, Any]:
    upload_id = str(data.get("upload_id", "")).strip()
    project_id = str(data.get("project_id", "")).strip()
    if not upload_id:
        raise HTTPException(400, "Укажите upload_id")

    row = s.get(UploadRow, upload_id)
    if not row or (project_id and row.project_id != project_id):
        raise HTTPException(404, "Загрузка не найдена")

    raw = row.content
    filename = row.filename.lower()

    if filename.endswith(".zip") or raw.startswith(b"PK\x03\x04"):
        try:
            all_issues = []
            scanned = 0
            with ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    if name.lower().endswith((".bsl", ".os", ".xml")):
                        zip_txt = z.read(name).decode("utf-8-sig", errors="replace")
                        all_issues.extend(_engine.scan_code(zip_txt, name))
                        scanned += 1
        except BadZipFile:
            raise HTTPException(422, "Поврежденный ZIP-архив")
        if not scanned:
            return _save_result(row, _no_sources("no_sources",
                               "Архив прочитан, но файлов .bsl/.os/.xml в нём нет: проверять нечего, аудит не выполнялся."))
        return _save_result(row, _summarize(all_issues, scanned, sources_checked=scanned))

    if filename.endswith((".cfe", ".epf", ".erf", ".cf")) or raw.startswith((b"\r\n", b"\n")):
        from testpilot.impact.v8_container import V8Container
        from testpilot.impact.binary_adapter import bsl_sources
        container = V8Container(raw)
        if not container.is_valid():
            return _save_result(row, _no_sources("not_a_container",
                               "Файл сохранён, но это не контейнер 1С v8: внутренних потоков для проверки нет. "
                               "Загрузите ZIP выгрузки исходников (1cv8.exe DESIGNER /DumpConfigToFiles)."))
        try:
            streams = container.extract_files()
        except Exception:
            return _save_result(row, _no_sources("container_error",
                               "Контейнер 1С не удалось прочитать: внутренняя структура повреждена или не поддерживается."))
        sources = bsl_sources(streams)
        if not sources:
            return _save_result(row, _no_sources("no_bsl_modules",
                               f"Контейнер 1С прочитан: внутренних потоков — {len(streams)}, "
                               "модулей BSL в них не найдено. Проверять исходники нечего, аудит не выполнялся."))
        all_issues = []
        for name, text in sources.items():
            all_issues.extend(_engine.scan_code(text, name))
        return _save_result(row, _summarize(all_issues, len(sources), sources_checked=len(sources)))

    text = raw.decode("utf-8-sig", errors="replace")
    issues = _engine.scan_code(text, row.filename)
    return _save_result(row, _summarize(issues, 1, sources_checked=1))


def _upload_issues_url(upload_id: str) -> str:
    return f"/api/v1/audit/uploads/{upload_id}/issues"


def _upload_audit_result(row: UploadRow) -> dict[str, Any]:
    """Карточка сохранённого аудита загрузки в форме ответа POST /scan-upload.

    Совместимость та же, что у журнала сканов: пока замечаний не больше audit_limit.MAX_ISSUES_KEPT,
    `issues` отдаются как раньше; выше — детали только постранично через `issues_url`.
    """
    stored = dict(row.audit_result or {})
    kept = stored.get("issues_kept")
    if kept is None:
        kept = len(stored.get("issues") or [])
    total = int(stored.get("total_issues") or kept or 0)
    result = {**stored,
              "total_issues": total,
              "issues_kept": int(kept),
              "issues_truncated": bool(stored.get("issues_truncated")),
              "issues_url": _upload_issues_url(row.id)}
    if total > audit_limit.MAX_ISSUES_KEPT:
        result.pop("issues", None)
    return result


@router.get("/uploads/{upload_id}")
def get_upload_audit(upload_id: str, actor=Depends(user), s=Depends(session, scope="function")) -> dict[str, Any]:
    row = s.get(UploadRow, upload_id)
    if row is None or row.audit_result is None:
        raise HTTPException(404, "Сохранённый аудит не найден")
    return _upload_audit_result(row)


@router.get("/uploads/{upload_id}/issues")
def list_upload_audit_issues(
    upload_id: str,
    offset: int = 0,
    limit: int = 100,
    actor=Depends(user),
    s=Depends(session, scope="function"),
) -> dict[str, Any]:
    if limit < 1 or limit > 1000:
        raise HTTPException(400, "limit должен быть от 1 до 1000")
    if offset < 0:
        raise HTTPException(400, "offset не может быть отрицательным")
    row = s.get(UploadRow, upload_id)
    if row is None or row.audit_result is None:
        raise HTTPException(404, "Сохранённый аудит не найден")
    stored = row.audit_result or {}
    ordered = _ordered_issues(stored.get("issues") or [])
    kept = stored.get("issues_kept")
    if kept is None:
        kept = len(ordered)
    return {
        "items": ordered[offset:offset + limit],
        "total": int(stored.get("total_issues") or len(ordered)),
        "offset": offset,
        "limit": limit,
        "issues_truncated": bool(stored.get("issues_truncated")),
        "issues_kept": int(kept),
    }


@router.get("/rules")
def get_rules() -> list[dict[str, Any]]:
    return [
        {
            "rule_id": r.rule_id,
            "category": r.category.value,
            "severity": r.severity.value,
            "message": r.message,
            "recommendation": r.recommendation,
        }
        for r in _engine.rules
    ]
