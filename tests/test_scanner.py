from __future__ import annotations

import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from testpilot.scanner import ScannerEngine, Severity, IssueCategory
from testpilot.web.api import app


def test_scanner_detects_security_and_v8std_rules():
    engine = ScannerEngine()
    code = """
    Процедура ОбработкаПроведения(Отказ, РежимПроведения)
        // LCK001 / v8std:managed-lock-in-posting
        Движения.ОстаткиТоваров.Записывать = Истина;
        Движения.ОстаткиТоваров.Записать();
    КонецПроцедуры

    &НаКлиенте
    Процедура Тест(Команда)
        // SEC001
        Выполнить("Расчет_" + Действие);
        // SEC002
        Запрос = Новый Запрос;
        Запрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Товары ГДЕ Код = " + Код;
        // v8std:privileged-mode
        УстановитьПривилегированныйРежим(Истина);
        // v8std:naming
        я = 5;
    КонецПроцедуры
    """
    issues = engine.scan_code(code, "test.bsl")
    rule_ids = {i.rule_id for i in issues}

    assert "v8std:managed-lock-in-posting" in rule_ids
    assert "SEC001" in rule_ids
    assert "SEC002" in rule_ids
    assert "v8std:privileged-mode" in rule_ids
    assert "v8std:naming" in rule_ids


def test_scanner_demo_extension():
    engine = ScannerEngine()
    demo_dir = Path(__file__).parent.parent / "samples" / "demo_vulnerable_extension"
    assert demo_dir.is_dir()

    summary = engine.scan_directory(demo_dir)
    assert summary.total_files >= 3
    assert summary.total_issues >= 8
    assert not summary.passed
    assert summary.issues_by_severity.get(Severity.CRITICAL.value, 0) >= 1
    assert summary.issues_by_severity.get(Severity.HIGH.value, 0) >= 3


def test_audit_api_scan_endpoint():
    client = TestClient(app)
    payload = {
        "code": "Процедура Тест()\nВыполнить(\"Действие_\" + Команда);\nКонецПроцедуры"
    }
    resp = client.post("/api/v1/audit/scan", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_issues"] >= 1
    assert any(i["rule_id"] == "SEC001" for i in data["issues"])


def test_audit_api_rules_endpoint():
    client = TestClient(app)
    resp = client.get("/api/v1/audit/rules")
    assert resp.status_code == 200
    rules = resp.json()
    assert len(rules) >= 10
    rule_ids = {r["rule_id"] for r in rules}
    assert "v8std:managed-lock-in-posting" in rule_ids
    assert "v8std:query-in-loop" in rule_ids
