from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from zipfile import ZipFile, BadZipFile
from fastapi import APIRouter, Depends, HTTPException

from testpilot.common import user
from testpilot.db import session, UploadRow
from testpilot.scanner import ScannerEngine, Severity, ScanSummary

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


@router.post("/scan")
async def scan_endpoint(data: dict[str, Any]) -> dict[str, Any]:
    code = str(data.get("code", "")).strip()
    path_str = str(data.get("path", "")).strip()
    min_sev_str = data.get("min_severity")
    min_sev = Severity(min_sev_str) if min_sev_str else None

    if code:
        issues = _engine.scan_code(code, "web_input.bsl")
        return _summarize(issues, 1, sources_checked=1)

    if path_str:
        p = Path(path_str).resolve()
        if p.is_file():
            issues = _engine.scan_file(p)
            return _summarize(issues, 1, sources_checked=1)
        elif p.is_dir():
            summary = _engine.scan_directory(p, min_severity=min_sev)
            if not summary.total_files:
                return _no_sources("no_sources", "В каталоге не найдено файлов .bsl/.os для проверки.")
            result = summary.to_dict()
            result.update({"status": "scanned", "message": None, "sources_checked": summary.total_files})
            return result
        else:
            raise HTTPException(400, f"Указанный путь не найден: {p}")

    raise HTTPException(400, "Передайте 'code' или 'path' для анализа.")


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
            return _no_sources("no_sources",
                               "Архив прочитан, но файлов .bsl/.os/.xml в нём нет: проверять нечего, аудит не выполнялся.")
        return _summarize(all_issues, scanned, sources_checked=scanned)

    if filename.endswith((".cfe", ".epf", ".erf", ".cf")) or raw.startswith((b"\r\n", b"\n")):
        from testpilot.impact.v8_container import V8Container
        from testpilot.impact.binary_adapter import bsl_sources
        container = V8Container(raw)
        if not container.is_valid():
            return _no_sources("not_a_container",
                               "Файл сохранён, но это не контейнер 1С v8: внутренних потоков для проверки нет. "
                               "Загрузите ZIP выгрузки исходников (1cv8.exe DESIGNER /DumpConfigToFiles).")
        try:
            streams = container.extract_files()
        except Exception:
            return _no_sources("container_error",
                               "Контейнер 1С не удалось прочитать: внутренняя структура повреждена или не поддерживается.")
        sources = bsl_sources(streams)
        if not sources:
            return _no_sources("no_bsl_modules",
                               f"Контейнер 1С прочитан: внутренних потоков — {len(streams)}, "
                               "модулей BSL в них не найдено. Проверять исходники нечего, аудит не выполнялся.")
        all_issues = []
        for name, text in sources.items():
            all_issues.extend(_engine.scan_code(text, name))
        return _summarize(all_issues, len(sources), sources_checked=len(sources))

    text = raw.decode("utf-8-sig", errors="replace")
    issues = _engine.scan_code(text, row.filename)
    return _summarize(issues, 1, sources_checked=1)


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
