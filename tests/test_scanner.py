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
    assert summary.total_files == 8
    assert summary.total_issues == 11
    assert not summary.passed
    assert summary.issues_by_severity == {"CRITICAL": 1, "HIGH": 6, "MEDIUM": 1, "LOW": 3}


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


SECURITY_FIXTURES = Path(__file__).parent / "fixtures" / "security"


def _scan_security_fixture(name: str):
    return ScannerEngine().scan_file(SECURITY_FIXTURES / name)


def test_sec010_flags_safe_mode_disable_positive_fixture():
    issues = [i for i in _scan_security_fixture("sec010_positive.bsl") if i.rule_id == "SEC010"]
    assert len(issues) == 1
    assert issues[0].severity == Severity.CRITICAL
    assert issues[0].message == "Отключение безопасного режима даёт исполняемому коду полный доступ к серверу."


def test_sec010_ignores_comments_and_strings_negative_fixture():
    assert not any(i.rule_id == "SEC010" for i in _scan_security_fixture("sec010_negative.bsl"))


def test_sec011_flags_multiline_query_concatenation_positive_fixture():
    issues = [i for i in _scan_security_fixture("sec011_positive.bsl") if i.rule_id == "SEC011"]
    assert len(issues) == 1
    assert issues[0].severity == Severity.HIGH
    assert issues[0].message == "Склейка строк в запросе — риск внедрения условий; используй параметры (&Имя)."


def test_sec011_ignores_comments_and_plain_strings_negative_fixture():
    assert not any(i.rule_id == "SEC011" for i in _scan_security_fixture("sec011_negative.bsl"))


def test_sec012_flags_com_object_creation_positive_fixture():
    issues = [i for i in _scan_security_fixture("sec012_positive.bsl") if i.rule_id == "SEC012"]
    assert len(issues) == 1
    assert issues[0].severity == Severity.HIGH
    assert issues[0].message == "Создание COM-объекта на сервере обходит типизацию и контроль прав."


def test_sec012_ignores_comments_and_strings_negative_fixture():
    assert not any(i.rule_id == "SEC012" for i in _scan_security_fixture("sec012_negative.bsl"))
