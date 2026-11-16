"""Тесты раннера веб-клиента 1С."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from testpilot.integration.probe import Profile
from testpilot.integration.webclient import WebClientRunner, StepResult, ScenarioReport

PROFILE = Profile(odata_url='https://1c.example.test/trade', web_url='https://1c.example.test/trade_web', username='admin', password='123')

def test_webclient_runner_initialization():
    runner = WebClientRunner(PROFILE)
    assert runner.web_url == 'https://1c.example.test/trade_web'
    assert runner.headless is True

def test_scenario_report_data_structures():
    step = StepResult(
        step_number=1,
        action='click',
        target='Записать',
        value=None,
        status='passed',
        message='OK',
        duration_seconds=0.12,
        screenshot=b'fake-png'
    )
    assert step.action == 'click'
    assert step.status == 'passed'

    report = ScenarioReport(title='Тест проведения', status='passed', steps=[step])
    assert len(report.steps) == 1
    assert report.status == 'passed'


def test_webclient_runner_real_playwright_execution():
    html = """<!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>1С:Предприятие</title></head>
    <body>
      <button onclick="document.getElementById('status').innerText='Документ успешно проведён'">Провести и закрыть</button>
      <label for="clientInput">Имя клиента</label>
      <input name="Клиент" id="clientInput" />
      <div id="status">Список документов</div>
    </body></html>
    """.encode("utf-8")

    class MockHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)
        def log_message(self, format, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        local_profile = Profile(
            odata_url=f"http://127.0.0.1:{port}/odata",
            web_url=f"http://127.0.0.1:{port}/web",
        )
        runner = WebClientRunner(local_profile, headless=True)
        steps = [
            {"action": "fill", "target": "Имя клиента", "value": "ООО Тест"},
            {"action": "click", "target": "Провести и закрыть"},
            {"action": "assert_text", "target": "Документ успешно проведён"},
        ]
        report = runner.run_scenario("Создание и проведение документа в 1С", steps)
        assert report.status == "passed", report.error
        assert len(report.steps) == 4  # open_url + 3 steps
        assert len(report.screenshots) == 4
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def visible_text_runner(monkeypatch):
    html = """<!DOCTYPE html>
    <html><head><meta charset="utf-8"></head><body>
      <button style="display:none" onclick="document.getElementById('result').textContent='Нажата скрытая'">Продажи</button>
      <button onclick="document.getElementById('result').textContent='Нажата видимая'">Продажи</button>
      <button style="display:none">Скрытая кнопка</button>
      <div>Только часть текста</div>
      <button onclick="setTimeout(() => document.getElementById('later').style.display='block', 300)">Показать позже</button>
      <div id="later" style="display:none">Появившийся текст</div>
      <div id="result">Ожидание</div>
    </body></html>""".encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(WebClientRunner, "handle_login", lambda self, page: None)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        yield WebClientRunner(Profile(odata_url=url, web_url=url), default_timeout=500)
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


def test_visible_exact_text_assert_and_click(visible_text_runner):
    report = visible_text_runner.run_scenario("Видимое совпадение", [
        {"action": "assert_text", "target": "Продажи"},
        {"action": "click", "target": "Продажи"},
        {"action": "assert_text", "target": "Нажата видимая"},
        {"action": "click", "target": "Показать позже"},
        {"action": "assert_text", "target": "Появившийся текст"},
    ])
    assert report.status == "passed", report.error
    assert [step.status for step in report.steps] == ["passed"] * 6


@pytest.mark.parametrize("target", ["Скрытая кнопка", "Только часть"])
def test_assert_text_requires_visible_exact_match(visible_text_runner, target):
    report = visible_text_runner.run_scenario("Нет видимого точного текста", [
        {"action": "assert_text", "target": target},
    ])
    assert report.status == "failed"
    assert report.steps[-1].message == f"Текст '{target}' не обнаружен на форме 1С"


def test_click_does_not_use_hidden_text(visible_text_runner):
    report = visible_text_runner.run_scenario("Скрытая кнопка", [
        {"action": "click", "target": "Скрытая кнопка"},
    ])
    assert report.status == "failed"
    assert report.steps[-1].action == "click"
    assert report.steps[-1].status == "failed"
