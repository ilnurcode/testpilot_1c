from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any
from zipfile import ZipFile, BadZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from testpilot.common import user
from testpilot.db import session, UploadRow, CodeAuditRow
from testpilot.scanner import ScannerEngine, Severity, ScanSummary
from testpilot.web.audit_report import render_audit_report

router = APIRouter(prefix="/api/v1/audit", tags=["Аудит и v8std"])
_engine = ScannerEngine()


def _summarize(issues, total_files: int, *, status: str = "scanned", message: str | None = None,
               sources_checked: int | None = None) -> dict[str, Any]:
    """Сводка аудита. status отличает реальную проверку от случая, когда проверять было нечего."""
    sev_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    for issue in issues:
        sev_counts[issue.severity.value] = sev_counts.get(issue.severity.value, 0) + 1
        cat_counts[issue.category.value] = cat_counts.get(issue.category.value, 0) + 1
    summary = ScanSummary(
        total_files=total_files,
        total_issues=len(issues),
        issues_by_severity=sev_counts,
        issues_by_category=cat_counts,
        passed=(sev_counts.get(Severity.CRITICAL.value, 0) == 0 and sev_counts.get(Severity.HIGH.value, 0) == 0),
        issues=issues,
    )
    result = summary.to_dict()
    result.update({"status": status, "message": message, "sources_checked": sources_checked})
    return result


def _no_sources(status: str, message: str) -> dict[str, Any]:
    """Результат без проверенных исходников: это не «пройдено», а «нечего проверять»."""
    result = _summarize([], 0, status=status, message=message, sources_checked=0)
    result["passed"] = False
    return result


def _code_audit_result(row: CodeAuditRow) -> dict[str, Any]:
    """Карточка сохранённого аудита в той же форме, что отдаёт POST /scan.

    Полный исходник не сохраняется: в замечаниях есть только фрагменты строк (code_fragment).
    """
    return {
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
        "issues": row.issues,
        "passed": row.passed,
        "duration_ms": row.duration_ms,
    }


def _code_audit_row(source_name: str, source_text: str, result: dict[str, Any]) -> CodeAuditRow:
    return CodeAuditRow(
        source_name=source_name,
        sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        total_files=int(result.get("total_files") or 0),
        sources_checked=result.get("sources_checked"),
        total_issues=int(result.get("total_issues") or 0),
        issues_by_severity=result.get("issues_by_severity") or {},
        issues_by_category=result.get("issues_by_category") or {},
        issues=result.get("issues") or [],
        passed=bool(result.get("passed")),
        status=result.get("status") or "scanned",
        message=result.get("message"),
        duration_ms=float(result.get("duration_ms") or 0.0),
    )


def _save_result(row, result):
    row.audit_result = result
    return result


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
    source_text = code

    if code:
        issues = _engine.scan_code(code, "web_input.bsl")
        result = _summarize(issues, 1, sources_checked=1)
    elif path_str:
        p = Path(path_str).resolve()
        source_name = p.name
        if p.is_file():
            source_text = p.read_text(encoding="utf-8-sig", errors="replace")
            issues = _engine.scan_file(p)
            result = _summarize(issues, 1, sources_checked=1)
        elif p.is_dir():
            summary = _engine.scan_directory(p, min_severity=min_sev)
            source_text = path_str
            if not summary.total_files:
                result = _no_sources("no_sources", "В каталоге не найдено файлов .bsl/.os для проверки.")
            else:
                result = summary.to_dict()
                result.update({"status": "scanned", "message": None, "sources_checked": summary.total_files})
        else:
            raise HTTPException(400, f"Указанный путь не найден: {p}")
    else:
        raise HTTPException(400, "Передайте 'code' или 'path' для анализа.")

    record = _code_audit_row(source_name, source_text, result)
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
    return render_audit_report(row.id, row.source_name, row.created_at, _code_audit_result(row), format)


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


@router.get("/uploads/{upload_id}")
def get_upload_audit(upload_id: str, actor=Depends(user), s=Depends(session, scope="function")) -> dict[str, Any]:
    row = s.get(UploadRow, upload_id)
    if row is None or row.audit_result is None:
        raise HTTPException(404, "Сохранённый аудит не найден")
    return row.audit_result


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
